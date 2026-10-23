from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyogrio


ID_COLUMN = "id"

# KLIC's gml_id is the preferred identity. The alternatives cover common
# spellings used by external source layers without relying on GeoPackage FIDs.
_STABLE_IDENTIFIER_NAMES = {
    "gmlid",
    "lokaalid",
    "identificatie",
    "identifier",
    "uuid",
    "globalid",
}

def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _normalise_column_name(value: str) -> str:
    return "".join(
        character
        for character in value.casefold()
        if character.isalnum()
    )


def _canonical_scalar(value):
    if value is None or value is pd.NA:
        return None

    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass

    if hasattr(value, "item"):
        try:
            value = value.item()
        except (TypeError, ValueError):
            pass

    if isinstance(value, float):
        if pd.isna(value):
            return None
        if value == float("inf") or value == float("-inf"):
            return str(value)
        return format(value, ".17g")

    if isinstance(value, bytes):
        return value.hex()

    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass

    if isinstance(value, (str, int, bool)):
        return value

    return str(value)


def _feature_layers(gpkg_path: Path) -> list[str]:
    with sqlite3.connect(gpkg_path) as connection:
        rows = connection.execute(
            """
            SELECT table_name
            FROM gpkg_contents
            WHERE data_type = 'features'
            ORDER BY table_name
            """
        ).fetchall()

    return [row[0] for row in rows]


def _primary_key_column(
    connection: sqlite3.Connection,
    layer_name: str,
) -> str:
    rows = connection.execute(
        f"PRAGMA table_info({_quote_identifier(layer_name)})"
    ).fetchall()
    primary_keys = [row[1] for row in rows if row[5]]

    if len(primary_keys) != 1:
        raise RuntimeError(
            f"Layer '{layer_name}' must have exactly one primary-key/FID "
            f"column; found {primary_keys}."
        )

    return primary_keys[0]


