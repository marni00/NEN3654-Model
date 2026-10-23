# Script 17
"""Store A/L values in normalized relationships without duplicating geometry."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

from .relational_tables import write_table

HV_LAYER = "proposed_hv_parts_with_runid"
CABLE_LAYER_CANDIDATES = [
    "electricity_cable_parts_with_run_id",
    "electricity_cable_parts_with_runid",
]
INFLUENCED_LAYER = "influenced_parts_with_runid_distance"
RUN_RELATIONSHIP_TABLE = "run_relationships"
RUN_MEMBERSHIP_TABLE = "run_memberships"


def _layers(path):
    return {row[0] for row in pyogrio.list_layers(path)}


def _read_al(path, reference_type):
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, dtype={"influenced_run_id": str})
    required = {
        "run_id",
        "influenced_run_id",
        "reference_feature_id",
        "a",
        "l",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
    frame = frame[
        [
            "run_id",
            "influenced_run_id",
            "reference_feature_id",
            "a",
            "l",
        ]
    ].copy()
    frame["reference_type"] = reference_type
    frame["run_id"] = pd.to_numeric(frame["run_id"], errors="coerce")
    frame["reference_feature_id"] = pd.to_numeric(
        frame["reference_feature_id"],
        errors="coerce",
    )
    frame["a"] = pd.to_numeric(frame["a"], errors="coerce")
    frame["l"] = pd.to_numeric(frame["l"], errors="coerce")
    frame["influenced_run_id"] = (
        frame["influenced_run_id"].astype(str).str.strip()
    )
    return frame.dropna(
        subset=[
            "run_id",
            "reference_feature_id",
            "influenced_run_id",
            "a",
            "l",
        ]
    ).drop_duplicates()


def assign_distance_length_to_run(
    source_data_path,
    proposed_hv_csv_path,
    electricity_cable_csv_path,
    output_data_path,
    support_data_path=None,
):
    source_data_path = Path(source_data_path)
    output_data_path = Path(output_data_path)
    support_data_path = (
        Path(support_data_path)
        if support_data_path is not None
        else source_data_path
    )

    available = _layers(source_data_path)
    cable_layer = next(
        (
            candidate
            for candidate in CABLE_LAYER_CANDIDATES
            if candidate in available
        ),
        None,
    )

    output_data_path.unlink(missing_ok=True)
    first_write = True
    copied = {}
    for layer_name in [HV_LAYER, cable_layer, INFLUENCED_LAYER]:
        if layer_name is None or layer_name not in available:
            continue
        gdf = gpd.read_file(source_data_path, layer=layer_name)
        for column in ["a", "l", "influenced_run_id"]:
            if column in gdf.columns:
                gdf = gdf.drop(columns=column)
        gdf.to_file(
            output_data_path,
            layer=layer_name,
            driver="GPKG",
            mode="w" if first_write else "a",
        )
        first_write = False
        copied[layer_name] = gdf

    support_layers = _layers(support_data_path)
    if "Mast" in support_layers:
        mast = gpd.read_file(support_data_path, layer="Mast")
        mast.to_file(
            output_data_path,
            layer="Mast",
            driver="GPKG",
            mode="w" if first_write else "a",
        )
        first_write = False

    relationships = pd.concat(
        [
            _read_al(Path(proposed_hv_csv_path), "hv"),
            _read_al(Path(electricity_cable_csv_path), "cable"),
        ],
        ignore_index=True,
    )
    relationships["run_id"] = relationships["run_id"].astype(int)
    relationships["reference_feature_id"] = relationships[
        "reference_feature_id"
    ].astype(int)

    membership_path = source_data_path.with_name(
        "influenced_runid_links_no_short_runlength.csv"
    )
    memberships = pd.read_csv(
        membership_path,
        dtype={"influenced_run_id": str},
    )

    write_table(
        output_data_path,
        RUN_RELATIONSHIP_TABLE,
        relationships,
    )
    write_table(
        output_data_path,
        RUN_MEMBERSHIP_TABLE,
        memberships,
    )
    print(
        f"Stored {len(relationships):,} A/L relationships without "
        "duplicating influencer geometry."
    )
    return {
        "output_data_path": output_data_path,
        "relationships": relationships,
    }
