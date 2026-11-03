import geopandas as gpd
from shapely.geometry import LineString

from model.part1.combine_electricity_features import (
    combine_elektriciteitskabel_layer,
)


def test_parallel_high_voltage_bundle_keeps_all_lines():
    geometries = [
        LineString([(0, 0), (100, 0)]),
        LineString([(0, 5), (100, 5)]),
        LineString([(0, 10), (100, 10)]),
    ]
    frame = gpd.GeoDataFrame(
        {
            "werkVoltage": [150_000, 150_000, 150_000],
            "utilityDeliveryType": ["electricity-a", "electricity-b", "electricity-c"],
            "verticalePositie": ["aboveground"] * 3,
            "thema": ["hoogspanning"] * 3,
            "netwerk": ["network-a"] * 3,
        },
        geometry=geometries,
        crs="EPSG:28992",
    )

    result = combine_elektriciteitskabel_layer(
        frame,
        "Elektriciteitskabel",
    )

    assert len(result) == 3
    assert [geometry.wkt for geometry in result.geometry] == [
        geometry.wkt for geometry in geometries
    ]


def test_combining_no_longer_requires_network_column():
    frame = gpd.GeoDataFrame(
        {
            "werkVoltage": [150_000],
            "utilityDeliveryType": ["electricity"],
            "verticalePositie": ["aboveground"],
            "thema": ["hoogspanning"],
        },
        geometry=[LineString([(0, 0), (100, 0)])],
        crs="EPSG:28992",
    )

    result = combine_elektriciteitskabel_layer(
        frame,
        "Elektriciteitskabel",
    )

    assert len(result) == 1
    assert result.geometry.iloc[0].equals(frame.geometry.iloc[0])
