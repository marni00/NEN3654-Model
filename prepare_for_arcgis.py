from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Callable

import pyogrio

from model.post.filtered_klic_relevant import combine_relevant_features
from model.post.raw_klic_cleaned import combine_cleaned_dwg
from model.post.filtered_klic_step1_result_nopass import filter_klic_nopass_step1
from model.post.filtered_klic_step2_result_nopass import filter_klic_nopass_step2
from model.post.filtered_klic_step1_result import filter_klic_step1
from model.post.filtered_klic_step2_result import filter_klic_step2


KLIC_PROJECT_NAME = "KLIC_groenedijk"
ProgressCallback = Callable[[int, int, str], None]
CancellationCheck = Callable[[], None]


def _population_keys(
    file_path: Path,
    layer_name: str,
) -> Counter[tuple[str, ...]]:
    """Return stable logical identities for a dissolved assessment layer."""
    available_layers = set(pyogrio.list_layers(file_path)[:, 0])
    if layer_name not in available_layers:
        raise RuntimeError(
            f"Expected layer '{layer_name}' was not created in "
            f"'{file_path}'."
        )

    fields = set(pyogrio.read_info(file_path, layer=layer_name)["fields"])
    identity_columns = [
        column
        for column in (
            "influenced_run_id",
            "infrastructure_type",
            "source_layer",
            "feature_id",
            "id",
            "run_id",
        )
        if column in fields
    ]
    if "influenced_run_id" not in identity_columns:
        raise RuntimeError(
            f"Layer '{layer_name}' in '{file_path}' does not contain "
            "'influenced_run_id'; its Step 2 population cannot be validated."
        )

    frame = pyogrio.read_dataframe(
        file_path,
        layer=layer_name,
        columns=identity_columns,
        read_geometry=False,
    )

    def normalized(value) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return "" if text.casefold() in {"", "nan", "none", "<na>"} else text

    keys: Counter[tuple[str, ...]] = Counter()
    for row in frame.to_dict("records"):
        run_id = normalized(row.get("influenced_run_id"))
        infrastructure = normalized(row.get("infrastructure_type"))
        if run_id:
            key = ("run", run_id, infrastructure)
        else:
            key = (
                "feature",
                infrastructure,
                normalized(row.get("source_layer")),
                normalized(row.get("feature_id")),
                normalized(row.get("id")),
                normalized(row.get("run_id")),
            )
        keys[key] += 1
    return keys


def _format_population_examples(
    differences: Counter[tuple[str, ...]],
    *,
    limit: int = 5,
) -> str:
    examples = []
    for key, count in differences.items():
        examples.append(f"{key!r} x{count}")
        if len(examples) >= limit:
            break
    return "; ".join(examples) if examples else "none"


def _validate_step2_population(
    step1_no_pass_path: Path,
    step2_results_path: Path,
) -> None:
    """Require Step 2 to use exactly the Step 1 inductive no-pass population."""
    step1_population = _population_keys(
        step1_no_pass_path,
        "no_pass_inductive",
    )
    step2_population = _population_keys(
        step2_results_path,
        "result_inductive",
    )
    if step2_population != step1_population:
        missing = step1_population - step2_population
        unexpected = step2_population - step1_population
        raise RuntimeError(
            "Step 2 population mismatch: "
            f"'result_inductive' contains {sum(step2_population.values())} "
            "logical features, but Step 1 'no_pass_inductive' contains "
            f"{sum(step1_population.values())}. "
            "Missing from Step 2: "
            f"{_format_population_examples(missing)}. "
            "Unexpected in Step 2: "
            f"{_format_population_examples(unexpected)}. "
            "Step 2 must build exactly on the Step 1 inductive "
            "no-pass population."
        )
    print(
        "Validated Step 2 population: "
        f"{sum(step2_population.values())} logical features, equal to "
        "Step 1 inductive no-pass."
    )


def _begin(
    *,
    index: int,
    total: int,
    description: str,
    progress: ProgressCallback | None,
    check_cancelled: CancellationCheck | None,
) -> None:
    if check_cancelled:
        check_cancelled()
    print("\n============================================================")
    print(f"POST STEP {index}: {description}")
    print("============================================================")
    if progress:
        progress(index - 1, total, description)


