#Script 9

from pathlib import Path
from collections import deque
from shapely.geometry import Point

import geopandas as gpd
import numpy as np
from shapely.ops import unary_union
from itertools import combinations

CONNECT_TOLERANCE_M = 0.01  # 1 cm, assuming CRS units are metres
MAX_BUNDLE_DISTANCE_M = 10.0

target_layer = "Elektriciteitskabel"

# Matching columns specifically for Elektriciteitskabel
# Note: if your actual column is named "utilityDelivertType",
# the script below will handle that too.
preferred_match_columns = [
    "werkVoltage",
    "utilityDeliveryType",
    "verticalePositie",
    "thema",
]


def get_layers(gpkg_path):
    """
    List layers in a GeoPackage without using Fiona.

    Requires geopandas with pyogrio support.
    """
    layers_df = gpd.list_layers(gpkg_path)
    return layers_df["name"].tolist()

def get_line_endpoints(geom):
    """
    Return the endpoints of all line parts in a geometry.

    Supports LineString, MultiLineString, and GeometryCollection.
    """
    if geom is None or geom.is_empty:
        return []

    if geom.geom_type in {"LineString", "LinearRing"}:
        coords = list(geom.coords)

        if not coords:
            return []

        return [
            Point(coords[0]),
            Point(coords[-1]),
        ]

    if geom.geom_type in {"MultiLineString", "GeometryCollection"}:
        endpoints = []

        for part in geom.geoms:
            endpoints.extend(get_line_endpoints(part))

        return endpoints

    return []

def endpoints_are_connected(geom_a, geom_b, tolerance_m=0.0):
    """
    Return True only when an endpoint of geom_a is within the
    specified tolerance of an endpoint of geom_b.

    Intersections in the middle of lines are not considered connections.
    """
    endpoints_a = get_line_endpoints(geom_a)
    endpoints_b = get_line_endpoints(geom_b)

    if not endpoints_a or not endpoints_b:
        return False

    return any(
        endpoint_a.distance(endpoint_b) <= tolerance_m
        for endpoint_a in endpoints_a
        for endpoint_b in endpoints_b
    )

def connected_components_touching(gdf, tolerance_m=0.0):
    """
    Find connected groups using endpoint-to-endpoint connections only.

    Lines that cross or overlap away from their endpoints are not
    considered connected.
    """
    if len(gdf) <= 1:
        return [list(gdf.index)]

    sindex = gdf.sindex
    visited = set()
    components = []

    for idx in gdf.index:
        if idx in visited:
            continue

        queue = deque([idx])
        visited.add(idx)
        component = []

        while queue:
            current_idx = queue.popleft()
            component.append(current_idx)

            geom = gdf.at[current_idx, "geometry"]

            if geom is None or geom.is_empty:
                continue

            if tolerance_m > 0:
                query_geom = geom.buffer(tolerance_m)
            else:
                query_geom = geom

            candidate_positions = list(
                sindex.query(
                    query_geom,
                    predicate="intersects",
                )
            )

            candidate_indices = gdf.index[candidate_positions]

            for other_idx in candidate_indices:
                if other_idx in visited:
                    continue

                other_geom = gdf.at[other_idx, "geometry"]

                if other_geom is None or other_geom.is_empty:
                    continue

                connected = endpoints_are_connected(
                    geom,
                    other_geom,
                    tolerance_m,
                )

                if connected:
                    visited.add(other_idx)
                    queue.append(other_idx)

        components.append(component)

    return components


def aggregate_non_geometry_columns(sub_gdf):
    """
    Preserve all non-geometry columns.

    If all values are identical, keep the value.
    If multiple different non-null values exist, combine unique values with ' | '.
    If all values are null, keep null.
    """
    attrs = {}

    for col in sub_gdf.columns:
        if col == "geometry":
            continue

        non_null_values = sub_gdf[col].dropna().unique()

        if len(non_null_values) == 0:
            attrs[col] = np.nan
        elif len(non_null_values) == 1:
            attrs[col] = non_null_values[0]
        else:
            attrs[col] = " | ".join(map(str, non_null_values))

    return attrs


def get_elektriciteitskabel_match_columns(gdf):
    """
    Determine match columns for Elektriciteitskabel.

    Handles both:
    - utilityDeliveryType
    - utilityDelivertType

    because the user mentioned utilityDelivertType, but the earlier script used utilityDeliveryType.
    """
    match_columns = []

    for col in preferred_match_columns:
        if col in gdf.columns:
            match_columns.append(col)
        elif col == "utilityDeliveryType" and "utilityDelivertType" in gdf.columns:
            match_columns.append("utilityDelivertType")
        else:
            raise ValueError(
                f"Required match column '{col}' not found in Elektriciteitskabel layer. "
                f"Available columns are: {list(gdf.columns)}"
            )

    return match_columns


