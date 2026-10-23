from __future__ import annotations

import logging
import math
import sqlite3
import struct
import warnings
from pathlib import Path
import tomllib

import pandas as pd
import pyogrio

from .assessment_storage import (
    CABLE_LAYER,
    copy_normalized_assessment,
    is_normalized_assessment,
    read_assessed_layer,
)

logger = logging.getLogger(__name__)


_GPKG_ENVELOPE_VALUE_COUNTS = {
    0: 0,
    1: 4,  # min_x, max_x, min_y, max_y
    2: 6,  # XY + min_z, max_z
    3: 6,  # XY + min_m, max_m
    4: 8,  # XY + min_z, max_z, min_m, max_m
}


def _geometry_details(value: object) -> tuple[bool, tuple[float, float, float, float] | None]:
    """Return ``(is_empty, bounds)`` for GeoPackage geometry or plain WKB."""
    if value is None:
        return True, None

    try:
        data = bytes(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Geometry value is not binary data.") from exc

    if len(data) >= 8 and data[:2] == b"GP":
        flags = data[3]
        little_endian = bool(flags & 0b00000001)
        envelope_type = (flags >> 1) & 0b00000111
        is_empty = bool(flags & 0b00010000)

        value_count = _GPKG_ENVELOPE_VALUE_COUNTS.get(envelope_type)
        if value_count is None:
            raise ValueError(
                f"Unsupported GeoPackage geometry envelope type: {envelope_type}"
            )

        geometry_offset = 8 + (value_count * 8)
        if len(data) < geometry_offset:
            raise ValueError("Truncated GeoPackage geometry header.")

        if is_empty:
            return True, None

        if value_count:
            byte_order = "<" if little_endian else ">"
            values = struct.unpack(
                f"{byte_order}{value_count}d",
                data[8:geometry_offset],
            )
            bounds = (values[0], values[2], values[1], values[3])
            if all(math.isfinite(value) for value in bounds):
                return False, bounds

        wkb_data = data[geometry_offset:]
    else:
        wkb_data = data

    if not wkb_data:
        raise ValueError("Geometry does not contain WKB data.")

    # GeoPackage normally stores an XY envelope, so this is only a fallback
    # for valid packages that deliberately omit it.
    from shapely import wkb

    geometry = wkb.loads(wkb_data)
    if geometry.is_empty:
        return True, None
    min_x, min_y, max_x, max_y = geometry.bounds
    bounds = (float(min_x), float(min_y), float(max_x), float(max_y))
    if not all(math.isfinite(value) for value in bounds):
        raise ValueError("Geometry has non-finite bounds.")
    return False, bounds


def _register_geopackage_geometry_functions(connection: sqlite3.Connection) -> None:
    """
    Register the spatial functions used by GeoPackage RTree update triggers.

    GDAL-created GeoPackages include generic ``AFTER UPDATE`` triggers. SQLite
    may evaluate their spatial expressions even when only a non-geometry
    attribute changes. Python's built-in sqlite3 connection does not provide
    the required functions, so an otherwise ordinary Code update fails with
    ``no such function: ST_IsEmpty``.
    """

    def is_empty(value: object) -> int | None:
        if value is None:
            return None
        empty, _ = _geometry_details(value)
        return int(empty)

    def bound(value: object, index: int) -> float | None:
        if value is None:
            return None
        empty, bounds = _geometry_details(value)
        if empty or bounds is None:
            return None
        return bounds[index]

    connection.create_function("ST_IsEmpty", 1, is_empty, deterministic=True)
    connection.create_function(
        "ST_MinX", 1, lambda value: bound(value, 0), deterministic=True
    )
    connection.create_function(
        "ST_MinY", 1, lambda value: bound(value, 1), deterministic=True
    )
    connection.create_function(
        "ST_MaxX", 1, lambda value: bound(value, 2), deterministic=True
    )
    connection.create_function(
        "ST_MaxY", 1, lambda value: bound(value, 3), deterministic=True
    )



def assign_electricity_cable_codes(
    input_path: Path,
    output_path: Path,
    config_path: Path,
    layer_name: str = "Elektriciteitskabel",
    id_column: str = "id",
) -> None:
    """
    Add a 'Code' column to the Elektriciteitskabel layer based on the
    [[Elektriciteitskabel]] entries in config.toml.

    Configured values are matched only against the persistent ``id``
    attribute assigned in temp9. Native GeoPackage FIDs are deliberately
    ignored because they may change whenever a GeoPackage is rewritten.

    The deprecated config key ``fid`` is accepted as an alias for ``id``.
    If a configured ID does not exist, an error is logged and processing
    continues. All input layers are retained in the output GeoPackage.
    """
    if not input_path.exists():
        raise FileNotFoundError(f"Input GeoPackage was not found:\n{input_path}")

    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file was not found:\n{config_path}")

    with config_path.open("rb") as file:
        config = tomllib.load(file)

    cable_entries = config.get("Elektriciteitskabel", [])

    if not isinstance(cable_entries, list):
        raise ValueError(
            "Expected 'Elektriciteitskabel' to be an array of tables in "
            f"{config_path}. Use [[Elektriciteitskabel]]."
        )

    available_layers = list(pyogrio.list_layers(input_path)[:, 0])

    if layer_name not in available_layers:
        raise ValueError(
            f"Layer '{layer_name}' was not found in:\n{input_path}\n\n"
            f"Available layers: {available_layers}"
        )

    # Safely overwrite an earlier output from this same pipeline step.
    if output_path.exists():
        output_path.unlink()

    for layer_index, current_layer in enumerate(available_layers):
        # fid_as_index=True makes the internal GeoPackage FID available
        # as the GeoDataFrame index.
        gdf = pyogrio.read_dataframe(
            input_path,
            layer=current_layer,
            fid_as_index=True,
        )

        if current_layer == layer_name:
            if id_column not in gdf.columns:
                raise KeyError(
                    f"Layer '{layer_name}' does not contain the required "
                    f"persistent '{id_column}' column."
                )

            persistent_ids = pd.to_numeric(gdf[id_column], errors="coerce")
            invalid_ids = (
                persistent_ids.isna()
                | persistent_ids.mod(1).ne(0)
                | persistent_ids.le(0)
            )
            if invalid_ids.any():
                bad_values = gdf.loc[invalid_ids, id_column].head(10).tolist()
                raise ValueError(
                    f"Layer '{layer_name}' contains invalid persistent IDs: "
                    f"{bad_values}"
                )
            persistent_ids = persistent_ids.astype("int64")
            duplicate_ids = persistent_ids[
                persistent_ids.duplicated(keep=False)
            ]
            if not duplicate_ids.empty:
                raise ValueError(
                    f"Layer '{layer_name}' contains duplicate persistent IDs: "
                    f"{sorted(duplicate_ids.unique().tolist())[:10]}"
                )

            # Reset/create Code on each pipeline run.
            gdf["Code"] = pd.NA

            for entry in cable_entries:
                has_ids = "id" in entry
                has_legacy_fids = "fid" in entry
                if has_ids and has_legacy_fids:
                    logger.error(
                        "Invalid [[Elektriciteitskabel]] entry containing both "
                        "'id' and deprecated 'fid': %s",
                        entry,
                    )
                    continue
                if has_legacy_fids:
                    warnings.warn(
                        "[[Elektriciteitskabel]].fid is deprecated; use 'id'. "
                        "Values are matched against the persistent id attribute.",
                        DeprecationWarning,
                        stacklevel=2,
                    )

                configured_ids = entry.get("id", entry.get("fid"))
                code = entry.get("code")

                if (
                    not isinstance(configured_ids, list)
                    or not configured_ids
                    or any(
                        isinstance(feature_id, bool)
                        or not isinstance(feature_id, int)
                        for feature_id in configured_ids
                    )
                    or code is None
                ):
                    logger.error(
                        "Invalid [[Elektriciteitskabel]] configuration entry: %s. "
                        "Expected both 'id = [...]' and 'code = \"...\"'.",
                        entry,
                    )
                    continue

                for feature_id in configured_ids:
                    matching_features = persistent_ids.eq(feature_id)

                    if not matching_features.any():
                        logger.error(
                            "Configured persistent ID=%r for Code=%r does not "
                            "exist in layer '%s'. Continuing.",
                            feature_id,
                            code,
                            layer_name,
                        )
                        continue

                    gdf.loc[matching_features, "Code"] = code

                    logger.info(
                        "Assigned Code=%r to %d feature(s) with persistent ID=%r.",
                        code,
                        int(matching_features.sum()),
                        feature_id,
                    )

        # First layer creates the output GeoPackage; remaining layers append.
        gdf.to_file(
            output_path,
            layer=current_layer,
            driver="GPKG",
            engine="pyogrio",
            append=layer_index > 0,
            index=False,
        )

    logger.info(
        "Electricity cable codes assigned. Output written to: %s",
        output_path,
    )

def assign_electricity_cable_codes_to_step1(
    input_path: Path,
    output_path: Path,
    config_path: Path,
    id_column: str = "id",
) -> None:
    """
    Apply GUI cable-code assignments to the Step 1 result.

    Step 1 may contain multiple segmented rows for one persistent temp9 ID.
    Every downstream electricity-cable row carrying a configured ``id`` gets
    the same Code. Other layers are copied unchanged. The original Step 1
    GeoPackage is never modified.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    config_path = Path(config_path)

    if not input_path.is_file():
        raise FileNotFoundError(f"Step 1 GeoPackage was not found:\n{input_path}")
    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file was not found:\n{config_path}")

    with config_path.open("rb") as file:
        config = tomllib.load(file)

    cable_entries = config.get("Elektriciteitskabel", [])
    if isinstance(cable_entries, dict):
        cable_entries = [cable_entries]
    if not isinstance(cable_entries, list):
        raise ValueError(
            "Expected 'Elektriciteitskabel' to be an array of tables in "
            f"{config_path}. Use [[Elektriciteitskabel]]."
        )

    code_by_id: dict[int, str] = {}
    for entry in cable_entries:
        if not isinstance(entry, dict):
            logger.error("Invalid [[Elektriciteitskabel]] entry: %r", entry)
            continue
        configured_ids = entry.get("id", entry.get("fid"))
        code = str(entry.get("code", "")).strip()
        if (
            not isinstance(configured_ids, list)
            or not configured_ids
            or not code
            or any(
                isinstance(feature_id, bool) or not isinstance(feature_id, int)
                for feature_id in configured_ids
            )
        ):
            logger.error(
                "Invalid [[Elektriciteitskabel]] configuration entry: %s. "
                "Expected both 'id = [...]' and a non-empty 'code'.",
                entry,
            )
            continue
        for feature_id in configured_ids:
            code_by_id[int(feature_id)] = code

    if is_normalized_assessment(input_path):
        copy_normalized_assessment(input_path, output_path)
        with sqlite3.connect(output_path) as connection:
            _register_geopackage_geometry_functions(connection)
            cable_layer_exists = connection.execute(
                "SELECT 1 FROM sqlite_master "
                "WHERE type = 'table' AND name = ?",
                (CABLE_LAYER,),
            ).fetchone()
            if cable_layer_exists is None:
                logger.info(
                    "The normalized Step 1 result contains no electricity-cable "
                    "layer; copying it unchanged for proposed-HV Step 2 processing."
                )
                for feature_id in sorted(code_by_id):
                    logger.error(
                        "Configured persistent ID=%r cannot be assigned because "
                        "the Step 1 result contains no electricity-cable layer. "
                        "Continuing.",
                        feature_id,
                    )
                return

            columns = {
                row[1]
                for row in connection.execute(
                    f'PRAGMA table_info("{CABLE_LAYER}")'
                )
            }
            if id_column not in columns:
                raise KeyError(
                    f"Layer '{CABLE_LAYER}' does not contain the required "
                    f"persistent '{id_column}' column."
                )
            if "Code" not in columns:
                connection.execute(
                    f'ALTER TABLE "{CABLE_LAYER}" ADD COLUMN "Code" TEXT'
                )
            connection.execute(
                f'UPDATE "{CABLE_LAYER}" SET "Code" = NULL'
            )
            existing_ids = {
                int(row[0])
                for row in connection.execute(
                    f'SELECT DISTINCT "{id_column}" '
                    f'FROM "{CABLE_LAYER}" '
                    f'WHERE "{id_column}" IS NOT NULL'
                )
            }
            for feature_id, code in code_by_id.items():
                if feature_id not in existing_ids:
                    logger.error(
                        "Configured persistent ID=%r does not exist in the "
                        "Step 1 electricity-cable population. Continuing.",
                        feature_id,
                    )
                    continue
                connection.execute(
                    f'UPDATE "{CABLE_LAYER}" SET "Code" = ? '
                    f'WHERE "{id_column}" = ?',
                    (code, feature_id),
                )
            connection.commit()
        return

    available_layers = list(pyogrio.list_layers(input_path)[:, 0])
    cable_layers = {
        layer
        for layer in available_layers
        if str(layer).startswith("electricity_cable_parts_with_runid")
    }
    if not cable_layers:
        logger.info(
            "The Step 1 result contains no electricity-cable layers; "
            "copying all layers unchanged for proposed-HV Step 2 processing."
        )

    if output_path.exists():
        output_path.unlink()

    matched_ids: set[int] = set()
    for layer_index, current_layer in enumerate(available_layers):
        gdf = pyogrio.read_dataframe(
            input_path,
            layer=current_layer,
            fid_as_index=True,
        )

        if current_layer in cable_layers:
            if id_column not in gdf.columns:
                raise KeyError(
                    f"Layer '{current_layer}' does not contain the required "
                    f"persistent '{id_column}' column."
                )
            persistent_ids = pd.to_numeric(gdf[id_column], errors="coerce")
            invalid_ids = (
                persistent_ids.isna()
                | persistent_ids.mod(1).ne(0)
                | persistent_ids.le(0)
            )
            if invalid_ids.any():
                bad_values = gdf.loc[invalid_ids, id_column].head(10).tolist()
                raise ValueError(
                    f"Layer '{current_layer}' contains invalid persistent IDs: "
                    f"{bad_values}"
                )
            persistent_ids = persistent_ids.astype("int64")
            gdf["Code"] = pd.NA
            for feature_id, code in code_by_id.items():
                matching_features = persistent_ids.eq(feature_id)
                if matching_features.any():
                    gdf.loc[matching_features, "Code"] = code
                    matched_ids.add(feature_id)

        gdf.to_file(
            output_path,
            layer=current_layer,
            driver="GPKG",
            engine="pyogrio",
            append=layer_index > 0,
            index=False,
        )

    missing_ids = sorted(set(code_by_id).difference(matched_ids))
    for feature_id in missing_ids:
        logger.error(
            "Configured persistent ID=%r does not exist in the Step 1 "
            "electricity-cable population. Continuing.",
            feature_id,
        )


def step1_electricity_candidate_ids(
    input_path: Path,
    id_column: str = "id",
) -> list[int]:
    """Return persistent cable IDs that remain for NEN 3654 Step 2."""
    input_path = Path(input_path)
    layer_name = "electricity_cable_parts_with_runid_no_pass"
    if is_normalized_assessment(input_path):
        available_layers = set(pyogrio.list_layers(input_path)[:, 0])
        if CABLE_LAYER not in available_layers:
            return []
        gdf = read_assessed_layer(
            input_path,
            CABLE_LAYER,
            no_pass=True,
        )
        if gdf.empty:
            return []
        missing = {id_column, "inductive"}.difference(gdf.columns)
        if missing:
            raise ValueError(
                f"Normalized Step 1 cable data is missing required columns: "
                + ", ".join(sorted(missing))
            )
        normalized = (
            gdf["inductive"]
            .astype("string")
            .str.strip()
            .str.casefold()
            .str.replace(r"[\s_–—]+", "-", regex=True)
        )
        ids = pd.to_numeric(
            gdf.loc[normalized.eq("no-pass"), id_column],
            errors="coerce",
        ).dropna()
        return sorted({int(value) for value in ids if float(value).is_integer()})

    available_layers = set(pyogrio.list_layers(input_path)[:, 0])
    if layer_name not in available_layers:
        return []

    gdf = pyogrio.read_dataframe(
        input_path,
        layer=layer_name,
        columns=[id_column, "inductive"],
        read_geometry=False,
    )
    missing = {id_column, "inductive"}.difference(gdf.columns)
    if missing:
        raise ValueError(
            f"Layer '{layer_name}' is missing required columns: "
            + ", ".join(sorted(missing))
        )

    normalized = (
        gdf["inductive"]
        .astype("string")
        .str.strip()
        .str.casefold()
        .str.replace(r"[\s_–—]+", "-", regex=True)
    )
    ids = pd.to_numeric(
        gdf.loc[normalized.eq("no-pass").fillna(False), id_column],
        errors="coerce",
    ).dropna()
    invalid = ids.mod(1).ne(0) | ids.le(0)
    if invalid.any():
        raise ValueError(
            f"Layer '{layer_name}' contains invalid persistent IDs in its "
            "Step 2 candidate population."
        )
    return sorted({int(value) for value in ids})

