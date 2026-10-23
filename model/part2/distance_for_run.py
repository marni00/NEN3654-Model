# Script 15
"""Create numeric reference sections between consecutive observations."""

from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

from .relational_tables import read_table

HV_LAYER = "proposed_hv_parts_with_runid"
CABLE_LAYER = "electricity_cable_parts_with_runid"
OBSERVATION_TABLE = "endpoint_distance_observations"

SEGMENT_COLUMNS = [
    "reference_dataset",
    "reference_feature_id",
    "reference_part_no",
    "run_id",
    "influenced_run_id",
    "seg_idx",
    "from_measure",
    "to_measure",
    "seg_length",
    "dist_from",
    "dist_to",
    "from_target_feature_id",
    "to_target_feature_id",
    "from_target_part_no",
    "to_target_part_no",
    "from_endpoint",
    "to_endpoint",
]


def _list_layers(path):
    return {row[0] for row in pyogrio.list_layers(path)}


def _clean_ids(dataframe, columns):
    result = dataframe.copy()
    for column in columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=columns).copy()
    for column in columns:
        result[column] = result[column].astype(int)
    return result


def _write_empty_segments(reference_name, output_path, reason):
    result = pd.DataFrame(columns=SEGMENT_COLUMNS)
    result.to_csv(output_path, index=False)
    print(f"Skipped {reference_name} ({reason}) -> {output_path}")
    return result


def _create_reference_segments(
    reference_name,
    reference_runs,
    observations,
    target_lookup,
    output_path,
):
    run_lookup = reference_runs[
        ["feature_id", "part_no", "run_id"]
    ].drop_duplicates().rename(
        columns={
            "feature_id": "reference_feature_id",
            "part_no": "reference_part_no",
        }
    )
    observations = observations.merge(
        run_lookup,
        on=["reference_feature_id", "reference_part_no"],
        how="inner",
    )
    observations = observations.merge(
        target_lookup,
        on=[
            "target_layer",
            "target_feature_id",
            "target_part_no",
            "run_id",
        ],
        how="inner",
    )

    projected = observations.rename(
        columns={
            "reference_measure": "measure",
            "endpoint_node_type": "endpoint",
        }
    ).copy()
    projected["influenced_run_id"] = (
        projected["influenced_run_id"].astype(str)
    )

    group_columns = [
        "reference_feature_id",
        "reference_part_no",
        "run_id",
        "influenced_run_id",
    ]
    segments = []
    if not projected.empty:
        projected["measure_round"] = projected["measure"].round(6)
        projected = projected.sort_values(
            group_columns + ["measure_round", "distance"]
        )
        projected = projected.drop_duplicates(
            group_columns + ["measure_round"]
        )

        for group_key, group in projected.groupby(group_columns):
            group = group.sort_values("measure").reset_index(drop=True)
            for index in range(len(group) - 1):
                first = group.iloc[index]
                second = group.iloc[index + 1]
                if second.measure <= first.measure:
                    continue
                same_part = (
                    first.target_feature_id == second.target_feature_id
                    and first.target_part_no == second.target_part_no
                )
                same_endpoint = (
                    pd.notna(first.endpoint)
                    and pd.notna(second.endpoint)
                    and first.endpoint == second.endpoint
                )
                if same_part and same_endpoint:
                    continue

                segments.append(
                    {
                        "reference_dataset": reference_name,
                        "reference_feature_id": int(group_key[0]),
                        "reference_part_no": int(group_key[1]),
                        "run_id": int(group_key[2]),
                        "influenced_run_id": str(group_key[3]),
                        "seg_idx": index,
                        "from_measure": float(first.measure),
                        "to_measure": float(second.measure),
                        "seg_length": float(
                            second.measure - first.measure
                        ),
                        "dist_from": float(first.distance),
                        "dist_to": float(second.distance),
                        "from_target_feature_id": int(
                            first.target_feature_id
                        ),
                        "to_target_feature_id": int(
                            second.target_feature_id
                        ),
                        "from_target_part_no": int(first.target_part_no),
                        "to_target_part_no": int(second.target_part_no),
                        "from_endpoint": first.endpoint,
                        "to_endpoint": second.endpoint,
                    }
                )

    result = pd.DataFrame(segments, columns=SEGMENT_COLUMNS)
    result.to_csv(output_path, index=False)
    print(f"Exported {len(result):,} numeric sections -> {output_path}")
    return result


def create_run_exports(input_path, distance_lines_path, output_path):
    input_path = Path(input_path)
    distance_lines_path = Path(distance_lines_path)
    output_dir = Path(output_path).parent
    output_dir.mkdir(parents=True, exist_ok=True)

    layers = _list_layers(input_path)
    observations = read_table(
        distance_lines_path,
        OBSERVATION_TABLE,
    )
    observations = _clean_ids(
        observations,
        [
            "target_feature_id",
            "target_part_no",
            "reference_feature_id",
            "reference_part_no",
        ],
    )
    observations["distance"] = pd.to_numeric(
        observations["distance"],
        errors="coerce",
    )
    observations["reference_measure"] = pd.to_numeric(
        observations["reference_measure"],
        errors="coerce",
    )
    observations = observations.dropna(
        subset=["distance", "reference_measure"]
    )

    links_path = input_path.with_name(
        "influenced_runid_links_no_short_runlength.csv"
    )
    links = pd.read_csv(
        links_path,
        dtype={"influenced_run_id": str},
    )
    links = _clean_ids(
        links,
        ["feature_id", "part_no", "run_id"],
    )
    links["source_layer"] = links["source_layer"].astype(str).str.strip()
    links["influenced_run_id"] = (
        links["influenced_run_id"].astype(str).str.strip()
    )
    target_lookup = links[
        [
            "source_layer",
            "feature_id",
            "part_no",
            "run_id",
            "influenced_run_id",
        ]
    ].drop_duplicates().rename(
        columns={
            "source_layer": "target_layer",
            "feature_id": "target_feature_id",
            "part_no": "target_part_no",
        }
    )

    outputs = [
        (
            "proposed_hv",
            HV_LAYER,
            output_dir / "proposed_hv_segments_between_distance_lines.csv",
        ),
        (
            "elektriciteitskabel",
            CABLE_LAYER,
            output_dir / "electriciteitskabel_segments_between_distance_lines.csv",
        ),
    ]
    last_result = None
    for reference_name, layer_name, output_csv in outputs:
        if layer_name not in layers:
            last_result = _write_empty_segments(
                reference_name,
                output_csv,
                "reference layer missing",
            )
            continue
        runs = gpd.read_file(
            input_path,
            layer=layer_name,
            columns=["feature_id", "part_no", "run_id"],
            use_arrow=True,
        )
        runs = _clean_ids(runs, ["feature_id", "part_no", "run_id"])
        last_result = _create_reference_segments(
            reference_name,
            runs,
            observations,
            target_lookup,
            output_csv,
        )
    return last_result
