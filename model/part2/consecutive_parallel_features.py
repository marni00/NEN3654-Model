#Script 13 Test, keeps whole run_ids, instead of only influenced sections.
"""
Script 13 (wrapped): assign HV run_id values to proposed high-voltage /
electricity-cable parts using their existing feature_id, and link
influenced-layer (buisleiding) parts to those runs plus their own
influenced_run_id sub-groups.

This module exposes a single entry point, `run_script13(...)`, so it can be
imported and called from main.py as one step of the KLIC processing
orchestrator instead of being executed as a standalone script.

Usage from main.py:

    from script13_run_ids import run_script13

    result = run_script13(
        input_gpkg_path="../data/KLIC/KLIC_didam_filtered_combined_relevant_nen3654v4_parallel_endpoint_distances.gpkg",
        output_gpkg_path="../data/KLIC/KLIC_v5.gpkg",
    )

    proposed_result = result["proposed_result"]
"""

from pathlib import Path
import os
import ast
import pandas as pd
import geopandas as gpd
import pyogrio
from shapely.geometry import Point

from .relational_tables import read_table


# ------------------------------------------------------------
# Pure helper functions (no dependency on script-level config;
# unchanged from the original script)
# ------------------------------------------------------------

def list_gpkg_layers(path):
    return [row[0] for row in pyogrio.list_layers(path)]


REFERENCE_REQUIRED_COLUMNS = [
    "feature_id",
    "part_no",
    "influenced_match",
    "influenced_keys",
    "verticalePositie",
]


def empty_reference_gdf(crs=None):
    # Carries the columns filter_matched_influencers() and the
    # required_part_cols check further down both expect, so a missing
    # reference layer degrades to "zero eligible parts" instead of a
    # KeyError / ValueError.
    data = {col: pd.Series(dtype="object") for col in REFERENCE_REQUIRED_COLUMNS}
    return gpd.GeoDataFrame(data, geometry=gpd.GeoSeries([], crs=crs), crs=crs)


def read_reference_layer_safe(path, key, layer_name, existing_layers):
    if layer_name not in existing_layers:
        print(
            f"Warning: reference layer '{layer_name}' ('{key}') not found in "
            f"{path.name}; continuing with an empty GeoDataFrame for it."
        )
        return empty_reference_gdf()

    gdf = gpd.read_file(path, layer=layer_name)

    if gdf.empty:
        print(f"Note: reference layer '{layer_name}' is present but empty (0 features).")

    return gdf


def filter_matched_influencers(gdf, layer_name):
    required = {"influenced_match", "influenced_keys", "feature_id"}
    missing = required - set(gdf.columns)

    if missing:
        raise ValueError(
            f"{layer_name} is missing required influencer columns: {missing}. "
            f"Available columns: {list(gdf.columns)}"
        )

    part_matched = (
        gdf["influenced_match"].astype(str).str.strip().str.lower().eq("yes") &
        gdf["influenced_keys"].notna() &
        gdf["influenced_keys"].astype(str).str.strip().ne("")
    )

    # Keep the complete original feature when any of its parts is matched.
    feature_has_match = part_matched.groupby(gdf["feature_id"]).transform("any")

    removed = int((~feature_has_match).sum())
    removed_features = gdf.loc[~feature_has_match, "feature_id"].nunique()
    if removed:
        print(
            f"{layer_name}: removing {removed_features} complete features "
            f"({removed} parts) with no influenced match."
        )

    return gdf.loc[feature_has_match].copy()


def filter_relevant_influenced_features(gdf, layer_name):
    required = {"hv_match", "cable_match", "feature_id"}
    missing = required - set(gdf.columns)

    if missing:
        raise ValueError(
            f"{layer_name} is missing required match columns: {missing}. "
            f"Available columns: {list(gdf.columns)}"
        )

    part_matched = (
        gdf["hv_match"].astype(str).str.strip().str.lower().eq("yes") |
        gdf["cable_match"].astype(str).str.strip().str.lower().eq("yes")
    )

    # Keep the complete original feature when any of its parts is matched.
    feature_has_match = part_matched.groupby(gdf["feature_id"]).transform("any")

    removed = int((~feature_has_match).sum())
    removed_features = gdf.loc[~feature_has_match, "feature_id"].nunique()
    if removed:
        print(
            f"{layer_name}: removing {removed_features} complete features "
            f"({removed} parts) with no HV/cable match."
        )

    return gdf.loc[feature_has_match].copy()


