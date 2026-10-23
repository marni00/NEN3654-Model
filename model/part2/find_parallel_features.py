#Script 11
# =============================================================================
# PARALLEL RUNNING FEATURES DETECTION  (performance-optimized)
#
# Behavior-preserving changes vs. the original script:
#   1. The reference buffer (MAX_DISTANCE around hv/cable geometries) is now
#      computed ONCE per reference layer, instead of being recomputed inside
#      run_authoritative_parallel_pairs on every (reference_layer x
#      target_layer x induction_class) call -> 2 buffer computations total
#      instead of 24.
#   2. orientation_difference is vectorized with numpy instead of a Python
#      list comprehension over scalar pairs.
#   3. overlap_length_with_buffer is vectorized using GeoSeries.intersection()
#      / .length instead of calling shapely .intersection() one row at a time.
#
# All three changes compute exactly the same values as before, just in
# batches instead of row-by-row / call-by-call, so the resulting pair tables
# and output GeoPackage layers are expected to be identical to the original
# script's output. Recommended: diff the CSVs / compare layers against a run
# of the original script on your data before fully switching over.
# =============================================================================

from pathlib import Path
import math
import time

import numpy as np
import geopandas as gpd
import pandas as pd
import pyogrio
import shapely as shp

# =============================================================================
# CONFIG
# =============================================================================
reference_layers = {
    "hv": "proposed_highvoltage",
    "cable": "Elektriciteitskabel",
}

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

SOURCE_LAYER_COL = "source_layer"
MAX_DISTANCES = {
    "ag": 1610,
    "ug_le24kv": 550,
    "ug_gt24kv": 850,
}
MAX_ANGLE_DIFF = 90
MIN_OVERLAP_LENGTH = 0.05
MIN_OVERLAP_RATIO = 0.01
BUFFER_CAP_STYLE = 2
BUFFER_JOIN_STYLE = 2
FEATURE_ID_COL = "feature_id"
PART_NO_COL = "part_no"
DISTANCE_TOLERANCE = 1e-9
VERTICAL_POSITION_COL = "verticalePositie"
VOLTAGE_COL = "werkVoltage"
INDUCTIVE_RULE_COLUMNS = {
    "ag": "inductive_ag",
    "ug_le24kv": "inductive_ug_le24kv",
    "ug_gt24kv": "inductive_ug_gt24kv",
}


# =============================================================================
# BASIC HELPERS
# =============================================================================
def get_layers(gpkg_path):
    """
    List layers in a GeoPackage without using Fiona.

    Requires geopandas with pyogrio support.
    """
    layers_df = gpd.list_layers(gpkg_path)
    return layers_df["name"].tolist()

def log_step(name, start_time):
    print(f"{name}: {round(time.perf_counter() - start_time, 2)} sec")


def list_gpkg_layers(gpkg_path):
    return [row[0] for row in pyogrio.list_layers(gpkg_path)]


def read_layer(gpkg_path, layer_name):
    return pyogrio.read_dataframe(gpkg_path, layer=layer_name)


def write_or_replace_gpkg_layer(gdf, gpkg_path, layer_name):
    print("")
    print(f"Writing layer: {layer_name}")
    pyogrio.write_dataframe(gdf, str(gpkg_path), layer=layer_name, driver="GPKG")
    print(f"Layer written: {layer_name}")


def bearing(p1, p2):
    return (math.degrees(math.atan2(p2[1] - p1[1], p2[0] - p1[0])) + 360) % 360


def line_bearing(line):
    if line is None or line.is_empty:
        return None
    if line.geom_type == "MultiLineString":
        parts = [g for g in line.geoms if hasattr(g, "coords") and len(g.coords) >= 2]
        if not parts:
            return None
        line = max(parts, key=lambda g: g.length)
    if not hasattr(line, "coords"):
        return None
    coords = list(line.coords)
    return bearing(coords[0], coords[-1]) if len(coords) >= 2 else None


def orientation_difference(a, b):
    # Kept for reference / any scalar use elsewhere. The hot path below uses
    # a vectorized numpy equivalent of this exact formula.
    diff = abs(a - b) % 180
    return min(diff, 180 - diff)


