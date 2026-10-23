#Script 16
# ============================================================
# IMPORT DEPENDENCIES
# ============================================================
from pathlib import Path
import pandas as pd
import os


# ============================================================
# CONFIG
# ============================================================
INPUT_DIR = Path("../data/KLIC/run_exports")

INPUT_FILES = [
    "proposed_hv_segments_between_distance_lines.csv",
    "electriciteitskabel_segments_between_distance_lines.csv",
]

OUTPUT_SUFFIX = "_with_la.csv"


# ============================================================
# COLUMN NAMES
# ============================================================

ID_COLS = [
    "run_id",
    "reference_feature_id",
    "reference_part_no"
]

TEXT_ID_COLS = [
    "influenced_run_id"
]

# Columns needed to identify each row/segment
ROW_ID_COLS = [
    "run_id",
    "influenced_run_id",
    "reference_feature_id",
    "reference_part_no"
]

# Columns used for calculating l and a
# l and a are calculated for the whole
# run_id + influenced_run_id group.
LA_GROUP_COLS = [
    "run_id",
    "influenced_run_id"
]

LENGTH_COL = "seg_length"
DIST_FROM_COL = "dist_from"
DIST_TO_COL = "dist_to"

NUMERIC_VALUE_COLS = [
    LENGTH_COL,
    DIST_FROM_COL,
    DIST_TO_COL
]


# ============================================================
# FUNCTION TO PROCESS ONE FILE
# ============================================================
def process_file(input_csv: Path) -> None:
    output_csv = input_csv.with_name(input_csv.stem + OUTPUT_SUFFIX)
    df = pd.read_csv(
        input_csv,
        dtype={"influenced_run_id": "string"},
    )

    required = ROW_ID_COLS + NUMERIC_VALUE_COLS
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns in {input_csv.name}: {missing}"
        )

    for column in ID_COLS + NUMERIC_VALUE_COLS:
        df[column] = pd.to_numeric(df[column], errors="coerce")
    df["influenced_run_id"] = (
        df["influenced_run_id"].astype("string").str.strip()
    )
    df = df[
        ~df["influenced_run_id"].isin(["", "nan", "None", "<NA>"])
    ].dropna(subset=required).copy()

    df["run_id"] = df["run_id"].astype(int)
    df["reference_feature_id"] = df[
        "reference_feature_id"
    ].astype(int)
    df["reference_part_no"] = df["reference_part_no"].astype(int)
    df["influenced_run_id"] = df["influenced_run_id"].astype(str)
    df["mid_distance"] = (
        df[DIST_FROM_COL] + df[DIST_TO_COL]
    ) / 2
    df["weighted_mid"] = df[LENGTH_COL] * df["mid_distance"]

    group_stats = (
        df.groupby(LA_GROUP_COLS, as_index=False)
        .agg(
            reference_dataset=("reference_dataset", "first"),
            reference_feature_id=("reference_feature_id", "first"),
            reference_part_count=("reference_part_no", "nunique"),
            l=(LENGTH_COL, "sum"),
            weighted_mid=("weighted_mid", "sum"),
        )
    )
    group_stats["a"] = pd.NA
    valid = group_stats["l"] != 0
    group_stats.loc[valid, "a"] = (
        group_stats.loc[valid, "weighted_mid"]
        / group_stats.loc[valid, "l"]
    )
    group_stats = group_stats.drop(columns="weighted_mid")
    group_stats.to_csv(output_csv, index=False)

    print(f"Saved compact A/L table: {output_csv}")
    print(f"Input segment rows: {len(df):,}")
    print(f"Output run relationships: {len(group_stats):,}")

def calculate_normative_distance_length(
    input_path=None,
    distance_lines_path=None,
    output_path=None,
):
    """Calculate normative distance and length for step 15 CSV exports."""

    # Step 15 writes both CSV exports beside its requested output path.
    if output_path is not None:
        input_dir = Path(output_path).parent
    elif input_path is not None:
        input_path = Path(input_path)
        input_dir = input_path if input_path.is_dir() else input_path.parent
    else:
        input_dir = INPUT_DIR

    processed_any = False

    for filename in INPUT_FILES:

        input_csv = input_dir / filename

        if not input_csv.exists():
            print(
                f"Skipping {filename} "
                f"(file not found)."
            )
            continue

        process_file(input_csv)
        processed_any = True

    if not processed_any:
        print()
        print(
            "No input files were found. "
            "Nothing was processed."
        )
    else:
        print()
        print("=" * 70)
        print("DONE")
        print("=" * 70)

    return output_path
#if __name__ == "__main__":
#    calculate_normative_distance_length()

