#Script 24
import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

from model.post.geometry_dissolve import dissolve_with_memory_fallback

INFLUENCED_LAYERS = [
    "OlieGasChemicalienPijpleiding",
    "Overig",
    "Waterleiding",
    "Rioolleiding",
    "ThermischePijpleiding",
]
INFLUENCED_COLUMNS = [
    "id",
    "feature_id",
    "run_id",
    "influenced_run_id",
    "influenced_component_length_m",
    "verticalePositie",
    "buisDiameter",
    "buisDiameterM",
    "druk",
    "drukM",
    "beheerder",
    "thema",
    "Eigenaar",
    "material",
    "material_norm",
    "conductive",
    "source_layer",
    "a",
    "l",
    "step2_inductive",
]
INFLUENCER_COLUMNS = [
    "id",
    "feature_id",
    "run_id",
    "influenced_run_id",
    "a",
    "l",
    "a_required",
    "verticalePositie",
    "werkVoltage",
    "werkVoltageM",
    "Eigenaar",
    "beheerder",
    "thema",
    "influencer_class",
    "UC_continu",
    "UC_corrosie",
    "UC_eenfase",
    "UC_continu_pass",
    "UC_corrosie_pass",
    "UC_eenfase_pass",
    "step2_inductive",
]

# Canonical assessment names and accepted source-column spellings.
#
# "resisitve" is included because it may exist as a typo in temp19.
ASSESSMENT_COLUMN_ALIASES = {
    "inductive": [
        "step2_inductive",
    ],
}
NO_PASS_OUTPUT_LAYERS = {
    "inductive": "no_pass_inductive",
}

# ============================================================
# GENERAL HELPERS
# ============================================================

from model.part2.assessment_storage import (
    CABLE_LAYER,
    HV_LAYER,
    INFLUENCED_LAYER,
    is_normalized_assessment,
    read_assessed_layer,
)

_MODEL_LAYERS = {HV_LAYER, CABLE_LAYER, INFLUENCED_LAYER}


def _read_model_dataframe(path, layer, **kwargs):
    if is_normalized_assessment(path):
        no_pass = str(layer).endswith("_no_pass")
        base_layer = str(layer)[:-8] if no_pass else str(layer)
        if base_layer in _MODEL_LAYERS:
            frame = read_assessed_layer(
                path,
                base_layer,
                no_pass=no_pass,
            )
            read_geometry = kwargs.get("read_geometry", True)
            geometry_name = frame.geometry.name
            columns = kwargs.get("columns")
            if columns is not None:
                selected = list(columns)
                if read_geometry and geometry_name not in selected:
                    selected.append(geometry_name)
                frame = frame[selected]
            if not read_geometry:
                frame = frame.drop(
                    columns=[geometry_name],
                    errors="ignore",
                )
                frame = pd.DataFrame(frame)
            return frame
    return pyogrio.read_dataframe(path, layer=layer, **kwargs)



def _read_model_info(path, layer, **kwargs):
    if is_normalized_assessment(path):
        no_pass = str(layer).endswith("_no_pass")
        base_layer = str(layer)[:-8] if no_pass else str(layer)
        if base_layer in _MODEL_LAYERS:
            frame = read_assessed_layer(
                path,
                base_layer,
                no_pass=no_pass,
            )
            return {
                "fields": [
                    column
                    for column in frame.columns
                    if column != frame.geometry.name
                ],
                "features": len(frame),
                "crs": frame.crs,
            }
    return pyogrio.read_info(path, layer=layer, **kwargs)

def _normalized_layer_names(path, names):
    names = set(names)
    if is_normalized_assessment(path):
        names.update(
            f"{layer}_no_pass"
            for layer in _MODEL_LAYERS
            if layer in names
        )
    return names

def get_layer_names(file_path: Path) -> set[str]:
    """Return the layer names present in a GIS file."""
    if not file_path.exists():
        raise FileNotFoundError(
            f"Input file not found: {file_path}"
        )
    layer_information = pyogrio.list_layers(file_path)
    return _normalized_layer_names(
        file_path,
        layer_information[:, 0],
    )

