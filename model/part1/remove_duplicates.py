#Step 4

from pathlib import Path
from collections import defaultdict, deque
from itertools import combinations

import pandas as pd
import geopandas as gpd
import pyogrio

from shapely.errors import GEOSException

try:
    from shapely import normalize as shapely_normalize
except ImportError:
    shapely_normalize = None


# ==============================================================
# Config
# ==============================================================

# Only used by the __main__ block below when running this file directly
#DEFAULT_INPUT_PATH = Path("../data/KLIC/KLIC_didam_temp4.gpkg")
#DEFAULT_OUTPUT_PATH = Path("../data/KLIC/duplicate_overlap_inspection_v3_fast.gpkg")

#Here, features that do not have to be considered can be removed from this list.
LAYERS_TO_USE = [
    "Waterleiding",
    "ThermischePijpleiding",
    "Rioolleiding",
    "OlieGasChemicalienPijpleiding",
    "Elektriciteitskabel",
    "Overig",
]

# --------------------------------------------------------------
# Detection settings
# --------------------------------------------------------------
FULL_OVERLAP_RATIO = 0.999

# Small tolerance for bbox / length comparison.
GEOMETRY_TOLERANCE = 0.001

# Big speed improvement:
# If False, topologically-equal groups are written as "star edges":
# keeper -> duplicate_1, keeper -> duplicate_2, etc.
#
# This is enough for duplicate grouping/removal.
# Set True only if you really need every pair combination.
OUTPUT_ALL_TOPOLOGICAL_PAIRS = False

# Writing the inspection GeoPackage can be slow and large.
# Use False for fast runs, True for visual inspection runs.
WRITE_INSPECTION_GPKG = False

# --------------------------------------------------------------
# Attribute settings
# --------------------------------------------------------------
# Recommended: include columns that define whether two objects are the same
# asset category.
#
# Important for your HS/MS example:
# make sure the column containing HS/MS is included here.
#
# Columns that do not exist in a layer are skipped.
IMPORTANT_EQUAL_COLUMNS = [
    # General / network owner / type
    "thema",
    "utilityNetworkType",
#    "Eigenaar",

    # Electricity-relevant examples
    "werkVoltage",

    # Pipe/cable properties
    "buisDiameter",
    "material_norm",

]

# If True:
# only features with equal important attributes are compared spatially.
#
# This is much faster and prevents HS/MS from being grouped together,
# provided the HS/MS column is included in IMPORTANT_EQUAL_COLUMNS.
ONLY_COMPARE_WITHIN_ATTRIBUTE_GROUPS = True

# If no important columns are found in a layer:
# - True: compare all valid features in the layer anyway
# - False: skip the layer
COMPARE_LAYER_IF_NO_IMPORTANT_COLUMNS_FOUND = True

IGNORE_ATTRIBUTE_COLUMNS = {
    "fid",
    "FID",
    "id",
    "ID",
    "gml_id",
    "identifier",
    "identificatie",
    "beginLifespanVersion",
    "endLifespanVersion",
    "originalLayer",
}


# ==============================================================
# Helpers
# ==============================================================
def _normalize_attr_value(value):
    if pd.isna(value):
        return None

    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None
        return value

    return value


def _make_attr_key(row, columns):
    """
    Create a hashable key from selected important attributes.
    """
    return tuple(
        _normalize_attr_value(row.get(col))
        for col in columns
    )


def _geom_measure(geom):
    """
    Length for line geometries, area for polygons.
    """
    if geom is None or geom.is_empty:
        return 0.0

    geom_type = geom.geom_type

    if "LineString" in geom_type:
        return geom.length

    if "Polygon" in geom_type:
        return geom.area

    return max(geom.length, geom.area)


def _overlap_measure(geom):
    """
    Length for line overlap, area for polygon overlap.
    Point-only intersections return 0.
    """
    if geom is None or geom.is_empty:
        return 0.0

    return max(geom.length, geom.area)