def empty_gdf(crs=None):
    return gpd.GeoDataFrame(
        geometry=gpd.GeoSeries([], crs=crs),
        crs=crs,
    )


def empty_influenced_features_gdf(crs=None):
    """
    Like empty_gdf, but also carries feature_id/part_no/source_layer columns
    so downstream key-building and layer-aware grouping don't crash when
    every influenced layer is missing or empty.
    """
    return gpd.GeoDataFrame(
        {
            "feature_id": pd.Series(dtype="int64"),
            "part_no": pd.Series(dtype="int64"),
            "source_layer": pd.Series(dtype="object"),
        },
        geometry=gpd.GeoSeries([], crs=crs),
        crs=crs,
    )


def get_influenced_layer(layer_name, influenced, references):
    if layer_name in influenced:
        return influenced[layer_name]

    print(f"Warning: influenced layer unavailable or empty, using empty GeoDataFrame: {layer_name}")

    fallback_crs = None

    if influenced:
        fallback_crs = next(
            (gdf.crs for gdf in influenced.values() if gdf.crs is not None),
            None,
        )

    if fallback_crs is None:
        fallback_crs = references["hv"].crs

    return empty_gdf(crs=fallback_crs)


def parse_layer_feature_part_keys(value):
    """
    Convert keys to (layer, feature_id, part_no) tuples.

    New format:
        "OlieGasChemicalienPijpleiding|3859:607,..."
    """
    if value is None:
        return []

    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        try:
            if pd.isna(value):
                return []
        except Exception:
            pass

        s = str(value).strip()
        if not s:
            return []

        try:
            parsed = ast.literal_eval(s)
            if isinstance(parsed, (list, tuple, set)):
                raw_items = list(parsed)
            else:
                raw_items = [s]
        except Exception:
            raw_items = [x.strip() for x in s.split(",") if x.strip()]

    out = []

    for item in raw_items:
        text = str(item).strip()

        if ":" not in text:
            continue

        if "|" not in text:
            continue

        layer_name, key_text = text.rsplit("|", 1)
        layer_name = layer_name.strip()

        if ":" not in key_text:
            continue

        left, right = key_text.split(":", 1)

        try:
            influenced_feature_id = int(float(left))
            influenced_part_no = int(float(right))
            out.append((layer_name, influenced_feature_id, influenced_part_no))
        except Exception:
            pass

    # Preserve order while removing duplicates
    return list(dict.fromkeys(out))


def keys_missing(value):
    return len(parse_layer_feature_part_keys(value)) == 0


def feature_ids_from_keys(value):
    """
    Extract only the influenced feature IDs from influenced_keys.

    Example:
        "50:1,50:2,60:1" -> {50, 60}
    """
    pairs = parse_layer_feature_part_keys(value)
    return set(
        (parsed_key[0], parsed_key[1])
        for parsed_key in pairs
    )


def feature_transition_allowed(a, b):
    """
    Allows connection when two proposed HV parts share at least one
    influenced feature_id, regardless of influenced part_no.
    """
    a_features = feature_ids_from_keys(a)
    b_features = feature_ids_from_keys(b)

    if not a_features or not b_features:
        return False

    return len(a_features & b_features) > 0


def normalise_key_value(value):
    """
    Make feature_id / part_no merge keys stable.

    This avoids problems like:
        1     -> "1"
        1.0   -> "1"
        "1"   -> "1"
    """
    if pd.isna(value):
        return ""

    try:
        number = float(value)
        if number.is_integer():
            return str(int(number))
    except Exception:
        pass

    return str(value).strip()


def make_feature_part_key(df, feature_col="feature_id", part_col="part_no"):
    return (
        df[feature_col].apply(normalise_key_value) + "|" +
        df[part_col].apply(normalise_key_value)
    )


def make_layer_feature_part_key(
    df,
    layer_col=None,
    feature_col="feature_id",
    part_col="part_no",
):
    """Build keys matching layer|feature_id:part_no values."""
    if layer_col is None:
        for candidate in ["originalLayer", "source_layer", "layer"]:
            if candidate in df.columns:
                layer_col = candidate
                break

    if layer_col is None:
        return make_feature_part_key(df, feature_col, part_col)

    return (
        df[layer_col].map(normalise_key_value) + "|" +
        df[feature_col].map(normalise_key_value) + ":" +
        df[part_col].map(normalise_key_value)
    )


