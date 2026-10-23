#Script 10
from pathlib import Path
import time
import math

import pandas as pd
import geopandas as gpd
import pyogrio

from shapely.geometry import Point, GeometryCollection, LineString
from shapely.ops import substring


# =============================================================================
# CONFIG
# =============================================================================

influencer_layers = [
    "proposed_highvoltage",
    "Elektriciteitskabel",
]

influenced_layers = [
    "OlieGasChemicalienPijpleiding",
    "Overig",
    "Waterleiding",
    "Rioolleiding",
    "ThermischePijpleiding",
]

copy_unchanged_layers = [
    "Mast",
]

OUTPUT_ENDPOINTS_LAYER = "Endpoints"

BUFFER_DISTANCES = [5, 30, 50]

# Lower = faster. Use 2 for speed, 4 for smoother buffers.
BUFFER_RESOLUTION = 2

SPLIT_AT_BENDS = True
BEND_TOLERANCE_DEGREES = 2

CREATE_ENDPOINTS = True

# If your layers already have a unique ID column, put it here.
# If None:
#   - existing column "feature_id" will be used if present
#   - otherwise original row index will be used
FEATURE_ID_SOURCE_COL = None

# Internal tolerance in map units
DISTANCE_TOLERANCE = 1e-6

# Buffer cap style: "round", "flat", or "square"
BUFFER_CAP_STYLE = "flat"

# Create long perpendicular cut lines at influencer segment endpoints.
CREATE_INFLUENCER_ENDPOINT_CUTS = True

# Length on each side of the influencer endpoint.
# Total cut-line length will be 2 * this value.
PERPENDICULAR_CUT_HALF_LENGTH_AG = 1610
PERPENDICULAR_CUT_HALF_LENGTH_UG_LE24KV = 550
PERPENDICULAR_CUT_HALF_LENGTH_UG_GT24KV = 850
PERPENDICULAR_CUT_HALF_LENGTH_DEFAULT = 1610


# =============================================================================
# HELPERS
# =============================================================================

def get_endpoint_cut_half_length(row):
    """Return the perpendicular cut half-length for one influencer part."""

    def is_no_pass(value):
        return str(value).strip().lower() == "no-pass"

    if is_no_pass(row.get("inductive_ag")):
        return PERPENDICULAR_CUT_HALF_LENGTH_AG

    if is_no_pass(row.get("inductive_ug_le24kv")):
        return PERPENDICULAR_CUT_HALF_LENGTH_UG_LE24KV

    if is_no_pass(row.get("inductive_ug_gt24kv")):
        return PERPENDICULAR_CUT_HALF_LENGTH_UG_GT24KV

    return PERPENDICULAR_CUT_HALF_LENGTH_DEFAULT


def create_perpendicular_endpoint_cut_lines(influencer_parts):
    """
    Create long perpendicular cut lines at the start and end point of each
    pre-split influencer LineString.

    These are used to cut influenced layers exactly perpendicular to the
    influencer segment direction at segment endpoints/bends.

    PERPENDICULAR_CUT_HALF_LENGTH is the length on each side of the endpoint.
    Total cut-line length is therefore 2 * PERPENDICULAR_CUT_HALF_LENGTH.
    """

    print("")
    print("=" * 80)
    print("Creating perpendicular influencer endpoint cut lines")
    print("=" * 80)

    if not CREATE_INFLUENCER_ENDPOINT_CUTS:
        print("Perpendicular endpoint cuts disabled.")
        return gpd.GeoDataFrame(
            geometry=[],
            crs=influencer_parts.crs
        )

    # Nothing to process
    if influencer_parts.empty:
        print("No influencer parts available, skipping endpoint cuts.")
        return gpd.GeoDataFrame(
            geometry=[],
            crs=influencer_parts.crs
        )

    t0 = time.perf_counter()

    cut_records = []

    for idx, row in influencer_parts.iterrows():

        line = row.geometry
        half_length = get_endpoint_cut_half_length(row)

        if idx % 10000 == 0 and idx > 0:
            print(
                f"Created endpoint cut lines for "
                f"{idx} / {len(influencer_parts)} influencer parts..."
            )

        if line is None or line.is_empty:
            continue

        if line.geom_type != "LineString":
            continue

        coords = list(line.coords)

        if len(coords) < 2:
            continue

        # ---------------------------------------------------------------------
        # Start endpoint
        # Direction is from first coordinate to second coordinate.
        # ---------------------------------------------------------------------

        start = coords[0]
        next_coord = coords[1]

        dx = next_coord[0] - start[0]
        dy = next_coord[1] - start[1]

        length = math.hypot(dx, dy)

        if length > DISTANCE_TOLERANCE:

            # Unit perpendicular vector
            px = -dy / length
            py = dx / length

            cut_line = LineString([
                (
                    start[0] - px * half_length,
                    start[1] - py * half_length
                ),
                (
                    start[0] + px * half_length,
                    start[1] + py * half_length
                )
            ])

            cut_records.append({
                "cut_type": "influencer_endpoint_perpendicular",
                "source_part_idx": idx,
                "endpoint_type": "start",
                "geometry": cut_line
            })

        # ---------------------------------------------------------------------
        # End endpoint
        # Direction is from second-last coordinate to last coordinate.
        # ---------------------------------------------------------------------

        prev_coord = coords[-2]
        end = coords[-1]

        dx = end[0] - prev_coord[0]
        dy = end[1] - prev_coord[1]

        length = math.hypot(dx, dy)

        if length > DISTANCE_TOLERANCE:

            # Unit perpendicular vector
            px = -dy / length
            py = dx / length

            cut_line = LineString([
                (
                    end[0] - px * half_length,
                    end[1] - py * half_length
                ),
                (
                    end[0] + px * half_length,
                    end[1] + py * half_length
                )
            ])

            cut_records.append({
                "cut_type": "influencer_endpoint_perpendicular",
                "source_part_idx": idx,
                "endpoint_type": "end",
                "geometry": cut_line
            })

    cut_gdf = gpd.GeoDataFrame(
        cut_records,
        geometry="geometry",
        crs=influencer_parts.crs
    )

    cut_gdf = cut_gdf[
        cut_gdf.geometry.notna() &
        ~cut_gdf.geometry.is_empty
    ].reset_index(drop=True)

    print(
        "Total perpendicular endpoint cut lines:",
        len(cut_gdf)
    )

    log_step(
        "Create perpendicular endpoint cut lines",
        t0
    )

    return cut_gdf


