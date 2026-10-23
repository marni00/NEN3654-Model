import sqlite3

import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from model.part2.assessment_storage import (
    CABLE_LAYER,
    initialize_normalized_output,
    is_normalized_assessment,
)
from model.part2.assign_electricity_cable_codes import (
    assign_electricity_cable_codes_to_step1,
)


def test_normalized_step1_code_update_preserves_geopackage_spatial_data(tmp_path):
    source = tmp_path / "temp18.gpkg"
    step1 = tmp_path / "temp19.gpkg"
    coded = tmp_path / "temp19_codes.gpkg"
    config = tmp_path / "codes.toml"

    cable = gpd.GeoDataFrame(
        {
            "feature_id": ["c1", "c2"],
            "part_no": [1, 1],
            "run_id": [1, 2],
            "id": [10, 20],
            "Code": ["OLD", "OLD"],
        },
        geometry=[
            LineString([(0, 0), (1, 0)]),
            LineString([(2, 3), (5, 7)]),
        ],
        crs="EPSG:28992",
    )
    cable.to_file(source, layer=CABLE_LAYER, driver="GPKG")

    reference = pd.DataFrame(
        columns=[
            "_assessment_id",
            "reference_type",
            "feature_id",
            "part_no",
            "run_id",
            "influenced_run_id",
            "thermal",
            "capacitive",
            "resistive",
            "inductive",
        ]
    )
    influenced = pd.DataFrame(
        columns=[
            "_assessment_id",
            "source_layer",
            "feature_id",
            "part_no",
            "influenced_run_id",
            "thermal",
            "capacitive",
            "resistive",
            "inductive",
        ]
    )
    initialize_normalized_output(source, step1, reference, influenced)
    config.write_text(
        '[[Elektriciteitskabel]]\nid = [10]\ncode = "L09"\n',
        encoding="utf-8",
    )

    assign_electricity_cable_codes_to_step1(
        input_path=step1,
        output_path=coded,
        config_path=config,
    )

    assert is_normalized_assessment(coded)
    result = gpd.read_file(coded, layer=CABLE_LAYER).sort_values("id")
    assert result.iloc[0]["Code"] == "L09"
    assert pd.isna(result.iloc[1]["Code"])
    assert result.geometry.to_wkt().tolist() == cable.geometry.to_wkt().tolist()

    with sqlite3.connect(coded) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        rtree_rows = connection.execute(
            f'SELECT minx, maxx, miny, maxy '
            f'FROM "rtree_{CABLE_LAYER}_geom" ORDER BY id'
        ).fetchall()

    assert rtree_rows == [
        (0.0, 1.0, 0.0, 0.0),
        (2.0, 5.0, 3.0, 7.0),
    ]

    original = gpd.read_file(step1, layer=CABLE_LAYER).sort_values("id")
    assert original["Code"].tolist() == ["OLD", "OLD"]
