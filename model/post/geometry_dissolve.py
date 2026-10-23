"""GeoDataFrame dissolve helpers with a bounded-memory line fallback."""

from __future__ import annotations

import warnings
from collections.abc import Mapping, Sequence

import geopandas as gpd
import pandas as pd
from shapely.geometry import GeometryCollection, MultiLineString
from shapely.ops import linemerge


def _group_columns(by: str | Sequence[str]) -> list[str]:
    return [by] if isinstance(by, str) else list(by)


def _line_parts(geometry):
    """
    Return the line parts and unsupported parts of one geometry.

    GeometryCollections are unpacked recursively so a line-layer output is not
    written as a GeometryCollection merely because an input geometry contains
    nested linework.
    """
    line_parts = []
    unsupported_parts = []
    pending = [geometry]

    while pending:
        part = pending.pop()

        if part is None or part.is_empty:
            continue

        if part.geom_type == "LineString":
            line_parts.append(part)
        elif part.geom_type in {"MultiLineString", "GeometryCollection"}:
            # Reverse before extending the stack to preserve source part order.
            pending.extend(reversed(part.geoms))
        else:
            unsupported_parts.append(part)

    return line_parts, unsupported_parts


def _normalise_dissolved_line_geometry(geometry):
    """
    Merge exactly connected parts of an already dissolved line geometry.

    This does not snap, simplify, round, or otherwise move coordinates. It
    only joins line parts whose endpoints are already exactly coincident,
    preventing very large dissolved features from being persisted as tens of
    thousands of tiny multipart components that ArcGIS Pro may not draw.
    """
    if geometry is None or geometry.is_empty:
        return geometry

    geometry_type = geometry.geom_type

    if geometry_type == "LineString":
        return geometry

    if geometry_type == "MultiLineString":
        merged = linemerge(geometry)
        return geometry if merged is None or merged.is_empty else merged

    if geometry_type != "GeometryCollection":
        return geometry

    line_parts, unsupported_parts = _line_parts(geometry)

    if not line_parts:
        # Preserve unexpected non-line geometry rather than silently deleting
        # it. The caller can then handle the upstream schema problem normally.
        return geometry

    if unsupported_parts:
        warnings.warn(
            "The line dissolve encountered non-line components; only the line "
            "components are retained so the result remains compatible with "
            "the line output layer.",
            RuntimeWarning,
            stacklevel=2,
        )

    line_geometry = (
        line_parts[0]
        if len(line_parts) == 1
        else MultiLineString(line_parts)
    )
    merged = linemerge(line_geometry)
    return line_geometry if merged is None or merged.is_empty else merged


def _collect_geometry(geometries):
    """Collect and normalize line parts without performing another union."""
    line_parts = []
    unsupported_parts = []

    for geometry in geometries:
        geometry_lines, geometry_unsupported = _line_parts(geometry)
        line_parts.extend(geometry_lines)
        unsupported_parts.extend(geometry_unsupported)

    if line_parts:
        if unsupported_parts:
            warnings.warn(
                "The line dissolve fallback encountered non-line components; "
                "only the line components are retained so the result remains "
                "compatible with the line output layer.",
                RuntimeWarning,
                stacklevel=2,
            )
        line_geometry = (
            line_parts[0]
            if len(line_parts) == 1
            else MultiLineString(line_parts)
        )
        return _normalise_dissolved_line_geometry(line_geometry)

    if not unsupported_parts:
        return None
    if len(unsupported_parts) == 1:
        return unsupported_parts[0]
    return GeometryCollection(unsupported_parts)


def _normalise_dissolved_geometries(
    frame: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """Normalize the existing geometry column without another layer scan."""
    if frame.empty:
        return frame

    geometry_column = frame.geometry.name
    frame = frame.copy()
    frame[geometry_column] = frame.geometry.map(
        _normalise_dissolved_line_geometry
    )
    return frame


def _fallback_grouped_collection(
    frame: gpd.GeoDataFrame,
    *,
    by: str | Sequence[str],
    aggfunc,
    as_index: bool,
    sort: bool,
) -> gpd.GeoDataFrame:
    geometry_column = frame.geometry.name
    group_columns = _group_columns(by)
    grouped = frame.groupby(group_columns, sort=sort, dropna=False)

    if isinstance(aggfunc, Mapping):
        attribute_aggregation = {
            column: operation
            for column, operation in aggfunc.items()
            if column not in group_columns and column != geometry_column
        }
        if attribute_aggregation:
            attributes = grouped.agg(attribute_aggregation)
        else:
            attributes = pd.DataFrame(index=grouped.size().index)
    else:
        attribute_columns = [
            column
            for column in frame.columns
            if column not in group_columns and column != geometry_column
        ]
        if attribute_columns:
            attributes = grouped[attribute_columns].agg(aggfunc)
        else:
            attributes = pd.DataFrame(index=grouped.size().index)

    attributes[geometry_column] = grouped[geometry_column].agg(_collect_geometry)

    if not as_index:
        attributes = attributes.reset_index()

    return gpd.GeoDataFrame(
        attributes,
        geometry=geometry_column,
        crs=frame.crs,
    )


def dissolve_with_memory_fallback(
    frame: gpd.GeoDataFrame,
    *,
    by: str | Sequence[str],
    aggfunc="first",
    as_index: bool = False,
    sort: bool = False,
) -> gpd.GeoDataFrame:
    """
    Dissolve normally and normalize connected line parts.

    Only GEOS allocation failures use the bounded-memory collection fallback.
    Both routes normalize the already computed geometry; neither performs an
    additional union, file read, or output-layer pass.
    """
    group_columns = _group_columns(by)
    try:
        dissolved = frame.dissolve(
            by=group_columns,
            aggfunc=aggfunc,
            as_index=as_index,
            sort=sort,
        )
        return _normalise_dissolved_geometries(dissolved)
    except Exception as exc:
        is_shapely_error = type(exc).__module__.startswith("shapely")
        if not is_shapely_error or "allocation" not in str(exc).casefold():
            raise
        warnings.warn(
            "GEOS could not allocate enough memory for a topological dissolve; "
            "preserving the grouped linework as multipart geometries instead.",
            RuntimeWarning,
            stacklevel=2,
        )
        return _fallback_grouped_collection(
            frame,
            by=group_columns,
            aggfunc=aggfunc,
            as_index=as_index,
            sort=sort,
        )
