from pathlib import Path

from gui.models import AppState
from gui.services.configuration import load_step2_codes


def test_load_step2_codes_strips_deduplicates_and_preserves_order(tmp_path: Path) -> None:
    table = tmp_path / "Step2_Table.csv"
    table.write_text(
        " Code;K1\n L09;1\nL10;2\nL09;3\n;;;;\n",
        encoding="utf-8",
    )

    assert load_step2_codes(table) == ("L09", "L10")


def test_project_step2_table_provides_dropdown_codes() -> None:
    codes = load_step2_codes(
        Path(__file__).resolve().parents[1] / "utils" / "Step2_Table.csv"
    )

    assert "K01" in codes
    assert "L09" in codes
    assert "T02" in codes


def test_app_state_has_separate_step1_output_input_path(tmp_path: Path) -> None:
    state = AppState(project_root=tmp_path)
    step1_output = tmp_path / "KLIC_temp19.gpkg"

    state.step2_input_path = step1_output

    assert state.step2_input_path == step1_output
