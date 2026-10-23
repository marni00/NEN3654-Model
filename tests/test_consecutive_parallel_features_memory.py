import sys
import types

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString


# pyogrio is only needed for GeoPackage I/O. The helper under test is pure
# in-memory logic, so a stub keeps this unit test independent of GDAL.
sys.modules.setdefault("pyogrio", types.SimpleNamespace())

from model.part2.consecutive_parallel_features import build_influenced_run_links


def test_build_influenced_run_links_keeps_geometry_normalized():
    influenced = gpd.GeoDataFrame(
        {
            "source_layer": ["Waterleiding", "Waterleiding", "Waterleiding"],
            "feature_id": [10, 10, 20],
            "part_no": [1, 2, 1],
        },
        geometry=[
            LineString([(0, 0), (10, 0)]),
            LineString([(10, 0), (20, 0)]),
            LineString([(0, 1), (5, 1)]),
        ],
        crs="EPSG:28992",
    )
    bridge = pd.DataFrame(
        {
            "source_layer": [
                "Waterleiding",
                "Waterleiding",
                "Waterleiding",
            ],
            "feature_id": [10, 10, 20],
            "run_id": [1, 2, 1],
        }
    )
    distances = pd.DataFrame(
        {
            "source_layer": ["Waterleiding", "Waterleiding", "Waterleiding"],
            "feature_id": [10, 10, 20],
            "part_no": [1, 2, 1],
            "min_distance": [2.0, 3.0, 4.0],
            "max_distance": [5.0, 6.0, 7.0],
        }
    )

    result = build_influenced_run_links(influenced, bridge, distances)

    assert len(result) == 5
    assert "geometry" not in result.columns

    run_1_feature_10 = result[
        result["run_id"].eq(1) & result["feature_id"].eq(10)
    ]
    assert set(run_1_feature_10["influenced_run_id"]) == {"1.1"}
    assert set(run_1_feature_10["influenced_component_length_m"]) == {20.0}

    run_1_feature_20 = result[
        result["run_id"].eq(1) & result["feature_id"].eq(20)
    ]
    assert set(run_1_feature_20["influenced_run_id"]) == {"1.2"}
    assert set(run_1_feature_20["influenced_component_length_m"]) == {5.0}

    run_2_feature_10 = result[
        result["run_id"].eq(2) & result["feature_id"].eq(10)
    ]
    assert set(run_2_feature_10["influenced_run_id"]) == {"2.1"}
    assert set(run_2_feature_10["influenced_component_length_m"]) == {20.0}