@contextmanager
def _suspended_layer_triggers(
    connection: sqlite3.Connection,
    layer_name: str,
):
    """
    Temporarily remove a layer's triggers inside the current transaction.

    Some GeoPackages contain spatial-index triggers that call GDAL/SpatiaLite
    SQL functions (for example ST_IsEmpty). Python's standard sqlite3 driver
    does not provide those functions, so even an attribute-only UPDATE can
    fail. Dropping and recreating the unchanged triggers transactionally is
    safe here because callers do not modify geometry or primary keys.
    """
    trigger_rows = connection.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'trigger' AND tbl_name = ?
        ORDER BY name
        """,
        (layer_name,),
    ).fetchall()

    for trigger_name, trigger_sql in trigger_rows:
        if not trigger_sql:
            raise RuntimeError(
                f"Cannot preserve trigger '{trigger_name}' on layer "
                f"'{layer_name}': its SQL definition is unavailable."
            )
        connection.execute(
            f"DROP TRIGGER {_quote_identifier(trigger_name)}"
        )

    try:
        yield
    finally:
        for _, trigger_sql in trigger_rows:
            connection.execute(trigger_sql)


def assign_persistent_layer_ids(gpkg_path: str | Path) -> dict[str, int]:
    """
    Persist each feature's current temp9 GeoPackage FID as ``id``.

    Numbering is layer-specific because every layer has its own native FID
    sequence. The value is assigned once in temp9 and is subsequently carried
    as a normal attribute; downstream rewrites may generate different native
    FIDs without changing ``id``.
    """
    gpkg_path = Path(gpkg_path)
    if not gpkg_path.is_file():
        raise FileNotFoundError(f"GeoPackage not found: {gpkg_path}")

    counts: dict[str, int] = {}

    with sqlite3.connect(gpkg_path) as connection:
        connection.execute("BEGIN IMMEDIATE")

        for layer_name in _feature_layers(gpkg_path):
            quoted_layer = _quote_identifier(layer_name)
            fields = {
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info({quoted_layer})"
                ).fetchall()
            }

            if ID_COLUMN in fields:
                raise RuntimeError(
                    f"Layer '{layer_name}' already contains an '{ID_COLUMN}' "
                    "column. Refusing to overwrite an existing attribute."
                )

            primary_key = _primary_key_column(connection, layer_name)
            quoted_primary_key = _quote_identifier(primary_key)
            quoted_id = _quote_identifier(ID_COLUMN)

            null_fid_count = connection.execute(
                f"SELECT COUNT(*) FROM {quoted_layer} "
                f"WHERE {quoted_primary_key} IS NULL"
            ).fetchone()[0]
            if null_fid_count:
                raise RuntimeError(
                    f"Layer '{layer_name}' contains {null_fid_count} null "
                    "native feature IDs."
                )

            with _suspended_layer_triggers(connection, layer_name):
                connection.execute(
                    f"ALTER TABLE {quoted_layer} "
                    f"ADD COLUMN {quoted_id} INTEGER"
                )
                connection.execute(
                    f"UPDATE {quoted_layer} "
                    f"SET {quoted_id} = {quoted_primary_key}"
                )

                index_name = (
                    "idx_nen3654_id_"
                    + hashlib.sha1(
                        layer_name.encode("utf-8")
                    ).hexdigest()[:12]
                )
                connection.execute(
                    f"CREATE UNIQUE INDEX {_quote_identifier(index_name)} "
                    f"ON {quoted_layer} ({quoted_id})"
                )

            counts[layer_name] = connection.execute(
                f"SELECT COUNT(*) FROM {quoted_layer}"
            ).fetchone()[0]

        connection.commit()

    validate_temp9_ids(gpkg_path)
    return counts

def validate_temp9_ids(gpkg_path: str | Path) -> None:
    """
    Require a persisted integer ID equal to the layer's current temp9 FID.
    """
    gpkg_path = Path(gpkg_path)

    with sqlite3.connect(gpkg_path) as connection:
        for layer_name in _feature_layers(gpkg_path):
            quoted_layer = _quote_identifier(layer_name)
            fields = {
                row[1]: row[2].upper()
                for row in connection.execute(
                    f"PRAGMA table_info({quoted_layer})"
                ).fetchall()
            }

            if ID_COLUMN not in fields:
                raise RuntimeError(
                    f"Layer '{layer_name}' has no persisted '{ID_COLUMN}' "
                    f"column in {gpkg_path.name}."
                )

            primary_key = _primary_key_column(connection, layer_name)
            quoted_primary_key = _quote_identifier(primary_key)
            quoted_id = _quote_identifier(ID_COLUMN)

            null_count = connection.execute(
                f"SELECT COUNT(*) FROM {quoted_layer} "
                f"WHERE {quoted_id} IS NULL"
            ).fetchone()[0]
            if null_count:
                raise RuntimeError(
                    f"Layer '{layer_name}' contains {null_count} null IDs."
                )

            mismatch_count = connection.execute(
                f"SELECT COUNT(*) FROM {quoted_layer} "
                f"WHERE {quoted_id} <> {quoted_primary_key}"
            ).fetchone()[0]
            if mismatch_count:
                raise RuntimeError(
                    f"Layer '{layer_name}' contains {mismatch_count} IDs that "
                    "do not equal the feature's native temp9 FID."
                )

            duplicate_count = connection.execute(
                f"SELECT COUNT(*) FROM ("
                f"SELECT {quoted_id} FROM {quoted_layer} "
                f"GROUP BY {quoted_id} HAVING COUNT(*) > 1"
                f")"
            ).fetchone()[0]
            if duplicate_count:
                raise RuntimeError(
                    f"Layer '{layer_name}' contains duplicate temp9 IDs."
                )

def propagate_ids_to_matching_layers(
    *,
    source_path: str | Path,
    target_path: str | Path,
    layer_names: Iterable[str],
) -> dict[str, int]:
    """
    Copy already-assigned IDs to matching records in an earlier dataset.

    This is used only for the raw post-processing export, whose established
    behaviour intentionally reads influenced features from temp5. Matching is
    based on stable source identifiers, never on native FIDs or row order.
    """
    source_path = Path(source_path)
    target_path = Path(target_path)
    source_layers = set(_feature_layers(source_path))
    target_layers = set(_feature_layers(target_path))
    updates_by_layer: dict[str, list[tuple[int, int]]] = {}
    counts: dict[str, int] = {}

    for layer_name in layer_names:
        if layer_name not in source_layers or layer_name not in target_layers:
            continue

        source = pyogrio.read_dataframe(
            source_path,
            layer=layer_name,
        )
        target = pyogrio.read_dataframe(
            target_path,
            layer=layer_name,
            fid_as_index=True,
        )

        stable_columns = [
            column
            for column in source.columns
            if (
                column != source.geometry.name
                and _normalise_column_name(str(column))
                in _STABLE_IDENTIFIER_NAMES
                and column in target.columns
            )
        ]
        if not stable_columns:
            raise RuntimeError(
                f"Cannot propagate IDs for layer '{layer_name}': no shared "
                "stable source identifier such as gml_id is available."
            )

        source_lookup: dict[tuple, int] = {}
        for _, row in source.iterrows():
            key = tuple(_canonical_scalar(row[column]) for column in stable_columns)
            persistent_id = int(row[ID_COLUMN])
            previous = source_lookup.get(key)
            if previous is not None and previous != persistent_id:
                raise RuntimeError(
                    f"Stable source identity is ambiguous in layer "
                    f"'{layer_name}'."
                )
            source_lookup[key] = persistent_id

        updates = []
        for native_fid, row in target.iterrows():
            key = tuple(_canonical_scalar(row[column]) for column in stable_columns)
            persistent_id = source_lookup.get(key)
            if persistent_id is not None:
                updates.append((persistent_id, int(native_fid)))

        updates_by_layer[layer_name] = updates
        counts[layer_name] = len(updates)

    with sqlite3.connect(target_path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        for layer_name, updates in updates_by_layer.items():
            quoted_layer = _quote_identifier(layer_name)
            fields = {
                row[1]
                for row in connection.execute(
                    f"PRAGMA table_info({quoted_layer})"
                ).fetchall()
            }
            primary_key = _primary_key_column(connection, layer_name)
            with _suspended_layer_triggers(connection, layer_name):
                if ID_COLUMN not in fields:
                    connection.execute(
                        f"ALTER TABLE {quoted_layer} "
                        f"ADD COLUMN {_quote_identifier(ID_COLUMN)} INTEGER"
                    )

                connection.executemany(
                    f"UPDATE {quoted_layer} "
                    f"SET {_quote_identifier(ID_COLUMN)} = ? "
                    f"WHERE {_quote_identifier(primary_key)} = ?",
                    updates,
                )
        connection.commit()

    return counts


def validate_model_output_ids(
    *,
    temp9_path: str | Path,
    output_path: str | Path,
) -> None:
    """
    Validate non-null IDs and layer-specific lineage in a Part 2 output.

    Synthetic relationship layers without an id column are ignored. Every
    feature layer that carries id is checked against its temp9 source layer.
    """
    temp9_path = Path(temp9_path)
    output_path = Path(output_path)
    validate_temp9_ids(temp9_path)

    reference_ids = {}
    for layer_name in _feature_layers(temp9_path):
        gdf = pyogrio.read_dataframe(
            temp9_path,
            layer=layer_name,
            columns=[ID_COLUMN],
        )
        reference_ids[layer_name] = set(gdf[ID_COLUMN].astype("int64"))

    source_feature_layer_prefixes = (
        "proposed_hv_parts",
        "electricity_cable_parts",
        "influenced_parts",
    )

    for layer_name in _feature_layers(output_path):
        info = pyogrio.read_info(output_path, layer=layer_name)
        fields = set(info["fields"])

        represents_source_features = (
            layer_name == "Mast"
            or layer_name in reference_ids
            or layer_name.startswith(source_feature_layer_prefixes)
        )
        if ID_COLUMN not in fields:
            if represents_source_features:
                raise RuntimeError(
                    f"Layer '{layer_name}' in {output_path.name} lost the "
                    f"persisted '{ID_COLUMN}' attribute."
                )
            continue

        columns = [ID_COLUMN]
        if "source_layer" in fields:
            columns.append("source_layer")

        gdf = pyogrio.read_dataframe(
            output_path,
            layer=layer_name,
            columns=columns,
        )
        if gdf[ID_COLUMN].isna().any():
            raise RuntimeError(
                f"Layer '{layer_name}' in {output_path.name} contains null IDs."
            )

        if "source_layer" in gdf.columns:
            for source_layer, group in gdf.groupby(
                "source_layer",
                dropna=False,
            ):
                if source_layer not in reference_ids:
                    continue
                unknown = set(group[ID_COLUMN].astype("int64")) - reference_ids[source_layer]
                if unknown:
                    raise RuntimeError(
                        f"Layer '{layer_name}' contains IDs not present in "
                        f"temp9 layer '{source_layer}': {sorted(unknown)[:10]}"
                    )
            continue

        source_layer = None
        if layer_name in reference_ids:
            source_layer = layer_name
        elif (
            layer_name.startswith("proposed_highvoltage")
            or layer_name.startswith("proposed_hv_parts")
        ):
            source_layer = "proposed_highvoltage"
        elif layer_name.startswith("electricity_cable"):
            source_layer = "Elektriciteitskabel"
        elif layer_name == "Mast":
            source_layer = "Mast"

        if source_layer in reference_ids:
            unknown = set(gdf[ID_COLUMN].astype("int64")) - reference_ids[source_layer]
            if unknown:
                raise RuntimeError(
                    f"Layer '{layer_name}' contains IDs not present in "
                    f"temp9 layer '{source_layer}': {sorted(unknown)[:10]}"
                )