def _normalized_wkb_hex(geom):
    """
    Stable geometry key for finding identical/topologically equal geometries.

    Shapely normalize helps with reversed line direction and multipart ordering.
    """
    if geom is None or geom.is_empty:
        return None

    try:
        if shapely_normalize is not None:
            return shapely_normalize(geom).wkb_hex

        # Fallback for older Shapely versions.
        if hasattr(geom, "normalize"):
            return geom.normalize().wkb_hex

        return geom.wkb_hex

    except Exception:
        return geom.wkb_hex


def _bounds_equal(bounds1, bounds2, tolerance):
    return (
        abs(bounds1[0] - bounds2[0]) <= tolerance
        and abs(bounds1[1] - bounds2[1]) <= tolerance
        and abs(bounds1[2] - bounds2[2]) <= tolerance
        and abs(bounds1[3] - bounds2[3]) <= tolerance
    )


def _bounds_contained(bounds_small, bounds_big, tolerance):
    """
    Cheap pre-check:
    if a geometry is fully covered by another, its bbox should also be
    contained in the larger bbox.
    """
    return (
        bounds_small[0] >= bounds_big[0] - tolerance
        and bounds_small[1] >= bounds_big[1] - tolerance
        and bounds_small[2] <= bounds_big[2] + tolerance
        and bounds_small[3] <= bounds_big[3] + tolerance
    )


def _nearly_equal(a, b, tolerance):
    return abs(a - b) <= tolerance


def _connected_components(nodes, edges):
    graph = defaultdict(set)

    for node in nodes:
        graph[node]

    for first, second in edges:
        graph[first].add(second)
        graph[second].add(first)

    visited = set()
    components = []

    for node in graph:
        if node in visited:
            continue

        component = []
        queue = deque([node])
        visited.add(node)

        while queue:
            current = queue.popleft()
            component.append(current)

            for neighbour in graph[current]:
                if neighbour not in visited:
                    visited.add(neighbour)
                    queue.append(neighbour)

        components.append(sorted(component))

    return components


class _UnionFind:
    def __init__(self, nodes):
        self.parent = {node: node for node in nodes}

    def find(self, node):
        while self.parent[node] != node:
            self.parent[node] = self.parent[self.parent[node]]
            node = self.parent[node]
        return node

    def union(self, first, second):
        root_first = self.find(first)
        root_second = self.find(second)

        if root_first == root_second:
            return

        if root_first < root_second:
            self.parent[root_second] = root_first
        else:
            self.parent[root_first] = root_second

    def groups(self):
        result = defaultdict(list)

        for node in self.parent:
            result[self.find(node)].append(node)

        return {
            root: sorted(members)
            for root, members in result.items()
        }


def _build_remove_lookup(report):
    """Build per-layer row indices to remove using the notebook's rules."""
    if report.empty:
        return {}

    safe_report = report[
        report["same_attributes"].fillna(False)
        & report["attribute_columns_used"].fillna("").astype(str).str.strip().ne("")
    ].copy()

    remove_lookup = {}

    for layer, layer_df in safe_report.groupby("source_layer", sort=False):
        all_nodes = set()
        component_edges = []
        equality_edges = []
        covered_by = defaultdict(set)

        for _, row in layer_df.iterrows():
            first = int(row["source_row_index_1"])
            second = int(row["source_row_index_2"])
            relation = row["relation"]

            all_nodes.update((first, second))
            component_edges.append((first, second))

            if relation == "topologically_equal":
                equality_edges.append((first, second))
            elif relation == "feature_1_fully_covered_by_feature_2":
                covered_by[first].add(second)
            elif relation == "feature_2_fully_covered_by_feature_1":
                covered_by[second].add(first)

        layer_remove_nodes = set()

        for component in _connected_components(all_nodes, component_edges):
            component_set = set(component)
            equality_union = _UnionFind(component)

            for first, second in equality_edges:
                if first in component_set and second in component_set:
                    equality_union.union(first, second)

            covered_nodes = {
                node
                for node in component
                if covered_by[node] & component_set
            }
            top_nodes = [node for node in component if node not in covered_nodes]
            keep_nodes = set()
            top_nodes_remaining = set(top_nodes)

            for members in equality_union.groups().values():
                members_in_top = sorted(set(members) & top_nodes_remaining)
                if not members_in_top:
                    continue

                keeper = members_in_top[0]
                keep_nodes.add(keeper)
                layer_remove_nodes.update(members_in_top[1:])
                top_nodes_remaining -= set(members_in_top)

            keep_nodes.update(top_nodes_remaining)

            for node in sorted(covered_nodes):
                direct_keepers = [
                    candidate
                    for candidate in sorted(covered_by[node] & component_set)
                    if candidate in keep_nodes
                ]

                if direct_keepers or keep_nodes:
                    layer_remove_nodes.add(node)
                else:
                    keep_nodes.add(node)

        if layer_remove_nodes:
            remove_lookup[layer] = layer_remove_nodes

    return remove_lookup


