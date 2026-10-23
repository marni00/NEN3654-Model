# Script 23: Merge full assessment results without no-pass filtering
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
# Assessment columns are NOT listed here. They are detected per layer
# (case-insensitive, with aliases) and renamed to their canonical name.
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
]
# Canonical assessment names and accepted source-column spellings.
#
# "resisitve" is included because it may exist as a typo in temp19.
ASSESSMENT_COLUMN_ALIASES = {
    "inductive": [
        "inductive",
    ],
    "thermal": [
        "thermal",
    ],
    "capacitive": [
        "capacitive",
    ],
    "resistive": [
        "resistive",
        "resisitve",
    ],
}
ASSESSMENT_COLUMNS = {
    "inductive": "inductive",
    "resistive": "resistive",
    "capacitive": "capacitive",
    "thermal": "thermal",
}
RESULT_OUTPUT_LAYERS = {
    "inductive": "result_inductive",
    "resistive": "result_resistive",
    "capacitive": "result_capacitive",
    "thermal": "result_thermal",
}


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
        raise FileNotFoundError(f"Input file not found: {file_path}")
    return _normalized_layer_names(
        file_path,
        pyogrio.list_layers(file_path)[:, 0],
    )


def get_available_columns(
    file_path: Path,
    layer_name: str,
    requested_columns: list[str],
) -> list[str]:
    """Return requested columns that exist in the layer."""
    fields = set(_read_model_info(file_path, layer=layer_name)["fields"])
    return [column for column in requested_columns if column in fields]


def find_assessment_column(
    file_path: Path,
    layer_name: str,
    canonical_name: str,
) -> str | None:
    """
    Find the actual source-column name for one assessment in a layer.

    Matching is case-insensitive and accepts the aliases listed in
    ASSESSMENT_COLUMN_ALIASES (e.g. the "resisitve" typo).
    Returns None when the layer does not contain the assessment.
    """
    fields = set(_read_model_info(file_path, layer=layer_name)["fields"])
    case_insensitive_lookup = {field.casefold(): field for field in fields}

    matches = []
    for alias in ASSESSMENT_COLUMN_ALIASES[canonical_name]:
        actual_name = case_insensitive_lookup.get(alias.casefold())
        if actual_name is not None and actual_name not in matches:
            matches.append(actual_name)

    if len(matches) > 1:
        raise ValueError(
            f"Layer '{layer_name}' contains multiple columns for "
            f"assessment '{canonical_name}': {', '.join(matches)}. "
            "Keep only one spelling."
        )
    if matches:
        return matches[0]

    print(
        f"Layer '{layer_name}' does not contain "
        f"assessment '{canonical_name}'."
    )
    return None


def add_infrastructure_type(
    layer_data: gpd.GeoDataFrame,
    infrastructure_type: str,
) -> gpd.GeoDataFrame:
    """Add the exact infrastructure type used for visualization."""
    layer_data = layer_data.copy()
    layer_data["infrastructure_type"] = infrastructure_type
    return layer_data


def read_layer(
    temp19_path: Path,
    layer_name: str,
    requested_columns: list[str],
    infrastructure_type: str,
    assessment_name: str,
) -> gpd.GeoDataFrame:
    """
    Read a layer together with the column of one assessment and add its
    visualization infrastructure type.

    The assessment column is renamed to its canonical name, so the rest of
    the workflow never has to deal with alternative spellings. An empty
    GeoDataFrame is returned when the layer has no such assessment column.
    """
    source_column = find_assessment_column(
        temp19_path,
        layer_name,
        assessment_name,
    )
    if source_column is None:
        return gpd.GeoDataFrame(geometry=[])

    available_columns = get_available_columns(
        temp19_path,
        layer_name,
        requested_columns,
    )
    if source_column not in available_columns:
        available_columns.append(source_column)

    data = _read_model_dataframe(
        temp19_path,
        layer=layer_name,
        columns=available_columns,
    )
    if data.empty:
        return data

    if source_column != assessment_name:
        data = data.rename(columns={source_column: assessment_name})

    return add_infrastructure_type(data, infrastructure_type)


def normalize_status(values: pd.Series) -> pd.Series:
    """Normalize assessment result text for run-level summaries."""
    return (
        values.astype("string")
        .str.strip()
        .str.casefold()
        .str.replace(r"[\s_–—]+", "-", regex=True)
    )


