import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString

from model.part2.assessment_storage import (
    CABLE_LAYER,
    INFLUENCED_LAYER,
    initialize_normalized_output,
    read_assessed_layer,
)


def test_normalized_storage_reconstructs_and_selects_no_pass(tmp_path):
    source = tmp_path / "temp18.gpkg"
    output = tmp_path / "temp19.gpkg"

    cable = gpd.GeoDataFrame(
        {"feature_id": ["c1"], "part_no": [1], "run_id": [1], "id": [10]},
        geometry=[LineString([(0, 0), (1, 0)])],
        crs="EPSG:28992",
    )
    cable.to_file(source, layer=CABLE_LAYER, driver="GPKG")

    influenced = gpd.GeoDataFrame(
        {
            "source_layer": ["Waterleiding"],
            "feature_id": ["w1"],
            "part_no": [1],
        },
        geometry=[LineString([(0, 1), (1, 1)])],
        crs="EPSG:28992",
    )
    influenced.to_file(
        source,
        layer=INFLUENCED_LAYER,
        driver="GPKG",
        mode="a",
    )

    reference = pd.DataFrame(
        {
            "_assessment_id": [0, 1],
            "reference_type": ["cable", "cable"],
            "feature_id": ["c1", "c1"],
            "part_no": [1, 1],
            "run_id": [1, 1],
            "influenced_run_id": ["r1", "r2"],
            "thermal": ["pass", "no-pass"],
            "capacitive": ["pass", "pass"],
            "resistive": ["pass", "pass"],
            "inductive": ["pass", "pass"],
        }
    )
    influenced_assessments = pd.DataFrame(
        {
            "_assessment_id": [0],
            "source_layer": ["Waterleiding"],
            "feature_id": ["w1"],
            "part_no": [1],
            "influenced_run_id": ["r2"],
            "thermal": ["pass"],
            "capacitive": ["pass"],
            "resistive": ["pass"],
            "inductive": ["pass"],
        }
    )

    initialize_normalized_output(
        source,
        output,
        reference,
        influenced_assessments,
    )

    assert len(gpd.read_file(output, layer=CABLE_LAYER)) == 1
    assert len(read_assessed_layer(output, CABLE_LAYER)) == 2
    assert len(read_assessed_layer(output, CABLE_LAYER, no_pass=True)) == 1
    assert len(
        read_assessed_layer(output, INFLUENCED_LAYER, no_pass=True)
    ) == 1


def test_reference_reconstruction_joins_assessments_to_the_exact_part(tmp_path):
    source = tmp_path / "temp18.gpkg"
    output = tmp_path / "temp19.gpkg"

    cable = gpd.GeoDataFrame(
        {
            "feature_id": ["c1", "c1"],
            "part_no": [1, 2],
            "run_id": [7, 7],
            "id": [10, 10],
            "Code": ["A", "A"],
        },
        geometry=[
            LineString([(0, 0), (1, 0)]),
            LineString([(1, 0), (2, 0)]),
        ],
        crs="EPSG:28992",
    )
    cable.to_file(source, layer=CABLE_LAYER, driver="GPKG")

    reference = pd.DataFrame(
        {
            "_assessment_id": [0, 1],
            "reference_type": ["cable", "cable"],
            "feature_id": ["c1", "c1"],
            "part_no": [1, 2],
            "run_id": [7, 7],
            "influenced_run_id": ["r1", "r2"],
            "thermal": ["pass", "pass"],
            "capacitive": ["pass", "pass"],
            "resistive": ["pass", "pass"],
            "inductive": ["no-pass", "pass"],
        }
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

    initialize_normalized_output(source, output, reference, influenced)

    reconstructed = read_assessed_layer(output, CABLE_LAYER)

    assert len(reconstructed) == 2
    assert reconstructed.sort_values("part_no")["influenced_run_id"].tolist() == [
        "r1",
        "r2",
    ]
    assert reconstructed["Code"].tolist() == ["A", "A"]
