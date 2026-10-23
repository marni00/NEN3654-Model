import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import LineString

from model.part2.assessment_storage import (
    CABLE_LAYER,
    HV_LAYER,
    INFLUENCED_LAYER,
    initialize_normalized_output,
    is_normalized_assessment,
)
from model.part2.assign_electricity_cable_codes import (
    assign_electricity_cable_codes_to_step1,
    step1_electricity_candidate_ids,
)
from model.part2.relational_tables import read_table
from model.part2.step2_NEN import step2_nen


def _create_normalized_step1(path, reference_layer, reference_type):
    source = path.parent / f"{reference_type}_temp18.gpkg"

    reference_data = {
        "feature_id": [f"{reference_type}1"],
        "part_no": [1],
        "run_id": [1],
        "Code": ["L09"],
    }
    if reference_type == "cable":
        reference_data["id"] = [10]

    reference = gpd.GeoDataFrame(
        reference_data,
        geometry=[LineString([(0, 0), (100, 0)])],
        crs="EPSG:28992",
    )
    reference.to_file(source, layer=reference_layer, driver="GPKG")

    influenced = gpd.GeoDataFrame(
        {
            "source_layer": ["Waterleiding"],
            "feature_id": ["w1"],
            "part_no": [1],
        },
        geometry=[LineString([(0, 10), (100, 10)])],
        crs="EPSG:28992",
    )
    influenced.to_file(
        source,
        layer=INFLUENCED_LAYER,
        driver="GPKG",
        mode="a",
    )

    reference_assessments = pd.DataFrame(
        {
            "_assessment_id": [0],
            "reference_type": [reference_type],
            "feature_id": [f"{reference_type}1"],
            "part_no": [1],
            "run_id": [1],
            "influenced_run_id": ["run-1"],
            "influencer_class": ["ug_le24kv"],
            "a": [100.0],
            "l": [1000.0],
            "min_distance": [10.0],
            "max_distance": [10.0],
            "thermal": ["pass"],
            "capacitive": ["pass"],
            "resistive": ["pass"],
            "inductive": ["no-pass"],
        }
    )
    influenced_assessments = pd.DataFrame(
        {
            "_assessment_id": [0],
            "source_layer": ["Waterleiding"],
            "feature_id": ["w1"],
            "part_no": [1],
            "influenced_run_id": ["run-1"],
            "influencer_class": ["ug_le24kv"],
            "a": [100.0],
            "l": [1000.0],
            "min_distance": [10.0],
            "max_distance": [10.0],
            "thermal": ["pass"],
            "capacitive": ["pass"],
            "resistive": ["pass"],
            "inductive": ["no-pass"],
        }
    )

    initialize_normalized_output(
        source,
        path,
        reference_assessments,
        influenced_assessments,
    )


@pytest.mark.parametrize(
    ("reference_layer", "reference_type", "missing_layer"),
    [
        (CABLE_LAYER, "cable", HV_LAYER),
        (HV_LAYER, "hv", CABLE_LAYER),
    ],
)
def test_step2_accepts_normalized_input_with_optional_reference_layer_missing(
    tmp_path,
    reference_layer,
    reference_type,
    missing_layer,
):
    step1 = tmp_path / f"{reference_type}_temp19.gpkg"
    output = tmp_path / f"{reference_type}_temp20.gpkg"
    _create_normalized_step1(step1, reference_layer, reference_type)

    assert missing_layer not in set(gpd.list_layers(step1)["name"])

    step2_nen(input_path=step1, output_path=output)

    assert output.is_file()
    assert is_normalized_assessment(output)
    updated = read_table(output, "step1_reference_assessments")
    assert "step2_inductive" in updated.columns
    assert len(updated) == 1


def test_code_assignment_copies_normalized_proposed_hv_input_without_cable_layer(
    tmp_path,
):
    step1 = tmp_path / "hv_temp19.gpkg"
    coded = tmp_path / "hv_temp19_codes.gpkg"
    config = tmp_path / "codes.toml"
    _create_normalized_step1(step1, HV_LAYER, "hv")
    config.write_text("", encoding="utf-8")

    assign_electricity_cable_codes_to_step1(
        input_path=step1,
        output_path=coded,
        config_path=config,
    )

    assert coded.is_file()
    assert is_normalized_assessment(coded)
    assert CABLE_LAYER not in set(gpd.list_layers(coded)["name"])
    assert HV_LAYER in set(gpd.list_layers(coded)["name"])



def test_candidate_lookup_returns_empty_for_normalized_input_without_cable_layer(
    tmp_path,
):
    step1 = tmp_path / "hv_temp19.gpkg"
    _create_normalized_step1(step1, HV_LAYER, "hv")

    assert step1_electricity_candidate_ids(step1) == []
