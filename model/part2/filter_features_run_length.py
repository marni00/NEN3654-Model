#Script 14
from pathlib import Path
from collections.abc import Mapping

import geopandas as gpd
import pandas as pd
from pyogrio.errors import DataLayerError


THRESHOLDS = {
    "ag": 10.0,
    "ug_le24kv": 350.0,
    "ug_gt24kv": 20.0,
}

HV_LAYER = "proposed_hv_parts_with_runid"
CABLE_LAYER = "electricity_cable_parts_with_runid"
INFLUENCED_LAYER = "influenced_parts_with_runid_distance"


def normalise_run_id(value) -> str:
    """Make run_id values comparable between CSV and GeoPackage."""

    if pd.isna(value):
        return ""

    try:
        number = float(value)

        if number.is_integer():
            return str(int(number))

    except (TypeError, ValueError):
        pass

    return str(value).strip()


def normalise_influenced_run_id(value) -> str:
    """
    Keep influenced_run_id as plain text.

    Never go through float here: "4.10" and "4.1" are different
    influenced_run_ids, but they are the same float.
    """

    if pd.isna(value):
        return ""

    return str(value).strip()


def get_run_classes(
    gdf: gpd.GeoDataFrame,
    layer_name: str,
    thresholds: Mapping[str, float],
) -> pd.DataFrame:
    """Return one influencer class per run_id."""

    temp = gdf[
        [
            "_run_id_key",
            "influencer_class",
        ]
    ].copy()

    temp["influencer_class"] = (
        temp["influencer_class"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    temp = temp[
        temp["_run_id_key"].ne("")
        & temp["influencer_class"].isin(thresholds)
    ]

    class_counts = (
        temp.drop_duplicates()
        .groupby("_run_id_key")["influencer_class"]
        .nunique()
    )

    problematic = class_counts[class_counts > 1]

    if not problematic.empty:
        raise ValueError(
            f"{layer_name} contains run_ids with multiple "
            f"influencer_class values: "
            f"{problematic.index.tolist()[:20]}"
        )

    return temp[
        [
            "_run_id_key",
            "influencer_class",
        ]
    ].drop_duplicates()


def report_layer(name, before, after) -> None:
    print()
    print(f"{name}:")

    if before is None:
        print("  Not present")
        return

    print(f"  Before:  {len(before):,}")
    print(f"  After:   {len(after):,}")
    print(f"  Removed: {len(before) - len(after):,}")



def _layer_exists_for_filter(path, layer_name):
    try:
        gpd.read_file(path, layer=layer_name, rows=1)
        return True
    except Exception:
        return False

def filter_features_run_length(
    input_path: Path,
    output_path: Path,
    pair_table_path: Path | None = None,
) -> Path:
    """Remove short run memberships while retaining each geometry once."""
    input_path = Path(input_path)
    output_path = Path(output_path)

    if pair_table_path is None:
        pair_table_path = input_path.with_name("influenced_runid_links.csv")
    else:
        pair_table_path = Path(pair_table_path)

    output_pair_table_path = output_path.with_name(
        "influenced_runid_links_no_short_runlength.csv"
    )

    proposed_hv = (
        gpd.read_file(input_path, layer=HV_LAYER)
        if _layer_exists_for_filter(input_path, HV_LAYER)
        else None
    )
    electricity_cable = (
        gpd.read_file(input_path, layer=CABLE_LAYER)
        if _layer_exists_for_filter(input_path, CABLE_LAYER)
        else None
    )
    influenced = gpd.read_file(input_path, layer=INFLUENCED_LAYER)
    links = pd.read_csv(
        pair_table_path,
        dtype={"influenced_run_id": str},
    )

    required = {
        "source_layer",
        "feature_id",
        "part_no",
        "run_id",
        "influenced_run_id",
        "influenced_component_length_m",
    }
    missing = required - set(links.columns)
    if missing:
        raise ValueError(
            f"Run-membership table is missing columns: {sorted(missing)}"
        )

    links["_run_id_key"] = links["run_id"].apply(normalise_run_id)
    links["_irun_key"] = links["influenced_run_id"].apply(
        normalise_influenced_run_id
    )
    links["influenced_component_length_m"] = pd.to_numeric(
        links["influenced_component_length_m"],
        errors="coerce",
    )

    run_class_tables = []
    for gdf, layer_name in [
        (proposed_hv, HV_LAYER),
        (electricity_cable, CABLE_LAYER),
    ]:
        if gdf is None:
            continue
        gdf["_run_id_key"] = gdf["run_id"].apply(normalise_run_id)
        run_class_tables.append(
            get_run_classes(gdf, layer_name, THRESHOLDS)
        )

    run_classes = pd.concat(run_class_tables, ignore_index=True)
    run_classes = run_classes.drop_duplicates("_run_id_key")

    summary = (
        links[links["_irun_key"].ne("")]
        .groupby("_irun_key", as_index=False)
        .agg(
            _run_id_key=("_run_id_key", "first"),
            n_run_ids=("_run_id_key", "nunique"),
            influenced_length_m=("influenced_component_length_m", "max"),
        )
    )
    if (summary["n_run_ids"] > 1).any():
        raise ValueError("An influenced_run_id is linked to multiple run_ids.")

    summary = summary.drop(columns="n_run_ids").merge(
        run_classes,
        on="_run_id_key",
        how="left",
    )
    summary["threshold_m"] = summary["influencer_class"].map(THRESHOLDS)
    summary["remove"] = (
        summary["threshold_m"].notna()
        & (summary["influenced_length_m"] < summary["threshold_m"])
    )

    iruns_to_remove = set(summary.loc[summary["remove"], "_irun_key"])
    runs_with_survivors = set(
        summary.loc[~summary["remove"], "_run_id_key"]
    )
    runs_to_remove = set(summary["_run_id_key"]) - runs_with_survivors

    links_clean = links[
        ~links["_irun_key"].isin(iruns_to_remove)
    ].drop(columns=["_run_id_key", "_irun_key"])

    def clean_reference(gdf):
        if gdf is None:
            return None
        keys = gdf["run_id"].apply(normalise_run_id)
        return gdf.loc[~keys.isin(runs_to_remove)].copy()

    proposed_hv_clean = clean_reference(proposed_hv)
    electricity_cable_clean = clean_reference(electricity_cable)

    surviving_parts = links_clean[
        ["source_layer", "feature_id", "part_no"]
    ].drop_duplicates()
    influenced_clean = influenced.merge(
        surviving_parts,
        on=["source_layer", "feature_id", "part_no"],
        how="inner",
        validate="one_to_one",
    )

    output_path.unlink(missing_ok=True)
    first_write = True
    for gdf, layer_name in [
        (proposed_hv_clean, HV_LAYER),
        (electricity_cable_clean, CABLE_LAYER),
        (influenced_clean, INFLUENCED_LAYER),
    ]:
        if gdf is None or gdf.empty:
            continue
        gdf.to_file(
            output_path,
            layer=layer_name,
            driver="GPKG",
            mode="w" if first_write else "a",
        )
        first_write = False

    links_clean.to_csv(output_pair_table_path, index=False)
    print(f"Saved filtered memberships: {output_pair_table_path}")
    return output_path