def remove_file_if_exists(path):
    if os.path.exists(path):
        os.remove(path)


def format_run_id_for_influenced_run_id(value):
    """
    Format run_id safely for influenced_run_id text.

    Examples:
        6     -> "6"
        6.0   -> "6"
        "6"   -> "6"
    """
    try:
        number = float(value)
        if number.is_integer():
            return str(int(number))
    except Exception:
        pass

    return str(value).strip()


def get_line_endpoints(geom):
    """
    Return start/end points for LineString or MultiLineString geometry.
    """
    if geom is None or geom.is_empty:
        return []

    endpoints = []

    if geom.geom_type == "LineString":
        coords = list(geom.coords)
        if len(coords) >= 2:
            endpoints.append(Point(coords[0]))
            endpoints.append(Point(coords[-1]))

    elif geom.geom_type == "MultiLineString":
        for part in geom.geoms:
            coords = list(part.coords)
            if len(coords) >= 2:
                endpoints.append(Point(coords[0]))
                endpoints.append(Point(coords[-1]))

    return endpoints


class UnionFind:
    def __init__(self, n):
        self.parent = list(range(n))

    def find(self, x):
        x = int(x)

        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]

        return x

    def union(self, a, b):
        a = int(a)
        b = int(b)

        root_a = self.find(a)
        root_b = self.find(b)

        if root_a != root_b:
            self.parent[root_b] = root_a


def detect_layer_col(df, candidates=("source_layer", "originalLayer", "layer")):
    for candidate in candidates:
        if candidate in df.columns:
            return candidate
    return None