def overlap_length_with_buffer(left_geom, right_buffer_geom):
    # Kept for reference / any scalar use elsewhere. The hot path below uses
    # a vectorized GeoSeries equivalent of this exact computation.
    if left_geom is None or right_buffer_geom is None or left_geom.is_empty or right_buffer_geom.is_empty:
        return 0.0
    inter = left_geom.intersection(right_buffer_geom)
    return 0.0 if inter.is_empty else inter.length


def unique_int_csv(series):
    vals = sorted({int(v) for v in series if pd.notnull(v)})
    return ",".join(map(str, vals)) if vals else None


def unique_text_csv(series):
    vals = sorted({str(v) for v in series if pd.notnull(v)})
    return ",".join(vals) if vals else None


def unique_pair_csv(feature_series, part_series):
    pairs = sorted({f"{int(f)}:{int(p)}" for f, p in zip(feature_series, part_series) if pd.notnull(f) and pd.notnull(p)})
    return ",".join(pairs) if pairs else None


def unique_layer_feature_part_csv(layer_series, feature_series, part_series):
    pairs = sorted({f"{str(layer)}|{int(f)}:{int(p)}" for layer, f, p in zip(layer_series, feature_series, part_series) if pd.notnull(layer) and pd.notnull(f) and pd.notnull(p)})
    return ",".join(pairs) if pairs else None


def unique_count(series):
    return len({int(v) for v in series if pd.notnull(v)})


def unique_pair_count(feature_series, part_series):
    return len({(int(f), int(p)) for f, p in zip(feature_series, part_series) if pd.notnull(f) and pd.notnull(p)})


def unique_layer_feature_part_count(layer_series, feature_series, part_series):
    return len({(str(layer), int(f), int(p)) for layer, f, p in zip(layer_series, feature_series, part_series) if pd.notnull(layer) and pd.notnull(f) and pd.notnull(p)})


def ensure_required_columns(gdf, layer_name):
    missing = [col for col in [FEATURE_ID_COL, PART_NO_COL] if col not in gdf.columns]
    if missing:
        raise ValueError(f"Layer '{layer_name}' is missing required columns: {missing}. Run the segmentation script first.")


def prepare_line_gdf(gdf, layer_name, target_crs=None):
    gdf = gdf.copy()
    if gdf.crs is None:
        raise ValueError(f"Layer '{layer_name}' has no CRS.")
    if target_crs is not None and gdf.crs != target_crs:
        gdf = gdf.to_crs(target_crs)
    if gdf.crs.is_geographic:
        raise ValueError(f"Layer '{layer_name}' has geographic CRS {gdf.crs}. Distances require a projected CRS in metres.")
    ensure_required_columns(gdf, layer_name)
    gdf[FEATURE_ID_COL] = pd.to_numeric(gdf[FEATURE_ID_COL], errors="coerce")
    gdf[PART_NO_COL] = pd.to_numeric(gdf[PART_NO_COL], errors="coerce")
    gdf = gdf.dropna(subset=[FEATURE_ID_COL, PART_NO_COL]).copy()
    gdf[FEATURE_ID_COL] = gdf[FEATURE_ID_COL].astype(int)
    gdf[PART_NO_COL] = gdf[PART_NO_COL].astype(int)
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].copy()
    gdf["geometry"] = gdf.geometry.make_valid()
    before = len(gdf)
    gdf = gdf[gdf.geometry.geom_type.isin(["LineString", "MultiLineString"])].copy()
    if before != len(gdf):
        print(f"Warning: layer '{layer_name}' dropped {before - len(gdf)} non-line geometries.")
    gdf["bearing"] = gdf.geometry.apply(line_bearing)
    gdf["line_length_m"] = gdf.geometry.length
    return gdf[gdf["bearing"].notna() & (gdf["line_length_m"] > DISTANCE_TOLERANCE)].reset_index(drop=True)


