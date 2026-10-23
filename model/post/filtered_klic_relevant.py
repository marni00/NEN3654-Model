# Script 22: Filtered and cleaned on relevance

import sqlite3
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

from model.post.geometry_dissolve import dissolve_with_memory_fallback

from model.part2.relational_tables import read_table, table_exists


INFLUENCER_LAYERS = [
    "proposed_highvoltage",
    "Elektriciteitskabel",
]

INFLUENCED_LAYERS = [
    "OlieGasChemicalienPijpleiding",
    "Overig",
    "Waterleiding",
    "Rioolleiding",
    "ThermischePijpleiding"
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
    "verticalePositie",
    "werkVoltage",
    "werkVoltageM",
    "Eigenaar",
    "beheerder",
    "thema",
    "influencer_class",
]


def get_layer_names(file_path: Path) -> set[str]:
    """Return the layer names present in a GIS file."""

    if not file_path.exists():
        raise FileNotFoundError(
            f"Input file not found: {file_path}"
        )

    layer_information = pyogrio.list_layers(file_path)

    return set(layer_information[:, 0])


def get_available_columns(
    file_path: Path,
    layer_name: str,
    requested_columns: list[str],
) -> list[str]:
    """
    Return requested attribute columns that exist in the layer.

    The native FID and geometry are handled separately.
    """

    layer_info = pyogrio.read_info(
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


def restore_native_fids(
    output_path: Path,
    layer_name: str,
    temporary_fid_column: str,
) -> None:
    """
    Restore retained source FIDs as native GeoPackage FIDs.

    For a merged feature, the lowest source FID in its group is used.
    """

    quoted_layer = quote_sql_identifier(layer_name)
    quoted_temp_fid = quote_sql_identifier(
        temporary_fid_column
    )

    with sqlite3.connect(output_path) as connection:
        table_info = connection.execute(
            f"PRAGMA table_info({quoted_layer})"
        ).fetchall()

        # PRAGMA columns:
        # cid, name, type, notnull, default, primary_key
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

        # Move generated FIDs temporarily so that they cannot conflict
        # with the original source FIDs.
        connection.execute(
            f"""
            UPDATE {quoted_layer}
            SET {quoted_fid} = {quoted_fid} + ?
            """,
            (offset,),
        )

        # Restore the retained source FIDs.
        connection.execute(
            f"""
            UPDATE {quoted_layer}
            SET {quoted_fid} = {quoted_temp_fid}
            """
        )

        # Remove the temporary FID attribute.
        connection.execute(
            f"""
            ALTER TABLE {quoted_layer}
            DROP COLUMN {quoted_temp_fid}
            """
        )

        connection.commit()


def merge_features_by_id(
    layer_data: gpd.GeoDataFrame,
    unique_id_column: str,
    source_layer_name: str,
    output_layer_name: str,
) -> gpd.GeoDataFrame:
    """
    Merge geometries having the same non-null unique ID.

    Attribute values and native FID are taken from the member with
    the lowest original native FID. Rows with null IDs remain separate.
    """

    if unique_id_column not in layer_data.columns:
        raise ValueError(
            f"Cannot merge layer '{output_layer_name}': "
            f"column '{unique_id_column}' is not available."
        )

    geometry_column = layer_data.geometry.name
    merged_fid_column = "_merged_source_fid"

    if merged_fid_column in layer_data.columns:
        raise ValueError(
            f"Layer '{source_layer_name}' already contains a column "
            f"named '{merged_fid_column}'."
        )

    original_count = len(layer_data)

    # Sorting ensures that "first" means the row with the lowest
    # original native FID.
    layer_data = layer_data.sort_index()

    rows_with_id = layer_data.loc[
        layer_data[unique_id_column].notna()
    ].copy()

    rows_without_id = layer_data.loc[
        layer_data[unique_id_column].isna()
    ].copy()

    if not rows_with_id.empty:
        # Preserve the original source FID before dissolving.
        rows_with_id[merged_fid_column] = (
            rows_with_id.index.to_numpy()
        )

        # Dissolve performs a geometric union for every equal
        # influenced_run_id. All other attributes use the first row,
        # which is the row with the lowest source FID.
        merged_rows = dissolve_with_memory_fallback(
            rows_with_id,
            by=unique_id_column,
            aggfunc="first",
            as_index=False,
            sort=False,
        )

        merged_rows = merged_rows.set_index(
            merged_fid_column,
            drop=True,
        )
    else:
        merged_rows = rows_with_id

    # Null influenced_run_id rows cannot be matched and therefore
    # remain separate.
    combined_data = pd.concat(
        [merged_rows, rows_without_id],
        axis=0,
    ).sort_index()

    result = gpd.GeoDataFrame(
        combined_data,
        geometry=geometry_column,
        crs=layer_data.crs,
    )

    merged_count = original_count - len(result)

    print(
        f"Merged {original_count} source features into "
        f"{len(result)} features for '{output_layer_name}' using "
        f"'{unique_id_column}' ({merged_count} fewer rows)."
    )

    return result


def copy_layer_preserving_fid(
    source_path: Path,
    output_path: Path,
    source_layer_name: str,
    output_layer_name: str,
    requested_columns: list[str] | None,
    where: str | None = None,
    unique_id_column: str | None = None,
    prepared_layer_data: gpd.GeoDataFrame | None = None,
) -> int:
    """
    Copy a layer or layer selection while preserving native FIDs.

    If requested_columns is None, all source attributes are retained.

    If unique_id_column is supplied, features with equal non-null IDs
    are geometrically merged. Rows with null IDs remain separate.
    """

    temporary_fid_column = "_source_fid_temp"

    if prepared_layer_data is None:
        if requested_columns is None:
            layer_info = pyogrio.read_info(
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

        layer_data = pyogrio.read_dataframe(
            source_path,
            layer=source_layer_name,
            columns=selected_columns,
            where=where,
            fid_as_index=True,
        )
    else:
        if where is not None:
            raise ValueError(
                "'where' cannot be used with prepared layer data."
            )

        layer_data = prepared_layer_data.copy()
        if requested_columns is None:
            selected_columns = [
                column
                for column in layer_data.columns
                if column != layer_data.geometry.name
            ]
        else:
            selected_columns = [
                column
                for column in requested_columns
                if column in layer_data.columns
            ]
            missing_columns = [
                column
                for column in requested_columns
                if column not in layer_data.columns
            ]
            if missing_columns:
                print(
                    f"Prepared layer '{source_layer_name}' does not "
                    f"contain: {', '.join(missing_columns)}"
                )

    if temporary_fid_column in selected_columns:
        raise ValueError(
            f"Layer '{source_layer_name}' already contains a column "
            f"named '{temporary_fid_column}'."
        )

    if layer_data.empty:
        print(
            f"No features found for output layer "
            f"'{output_layer_name}'; skipping."
        )
        return 0

    # MERGE FEATURES BY UNIQUE ID
    # ============================================================

    if unique_id_column is not None:
        layer_data = merge_features_by_id(
            layer_data=layer_data,
            unique_id_column=unique_id_column,
            source_layer_name=source_layer_name,
            output_layer_name=output_layer_name,
        )

    geometry_column = layer_data.geometry.name

    # Store the retained source FID temporarily as an attribute.
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


def get_source_layer_values(
    file_path: Path,
    layer_name: str,
) -> set[str]:
    """Return the non-null source_layer values in a layer."""

    layer_info = pyogrio.read_info(
        file_path,
        layer=layer_name,
    )

    if "source_layer" not in set(layer_info["fields"]):
        raise ValueError(
            f"Layer '{layer_name}' does not contain the required "
            "'source_layer' column."
        )

    source_layers = pyogrio.read_dataframe(
        file_path,
        layer=layer_name,
        columns=["source_layer"],
        read_geometry=False,
    )

    return {
        str(value)
        for value in source_layers["source_layer"]
        .dropna()
        .unique()
    }



def _as_nullable_integer(
    dataframe: pd.DataFrame,
    columns: list[str],
) -> None:
    """Normalize integer join keys without changing missing values."""

    for column in columns:
        if column in dataframe.columns:
            dataframe[column] = pd.to_numeric(
                dataframe[column],
                errors="coerce",
            ).astype("Int64")


def _with_unique_export_index(
    layer_data: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Give materialized relationship rows unique, deterministic FIDs.

    One physical feature can participate in multiple normalized
    relationships, so its original FID cannot be reused for every
    materialized output row.
    """

    result = layer_data.sort_values(
        ["_source_fid_order", "influenced_run_id"],
        kind="stable",
    ).drop(columns=["_source_fid_order"])

    result = gpd.GeoDataFrame(
        result,
        geometry=layer_data.geometry.name,
        crs=layer_data.crs,
    )
    result.index = pd.RangeIndex(
        start=1,
        stop=len(result) + 1,
        name="fid",
    )
    return result


def _prepare_influencer_relationship_layer(
    *,
    temp18_path: Path,
    source_layer_name: str,
    reference_type: str,
    relationships: pd.DataFrame,
) -> gpd.GeoDataFrame:
    """Materialize normalized influencer relationships for final export."""

    available_columns = get_available_columns(
        file_path=temp18_path,
        layer_name=source_layer_name,
        requested_columns=list(
            dict.fromkeys(
                INFLUENCER_COLUMNS
                + ["feature_id", "run_id"]
            )
        ),
    )
    base = pyogrio.read_dataframe(
        temp18_path,
        layer=source_layer_name,
        columns=available_columns,
        fid_as_index=True,
    )
    base["_source_fid_order"] = base.index.to_numpy()

    relationship_columns = [
        "reference_feature_id",
        "run_id",
        "influenced_run_id",
        "a",
        "l",
    ]
    missing_relationship_columns = [
        column
        for column in relationship_columns + ["reference_type"]
        if column not in relationships.columns
    ]
    if missing_relationship_columns:
        raise ValueError(
            "Normalized table 'run_relationships' is missing "
            f"columns: {missing_relationship_columns}"
        )

    relevant_relationships = relationships.loc[
        relationships["reference_type"].eq(reference_type),
        relationship_columns,
    ].rename(
        columns={"reference_feature_id": "feature_id"}
    )

    for frame in [base, relevant_relationships]:
        _as_nullable_integer(
            frame,
            ["feature_id", "run_id"],
        )

    base = base.drop(
        columns=["influenced_run_id", "a", "l"],
        errors="ignore",
    )
    expanded = base.merge(
        relevant_relationships,
        on=["feature_id", "run_id"],
        how="inner",
        validate="many_to_many",
    )
    expanded = gpd.GeoDataFrame(
        expanded,
        geometry=base.geometry.name,
        crs=base.crs,
    )
    return _with_unique_export_index(expanded)


def _prepare_influenced_relationship_layer(
    *,
    temp18_path: Path,
    source_layer_name: str,
    source_layer_value: str,
    memberships: pd.DataFrame,
    relationships: pd.DataFrame,
) -> gpd.GeoDataFrame:
    """Materialize normalized influenced relationships for final export."""

    available_columns = get_available_columns(
        file_path=temp18_path,
        layer_name=source_layer_name,
        requested_columns=list(
            dict.fromkeys(
                INFLUENCED_COLUMNS
                + ["source_layer", "feature_id", "part_no"]
            )
        ),
    )
    where = (
        f"{quote_sql_identifier('source_layer')} = "
        f"{quote_sql_value(source_layer_value)}"
    )
    base = pyogrio.read_dataframe(
        temp18_path,
        layer=source_layer_name,
        columns=available_columns,
        where=where,
        fid_as_index=True,
    )
    base["_source_fid_order"] = base.index.to_numpy()

    membership_columns = [
        "source_layer",
        "feature_id",
        "part_no",
        "run_id",
        "influenced_run_id",
        "influenced_component_length_m",
    ]
    missing_membership_columns = [
        column
        for column in membership_columns
        if column not in memberships.columns
    ]
    if missing_membership_columns:
        raise ValueError(
            "Normalized table 'run_memberships' is missing "
            f"columns: {missing_membership_columns}"
        )

    relevant_memberships = memberships.loc[
        memberships["source_layer"].astype(str).eq(
            source_layer_value
        ),
        membership_columns,
    ].copy()

    for frame in [base, relevant_memberships]:
        _as_nullable_integer(
            frame,
            ["feature_id", "part_no", "run_id"],
        )
        frame["source_layer"] = (
            frame["source_layer"].astype(str).str.strip()
        )

    base = base.drop(
        columns=[
            "run_id",
            "influenced_run_id",
            "a",
            "l",
        ],
        errors="ignore",
    )
    expanded = base.merge(
        relevant_memberships,
        on=["source_layer", "feature_id", "part_no"],
        how="inner",
        validate="many_to_many",
    )

    relationship_columns = [
        "run_id",
        "influenced_run_id",
        "a",
        "l",
    ]
    missing_relationship_columns = [
        column
        for column in relationship_columns
        if column not in relationships.columns
    ]
    if missing_relationship_columns:
        raise ValueError(
            "Normalized table 'run_relationships' is missing "
            f"columns: {missing_relationship_columns}"
        )

    relationship_values = relationships[
        relationship_columns
    ].drop_duplicates(
        ["run_id", "influenced_run_id"]
    ).copy()
    _as_nullable_integer(relationship_values, ["run_id"])
    relationship_values["influenced_run_id"] = (
        relationship_values["influenced_run_id"]
        .astype(str)
        .str.strip()
    )
    expanded["influenced_run_id"] = (
        expanded["influenced_run_id"].astype(str).str.strip()
    )
    expanded = expanded.merge(
        relationship_values,
        on=["run_id", "influenced_run_id"],
        how="left",
        validate="many_to_one",
    )
    expanded = gpd.GeoDataFrame(
        expanded,
        geometry=base.geometry.name,
        crs=base.crs,
    )
    return _with_unique_export_index(expanded)


def combine_relevant_features(
    temp18_path: str | Path,
    output_path: str | Path,
) -> None:
    """
    Create cleaned KLIC layers from KLIC_temp18.

    Direct mappings:
    - electricity_cable_parts_with_runid -> Elektriciteitskabel
    - proposed_hv_parts_with_runid -> proposed_highvoltage
    - Mast -> Mast, unchanged

    influenced_parts_with_runid_distance is split into separate
    layers using its source_layer attribute.

    Both influencer and influenced features are merged within their
    output layer when influenced_run_id is equal.
    """

    # INPUT
    # ============================================================

    temp18_path = Path(temp18_path)
    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_path.resolve() == temp18_path.resolve():
        raise ValueError(
            "The output path must differ from the input path."
        )

    if output_path.exists():
        output_path.unlink()

    available_layers = get_layer_names(temp18_path)
    written_layers = []

    normalized_relationships = table_exists(
        temp18_path,
        "run_relationships",
    )
    if normalized_relationships:
        if not table_exists(temp18_path, "run_memberships"):
            raise ValueError(
                "KLIC_temp18 contains 'run_relationships' but not "
                "the required 'run_memberships' table."
            )
        relationships = read_table(
            temp18_path,
            "run_relationships",
        )
        memberships = read_table(
            temp18_path,
            "run_memberships",
        )
    else:
        relationships = None
        memberships = None

    # DIRECT INFLUENCER LAYERS
    # ============================================================

    direct_influencer_layers = [
        (
            "electricity_cable_parts_with_runid",
            "Elektriciteitskabel",
            "cable",
        ),
        (
            "proposed_hv_parts_with_runid",
            "proposed_highvoltage",
            "hv",
        ),
    ]

    for (
        direct_source_layer,
        output_layer_name,
        reference_type,
    ) in direct_influencer_layers:
        if direct_source_layer not in available_layers:
            print(
                f"Input layer '{direct_source_layer}' is not present; "
                "skipping."
            )
            continue

        print(
            f"\nCreating influencer layer "
            f"'{output_layer_name}' from "
            f"'{direct_source_layer}'..."
        )

        prepared_layer_data = None
        if normalized_relationships:
            prepared_layer_data = (
                _prepare_influencer_relationship_layer(
                    temp18_path=temp18_path,
                    source_layer_name=direct_source_layer,
                    reference_type=reference_type,
                    relationships=relationships,
                )
            )

        feature_count = copy_layer_preserving_fid(
            source_path=temp18_path,
            output_path=output_path,
            source_layer_name=direct_source_layer,
            output_layer_name=output_layer_name,
            requested_columns=INFLUENCER_COLUMNS,
            unique_id_column="influenced_run_id",
            prepared_layer_data=prepared_layer_data,
        )

        if feature_count > 0:
            written_layers.append(output_layer_name)

            print(
                f"Copied '{output_layer_name}' with "
                f"{feature_count} features."
            )

    # SPLIT INFLUENCED FEATURES
    # ============================================================

    combined_influenced_layer = (
        "influenced_parts_with_runid_distance"
    )

    if combined_influenced_layer in available_layers:
        present_source_layers = get_source_layer_values(
            file_path=temp18_path,
            layer_name=combined_influenced_layer,
        )

        print(
            f"\nValues found in "
            f"'{combined_influenced_layer}.source_layer':"
        )

        for source_layer_value in sorted(
            present_source_layers,
            key=str.casefold,
        ):
            print(f"  - {source_layer_value}")

        # Match source_layer values case-insensitively while retaining
        # their original input values for the SQL selection.
        source_layer_lookup: dict[str, str] = {}

        for source_layer_value in present_source_layers:
            normalized_value = (
                source_layer_value.strip().casefold()
            )

            if normalized_value in source_layer_lookup:
                other_value = source_layer_lookup[
                    normalized_value
                ]

                raise ValueError(
                    "Multiple source_layer values differ only in "
                    "capitalization or whitespace: "
                    f"'{other_value}' and "
                    f"'{source_layer_value}'."
                )

            source_layer_lookup[normalized_value] = (
                source_layer_value
            )

        # INFLUENCED_LAYERS supplies the canonical output names.
        for output_layer_name in INFLUENCED_LAYERS:
            normalized_output_name = (
                output_layer_name.strip().casefold()
            )

            actual_source_layer_value = (
                source_layer_lookup.get(
                    normalized_output_name
                )
            )

            if actual_source_layer_value is None:
                print(
                    f"\nNo influenced features found for "
                    f"'{output_layer_name}'; skipping."
                )
                continue

            where = (
                f"{quote_sql_identifier('source_layer')} = "
                f"{quote_sql_value(actual_source_layer_value)}"
            )

            print(
                f"\nCreating influenced layer "
                f"'{output_layer_name}' from source_layer "
                f"'{actual_source_layer_value}'..."
            )

            prepared_layer_data = None
            copy_where = where
            if normalized_relationships:
                prepared_layer_data = (
                    _prepare_influenced_relationship_layer(
                        temp18_path=temp18_path,
                        source_layer_name=combined_influenced_layer,
                        source_layer_value=actual_source_layer_value,
                        memberships=memberships,
                        relationships=relationships,
                    )
                )
                copy_where = None

            feature_count = copy_layer_preserving_fid(
                source_path=temp18_path,
                output_path=output_path,
                source_layer_name=combined_influenced_layer,
                output_layer_name=output_layer_name,
                requested_columns=INFLUENCED_COLUMNS,
                where=copy_where,
                unique_id_column="influenced_run_id",
                prepared_layer_data=prepared_layer_data,
            )

            if feature_count > 0:
                written_layers.append(output_layer_name)

                print(
                    f"Copied '{output_layer_name}' with "
                    f"{feature_count} features."
                )
    else:
        print(
            f"\nInput layer '{combined_influenced_layer}' "
            "is not present; skipping influenced layers."
        )

    # COPY MAST UNCHANGED
    # ============================================================

    if "Mast" in available_layers:
        print("\nCopying 'Mast' unchanged...")

        feature_count = copy_layer_preserving_fid(
            source_path=temp18_path,
            output_path=output_path,
            source_layer_name="Mast",
            output_layer_name="Mast",
            requested_columns=None,
        )

        if feature_count > 0:
            written_layers.append("Mast")

            print(
                f"Copied 'Mast' with "
                f"{feature_count} features."
            )
    else:
        print(
            "\nInput layer 'Mast' is not present; skipping."
        )

    # RESULT
    # ============================================================

    if not written_layers:
        raise ValueError(
            "No output layers were written. Check the input "
            "layer names and source_layer values."
        )

    print(f"\nCreated: {output_path}")
    print("Written layers:")

    for layer_name in written_layers:
        print(f"  - {layer_name}")


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    combine_relevant_features(
        temp18_path=(
            "data/KLIC/KLIC_groenedijk/temp/"
            "KLIC_temp18.gpkg"
        ),
        output_path=(
            "data/KLIC/KLIC_groenedijk/output/"
            "KLIC_filtered.gpkg"
        ),
    )