def assign_influenced_run_ids_per_hv_run(
    influenced_gdf,
    run_col="run_id",
    tolerance_m=1.0,
    connect_same_feature_id=True,
    layer_col=None,
):
    """
    Assign influenced_run_id values inside each HV run_id.

    NEW GROUPING RULE:
    - An influenced_run_id may NOT cross feature_id boundaries.
    - Within each run_id, all parts with the same
      (influenced layer, feature_id) are assigned to the same
      influenced_run_id.
    - Different influenced layers are never merged, even if they
      share the same numeric feature_id.
    - Geometry/connectivity is NOT used to merge different feature_ids.

    Example:
        run_id = 4

        Waterleiding feature_id 50, parts 1/2/3
            -> influenced_run_id = "4.1"

        Waterleiding feature_id 51, parts 1/2
            -> influenced_run_id = "4.2"

        Rioolleiding feature_id 50, parts 1/2
            -> influenced_run_id = "4.3"

    Important:
    - No assignment_type column is created.
    - influenced_run_id is text, not float.
    - Parts with the same influenced feature_id AND the same
      influenced layer are forced into the same component.
    - Different influenced layers are NEVER merged just because
      they share a numeric feature_id.
    - Different feature_ids are NEVER merged into the same
      influenced_run_id.
    """

    out = influenced_gdf.copy()

    out = out.drop(
        columns=[
            "influenced_component_no",
            "influenced_subrun_no",
            "influenced_run_id",
            "influenced_component_length_m",
        ],
        errors="ignore",
    )

    out["influenced_component_no"] = pd.Series(
        pd.NA,
        index=out.index,
        dtype="Int64"
    )
    out["influenced_subrun_no"] = pd.Series(
        pd.NA,
        index=out.index,
        dtype="Int64"
    )
    out["influenced_run_id"] = pd.NA
    out["influenced_component_length_m"] = pd.NA

    if run_col not in out.columns:
        raise ValueError(
            f"Cannot assign influenced_run_id because '{run_col}' is missing."
        )

    if len(out) == 0:
        return out

    # Make sure geometry distance is in metres
    if out.crs is not None and out.crs.is_geographic:
        out = out.to_crs(28992)

    out["_component_length_helper"] = out.geometry.length

    resolved_layer_col = layer_col or detect_layer_col(out)

    if resolved_layer_col is None and out[run_col].notna().any():
        print(
            "Warning: no layer column found (looked for 'source_layer', "
            "'originalLayer', 'layer'). Falling back to feature_id-only "
            "connectivity -- influenced_run_id may merge across different "
            "influenced layers. Add a layer column to avoid this."
        )

    valid = (
        out[run_col].notna() &
        out.geometry.notna() &
        (~out.geometry.is_empty)
    )

    if valid.sum() == 0:
        out = out.drop(
            columns=["_component_length_helper"],
            errors="ignore"
        )
        return out

    # ============================================================
    # PROCESS EACH HV run_id SEPARATELY
    # ============================================================
    for run_id, group in out.loc[valid].groupby(run_col, sort=True):

        group_work = group.copy().reset_index().rename(
            columns={"index": "_original_index"}
        )

        n = len(group_work)

        if n == 0:
            continue

        uf = UnionFind(n)

        layer_values = (
            group_work[resolved_layer_col]
            if resolved_layer_col is not None
            else pd.Series([None] * n, index=group_work.index)
        )

        # ========================================================
        # GROUP ONLY BY:
        #
        #     source_layer + feature_id
        #
        # This means an influenced_run_id can NEVER cross
        # feature_id boundaries.
        #
        # Geometry is deliberately NOT used here.
        # ========================================================
        if connect_same_feature_id and "feature_id" in group_work.columns:

            group_keys = (
                [resolved_layer_col, "feature_id"]
                if resolved_layer_col
                else ["feature_id"]
            )

            for _, idxs in group_work.groupby(group_keys).groups.items():

                idxs = list(idxs)

                if len(idxs) <= 1:
                    continue

                first_idx = idxs[0]

                for other_idx in idxs[1:]:
                    uf.union(first_idx, other_idx)

        # ========================================================
        # CONVERT COMPONENTS TO influenced_run_id
        #
        # At this point each component corresponds to exactly one
        # (source_layer, feature_id) combination.
        # ========================================================
        group_work["_component_root"] = [
            uf.find(i)
            for i in range(n)
        ]

        component_lengths = (
            group_work
            .groupby("_component_root")["_component_length_helper"]
            .sum()
            .sort_values(ascending=False)
        )

        root_to_subrun = {
            root: idx + 1
            for idx, root in enumerate(
                component_lengths.index.tolist()
            )
        }

        root_to_length = component_lengths.to_dict()

        group_work["influenced_subrun_no"] = (
            group_work["_component_root"]
            .map(root_to_subrun)
        )

        group_work["influenced_component_no"] = (
            group_work["influenced_subrun_no"]
        )

        group_work["influenced_component_length_m"] = (
            group_work["_component_root"]
            .map(root_to_length)
        )

        run_text = format_run_id_for_influenced_run_id(run_id)

        group_work["influenced_run_id"] = (
            run_text
            + "."
            + group_work["influenced_subrun_no"]
                .astype(int)
                .astype(str)
        )

        # Write results back to original dataframe
        for _, row in group_work.iterrows():

            original_idx = row["_original_index"]

            out.at[
                original_idx,
                "influenced_component_no"
            ] = int(row["influenced_component_no"])

            out.at[
                original_idx,
                "influenced_subrun_no"
            ] = int(row["influenced_subrun_no"])

            out.at[
                original_idx,
                "influenced_run_id"
            ] = row["influenced_run_id"]

            out.at[
                original_idx,
                "influenced_component_length_m"
            ] = float(
                row["influenced_component_length_m"]
            )

    out = out.drop(
        columns=["_component_length_helper"],
        errors="ignore"
    )

    return out