def add_influencer_class(gdf, layer_name):
    missing = [col for col in [VERTICAL_POSITION_COL, VOLTAGE_COL] if col not in gdf.columns]
    if missing:
        raise ValueError(f"Influencer layer '{layer_name}' is missing required columns: {missing}")
    out = gdf.copy()
    vertical = out[VERTICAL_POSITION_COL]
    voltage = pd.to_numeric(out[VOLTAGE_COL], errors="coerce")
    out["influencer_class"] = None
    out.loc[vertical.eq("aboveground"), "influencer_class"] = "ag"
    underground = vertical.eq("underground")
    out.loc[underground & voltage.le(24000), "influencer_class"] = "ug_le24kv"
    out.loc[underground & voltage.gt(24000), "influencer_class"] = "ug_gt24kv"
    n_unclassified = out["influencer_class"].isna().sum()
    if n_unclassified > 0:
        print(f"Warning: influencer layer '{layer_name}' has {n_unclassified} unclassified features.")
    return out


def validate_inductive_columns(gdf, layer_name):
    missing = [col for col in INDUCTIVE_RULE_COLUMNS.values() if col not in gdf.columns]
    if missing:
        raise ValueError(f"Influenced layer '{layer_name}' is missing inductive rule columns: {missing}")


# =============================================================================
# MATCHING
# =============================================================================

def empty_pair_df():
    return pd.DataFrame(columns=[
        "target_layer", "target_feature_id", "target_part_no", "reference_type",
        "reference_layer", "reference_feature_id", "reference_part_no",
        "angle_diff", "overlap_length", "overlap_ratio",
    ])


def run_authoritative_parallel_pairs(
    target_df,
    reference_df,
    target_layer_name,
    reference_layer_name,
    reference_prefix
):
    function_start = time.perf_counter()

    if target_df.empty or reference_df.empty:
        return empty_pair_df()

    print(
        f"  Matching {target_layer_name} to {reference_prefix} "
        f"with {len(target_df):,} targets and {len(reference_df):,} references"
    )

    target = target_df[
        [FEATURE_ID_COL, PART_NO_COL, "bearing", "line_length_m", "geometry"]
    ].copy()

    if "reference_buffer" not in reference_df.columns:
        raise ValueError(
            "reference_df is missing 'reference_buffer' - precompute it once "
            "per reference layer before calling this function."
        )

    reference = reference_df[
        [FEATURE_ID_COL, PART_NO_COL, "bearing", "line_length_m", "geometry", "reference_buffer"]
    ].copy()
    reference["reference_idx"] = reference.index

    reference_buffer_gdf = reference.set_geometry("reference_buffer")

    print("  Running spatial join...")
    t = time.perf_counter()

    candidates = gpd.sjoin(
        target,
        reference_buffer_gdf[
            [
                "reference_idx",
                FEATURE_ID_COL,
                PART_NO_COL,
                "bearing",
                "reference_buffer",
            ]
        ],
        how="inner",
        predicate="intersects",
    )

    log_step("  Spatial join", t)
    print(f"  Candidates after spatial join: {len(candidates):,}")

    if candidates.empty:
        return empty_pair_df()

    candidates = candidates.drop(columns=["index_right"], errors="ignore")

    print("  Calculating angle differences...")
    t = time.perf_counter()

    bearing_left = candidates["bearing_left"].to_numpy(dtype=float)
    bearing_right = candidates["bearing_right"].to_numpy(dtype=float)
    diff = np.abs(bearing_left - bearing_right) % 180
    candidates["angle_diff"] = np.minimum(diff, 180 - diff)

    log_step("  Angle calculation", t)

    before_angle = len(candidates)

    candidates = candidates[
        candidates["angle_diff"] <= MAX_ANGLE_DIFF
    ].copy()

    print(f"  Candidates after angle filter: {len(candidates):,} / {before_angle:,}")

    if candidates.empty:
        return empty_pair_df()

    print("  Filtering candidates by reference endpoints...")
    t = time.perf_counter()

    reference_geometry_lookup = reference.set_index("reference_idx")["geometry"]
    reference_length_lookup = reference.set_index("reference_idx")["line_length_m"]
    reference_geometries = reference_geometry_lookup.loc[
        candidates["reference_idx"]
    ].to_numpy()
    reference_lengths = reference_length_lookup.loc[
        candidates["reference_idx"]
    ].to_numpy(dtype=float)
    target_midpoints = shp.line_interpolate_point(
        candidates.geometry.to_numpy(),
        0.5,
        normalized=True,
    )
    midpoint_positions = shp.line_locate_point(
        reference_geometries,
        target_midpoints,
    )
    endpoint_mask = (
        (midpoint_positions > DISTANCE_TOLERANCE)
        & (midpoint_positions < reference_lengths - DISTANCE_TOLERANCE)
    )
    before_endpoint = len(candidates)
    candidates = candidates[endpoint_mask].copy()

    log_step("  Endpoint-position filter", t)
    print(
        f"  Candidates after endpoint-position filter: "
        f"{len(candidates):,} / {before_endpoint:,}"
    )

    if candidates.empty:
        return empty_pair_df()

    print("  Calculating overlap lengths...")
    t = time.perf_counter()

    reference_buffer_lookup = reference.set_index("reference_idx")["reference_buffer"]

    right_buffer_arr = reference_buffer_lookup.loc[candidates["reference_idx"]].to_numpy()
    left_geom_arr = candidates.geometry.to_numpy()

    overlap_geoms = shp.intersection(left_geom_arr, right_buffer_arr)
    overlap_length = shp.length(overlap_geoms)
    candidates["overlap_length"] = np.nan_to_num(overlap_length, nan=0.0)

    log_step("  Overlap calculation", t)

    candidates["overlap_ratio"] = (
        candidates["overlap_length"] / candidates["line_length_m"]
    )

    before_overlap = len(candidates)

    candidates = candidates[
        (candidates["overlap_length"] >= MIN_OVERLAP_LENGTH)
        & (candidates["overlap_ratio"] >= MIN_OVERLAP_RATIO)
    ].copy()

    print(f"  Candidates after overlap filter: {len(candidates):,} / {before_overlap:,}")

    if candidates.empty:
        return empty_pair_df()

    out = candidates.rename(columns={
        f"{FEATURE_ID_COL}_left": "target_feature_id",
        f"{PART_NO_COL}_left": "target_part_no",
        f"{FEATURE_ID_COL}_right": "reference_feature_id",
        f"{PART_NO_COL}_right": "reference_part_no",
    })

    out["target_layer"] = target_layer_name
    out["reference_type"] = reference_prefix
    out["reference_layer"] = reference_layer_name

    out = out[
        [
            "target_layer",
            "target_feature_id",
            "target_part_no",
            "reference_type",
            "reference_layer",
            "reference_feature_id",
            "reference_part_no",
            "angle_diff",
            "overlap_length",
            "overlap_ratio",
        ]
    ].copy()

    print(f"  Accepted pairs: {len(out):,}")
    log_step("  Total pair function", function_start)

    return out


