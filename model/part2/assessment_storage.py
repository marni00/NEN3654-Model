"""Normalized storage and reconstruction for NEN 3654 assessment results."""

from __future__ import annotations

from pathlib import Path
import shutil
import sqlite3

import geopandas as gpd
import pandas as pd

from .relational_tables import read_table, table_exists, write_table


REFERENCE_TABLE = "step1_reference_assessments"
INFLUENCED_TABLE = "step1_influenced_assessments"
FORMAT_TABLE = "nen_assessment_storage"
FORMAT_VERSION = 1

HV_LAYER = "proposed_hv_parts_with_runid"
CABLE_LAYER = "electricity_cable_parts_with_runid"
INFLUENCED_LAYER = "influenced_parts_with_runid_distance"

REFERENCE_TYPES = {
    HV_LAYER: "hv",
    CABLE_LAYER: "cable",
}
STATUS_COLUMNS = ("thermal", "capacitive", "resistive", "inductive")


def normalize_status(values: pd.Series) -> pd.Series:
    """Normalize pass/no-pass values for reliable comparison."""
    return (
        values.astype("string")
        .str.strip()
        .str.casefold()
        .str.replace(r"[\s_–—]+", "-", regex=True)
    )


def select_step1_inductive_no_pass(
    frame: gpd.GeoDataFrame,
    layer_name: str,
) -> gpd.GeoDataFrame:
    """Return the complete Step 1 inductive no-pass run population.

    Normalized storage reconstructs the broader Step 1 ``*_no_pass`` view:
    rows failing any assessment are present. Step 2, however, is defined only
    for runs whose Step 1 *inductive* assessment is no-pass. This selector is
    shared by the Step 2 calculation and its post-processing so both operate
    on exactly the same population.
    """
    required_columns = {"influenced_run_id", "inductive"}
    missing_columns = sorted(required_columns.difference(frame.columns))
    if missing_columns:
        raise ValueError(
            f"Layer '{layer_name}' is missing required Step 1 columns: "
            + ", ".join(missing_columns)
        )

    selected = frame.copy()
    run_ids = selected["influenced_run_id"].astype("string").str.strip()
    run_ids = run_ids.mask(
        run_ids.isna()
        | run_ids.str.casefold().isin({"", "nan", "none", "<na>"}),
        pd.NA,
    )
    selected["influenced_run_id"] = run_ids

    inductive_no_pass = normalize_status(
        selected["inductive"]
    ).eq("no-pass").fillna(False)
    rows_with_id = selected["influenced_run_id"].notna()
    no_pass_run_ids = (
        selected.loc[
            rows_with_id & inductive_no_pass,
            "influenced_run_id",
        ]
        .dropna()
        .unique()
    )
    keep_mask = (
        selected["influenced_run_id"].isin(no_pass_run_ids)
        | (~rows_with_id & inductive_no_pass)
    )
    result = selected.loc[keep_mask].copy()

    print(
        f"Selected {len(result)} of {len(frame)} rows from '{layer_name}' "
        "for Step 2 (Step 1 inductive no-pass only)."
    )
    return result


def is_normalized_assessment(path: Path | str) -> bool:
    return table_exists(path, FORMAT_TABLE)


def initialize_normalized_output(
    input_path: Path | str,
    output_path: Path | str,
    reference_assessments: pd.DataFrame,
    influenced_assessments: pd.DataFrame,
) -> None:
    """Copy the normalized geometry package and add narrow assessment tables."""
    input_path = Path(input_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    shutil.copy2(input_path, output_path)

    write_table(output_path, REFERENCE_TABLE, reference_assessments)
    write_table(output_path, INFLUENCED_TABLE, influenced_assessments)
    write_table(
        output_path,
        FORMAT_TABLE,
        pd.DataFrame(
            [{"format_version": FORMAT_VERSION, "description": "normalized NEN assessments"}]
        ),
    )

    with sqlite3.connect(output_path) as connection:
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{REFERENCE_TABLE}_layer" '
            f'ON "{REFERENCE_TABLE}" '
            '("reference_type", "feature_id", "part_no", "run_id")'
        )
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{REFERENCE_TABLE}_run" '
            f'ON "{REFERENCE_TABLE}" ("influenced_run_id")'
        )
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{INFLUENCED_TABLE}_feature" '
            f'ON "{INFLUENCED_TABLE}" '
            '("source_layer", "feature_id", "part_no")'
        )
        connection.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{INFLUENCED_TABLE}_run" '
            f'ON "{INFLUENCED_TABLE}" ("influenced_run_id")'
        )
        connection.commit()