def build_influenced_run_links(
    influenced_features,
    feature_run_bridge,
    dist_summary,
):
    """
    Build normalized run memberships without duplicating feature geometry.

    A physical influenced part can belong to many reference runs. Expanding the
    complete GeoDataFrame for that many-to-many relationship is prohibitively
    expensive because every attribute and geometry is copied before grouping.
    This function carries only identifiers, distances, and one numeric length
    through the expansion.
    """
    output_columns = [
        "source_layer",
        "feature_id",
        "part_no",
        "run_id",
        "influenced_component_no",
        "influenced_subrun_no",
        "influenced_run_id",
        "influenced_component_length_m",
        "min_distance",
        "max_distance",
    ]

    if influenced_features.empty or feature_run_bridge.empty:
        return pd.DataFrame(columns=output_columns)

    required_feature_columns = {"source_layer", "feature_id", "part_no"}
    missing = required_feature_columns - set(influenced_features.columns)
    if missing:
        raise ValueError(
            "Influenced geometry is missing membership columns: "
            f"{sorted(missing)}"
        )

    length_source = influenced_features
    if (
        length_source.crs is not None
        and length_source.crs.is_geographic
    ):
        length_source = length_source.to_crs(28992)

    # Convert Arrow-backed columns to ordinary pandas/numpy arrays here. This
    # prevents pandas from materializing and sorting the entire expanded
    # GeoDataFrame through pyarrow during groupby.
    parts = pd.DataFrame(
        {
            "source_layer": influenced_features["source_layer"]
            .astype("string")
            .to_numpy(dtype=object),
            "feature_id": pd.to_numeric(
                influenced_features["feature_id"],
                errors="raise",
            ).to_numpy(),
            "part_no": pd.to_numeric(
                influenced_features["part_no"],
                errors="raise",
            ).to_numpy(),
            "_part_length_m": length_source.geometry.length.to_numpy(
                dtype=float
            ),
            "_feature_order": range(len(influenced_features)),
        }
    )

    bridge = feature_run_bridge[
        ["source_layer", "feature_id", "run_id"]
    ].drop_duplicates().copy()
    bridge["source_layer"] = (
        bridge["source_layer"].astype("string").to_numpy(dtype=object)
    )
    bridge["feature_id"] = pd.to_numeric(
        bridge["feature_id"],
        errors="raise",
    ).to_numpy()
    bridge["run_id"] = pd.to_numeric(
        bridge["run_id"],
        errors="raise",
    ).to_numpy()

    memberships = parts.merge(
        bridge,
        on=["source_layer", "feature_id"],
        how="inner",
        validate="many_to_many",
        sort=False,
    )

    if memberships.empty:
        return pd.DataFrame(columns=output_columns)

    if dist_summary is not None and not dist_summary.empty:
        distances = dist_summary[
            [
                "source_layer",
                "feature_id",
                "part_no",
                "min_distance",
                "max_distance",
            ]
        ].copy()
        distances["source_layer"] = (
            distances["source_layer"]
            .astype("string")
            .to_numpy(dtype=object)
        )
        for column in ["feature_id", "part_no"]:
            distances[column] = pd.to_numeric(
                distances[column],
                errors="raise",
            ).to_numpy()

        memberships = memberships.merge(
            distances,
            on=["source_layer", "feature_id", "part_no"],
            how="left",
            validate="many_to_one",
            sort=False,
        )
    else:
        memberships["min_distance"] = pd.NA
        memberships["max_distance"] = pd.NA

    component_keys = ["run_id", "source_layer", "feature_id"]
    components = (
        memberships.groupby(
            component_keys,
            as_index=False,
            sort=False,
            dropna=False,
        )
        .agg(
            influenced_component_length_m=("_part_length_m", "sum"),
            _feature_order=("_feature_order", "min"),
        )
        .sort_values(
            [
                "run_id",
                "influenced_component_length_m",
                "_feature_order",
            ],
            ascending=[True, False, True],
            kind="stable",
        )
    )
    components["influenced_subrun_no"] = (
        components.groupby("run_id", sort=False).cumcount() + 1
    )
    components["influenced_component_no"] = (
        components["influenced_subrun_no"]
    )
    components["influenced_run_id"] = (
        components["run_id"]
        .map(format_run_id_for_influenced_run_id)
        + "."
        + components["influenced_subrun_no"].astype(str)
    )

    memberships = memberships.merge(
        components[
            component_keys
            + [
                "influenced_component_no",
                "influenced_subrun_no",
                "influenced_run_id",
                "influenced_component_length_m",
            ]
        ],
        on=component_keys,
        how="left",
        validate="many_to_one",
        sort=False,
    )

    return (
        memberships[output_columns]
        .drop_duplicates()
        .reset_index(drop=True)
    )


def write_gdf_layer(gdf, path, layer_name, driver="GPKG"):
    if gdf is None or gdf.empty:
        print(
            f"Note: '{layer_name}' has no features to write; skipping this "
            "layer in the output GeoPackage."
        )
        return

    gdf.to_file(path, layer=layer_name, driver=driver)