def run_smart_authoritative_parallel_pairs(target_df, reference_df, target_layer_name, reference_layer_name, reference_prefix):
    all_rule_pairs = []
    for influencer_class, rule_col in INDUCTIVE_RULE_COLUMNS.items():
        reference_subset = reference_df[reference_df["influencer_class"].eq(influencer_class)]
        target_subset = target_df[target_df[rule_col].eq("no-pass")]
        print(f"{influencer_class}: references={len(reference_subset)}, targets={len(target_subset)}")
        if reference_subset.empty or target_subset.empty:
            continue
        pair_df = run_authoritative_parallel_pairs(
            target_subset, reference_subset, target_layer_name, reference_layer_name, reference_prefix
        )
        if pair_df.empty:
            continue
        pair_df["influencer_class"] = influencer_class
        pair_df["eligibility_column"] = rule_col
        pair_df["eligibility_value"] = "no-pass"
        all_rule_pairs.append(pair_df)
    if not all_rule_pairs:
        return pd.DataFrame(columns=[
            "target_layer", "target_feature_id", "target_part_no", "reference_type",
            "reference_layer", "reference_feature_id", "reference_part_no", "angle_diff",
            "overlap_length", "overlap_ratio", "influencer_class", "eligibility_column",
            "eligibility_value",
        ])
    out = pd.concat(all_rule_pairs, ignore_index=True)
    return out.groupby([
        "target_layer", "target_feature_id", "target_part_no", "reference_type",
        "reference_layer", "reference_feature_id", "reference_part_no",
    ], as_index=False).agg(
        angle_diff=("angle_diff", "min"),
        overlap_length=("overlap_length", "max"),
        overlap_ratio=("overlap_ratio", "max"),
        influencer_class=("influencer_class", lambda s: ",".join(sorted(set(s)))),
        eligibility_column=("eligibility_column", lambda s: ",".join(sorted(set(s)))),
        eligibility_value=("eligibility_value", lambda s: ",".join(sorted(set(s)))),
    )