def get_available_columns(
    file_path: Path,
    layer_name: str,
    requested_columns: list[str],
) -> list[str]:
    """
    Return requested attribute columns that exist in the layer.
    The native FID and geometry are handled separately.
    """
    layer_info = _read_model_info(
        file_path,
        layer=layer_name,
    )
    available_fields = set(layer_info["fields"])
    selected_columns = [
        column
        for column in requested_columns
        if column in available_fields
    ]
    missing_columns = [
        column
        for column in requested_columns
        if column not in available_fields
    ]
    if missing_columns:
        print(
            f"Layer '{layer_name}' does not contain: "
            f"{', '.join(missing_columns)}"
        )
    return selected_columns

def quote_sql_identifier(identifier: str) -> str:
    """Safely quote a SQLite table or column name."""
    return '"' + identifier.replace('"', '""') + '"'

def quote_sql_value(value: str) -> str:
    """Safely quote a string for a GDAL SQL WHERE clause."""
    return "'" + value.replace("'", "''") + "'"

def normalize_assessment_values(
    values: pd.Series,
) -> pd.Series:
    """
    Normalize assessment values.
    Examples converted to "no-pass":
    - no-pass
    - NO-PASS
    - no_pass
    - no pass
    - no–pass
    """
    return (
        values
        .astype("string")
        .str.strip()
        .str.casefold()
        .str.replace(
            r"[\s_–—]+",
            "-",
            regex=True,
        )
    )

def is_no_pass(values: pd.Series) -> pd.Series:
    """Return True where an assessment value means no-pass."""
    normalized_values = normalize_assessment_values(values)
    return normalized_values.eq("no-pass").fillna(False)

def find_assessment_columns(
    available_fields: set[str],
    source_layer_name: str,
) -> dict[str, str]:
    """
    Find the assessment columns in a source layer.
    Returns a mapping from canonical assessment name to the actual
    source-column name, for example:
        {
            "thermal": "thermal",
            "resistive": "resisitve",
        }
    """
    result: dict[str, str] = {}
    # Permit case-insensitive source-column matching.
    case_insensitive_lookup = {
        field.casefold(): field
        for field in available_fields
    }
    for canonical_name, aliases in (
        ASSESSMENT_COLUMN_ALIASES.items()
    ):
        matches = []
        for alias in aliases:
            actual_name = case_insensitive_lookup.get(
                alias.casefold()
            )
            if (
                actual_name is not None
                and actual_name not in matches
            ):
                matches.append(actual_name)
        if len(matches) > 1:
            raise ValueError(
                f"Layer '{source_layer_name}' contains multiple "
                f"columns for assessment '{canonical_name}': "
                f"{', '.join(matches)}. Keep only one spelling."
            )
        if matches:
            result[canonical_name] = matches[0]
        else:
            print(
                f"Layer '{source_layer_name}' does not contain "
                f"assessment '{canonical_name}'."
            )
    return result

# ============================================================
# COPY AN UNCHANGED LAYER WHILE PRESERVING FIDS
# ============================================================

