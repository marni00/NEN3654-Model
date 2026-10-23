from __future__ import annotations

from pathlib import Path
import shutil
from typing import Callable, Iterator

import pandas as pd
import pyogrio
import shapely
from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon
from pandas.api.types import (
    is_bool_dtype,
    is_datetime64_any_dtype,
    is_float_dtype,
    is_integer_dtype,
)

ProgressCallback = Callable[[int, int, str], None]
CancellationCheck = Callable[[], None]


def _classify_dtype(dtype) -> str:
    if is_bool_dtype(dtype):
        return "bool"
    if is_integer_dtype(dtype):
        return "integer"
    if is_float_dtype(dtype):
        return "float"
    if is_datetime64_any_dtype(dtype):
        return "datetime"
    return "string"


def _merged_dtype(dtypes) -> str:
    categories = {_classify_dtype(dtype) for dtype in dtypes}
    if categories == {"bool"}:
        return "bool"
    if categories <= {"integer"}:
        return "integer"
    if categories <= {"integer", "float"}:
        return "float"
    if categories == {"datetime"}:
        return "datetime"
    return "string"


def _cast_column(series, target_type: str):
    if target_type == "integer":
        return pd.to_numeric(series, errors="coerce").astype("Int64")
    if target_type == "float":
        return pd.to_numeric(series, errors="coerce").astype("float64")
    if target_type == "datetime":
        return pd.to_datetime(series, errors="coerce")
    if target_type == "bool":
        return series.astype("boolean")
    return series.astype("string")


def _geometry_key(geometry):
    if geometry is None or geometry.is_empty:
        return None
    return shapely.normalize(geometry).wkb_hex


def _feature_keys(gdf):
    """Hash geometry and all harmonised attributes for exact duplicate removal."""
    geometry_keys = gdf.geometry.map(_geometry_key)
    attributes = gdf.drop(columns=gdf.geometry.name)
    attribute_hash = pd.util.hash_pandas_object(
        attributes, index=False
    ).astype("uint64")
    return [
        f"{geometry}|{attribute}"
        for geometry, attribute in zip(geometry_keys, attribute_hash)
    ]


def _combined_schema(layer: str, sources: list[Path]):
    field_order: list[str] = []
    field_dtypes: dict[str, list] = {}
    target_crs = None
    for source in sources:
        info = pyogrio.read_info(source, layer=layer)
        if target_crs is None:
            target_crs = info["crs"]
        for field, dtype in zip(info["fields"], info["dtypes"]):
            if field not in field_order:
                field_order.append(field)
            field_dtypes.setdefault(field, []).append(dtype)
    return (
        field_order,
        {field: _merged_dtype(values) for field, values in field_dtypes.items()},
        target_crs,
    )


def _prepare(gdf, field_order, field_types, target_crs):
    geometry_column = gdf.geometry.name
    if target_crs:
        if gdf.crs is None:
            raise ValueError(
                "Een bronlaag heeft geen CRS terwijl de referentielaag dat wel heeft."
            )
        if gdf.crs != target_crs:
            gdf = gdf.to_crs(target_crs)
    for field in field_order:
        if field not in gdf.columns:
            gdf[field] = pd.NA
        gdf[field] = _cast_column(gdf[field], field_types[field])
    return gdf[field_order + [geometry_column]]


def _usable_gml_id(value) -> str | None:
    """Return a usable gml_id, or None for missing and blank values."""
    if value is None or pd.isna(value):
        return None
    value = str(value).strip()
    return value or None


def _geometry_parts(geometry, *, layer: str, gml_id: str | None):
    """Return the simple parts and geometry family for a supported geometry."""
    if geometry is None or geometry.is_empty:
        return None, []

    geometry_type = geometry.geom_type
    if geometry_type == "Point":
        return "point", [geometry]
    if geometry_type == "MultiPoint":
        return "point", list(geometry.geoms)
    if geometry_type == "LineString":
        return "line", [geometry]
    if geometry_type == "MultiLineString":
        return "line", list(geometry.geoms)
    if geometry_type == "Polygon":
        return "polygon", [geometry]
    if geometry_type == "MultiPolygon":
        return "polygon", list(geometry.geoms)

    id_text = f" voor gml_id {gml_id!r}" if gml_id is not None else ""
    raise ValueError(
        f"Laag {layer!r} bevat een niet-ondersteund geometrie-type "
        f"{geometry_type!r}{id_text}."
    )


