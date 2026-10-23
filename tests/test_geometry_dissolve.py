import geopandas as gpd
import pytest
import shapely
from shapely.geometry import GeometryCollection, LineString, MultiLineString, Point

from model.post.geometry_dissolve import (
    _normalise_dissolved_line_geometry,
    dissolve_with_memory_fallback,
)


def test_normalise_dissolved_line_geometry_merges_exact_endpoints():
    geometry = MultiLineString(
        [
            [(0, 0), (1, 0)],
            [(1, 0), (2, 0)],
            [(10, 0), (11, 0)],
        ]
    )

    result = _normalise_dissolved_line_geometry(geometry)

    assert isinstance(result, MultiLineString)
    assert len(result.geoms) == 2
    assert result.length == pytest.approx(geometry.length)
    assert result.bounds == geometry.bounds


@pytest.mark.skipif(int(shapely.__version__.split(".")[0]) < 2, reason="requires Shapely 2 multipart support")
def test_normal_dissolve_merges_exactly_connected_parts():
    frame = gpd.GeoDataFrame(
        {
            "run_id": ["r1", "r1", "r1"],
            "count": [1, 1, 1],
        },
        geometry=[
            LineString([(0, 0), (1, 0)]),
            LineString([(1, 0), (2, 0)]),
            LineString([(10, 0), (11, 0)]),
        ],
        crs="EPSG:28992",
    )

    result = dissolve_with_memory_fallback(
        frame,
        by=["run_id"],
        aggfunc={"count": "sum"},
        as_index=False,
        sort=False,
    )

    geometry = result.loc[0, "geometry"]

    assert result["run_id"].tolist() == ["r1"]
    assert result["count"].tolist() == [3]
    assert isinstance(geometry, MultiLineString)
    assert len(geometry.geoms) == 2
    assert geometry.length == pytest.approx(3.0)
    assert geometry.bounds == (0.0, 0.0, 11.0, 0.0)


@pytest.mark.skipif(int(shapely.__version__.split(".")[0]) < 2, reason="requires Shapely 2 multipart support")
def test_dissolve_falls_back_and_merges_connected_linework(monkeypatch):
    frame = gpd.GeoDataFrame(
        {
            "run_id": ["r1", "r1", "r2"],
            "count": [1, 1, 1],
        },
        geometry=[
            LineString([(0, 0), (1, 0)]),
            LineString([(1, 0), (2, 0)]),
            LineString([(0, 1), (1, 1)]),
        ],
        crs="EPSG:28992",
    )

    def fail_dissolve(*args, **kwargs):
        error_type = type("GEOSException", (Exception,), {"__module__": "shapely.errors"})
        raise error_type("bad allocation")

    monkeypatch.setattr(gpd.GeoDataFrame, "dissolve", fail_dissolve)

    result = dissolve_with_memory_fallback(
        frame,
        by=["run_id"],
        aggfunc={"count": "sum"},
        as_index=False,
        sort=False,
    )

    assert result["run_id"].tolist() == ["r1", "r2"]
    assert result["count"].tolist() == [2, 1]
    assert result.loc[0, "geometry"].equals(
        LineString([(0, 0), (1, 0), (2, 0)])
    )
    assert result.loc[1, "geometry"].equals(frame.loc[2, "geometry"])


@pytest.mark.skipif(int(shapely.__version__.split(".")[0]) < 2, reason="requires Shapely 2 multipart support")
def test_fallback_extracts_and_merges_nested_linework(monkeypatch):
    first_line = LineString([(0, 0), (1, 0)])
    second_line = LineString([(1, 0), (2, 0)])
    frame = gpd.GeoDataFrame(
        {
            "run_id": ["r1", "r1"],
            "count": [1, 1],
        },
        geometry=[
            GeometryCollection([first_line, Point(0, 0)]),
            GeometryCollection([GeometryCollection([second_line])]),
        ],
        crs="EPSG:28992",
    )

    def fail_dissolve(*args, **kwargs):
        error_type = type("GEOSException", (Exception,), {"__module__": "shapely.errors"})
        raise error_type("bad allocation")

    monkeypatch.setattr(gpd.GeoDataFrame, "dissolve", fail_dissolve)

    with pytest.warns(RuntimeWarning, match="non-line components"):
        result = dissolve_with_memory_fallback(
            frame,
            by=["run_id"],
            aggfunc={"count": "sum"},
            as_index=False,
            sort=False,
        )

    geometry = result.loc[0, "geometry"]

    assert result["run_id"].tolist() == ["r1"]
    assert result["count"].tolist() == [2]
    assert geometry.equals(LineString([(0, 0), (1, 0), (2, 0)]))