def restore_native_fids(
    output_path: Path,
    layer_name: str,
    temporary_fid_column: str,
) -> None:
    """Restore source FIDs as native GeoPackage FIDs."""
    quoted_layer = quote_sql_identifier(layer_name)
    quoted_temp_fid = quote_sql_identifier(
        temporary_fid_column
    )
    with sqlite3.connect(output_path) as connection:
        table_info = connection.execute(
            f"PRAGMA table_info({quoted_layer})"
        ).fetchall()
        primary_key_columns = [
            column_info[1]
            for column_info in table_info
            if column_info[5] == 1
        ]
        if len(primary_key_columns) != 1:
            raise RuntimeError(
                "Could not determine the native FID column "
                f"for layer '{layer_name}'."
            )
        fid_column = primary_key_columns[0]
        quoted_fid = quote_sql_identifier(fid_column)
        missing_fid_count = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM {quoted_layer}
            WHERE {quoted_temp_fid} IS NULL
            """
        ).fetchone()[0]
        if missing_fid_count:
            raise RuntimeError(
                f"Layer '{layer_name}' contains "
                f"{missing_fid_count} missing source FIDs."
            )
        duplicate_fid_count = connection.execute(
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT {quoted_temp_fid}
                FROM {quoted_layer}
                GROUP BY {quoted_temp_fid}
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
        if duplicate_fid_count:
            raise RuntimeError(
                f"Layer '{layer_name}' contains duplicate "
                "source FIDs."
            )
        maximum_generated_fid = connection.execute(
            f"""
            SELECT MAX({quoted_fid})
            FROM {quoted_layer}
            """
        ).fetchone()[0]
        maximum_source_fid = connection.execute(
            f"""
            SELECT MAX({quoted_temp_fid})
            FROM {quoted_layer}
            """
        ).fetchone()[0]
        offset = max(
            int(maximum_generated_fid or 0),
            int(maximum_source_fid or 0),
        ) + 1
        # Temporarily move generated FIDs out of the source-FID range.
        connection.execute(
            f"""
            UPDATE {quoted_layer}
            SET {quoted_fid} = {quoted_fid} + ?
            """,
            (offset,),
        )
        connection.execute(
            f"""
            UPDATE {quoted_layer}
            SET {quoted_fid} = {quoted_temp_fid}
            """
        )
        connection.execute(
            f"""
            ALTER TABLE {quoted_layer}
            DROP COLUMN {quoted_temp_fid}
            """
        )
        connection.commit()

def copy_layer_preserving_fid(
    source_path: Path,
    output_path: Path,
    source_layer_name: str,
    output_layer_name: str,
    requested_columns: list[str] | None,
    where: str | None = None,
) -> int:
    """
    Copy a layer or selection while preserving native source FIDs.
    This is used for Mast. It is not used for no-pass layers because
    one source feature can contribute to multiple assessment groups.
    """
    temporary_fid_column = "_source_fid_temp"
    if requested_columns is None:
        layer_info = _read_model_info(
            source_path,
            layer=source_layer_name,
        )
        selected_columns = list(layer_info["fields"])
    else:
        selected_columns = get_available_columns(
            file_path=source_path,
            layer_name=source_layer_name,
            requested_columns=requested_columns,
        )
    if temporary_fid_column in selected_columns:
        raise ValueError(
            f"Layer '{source_layer_name}' already contains a "
            f"column named '{temporary_fid_column}'."
        )
    layer_data = _read_model_dataframe(
        source_path,
        layer=source_layer_name,
        columns=selected_columns,
        where=where,
        fid_as_index=True,
    )
    if layer_data.empty:
        print(
            f"No features found for output layer "
            f"'{output_layer_name}'; skipping."
        )
        return 0
    geometry_column = layer_data.geometry.name
    layer_data[temporary_fid_column] = (
        layer_data.index.to_numpy()
    )
    columns_to_keep = (
        selected_columns
        + [temporary_fid_column, geometry_column]
    )
    cleaned_data = layer_data[columns_to_keep].copy()
    cleaned_data.to_file(
        output_path,
        layer=output_layer_name,
        driver="GPKG",
        engine="pyogrio",
        index=False,
        SPATIAL_INDEX="NO",
    )
    restore_native_fids(
        output_path=output_path,
        layer_name=output_layer_name,
        temporary_fid_column=temporary_fid_column,
    )
    return len(cleaned_data)

# ============================================================
# CREATE SEPARATE NO-PASS GROUPS
# ============================================================

def create_no_pass_groups(
    layer_data: gpd.GeoDataFrame,
    unique_id_column: str,
    assessment_columns: dict[str, str],
    source_layer_name: str,
    output_layer_name: str,
    infrastructure_type: str | None = None,
) -> gpd.GeoDataFrame:
    """
    Create separate groups for every no-pass assessment.
    A source feature can contribute to more than one no-pass type.
    Output groups are dissolved by:
        influenced_run_id + no_pass_type + infrastructure_type
    """
    if unique_id_column not in layer_data.columns:
        raise ValueError(
            f"Cannot group layer '{output_layer_name}': "
            f"column '{unique_id_column}' is unavailable."
        )
    if not assessment_columns:
        raise ValueError(
            f"Layer '{source_layer_name}' does not contain any "
            "assessment columns."
        )
    geometry_column = layer_data.geometry.name
    reserved_columns = {
        "no_pass_type",
        "assessment_result",
        "source_fid_min",
        "source_feature_count",
        "infrastructure_type",
    }
    conflicting_columns = reserved_columns.intersection(layer_data.columns)
    if conflicting_columns:
        raise ValueError(
            f"Layer '{source_layer_name}' already contains reserved "
            f"output columns: {', '.join(sorted(conflicting_columns))}"
        )
    
    # Preserve the infrastructure origin using the exact configured names.
    if "source_layer" in layer_data.columns:
        source_layer_values = (
            layer_data["source_layer"]
            .astype("string")
            .str.strip()
        )
        infrastructure_lookup = {
            name.casefold(): name
            for name in INFLUENCED_LAYERS
        }
        layer_data["infrastructure_type"] = source_layer_values.map(
            lambda value: (
                infrastructure_lookup.get(value.casefold())
                if pd.notna(value)
                else pd.NA
            )
        )
        
        unknown_values = sorted(
            set(
                source_layer_values.dropna()
                .loc[
                    ~source_layer_values.dropna()
                     .str.casefold()
                     .isin(infrastructure_lookup)
                ]
                .tolist()
            )
        )

        if unknown_values:
            raise ValueError(
                f"Layer '{source_layer_name}' contains source_layer values "
                f"outside INFLUENCED_LAYERS: {', '.join(unknown_values)}"
            )
    else:
        layer_data["infrastructure_type"] = (
            infrastructure_type 
            if infrastructure_type is not None
            else output_layer_name
        )
    layer_data = layer_data.sort_index().copy()
    layer_data["source_fid_min"] = layer_data.index.to_numpy()
    
    no_pass_parts = []
    
    for canonical_name, source_column in assessment_columns.items():
        assessment_mask = is_no_pass(layer_data[source_column])

        # Identify complete runs that contain at least one no-pass segment.
        rows_with_id = layer_data[unique_id_column].notna()
        no_pass_run_ids = (
            layer_data.loc[
                rows_with_id & assessment_mask,
                unique_id_column,
            ]
            .dropna()
            .unique()
        )

        # Keep ALL segments of every run that contains a no-pass segment.
        whole_run_mask = layer_data[unique_id_column].isin(no_pass_run_ids)

        # Features without a run ID cannot be assessed as a run.
        # For these, retain the individual feature only when it is no-pass.
        rows_without_id = (
            layer_data[~rows_with_id & assessment_mask].copy()
        )

        assessment_data = layer_data.loc[
            whole_run_mask
        ].copy()

        if not rows_without_id.empty:
            assessment_data = pd.concat(
                [assessment_data, rows_without_id],
                ignore_index=False,
            )

        if assessment_data.empty:
            print(
                f"No '{canonical_name}' no-pass runs found "
                f"for '{output_layer_name}'."
            )
            continue

        assessment_data["no_pass_type"] = canonical_name
        assessment_data["assessment_result"] = "no-pass"
        assessment_data["source_feature_count"] = 1

        no_pass_parts.append(assessment_data)

        print(
            f"Found {len(no_pass_run_ids)} complete "
            f"'{canonical_name}' no-pass runs for "
            f"'{output_layer_name}', retaining "
            f"{len(assessment_data)} segments."
        )

    if not no_pass_parts:
        return gpd.GeoDataFrame(
            geometry=[],
            crs=layer_data.crs,
        )

    expanded_data = pd.concat(no_pass_parts, axis=0)

    expanded_data = gpd.GeoDataFrame(
        expanded_data,
        geometry=geometry_column,
        crs=layer_data.crs,
    )

    actual_assessment_columns = list(assessment_columns.values())
    expanded_data = expanded_data.drop(
        columns=actual_assessment_columns + ["source_layer"],
        errors="ignore",
    )

    rows_with_id = expanded_data.loc[
        expanded_data[unique_id_column].notna()
    ].copy()

    rows_without_id = expanded_data.loc[
        expanded_data[unique_id_column].isna()
    ].copy()

    if not rows_with_id.empty:
        group_columns = [
            unique_id_column,
            "no_pass_type",
            "infrastructure_type",
        ]

        aggregation: dict[str, str] = {}

        for column in rows_with_id.columns:
            if column in {
                geometry_column,
                *group_columns,
            }:
                continue

            if column == "source_fid_min":
                aggregation[column] = "min"
            elif column == "source_feature_count":
                aggregation[column] = "sum"
            else:
                aggregation[column] = "first"

        merged_rows = dissolve_with_memory_fallback(
            rows_with_id,
            by=group_columns,
            aggfunc=aggregation,
            as_index=False,
            sort=False,
        )
    else:
        merged_rows = rows_with_id

    combined_data = pd.concat(
        [
            merged_rows,
            rows_without_id,
        ],
        ignore_index=True,
    )

    result = gpd.GeoDataFrame(
        combined_data,
        geometry=geometry_column,
        crs=layer_data.crs,
    )

    print(
        f"Created {len(result)} separate no-pass groups for "
        f"'{output_layer_name}' from "
        f"{len(expanded_data)} retained segments."
    )

    return result

def copy_no_pass_layer(
    source_path: Path,
    output_path: Path,
    source_layer_name: str,
    output_layer_name: str,
    requested_columns: list[str],
    where: str | None = None,
    unique_id_column: str = "influenced_run_id",
    no_pass_type: str | None = None,
    infrastructure_type: str | None = None,
    write_output: bool = True,
) -> int | gpd.GeoDataFrame:
    """
    Select and create no-pass groups for one output layer.

    When write_output=False, the resulting GeoDataFrame is returned
    without writing it. This allows influenced and influencer features
    to be combined into the same assessment-specific layer.
    """
    layer_info = _read_model_info(
        source_path,
        layer=source_layer_name,
    )
    available_fields = set(layer_info["fields"])

    selected_columns = [
        column
        for column in requested_columns
        if column in available_fields
    ]

    missing_columns = [
        column
        for column in requested_columns
        if column not in available_fields
    ]

    if missing_columns:
        print(
            f"Layer '{source_layer_name}' does not contain: "
            f"{', '.join(missing_columns)}"
        )

    if unique_id_column not in available_fields:
        raise ValueError(
            f"Layer '{source_layer_name}' does not contain "
            f"'{unique_id_column}'."
        )

    if unique_id_column not in selected_columns:
        selected_columns.append(unique_id_column)

    assessment_columns = find_assessment_columns(
        available_fields=available_fields,
        source_layer_name=source_layer_name,
    )

    if not assessment_columns:
        print(
            f"Layer '{source_layer_name}' contains no assessment "
            "columns; skipping."
        )
        return gpd.GeoDataFrame()

    if no_pass_type is not None:
        if no_pass_type not in assessment_columns:
            raise ValueError(
                f"Layer '{source_layer_name}' does not contain the "
                f"assessment column for '{no_pass_type}'."
            )
        assessment_columns = {
            no_pass_type: assessment_columns[no_pass_type]
        }

    for source_column in assessment_columns.values():
        if source_column not in selected_columns:
            selected_columns.append(source_column)

    layer_data = _read_model_dataframe(
        source_path,
        layer=source_layer_name,
        columns=selected_columns,
        where=where,
        fid_as_index=True,
    )

    if layer_data.empty:
        print(
            f"No source features found for output layer "
            f"'{output_layer_name}'; skipping."
        )
        if write_output:
            return 0
        return gpd.GeoDataFrame(
            geometry=[],
            crs=layer_data.crs,
        )

    no_pass_data = create_no_pass_groups(
        layer_data=layer_data,
        unique_id_column=unique_id_column,
        assessment_columns=assessment_columns,
        source_layer_name=source_layer_name,
        output_layer_name=output_layer_name,
        infrastructure_type=infrastructure_type,
    )

    if no_pass_data.empty:
        print(
            f"No no-pass features found for output layer "
            f"'{output_layer_name}'; skipping."
        )
        if write_output:
            return 0
        return no_pass_data

    if not write_output:
        return no_pass_data

    no_pass_data.to_file(
        output_path,
        layer=output_layer_name,
        driver="GPKG",
        engine="pyogrio",
        index=False,
        SPATIAL_INDEX="NO",
    )

    return len(no_pass_data)

def get_source_layer_values(
    file_path: Path,
    layer_name: str,
) -> set[str]:
    """Return the non-null source_layer values in a layer."""
    layer_info = _read_model_info(
        file_path,
        layer=layer_name,
    )
    if "source_layer" not in set(layer_info["fields"]):
        raise ValueError(
            f"Layer '{layer_name}' does not contain the required "
            "'source_layer' column."
        )
    source_layers = _read_model_dataframe(
        file_path,
        layer=layer_name,
        columns=["source_layer"],
        read_geometry=False,
    )
    layers = {
        str(value)
        for value in source_layers["source_layer"]
        .dropna()
        .unique()
    }
    return layers

# ============================================================
# MAIN WORKFLOW
# ============================================================

def filter_klic_nopass_step2(
    temp20_path: str | Path,
    output_path: str | Path,
) -> None:
    """
    Create the Step 2 inductive no-pass layer from KLIC_temp20.

    Only features that receive a new Step 2 inductive no-pass are kept.
    The source infrastructure type is preserved in the
    'infrastructure_type' column.
    """
    temp20_path = Path(temp20_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    if output_path.resolve() == temp20_path.resolve():
        raise ValueError(
            "The output path must differ from the input path."
        )
    if output_path.exists():
        output_path.unlink()
    available_layers = get_layer_names(temp20_path)
    written_layers = []

    # ============================================================
    # CREATE NO-PASS LAYERS BY ASSESSMENT TYPE
    # ============================================================

    combined_influenced_layer = (
        "influenced_parts_with_runid_distance_no_pass"
    )

    direct_influencer_layers = [
        (
            "electricity_cable_parts_with_runid_no_pass",
            "Elektriciteitskabel",
        ),
        (
            "proposed_hv_parts_with_runid_no_pass",
            "proposed_highvoltage",
        ),
    ]

    for no_pass_type, output_layer_name in NO_PASS_OUTPUT_LAYERS.items():
        print(
            f"\nCreating '{output_layer_name}' "
            f"for '{no_pass_type}'..."
        )

        no_pass_parts = []

        # --------------------------------------------------------
        # INFLUENCED INFRASTRUCTURE
        # --------------------------------------------------------

        if combined_influenced_layer in available_layers:
            present_source_layers = get_source_layer_values(
                file_path=temp20_path,
                layer_name=combined_influenced_layer,
            )

            source_layer_lookup = {
                value.strip().casefold(): value
                for value in present_source_layers
            }

            actual_source_layers = [
                source_layer_lookup.get(layer_name.casefold())
                for layer_name in INFLUENCED_LAYERS
                if source_layer_lookup.get(layer_name.casefold()) is not None
            ]

            if actual_source_layers:
                where = (
                    f"{quote_sql_identifier('source_layer')} IN ("
                    + ", ".join(
                        quote_sql_value(value)
                        for value in actual_source_layers
                    )
                    + ")"
                )

                influenced_data = copy_no_pass_layer(
                    source_path=temp20_path,
                    output_path=output_path,
                    source_layer_name=combined_influenced_layer,
                    output_layer_name=output_layer_name,
                    requested_columns=INFLUENCED_COLUMNS,
                    where=where,
                    unique_id_column="influenced_run_id",
                    no_pass_type=no_pass_type,
                    write_output=False,
                )

                if (
                    isinstance(influenced_data, gpd.GeoDataFrame)
                    and not influenced_data.empty
                ):
                    no_pass_parts.append(influenced_data)

        # --------------------------------------------------------
        # INFLUENCERS
        # --------------------------------------------------------

        for direct_source_layer, infrastructure_type in (
            direct_influencer_layers
        ):
            if direct_source_layer not in available_layers:
                continue

            influencer_data = copy_no_pass_layer(
                source_path=temp20_path,
                output_path=output_path,
                source_layer_name=direct_source_layer,
                output_layer_name=output_layer_name,
                requested_columns=INFLUENCER_COLUMNS,
                unique_id_column="influenced_run_id",
                no_pass_type=no_pass_type,
                infrastructure_type=infrastructure_type,
                write_output=False,
            )

            if (
                isinstance(influencer_data, gpd.GeoDataFrame)
                and not influencer_data.empty
            ):
                no_pass_parts.append(influencer_data)

        # --------------------------------------------------------
        # COMBINE AND WRITE ONE ASSESSMENT-SPECIFIC LAYER
        # --------------------------------------------------------

        if not no_pass_parts:
            print(
                f"No '{no_pass_type}' no-pass features found."
            )
            continue

        geometry_column = no_pass_parts[0].geometry.name

        combined_data = pd.concat(
            no_pass_parts,
            ignore_index=True,
            sort=False,
        )

        no_pass_data = gpd.GeoDataFrame(
            combined_data,
            geometry=geometry_column,
            crs=no_pass_parts[0].crs,
        )

        no_pass_data.to_file(
            output_path,
            layer=output_layer_name,
            driver="GPKG",
            engine="pyogrio",
            index=False,
            SPATIAL_INDEX="NO",
        )

        written_layers.append(output_layer_name)

        print(
            f"Created '{output_layer_name}' with "
            f"{len(no_pass_data)} features "
            f"(influenced infrastructure + influencers)."
        )

# ============================================================
# RUN
# ============================================================
#if __name__ == "__main__":
#    filter_klic_nopass_step2(
#        temp19_path=(
#            "data/KLIC/KLIC_groenedijk/temp/"
#            "KLIC_temp20.gpkg"
#        ),
#        output_path=(
#            "data/KLIC/KLIC_groenedijk/temp/"
#            "KLIC_no_pass_step_2.gpkg"
#        ),
#    )
