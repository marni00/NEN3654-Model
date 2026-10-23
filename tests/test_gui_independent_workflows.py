from pathlib import Path
import sqlite3

from gui.controller import WorkflowController
from gui.models import AppState, FeatureKey
from gui.services.configuration import (
    AUTO_INCLUDED_LAYERS,
    ELECTRICITY_LAYER,
    PROPOSED_HV_LAYER,
    write_runtime_selection,
)


class RootStub:
    def after(self, _delay, callback):
        callback()


class RepositoryStub:
    def __init__(self):
        self.loaded_path = None

    def load_dataset(self, path, dataset):
        self.loaded_path = Path(path)
        assert dataset == "part1"
        return []


def _create_minimal_gpkg(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE gpkg_contents "
            "(table_name TEXT, data_type TEXT, identifier TEXT)"
        )
        connection.execute(
            "INSERT INTO gpkg_contents VALUES (?, ?, ?)",
            ("Elektriciteitskabel", "features", "Elektriciteitskabel"),
        )


def test_part2_input_is_imported_into_isolated_workspace(tmp_path: Path) -> None:
    source_dir = tmp_path / "earlier_run" / "temp"
    source_dir.mkdir(parents=True)
    temp9 = source_dir / "KLIC_temp9.gpkg"
    temp5 = source_dir / "KLIC_temp5.gpkg"
    _create_minimal_gpkg(temp9)
    temp5.write_bytes(b"required intermediate")

    repository = RepositoryStub()
    state = AppState(project_root=tmp_path / "project")
    controller = WorkflowController(
        RootStub(), state, repository=repository
    )

    valid, _message = controller.choose_part2_input(temp9)

    assert valid is True
    assert state.workspace_dir is not None
    assert state.part2_input_path == state.workspace_dir / "temp" / "KLIC_temp9.gpkg"
    assert state.part2_input_path.is_file()
    assert (state.workspace_dir / "temp" / "KLIC_temp5.gpkg").is_file()
    assert repository.loaded_path == state.workspace_dir / "temp" / "KLIC_temp9.gpkg"


def test_part2_input_reuses_its_existing_gui_run_workspace(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    workspace = project_root / "data" / "gui_runs" / "klic_20261009_120000_000000"
    temp_dir = workspace / "temp"
    temp_dir.mkdir(parents=True)
    (workspace / "config").mkdir()
    (workspace / "output").mkdir()
    temp9 = temp_dir / "KLIC_temp9.gpkg"
    temp5 = temp_dir / "KLIC_temp5.gpkg"
    _create_minimal_gpkg(temp9)
    temp5.write_bytes(b"required intermediate")
    runs_before = set((project_root / "data" / "gui_runs").iterdir())

    repository = RepositoryStub()
    state = AppState(project_root=project_root)
    controller = WorkflowController(
        RootStub(), state, repository=repository
    )

    valid, _message = controller.choose_part2_input(temp9)

    assert valid is True
    assert state.workspace_dir == workspace
    assert state.part2_input_path == temp9
    assert repository.loaded_path == temp9
    assert set((project_root / "data" / "gui_runs").iterdir()) == runs_before


def test_all_gui_selection_is_written_to_selection_toml(tmp_path: Path) -> None:
    assert AUTO_INCLUDED_LAYERS == (ELECTRICITY_LAYER, PROPOSED_HV_LAYER)
    selected = {
        FeatureKey("part1", "Waterleiding", 4004, 4),
        FeatureKey("part1", ELECTRICITY_LAYER, 9009, 9),
        FeatureKey("part1", PROPOSED_HV_LAYER, 2002, 2),
    }
    output = tmp_path / "selection.toml"

    write_runtime_selection(output, selected)
    content = output.read_text(encoding="utf-8")

    assert "[Waterleiding]\nids = [4]" in content
    assert "[Elektriciteitskabel]\nids = [9]" in content
    assert "[proposed_highvoltage]\nids = [2]" in content


def test_bulk_pipeline_selection_can_be_selected_and_cleared(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    water_1 = FeatureKey("part1", "Waterleiding", 1)
    water_2 = FeatureKey("part1", "Waterleiding", 2)
    electricity = FeatureKey("part1", ELECTRICITY_LAYER, 3)
    state.features = {
        water_1: object(),
        water_2: object(),
        electricity: object(),
    }
    controller = WorkflowController(RootStub(), state)

    controller.set_layer_analysis_selection("Waterleiding", True)

    assert state.selected_for_analysis == {water_1, water_2}

    controller.set_analysis_selection((water_1,), False)

    assert state.selected_for_analysis == {water_2}

    controller.clear_analysis_selection()

    assert state.selected_for_analysis == set()


def test_influencer_layers_can_be_added_to_and_removed_from_selection(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    electricity = FeatureKey("part1", ELECTRICITY_LAYER, 3)
    controller = WorkflowController(RootStub(), state)

    controller.set_analysis_selection((electricity,), True)
    assert state.selected_for_analysis == {electricity}

    controller.set_analysis_selection((electricity,), False)
    assert state.selected_for_analysis == set()


def test_default_selection_preselects_but_does_not_lock_influencers(tmp_path: Path) -> None:
    from gui.models import MapFeature
    from gui.services.configuration import CONDUIT_LAYERS

    electricity = FeatureKey("part1", ELECTRICITY_LAYER, 3)
    proposed = FeatureKey("part1", PROPOSED_HV_LAYER, 4)
    conduit = FeatureKey("part1", CONDUIT_LAYERS[0], 5)
    features = [
        MapFeature(electricity, None, {}, "LineString"),
        MapFeature(proposed, None, {}, "LineString"),
        MapFeature(conduit, None, {}, "LineString"),
    ]

    selected = WorkflowController._default_analysis_selection(features)

    assert selected == {electricity, proposed}


def test_overlapping_map_results_can_be_cycled_from_feature_pane_state(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    first = FeatureKey("part2_step1", "result", 1)
    second = FeatureKey("part2_step1", "result", 2)
    state.features = {first: object(), second: object()}
    controller = WorkflowController(RootStub(), state)

    controller.select_overlapping_features([first, first, second])

    assert state.overlapping_features == (first, second)
    assert state.selected_feature == first

    controller.cycle_overlapping_feature(1)
    assert state.selected_feature == second

    controller.cycle_overlapping_feature(1)
    assert state.selected_feature == first

    controller.select_feature(None)
    assert state.overlapping_features == ()
