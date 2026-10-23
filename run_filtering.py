from __future__ import annotations

from pathlib import Path
from typing import Callable
import tomllib

from model.part1.rewrite_gdb_to_gpkg import rewrite_gdb_to_gpkg
from model.part1.harmonize_units_materials import harmonize_units_materials
from model.part1.reorder_utility_types_and_vertical import reorder_utility_types_and_vertical
from model.part1.remove_duplicates import remove_duplicates
from model.part1.combine_conduit_features import combine_conduit_features
from model.part1.optional_add_proposed_hv import add_proposed_highvoltage
from model.part1.filter_relevant_features_properties import filter_relevant_features_properties
from model.part1.filter_relevant_features_NEN import filter_relevant_features_NEN
from model.part1.combine_electricity_features import combine_electricity_features
from model.id_lineage import (
    assign_persistent_layer_ids,
    propagate_ids_to_matching_layers,
)


KLIC_PROJECT_NAME = "KLIC_groenedijk"
INCLUDE_PROPOSED_HV = False

ProgressCallback = Callable[[int, int, str], None]
CancellationCheck = Callable[[], None]


def print_step(number: int, description: str) -> None:
    print("\n============================================================")
    print(f"STEP {number}: {description}")
    print("============================================================")


# GUI INTEGRATION CHANGE: parameterized orchestration keeps all GIS calculations
# in their existing functions while allowing the GUI to supply an input GPKG,
# isolated runtime paths, status callbacks, and cooperative cancellation.
def run_filtering_pipeline(
    *,
    input_path: Path,
    temp_dir: Path,
    config_path: Path,
    proposed_hv_path: Path | None = None,
    progress: ProgressCallback | None = None,
    check_cancelled: CancellationCheck | None = None,
) -> Path:
    input_path = Path(input_path)
    temp_dir = Path(temp_dir)
    config_path = Path(config_path)
    temp_dir.mkdir(parents=True, exist_ok=True)

    if not input_path.exists():
        raise FileNotFoundError(f"KLIC input datasource not found: {input_path}")
    if not config_path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)
    proposed_hv_config = config["proposed_highvoltage"]

    def begin(number: int, description: str) -> None:
        if check_cancelled:
            check_cancelled()
        print_step(number, description)
        if progress:
            progress(number - 1, 9, description)

    begin(1, "KLIC-bron naar werk-GeoPackage schrijven")
    step_1_output = temp_dir / "KLIC.gpkg"
    rewrite_gdb_to_gpkg(input_path=input_path, output_path=step_1_output)

    begin(2, "Eenheden en materialen harmoniseren")
    step_2_output = temp_dir / "KLIC_temp2.gpkg"
    harmonize_units_materials(input_path=step_1_output, output_path=step_2_output)

    begin(3, "Nettypen en verticale posities ordenen")
    step_3_output = temp_dir / "KLIC_temp3.gpkg"
    reorder_utility_types_and_vertical(input_path=step_2_output, output_path=step_3_output)

    begin(4, "Dubbele features verwijderen")
    step_4_output = temp_dir / "KLIC_temp4.gpkg"
    remove_duplicates(input_path=step_3_output, output_path=step_4_output)

    begin(5, "Mantelbuisfeatures combineren")
    step_5_output = temp_dir / "KLIC_temp5.gpkg"
    combine_conduit_features(input_path=step_4_output, output_path=step_5_output)

    begin(6, "Voorgestelde hoogspanning verwerken")
    if proposed_hv_config.get("enabled", INCLUDE_PROPOSED_HV):
        if proposed_hv_path is None or not Path(proposed_hv_path).is_file():
            raise FileNotFoundError(
                "Voorgestelde hoogspanning is ingeschakeld, maar het bronbestand ontbreekt."
            )
        step_6_output = temp_dir / "KLIC_temp6.gpkg"
        add_proposed_highvoltage(
            input_path=step_5_output,
            proposed_hv_path=Path(proposed_hv_path),
            output_path=step_6_output,
            code=proposed_hv_config["code"],
            vertical_position=proposed_hv_config["vertical_position"],
            work_voltage=proposed_hv_config["work_voltage"],
        )
        step_6_result = step_6_output
    else:
        print("STEP 6 skipped through runtime config")
        step_6_result = step_5_output

    begin(7, "Relevante features op eigenschappen filteren")
    step_7_output = temp_dir / "KLIC_temp7.gpkg"
    filter_relevant_features_properties(input_path=step_6_result, output_path=step_7_output)

    begin(8, "Relevante features volgens NEN 3654 filteren")
    step_8_output = temp_dir / "KLIC_temp8.gpkg"
    filter_relevant_features_NEN(input_path=step_7_output, output_path=step_8_output)

    begin(9, "Elektriciteitsfeatures combineren")
    filtering_output = temp_dir / "KLIC_temp9.gpkg"
    combine_electricity_features(input_path=step_8_output, output_path=filtering_output)

    assigned_counts = assign_persistent_layer_ids(filtering_output)
    print(
        "Assigned persistent layer-specific IDs in temp9: "
        f"{sum(assigned_counts.values())} features across "
        f"{len(assigned_counts)} layers."
    )

    # KLIC_raw_cleaned intentionally uses influenced layers from temp5.
    # Copy the already-assigned temp9 IDs back to matching temp5 records
    # without assigning or renumbering anything there.
    propagate_ids_to_matching_layers(
        source_path=filtering_output,
        target_path=step_5_output,
        layer_names=(
            "OlieGasChemicalienPijpleiding",
            "Overig",
            "Waterleiding",
            "Rioolleiding",
            "ThermischePijpleiding",
        ),
    )

    if progress:
        progress(9, 9, "Part 1 gereed")
    return filtering_output


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    data_dir = project_dir / "data" / "KLIC" / KLIC_PROJECT_NAME
    run_filtering_pipeline(
        input_path=data_dir / "GIS" / "output_verwerkt.gdb",
        temp_dir=data_dir / "temp",
        config_path=project_dir / "config" / "config.toml",
        proposed_hv_path=(
            project_dir
            / "data"
            / "Proposed_Highvoltage"
            / "Proposed_Highvoltage_Groenedijk.shp"
        ),
    )


if __name__ == "__main__":
    main()
