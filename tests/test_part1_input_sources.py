from pathlib import Path

from gui.models import AppState
from gui.services.validation import validate_part1_input


def _make_file_gdb(path: Path) -> Path:
    path.mkdir()
    (path / "a00000001.gdbtable").write_bytes(b"table")
    return path


def test_part1_validation_accepts_file_gdb_directory(tmp_path):
    source = _make_file_gdb(tmp_path / "input.gdb")

    valid, message = validate_part1_input(source)

    assert valid is True
    assert "File Geodatabase" in message


def test_part1_validation_rejects_empty_gdb_directory(tmp_path):
    source = tmp_path / "empty.gdb"
    source.mkdir()

    valid, message = validate_part1_input(source)

    assert valid is False
    assert "FileGDB-tabellen" in message


def test_app_state_accepts_gdb_as_part1_input(tmp_path):
    source = _make_file_gdb(tmp_path / "input.gdb")
    state = AppState(project_root=tmp_path, input_path=source)

    assert state.input_valid is True


