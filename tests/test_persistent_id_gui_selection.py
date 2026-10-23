from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from gui.models import FeatureKey
from model.part2.select_emphasized_conduit import (
    load_id_selection,
    persistent_id_series,
)


def test_feature_label_uses_persistent_id_not_native_fid() -> None:
    key = FeatureKey("part1", "Waterleiding", 9008, 8)

    assert key.label == "Waterleiding · ID 8"


def test_selection_matches_persistent_id_when_native_fid_differs() -> None:
    gdf = gpd.GeoDataFrame(
        {"id": [7, 8], "geometry": [Point(0, 0), Point(1, 1)]},
        geometry="geometry",
        crs="EPSG:28992",
        index=pd.Index([1007, 1008], name="source_fid"),
    )

    ids = persistent_id_series(gdf, "Waterleiding")
    selected = gdf.loc[ids.isin({8})]

    assert selected.index.tolist() == [1008]
    assert selected["id"].tolist() == [8]


def test_legacy_fids_key_is_matched_as_persistent_id(tmp_path: Path) -> None:
    path = tmp_path / "selection.toml"
    path.write_text(
        """
[Waterleiding]
fids = [8]
[ThermischePijpleiding]
ids = []
[Rioolleiding]
ids = []
[OlieGasChemicalienPijpleiding]
ids = []
[Overig]
ids = []
[Elektriciteitskabel]
ids = []
[proposed_highvoltage]
ids = []
""".strip(),
        encoding="utf-8",
    )

    selection = load_id_selection(path)

    assert selection["Waterleiding"]["ids"] == {8}