def log_step(name, start_time):
    print(
        f"{name}: "
        f"{round(time.perf_counter() - start_time, 2)} sec"
    )


def output_layer_name(layer_name):
    return layer_name


def list_gpkg_layers(gpkg_path):
    return [
        row[0]
        for row in pyogrio.list_layers(gpkg_path)
    ]


def read_layer(gpkg_path, layer_name):
    return pyogrio.read_dataframe(
        gpkg_path,
        layer=layer_name
    )


def write_or_replace_gpkg_layer(
    gdf,
    gpkg_path,
    layer_name
):
    """
    Deletes an existing GeoPackage layer if present and writes a new one.
    Pyogrio-only version.
    """

    gpkg_path = Path(gpkg_path)

    print("")
    print(
        f"Writing GeoPackage layer: {layer_name}"
    )

    if gpkg_path.exists():

        existing_layers = list_gpkg_layers(
            gpkg_path
        )

        if layer_name in existing_layers:

            print(
                f"Deleting existing layer with pyogrio: "
                f"{layer_name}"
            )

            pyogrio.delete_layer(
                str(gpkg_path),
                layer=layer_name
            )

    pyogrio.write_dataframe(
        gdf,
        str(gpkg_path),
        layer=layer_name,
        driver="GPKG"
    )

    print(
        f"Layer written: {layer_name}"
    )


def flatten_geometries(geom):

    if geom is None or geom.is_empty:
        return

    if geom.geom_type in [
        "GeometryCollection",
        "MultiLineString",
        "MultiPolygon",
        "MultiPoint",
    ]:
        for part in geom.geoms:
            yield from flatten_geometries(part)

    else:
        yield geom


def extract_points(geom):
    """
    Extract split points from an intersection geometry.
    """

    points = []

    if geom is None or geom.is_empty:
        return points

    if geom.geom_type == "Point":

        points.append(geom)

    elif geom.geom_type == "MultiPoint":

        points.extend(
            list(geom.geoms)
        )

    elif geom.geom_type == "LineString":

        coords = list(geom.coords)

        if len(coords) >= 2:

            points.append(
                Point(coords[0])
            )

            points.append(
                Point(coords[-1])
            )

    elif geom.geom_type == "MultiLineString":

        for part in geom.geoms:
            points.extend(
                extract_points(part)
            )

    elif isinstance(
        geom,
        GeometryCollection
    ):

        for part in geom.geoms:
            points.extend(
                extract_points(part)
            )

    return points


def split_line_by_distances(
    line,
    distances,
    tol=DISTANCE_TOLERANCE
):
    """
    Split LineString by distances along the line.
    """

    if not distances:
        return [line]

    distances = sorted(distances)

    unique = []

    for d in distances:

        if (
            not unique
            or abs(d - unique[-1]) > tol
        ):
            unique.append(d)

    cuts = [
        0.0
    ] + unique + [
        line.length
    ]

    segments = []

    for start, end in zip(
        cuts[:-1],
        cuts[1:]
    ):

        if end - start > tol:

            seg = substring(
                line,
                start,
                end
            )

            if (
                not seg.is_empty
                and seg.length > tol
            ):
                segments.append(seg)

    return segments


