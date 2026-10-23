from __future__ import annotations

from pathlib import Path
from typing import Callable

from model.part2.select_emphasized_conduit import filter_influencers_from_selected_features
from model.part2.assign_electricity_cable_codes import (
    assign_electricity_cable_codes,
    assign_electricity_cable_codes_to_step1,
)
from model.part2.segment_features import segment_features
from model.part2.find_parallel_features import find_parallel_features
from model.part2.calculate_parallel_distance import calculate_parallel_distance
from model.part2.consecutive_parallel_features import consecutive_parallel_features
from model.part2.filter_features_run_length import filter_features_run_length
from model.part2.distance_for_run import create_run_exports
from model.part2.calculate_normative_distance_length import calculate_normative_distance_length
from model.part2.assign_distance_length_to_run import assign_distance_length_to_run
from model.part2.assign_correct_distance_to_unique_run_step import assign_correct_distance_to_unique_run
from model.part2.step1_NEN import step1_nen
from model.part2.step2_NEN import step2_nen
from model.id_lineage import validate_model_output_ids, validate_temp9_ids


KLIC_PROJECT_NAME = "KLIC_groenedijk"
ProgressCallback = Callable[[int, int, str], None]
CancellationCheck = Callable[[], None]


def print_step(number: int | str, description: str) -> None:
    print("\n============================================================")
    print(f"STEP {number}: {description}")
    print("============================================================")


def run_model_step1_pipeline(
    *,
    temp_dir: Path,
    selection_config: Path,
    cable_code_config: Path,
    progress: ProgressCallback | None = None,
    check_cancelled: CancellationCheck | None = None,
) -> Path:
    """Run the unchanged model sequence through NEN 3654 Step 1."""
    temp_dir = Path(temp_dir)
    filtering_output = temp_dir / "KLIC_temp9.gpkg"
    if not filtering_output.is_file():
        raise FileNotFoundError(f"Filtering output was not found: {filtering_output}")
    if not Path(selection_config).is_file():
        raise FileNotFoundError(f"Selection configuration not found: {selection_config}")
    if not Path(cable_code_config).is_file():
        raise FileNotFoundError(f"Cable-code configuration not found: {cable_code_config}")

    validate_temp9_ids(filtering_output)
    total = 12

    def begin(index: int, number: int | str, description: str) -> None:
        if check_cancelled:
            check_cancelled()
        print_step(number, description)
        if progress:
            progress(index - 1, total, description)

    # This established preprocessing step is retained. In the split GUI
    # workflow the config contains no cable groups yet, so it creates an empty
    # Code column without requiring the user to classify cables in advance.
    begin(1, "9B", "Codekolom voor elektriciteitskabels voorbereiden")
    cable_codes_output = temp_dir / "KLIC_temp9_codes.gpkg"
    assign_electricity_cable_codes(
        input_path=filtering_output,
        output_path=cable_codes_output,
        config_path=Path(cable_code_config),
        layer_name="Elektriciteitskabel",
        id_column="id",
    )

    begin(2, "9C", "Geselecteerde leidingen en beïnvloeders verwerken")
    selected_output = temp_dir / "KLIC_temp9_selected.gpkg"
    filter_influencers_from_selected_features(
        input_path=cable_codes_output,
        output_path=selected_output,
        config_path=Path(selection_config),
    )

    begin(3, 10, "Features segmenteren")
    step_10_output = temp_dir / "KLIC_temp10.gpkg"
    segment_features(input_path=selected_output, output_path=step_10_output)

    begin(4, 11, "Parallelle features zoeken")
    step_11_output = temp_dir / "KLIC_temp11.gpkg"
    find_parallel_features(input_path=step_10_output, output_path=step_11_output)

    begin(5, 12, "Parallelle afstand berekenen")
    step_12_output = temp_dir / "KLIC_temp12.gpkg"
    calculate_parallel_distance(input_path=step_11_output, output_path=step_12_output)

    begin(6, 13, "Opeenvolgende parallelle features bepalen")
    step_13_output = temp_dir / "KLIC_temp13.gpkg"
    consecutive_parallel_features(
        input_path=step_12_output,
        output_path=step_13_output,
        geometry_path=step_11_output,
    )

    begin(7, 14, "Features op runlengte filteren")
    step_14_output = temp_dir / "KLIC_temp14.gpkg"
    filter_features_run_length(input_path=step_13_output, output_path=step_14_output)

    begin(8, 15, "Afstand per run bepalen")
    step_15_output = temp_dir / "KLIC_temp15.gpkg"
    create_run_exports(
        input_path=step_14_output,
        distance_lines_path=step_12_output,
        output_path=step_15_output,
    )

    begin(9, 16, "Normatieve afstand en lengte berekenen")
    step_16_output = temp_dir / "KLIC_temp16.gpkg"
    calculate_normative_distance_length(input_path=step_14_output, output_path=step_16_output)

    begin(10, 17, "Afstand en lengte aan runs toewijzen")
    step_17_output = temp_dir / "KLIC_temp17.gpkg"
    assign_distance_length_to_run(
        source_data_path=step_14_output,
        proposed_hv_csv_path=temp_dir / "proposed_hv_segments_between_distance_lines_with_la.csv",
        electricity_cable_csv_path=temp_dir / "electriciteitskabel_segments_between_distance_lines_with_la.csv",
        output_data_path=step_17_output,
        support_data_path=step_11_output,
    )

    begin(11, 18, "Correcte afstand aan unieke runs toewijzen")
    step_18_output = temp_dir / "KLIC_temp18.gpkg"
    assign_correct_distance_to_unique_run(
        source_data_path=step_17_output,
        distance_lines_path=step_12_output,
        output_data_path=step_18_output,
    )

    begin(12, 19, "NEN 3654 stap 1 uitvoeren")
    step_19_output = temp_dir / "KLIC_temp19.gpkg"
    step1_nen(input_path=step_18_output, output_path=step_19_output)

    # A rerun of Step 1 invalidates any earlier Step 2 result.
    (temp_dir / "KLIC_temp19_codes.gpkg").unlink(missing_ok=True)
    (temp_dir / "KLIC_temp20.gpkg").unlink(missing_ok=True)

    validate_model_output_ids(
        temp9_path=filtering_output,
        output_path=step_19_output,
    )
    if progress:
        progress(total, total, "NEN 3654 stap 1 gereed")
    return step_19_output