def _drop_assessment_columns(base: gpd.GeoDataFrame, assessments: pd.DataFrame, keys):
    replace = [
        column
        for column in assessments.columns
        if column in base.columns and column not in keys and column != "_assessment_id"
    ]
    return base.drop(columns=replace, errors="ignore")


def _read_reference_assessments(path, layer_name):
    reference_type = REFERENCE_TYPES[layer_name]
    assessments = read_table(path, REFERENCE_TABLE)
    return assessments.loc[
        assessments["reference_type"].eq(reference_type)
    ].drop(columns=["reference_type"])


def read_assessed_layer(
    path: Path | str,
    layer_name: str,
    *,
    no_pass: bool = False,
) -> gpd.GeoDataFrame:
    """Read a model layer, reconstructing run-specific rows when normalized."""
    path = Path(path)
    if not is_normalized_assessment(path):
        actual_layer = f"{layer_name}_no_pass" if no_pass else layer_name
        return gpd.read_file(path, layer=actual_layer)

    base = gpd.read_file(path, layer=layer_name)

    if layer_name in REFERENCE_TYPES:
        assessments = _read_reference_assessments(path, layer_name)
        keys = ["feature_id", "part_no", "run_id"]
    elif layer_name == INFLUENCED_LAYER:
        assessments = read_table(path, INFLUENCED_TABLE)
        keys = ["source_layer", "feature_id", "part_no"]
    else:
        return base

    missing_base = sorted(set(keys).difference(base.columns))
    missing_assessments = sorted(set(keys).difference(assessments.columns))
    if missing_base or missing_assessments:
        raise ValueError(
            f"Cannot reconstruct '{layer_name}'. Missing base keys={missing_base}; "
            f"missing assessment keys={missing_assessments}."
        )

    duplicate_base_keys = base.duplicated(keys, keep=False)
    if duplicate_base_keys.any():
        examples = (
            base.loc[duplicate_base_keys, keys]
            .drop_duplicates()
            .head(10)
            .to_dict("records")
        )
        raise ValueError(
            f"Cannot reconstruct '{layer_name}': base geometry keys are not "
            f"unique for {keys}. Examples: {examples}"
        )

    base = _drop_assessment_columns(base, assessments, keys)
    result = base.merge(
        assessments,
        on=keys,
        how="inner",
        validate="one_to_many",
    )
    result = gpd.GeoDataFrame(result, geometry=base.geometry.name, crs=base.crs)

    if no_pass:
        result = _select_no_pass(path, result, layer_name)
    return result


def _status_failure_mask(frame: pd.DataFrame) -> pd.Series:
    present = [column for column in STATUS_COLUMNS if column in frame.columns]
    if len(present) != len(STATUS_COLUMNS):
        missing = sorted(set(STATUS_COLUMNS).difference(present))
        raise ValueError(f"Assessment data is missing status columns: {missing}")
    normalized = frame[present].apply(
        lambda column: column.astype("string").str.strip().str.casefold()
    )
    return ~normalized.eq("pass").all(axis=1)


def failing_run_ids(path: Path | str) -> set[str]:
    """Return runs where any reference or influenced assessment is non-pass."""
    failed: set[str] = set()
    for table_name in (REFERENCE_TABLE, INFLUENCED_TABLE):
        frame = read_table(path, table_name)
        if frame.empty:
            continue
        mask = _status_failure_mask(frame)
        values = frame.loc[mask, "influenced_run_id"].dropna()
        failed.update(str(value).strip() for value in values if str(value).strip())
    return failed


def _select_no_pass(path, frame, layer_name):
    row_fails = _status_failure_mask(frame)
    run_fails = frame["influenced_run_id"].astype("string").isin(failing_run_ids(path))
    fails = row_fails | run_fails

    feature_keys = (
        ["source_layer", "feature_id"]
        if layer_name == INFLUENCED_LAYER and "source_layer" in frame.columns
        else ["feature_id"]
    )
    non_null_keys = frame[feature_keys].notna().all(axis=1)
    failing_features = frame.loc[fails & non_null_keys, feature_keys].drop_duplicates()

    if failing_features.empty:
        in_failing_feature = pd.Series(False, index=frame.index)
    else:
        marker = frame[feature_keys].merge(
            failing_features.assign(_failing_feature=True),
            on=feature_keys,
            how="left",
        )["_failing_feature"]
        in_failing_feature = marker.fillna(False).astype(bool)
        in_failing_feature.index = frame.index

    return frame.loc[fails | in_failing_feature].copy()


def copy_normalized_assessment(input_path: Path | str, output_path: Path | str) -> None:
    input_path = Path(input_path)
    output_path = Path(output_path)
    if output_path.exists():
        output_path.unlink()
    shutil.copy2(input_path, output_path)
