from pathlib import Path

from gui.models import AppState, FeatureKey, MapFeature
from gui.services.gis_repository import GISRepository
from gui.services.symbology import Symbology


def test_available_datasets_include_unloaded_result_sources(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    key = FeatureKey("part1", "Waterleiding", 1)
    state.features[key] = MapFeature(key, None, {}, "LineString")
    state.result_sources["part2_step1"] = tmp_path / "KLIC_results_step1.gpkg"

    assert state.available_datasets == {"part1", "part2_step1"}


def test_part2_outputs_are_discovered_without_reading_geometries(tmp_path: Path) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    expected = output_dir / "KLIC_no_pass_step2.gpkg"
    expected.write_bytes(b"placeholder")

    sources = GISRepository().discover_part2_outputs(output_dir)

    assert sources == {"part2_no_pass_step2": expected}


def test_symbology_uses_voltage_class_from_json(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "symbology.json"
    path.write_text(
        """
        {
          "default_color": [80, 80, 80],
          "layer_colors": {"Elektriciteitskabel": [250, 150, 5]},
          "voltage_class": {
            "field": "thema",
            "classes": {
              "hoogspanning": {
                "label": "Hoogspanning",
                "color": [215, 60, 10],
                "style": "esriSLSSolid",
                "width": 1.0
              }
            }
          }
        }
        """,
        encoding="utf-8",
    )
    key = FeatureKey("part1", "Elektriciteitskabel", 3)
    feature = MapFeature(
        key,
        None,
        {"thema": "hoogspanning"},
        "LineString",
    )

    assert Symbology(path).feature_color(feature) == "#D73C0A"



def test_symbology_uses_infrastructure_type_for_result_layers(tmp_path: Path) -> None:
    path = tmp_path / "symbology.json"
    path.write_text(
        """
        {
          "default_color": [80, 80, 80],
          "layer_colors": {
            "OlieGasChemicalienPijpleiding": [110, 160, 95],
            "Elektriciteitskabel": [250, 150, 5]
          },
          "voltage_class": {
            "field": "thema",
            "classes": {
              "hoogspanning": {
                "label": "Hoogspanning",
                "color": [215, 60, 10],
                "style": "esriSLSSolid",
                "width": 1.0
              }
            }
          },
          "standard_line": {"width": 1.0}
        }
        """,
        encoding="utf-8",
    )
    result_key = FeatureKey("part2_step2", "result_inductive", 7)
    pipeline = MapFeature(
        result_key,
        None,
        {"infrastructure_type": "OlieGasChemicalienPijpleiding"},
        "LineString",
    )
    cable = MapFeature(
        result_key,
        None,
        {
            "infrastructure_type": "Elektriciteitskabel",
            "thema": "hoogspanning",
        },
        "LineString",
    )
    symbology = Symbology(path)

    assert symbology.feature_color(pipeline) == "#6EA05F"
    assert symbology.feature_color(cable) == "#D73C0A"
    assert symbology.line_width(cable) == 1


def test_symbology_uses_voltage_class_alias_for_proposed_hv(tmp_path: Path) -> None:
    path = tmp_path / "symbology.json"
    path.write_text(
        """
        {
          "default_color": [80, 80, 80],
          "layer_colors": {"proposed_highvoltage": [210, 35, 110]},
          "voltage_class": {
            "field": "thema",
            "classes": {
              "hoogspanning": {
                "label": "Hoogspanning",
                "color": [215, 60, 10],
                "width": 1.0
              }
            }
          }
        }
        """,
        encoding="utf-8",
    )
    key = FeatureKey("part1", "proposed_highvoltage", 1)
    feature = MapFeature(key, None, {"voltage_class": "hoogspanning"}, "LineString")
    assert Symbology(path).feature_color(feature) == "#D73C0A"

def test_result_layer_visibility_is_independent_per_layer(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    state.result_sources["part2_step1"] = tmp_path / "KLIC_results_step1.gpkg"
    state.result_layers["part2_step1"] = ("Waterleiding", "Elektriciteitskabel")
    state.visible_result_layers.add(("part2_step1", "Waterleiding"))

    water = FeatureKey("part2_step1", "Waterleiding", 1)
    cable = FeatureKey("part2_step1", "Elektriciteitskabel", 2)

    assert state.feature_is_visible(water) is True
    assert state.feature_is_visible(cable) is False


def test_part1_visibility_remains_dataset_wide(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    key = FeatureKey("part1", "Waterleiding", 1)

    assert state.feature_is_visible(key) is False
    state.visible_datasets.add("part1")
    assert state.feature_is_visible(key) is True