def _print_summary(
    parts,
    proposed_result,
    influenced_features,
    influenced_result,
    influenced_run_links,
    output_gpkg_path,
    influenced_run_links_out,
):
    if len(influenced_run_links) > 0:
        influenced_run_links = influenced_run_links.copy()
        influenced_run_links["_key"] = make_feature_part_key(influenced_run_links)

        multi_run_influenced = influenced_run_links.groupby("_key")["run_id"].nunique()
        multi_influenced_run_targets = influenced_run_links.groupby("_key")["influenced_run_id"].nunique()

        multi_run_influenced_count = int((multi_run_influenced > 1).sum())
        multi_influenced_run_influenced_count = int((multi_influenced_run_targets > 1).sum())
    else:
        multi_run_influenced = pd.Series(dtype=int)
        multi_influenced_run_targets = pd.Series(dtype=int)
        multi_run_influenced_count = 0
        multi_influenced_run_influenced_count = 0

    expanded_extra_rows = len(influenced_result) - len(influenced_features)

    print("Saved all GeoPackage layers to:", output_gpkg_path)
    print("  - proposed_hv_parts_with_runid")
    print("  - electricity_cable_parts_with_runid")
    print("  - influenced_parts_with_runid_distance")
    print("Saved influenced-run_id link table to:", influenced_run_links_out)

    print()
    print("Number of proposed HV parts used for run_id:", len(parts))
    print("Number of HV run_id groups:", parts["run_id"].nunique())
    print("Proposed HV parts with run_id:", proposed_result["run_id"].notna().sum())
    print("Proposed HV parts without run_id:", proposed_result["run_id"].isna().sum())

    print()
    print("Rows in original influenced_features:", len(influenced_features))
    print("Rows in expanded influenced_result:", len(influenced_result))
    print("Extra rows in expanded influenced_result due to multiple run_ids:", expanded_extra_rows)

    print()
    print("Influenced-layer rows with HV run_id:", influenced_result["run_id"].notna().sum())
    print("Influenced-layer rows without HV run_id:", influenced_result["run_id"].isna().sum())

    print()
    print("Influenced-layer rows with influenced_run_id:", influenced_result["influenced_run_id"].notna().sum())
    print("Influenced-layer rows without influenced_run_id:", influenced_result["influenced_run_id"].isna().sum())

    print()
    print("Influenced-layer rows linked to multiple HV run_ids:", multi_run_influenced_count)
    print("Influenced-layer rows linked to multiple influenced_run_ids:", multi_influenced_run_influenced_count)

    print()
    print("Influenced-layer rows with min_distance:", influenced_result["min_distance"].notna().sum())
    print("Influenced-layer rows with max_distance:", influenced_result["max_distance"].notna().sum())
    print("Influenced-layer rows without any distance:", influenced_result["min_distance"].isna().sum())

    print()
    print("Influenced-run_id summary:")
    if influenced_result["influenced_run_id"].notna().any():
        influenced_run_preview = (
            influenced_result[
                influenced_result["influenced_run_id"].notna()
            ]
            .groupby(["run_id", "influenced_run_id"], as_index=False)
            .agg(
                row_count=("influenced_run_id", "size"),
                feature_ids=(
                    "feature_id",
                    lambda x: ",".join(
                        map(str, sorted(set(int(v) for v in x if pd.notna(v))))
                    )
                ),
                part_count=("part_no", "count"),
                component_length_m=("influenced_component_length_m", "max")
            )
            .sort_values(["run_id", "influenced_run_id"])
        )

        print(influenced_run_preview.to_string(index=False))
    else:
        print("No influenced_run_id values assigned.")

    print()
    print("Top influenced parts linked to multiple HV run_ids:")
    if multi_run_influenced_count > 0:
        multi_keys = multi_run_influenced[multi_run_influenced > 1].reset_index()
        multi_keys = multi_keys.rename(columns={"run_id": "run_id_count"})

        print(multi_keys.head(20).to_string(index=False))
    else:
        print("None")

    print()
    print("Top influenced parts linked to multiple influenced_run_ids:")
    if multi_influenced_run_influenced_count > 0:
        multi_influenced_keys = multi_influenced_run_targets[multi_influenced_run_targets > 1].reset_index()
        multi_influenced_keys = multi_influenced_keys.rename(columns={"influenced_run_id": "influenced_run_id_count"})

        print(multi_influenced_keys.head(20).to_string(index=False))
    else:
        print("None")

