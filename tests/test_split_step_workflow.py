from pathlib import Path

import pytest

from gui.controller import WorkflowController
from gui.models import AppState, FeatureKey, WorkflowStage
from gui.services.configuration import ELECTRICITY_LAYER


class RootStub:
    def after(self, _delay, callback):
        callback()


def test_codes_can_only_be_assigned_to_post_step1_candidate_ids(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    candidate = FeatureKey("part1", ELECTRICITY_LAYER, 10, 101)
    rejected = FeatureKey("part1", ELECTRICITY_LAYER, 11, 102)
    state.stage = WorkflowStage.STEP1_READY
    state.step2_electricity_candidate_ids = {101}
    controller = WorkflowController(RootStub(), state)

    controller.assign_cable_group([candidate], "L09")

    assert state.cable_groups == {"L09": {candidate}}

    with pytest.raises(ValueError):
        controller.assign_cable_group([rejected], "L10")


def test_changing_step1_selection_invalidates_step1_candidates(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    conduit = FeatureKey("part1", "Waterleiding", 1, 1)
    state.features = {conduit: object()}
    state.selected_for_analysis = {conduit}
    state.stage = WorkflowStage.STEP1_READY
    state.step2_electricity_candidate_ids = {101}
    state.cable_groups = {
        "L09": {FeatureKey("part1", ELECTRICITY_LAYER, 10, 101)}
    }
    controller = WorkflowController(RootStub(), state)

    controller.set_analysis_selection([conduit], False)

    assert state.stage == WorkflowStage.PART1_READY
    assert state.step2_electricity_candidate_ids == set()
    assert state.cable_groups == {}


def test_step2_code_assignment_uses_candidate_id_without_loaded_map_feature(
    tmp_path: Path,
) -> None:
    state = AppState(project_root=tmp_path)
    state.stage = WorkflowStage.STEP1_READY
    state.step2_electricity_candidate_ids = {501}
    controller = WorkflowController(RootStub(), state)

    controller.assign_cable_group_ids([501], "L09")

    assigned = next(iter(state.cable_groups["L09"]))
    assert assigned.persistent_id == 501
    assert controller.group_for_id(501) == "L09"


def test_step2_code_reassignment_is_persistent_id_based(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    source_key = FeatureKey("part1", ELECTRICITY_LAYER, 9001, 501)
    rewritten_key = FeatureKey("part1", ELECTRICITY_LAYER, 42, 501)
    state.features = {source_key: object()}
    state.stage = WorkflowStage.STEP1_READY
    state.step2_electricity_candidate_ids = {501}
    controller = WorkflowController(RootStub(), state)

    controller.assign_cable_group_ids([501], "L09")
    controller.assign_cable_group_ids([501], "L10")

    assert "L09" not in state.cable_groups
    assert controller.group_for(rewritten_key) == "L10"


def test_step2_code_assignment_rejects_mixed_non_candidate_ids_atomically(
    tmp_path: Path,
) -> None:
    state = AppState(project_root=tmp_path)
    state.stage = WorkflowStage.STEP1_READY
    state.step2_electricity_candidate_ids = {501}
    controller = WorkflowController(RootStub(), state)

    with pytest.raises(ValueError, match="502"):
        controller.assign_cable_group_ids([501, 502], "L09")

    assert state.cable_groups == {}
