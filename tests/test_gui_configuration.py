from pathlib import Path
import tomllib

from gui.models import FeatureKey, ProposedHighVoltageConfig
from gui.services.configuration import write_runtime_config, write_runtime_selection
from gui.services.validation import validate_geopackage


def test_runtime_toml_matches_backend_contract(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    selection_path = tmp_path / "selection.toml"
    proposed = ProposedHighVoltageConfig(
        enabled=False, code='L"09', vertical_position="underground",
        work_voltage=110000,
    )
    groups = {
        "12": {
            FeatureKey("part1", "Elektriciteitskabel", 9004, 104),
            FeatureKey("part1", "Elektriciteitskabel", 9005, 105),
        }
    }
    selected = {
        FeatureKey("part1", "Waterleiding", 7003, 3),
        FeatureKey("part1", "OlieGasChemicalienPijpleiding", 7008, 8),
    }

    write_runtime_config(config_path, proposed, groups)
    write_runtime_selection(
        selection_path,
        selected,
        {"OlieGasChemicalienPijpleiding"},
    )

    with config_path.open("rb") as stream:
        config = tomllib.load(stream)
    with selection_path.open("rb") as stream:
        selection = tomllib.load(stream)

    assert config["proposed_highvoltage"]["enabled"] is False
    assert config["proposed_highvoltage"]["code"] == 'L"09'
    assert config["Elektriciteitskabel"][0] == {"id": [104, 105], "code": "12"}
    assert selection["Waterleiding"]["ids"] == [3]
    assert selection["OlieGasChemicalienPijpleiding"]["ids"] == [8]
    assert selection["Rioolleiding"]["ids"] == []
    assert selection["Waterleiding"]["combine"] is False
    assert selection["OlieGasChemicalienPijpleiding"]["combine"] is True


def test_geopackage_validation_rejects_wrong_extension(tmp_path: Path) -> None:
    path = tmp_path / "input.txt"
    path.write_text("not a gpkg", encoding="utf-8")
    valid, message = validate_geopackage(path)
    assert valid is False
    assert ".gpkg" in message


def test_backend_reads_explicit_influencer_selection(tmp_path: Path) -> None:
    from model.part2.select_emphasized_conduit import load_id_selection

    selection_path = tmp_path / "selection.toml"
    selection_path.write_text(
        """
[Waterleiding]
ids = [3]

[ThermischePijpleiding]
ids = []

[Rioolleiding]
ids = []

[OlieGasChemicalienPijpleiding]
ids = []

[Overig]
ids = []

[Elektriciteitskabel]
ids = [9, 10]

[proposed_highvoltage]
ids = []
""".strip(),
        encoding="utf-8",
    )

    selection = load_id_selection(selection_path)

    assert selection["Elektriciteitskabel"]["ids"] == {9, 10}
    assert selection["proposed_highvoltage"]["ids"] == set()
    assert selection["Waterleiding"]["combine"] is False
