from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import Point

from gui.services import gdb_combiner


@pytest.mark.parametrize(
    ("geometry_type", "expected"),
    [
        ("Point", ("point", False)),
        ("MultiPoint", ("point", True)),
        ("3D MultiLineString", ("line", True)),
        ("Polygon Z", ("polygon", False)),
        (None, (None, False)),
    ],
)
def test_geometry_type_details(geometry_type, expected):
    assert gdb_combiner._geometry_type_details(geometry_type) == expected


def test_combined_schema_requires_multi_when_any_source_is_multipart(monkeypatch):
    sources = [Path("first.gdb"), Path("second.gdb")]
    geometry_types = {
        sources[0]: "Point",
        sources[1]: "MultiPoint",
    }

    def read_info(source, *, layer):
        return {
            "crs": "EPSG:28992",
            "fields": ["name"],
            "dtypes": [object],
            "geometry_type": geometry_types[source],
        }

    monkeypatch.setattr(gdb_combiner.pyogrio, "read_info", read_info)

    _, _, _, source_requires_multi = gdb_combiner._combined_schema(
        "objects", sources
    )

    assert source_requires_multi is True


def test_combiner_promotes_points_when_schema_requires_multipart(
    monkeypatch, tmp_path
):
    first = tmp_path / "first.gdb"
    second = tmp_path / "second.gdb"
    output = tmp_path / "combined.gdb"
    first.mkdir()
    second.mkdir()

    chunks = [
        gpd.GeoDataFrame(
            {"name": ["first"]},
            geometry=[Point(1.0, 1.0)],
            crs="EPSG:28992",
        ),
        gpd.GeoDataFrame(
            {"name": ["second"]},
            geometry=[Point(2.0, 2.0)],
            crs="EPSG:28992",
        ),
    ]
    written_geometry_types = []
    append_flags = []

    monkeypatch.setattr(
        gdb_combiner.pyogrio,
        "list_layers",
        lambda source: [["objects", "Point"]],
    )
    monkeypatch.setattr(
        gdb_combiner,
        "_combined_schema",
        lambda layer, sources: (
            ["name"],
            {"name": "string"},
            "EPSG:28992",
            True,
        ),
    )

    def prepared_chunks(**kwargs):
        yield from chunks

    def write_dataframe(frame, path, *, layer, driver, append):
        written_geometry_types.append(
            [geometry.geom_type for geometry in frame.geometry]
        )
        append_flags.append(append)

    monkeypatch.setattr(gdb_combiner, "_prepared_chunks", prepared_chunks)
    monkeypatch.setattr(
        gdb_combiner.pyogrio, "write_dataframe", write_dataframe
    )
    monkeypatch.setattr(
        gdb_combiner,
        "_feature_keys",
        lambda frame: frame["name"].tolist(),
    )

    result = gdb_combiner.combine_file_geodatabases(
        input_gdbs=[first, second],
        output_gdb=output,
        chunk_size=10,
    )

    assert written_geometry_types == [["MultiPoint"], ["MultiPoint"]]
    assert append_flags == [False, True]
    assert result == {"layers": 1, "written": 2, "duplicates": 0}

def test_combined_schema_rejects_different_geometry_families(monkeypatch):
    sources = [Path("points.gdb"), Path("lines.gdb")]
    geometry_types = {
        sources[0]: "Point",
        sources[1]: "LineString",
    }

    def read_info(source, *, layer):
        return {
            "crs": "EPSG:28992",
            "fields": [],
            "dtypes": [],
            "geometry_type": geometry_types[source],
        }

    monkeypatch.setattr(gdb_combiner.pyogrio, "read_info", read_info)

    with pytest.raises(ValueError, match="onverenigbare geometrie-families"):
        gdb_combiner._combined_schema("objects", sources)
