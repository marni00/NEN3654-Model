# Script 18
"""Assign run-specific values to normalized relationship tables."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

from .relational_tables import read_table, write_table

HV_LAYER = "proposed_hv_parts_with_runid"
CABLE_LAYER_CANDIDATES = [
    "electricity_cable_parts_with_run_id",
    "electricity_cable_parts_with_runid",
]
INFLUENCED_LAYER = "influenced_parts_with_runid_distance"
OBSERVATION_TABLE = "endpoint_distance_observations"
RUN_RELATIONSHIP_TABLE = "run_relationships"
RUN_MEMBERSHIP_TABLE = "run_memberships"


def _layers(path):
    return {row[0] for row in pyogrio.list_layers(path)}


def assign_correct_distance_to_unique_run(
    source_data_path,
    distance_lines_path,
    output_data_path,
):
    source_data_path = Path(source_data_path)
    distance_lines_path = Path(distance_lines_path)
    output_data_path = Path(output_data_path)

    available = _layers(source_data_path)
    cable_layer = next(
        (
            candidate
            for candidate in CABLE_LAYER_CANDIDATES
            if candidate in available
        ),
        None,
    )

    relationships = read_table(
        source_data_path,
        RUN_RELATIONSHIP_TABLE,
    )
    memberships = read_table(
        source_data_path,
        RUN_MEMBERSHIP_TABLE,
    )
    observations = read_table(
        distance_lines_path,
        OBSERVATION_TABLE,
    )

    for frame in [relationships, memberships]:
        frame["run_id"] = pd.to_numeric(
            frame["run_id"],
            errors="coerce",
        ).astype("Int64")
        frame["influenced_run_id"] = (
            frame["influenced_run_id"].astype(str).str.strip()
        )

    reference_frames = []
    for reference_type, layer_name in [
        ("hv", HV_LAYER),
        ("cable", cable_layer),
    ]:
        if layer_name is None or layer_name not in available:
            continue
        gdf = gpd.read_file(
            source_data_path,
            layer=layer_name,
            columns=[
                "feature_id",
                "part_no",
                "run_id",
                "influencer_class",
            ],
        )
        gdf["reference_type"] = reference_type
        reference_frames.append(gdf)

    reference_lookup = pd.concat(reference_frames, ignore_index=True)
    for column in ["feature_id", "part_no", "run_id"]:
        reference_lookup[column] = pd.to_numeric(
            reference_lookup[column],
            errors="coerce",
        ).astype("Int64")

    classes = (
        reference_lookup[
            ["reference_type", "run_id", "influencer_class"]
        ]
        .dropna()
        .drop_duplicates()
    )
    relationships = relationships.merge(
        classes,
        on=["reference_type", "run_id"],
        how="left",
        validate="many_to_one",
    )

    observation_runs = observations.merge(
        reference_lookup[
            [
                "reference_type",
                "feature_id",
                "part_no",
                "run_id",
            ]
        ].rename(
            columns={
                "feature_id": "reference_feature_id",
                "part_no": "reference_part_no",
            }
        ),
        on=[
            "reference_type",
            "reference_feature_id",
            "reference_part_no",
        ],
        how="inner",
    )

    observation_memberships = observation_runs.merge(
        memberships[
            [
                "source_layer",
                "feature_id",
                "part_no",
                "run_id",
                "influenced_run_id",
            ]
        ].rename(
            columns={
                "source_layer": "target_layer",
                "feature_id": "target_feature_id",
                "part_no": "target_part_no",
            }
        ),
        on=[
            "target_layer",
            "target_feature_id",
            "target_part_no",
            "run_id",
        ],
        how="inner",
    )

    target_distances = (
        observation_memberships.groupby(
            [
                "target_layer",
                "target_feature_id",
                "target_part_no",
                "run_id",
                "influenced_run_id",
            ],
            as_index=False,
        )
        .agg(
            min_distance=("distance", "min"),
            max_distance=("distance", "max"),
        )
        .rename(
            columns={
                "target_layer": "source_layer",
                "target_feature_id": "feature_id",
                "target_part_no": "part_no",
            }
        )
    )

    memberships = memberships.drop(
        columns=["min_distance", "max_distance"],
        errors="ignore",
    ).merge(
        target_distances,
        on=[
            "source_layer",
            "feature_id",
            "part_no",
            "run_id",
            "influenced_run_id",
        ],
        how="left",
    )

    reference_distances = (
        observation_memberships.groupby(
            [
                "reference_type",
                "reference_feature_id",
                "run_id",
                "influenced_run_id",
            ],
            as_index=False,
        )
        .agg(
            min_distance=("distance", "min"),
            max_distance=("distance", "max"),
        )
    )

    relationships = relationships.drop(
        columns=["min_distance", "max_distance"],
        errors="ignore",
    ).merge(
        reference_distances,
        on=[
            "reference_type",
            "reference_feature_id",
            "run_id",
            "influenced_run_id",
        ],
        how="left",
    )

    output_data_path.unlink(missing_ok=True)
    first_write = True
    for layer_name in [HV_LAYER, cable_layer, INFLUENCED_LAYER, "Mast"]:
        if layer_name is None or layer_name not in available:
            continue
        gdf = gpd.read_file(source_data_path, layer=layer_name)
        gdf.to_file(
            output_data_path,
            layer=layer_name,
            driver="GPKG",
            mode="w" if first_write else "a",
        )
        first_write = False

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
        f"Saved {len(relationships):,} normalized run results and "
        f"{len(memberships):,} part memberships."
    )
    return output_data_path