def aggregate_reference_matches_for_target(pair_df, reference_prefix):
    columns = [
        "feature_id",
        "part_no",
        f"{reference_prefix}_match",
        f"{reference_prefix}_nfeatures",
        f"{reference_prefix}_nparts",
        f"{reference_prefix}_nkeys",
        f"{reference_prefix}_min_angle_diff",
        f"{reference_prefix}_max_overlap_length",
        f"{reference_prefix}_max_overlap_ratio",
    ]
    if pair_df.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for (feature_id, part_no), group in pair_df.groupby(
        ["target_feature_id", "target_part_no"],
        sort=False,
    ):
        rows.append(
            {
                "feature_id": feature_id,
                "part_no": part_no,
                f"{reference_prefix}_match": "yes",
                f"{reference_prefix}_nfeatures": unique_count(
                    group["reference_feature_id"]
                ),
                f"{reference_prefix}_nparts": unique_count(
                    group["reference_part_no"]
                ),
                f"{reference_prefix}_nkeys": unique_pair_count(
                    group["reference_feature_id"],
                    group["reference_part_no"],
                ),
                f"{reference_prefix}_min_angle_diff": group["angle_diff"].min(),
                f"{reference_prefix}_max_overlap_length": group[
                    "overlap_length"
                ].max(),
                f"{reference_prefix}_max_overlap_ratio": group[
                    "overlap_ratio"
                ].max(),
            }
        )
    return pd.DataFrame(rows, columns=columns)

def aggregate_targets_for_reference(pair_df, target_group_name):
    columns = [
        "feature_id",
        "part_no",
        f"{target_group_name}_match",
        f"{target_group_name}_nfeatures",
        f"{target_group_name}_nparts",
        f"{target_group_name}_nkeys",
        f"{target_group_name}_min_angle_diff",
        f"{target_group_name}_max_overlap_length",
        f"{target_group_name}_max_overlap_ratio",
    ]
    if pair_df.empty:
        return pd.DataFrame(columns=columns)

    rows = []
    for (feature_id, part_no), group in pair_df.groupby(
        ["reference_feature_id", "reference_part_no"],
        sort=False,
    ):
        rows.append(
            {
                "feature_id": feature_id,
                "part_no": part_no,
                f"{target_group_name}_match": "yes",
                f"{target_group_name}_nfeatures": group[
                    ["target_layer", "target_feature_id"]
                ].drop_duplicates().shape[0],
                f"{target_group_name}_nparts": group[
                    ["target_layer", "target_feature_id", "target_part_no"]
                ].drop_duplicates().shape[0],
                f"{target_group_name}_nkeys": group[
                    ["target_layer", "target_feature_id", "target_part_no"]
                ].drop_duplicates().shape[0],
                f"{target_group_name}_min_angle_diff": group["angle_diff"].min(),
                f"{target_group_name}_max_overlap_length": group[
                    "overlap_length"
                ].max(),
                f"{target_group_name}_max_overlap_ratio": group[
                    "overlap_ratio"
                ].max(),
            }
        )
    return pd.DataFrame(rows, columns=columns)

def merge_match_columns(base_gdf, agg_df, match_prefix):
    out = base_gdf.merge(agg_df, on=["feature_id", "part_no"], how="left")
    match_col = f"{match_prefix}_match"
    if match_col in out.columns:
        out[match_col] = out[match_col].fillna("no")
    return out