# ==============================================================
# Public entry point
# ==============================================================
def remove_duplicates(
    input_path: Path,
    output_path: Path,
) -> Path:
    """Remove duplicate/fully-covered geometries and write every input layer.

    output_path is the cleaned GeoPackage location. The CSV report is
    derived from the same path (same folder and stem, with a
    "_material_report.csv" suffix). When enabled, inspection layers are
    written to a sibling GeoPackage with an "_inspection" suffix.

    Import and call this from your own main.py, e.g.:

        from duplicate_overlap_inspection import remove_duplicates
        remove_duplicates(
            Path("data/KLIC_in.gpkg"),
            Path("data/report.gpkg"),
        )
    """
    available_layers = [name for name, _ in pyogrio.list_layers(input_path)]

    all_report_rows = []
    intersection_rows = []
    feature_rows = []

    inspection_output_path = output_path.with_name(
        f"{output_path.stem}_inspection.gpkg"
    )

    if output_path.exists():
        output_path.unlink()

    if WRITE_INSPECTION_GPKG and inspection_output_path.exists():
        inspection_output_path.unlink()

    pair_id = 0
    output_crs = None

    for layer in LAYERS_TO_USE:
        if layer not in available_layers:
            print(f"Layer missing, skipped: {layer}")
            continue

        print(f"\nInspecting layer: {layer}")

        gdf = pyogrio.read_dataframe(
            input_path,
            layer=layer,
            read_geometry=True
        )

        if gdf.empty:
            print("  Empty layer, skipped.")
            continue

        if gdf.geometry.name != "geometry":
            gdf = gdf.rename_geometry("geometry")

        if output_crs is None:
            output_crs = gdf.crs

        # Keep original row number for traceability.
        gdf = gdf.reset_index(drop=False).rename(columns={"index": "_source_row_index"})

        # Filter valid geometries.
        gdf_valid = gdf[
            gdf.geometry.notna() & ~gdf.geometry.is_empty
        ].copy().reset_index(drop=True)

        if gdf_valid.empty:
            print("  No valid geometries, skipped.")
            continue

        gdf_valid["_inspection_index"] = gdf_valid.index

        # Select important columns that exist in this layer.
        existing_important_columns = [
            col for col in IMPORTANT_EQUAL_COLUMNS
            if col in gdf_valid.columns
            and col not in IGNORE_ATTRIBUTE_COLUMNS
        ]

        print(f"  Valid geometries: {len(gdf_valid)}")
        print(f"  Important columns found: {existing_important_columns}")

        if ONLY_COMPARE_WITHIN_ATTRIBUTE_GROUPS:
            if existing_important_columns:
                gdf_valid["_attr_key"] = gdf_valid.apply(
                    lambda row: _make_attr_key(row, existing_important_columns),
                    axis=1
                )
            else:
                if not COMPARE_LAYER_IF_NO_IMPORTANT_COLUMNS_FOUND:
                    print("  No important columns found, skipped.")
                    continue

                print("  No important columns found; comparing whole layer.")
                gdf_valid["_attr_key"] = "all_features"
        else:
            gdf_valid["_attr_key"] = "all_features"

        # Precompute expensive/simple values once.
        gdf_valid["_measure"] = gdf_valid.geometry.apply(_geom_measure)
        gdf_valid["_bounds"] = gdf_valid.geometry.apply(lambda g: g.bounds)
        gdf_valid["_norm_wkb"] = gdf_valid.geometry.apply(_normalized_wkb_hex)

        emitted_pairs = set()
        layer_pair_count = 0

        def emit_pair(
            pos1,
            pos2,
            relation,
            intersection_geom,
            measure_1,
            measure_2,
            intersection_measure,
            overlap_ratio_1,
            overlap_ratio_2,
            overlap_ratio_smaller,
        ):
            nonlocal_pair = None

            # Make sure we never emit the same pair twice.
            pair_key = tuple(sorted((int(pos1), int(pos2))))
            if pair_key in emitted_pairs:
                return False

            emitted_pairs.add(pair_key)

            row1 = gdf_valid.iloc[pos1]
            row2 = gdf_valid.iloc[pos2]

            nonlocal pair_id
            pair_id += 1

            report_row = {
                "pair_id": pair_id,
                "source_layer": layer,
                "feature_index_1": int(row1["_inspection_index"]),
                "feature_index_2": int(row2["_inspection_index"]),
                "source_row_index_1": int(row1["_source_row_index"]),
                "source_row_index_2": int(row2["_source_row_index"]),
                "relation": relation,

                # Because we compare only inside attribute groups.
                # This means selected important attributes are equal.
                "same_attributes": True,
                "attribute_columns_used": ", ".join(existing_important_columns),
                "differing_columns": "",
                "differing_column_count": 0,

                "measure_1": measure_1,
                "measure_2": measure_2,
                "intersection_measure": intersection_measure,
                "overlap_ratio_1": overlap_ratio_1,
                "overlap_ratio_2": overlap_ratio_2,
                "overlap_ratio_smaller": overlap_ratio_smaller,
            }

            all_report_rows.append(report_row)

            if WRITE_INSPECTION_GPKG:
                intersection_rows.append({
                    **report_row,
                    "geometry": intersection_geom,
                })

                feature_rows.append({
                    **report_row,
                    "candidate_side": "feature_1",
                    "source_row_index": int(row1["_source_row_index"]),
                    "feature_index": int(row1["_inspection_index"]),
                    "geometry": row1.geometry,
                })

                feature_rows.append({
                    **report_row,
                    "candidate_side": "feature_2",
                    "source_row_index": int(row2["_source_row_index"]),
                    "feature_index": int(row2["_inspection_index"]),
                    "geometry": row2.geometry,
                })

            return True

        # =====================================================================
        # Process by attribute group
        # =====================================================================

        for attr_key, group_df in gdf_valid.groupby("_attr_key", sort=False):
            if len(group_df) < 2:
                continue

            print(f"  Attribute group size: {len(group_df)}")

            # -------------------------------------------------------------------
            # A. Fast topologically-equal detection by normalized geometry key
            # -------------------------------------------------------------------

            for norm_key, equal_geom_group in group_df.groupby("_norm_wkb", sort=False):
                if norm_key is None:
                    continue

                if len(equal_geom_group) < 2:
                    continue

                positions = list(equal_geom_group.index)

                if OUTPUT_ALL_TOPOLOGICAL_PAIRS:
                    pair_iter = combinations(positions, 2)
                else:
                    # Star edges are enough for clustering:
                    # first feature connects to all others.
                    keeper = positions[0]
                    pair_iter = ((keeper, other) for other in positions[1:])

                for pos1, pos2 in pair_iter:
                    g1 = gdf_valid.at[pos1, "geometry"]
                    m1 = float(gdf_valid.at[pos1, "_measure"])
                    m2 = float(gdf_valid.at[pos2, "_measure"])

                    emitted = emit_pair(
                        pos1=pos1,
                        pos2=pos2,
                        relation="topologically_equal",
                        intersection_geom=g1,
                        measure_1=m1,
                        measure_2=m2,
                        intersection_measure=min(m1, m2),
                        overlap_ratio_1=1.0,
                        overlap_ratio_2=1.0,
                        overlap_ratio_smaller=1.0,
                    )

                    if emitted:
                        layer_pair_count += 1

            # -------------------------------------------------------------------
            # B. Fully-covered detection
            # -------------------------------------------------------------------
            # We still need spatial index + intersection here, but:
            # - only inside equal attribute groups
            # - no partial-overlap reporting
            # - cheap bbox checks before expensive intersection
            # -------------------------------------------------------------------

            sub = group_df.copy()
            sindex = sub.sindex
            sub_positions = sub.index.to_numpy()

            for local_pos1, pos1 in enumerate(sub_positions):
                g1 = gdf_valid.at[pos1, "geometry"]
                m1 = float(gdf_valid.at[pos1, "_measure"])
                b1 = gdf_valid.at[pos1, "_bounds"]
                norm1 = gdf_valid.at[pos1, "_norm_wkb"]

                if m1 <= 0:
                    continue

                candidate_local_positions = list(
                    sindex.query(
                        g1,
                        predicate="intersects"
                    )
                )

                for local_pos2 in candidate_local_positions:
                    pos2 = int(sub_positions[local_pos2])

                    if pos2 <= pos1:
                        continue

                    pair_key = tuple(sorted((int(pos1), int(pos2))))
                    if pair_key in emitted_pairs:
                        continue

                    g2 = gdf_valid.at[pos2, "geometry"]
                    m2 = float(gdf_valid.at[pos2, "_measure"])
                    b2 = gdf_valid.at[pos2, "_bounds"]
                    norm2 = gdf_valid.at[pos2, "_norm_wkb"]

                    if m2 <= 0:
                        continue

                    # Already handled exact normalized equality.
                    if norm1 == norm2:
                        continue

                    # Optional safety catch:
                    # If same measure and same bbox, they may still be
                    # topologically equal but not identical WKB.
                    if _nearly_equal(m1, m2, GEOMETRY_TOLERANCE) and _bounds_equal(
                        b1,
                        b2,
                        GEOMETRY_TOLERANCE,
                    ):
                        try:
                            if g1.equals(g2):
                                emitted = emit_pair(
                                    pos1=pos1,
                                    pos2=pos2,
                                    relation="topologically_equal",
                                    intersection_geom=g1,
                                    measure_1=m1,
                                    measure_2=m2,
                                    intersection_measure=min(m1, m2),
                                    overlap_ratio_1=1.0,
                                    overlap_ratio_2=1.0,
                                    overlap_ratio_smaller=1.0,
                                )

                                if emitted:
                                    layer_pair_count += 1

                                continue

                        except GEOSException:
                            continue

                    # We only care about fully-covered cases.
                    # If measures are effectively equal and not topologically equal,
                    # skip. We are not reporting "fully_overlapping_same_measure".
                    if _nearly_equal(m1, m2, GEOMETRY_TOLERANCE):
                        continue

                    if m1 < m2:
                        small_pos = pos1
                        big_pos = pos2
                        small_geom = g1
                        big_geom = g2
                        small_measure = m1
                        big_measure = m2
                        small_bounds = b1
                        big_bounds = b2
                        relation = "feature_1_fully_covered_by_feature_2"
                    else:
                        small_pos = pos2
                        big_pos = pos1
                        small_geom = g2
                        big_geom = g1
                        small_measure = m2
                        big_measure = m1
                        small_bounds = b2
                        big_bounds = b1
                        relation = "feature_2_fully_covered_by_feature_1"

                    # Cheap bbox containment check before expensive intersection.
                    if not _bounds_contained(
                        small_bounds,
                        big_bounds,
                        GEOMETRY_TOLERANCE,
                    ):
                        continue

                    try:
                        intersection_geom = small_geom.intersection(big_geom)

                        if intersection_geom is None or intersection_geom.is_empty:
                            continue

                        intersection_measure = _overlap_measure(intersection_geom)

                        if intersection_measure <= 0:
                            continue

                        overlap_ratio_smaller = intersection_measure / small_measure

                        if overlap_ratio_smaller < FULL_OVERLAP_RATIO:
                            continue

                        overlap_ratio_1 = intersection_measure / m1
                        overlap_ratio_2 = intersection_measure / m2

                        emitted = emit_pair(
                            pos1=pos1,
                            pos2=pos2,
                            relation=relation,
                            intersection_geom=intersection_geom,
                            measure_1=m1,
                            measure_2=m2,
                            intersection_measure=intersection_measure,
                            overlap_ratio_1=overlap_ratio_1,
                            overlap_ratio_2=overlap_ratio_2,
                            overlap_ratio_smaller=overlap_ratio_smaller,
                        )

                        if emitted:
                            layer_pair_count += 1

                    except GEOSException:
                        continue

        print(f"  Candidate duplicate/coverage links found: {layer_pair_count}")

    # ==================================================================
    # Write reports
    # ==================================================================

    # CSV report lives next to the GeoPackage, same stem, fixed suffix.
    report_csv_path = output_path.with_name(
        f"{output_path.stem}_material_report.csv"
    )

    report = pd.DataFrame(all_report_rows)

    remove_lookup = _build_remove_lookup(report)

    # Rewrite every available layer so layers without duplicates are retained.
    print("\nWriting cleaned GeoPackage")
    count_rows = []

    for layer in available_layers:
        gdf = pyogrio.read_dataframe(
            input_path,
            layer=layer,
            read_geometry=True,
        )

        remove_indices = remove_lookup.get(layer, set())
        gdf_work = gdf.reset_index(drop=True)
        valid_remove_indices = {
            index for index in remove_indices
            if 0 <= index < len(gdf_work)
        }
        gdf_out = gdf_work.loc[
            ~gdf_work.index.isin(valid_remove_indices)
        ].copy()

        pyogrio.write_dataframe(
            gdf_out,
            output_path,
            layer=layer,
            driver="GPKG",
        )

        count_rows.append({
            "source_layer": layer,
            "rows_before": len(gdf_work),
            "rows_removed": len(gdf_work) - len(gdf_out),
            "rows_after": len(gdf_out),
        })

        print(
            f"  {layer}: {len(gdf_work)} -> {len(gdf_out)} "
            f"({len(gdf_work) - len(gdf_out)} removed)"
        )

    removal_counts = pd.DataFrame(count_rows)
    removal_counts_path = output_path.with_name(
        f"{output_path.stem}_removal_counts.csv"
    )
    removal_counts.to_csv(removal_counts_path, index=False, encoding="utf-8-sig")

    print("\nWrote cleaned GeoPackage to:")
    print(output_path)
    print("Wrote removal counts to:")
    print(removal_counts_path)

    if report.empty:
        print("\nNo duplicate or fully-covered candidate links found.")
    else:
        report.to_csv(
            report_csv_path,
            index=False,
            encoding="utf-8-sig"
        )

        print("\nWrote CSV report to:")
        print(report_csv_path)

        if WRITE_INSPECTION_GPKG:
            intersections_gdf = gpd.GeoDataFrame(
                intersection_rows,
                geometry="geometry",
                crs=output_crs
            )

            features_gdf = gpd.GeoDataFrame(
                feature_rows,
                geometry="geometry",
                crs=output_crs
            )

            pyogrio.write_dataframe(
                intersections_gdf,
                inspection_output_path,
                layer="overlap_intersections",
                driver="GPKG",
                geometry_type="Unknown"
            )

            pyogrio.write_dataframe(
                features_gdf,
                inspection_output_path,
                layer="candidate_original_features",
                driver="GPKG",
                geometry_type="Unknown"
            )

            print("Wrote GeoPackage inspection file to:")
            print(inspection_output_path)

        print("\nSummary by relation:")
        print(
            report
            .groupby(["source_layer", "relation"], as_index=False)
            .size()
            .sort_values(["source_layer", "relation"])
        )

        print("\nSummary by relation and attribute equality:")
        print(
            report
            .groupby(["source_layer", "relation", "same_attributes"], as_index=False)
            .size()
            .sort_values(["source_layer", "relation", "same_attributes"])
        )

    return report_csv_path


#if __name__ == "__main__":
#    remove_duplicates(
#        DEFAULT_INPUT_PATH,
#        DEFAULT_OUTPUT_PATH,
#    )