def get_bend_split_distances(
    line,
    tolerance_degrees=2,
    tol=DISTANCE_TOLERANCE
):
    """
    Returns distances along a LineString where bends exceed the tolerance.

    A perfectly straight continuation has angle 0.
    A sharp bend has a larger angle.
    If angle > tolerance_degrees, the line is split at that vertex.
    """

    if line is None or line.is_empty:
        return []

    coords = list(line.coords)

    if len(coords) < 3:
        return []

    distances = []
    cumulative_distance = 0.0

    for i in range(
        1,
        len(coords) - 1
    ):

        prev_coord = coords[i - 1]
        curr_coord = coords[i]
        next_coord = coords[i + 1]

        # Use XY only, even if geometry has Z.
        ax = curr_coord[0] - prev_coord[0]
        ay = curr_coord[1] - prev_coord[1]

        bx = next_coord[0] - curr_coord[0]
        by = next_coord[1] - curr_coord[1]

        len_a = math.hypot(
            ax,
            ay
        )

        len_b = math.hypot(
            bx,
            by
        )

        cumulative_distance += len_a

        if (
            len_a <= tol
            or len_b <= tol
        ):
            continue

        dot = ax * bx + ay * by

        cos_angle = dot / (
            len_a * len_b
        )

        # Numerical safety
        cos_angle = max(
            -1.0,
            min(1.0, cos_angle)
        )

        angle_degrees = math.degrees(
            math.acos(cos_angle)
        )

        if angle_degrees > tolerance_degrees:

            if (
                tol < cumulative_distance
                < line.length - tol
            ):
                distances.append(
                    cumulative_distance
                )

    return distances


def prepare_line_layer(
    raw_gdf,
    layer_name,
    working_crs
):
    """
    Explodes line geometries to LineStrings while preserving all original columns.
    Adds feature_id if needed.
    Removes/recreates part_no later.

    Empty input layers are returned as empty GeoDataFrames so that processing
    can continue with the next layer.
    """

    gdf = raw_gdf.reset_index(
        drop=True
    ).copy()

    # -------------------------------------------------------------------------
    # Empty input layer
    # -------------------------------------------------------------------------

    if gdf.empty:

        print(
            f"{layer_name}: layer is empty, "
            f"skipping preparation."
        )

        return gdf.copy()

    if gdf.crs is None:

        raise ValueError(
            f"Layer '{layer_name}' has no CRS."
        )

    if gdf.crs != working_crs:

        gdf = gdf.to_crs(
            working_crs
        )

    gdf = gdf[
        gdf.geometry.notna()
        & ~gdf.geometry.is_empty
    ].copy()

    # -------------------------------------------------------------------------
    # No non-empty geometries
    # -------------------------------------------------------------------------

    if gdf.empty:

        print(
            f"{layer_name}: no non-empty geometries, "
            f"skipping preparation."
        )

        return gdf.copy()

    if FEATURE_ID_SOURCE_COL is not None:

        if FEATURE_ID_SOURCE_COL not in gdf.columns:

            raise ValueError(
                f"FEATURE_ID_SOURCE_COL "
                f"'{FEATURE_ID_SOURCE_COL}' "
                f"not found in layer "
                f"'{layer_name}'."
            )

        gdf["feature_id"] = (
            gdf[FEATURE_ID_SOURCE_COL]
        )

    elif "feature_id" in gdf.columns:

        # Keep existing feature_id
        pass

    else:

        # Use original row index as feature_id
        gdf["feature_id"] = gdf.index

    # Recreate part_no cleanly after segmentation
    if "part_no" in gdf.columns:

        gdf = gdf.drop(
            columns=["part_no"]
        )

    gdf = gdf.explode(
        index_parts=True
    )

    gdf = gdf.reset_index(
        drop=True
    )

    gdf = gdf[
        gdf.geometry.notna()
        & ~gdf.geometry.is_empty
    ].copy()

    # -------------------------------------------------------------------------
    # No valid geometries after explode
    # -------------------------------------------------------------------------

    if gdf.empty:

        print(
            f"{layer_name}: no valid geometries "
            f"after explode, skipping preparation."
        )

        return gdf.copy()

    before = len(gdf)

    gdf = gdf[
        gdf.geometry.geom_type == "LineString"
    ].copy()

    after = len(gdf)

    if before != after:

        print(
            f"Warning: layer '{layer_name}' "
            f"dropped {before - after} "
            f"non-LineString geometries."
        )

    gdf = gdf.reset_index(
        drop=True
    )

    if gdf.empty:

        print(
            f"{layer_name}: no LineString geometries "
            f"remain, skipping preparation."
        )

    return gdf


