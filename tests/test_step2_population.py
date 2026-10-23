from __future__ import annotations

import sys
import types

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

if "pyogrio" not in sys.modules:
    pyogrio = types.ModuleType("pyogrio")
    pyogrio.__path__ = []
    pyogrio_errors = types.ModuleType("pyogrio.errors")
    pyogrio_errors.DataLayerError = type("DataLayerError", (Exception,), {})
    sys.modules["pyogrio"] = pyogrio
    sys.modules["pyogrio.errors"] = pyogrio_errors

from model.part2.assessment_storage import select_step1_inductive_no_pass
from model.post import filtered_klic_step2_result
import prepare_for_arcgis


def _frame(rows):
    return gpd.GeoDataFrame(
        rows,
        geometry=[Point(index, 0) for index in range(len(rows))],
        crs="EPSG:28992",
    )


def test_shared_selector_keeps_only_complete_inductive_no_pass_runs() -> None:
    frame = _frame(
        [
            {
                "influenced_run_id": "run-a",
                "inductive": "Pass",
                "step2_inductive": "pass",
            },
            {
                "influenced_run_id": "run-a",
                "inductive": "NO PASS",
                "step2_inductive": "no-pass",
            },
            {
                "influenced_run_id": "run-b",
                "inductive": "Pass",
                "step2_inductive": pd.NA,
            },
            {
                "influenced_run_id": None,
                "inductive": "no_pass",
                "step2_inductive": "pass",
            },
            {
                "influenced_run_id": " ",
                "inductive": "Pass",
                "step2_inductive": pd.NA,
            },
        ]
    )

    selected = select_step1_inductive_no_pass(frame, "reference_no_pass")

    assert selected.index.tolist() == [0, 1, 3]
    assert selected["influenced_run_id"].tolist()[:2] == ["run-a", "run-a"]
    assert pd.isna(selected["influenced_run_id"].iloc[2])


def test_step2_result_reader_excludes_other_step1_failure_types(
    monkeypatch,
) -> None:
    source = _frame(
        [
            {
                "influenced_run_id": "run-inductive",
                "inductive": "no-pass",
                "step2_inductive": "pass",
                "id": 1,
            },
            {
                "influenced_run_id": "run-thermal-only",
                "inductive": "pass",
                "step2_inductive": pd.NA,
                "id": 2,
            },
        ]
    )

    monkeypatch.setattr(
        filtered_klic_step2_result,
        "find_assessment_column",
        lambda *_args, **_kwargs: "step2_inductive",
    )
    monkeypatch.setattr(
        filtered_klic_step2_result,
        "get_available_columns",
        lambda *_args, **_kwargs: [
            "influenced_run_id",
            "step2_inductive",
            "id",
        ],
    )

    requested_columns = []

    def fake_read(_path, layer, **kwargs):
        assert layer.endswith("_no_pass")
        requested_columns.extend(kwargs["columns"])
        return source[kwargs["columns"] + ["geometry"]].copy()

    monkeypatch.setattr(
        filtered_klic_step2_result,
        "_read_model_dataframe",
        fake_read,
    )

    result = filtered_klic_step2_result.read_layer(
        input_path="unused.gpkg",
        layer_name="electricity_cable_parts_with_runid_no_pass",
        requested_columns=["influenced_run_id", "id", "step2_inductive"],
        infrastructure_type="Elektriciteitskabel",
        assessment_name="inductive",
    )

    assert "inductive" in requested_columns
    assert result["influenced_run_id"].tolist() == ["run-inductive"]
    assert result["inductive"].tolist() == ["pass"]
    assert "step2_inductive" not in result.columns


def test_population_validation_compares_logical_identities(monkeypatch) -> None:
    layers = {
        ("step1.gpkg", "no_pass_inductive"): pd.DataFrame(
            [
                {
                    "influenced_run_id": "run-a",
                    "infrastructure_type": "Elektriciteitskabel",
                },
                {
                    "influenced_run_id": "run-b",
                    "infrastructure_type": "Waterleiding",
                },
            ]
        ),
        ("step2.gpkg", "result_inductive"): pd.DataFrame(
            [
                {
                    "influenced_run_id": "run-a",
                    "infrastructure_type": "Elektriciteitskabel",
                },
                {
                    "influenced_run_id": "run-c",
                    "infrastructure_type": "Waterleiding",
                },
            ]
        ),
    }

    monkeypatch.setattr(
        prepare_for_arcgis.pyogrio,
        "list_layers",
        lambda path: pd.DataFrame(
            [[layer, "Unknown"] for file_name, layer in layers if file_name == str(path)]
        ).to_numpy(),
        raising=False,
    )
    monkeypatch.setattr(
        prepare_for_arcgis.pyogrio,
        "read_info",
        lambda path, layer: {
            "fields": list(layers[(str(path), layer)].columns),
            "features": len(layers[(str(path), layer)]),
        },
        raising=False,
    )
    monkeypatch.setattr(
        prepare_for_arcgis.pyogrio,
        "read_dataframe",
        lambda path, layer, columns, read_geometry: layers[
            (str(path), layer)
        ][columns].copy(),
        raising=False,
    )

    try:
        prepare_for_arcgis._validate_step2_population(
            "step1.gpkg",
            "step2.gpkg",
        )
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected a population mismatch")

    assert "run-b" in message
    assert "run-c" in message
