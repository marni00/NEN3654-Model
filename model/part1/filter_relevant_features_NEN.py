#Script 8

from pathlib import Path
import warnings
import sqlite3

import geopandas as gpd
import pandas as pd
import numpy as np
from shapely.geometry import GeometryCollection


# ============================================================
# CONFIG
# ============================================================

INFLUENCER_LAYERS = [
    "proposed_highvoltage",
    "Elektriciteitskabel",
]

SKIP_FILTER_LAYERS = {
    "Mast",
    }

FILTER_ELECTRICITEITSKABEL_ITSELF = True
WRITE_EMPTY_LAYERS = False

GPD_ENGINE = "pyogrio"

TARGET_CRS = "EPSG:28992"

VERTICAL_COL = "verticalePositie"
VOLTAGE_COL = "werkVoltage"

BUFFER_UG_GT_24KV_M = 850.0
BUFFER_UG_LE_24KV_M = 550.0
BUFFER_AG_M = 1610.0

MIN_LENGTH_UG_GT_24KV_M = 20.0
MIN_LENGTH_UG_LE_24KV_M = 350.0
MIN_LENGTH_AG_M = 10.0

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def list_gpkg_layers(gpkg_path: Path) -> list[str]:
    with sqlite3.connect(gpkg_path) as conn:
        rows = conn.execute(
            """
            SELECT table_name
            FROM gpkg_contents
            WHERE data_type = 'features'
            ORDER BY table_name
            """
        ).fetchall()

    return [row[0] for row in rows]


def read_layer(gpkg_path: Path, layer_name: str) -> gpd.GeoDataFrame:
    return gpd.read_file(
        gpkg_path,
        layer=layer_name,
        engine=GPD_ENGINE,
    )


def write_layer(gdf: gpd.GeoDataFrame, gpkg_path: Path, layer_name: str):
    gdf.to_file(
        gpkg_path,
        layer=layer_name,
        driver="GPKG",
        engine=GPD_ENGINE,
    )


def ensure_analysis_crs(gdf: gpd.GeoDataFrame, layer_name: str) -> gpd.GeoDataFrame:
    if gdf.crs is None:
        warnings.warn(
            f"Layer '{layer_name}' has no CRS. Assuming {TARGET_CRS}."
        )
        return gdf.set_crs(TARGET_CRS, allow_override=True)

    if gdf.crs.to_string() != TARGET_CRS:
        return gdf.to_crs(TARGET_CRS)

    return gdf


def dissolve_buffer(geometries: gpd.GeoSeries, distance_m: float):
    geometries = geometries[geometries.notna() & ~geometries.is_empty]

    if geometries.empty:
        return GeometryCollection()

    buffered = geometries.buffer(distance_m, cap_style="round")

    try:
        return buffered.union_all()
    except AttributeError:
        return buffered.unary_union


def intersects_union(gdf: gpd.GeoDataFrame, union_geom) -> pd.Series:
    result = pd.Series(False, index=gdf.index)

    if gdf.empty or union_geom is None or union_geom.is_empty:
        return result

    valid = gdf.geometry.notna() & ~gdf.geometry.is_empty

    if valid.any():
        result.loc[valid] = gdf.loc[valid, "geometry"].intersects(union_geom)

    return result


def safe_write_layer(gdf: gpd.GeoDataFrame, output_gpkg: Path, layer_name: str):
    if gdf.empty and not WRITE_EMPTY_LAYERS:
        print(f"  skipped empty output layer: {layer_name}")
        return

    write_layer(gdf, output_gpkg, layer_name)


# ============================================================
# MAIN SCRIPT
# ============================================================