def create_all_buffer_boundaries(
    target_lines,
    influencer_parts
):
    """
    Creates individual buffer boundaries for all influencer layers and all distances.

    These boundaries are then used together to cut all processed line layers.
    """

    print("")
    print("=" * 80)
    print("Creating individual influencer buffer boundaries")
    print("=" * 80)

    # -------------------------------------------------------------------------
    # Nothing to process
    # -------------------------------------------------------------------------

    if target_lines.empty:

        print(
            "No target lines available. "
            "Skipping buffer creation."
        )

        return gpd.GeoDataFrame(
            geometry=[],
            crs=target_lines.crs
        )

    if influencer_parts.empty:

        print(
            "No influencer parts available. "
            "Skipping buffer creation."
        )

        return gpd.GeoDataFrame(
            geometry=[],
            crs=target_lines.crs
        )

    max_distance = max(
        BUFFER_DISTANCES
    )

    t0 = time.perf_counter()

    influencer_sindex = (
        influencer_parts.sindex
    )

    try:

        pairs = influencer_sindex.query(
            target_lines.geometry,
            predicate="dwithin",
            distance=max_distance
        )

        print(
            f"Used spatial index predicate: "
            f"dwithin, distance {max_distance} m"
        )

    except Exception as e:

        print(
            "dwithin not available, "
            "using temporary target buffers."
        )

        print(
            "Reason:",
            str(e)
        )

        target_search_buffers = (
            target_lines.geometry.buffer(
                max_distance,
                resolution=BUFFER_RESOLUTION
            )
        )

        pairs = influencer_sindex.query(
            target_search_buffers,
            predicate="intersects"
        )

    if len(pairs[0]) == 0:

        print(
            "No influencer parts found near target line layers."
        )

        return gpd.GeoDataFrame(
            geometry=[],
            crs=target_lines.crs
        )

    relevant_influencer_positions = (
        pd.Index(
            pairs[1]
        ).unique().to_numpy()
    )

    influencer_relevant = (
        influencer_parts
        .iloc[relevant_influencer_positions]
        .copy()
    )

    influencer_relevant = (
        influencer_relevant
        .reset_index(drop=True)
    )

    print(
        "Relevant influencer LineString parts:",
        len(influencer_relevant)
    )

    print(
        "Target-influencer candidate pairs:",
        len(pairs[0])
    )

    log_step(
        "Select relevant influencer lines",
        t0
    )

    boundary_records = []

    for buffer_distance in BUFFER_DISTANCES:

        print("")
        print(
            f"Creating individual "
            f"{buffer_distance} m "
            f"buffer boundaries..."
        )

        t0 = time.perf_counter()

        influencer_buffers = (
            influencer_relevant.geometry.buffer(
                buffer_distance,
                resolution=BUFFER_RESOLUTION,
                cap_style=BUFFER_CAP_STYLE
            )
        )

        for buffer_geom in influencer_buffers:

            if (
                buffer_geom is None
                or buffer_geom.is_empty
            ):
                continue

            boundary = (
                buffer_geom.boundary
            )

            for part in flatten_geometries(
                boundary
            ):

                if (
                    part is None
                    or part.is_empty
                ):
                    continue

                if part.geom_type == "LinearRing":

                    part = LineString(
                        part
                    )

                if part.geom_type == "LineString":

                    boundary_records.append({
                        "geometry": part
                    })

        log_step(
            f"Create {buffer_distance} m boundaries",
            t0
        )

    boundary_gdf = gpd.GeoDataFrame(
        boundary_records,
        geometry="geometry",
        crs=target_lines.crs
    )

    boundary_gdf = boundary_gdf[
        boundary_gdf.geometry.notna()
        & ~boundary_gdf.geometry.is_empty
    ].reset_index(
        drop=True
    )

    print("")
    print(
        "Total individual buffer boundary parts:",
        len(boundary_gdf)
    )

    return boundary_gdf


def build_boundary_candidate_lookup(
    lines,
    boundary_gdf
):
    """
    Builds lookup dict:
        line_index -> list of boundary indexes that intersect it
    """

    if (
        len(boundary_gdf) == 0
        or len(lines) == 0
    ):
        return {}

    boundary_sindex = (
        boundary_gdf.sindex
    )

    boundary_pairs = boundary_sindex.query(
        lines.geometry,
        predicate="intersects"
    )

    if len(boundary_pairs[0]) == 0:
        return {}

    candidate_boundaries_by_line = (
        pd.DataFrame({
            "line_idx": boundary_pairs[0],
            "boundary_idx": boundary_pairs[1]
        })
        .groupby("line_idx")["boundary_idx"]
        .apply(list)
        .to_dict()
    )

    return candidate_boundaries_by_line


