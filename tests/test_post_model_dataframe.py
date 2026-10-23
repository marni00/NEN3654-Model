from __future__ import annotations

import importlib

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point


MODULE_NAMES = [
    "model.post.filtered_klic_step1_result",
    "model.post.filtered_klic_step1_result_nopass",
    "model.post.filtered_klic_step2_result",
    "model.post.filtered_klic_step2_result_nopass",
]


@pytest.fixture
def assessed_frame() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "source_layer": ["Waterleiding"],
            "value": [1],
            "shape": [Point(0, 0)],
        },
        geometry="shape",
        crs="EPSG:28992",
    )


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_read_model_dataframe_without_geometry_returns_requested_columns(
    monkeypatch,
    module_name,
    assessed_frame,
):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "is_normalized_assessment", lambda _path: True)
    monkeypatch.setattr(
        module,
        "read_assessed_layer",
        lambda *_args, **_kwargs: assessed_frame.copy(),
    )

    result = module._read_model_dataframe(
        "assessment.gpkg",
        module.INFLUENCED_LAYER,
        columns=["source_layer"],
        read_geometry=False,
    )

    assert type(result) is pd.DataFrame
    assert result.columns.tolist() == ["source_layer"]
    assert result["source_layer"].tolist() == ["Waterleiding"]


@pytest.mark.parametrize("module_name", MODULE_NAMES)
def test_read_model_dataframe_keeps_geometry_by_default(
    monkeypatch,
    module_name,
    assessed_frame,
):
    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, "is_normalized_assessment", lambda _path: True)
    monkeypatch.setattr(
        module,
        "read_assessed_layer",
        lambda *_args, **_kwargs: assessed_frame.copy(),
    )

    result = module._read_model_dataframe(
        "assessment.gpkg",
        module.INFLUENCED_LAYER,
        columns=["source_layer"],
    )

    assert isinstance(result, gpd.GeoDataFrame)
    assert result.geometry.name == "shape"
    assert result.columns.tolist() == ["source_layer", "shape"]