def filter_relevant_features_NEN(
    input_path: Path,
    output_path: Path,
) -> Path:

    if not input_path .exists():
        raise FileNotFoundError(f"Input GeoPackage does not exist: {input_path}")

    if output_path.exists():
        output_path.unlink()

    layers = list_gpkg_layers(input_path)

    available_influencer_layers = [
        layer for layer in INFLUENCER_LAYERS
        if layer in layers
    ]

    missing_influencer_layers = [
        layer for layer in INFLUENCER_LAYERS
        if layer not in layers
    ]

    if missing_influencer_layers:
        warnings.warn(
            "The following influencer layers are missing and will be skipped: "
            f"{missing_influencer_layers}"
        )

    if not available_influencer_layers:
        raise ValueError(
            "None of the required influencer layers were found.\n"
            f"Expected at least one of: {INFLUENCER_LAYERS}\n"
            f"Available layers: {layers}"
        )

    print("============================================================")
    print("NEN3654 inductive influence filtering")
    print("============================================================")
    print(f"Input : {input_path}")
    print(f"Output: {output_path}")
    print()

    # ------------------------------------------------------------
    # Read influencer source layers
    # ------------------------------------------------------------

    influencer_gdfs = []

    print("Using influencer source layers:")

    for influencer_layer in available_influencer_layers:
        influencer_gdf = read_layer(input_path, influencer_layer)
        influencer_gdf = ensure_analysis_crs(influencer_gdf, influencer_layer)

        influencer_gdfs.append(influencer_gdf)

        print(f"  {influencer_layer}: {len(influencer_gdf)} features")

    influencers = gpd.GeoDataFrame(
        pd.concat(influencer_gdfs, ignore_index=True),
        crs=TARGET_CRS,
    )

    print()
    print(f"Combined influencer features: {len(influencers)}")
    print(f"  vertical position column: {VERTICAL_COL}")
    print(f"  voltage column          : {VOLTAGE_COL}")
    print()

    vertical = influencers[VERTICAL_COL].astype("string").str.strip().str.lower()
    voltage = influencers[VOLTAGE_COL]

    source_ug_gt24kv = (vertical == "underground") & (voltage > 24000)
    source_ug_le24kv = (vertical == "underground") & (voltage <= 24000)
    source_ag = vertical == "aboveground"

    print("Influencer source classification:")
    print(f"  underground > 24 kV : {int(source_ug_gt24kv.sum())}")
    print(f"  underground <= 24 kV: {int(source_ug_le24kv.sum())}")
    print(f"  aboveground         : {int(source_ag.sum())}")
    print(f"  unclassified        : {int((~source_ug_gt24kv & ~source_ug_le24kv & ~source_ag).sum())}")
    print()

    # ------------------------------------------------------------
    # Build buffers
    # ------------------------------------------------------------

    print("Building dissolved influence buffers...")

    buffer_ug_gt24kv = dissolve_buffer(
        influencers.loc[source_ug_gt24kv, "geometry"],
        BUFFER_UG_GT_24KV_M,
    )

    buffer_ug_le24kv = dissolve_buffer(
        influencers.loc[source_ug_le24kv, "geometry"],
        BUFFER_UG_LE_24KV_M,
    )

    buffer_ag = dissolve_buffer(
        influencers.loc[source_ag, "geometry"],
        BUFFER_AG_M,
    )

    print("  done")
    print()

    # ------------------------------------------------------------
    # Process layers
    # ------------------------------------------------------------

    summary_rows = []

    for layer in layers:
        print("------------------------------------------------------------")
        print(f"Processing layer: {layer}")

        gdf = read_layer(input_path, layer)
        gdf = ensure_analysis_crs(gdf, layer)

        input_count = len(gdf)

        if gdf.empty:
            safe_write_layer(gdf, output_path, layer)

            summary_rows.append(
                {
                    "layer": layer,
                    "mode": "empty",
                    "input": input_count,
                    "kept": 0,
                    "removed": 0,
                }
            )

            print("  input layer is empty")
            continue

        if layer in SKIP_FILTER_LAYERS:
            safe_write_layer(gdf, output_path, layer)

            summary_rows.append(
                {
                    "layer": layer,
                    "mode": "copied_unfiltered",
                    "input": input_count,
                    "kept": input_count,
                    "removed": 0,
                }
            )

            print(f"  copied unchanged: {input_count}")
            continue

        if layer in available_influencer_layers:
            gdf = gdf.copy()

            vertical_layer = (
                gdf[VERTICAL_COL]
                .astype("string")
                .str.strip()
                .str.lower()
            )

            voltage_layer = pd.to_numeric(
                gdf[VOLTAGE_COL],
                errors="coerce",
            )

            is_ug_gt24kv = (
                (vertical_layer == "underground")
                & (voltage_layer > 24000)
            ).fillna(False)

            is_ug_le24kv = (
                (vertical_layer == "underground")
                & (voltage_layer <= 24000)
            ).fillna(False)

            is_ag = (
                vertical_layer == "aboveground"
            ).fillna(False)

            # Influencer features belong to one inductive theme.
            gdf["inductive_theme"] = np.select(
                [
                    is_ug_gt24kv.to_numpy(dtype=bool),
                    is_ug_le24kv.to_numpy(dtype=bool),
                    is_ag.to_numpy(dtype=bool),
                ],
                [
                    "inductive_gt24kv",
                    "inductive_le24kv",
                    "inductive_ag",
                ],
                default="unclassified",
            )

            # Influencer layers are source layers and must not be
            # filtered against their own influence buffers.
            filtered = gdf.copy()

            safe_write_layer(filtered, output_path, layer)

            summary_rows.append(
                {
                    "layer": layer,
                    "mode": "source_labelled_not_self_filtered",
                    "input": input_count,
                    "kept": input_count,
                    "removed": 0,
                }
            )

            print(
                f"  source layer labelled, not self-filtered: "
                f"{input_count}"
            )
            continue

        # --------------------------------------------------------
        # Apply filtering
        # --------------------------------------------------------

        gdf = gdf.copy()

        feature_length_m = gdf.geometry.length

        near_ug_gt24kv = intersects_union(gdf, buffer_ug_gt24kv)
        near_ug_le24kv = intersects_union(gdf, buffer_ug_le24kv)
        near_ag = intersects_union(gdf, buffer_ag)

        # pass = this rule allows removal
        # no-pass = this rule keeps the feature

        keep_by_ug_gt24kv = near_ug_gt24kv & (
            feature_length_m >= MIN_LENGTH_UG_GT_24KV_M
        )

        keep_by_ug_le24kv = near_ug_le24kv & (
            feature_length_m >= MIN_LENGTH_UG_LE_24KV_M
        )

        keep_by_ag = near_ag & (
            feature_length_m >= MIN_LENGTH_AG_M
        )

        gdf["inductive_ug_gt24kv"] = np.where(
            keep_by_ug_gt24kv,
            "no-pass",
            "pass",
        )

        gdf["inductive_ug_le24kv"] = np.where(
            keep_by_ug_le24kv,
            "no-pass",
            "pass",
        )

        gdf["inductive_ag"] = np.where(
            keep_by_ag,
            "no-pass",
            "pass",
        )

        remove_feature = (
            (gdf["inductive_ug_gt24kv"] == "pass")
            & (gdf["inductive_ug_le24kv"] == "pass")
            & (gdf["inductive_ag"] == "pass")
        )

        filtered = gdf.loc[~remove_feature].copy()

        safe_write_layer(filtered, output_path, layer)

        kept_count = len(filtered)
        removed_count = input_count - kept_count

        summary_rows.append(
            {
                "layer": layer,
                "mode": "filtered",
                "input": input_count,
                "kept": kept_count,
                "removed": removed_count,
            }
        )

        print(f"  input  : {input_count}")
        print(f"  kept   : {kept_count}")
        print(f"  removed: {removed_count}")

    print()
    print("============================================================")
    print("SUMMARY")
    print("============================================================")

    summary = pd.DataFrame(summary_rows)
    print(summary.to_string(index=False))

    print()
    print("Finished.")
    print(f"Output written to: {output_path}")

    return output_path

#if __name__ == "__main__":
#    filter_relevant_features_NEN()