def _multipart_geometry(geometries, *, layer: str, gml_id: str):
    """
    Combine geometries into one multipart geometry without dissolving,
    unioning, or line-merging their parts.
    """
    family = None
    parts = []
    seen_parts = set()

    for geometry in geometries:
        geometry_family, geometry_parts = _geometry_parts(
            geometry, layer=layer, gml_id=gml_id
        )
        if geometry_family is None:
            continue
        if family is None:
            family = geometry_family
        elif geometry_family != family:
            raise ValueError(
                f"Laag {layer!r} bevat verschillende geometrie-families "
                f"voor gml_id {gml_id!r}."
            )

        for part in geometry_parts:
            key = _geometry_key(part)
            if key not in seen_parts:
                seen_parts.add(key)
                parts.append(part)

    if not parts:
        return None
    if family == "point":
        return MultiPoint(parts)
    if family == "line":
        return MultiLineString(parts)
    if family == "polygon":
        return MultiPolygon(parts)
    raise AssertionError("Onbekende geometrie-familie.")


def _promote_geometry_to_multi(geometry, *, layer: str):
    """Promote a single geometry so the complete output layer has one type."""
    family, parts = _geometry_parts(geometry, layer=layer, gml_id=None)
    if family is None:
        return None
    if family == "point":
        return MultiPoint(parts)
    if family == "line":
        return MultiLineString(parts)
    if family == "polygon":
        return MultiPolygon(parts)
    raise AssertionError("Onbekende geometrie-familie.")


def _prepared_chunks(
    *,
    layer: str,
    layer_inputs: list[Path],
    chunk_size: int,
    field_order,
    field_types,
    target_crs,
    check_cancelled: CancellationCheck | None,
) -> Iterator:
    for source in layer_inputs:
        info = pyogrio.read_info(source, layer=layer)
        feature_count = int(info.get("features") or 0)
        for skip in range(0, feature_count or 1, chunk_size):
            if check_cancelled:
                check_cancelled()
            chunk = pyogrio.read_dataframe(
                source,
                layer=layer,
                skip_features=skip,
                max_features=chunk_size,
                use_arrow=True,
            )
            if chunk.empty:
                break
            yield _prepare(chunk, field_order, field_types, target_crs)


