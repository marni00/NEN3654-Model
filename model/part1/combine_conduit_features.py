#Script 5
from pathlib import Path
from collections import deque

import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.ops import unary_union
from shapely.geometry import Point

CONNECT_TOLERANCE_M = 0.1  # 10 cm, assuming CRS units are metres
DIAMETER_TOLERANCE = 10.0  # Diameter in mm; adjust as needed
DIAMETER_COLUMN = "buisDiameter"

exclude_layers = {"Mast", "Elektriciteitskabel"}

match_columns = [
    "conductive",
    "verticalePositie",
#    "utilityDeliveryType",
    "thema",
    "utilityNetworkType",
#    "Eigenaar",
    "werkVoltage",
    "buisDiameter",
    "material_norm",
#    "olieGasChemicalienProductType",
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
    Return the endpoints of every line part.

    Supports LineString, LinearRing, MultiLineString,
    and GeometryCollection.
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
    Return True when at least one endpoint of geom_a is within the
    specified tolerance of an endpoint of geom_b.

    Crossings, overlaps, and endpoint-to-middle intersections do not count.
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

def connected_components_touching(
    gdf,
    tolerance_m=0.0,
    diameter_column=None,
    diameter_tolerance=0.0,
):
    """
    Find connected groups using endpoint-to-endpoint connections only.

    Features are directly connected when:
    - at least one endpoint pair is within tolerance_m; and
    - their diameters differ by no more than diameter_tolerance.

    Crossings, interior overlaps, and endpoint-to-middle intersections
    are not considered connections.
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

            query_geom = (
                geom.buffer(tolerance_m)
                if tolerance_m > 0
                else geom
            )

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

                # Only endpoint-to-endpoint connections are accepted.
                if not endpoints_are_connected(
                    geom,
                    other_geom,
                    tolerance_m,
                ):
                    continue

                # Apply the diameter condition.
                diameter_matches = True

                if diameter_column is not None:
                    diameter_1 = gdf.at[current_idx, diameter_column]
                    diameter_2 = gdf.at[other_idx, diameter_column]

                    diameter_matches = (
                        abs(diameter_1 - diameter_2)
                        <= diameter_tolerance
                    )

                if diameter_matches:
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


def combine_layer(gdf, layer_name):
    print(f"Processing layer: {layer_name}")

    if gdf.empty:
        print("  Empty layer.")
        return gdf

    gdf = gdf.copy()

    present_match_columns = [
        col for col in match_columns
        if col in gdf.columns
    ]

    if not present_match_columns:
        print("  None of the match columns are present; no combining done.")
        return gdf

    # Convert diameter to a numeric value. This also supports values such as
    # "302,9" that use a decimal comma.
    diameter_is_present = DIAMETER_COLUMN in present_match_columns

    if diameter_is_present:
        gdf["_diameter_numeric"] = pd.to_numeric(
            gdf[DIAMETER_COLUMN]
            .astype("string")
            .str.replace(",", ".", regex=False),
            errors="coerce",
        )

    # All original match columns must contain values.
    valid_mask = gdf[present_match_columns].notna().all(axis=1)

    # Diameter must additionally be convertible to a number.
    if diameter_is_present:
        valid_mask &= gdf["_diameter_numeric"].notna()

    valid_gdf = gdf[valid_mask].copy()
    invalid_gdf = gdf[~valid_mask].copy()

    if not invalid_gdf.empty:
        print(
            f"  {len(invalid_gdf)} features have missing or invalid values "
            "in match columns; these are kept uncombined."
        )

    output_rows = []

    # Group exactly by all match columns except diameter.
    exact_match_columns = [
        col for col in present_match_columns
        if col != DIAMETER_COLUMN
    ]

    if exact_match_columns:
        grouped = valid_gdf.groupby(
            exact_match_columns,
            dropna=False,
            sort=False,
        )
    else:
        # One group if diameter is the only available matching column.
        grouped = [(None, valid_gdf)]

    for _, group in grouped:
        if len(group) == 1:
            clean_group = group.drop(columns=["_diameter_numeric"], errors="ignore")
            row = clean_group.iloc[0].drop(labels="geometry").to_dict()
            row["geometry"] = clean_group.iloc[0].geometry
            output_rows.append(row)
            continue

        components = connected_components_touching(
            group,
            tolerance_m=CONNECT_TOLERANCE_M,
            diameter_column=(
                "_diameter_numeric"
                if diameter_is_present
                else None
            ),
            diameter_tolerance=DIAMETER_TOLERANCE,
        )

        for component_indices in components:
            component_gdf = group.loc[component_indices]
            clean_component = component_gdf.drop(
                columns=["_diameter_numeric"],
                errors="ignore",
            )

            row = aggregate_non_geometry_columns(clean_component)
            row["geometry"] = unary_union(
                clean_component.geometry.values
            )
            output_rows.append(row)

    for _, original_row in invalid_gdf.iterrows():
        original_row = original_row.drop(
            labels=["_diameter_numeric"],
            errors="ignore",
        )
        row = original_row.drop(labels="geometry").to_dict()
        row["geometry"] = original_row.geometry
        output_rows.append(row)

    combined_gdf = gpd.GeoDataFrame(
        output_rows,
        geometry="geometry",
        crs=gdf.crs,
    )

    # Do not include the temporary numeric column.
    original_columns = [
        col for col in gdf.columns
        if col != "_diameter_numeric"
    ]
    combined_gdf = combined_gdf[original_columns]

    print(f"  Input features:  {len(gdf)}")
    print(f"  Output features: {len(combined_gdf)}")

    return combined_gdf



def combine_conduit_features(
    input_path: Path,
    output_path: Path,
) -> Path:

    # Remove existing output file
    if output_path.exists():
        output_path.unlink()

    layers = get_layers(input_path)


    for layer in layers:
        gdf = gpd.read_file(
            input_path,
            layer=layer,
            engine="pyogrio",
        )

        if layer in exclude_layers:
            print(f"Skipping layer without changes: {layer}")
            result_gdf = gdf
        else:
            result_gdf = combine_layer(gdf, layer)

        result_gdf.to_file(
            output_path,
            layer=layer,
            driver="GPKG",
            engine="pyogrio",
        )

    print(f"Done. Written to: {output_path}")

    return output_path