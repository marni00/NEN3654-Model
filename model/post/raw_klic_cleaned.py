#Script 21
import sqlite3
from pathlib import Path

import geopandas as gpd
import pyogrio

INFLUENCER_LAYERS = [
    "proposed_highvoltage",
    "Elektriciteitskabel",
]

INFLUENCED_LAYERS = [
    "OlieGasChemicalienPijpleiding",
    "Overig",
    "Waterleiding",
    "Rioolleiding",
    "ThermischePijpleiding",
]


INFLUENCED_COLUMNS = [
    "id",
    "currentStatus",
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
]

INFLUENCER_COLUMNS = [
    "id",
    "currentStatus",
    "verticalePositie",
    "werkVoltage",
    "werkVoltageM",
    "Eigenaar",
    "beheerder",
    "thema",
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

    The native fid and geometry are handled separately by GDAL.
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

def restore_native_fids(
    output_path: Path,
    layer_name: str,
    temporary_fid_column: str,
) -> None:
    """
    Restore the original source FIDs as the native GeoPackage FIDs.
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
                f"Could not determine the native FID column "
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

        # Temporarily move generated FIDs out of the way to avoid
        # primary-key conflicts.
        connection.execute(
            f"""
            UPDATE {quoted_layer}
            SET {quoted_fid} = {quoted_fid} + ?
            """,
            (offset,),
        )

        # Restore the original source FIDs.
        connection.execute(
            f"""
            UPDATE {quoted_layer}
            SET {quoted_fid} = {quoted_temp_fid}
            """
        )

        # Remove the temporary source-FID attribute.
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
    layer_name: str,
    requested_columns: list[str],
) -> int:
    """
    Copy and clean one layer while preserving its native FIDs.

    Returns the number of copied features.
    """

    temporary_fid_column = "_source_fid_temp"

    selected_columns = get_available_columns(
        file_path=source_path,
        layer_name=layer_name,
        requested_columns=requested_columns,
    )

    # Read the native GeoPackage FID into the index.
    layer_data = pyogrio.read_dataframe(
        source_path,
        layer=layer_name,
        columns=selected_columns,
        fid_as_index=True,
    )

    if layer_data.empty:
        print(f"Layer '{layer_name}' is empty; skipping.")
        return 0

    geometry_column = layer_data.geometry.name

    if temporary_fid_column in layer_data.columns:
        raise ValueError(
            f"Layer '{layer_name}' already contains a column named "
            f"'{temporary_fid_column}'."
        )

    # Save the original native FID temporarily as an attribute.
    layer_data[temporary_fid_column] = (
        layer_data.index.to_numpy()
    )

    columns_to_keep = (
        selected_columns
        + [temporary_fid_column, geometry_column]
    )

    cleaned_data = layer_data[columns_to_keep].copy()

    # No spatial index is created because the native FIDs are changed
    # directly afterwards.
    cleaned_data.to_file(
        output_path,
        layer=layer_name,
        driver="GPKG",
        engine="pyogrio",
        index=False,
        SPATIAL_INDEX="NO",
    )

    restore_native_fids(
        output_path=output_path,
        layer_name=layer_name,
        temporary_fid_column=temporary_fid_column,
    )

    return len(cleaned_data)

def combine_cleaned_dwg(
    temp5_path: str | Path,
    temp9_path: str | Path,
    output_path: str | Path,
) -> None:
    """
    Combine selected layers while preserving their original FIDs.

    Influenced layers are taken from temp5.
    Influencer layers are taken from temp9.
    """

    # INPUT
    # ============================================================

    temp5_path = Path(temp5_path)
    temp9_path = Path(temp9_path)
    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_path.resolve() in {
        temp5_path.resolve(),
        temp9_path.resolve(),
    }:
        raise ValueError(
            "The output path must differ from the input paths."
        )

    if output_path.exists():
        output_path.unlink()

    # AVAILABLE LAYERS
    # ============================================================

    temp5_available_layers = get_layer_names(temp5_path)
    temp9_available_layers = get_layer_names(temp9_path)

    layers_to_copy = []

    for layer_name in INFLUENCED_LAYERS:
        if layer_name in temp5_available_layers:
            layers_to_copy.append(
                (
                    temp5_path,
                    layer_name,
                    INFLUENCED_COLUMNS,
                    "influenced",
                )
            )
        else:
            print(
                f"Influenced layer '{layer_name}' is not present "
                "in temp5; skipping."
            )

    for layer_name in INFLUENCER_LAYERS:
        if layer_name in temp9_available_layers:
            layers_to_copy.append(
                (
                    temp9_path,
                    layer_name,
                    INFLUENCER_COLUMNS,
                    "influencer",
                )
            )
        else:
            print(
                f"Influencer layer '{layer_name}' is not present "
                "in temp9; skipping."
            )

    if not layers_to_copy:
        raise ValueError(
            "None of the requested layers were found."
        )

    # COPY AND PRESERVE FIDS
    # ============================================================

    written_layers = []

    for (
        source_path,
        layer_name,
        requested_columns,
        layer_type,
    ) in layers_to_copy:

        print(
            f"Copying {layer_type} layer '{layer_name}' "
            f"from '{source_path.name}'..."
        )

        feature_count = copy_layer_preserving_fid(
            source_path=source_path,
            output_path=output_path,
            layer_name=layer_name,
            requested_columns=requested_columns,
        )

        if feature_count > 0:
            written_layers.append(layer_name)

            print(
                f"Copied '{layer_name}' with {feature_count} features "
                "while preserving its FIDs."
            )

    print(f"\nCreated: {output_path}")
    print("Written layers:")

    for layer_name in written_layers:
        print(f"  - {layer_name}")


# ============================================================
# RUN
# ============================================================

#if __name__ == "__main__":
#    combine_cleaned_dwg(
#        temp5_path=(
#            "data/KLIC/KLIC_groenedijk/temp/"
#            "KLIC_temp5.gpkg"
#        ),
#        temp9_path=(
#            "data/KLIC/KLIC_groenedijk/temp/"
#            "KLIC_temp9.gpkg"
#        ),
#        output_path=(
#            "data/KLIC/KLIC_groenedijk/temp/"
#            "KLIC_raw_cleaned.gpkg"
#        ),
#    )