from pathlib import Path
import sys
import types

if "pyogrio" not in sys.modules:
    pyogrio = types.ModuleType("pyogrio")
    pyogrio.__path__ = []
    pyogrio_errors = types.ModuleType("pyogrio.errors")
    pyogrio_errors.DataLayerError = type("DataLayerError", (Exception,), {})
    sys.modules["pyogrio"] = pyogrio
    sys.modules["pyogrio.errors"] = pyogrio_errors

import prepare_for_arcgis
import run_model


def test_model_runner_skips_only_step2_when_requested(tmp_path, monkeypatch) -> None:
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    (temp_dir / "KLIC_temp9.gpkg").touch()
    selection = tmp_path / "selection.toml"
    selection.touch()
    config = tmp_path / "config.toml"
    config.touch()

    calls = []
    model_functions = [
        "assign_electricity_cable_codes",
        "filter_influencers_from_selected_features",
        "segment_features",
        "find_parallel_features",
        "calculate_parallel_distance",
        "consecutive_parallel_features",
        "filter_features_run_length",
        "create_run_exports",
        "calculate_normative_distance_length",
        "assign_distance_length_to_run",
        "assign_correct_distance_to_unique_run",
    ]
    for name in model_functions:
        monkeypatch.setattr(run_model, name, lambda *args, _name=name, **kwargs: calls.append(_name))

    def fake_step1(*, input_path, output_path):
        calls.append("step1_nen")
        Path(output_path).touch()

    def fake_step2(*, input_path, output_path):
        calls.append("step2_nen")
        Path(output_path).touch()

    monkeypatch.setattr(run_model, "step1_nen", fake_step1)
    monkeypatch.setattr(run_model, "step2_nen", fake_step2)
    stale_step2 = temp_dir / "KLIC_temp20.gpkg"
    stale_step2.touch()

    result = run_model.run_model_pipeline(
        temp_dir=temp_dir,
        selection_config=selection,
        cable_code_config=config,
        run_step2=False,
    )

    assert result == temp_dir / "KLIC_temp19.gpkg"
    assert "step1_nen" in calls
    assert "step2_nen" not in calls
    assert not stale_step2.exists()


def test_arcgis_preparation_omits_and_clears_step2_outputs(tmp_path, monkeypatch) -> None:
    temp_dir = tmp_path / "temp"
    output_dir = tmp_path / "output"
    temp_dir.mkdir()
    output_dir.mkdir()

    calls = []

    def writer(name):
        def write(**kwargs):
            calls.append(name)
            Path(kwargs["output_path"]).touch()
        return write

    monkeypatch.setattr(prepare_for_arcgis, "combine_cleaned_dwg", writer("raw"))
    monkeypatch.setattr(prepare_for_arcgis, "combine_relevant_features", writer("filtered"))
    monkeypatch.setattr(prepare_for_arcgis, "filter_klic_nopass_step1", writer("step1_no_pass"))
    monkeypatch.setattr(prepare_for_arcgis, "filter_klic_step1", writer("step1"))
    monkeypatch.setattr(prepare_for_arcgis, "filter_klic_nopass_step2", writer("step2_no_pass"))
    monkeypatch.setattr(prepare_for_arcgis, "filter_klic_step2", writer("step2"))

    stale_no_pass = output_dir / "KLIC_no_pass_step2.gpkg"
    stale_results = output_dir / "KLIC_results_step2.gpkg"
    stale_no_pass.touch()
    stale_results.touch()

    outputs = prepare_for_arcgis.prepare_for_arcgis_online(
        temp_dir=temp_dir,
        output_dir=output_dir,
        include_step2=False,
    )

    assert [path.name for path in outputs] == [
        "KLIC_raw_cleaned.gpkg",
        "KLIC_filtered.gpkg",
        "KLIC_no_pass_step1.gpkg",
        "KLIC_results_step1.gpkg",
    ]
    assert calls == ["raw", "filtered", "step1_no_pass", "step1"]
    assert not stale_no_pass.exists()
    assert not stale_results.exists()
