from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from gui.services.map_server import LocalMapServer


def test_map_shell_and_geojson_are_served_from_same_loopback_origin(tmp_path: Path) -> None:
    web_root = tmp_path / "web"
    web_root.mkdir()
    for name in ("map.html", "map.js", "map.css"):
        (web_root / name).write_text(name, encoding="utf-8")

    server = LocalMapServer(web_root)
    try:
        assert server.map_url.startswith("http://127.0.0.1:")
        with urlopen(server.map_url, timeout=2) as response:
            assert response.read() == b"map.html"

        payload = json.dumps(
            {
                "type": "FeatureCollection",
                "features": [],
            }
        )
        data_url = server.publish_geojson("part1", "Waterleiding", payload)
        with urlopen(data_url, timeout=2) as response:
            assert response.headers["Content-Type"].startswith(
                "application/geo+json"
            )
            assert response.read().decode("utf-8") == payload
    finally:
        server.close()


def test_map_server_does_not_expose_files_outside_allowlist(tmp_path: Path) -> None:
    web_root = tmp_path / "web"
    web_root.mkdir()
    for name in ("map.html", "map.js", "map.css"):
        (web_root / name).write_text(name, encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")

    server = LocalMapServer(web_root)
    try:
        try:
            urlopen(f"{server.base_url}/../secret.txt", timeout=2)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("Path traversal request should be rejected")
    finally:
        server.close()


def test_map_javascript_loads_geojson_by_url() -> None:
    project_root = Path(__file__).resolve().parents[1]
    javascript = (project_root / "gui" / "web" / "map.js").read_text(
        encoding="utf-8"
    )
    assert "setLayerUrl(dataset, layer, url)" in javascript
    assert 'data: url' in javascript
    assert "setLayerData(dataset, layer, data)" not in javascript


def test_map_server_can_release_all_published_payloads(tmp_path: Path) -> None:
    web_root = tmp_path / "web"
    web_root.mkdir()
    for name in ("map.html", "map.js", "map.css"):
        (web_root / name).write_text(name, encoding="utf-8")

    server = LocalMapServer(web_root)
    try:
        data_url = server.publish_geojson(
            "part1",
            "Waterleiding",
            '{"type":"FeatureCollection","features":[]}',
        )
        server.clear_geojson()

        try:
            urlopen(data_url, timeout=2)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("Cleared GeoJSON payload should not remain available")
    finally:
        server.close()


def test_map_supports_empty_click_deselection_and_persistent_base_map() -> None:
    project_root = Path(__file__).resolve().parents[1]
    javascript = (project_root / "gui" / "web" / "map.js").read_text(
        encoding="utf-8"
    )
    panel = (project_root / "gui" / "views" / "map_panel.py").read_text(
        encoding="utf-8"
    )

    assert "bridge.clearFeatureSelection()" in javascript
    assert 'id: "base-background"' in javascript
    assert 'self.web.load(QUrl("about:blank"))' not in panel
    assert "self.controller.select_feature(None)" in panel


def test_map_payload_excludes_full_feature_attributes() -> None:
    project_root = Path(__file__).resolve().parents[1]
    repository = (project_root / "gui" / "services" / "gis_repository.py").read_text(
        encoding="utf-8"
    )

    assert "Full attributes remain in MapFeature" in repository
    assert "for key, value in attributes.items()" not in repository



def test_map_zoom_expressions_and_overlapping_feature_cycle_are_validated() -> None:
    project_root = Path(__file__).resolve().parents[1]
    javascript = (project_root / "gui" / "web" / "map.js").read_text(
        encoding="utf-8"
    )

    assert 'const casingLineWidth = [\n    "interpolate"' in javascript
    assert '"+"\n    renderedLineWidth' not in javascript
    assert "uniqueClickableFeatures" in javascript
    assert "selectOverlappingFeatures" in javascript
    assert "Gebruik Vorige en Volgende in het featurepaneel." in javascript
    assert "clickCycle.index + 1" not in javascript
    assert 'map.moveLayer(layerId)' in javascript
    assert 'id: "selection-line-casing"' in javascript


def test_overlap_navigation_and_arcgis_hints_are_exposed_in_gui() -> None:
    project_root = Path(__file__).resolve().parents[1]
    panel = (project_root / "gui" / "views" / "feature_panel.py").read_text(
        encoding="utf-8"
    )
    setup = (project_root / "gui" / "views" / "setup_panel.py").read_text(
        encoding="utf-8"
    )

    assert 'QPushButton("Vorige")' in panel
    assert 'QPushButton("Volgende")' in panel
    assert "cycle_overlapping_feature" in panel
    assert 'Sweco: "https://ggc.maps.arcgis.com"' in setup
    assert '"voornaam.achternaal@sweco.nl"' in setup