def combine_file_geodatabases(
    *,
    input_gdbs: list[Path],
    output_gdb: Path,
    overwrite: bool = False,
    chunk_size: int = 50_000,
    progress: ProgressCallback | None = None,
    check_cancelled: CancellationCheck | None = None,
) -> dict[str, int]:
    """
    Combine FileGDB layers and remove exact duplicates.

    For layers containing a gml_id field, features with the same non-empty
    gml_id are written as one multipart feature. Layers without gml_id, and
    rows with a missing or blank gml_id, retain the original behavior.
    """
    sources = [Path(path).resolve() for path in input_gdbs]
    output = Path(output_gdb).resolve()
    if len(sources) < 2:
        raise ValueError("Selecteer minimaal twee File Geodatabases.")
    if len(set(sources)) != len(sources):
        raise ValueError("Dezelfde File Geodatabase is meer dan één keer geselecteerd.")
    for source in sources:
        if source.suffix.casefold() != ".gdb" or not source.is_dir():
            raise ValueError(f"Geen geldige File Geodatabase: {source}")
    if output.suffix.casefold() != ".gdb":
        raise ValueError("De uitvoerlocatie moet eindigen op .gdb.")
    if output in sources:
        raise ValueError("De uitvoer-FileGDB mag niet ook een invoerbron zijn.")
    if output.exists():
        if not overwrite:
            raise FileExistsError(f"Uitvoer bestaat al: {output}")
        shutil.rmtree(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    layer_sources: dict[str, list[Path]] = {}
    for source in sources:
        if check_cancelled:
            check_cancelled()
        for row in pyogrio.list_layers(source):
            layer_sources.setdefault(str(row[0]), []).append(source)

    total_layers = max(1, len(layer_sources))
    total_written = 0
    total_duplicates = 0

    try:
        for layer_index, (layer, layer_inputs) in enumerate(
            layer_sources.items(), start=1
        ):
            if check_cancelled:
                check_cancelled()
            if progress:
                progress(
                    layer_index - 1,
                    total_layers,
                    f"FileGDB-laag combineren: {layer}",
                )

            field_order, field_types, target_crs = _combined_schema(
                layer, layer_inputs
            )
            has_gml_id = "gml_id" in field_order
            duplicate_gml_ids: set[str] = set()

            if has_gml_id:
                gml_id_counts: dict[str, int] = {}
                for chunk in _prepared_chunks(
                    layer=layer,
                    layer_inputs=layer_inputs,
                    chunk_size=chunk_size,
                    field_order=field_order,
                    field_types=field_types,
                    target_crs=target_crs,
                    check_cancelled=check_cancelled,
                ):
                    for value in chunk["gml_id"]:
                        gml_id = _usable_gml_id(value)
                        if gml_id is not None:
                            gml_id_counts[gml_id] = gml_id_counts.get(gml_id, 0) + 1
                duplicate_gml_ids = {
                    gml_id
                    for gml_id, count in gml_id_counts.items()
                    if count > 1
                }

            seen_keys = set()
            grouped_features: dict[str, dict] = {}
            first_write = True
            promote_layer_to_multi = bool(duplicate_gml_ids)

            for chunk in _prepared_chunks(
                layer=layer,
                layer_inputs=layer_inputs,
                chunk_size=chunk_size,
                field_order=field_order,
                field_types=field_types,
                target_crs=target_crs,
                check_cancelled=check_cancelled,
            ):
                keep = []
                for key in _feature_keys(chunk):
                    is_new = key not in seen_keys
                    keep.append(is_new)
                    if is_new:
                        seen_keys.add(key)
                    else:
                        total_duplicates += 1

                chunk = chunk.loc[keep].copy()
                if chunk.empty:
                    continue

                if has_gml_id and duplicate_gml_ids:
                    geometry_column = chunk.geometry.name
                    write_mask = []

                    for index, value in chunk["gml_id"].items():
                        gml_id = _usable_gml_id(value)
                        if gml_id not in duplicate_gml_ids:
                            write_mask.append(True)
                            continue

                        write_mask.append(False)
                        grouped = grouped_features.get(gml_id)
                        if grouped is None:
                            grouped = {
                                "record": chunk.loc[index].to_dict(),
                                "geometries": [],
                            }
                            grouped_features[gml_id] = grouped
                        grouped["geometries"].append(chunk.at[index, geometry_column])

                    chunk = chunk.loc[write_mask].copy()

                if chunk.empty:
                    continue

                if promote_layer_to_multi:
                    geometry_column = chunk.geometry.name
                    chunk[geometry_column] = chunk[geometry_column].map(
                        lambda geometry: _promote_geometry_to_multi(
                            geometry, layer=layer
                        )
                    )

                pyogrio.write_dataframe(
                    chunk,
                    output,
                    layer=layer,
                    driver="OpenFileGDB",
                    append=not first_write,
                )
                first_write = False
                total_written += len(chunk)

            if grouped_features:
                import geopandas as gpd

                records = []
                for gml_id, grouped in grouped_features.items():
                    record = grouped["record"]
                    geometry_column = next(
                        key for key in record if key not in field_order
                    )
                    record[geometry_column] = _multipart_geometry(
                        grouped["geometries"],
                        layer=layer,
                        gml_id=gml_id,
                    )
                    records.append(record)

                geometry_column = next(
                    key for key in records[0] if key not in field_order
                )
                merged = gpd.GeoDataFrame(
                    records,
                    columns=field_order + [geometry_column],
                    geometry=geometry_column,
                    crs=target_crs,
                )
                pyogrio.write_dataframe(
                    merged,
                    output,
                    layer=layer,
                    driver="OpenFileGDB",
                    append=not first_write,
                )
                first_write = False
                total_written += len(merged)

        if progress:
            progress(total_layers, total_layers, "File Geodatabases gecombineerd")
    except Exception:
        # Do not leave a partially valid-looking output after failure/cancellation.
        if output.exists():
            shutil.rmtree(output, ignore_errors=True)
        raise

    return {
        "layers": len(layer_sources),
        "written": total_written,
        "duplicates": total_duplicates,
    }