def segment_line_layer(
    layer_name,
    lines,
    boundary_gdf
):
    """
    Splits one line layer by:
      1. influencer buffer boundaries
      2. bends, if SPLIT_AT_BENDS is True

    Keeps original columns.
    Adds consecutive part_no per feature_id.
    """

    print("")
    print("=" * 80)
    print(
        f"Segmenting layer: {layer_name}"
    )
    print("=" * 80)

    # -------------------------------------------------------------------------
    # Empty layer
    # -------------------------------------------------------------------------

    if lines.empty:

        print(
            f"{layer_name}: empty layer, "
            f"skipping segmentation."
        )

        return gpd.GeoDataFrame(
            columns=[
                "feature_id",
                "part_no",
                "geometry"
            ],
            geometry="geometry",
            crs=lines.crs
        )

    t0 = time.perf_counter()

    candidate_boundaries_by_line = (
        build_boundary_candidate_lookup(
            lines=lines,
            boundary_gdf=boundary_gdf
        )
    )

    print(
        "Input LineString parts:",
        len(lines)
    )

    print(
        "Line parts touching buffer boundaries:",
        len(candidate_boundaries_by_line)
    )

    split_records = []

    n_lines = len(lines)

    for idx, row in lines.iterrows():

        if idx % 1000 == 0 and idx > 0:

            print(
                f"{layer_name}: processed "
                f"{idx} / {n_lines} line parts..."
            )

        line = row.geometry

        base_attrs = (
            row.drop(
                labels="geometry"
            ).to_dict()
        )

        distances = []

        # ---------------------------------------------------------------------
        # 1. Buffer-boundary split distances
        # ---------------------------------------------------------------------

        if idx in candidate_boundaries_by_line:

            for bidx in (
                candidate_boundaries_by_line[idx]
            ):

                boundary_part = (
                    boundary_gdf
                    .geometry
                    .iloc[bidx]
                )

                inter = (
                    line.intersection(
                        boundary_part
                    )
                )

                for pt in extract_points(
                    inter
                ):

                    d = line.project(pt)

                    if (
                        DISTANCE_TOLERANCE
                        < d
                        < line.length
                        - DISTANCE_TOLERANCE
                    ):

                        distances.append(d)

        # ---------------------------------------------------------------------
        # 2. Bend split distances
        # ---------------------------------------------------------------------

        if SPLIT_AT_BENDS:

            bend_distances = (
                get_bend_split_distances(
                    line,
                    tolerance_degrees=(
                        BEND_TOLERANCE_DEGREES
                    )
                )
            )

            distances.extend(
                bend_distances
            )

        # ---------------------------------------------------------------------
        # 3. Split once using all distances
        # ---------------------------------------------------------------------

        segments = (
            split_line_by_distances(
                line,
                distances,
                tol=DISTANCE_TOLERANCE
            )
        )

        for seg in segments:

            record = base_attrs.copy()

            record["geometry"] = seg

            split_records.append(record)

    result = gpd.GeoDataFrame(
        split_records,
        geometry="geometry",
        crs=lines.crs
    )

    # Safety check
    if result.empty:

        print(
            f"{layer_name}: segmentation produced "
            f"no output features."
        )

        return result

    result["part_no"] = (
        result
        .groupby(
            "feature_id",
            sort=False
        )
        .cumcount()
        + 1
    )

    # Put feature_id and part_no near the front
    cols = list(
        result.columns
    )

    front_cols = [
        "feature_id",
        "part_no"
    ]

    other_cols = [
        col
        for col in cols
        if col not in front_cols + ["geometry"]
    ]

    result = result[
        front_cols
        + other_cols
        + ["geometry"]
    ]

    result["segment_length_m"] = (
        result.geometry.length
    )

    print(
        "Output segmented parts:",
        len(result)
    )

    log_step(
        f"Segment layer {layer_name}",
        t0
    )

    return result


def create_endpoints_for_layer(
    segmented_gdf,
    source_layer
):
    """
    Creates start/end endpoint records for every segmented line part.

    Output columns:
      - source_layer
      - id: persistent temp9 source-feature identity
      - feature_id
      - part_no
      - node_type: start/end
      - geometry
    """

    endpoint_records = []

    if segmented_gdf.empty:
        return gpd.GeoDataFrame(
            columns=[
                "source_layer",
                "id",
                "feature_id",
                "part_no",
                "node_type",
                "geometry"
            ],
            geometry="geometry",
            crs=segmented_gdf.crs
        )

    for _, row in segmented_gdf.iterrows():

        geom = row.geometry

        if geom is None or geom.is_empty:
            continue

        if geom.geom_type != "LineString":
            continue

        coords = list(
            geom.coords
        )

        if len(coords) < 2:
            continue

        endpoint_records.append({
            "source_layer": source_layer,
            "id": row["id"],
            "feature_id": str(
                row["feature_id"]
            ),
            "part_no": int(
                row["part_no"]
            ),
            "node_type": "start",
            "geometry": Point(
                coords[0]
            )
        })

        endpoint_records.append({
            "source_layer": source_layer,
            "id": row["id"],
            "feature_id": str(
                row["feature_id"]
            ),
            "part_no": int(
                row["part_no"]
            ),
            "node_type": "end",
            "geometry": Point(
                coords[-1]
            )
        })

    endpoints = gpd.GeoDataFrame(
        endpoint_records,
        geometry="geometry",
        crs=segmented_gdf.crs
    )

    return endpoints