# =============================================================================
# MAIN
# =============================================================================
def find_parallel_features(
    input_path: Path,
    output_path: Path,
) -> Path:

    input_path = Path(input_path)
    output_path = Path(output_path)

    # Local working copies of the configuration.
    active_reference_layers = dict(reference_layers)
    active_influenced_layers = list(influenced_layers)

    pair_table_output_dir = output_path.with_name(
        f"{output_path.stem}_parallel_match_tables"
    )
    pair_table_output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    total_start = time.perf_counter()

    # Remove existing relevant output
    if output_path.exists():
        output_path.unlink()

    existing_layers = get_layers(input_path)

    # ---------------------------------------------------------------------------
    # Instead of raising as soon as *any* required layer is missing, check the
    # reference layers and influenced layers separately, print a warning for
    # whatever is missing, and drop just those entries so the run continues
    # with whatever layers ARE present. Everything downstream only ever
    # iterates over `reference_layers` / `influenced_layers`, so filtering
    # these two collections here is enough to make the rest of the script
    # skip the missing layers automatically.
    # ---------------------------------------------------------------------------
    missing_reference_layers = {
        prefix: name
        for prefix, name in active_reference_layers.items()
        if name not in existing_layers
    }

    missing_influenced_layers = [
        name
        for name in active_influenced_layers
        if name not in existing_layers
    ]

    if missing_reference_layers:
        print(
            "Warning: missing reference layers in input GeoPackage, skipping: "
            + ", ".join(
                f"{prefix} ({name})"
                for prefix, name in missing_reference_layers.items()
            )
        )

        active_reference_layers = {
            prefix: name
            for prefix, name in active_reference_layers.items()
            if prefix not in missing_reference_layers
        }

    if missing_influenced_layers:
        print(
            "Warning: missing influenced layers in input GeoPackage, skipping: "
            + ", ".join(missing_influenced_layers)
        )

        active_influenced_layers = [
            name
            for name in active_influenced_layers
            if name not in missing_influenced_layers
        ]

    required_layers = (
    list(active_reference_layers.values())
    + active_influenced_layers

    )
    if not required_layers:
        raise ValueError(
            "None of the required reference or influenced layers were found in the "
            "input GeoPackage - nothing to process."
        )

    print("Reading and preparing layers")
    raw_layers = {layer_name: read_layer(input_path, layer_name) for layer_name in required_layers}

    # Pick the CRS from the first available reference layer (preserving the
    # original "hv" preference when present); fall back to the first available
    # influenced layer if no reference layers made it through the check above.
    crs_source_layer_name = next(iter(active_reference_layers.values()), None) or required_layers[0]
    target_crs = raw_layers[crs_source_layer_name].crs
    if target_crs is None or target_crs.is_geographic:
        raise ValueError(f"Reference CRS must be projected; got {target_crs}.")
    prepared_layers = {
        layer_name: prepare_line_gdf(raw_layers[layer_name], layer_name, target_crs)
        for layer_name in required_layers
    }
    del raw_layers

    for reference_layer_name in active_reference_layers.values():
        prepared_layers[reference_layer_name] = add_influencer_class(
            prepared_layers[reference_layer_name],
            reference_layer_name
        )

        prepared_layers[reference_layer_name]["reference_buffer"] = (
            prepared_layers[reference_layer_name]
            .apply(
                lambda row: row.geometry.buffer(
                    MAX_DISTANCES[row["influencer_class"]],
                    cap_style=BUFFER_CAP_STYLE,
                    join_style=BUFFER_JOIN_STYLE,
                ),
                axis=1,
            )
        )

    for target_layer_name in active_influenced_layers:
        validate_inductive_columns(prepared_layers[target_layer_name], target_layer_name)

    all_pair_tables = []
    for reference_prefix, reference_layer_name in active_reference_layers.items():
        reference_df = prepared_layers[reference_layer_name]
        for target_layer_name in active_influenced_layers:
            pair_df = run_smart_authoritative_parallel_pairs(
                prepared_layers[target_layer_name], reference_df,
                target_layer_name, reference_layer_name, reference_prefix,
            )
            all_pair_tables.append(pair_df)

    all_pairs = pd.concat(all_pair_tables, ignore_index=True) if all_pair_tables else pd.DataFrame()
    all_pairs_path = pair_table_output_dir / "parallel_pairs_all.csv"
    all_pairs.to_csv(all_pairs_path, index=False)
    print("Total accepted pairs:", len(all_pairs))

    influenced_output_layers = {}
    for target_layer_name in active_influenced_layers:
        layer_out = prepared_layers[target_layer_name].copy()
        for reference_prefix in active_reference_layers:
            pair_subset = all_pairs[(all_pairs["target_layer"] == target_layer_name) & (all_pairs["reference_type"] == reference_prefix)]
            layer_out = merge_match_columns(layer_out, aggregate_reference_matches_for_target(pair_subset, reference_prefix), reference_prefix)
            match_col = f"{reference_prefix}_match"
            if match_col not in layer_out.columns:
                layer_out[match_col] = "no"
        influenced_output_layers[target_layer_name] = layer_out

    reference_output_layers = {}
    for reference_prefix, reference_layer_name in active_reference_layers.items():
        pair_subset = all_pairs[all_pairs["reference_type"] == reference_prefix]
        layer_out = merge_match_columns(
            prepared_layers[reference_layer_name],
            aggregate_targets_for_reference(pair_subset, "influenced"),
            "influenced",
        )
        if "influenced_match" not in layer_out.columns:
            layer_out["influenced_match"] = "no"
        reference_output_layers[reference_layer_name] = layer_out

