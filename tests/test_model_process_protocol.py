from __future__ import annotations

import json
from pathlib import Path

from gui import model_worker
from gui.services.backend_adapter import BackendAdapter


def test_worker_rejects_unknown_mode_without_running_model(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    state = tmp_path / "state.json"
    result = tmp_path / "result.json"
    request.write_text(
        json.dumps({"mode": "unknown", "arguments": {}}),
        encoding="utf-8",
    )

    exit_code = model_worker.run(request, state, result)

    payload = json.loads(result.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert payload["ok"] is False
    assert "Unsupported worker mode" in payload["error"]


def test_progress_writer_replaces_state_atomically(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    report = model_worker._progress_writer(state)

    report(1, 9, "first")
    report(2, 9, "second")

    assert json.loads(state.read_text(encoding="utf-8")) == {
        "kind": "progress",
        "current": 2,
        "total": 9,
        "message": "second",
    }
    assert list(tmp_path.glob("*.tmp")) == []


def test_backend_json_reader_ignores_partial_state(tmp_path: Path) -> None:
    state = tmp_path / "state.json"
    state.write_text('{"kind":', encoding="utf-8")

    assert BackendAdapter._read_json(state) is None




def test_worker_control_directory_is_outside_disposable_temp(tmp_path: Path) -> None:
    temp_dir = tmp_path / "temp"

    control_root = BackendAdapter._worker_control_root(temp_dir)

    assert control_root == tmp_path.resolve() / ".gui_workers"
    assert control_root.parent == temp_dir.resolve().parent
    assert control_root != temp_dir.resolve()


def test_compute_process_configuration_is_a_noop_off_windows() -> None:
    model_worker._configure_compute_process()


def test_worker_command_output_can_be_inherited_when_stdout_is_available(monkeypatch) -> None:
    class Stream:
        def fileno(self):
            return 1

    monkeypatch.setattr("sys.stdout", Stream())
    assert BackendAdapter._can_inherit_stdout() is True


def test_worker_command_output_is_disabled_without_stdout(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdout", None)
    assert BackendAdapter._can_inherit_stdout() is False



def test_step2_code_detection_ignores_inactive_or_empty_codes(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        """
[proposed_highvoltage]
enabled = false
code = "STALE"

[[Elektriciteitskabel]]
fid = [1]
code = "   "
""".strip(),
        encoding="utf-8",
    )

    assert model_worker._has_step2_code_input(config) is False


def test_step2_code_detection_accepts_active_proposed_or_cable_code(tmp_path: Path) -> None:
    proposed = tmp_path / "proposed.toml"
    proposed.write_text(
        """
[proposed_highvoltage]
enabled = true
code = "HV-01"
""".strip(),
        encoding="utf-8",
    )
    cable = tmp_path / "cable.toml"
    cable.write_text(
        """
[proposed_highvoltage]
enabled = false
code = ""

[[Elektriciteitskabel]]
fid = [7, 8]
code = "CABLE-A"
""".strip(),
        encoding="utf-8",
    )

    assert model_worker._has_step2_code_input(proposed) is True
    assert model_worker._has_step2_code_input(cable) is True