# =============================================================================
# MAIN SCRIPT
# =============================================================================

def segment_features(
    input_path: Path,
    output_path: Path,
) -> Path:
    """
    Segment the configured influencer and influenced layers.

    The input GeoPackage is not modified. Results are written to the
    specified output GeoPackage.

    Parameters
    ----------
    input_path:
        Path to the source GeoPackage.

    output_path:
        Path to the output GeoPackage. An existing file at this path
        will be deleted.

    Returns
    -------
    Path
        Path to the generated output GeoPackage.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input GeoPackage does not exist: {input_path}"
        )

    total_start = time.perf_counter()

    existing_layers = list_gpkg_layers(
        input_path
    )

    processing_layers = (
        influencer_layers
        + influenced_layers
    )

    # -------------------------------------------------------------------------
    # Skip processing layers that are not present in the GeoPackage.
    # Do NOT stop the script.
    # -------------------------------------------------------------------------

    missing_processing_layers = [
        layer
        for layer in processing_layers
        if layer not in existing_layers
    ]

    if missing_processing_layers:

        print("")
        print(
            "The following processing layers were not "
            "found in the GeoPackage and will be skipped:"
        )

        for layer in missing_processing_layers:
            print(
                f"  - {layer}"
            )

        processing_layers = [
            layer
            for layer in processing_layers
            if layer in existing_layers
        ]

    if not processing_layers:

        raise ValueError(
            "None of the configured processing layers "
            "were found in the GeoPackage."
        )

    if output_path.exists():

        print(
            f"Deleting existing output GeoPackage: "
            f"{output_path}"
        )

        output_path.unlink()


    # =============================================================================
    # 1. READ PROCESSING LAYERS
    # =============================================================================

    print("")
    print("=" * 80)
    print("Reading processing layers")
    print("=" * 80)

    t0 = time.perf_counter()

    raw_processing = {}

    for layer_name in processing_layers:

        raw_processing[layer_name] = read_layer(
            input_path,
            layer_name
        )

        if raw_processing[layer_name].empty:

            print(
                f"{layer_name}: layer exists but is empty."
            )

    log_step(
        "Read processing layers",
        t0
    )


    # =============================================================================
    # 2. DETERMINE WORKING CRS
    # =============================================================================

    working_crs = None

    for layer_name, gdf in raw_processing.items():

        if gdf.crs is not None:

            working_crs = gdf.crs
            break

    if working_crs is None:

        raise ValueError(
            "None of the processing layers has a CRS."
        )

    # For metre-based buffers in the Netherlands, EPSG:28992 is appropriate.
    if working_crs.is_geographic:

        working_crs = "EPSG:28992"

    print(
        "Working CRS:",
        working_crs
    )


    # =============================================================================
    # 3. PREPARE LINE LAYERS
    # =============================================================================

    print("")
    print("=" * 80)
    print("Preparing line layers")
    print("=" * 80)

    t0 = time.perf_counter()

    prepared_layers = {}

    for layer_name in processing_layers:

        prepared_layers[layer_name] = (
            prepare_line_layer(
                raw_gdf=raw_processing[layer_name],
                layer_name=layer_name,
                working_crs=working_crs
            )
        )

        print(
            f"{layer_name}: "
            f"original features="
            f"{len(raw_processing[layer_name])}, "
            f"prepared LineStrings="
            f"{len(prepared_layers[layer_name])}"
        )

    log_step(
        "Prepare line layers",
        t0
    )


    # =============================================================================
    # 3B. PRE-SPLIT INFLUENCER LINES FOR BUFFER CREATION
    # =============================================================================

    # Important:
    # Buffer cap_style="flat" only creates flat caps at LineString start/end points.
    # Therefore, to also get flat caps at bends, we first split the influencer
    # lines at bends and then create buffers from those split parts.

    # This pre-split version is only used for buffer-boundary creation.
    # The final output influencer layers are still segmented later as before.

    print("")
    print("=" * 80)
    print("Pre-splitting influencer lines for buffer creation")
    print("=" * 80)

    empty_boundary_gdf = gpd.GeoDataFrame(
        geometry=[],
        crs=working_crs
    )

    influencer_buffer_source_layers = {}

    for layer_name in influencer_layers:

        print("")
        print(
            f"{layer_name}: pre-splitting at bends "
            f"for buffer creation."
        )

        # Layer is missing entirely
        if layer_name not in prepared_layers:

            print(
                f"{layer_name}: not present in processing "
                f"layers, skipping."
            )

            influencer_buffer_source_layers[
                layer_name
            ] = gpd.GeoDataFrame(
                geometry=[],
                crs=working_crs
            )

            continue

        # Layer exists but is empty
        if prepared_layers[layer_name].empty:

            print(
                f"{layer_name}: empty layer, skipping."
            )

            influencer_buffer_source_layers[
                layer_name
            ] = prepared_layers[layer_name].copy()

            continue

        if SPLIT_AT_BENDS:

            influencer_buffer_source_layers[
                layer_name
            ] = segment_line_layer(
                layer_name=(
                    f"{layer_name}_buffer_source"
                ),
                lines=prepared_layers[layer_name],
                boundary_gdf=empty_boundary_gdf
            )

        else:

            influencer_buffer_source_layers[
                layer_name
            ] = prepared_layers[layer_name].copy()

        print(
            f"{layer_name}: buffer source LineStrings="
            f"{len(influencer_buffer_source_layers[layer_name])}"
        )


    # =============================================================================
    # 4. COMBINE INFLUENCED TARGET LINES AND PRE-SPLIT INFLUENCER LINES
    # =============================================================================

    target_geometries = [
        prepared_layers[layer].geometry
        for layer in influenced_layers
        if (
            layer in prepared_layers
            and not prepared_layers[layer].empty
        )
    ]

    if target_geometries:

        target_lines_for_buffer_relevance = (
            gpd.GeoDataFrame(
                geometry=pd.concat(
                    target_geometries,
                    ignore_index=True
                ),
                crs=working_crs
            )
        )

    else:

        target_lines_for_buffer_relevance = (
            gpd.GeoDataFrame(
                geometry=[],
                crs=working_crs
            )
        )


    # Buffers are created from pre-split influencer lines.
    # This ensures flat buffer caps are also created at bend split locations.

    influencer_parts_list = []

    for layer in influencer_layers:

        source = (
            influencer_buffer_source_layers[layer]
        )

        if source.empty:
            continue

        # Only keep columns that exist in this layer.
        required_cols = [
            col
            for col in [
                "inductive_ag",
                "inductive_ug_le24kv",
                "inductive_ug_gt24kv"
            ]
            if col in source.columns
        ]

        required_cols.append(
            "geometry"
        )

        influencer_parts_list.append(
            source[required_cols]
        )

    if influencer_parts_list:

        influencer_parts = gpd.GeoDataFrame(
            pd.concat(
                influencer_parts_list,
                ignore_index=True
            ),
            geometry="geometry",
            crs=working_crs
        )

    else:

        influencer_parts = gpd.GeoDataFrame(
            geometry=[],
            crs=working_crs
        )

    print(
        "Total influenced target LineStrings "
        "for buffer relevance:",
        len(target_lines_for_buffer_relevance)
    )

    print(
        "Total pre-split influencer LineStrings "
        "used for buffer creation:",
        len(influencer_parts)
    )


    # =============================================================================
    # 5. CREATE CUT BOUNDARIES FROM ALL INFLUENCER LAYERS
    # =============================================================================

    # 5A. Normal influence-zone buffer boundaries: 5 m, 30 m, 50 m, etc.

    buffer_boundary_gdf = (
        create_all_buffer_boundaries(
            target_lines=target_lines_for_buffer_relevance,
            influencer_parts=influencer_parts
        )
    )


    # 5B. Long perpendicular cut lines at influencer segment endpoints/bends.

    endpoint_cut_gdf = (
        create_perpendicular_endpoint_cut_lines(
            influencer_parts=influencer_parts
        )
    )


    # 5C. Combine both types of cutting geometry.

    boundary_parts = []

    if not buffer_boundary_gdf.empty:
        boundary_parts.append(
            buffer_boundary_gdf
        )

    if not endpoint_cut_gdf.empty:
        boundary_parts.append(
            endpoint_cut_gdf
        )

    if boundary_parts:

        boundary_gdf = gpd.GeoDataFrame(
            pd.concat(
                boundary_parts,
                ignore_index=True
            ),
            geometry="geometry",
            crs=working_crs
        )

    else:

        boundary_gdf = gpd.GeoDataFrame(
            geometry=[],
            crs=working_crs
        )

    boundary_gdf = boundary_gdf[
        boundary_gdf.geometry.notna()
        & ~boundary_gdf.geometry.is_empty
    ].reset_index(
        drop=True
    )

    print("")
    print(
        "Total combined cutting boundary parts:",
        len(boundary_gdf)
    )

    print(
        "  Buffer boundary parts:",
        len(buffer_boundary_gdf)
    )

    print(
        "  Perpendicular endpoint cut lines:",
        len(endpoint_cut_gdf)
    )


    # =============================================================================
    # 6. SEGMENT ALL PROCESSING LINE LAYERS
    # =============================================================================

    segmented_layers = {}
    endpoint_layers = []

    for layer_name in processing_layers:

        # -------------------------------------------------------------------------
        # Empty layer: skip and continue with next one.
        # -------------------------------------------------------------------------

        if (
            layer_name not in prepared_layers
            or prepared_layers[layer_name].empty
        ):

            print("")
            print(
                f"{layer_name}: empty layer, skipping."
            )

            continue

        if layer_name in influencer_layers:

            print("")
            print(
                f"{layer_name}: influencer layer, "
                f"using bend-splitting only."
            )

            boundary_gdf_for_layer = (
                empty_boundary_gdf
            )

        else:

            print("")
            print(
                f"{layer_name}: influenced layer, "
                f"using influencer buffer boundaries "
                f"+ bend-splitting."
            )

            boundary_gdf_for_layer = (
                boundary_gdf
            )

        segmented = segment_line_layer(
            layer_name=layer_name,
            lines=prepared_layers[layer_name],
            boundary_gdf=boundary_gdf_for_layer
        )

        if segmented.empty:

            print(
                f"{layer_name}: no segmented features, "
                f"skipping."
            )

            continue

        segmented_layers[
            layer_name
        ] = segmented

        if CREATE_ENDPOINTS:

            endpoints = create_endpoints_for_layer(
                segmented_gdf=segmented,
                source_layer=layer_name
            )

            if not endpoints.empty:

                endpoint_layers.append(
                    endpoints
                )


    # =============================================================================
    # 7. WRITE SEGMENTED OUTPUT LAYERS
    # =============================================================================

    print("")
    print("=" * 80)
    print("Writing segmented output layers")
    print("=" * 80)

    for layer_name, segmented in (
        segmented_layers.items()
    ):

        t0 = time.perf_counter()

        out_layer = output_layer_name(
            layer_name
        )

        write_or_replace_gpkg_layer(
            gdf=segmented,
            gpkg_path=output_path,
            layer_name=out_layer
        )

        log_step(
            f"Write output layer {out_layer}",
            t0
        )


    # =============================================================================
    # 8. WRITE COMBINED ENDPOINT LAYER
    # =============================================================================

    if CREATE_ENDPOINTS:

        print("")
        print("=" * 80)
        print("Writing combined endpoint layer")
        print("=" * 80)

        if endpoint_layers:

            endpoints_all = gpd.GeoDataFrame(
                pd.concat(
                    endpoint_layers,
                    ignore_index=True
                ),
                geometry="geometry",
                crs=working_crs
            )

        else:

            endpoints_all = gpd.GeoDataFrame(
                columns=[
                    "source_layer",
                    "feature_id",
                    "part_no",
                    "node_type",
                    "geometry"
                ],
                geometry="geometry",
                crs=working_crs
            )

        t0 = time.perf_counter()

        write_or_replace_gpkg_layer(
            gdf=endpoints_all,
            gpkg_path=output_path,
            layer_name=OUTPUT_ENDPOINTS_LAYER
        )

        log_step(
            f"Write endpoint layer "
            f"{OUTPUT_ENDPOINTS_LAYER}",
            t0
        )

        print(
            "Total endpoints:",
            len(endpoints_all)
        )


    # =============================================================================
    # 9. COPY UNCHANGED LAYERS
    # =============================================================================

    print("")
    print("=" * 80)
    print("Copying unchanged layers")
    print("=" * 80)

    for layer_name in copy_unchanged_layers:

        if layer_name not in existing_layers:

            print(
                f"Warning: unchanged layer not found, "
                f"skipping: {layer_name}"
            )

            continue

        t0 = time.perf_counter()

        unchanged = read_layer(
            input_path,
            layer_name
        )

        out_layer = output_layer_name(
            layer_name
        )

        write_or_replace_gpkg_layer(
            gdf=unchanged,
            gpkg_path=output_path,
            layer_name=out_layer
        )

        log_step(
            f"Copy unchanged layer "
            f"{layer_name} to {out_layer}",
            t0
        )


    # =============================================================================
    # DONE
    # =============================================================================

    print("")
    print("=" * 80)
    print("Done")
    print("=" * 80)

    print("")
    print("Original GeoPackage was not changed:")
    print(input_path)

    print("")
    print("New output GeoPackage written to:")
    print(output_path)

    print("")
    print("Segmented output layers:")

    for layer_name in processing_layers:
        if layer_name in segmented_layers:
            print(
                f"- {layer_name}"
            )

    if CREATE_ENDPOINTS:

        print("")
        print("Combined endpoint layer:")
        print(
            f"- {OUTPUT_ENDPOINTS_LAYER}"
        )

    print("")
    print("Copied unchanged layers:")

    for layer_name in copy_unchanged_layers:

        if layer_name in existing_layers:
            print(
                f"- {output_layer_name(layer_name)}"
            )

    print("")
    print("Segmentation settings:")
    print(
        "Buffer distances:",
        BUFFER_DISTANCES
    )
    print(
        "Split at bends:",
        SPLIT_AT_BENDS
    )
    print(
        "Bend tolerance degrees:",
        BEND_TOLERANCE_DEGREES
    )
    print(
        "Create endpoints:",
        CREATE_ENDPOINTS
    )

    print("")
    print(
        "Total runtime:",
        round(
            time.perf_counter() - total_start,
            2
        ),
        "sec"
    )
    return output_path