#    # Remove influenced features that have no match to either reference layer
#    for layer_name in influenced_output_layers:
#        gdf = influenced_output_layers[layer_name]
#
#        if "hv_match" not in gdf.columns:
#            gdf["hv_match"] = "no"
#        if "cable_match" not in gdf.columns:
#            gdf["cable_match"] = "no"
#
#        keep_mask = ~(
#            (gdf["hv_match"] == "no") &
#            (gdf["cable_match"] == "no")
#        )
#
#        removed = int((~keep_mask).sum())
#        print(f"{layer_name}: removing {removed} features with hv_match=no and cable_match=no")
#
#        influenced_output_layers[layer_name] = gdf.loc[keep_mask].copy()

    # Keep all influenced features, including those with no HV/cable match.
    # Match columns are retained for downstream processing.
    # Remove only complete features that have no match on any of their parts.
    # If at least one part of a feature matches HV or cable, keep all parts.
    for layer_name in influenced_output_layers:
        gdf = influenced_output_layers[layer_name]

        if "hv_match" not in gdf.columns:
            gdf["hv_match"] = "no"
        if "cable_match" not in gdf.columns:
            gdf["cable_match"] = "no"

        feature_has_match = (
            gdf.groupby(FEATURE_ID_COL)["hv_match"].transform(lambda s: (s == "yes").any())
            |
            gdf.groupby(FEATURE_ID_COL)["cable_match"].transform(lambda s: (s == "yes").any())
        )

        removed = int((~feature_has_match).sum())
        removed_features = gdf.loc[~feature_has_match, FEATURE_ID_COL].nunique()

        print(
            f"{layer_name}: removing {removed_features} complete features "
            f"({removed} parts) with no HV/cable match"
        )

        influenced_output_layers[layer_name] = gdf.loc[feature_has_match].copy()


    # Remove Elektriciteitskabel features that did not match any influenced feature
