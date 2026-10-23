# Script 12
"""Calculate numeric endpoint-to-influencer distance observations."""

import math
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
from shapely.ops import nearest_points

from .relational_tables import write_table

OBSERVATION_TABLE = "endpoint_distance_observations"
ENDPOINTS_LAYER = "Endpoints"
ENDPOINT_SIDE = "target"
INCLUDE_ZERO_DISTANCE_LINES = True

REFERENCE_LAYERS = {
    "hv": "proposed_highvoltage",
    "cable": "Elektriciteitskabel",
}

INFLUENCED_LAYERS = [
    "OlieGasChemicalienPijpleiding",
    "Overig",
    "Waterleiding",
    "Rioolleiding",
    "ThermischePijpleiding",
]


def _list_layers(path):
    return {row[0] for row in pyogrio.list_layers(path)}


def _normalise_ids(dataframe, columns):
    result = dataframe.copy()
    for column in columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=columns).copy()
    for column in columns:
        result[column] = result[column].astype(int)
    return result


def _normalise_pairs(pair_df):
    required = {
        "target_layer",
        "target_feature_id",
        "target_part_no",
        "reference_type",
        "reference_layer",
        "reference_feature_id",
        "reference_part_no",
    }
    missing = required - set(pair_df.columns)
    if missing:
        raise ValueError(f"Pair table is missing columns: {sorted(missing)}")

    result = pair_df.copy()
    for column in ["target_layer", "reference_type", "reference_layer"]:
        result[column] = result[column].astype(str).str.strip()

    id_columns = [
        "target_feature_id",
        "target_part_no",
        "reference_feature_id",
        "reference_part_no",
    ]
    result = _normalise_ids(result, id_columns)

    return result.drop_duplicates(
        [
            "target_layer",
            "target_feature_id",
            "target_part_no",
            "reference_type",
            "reference_layer",
            "reference_feature_id",
            "reference_part_no",
        ]
    )


def _empty_observations():
    return pd.DataFrame(
        columns=[
            "endpoint_side",
            "endpoint_role",
            "distance_target_role",
            "reference_type",
            "reference_layer",
            "reference_feature_id",
            "reference_part_no",
            "target_layer",
            "target_feature_id",
            "target_part_no",
            "endpoint_source_layer",
            "endpoint_feature_id",
            "endpoint_part_no",
            "endpoint_node_type",
            "distance_to_layer",
            "distance_to_feature_id",
            "distance_to_part_no",
            "distance",
            "reference_measure",
            "match_source",
            "angle_diff",
            "overlap_length",
            "overlap_ratio",
            "influencer_class",
            "eligibility_column",
            "eligibility_value",
        ]
    )