def remove_outer_high_voltage_lines(gdf):
    """
    For hoogspanning and landelijkHoogspanningsnet, group lines with
    equal 'netwerk' into spatial bundles of three and remove the two
    outer lines from each bundle.
    """
    relevant_themes = {
        "hoogspanning",
        "landelijkhoogspanningsnet",
    }

    themes = (
        gdf["thema"]
        .astype("string")
        .str.strip()
        .str.casefold()
    )

    candidates = gdf[
        themes.isin(relevant_themes)
        & gdf.geometry.notna()
        & ~gdf.geometry.is_empty
    ].copy()

    candidates["_theme"] = themes.loc[candidates.index]

    indices_to_remove = set()

    for _, group in candidates.groupby(
        ["_theme", "netwerk"],
        dropna=False,
        sort=False,
    ):
        remaining = list(group.index)

        while len(remaining) >= 3:
            # Find the two spatially closest lines.
            first, second = min(
                combinations(remaining, 2),
                key=lambda pair: gdf.at[
                    pair[0], "geometry"
                ].hausdorff_distance(
                    gdf.at[pair[1], "geometry"]
                ),
            )

            # Find the line closest to that pair.
            possible_thirds = [
                idx
                for idx in remaining
                if idx not in {first, second}
            ]

            third = min(
                possible_thirds,
                key=lambda idx: (
                    gdf.at[first, "geometry"].hausdorff_distance(
                        gdf.at[idx, "geometry"]
                    )
                    + gdf.at[second, "geometry"].hausdorff_distance(
                        gdf.at[idx, "geometry"]
                    )
                ),
            )

            trio = [first, second, third]

            # All three lines must remain close over their full path.
            pair_distances = [
                gdf.at[a, "geometry"].hausdorff_distance(
                    gdf.at[b, "geometry"]
                )
                for a, b in combinations(trio, 2)
            ]

            # The closest available trio is not a real spatial bundle.
            # Stop processing this theme/network group rather than
            # deleting lines incorrectly.
            if max(pair_distances) > MAX_BUNDLE_DISTANCE_M:
                break

            # The middle line has the smallest total distance
            # to the other two.
            scores = {
                idx: sum(
                    gdf.at[idx, "geometry"].hausdorff_distance(
                        gdf.at[other_idx, "geometry"]
                    )
                    for other_idx in trio
                    if other_idx != idx
                )
                for idx in trio
            }

            middle_idx = min(scores, key=scores.get)

            # Remove both outer lines.
            indices_to_remove.update(
                idx for idx in trio if idx != middle_idx
            )

            # This bundle has been handled.
            remaining = [
                idx for idx in remaining if idx not in trio
            ]

    print(
        f"  Removed {len(indices_to_remove)} outer "
        "high-voltage lines."
    )

    return gdf.drop(
        index=list(indices_to_remove)
    ).reset_index(drop=True)

def combine_elektriciteitskabel_layer(gdf, layer_name):
    print(f"Processing Elektriciteitskabel layer: {layer_name}")

    if gdf.empty:
        print("  Empty layer.")
        return gdf

    gdf = gdf.copy()

    match_columns = get_elektriciteitskabel_match_columns(gdf)

    print(f"  Matching on columns: {match_columns}")

    # Keep features with invalid or empty geometries uncombined
    valid_geom_mask = gdf.geometry.notna() & ~gdf.geometry.is_empty

    valid_gdf = gdf[valid_geom_mask].copy()
    invalid_geom_gdf = gdf[~valid_geom_mask].copy()

    if not invalid_geom_gdf.empty:
        print(
            f"  {len(invalid_geom_gdf)} features have null or empty geometry; "
            "these are kept uncombined."
        )

    output_rows = []

    # Important:
    # dropna=False means that Null/NaN values are included as valid grouping keys.
    # So features where utilityDeliveryType is Null can still be combined with
    # other features where utilityDeliveryType is also Null.
    grouped = valid_gdf.groupby(
        match_columns,
        dropna=False,
        sort=False,
    )

    for _, group in grouped:
        if len(group) == 1:
            row = group.iloc[0].drop(labels="geometry").to_dict()
            row["geometry"] = group.iloc[0].geometry
            output_rows.append(row)
            continue

        components = connected_components_touching(group, CONNECT_TOLERANCE_M)

        for component_indices in components:
            component_gdf = group.loc[component_indices]

            row = aggregate_non_geometry_columns(component_gdf)
            row["geometry"] = unary_union(component_gdf.geometry.values)

            output_rows.append(row)

    # Add invalid geometry features back unchanged
    for _, original_row in invalid_geom_gdf.iterrows():
        row = original_row.drop(labels="geometry").to_dict()
        row["geometry"] = original_row.geometry
        output_rows.append(row)

    combined_gdf = gpd.GeoDataFrame(
        output_rows,
        geometry="geometry",
        crs=gdf.crs,
    )

    combined_gdf = remove_outer_high_voltage_lines(combined_gdf)

    # Preserve original column order
    combined_gdf = combined_gdf[gdf.columns]

    # Preserve original column order
    combined_gdf = combined_gdf[gdf.columns]

    print(f"  Input features:  {len(gdf)}")
    print(f"  Output features: {len(combined_gdf)}")

    return combined_gdf

# ---------------------------------------------------------
# Process layers
# ---------------------------------------------------------

def combine_electricity_features(
    input_path: Path,
    output_path: Path,
) -> Path:

    input_path = Path(input_path)
    output_path = Path(output_path)

    # Remove existing relevant output
    if output_path.exists():
        output_path.unlink()

    layers = get_layers(input_path)

    for layer in layers:
        gdf = gpd.read_file(
            input_path,
            layer=layer,
            engine="pyogrio",
        )

        if layer == target_layer:
            result_gdf = combine_elektriciteitskabel_layer(gdf, layer)
        else:
            print(f"Copying layer without changes: {layer}")
            result_gdf = gdf

        result_gdf.to_file(
            output_path,
            layer=layer,
            driver="GPKG",
            engine="pyogrio",
        )

    print(f"Done. Written to: {output_path}")

    return output_path

# ============================================================
# RUN
# ============================================================
#if __name__ == "__main__":
    combine_electricity_features(
        input_path=(
            "data/KLIC/KLIC_didam/temp/"
            "KLIC_temp8.gpkg"
        ),
        output_path=(
            "data/KLIC/KLIC_didam/temp/"
            "KLIC_etstsettest.gpkg"
        ),
    )