#    electricity_layer_name = active_reference_layers.get("cable")  # "Elektriciteitskabel", if present
#
#    if electricity_layer_name and electricity_layer_name in reference_output_layers:
#        gdf = reference_output_layers[electricity_layer_name]
#
#        if "influenced_match" not in gdf.columns:
#            gdf["influenced_match"] = "no"
#
#        keep_mask = gdf["influenced_match"] != "no"
#
#        removed = int((~keep_mask).sum())
#        print(f"{electricity_layer_name}: removing {removed} features with influenced_match=no")
#
#        reference_output_layers[electricity_layer_name] = gdf.loc[keep_mask].copy()
#    elif "cable" in missing_reference_layers:
#        print("Skipping Elektriciteitskabel cleanup: layer was missing from input.")

    # Remove only complete cable features that have no influenced match
    # on any of their parts. If one part matches, keep all parts.
    electricity_layer_name = active_reference_layers.get("cable")

    if electricity_layer_name and electricity_layer_name in reference_output_layers:
        gdf = reference_output_layers[electricity_layer_name]

        if "influenced_match" not in gdf.columns:
            gdf["influenced_match"] = "no"

        feature_has_match = (
            gdf.groupby(FEATURE_ID_COL)["influenced_match"]
            .transform(lambda s: (s == "yes").any())
        )

        removed = int((~feature_has_match).sum())
        removed_features = gdf.loc[~feature_has_match, FEATURE_ID_COL].nunique()

        print(
            f"{electricity_layer_name}: removing {removed_features} complete features "
            f"({removed} parts) with no influenced match"
        )

        reference_output_layers[electricity_layer_name] = gdf.loc[feature_has_match].copy()

    elif "cable" in missing_reference_layers:
        print("Skipping Elektriciteitskabel cleanup: layer was missing from input.")

    # Filter Endpoints so only endpoints belonging to remaining v4 features are kept
    # Key is: source_layer + feature_id + part_no
    if "Endpoints" in existing_layers:
        endpoints_gdf = read_layer(input_path, "Endpoints").copy()

        required_endpoint_cols = [SOURCE_LAYER_COL, FEATURE_ID_COL, PART_NO_COL]
        missing = [col for col in required_endpoint_cols if col not in endpoints_gdf.columns]
        if missing:
            raise ValueError(f"Endpoints layer is missing required columns: {missing}")

        endpoints_gdf[SOURCE_LAYER_COL] = endpoints_gdf[SOURCE_LAYER_COL].astype(str)

        endpoints_gdf[FEATURE_ID_COL] = pd.to_numeric(
            endpoints_gdf[FEATURE_ID_COL],
            errors="coerce",
        )
        endpoints_gdf[PART_NO_COL] = pd.to_numeric(
            endpoints_gdf[PART_NO_COL],
            errors="coerce",
        )

        endpoints_gdf = endpoints_gdf.dropna(
            subset=[SOURCE_LAYER_COL, FEATURE_ID_COL, PART_NO_COL]
        ).copy()

        endpoints_gdf[FEATURE_ID_COL] = endpoints_gdf[FEATURE_ID_COL].astype(int)
        endpoints_gdf[PART_NO_COL] = endpoints_gdf[PART_NO_COL].astype(int)

        remaining_output_layers = {
            **reference_output_layers,
            **influenced_output_layers,
        }

        valid_layer_feature_part_keys = set()

        for layer_name, gdf in remaining_output_layers.items():
            if FEATURE_ID_COL not in gdf.columns or PART_NO_COL not in gdf.columns:
                continue

            layer_keys = zip(
                [str(layer_name)] * len(gdf),
                gdf[FEATURE_ID_COL].astype(int),
                gdf[PART_NO_COL].astype(int),
            )

            valid_layer_feature_part_keys.update(layer_keys)

        endpoint_keys = list(zip(
            endpoints_gdf[SOURCE_LAYER_COL].astype(str),
            endpoints_gdf[FEATURE_ID_COL].astype(int),
            endpoints_gdf[PART_NO_COL].astype(int),
        ))

        keep_mask = pd.Series(
            [key in valid_layer_feature_part_keys for key in endpoint_keys],
            index=endpoints_gdf.index,
        )

        removed = int((~keep_mask).sum())
        print(
            f"Endpoints: removing {removed} floating endpoints without remaining "
            f"source_layer/feature_id/part_no match"
        )

        endpoints_output_layer = endpoints_gdf.loc[keep_mask].copy()
    else:
        endpoints_output_layer = None

    print("Writing output GeoPackage")

    for layer_name, gdf in {**reference_output_layers, **influenced_output_layers}.items():
        gdf_to_write = gdf.drop(columns=["reference_buffer"], errors="ignore")
        write_or_replace_gpkg_layer(gdf_to_write, output_path, layer_name)

    if endpoints_output_layer is not None:
        write_or_replace_gpkg_layer(
            endpoints_output_layer,
            output_path,
            "Endpoints",
        )

    for layer_name in copy_unchanged_layers:
        if layer_name in existing_layers:
            write_or_replace_gpkg_layer(
                read_layer(input_path, layer_name),
                output_path,
                layer_name,
            )

    print("DONE")
    print("Input GeoPackage:", input_path)
    print("Output GeoPackage:", output_path)
    print("Combined pair table:", all_pairs_path)
    print("MAX_DISTANCES:", MAX_DISTANCES)
    print("MAX_ANGLE_DIFF:", MAX_ANGLE_DIFF)
    print("MIN_OVERLAP_LENGTH:", MIN_OVERLAP_LENGTH)
    print("MIN_OVERLAP_RATIO:", MIN_OVERLAP_RATIO)
    log_step("Total runtime", total_start)

    return output_path