def calculate_parallel_distance(
    input_path: Path,
    output_path: Path,
    pair_table_path: Path | None = None,
) -> Path:
    input_path = Path(input_path)
    output_path = Path(output_path)

    if pair_table_path is None:
        pair_table_path = input_path.with_name(
            f"{input_path.stem}_parallel_match_tables"
        ) / "parallel_pairs_all.csv"
    else:
        pair_table_path = Path(pair_table_path)

    if not input_path.exists():
        raise FileNotFoundError(f"Input GeoPackage not found: {input_path}")
    if not pair_table_path.exists():
        raise FileNotFoundError(f"Pair table not found: {pair_table_path}")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("input_path and output_path must be different.")

    pairs = _normalise_pairs(pd.read_csv(pair_table_path))
    pairs = pairs[
        pairs["target_layer"].isin(INFLUENCED_LAYERS)
        & pairs.apply(
            lambda row: (
                row["reference_type"],
                row["reference_layer"],
            ) in set(REFERENCE_LAYERS.items()),
            axis=1,
        )
    ].copy()
    if pairs.empty:
        raise ValueError("No usable normalized pair relationships were found.")

    existing_layers = _list_layers(input_path)
    if ENDPOINTS_LAYER not in existing_layers:
        raise ValueError(f"Layer '{ENDPOINTS_LAYER}' is required.")

    required_geometry_layers = sorted(
        set(pairs["target_layer"]).union(pairs["reference_layer"])
    )
    geometry_lookup = {}
    crs = None

    for layer_name in required_geometry_layers:
        if layer_name not in existing_layers:
            raise ValueError(
                f"Pair table references missing geometry layer '{layer_name}'."
            )
        gdf = gpd.read_file(
            input_path,
            layer=layer_name,
            columns=["feature_id", "part_no", "geometry"],
            use_arrow=True,
        )
        if crs is None:
            crs = gdf.crs
        elif gdf.crs != crs:
            gdf = gdf.to_crs(crs)

        gdf = _normalise_ids(gdf, ["feature_id", "part_no"])
        gdf = gdf.dropna(subset=["geometry"])
        gdf = gdf[~gdf.geometry.is_empty]
        for row in gdf.itertuples(index=False):
            geometry_lookup.setdefault(
                (layer_name, int(row.feature_id), int(row.part_no)),
                [],
            ).append(row.geometry)

    endpoints = gpd.read_file(
        input_path,
        layer=ENDPOINTS_LAYER,
        columns=[
            "source_layer",
            "feature_id",
            "part_no",
            "node_type",
            "geometry",
        ],
        use_arrow=True,
    )
    if endpoints.crs != crs:
        endpoints = endpoints.to_crs(crs)
    endpoints = _normalise_ids(endpoints, ["feature_id", "part_no"])
    endpoints = endpoints.dropna(subset=["geometry"])
    endpoints = endpoints[~endpoints.geometry.is_empty]

    endpoint_lookup = {}
    for row in endpoints.itertuples(index=False):
        endpoint_lookup.setdefault(
            (str(row.source_layer), int(row.feature_id), int(row.part_no)),
            [],
        ).append((str(row.node_type).strip().lower(), row.geometry))

    optional_columns = [
        "angle_diff",
        "overlap_length",
        "overlap_ratio",
        "influencer_class",
        "eligibility_column",
        "eligibility_value",
    ]

    records = []
    for pair in pairs.itertuples(index=False):
        reference_key = (
            str(pair.reference_layer),
            int(pair.reference_feature_id),
            int(pair.reference_part_no),
        )
        target_key = (
            str(pair.target_layer),
            int(pair.target_feature_id),
            int(pair.target_part_no),
        )

        if ENDPOINT_SIDE == "target":
            endpoint_key = target_key
            distance_target_key = reference_key
            endpoint_role = "target"
            distance_target_role = "reference"
        elif ENDPOINT_SIDE == "reference":
            endpoint_key = reference_key
            distance_target_key = target_key
            endpoint_role = "reference"
            distance_target_role = "target"
        else:
            raise ValueError("ENDPOINT_SIDE must be 'target' or 'reference'.")

        for endpoint_name, endpoint_geometry in endpoint_lookup.get(endpoint_key, []):
            for distance_target_geometry in geometry_lookup.get(
                distance_target_key,
                [],
            ):
                nearest_endpoint, projected_point = nearest_points(
                    endpoint_geometry,
                    distance_target_geometry,
                )
                distance = float(nearest_endpoint.distance(projected_point))
                if distance == 0 and not INCLUDE_ZERO_DISTANCE_LINES:
                    continue

                reference_geometries = geometry_lookup.get(reference_key, [])
                if not reference_geometries:
                    continue
                reference_geometry = reference_geometries[0]
                reference_measure = float(
                    reference_geometry.project(projected_point)
                )

                record = {
                    "endpoint_side": ENDPOINT_SIDE,
                    "endpoint_role": endpoint_role,
                    "distance_target_role": distance_target_role,
                    "reference_type": str(pair.reference_type),
                    "reference_layer": str(pair.reference_layer),
                    "reference_feature_id": int(pair.reference_feature_id),
                    "reference_part_no": int(pair.reference_part_no),
                    "target_layer": str(pair.target_layer),
                    "target_feature_id": int(pair.target_feature_id),
                    "target_part_no": int(pair.target_part_no),
                    "endpoint_source_layer": endpoint_key[0],
                    "endpoint_feature_id": endpoint_key[1],
                    "endpoint_part_no": endpoint_key[2],
                    "endpoint_node_type": endpoint_name,
                    "distance_to_layer": distance_target_key[0],
                    "distance_to_feature_id": distance_target_key[1],
                    "distance_to_part_no": distance_target_key[2],
                    "distance": distance,
                    "reference_measure": reference_measure,
                    "match_source": "parallel_pairs_all",
                }
                pair_values = pair._asdict()
                for column in optional_columns:
                    if column in pair_values:
                        record[column] = pair_values[column]
                records.append(record)

    observations = pd.DataFrame(records)
    if observations.empty:
        observations = _empty_observations()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.unlink(missing_ok=True)
    write_table(output_path, OBSERVATION_TABLE, observations)

    print(f"Wrote {len(observations):,} numeric observations to {output_path}")
    print(f"Table: {OBSERVATION_TABLE}")
    return output_path