def prepare_step1_for_arcgis_online(
    *,
    temp_dir: Path,
    output_dir: Path,
    progress: ProgressCallback | None = None,
    check_cancelled: CancellationCheck | None = None,
) -> list[Path]:
    """Create the four readable datasets available after NEN Step 1."""
    temp_dir = Path(temp_dir)
    output_dir = Path(output_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    total = 4
    outputs: list[Path] = []

    _begin(index=1, total=total, description="Opgeschoonde KLIC-data combineren",
           progress=progress, check_cancelled=check_cancelled)
    target = output_dir / "KLIC_raw_cleaned.gpkg"
    combine_cleaned_dwg(
        temp5_path=temp_dir / "KLIC_temp5.gpkg",
        temp9_path=temp_dir / "KLIC_temp9.gpkg",
        output_path=target,
    )
    outputs.append(target)

    _begin(index=2, total=total, description="Relevante NEN 3654-features combineren",
           progress=progress, check_cancelled=check_cancelled)
    target = output_dir / "KLIC_filtered.gpkg"
    combine_relevant_features(
        temp18_path=temp_dir / "KLIC_temp18.gpkg",
        output_path=target,
    )
    outputs.append(target)

    _begin(index=3, total=total, description="No-pass-resultaten van stap 1 selecteren",
           progress=progress, check_cancelled=check_cancelled)
    target = output_dir / "KLIC_no_pass_step1.gpkg"
    filter_klic_nopass_step1(
        temp19_path=temp_dir / "KLIC_temp19.gpkg",
        output_path=target,
    )
    outputs.append(target)

    _begin(index=4, total=total, description="Alle resultaten van stap 1 selecteren",
           progress=progress, check_cancelled=check_cancelled)
    target = output_dir / "KLIC_results_step1.gpkg"
    filter_klic_step1(
        temp19_path=temp_dir / "KLIC_temp19.gpkg",
        output_path=target,
    )
    outputs.append(target)

    # Step 1 reruns invalidate old Step 2 exports.
    (output_dir / "KLIC_no_pass_step2.gpkg").unlink(missing_ok=True)
    (output_dir / "KLIC_results_step2.gpkg").unlink(missing_ok=True)

    if progress:
        progress(total, total, "Resultaten van NEN 3654 stap 1 gereed")
    return outputs


def prepare_step2_for_arcgis_online(
    *,
    temp_dir: Path,
    output_dir: Path,
    progress: ProgressCallback | None = None,
    check_cancelled: CancellationCheck | None = None,
) -> list[Path]:
    """Create the two readable datasets added by NEN Step 2."""
    temp_dir = Path(temp_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    total = 2
    outputs: list[Path] = []

    step1_no_pass = output_dir / "KLIC_no_pass_step1.gpkg"
    if not step1_no_pass.is_file():
        step1_source = temp_dir / "KLIC_temp19.gpkg"
        if not step1_source.is_file():
            raise FileNotFoundError(
                "The Step 1 model output is required before Step 2 can be prepared: "
                f"{step1_source}"
            )
        filter_klic_nopass_step1(
            temp19_path=step1_source,
            output_path=step1_no_pass,
        )

    _begin(index=1, total=total, description="No-pass-resultaten van stap 2 selecteren",
           progress=progress, check_cancelled=check_cancelled)
    step2_no_pass = output_dir / "KLIC_no_pass_step2.gpkg"
    filter_klic_nopass_step2(
        temp20_path=temp_dir / "KLIC_temp20.gpkg",
        output_path=step2_no_pass,
    )
    outputs.append(step2_no_pass)

    _begin(index=2, total=total, description="Alle resultaten van stap 2 selecteren",
           progress=progress, check_cancelled=check_cancelled)
    step2_results = output_dir / "KLIC_results_step2.gpkg"
    filter_klic_step2(
        temp20_path=temp_dir / "KLIC_temp20.gpkg",
        output_path=step2_results,
    )
    _validate_step2_population(
        step1_no_pass_path=step1_no_pass,
        step2_results_path=step2_results,
    )
    outputs.append(step2_results)

    if progress:
        progress(total, total, "Resultaten van NEN 3654 stap 2 gereed")
    return outputs


# Backward-compatible combined post-processing entry point.
def prepare_for_arcgis_online(
    *,
    temp_dir: Path | None = None,
    output_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    check_cancelled: CancellationCheck | None = None,
    include_step2: bool = True,
) -> list[Path]:
    project_dir = Path(__file__).resolve().parent
    if temp_dir is None or output_dir is None:
        data_dir = project_dir / "data" / "KLIC" / KLIC_PROJECT_NAME
        temp_dir = data_dir / "temp"
        output_dir = data_dir / "output"

    temp_dir = Path(temp_dir)
    output_dir = Path(output_dir)
    total = 6 if include_step2 else 4

    def step1_progress(current: int, _phase_total: int, message: str) -> None:
        if progress:
            progress(current, total, message)

    outputs = prepare_step1_for_arcgis_online(
        temp_dir=temp_dir,
        output_dir=output_dir,
        progress=step1_progress,
        check_cancelled=check_cancelled,
    )
    if not include_step2:
        return outputs

    def step2_progress(current: int, _phase_total: int, message: str) -> None:
        if progress:
            progress(4 + current, total, message)

    outputs.extend(
        prepare_step2_for_arcgis_online(
            temp_dir=temp_dir,
            output_dir=output_dir,
            progress=step2_progress,
            check_cancelled=check_cancelled,
        )
    )
    return outputs


if __name__ == "__main__":
    prepare_for_arcgis_online()