def run_model_step2_pipeline(
    *,
    temp_dir: Path,
    cable_code_config: Path,
    progress: ProgressCallback | None = None,
    check_cancelled: CancellationCheck | None = None,
) -> Path:
    """Assign post-Step-1 cable Codes by persistent ID and run Step 2."""
    temp_dir = Path(temp_dir)
    filtering_output = temp_dir / "KLIC_temp9.gpkg"
    step_19_output = temp_dir / "KLIC_temp19.gpkg"
    if not filtering_output.is_file():
        raise FileNotFoundError(f"Filtering output was not found: {filtering_output}")
    if not step_19_output.is_file():
        raise FileNotFoundError(
            f"NEN 3654 Step 1 output was not found: {step_19_output}"
        )
    if not Path(cable_code_config).is_file():
        raise FileNotFoundError(f"Cable-code configuration not found: {cable_code_config}")

    total = 2

    def begin(index: int, number: int | str, description: str) -> None:
        if check_cancelled:
            check_cancelled()
        print_step(number, description)
        if progress:
            progress(index - 1, total, description)

    begin(1, "19B", "Codes aan resterende elektriciteitskabels toewijzen")
    coded_step1_output = temp_dir / "KLIC_temp19_codes.gpkg"
    assign_electricity_cable_codes_to_step1(
        input_path=step_19_output,
        output_path=coded_step1_output,
        config_path=Path(cable_code_config),
        id_column="id",
    )

    begin(2, 20, "NEN 3654 stap 2 uitvoeren")
    model_output = temp_dir / "KLIC_temp20.gpkg"
    step2_nen(input_path=coded_step1_output, output_path=model_output)

    validate_model_output_ids(
        temp9_path=filtering_output,
        output_path=model_output,
    )
    if progress:
        progress(total, total, "NEN 3654 stap 2 gereed")
    return model_output


# Backward-compatible combined runner for command-line and existing integrations.
# The split GUI uses the two phase-specific functions above.
def run_model_pipeline(
    *,
    temp_dir: Path,
    selection_config: Path,
    cable_code_config: Path,
    progress: ProgressCallback | None = None,
    check_cancelled: CancellationCheck | None = None,
    run_step2: bool = True,
) -> Path:
    step1_output = run_model_step1_pipeline(
        temp_dir=temp_dir,
        selection_config=selection_config,
        cable_code_config=cable_code_config,
        progress=progress,
        check_cancelled=check_cancelled,
    )
    if not run_step2:
        return step1_output

    # Preserve the established CLI behavior: Codes assigned on temp9 are
    # already propagated into temp19, so no second assignment pass is needed.
    if check_cancelled:
        check_cancelled()
    model_output = Path(temp_dir) / "KLIC_temp20.gpkg"
    step2_nen(input_path=step1_output, output_path=model_output)
    validate_model_output_ids(
        temp9_path=Path(temp_dir) / "KLIC_temp9.gpkg",
        output_path=model_output,
    )
    return model_output


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    temp_dir = project_dir / "data" / "KLIC" / KLIC_PROJECT_NAME / "temp"
    run_model_pipeline(
        temp_dir=temp_dir,
        selection_config=project_dir / "config" / "selection.toml",
        cable_code_config=project_dir / "config" / "config.toml",
    )


if __name__ == "__main__":
    main()