# ------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------

def run_script13(
    input_gpkg_path,
    output_gpkg_path,
    output_dir=None,
    reference_layers=None,
    influenced_layers=None,
    copy_unchanged_layers=None,
    extra_layers=None,
    touch_tolerance=0.1,
    influenced_touch_tolerance=1.0,
    influenced_run_links_filename="influenced_runid_links.csv",
    verbose=True,
    geometry_gpkg_path=None,
    pair_table_path=None,
):
    """Assign run IDs while storing geometry and run membership separately."""
    input_gpkg_path = Path(input_gpkg_path)
    output_gpkg_path = Path(output_gpkg_path)
    geometry_gpkg_path = (
        Path(geometry_gpkg_path)
        if geometry_gpkg_path is not None
        else input_gpkg_path
    )

    if output_dir is None:
        output_dir = output_gpkg_path.parent
    output_dir = Path(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    if reference_layers is None:
        reference_layers = {
            "hv": "proposed_highvoltage",
            "cable": "Elektriciteitskabel",
        }
    if influenced_layers is None:
        influenced_layers = [
            "OlieGasChemicalienPijpleiding",
            "Overig",
            "Waterleiding",
            "Rioolleiding",
            "ThermischePijpleiding",
        ]

    if pair_table_path is None:
        pair_table_path = geometry_gpkg_path.with_name(
            f"{geometry_gpkg_path.stem}_parallel_match_tables"
        ) / "parallel_pairs_all.csv"
    else:
        pair_table_path = Path(pair_table_path)

    if not pair_table_path.exists():
        raise FileNotFoundError(f"Pair table not found: {pair_table_path}")

    influenced_run_links_out = output_dir / influenced_run_links_filename
    existing_layers = list_gpkg_layers(geometry_gpkg_path)
    pairs = pd.read_csv(pair_table_path)

    required_pair_columns = {
        "target_layer",
        "target_feature_id",
        "target_part_no",
        "reference_type",
        "reference_layer",
        "reference_feature_id",
        "reference_part_no",
    }
    missing = required_pair_columns - set(pairs.columns)
    if missing:
        raise ValueError(f"Pair table is missing columns: {sorted(missing)}")

    for column in [
        "target_feature_id",
        "target_part_no",
        "reference_feature_id",
        "reference_part_no",
    ]:
        pairs[column] = pd.to_numeric(pairs[column], errors="coerce")
    pairs = pairs.dropna(
        subset=[
            "target_feature_id",
            "target_part_no",
            "reference_feature_id",
            "reference_part_no",
        ]
    ).copy()
    for column in [
        "target_feature_id",
        "target_part_no",
        "reference_feature_id",
        "reference_part_no",
    ]:
        pairs[column] = pairs[column].astype(int)

    references = {}
    for key, layer_name in reference_layers.items():
        if layer_name not in existing_layers:
            references[key] = empty_reference_gdf()
            continue
        references[key] = gpd.read_file(geometry_gpkg_path, layer=layer_name)

    influenced_frames = []
    for layer_name in influenced_layers:
        if layer_name not in existing_layers:
            continue
        layer_gdf = gpd.read_file(geometry_gpkg_path, layer=layer_name)
        layer_gdf["source_layer"] = layer_name
        relevant_feature_ids = set(
            pairs.loc[
                pairs["target_layer"].eq(layer_name),
                "target_feature_id",
            ].astype(int)
        )
        layer_gdf = layer_gdf[
            pd.to_numeric(layer_gdf["feature_id"], errors="coerce")
            .astype("Int64")
            .isin(relevant_feature_ids)
        ].copy()
        if not layer_gdf.empty:
            influenced_frames.append(layer_gdf)

    fallback_crs = next(
        (
            gdf.crs
            for gdf in references.values()
            if gdf is not None and gdf.crs is not None
        ),
        None,
    )
    if influenced_frames:
        influenced_features = gpd.GeoDataFrame(
            pd.concat(influenced_frames, ignore_index=True),
            geometry="geometry",
            crs=influenced_frames[0].crs,
        )
    else:
        influenced_features = empty_influenced_features_gdf(fallback_crs)

    reference_results = {}
    run_rows = []
    cable_offset = 100

    for reference_type, layer_name in reference_layers.items():
        gdf = references[reference_type].copy()
        if gdf.empty:
            reference_results[reference_type] = gdf
            continue

        matched_feature_ids = set(
            pairs.loc[
                pairs["reference_type"].eq(reference_type),
                "reference_feature_id",
            ].astype(int)
        )
        feature_ids = pd.to_numeric(gdf["feature_id"], errors="coerce").astype(
            "Int64"
        )
        gdf = gdf[feature_ids.isin(matched_feature_ids)].copy()
        gdf["feature_id"] = pd.to_numeric(
            gdf["feature_id"],
            errors="raise",
        ).astype(int)
        gdf["part_no"] = pd.to_numeric(
            gdf["part_no"],
            errors="raise",
        ).astype(int)
        gdf["run_id"] = gdf["feature_id"]
        if reference_type == "cable":
            gdf["run_id"] = gdf["run_id"] + cable_offset

        reference_results[reference_type] = gdf
        run_rows.append(
            gdf[
                ["feature_id", "part_no", "run_id"]
            ].assign(
                reference_type=reference_type,
                reference_layer=layer_name,
            )
        )

    if run_rows:
        run_lookup = pd.concat(run_rows, ignore_index=True)
    else:
        run_lookup = pd.DataFrame(
            columns=[
                "feature_id",
                "part_no",
                "run_id",
                "reference_type",
                "reference_layer",
            ]
        )

    relationship_parts = pairs.merge(
        run_lookup.rename(
            columns={
                "feature_id": "reference_feature_id",
                "part_no": "reference_part_no",
            }
        ),
        on=[
            "reference_type",
            "reference_layer",
            "reference_feature_id",
            "reference_part_no",
        ],
        how="inner",
        validate="many_to_one",
    )

    feature_run_bridge = relationship_parts[
        ["target_layer", "target_feature_id", "run_id"]
    ].drop_duplicates().rename(
        columns={
            "target_layer": "source_layer",
            "target_feature_id": "feature_id",
        }
    )

    observations = read_table(
        input_gpkg_path,
        "endpoint_distance_observations",
    )
    dist_summary = (
        observations.groupby(
            ["target_layer", "target_feature_id", "target_part_no"],
            as_index=False,
        )["distance"]
        .agg(min_distance="min", max_distance="max")
        .rename(
            columns={
                "target_layer": "source_layer",
                "target_feature_id": "feature_id",
                "target_part_no": "part_no",
            }
        )
    )

    influenced_run_links = build_influenced_run_links(
        influenced_features=influenced_features,
        feature_run_bridge=feature_run_bridge,
        dist_summary=dist_summary,
    )

    remove_file_if_exists(output_gpkg_path)
    first_write = True
    layer_outputs = [
        (
            reference_results.get("hv"),
            "proposed_hv_parts_with_runid",
        ),
        (
            reference_results.get("cable"),
            "electricity_cable_parts_with_runid",
        ),
        (
            influenced_features,
            "influenced_parts_with_runid_distance",
        ),
    ]
    for gdf, layer_name in layer_outputs:
        if gdf is None or gdf.empty:
            continue
        gdf.to_file(
            output_gpkg_path,
            layer=layer_name,
            driver="GPKG",
            mode="w" if first_write else "a",
        )
        first_write = False

    influenced_run_links.to_csv(influenced_run_links_out, index=False)

    if verbose:
        print(f"Saved normalized run memberships: {influenced_run_links_out}")
        print(
            "Distinct cable/HV-to-conduit runs:",
            influenced_run_links[
                ["run_id", "influenced_run_id"]
            ].drop_duplicates().shape[0],
        )

    return {
        "proposed_result": reference_results.get("hv"),
        "cable_result": reference_results.get("cable"),
        "influenced_result": influenced_features,
        "influenced_run_links": influenced_run_links,
        "output_gpkg_path": output_gpkg_path,
        "influenced_run_links_out": influenced_run_links_out,
    }

def consecutive_parallel_features(
    input_path,
    output_path,
    geometry_path=None,
    pair_table_path=None,
):
    """Run Script 13 using separate observation and geometry sources."""
    run_script13(
        input_gpkg_path=input_path,
        output_gpkg_path=output_path,
        geometry_gpkg_path=geometry_path,
        pair_table_path=pair_table_path,
    )
    return Path(output_path)