def filter_klic_step1(
    temp19_path: str | Path,
    output_path: str | Path,
) -> None:
    """
    Merge all assessment features without filtering any pass/no-pass rows.

    For each assessment type, all influenced infrastructure and direct
    influencer features are included. Segments belonging to the same
    influenced_run_id and infrastructure_type are dissolved together.

    A run-level assessment_result is added:
        Pass    = every contributing segment is Pass
        No pass = at least one contributing segment is not Pass
        Unknown = no usable assessment value is available
    """
    temp19_path = Path(temp19_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()

    available_layers = get_layer_names(temp19_path)
    influenced_layer = "influenced_parts_with_runid_distance"
    direct_influencer_layers = [
        ("electricity_cable_parts_with_runid", "Elektriciteitskabel"),
        ("proposed_hv_parts_with_runid", "proposed_highvoltage"),
    ]

    for assessment_name, output_layer_name in RESULT_OUTPUT_LAYERS.items():
        print(f"\nCreating '{output_layer_name}'...")
        parts = []
        assessment_column = ASSESSMENT_COLUMNS[assessment_name]

        if influenced_layer in available_layers:
            influenced_data = read_layer(
                temp19_path,
                influenced_layer,
                INFLUENCED_COLUMNS,
                "",
                assessment_name,
            )
            if not influenced_data.empty:
                source_values = influenced_data.get("source_layer")
                if source_values is not None:
                    lookup = {
                        name.casefold(): name
                        for name in INFLUENCED_LAYERS
                    }
                    influenced_data["infrastructure_type"] = (
                        source_values.astype("string")
                        .str.strip()
                        .str.casefold()
                        .map(lookup)
                    )
                parts.append(influenced_data)

        for source_layer, infrastructure_type in direct_influencer_layers:
            if source_layer not in available_layers:
                continue
            influencer_data = read_layer(
                temp19_path,
                source_layer,
                INFLUENCER_COLUMNS,
                infrastructure_type,
                assessment_name,
            )
            if not influencer_data.empty:
                parts.append(influencer_data)

        if not parts:
            print(f"No input features found for '{output_layer_name}'.")
            continue

        combined = gpd.GeoDataFrame(
            pd.concat(parts, ignore_index=True, sort=False),
            geometry=parts[0].geometry.name,
            crs=parts[0].crs,
        )
        combined["assessment_type"] = assessment_name

        if "influenced_run_id" not in combined.columns:
            raise ValueError(
                f"Layer '{output_layer_name}' cannot be created because "
                "'influenced_run_id' is missing."
            )

        with_id = combined[combined["influenced_run_id"].notna()].copy()
        without_id = combined[combined["influenced_run_id"].isna()].copy()

        if not with_id.empty:
            group_columns = [
                "influenced_run_id",
                "infrastructure_type",
                "assessment_type",
            ]
            geometry_column = with_id.geometry.name
            aggregation = {}
            for column in with_id.columns:
                if column in {geometry_column, *group_columns}:
                    continue
                if column == assessment_column:
                    aggregation[column] = "first"
                else:
                    aggregation[column] = "first"

            merged = dissolve_with_memory_fallback(
                with_id,
                by=group_columns,
                aggfunc=aggregation,
                as_index=False,
                sort=False,
            )

            # Summarize the assessment across the COMPLETE influenced run.
            # Any no-pass segment makes the whole run no-pass. The run is
            # Pass only when every usable segment explicitly says Pass.
            status_by_run = (
                with_id.assign(
                    _normalized_status=normalize_status(
                        with_id[assessment_column]
                    )
                )
                .groupby("influenced_run_id", dropna=False)["_normalized_status"]
                .agg(
                    lambda values: (
                        "No pass"
                        if values.eq("no-pass").any()
                        else (
                            "Pass"
                            if values.notna().all()
                            and values.eq("pass").all()
                            else "Unknown"
                        )
                    )
                )
                .reset_index(name="assessment_result")
            )
            merged = merged.merge(
                status_by_run,
                on="influenced_run_id",
                how="left",
                validate="many_to_one",
            )

            # Replace the old first-segment assessment value with the
            # assessment of the complete influenced run.
            merged[assessment_column] = merged["assessment_result"]
        else:
            merged = with_id

        if not without_id.empty:
            without_id["assessment_result"] = (
                normalize_status(without_id[assessment_column])
                .map({"pass": "Pass", "no-pass": "No pass"})
                .fillna("Unknown")
            )

        result = gpd.GeoDataFrame(
            pd.concat([merged, without_id], ignore_index=True, sort=False),
            geometry=combined.geometry.name,
            crs=combined.crs,
        )

        # Keep only the assessment column relevant to this output layer.
        other_assessment_columns = [
            column
            for column in ASSESSMENT_COLUMNS.values()
            if column != assessment_column
        ]
        result = result.drop(
            columns=other_assessment_columns,
            errors="ignore",
        )

        result.to_file(
            output_path,
            layer=output_layer_name,
            driver="GPKG",
            engine="pyogrio",
            mode="w" if not output_path.exists() else "a",
            index=False,
            SPATIAL_INDEX="NO",
        )
        print(
            f"Created '{output_layer_name}' with {len(result)} merged features."
        )

    # Copy Mast unchanged when available.
    if "Mast" in available_layers:
        mast = _read_model_dataframe(temp19_path, layer="Mast")
        if not mast.empty:
            mast.to_file(
                output_path,
                layer="Mast",
                driver="GPKG",
                engine="pyogrio",
                mode="a",
                index=False,
                SPATIAL_INDEX="NO",
            )
            print(f"Copied 'Mast' unchanged with {len(mast)} features.")


# ============================================================
# RUN
# ============================================================
#if __name__ == "__main__":
#    filter_klic_step1(
#        input_path=(
#            "data/KLIC/KLIC_groenedijk/temp/"
#            "KLIC_temp19.gpkg"
#        ),
#        output_path=(
#            "data/KLIC/KLIC_groenedijk/output/"
#            "KLIC_results_step1.gpkg"
#        ),
#    )