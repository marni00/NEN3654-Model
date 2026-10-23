# Script 20

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from .assessment_storage import (
    CABLE_LAYER,
    HV_LAYER,
    INFLUENCED_LAYER,
    INFLUENCED_TABLE,
    REFERENCE_TABLE,
    copy_normalized_assessment,
    is_normalized_assessment,
    read_assessed_layer,
    select_step1_inductive_no_pass,
)
from .relational_tables import read_table, write_table

def step2_nen(input_path, output_path):

    # ============================================================
    # INPUT
    # ============================================================

    input_path = Path(input_path)
    output_path = Path(output_path)

    project_dir = Path(__file__).resolve().parents[2]

    step2_table_path = (
        project_dir
        / "utils"
        / "Step2_Table.csv"
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_path.exists():
        output_path.unlink()

    # ============================================================
    # LAYER NAMES
    # ============================================================

    hv_layer_pairs = [
        (
            "proposed_hv_parts_with_runid",
            "proposed_hv_parts_with_runid_no_pass",
        ),
        (
            "electricity_cable_parts_with_runid",
            "electricity_cable_parts_with_runid_no_pass",
        ),
    ]

    influenced_no_pass_layer = (
        "influenced_parts_with_runid_distance_no_pass"
    )

    # ============================================================
    # K COLUMNS
    # ============================================================

    k_cols = [
        "K1_continu",
        "K2_continu",
        "K1_corrosie",
        "K2_corrosie",
        "K1_eenfase",
        "K2_eenfase",
    ]

    # ============================================================
    # HELPERS
    # ============================================================

    def to_number(series):
        return pd.to_numeric(
            series.astype(str)
            .str.strip()
            .str.replace("\u00a0", "", regex=False)
            .str.replace(" ", "", regex=False)
            .str.replace(",", ".", regex=False),
            errors="coerce",
        )

    def calculate_uc_vectorized(a, l, K1, K2):
        valid = (
            a.notna()
            & l.notna()
            & K1.notna()
            & K2.notna()
            & (a > 0)
            & (K2 > 0)
        )

        result = pd.Series(
            np.nan,
            index=a.index,
            dtype="float64",
        )

        # Formula requires l in km, while l remains in metres
        # in the dataset.
        l_km = l / 1000

        result.loc[valid] = (
            l_km.loc[valid]
            * K1.loc[valid]
            * (
                np.log10(K2.loc[valid])
                - np.log10(a.loc[valid])
            )
        )

        return result

    def uc_pass_status(series):
        status = pd.Series(
            pd.NA,
            index=series.index,
            dtype="object",
        )

        status.loc[series <= 1] = "Pass"
        status.loc[series > 1] = "No pass"

        return status

    output_created = False

    def write_layer(gdf, layer_name):
        """Create the output for the first layer and append thereafter."""
        nonlocal output_created

        if normalized_input:
            normalized_writes[layer_name] = gdf.copy()
            return

        gdf.to_file(
            output_path,
            layer=layer_name,
            driver="GPKG",
            mode="a" if output_created else "w",
        )

        output_created = True

    # ============================================================
    # READ STEP 2 TABLE
    # ============================================================

    step2_table = pd.read_csv(
        step2_table_path,
        sep=";",
        dtype=str,
    )

    step2_table.columns = step2_table.columns.str.strip()

    step2_table["Code"] = (
        step2_table["Code"]
        .astype(str)
        .str.strip()
        .str.upper()
    )

    step2_table = step2_table[
        step2_table["Code"].notna()
        & (step2_table["Code"] != "")
        & (step2_table["Code"].str.lower() != "nan")
    ]

    for col in k_cols:
        step2_table[col] = to_number(
            step2_table[col]
        )

    # ============================================================
    # AVAILABLE INPUT LAYERS
    # ============================================================

    available_layers = list(
        gpd.list_layers(input_path)["name"]
    )
    physical_layers = set(available_layers)
    normalized_input = is_normalized_assessment(input_path)
    normalized_writes = {}
    if normalized_input:
        # Normalized Step 1 storage keeps each physical geometry layer once
        # and reconstructs its virtual *_no_pass view from assessment tables.
        # HV and electricity-cable layers are optional upstream. Only expose a
        # virtual no-pass layer when its physical base layer actually exists;
        # otherwise read_assessed_layer() would ask GDAL to open a layer that
        # was correctly omitted from the GeoPackage.
        for base_layer in (HV_LAYER, CABLE_LAYER, INFLUENCED_LAYER):
            if base_layer in physical_layers:
                available_layers.append(f"{base_layer}_no_pass")

    print("============================================================")
    print("STEP 2 NEN3654")
    print("============================================================")

    # Keep track of layers handled by this step.
    handled_layers = set()

    # Store calculated HV data so that one combined run-level
    # UC lookup can be created for the influenced layer.
    calculated_hv_frames = []

    hv_no_pass_row_counts = {}

    # ============================================================
    # PROCESS STEP 1 NO-PASS HV INPUTS
    # ============================================================
    #
    # Step 2 builds only on the no-pass selection produced by Step 1.
    # The full Step 1 layers are passed through unchanged below.
    # ============================================================

    for hv_layer, hv_no_pass_layer in hv_layer_pairs:

        if hv_no_pass_layer not in available_layers:
            print()
            print(
                f"Step 1 no-pass layer '{hv_no_pass_layer}' not found; "
                "skipping this HV input."
            )
            continue

        handled_layers.add(hv_no_pass_layer)

        print()
        print(f"Reading Step 1 no-pass layer: {hv_no_pass_layer}")

        hv_gdf = (
            read_assessed_layer(
                input_path,
                hv_layer,
                no_pass=True,
            )
            if normalized_input
            else gpd.read_file(
                input_path,
                layer=hv_no_pass_layer,
            )
        )

        hv_gdf.columns = hv_gdf.columns.str.strip()

        # The raw Step 1 *_no_pass layer contains failures from all
        # assessment types. Step 2 may only receive the Step 1 inductive
        # no-pass population.
        hv_gdf = select_step1_inductive_no_pass(
            hv_gdf,
            hv_no_pass_layer,
        )

        if hv_gdf.empty:
            print(
                f"No Step 1 inductive no-pass rows in "
                f"'{hv_no_pass_layer}'; skipping."
            )
            continue

        # ========================================================
        # CLEAN HV INPUT
        # ========================================================

        hv_gdf["Code"] = (
            hv_gdf["Code"]
            .astype(str)
            .str.strip()
            .str.upper()
        )

        hv_gdf["a"] = to_number(
            hv_gdf["a"]
        )

        hv_gdf["l"] = to_number(
            hv_gdf["l"]
        )

        # ========================================================
        # JOIN STEP 2 TABLE
        # ========================================================

        hv_gdf = hv_gdf.merge(
            step2_table[
                ["Code"] + k_cols
            ],
            on="Code",
            how="left",
            validate="many_to_one",
        )

        # ========================================================
        # CALCULATE UC VALUES
        # ========================================================

        print(f"Calculating UC values for: {hv_no_pass_layer}")

        hv_gdf["UC_continu"] = calculate_uc_vectorized(
            hv_gdf["a"],
            hv_gdf["l"],
            hv_gdf["K1_continu"],
            hv_gdf["K2_continu"],
        )

        hv_gdf["UC_corrosie"] = calculate_uc_vectorized(
            hv_gdf["a"],
            hv_gdf["l"],
            hv_gdf["K1_corrosie"],
            hv_gdf["K2_corrosie"],
        )

        hv_gdf["UC_eenfase"] = calculate_uc_vectorized(
            hv_gdf["a"],
            hv_gdf["l"],
            hv_gdf["K1_eenfase"],
            hv_gdf["K2_eenfase"],
        )

        # ========================================================
        # CALCULATE UC PASS / NO-PASS
        # ========================================================

        hv_gdf["UC_continu_pass"] = uc_pass_status(
            hv_gdf["UC_continu"]
        )

        hv_gdf["UC_corrosie_pass"] = uc_pass_status(
            hv_gdf["UC_corrosie"]
        )

        hv_gdf["UC_eenfase_pass"] = uc_pass_status(
            hv_gdf["UC_eenfase"]
        )

        uc_status_columns = [
            "UC_continu_pass",
            "UC_corrosie_pass",
            "UC_eenfase_pass",
        ]

        all_uc_pass = (
            hv_gdf[uc_status_columns]
            .eq("Pass")
            .all(axis=1)
        )

        any_uc_no_pass = (
            hv_gdf[uc_status_columns]
            .eq("No pass")
            .any(axis=1)
        )

        # Keep the Step 1 inductive result and add the Step 2 result
        # separately. This makes it possible to export:
        # - all Step 1 no-pass features with their Step 2 result; and
        # - only features receiving a new Step 2 inductive no-pass.
        hv_gdf["step2_inductive"] = pd.Series(
            pd.NA,
            index=hv_gdf.index,
            dtype="object",
        )
        hv_gdf.loc[all_uc_pass, "step2_inductive"] = "pass"
        hv_gdf.loc[any_uc_no_pass, "step2_inductive"] = "no-pass"

        # Store the calculated rows before removing the K columns.
        calculated_hv_frames.append(
            hv_gdf[
                [
                    "influenced_run_id",
                    "UC_continu_pass",
                    "UC_corrosie_pass",
                    "UC_eenfase_pass",
                ]
            ].copy()
        )

        # ========================================================
        # REMOVE K COLUMNS AND SAVE ENRICHED STEP 1 NO-PASS LAYER
        # ========================================================

        hv_gdf = hv_gdf.drop(
            columns=k_cols,
            errors="ignore",
        )

        print(
            "Saving Step 1 no-pass layer with Step 2 results: "
            f"{hv_no_pass_layer}"
        )

        write_layer(
            hv_gdf,
            hv_no_pass_layer,
        )

        hv_no_pass_row_counts[hv_no_pass_layer] = len(hv_gdf)

    # ============================================================
    # BUILD COMBINED RUN-LEVEL STEP 2 LOOKUP
    # ============================================================

    if calculated_hv_frames:

        combined_hv_status = pd.concat(
            calculated_hv_frames,
            ignore_index=True,
        )

        def summarize_step2_status(group):
            statuses = group[
                [
                    "UC_continu_pass",
                    "UC_corrosie_pass",
                    "UC_eenfase_pass",
                ]
            ]

            if statuses.eq("No pass").to_numpy().any():
                return "no-pass"

            if statuses.eq("Pass").to_numpy().all():
                return "pass"

            return pd.NA

        run_step2_status = (
            combined_hv_status
            .dropna(subset=["influenced_run_id"])
            .groupby("influenced_run_id")[
                [
                    "UC_continu_pass",
                    "UC_corrosie_pass",
                    "UC_eenfase_pass",
                ]
            ]
            .apply(summarize_step2_status)
            .reset_index(name="step2_inductive")
        )

    else:
        run_step2_status = pd.DataFrame(
            columns=[
                "influenced_run_id",
                "step2_inductive",
            ]
        )

    # ============================================================
    # ENRICH THE STEP 1 INFLUENCED NO-PASS LAYER
    # ============================================================

    influenced_no_pass_gdf = None

    if influenced_no_pass_layer in available_layers:

        handled_layers.add(influenced_no_pass_layer)

        print()
        print("============================================================")
        print("UPDATING INFLUENCED STEP 1 NO-PASS")
        print("============================================================")

        influenced_no_pass_gdf = (
            read_assessed_layer(
                input_path,
                INFLUENCED_LAYER,
                no_pass=True,
            )
            if normalized_input
            else gpd.read_file(
                input_path,
                layer=influenced_no_pass_layer,
            )
        )

        # Apply the same Step 1 inductive no-pass selection as for the
        # influencer layers. This removes thermal/capacitive/resistive-only
        # failures before the Step 2 result export is built.
        influenced_no_pass_gdf = select_step1_inductive_no_pass(
            influenced_no_pass_gdf,
            influenced_no_pass_layer,
        )

        if influenced_no_pass_gdf.empty:
            print(
                f"No Step 1 inductive no-pass rows in "
                f"'{influenced_no_pass_layer}'; skipping."
            )
            influenced_no_pass_gdf = None
        else:
            influenced_no_pass_gdf = influenced_no_pass_gdf.merge(
                run_step2_status,
                on="influenced_run_id",
                how="left",
                validate="many_to_one",
            )

            write_layer(
                influenced_no_pass_gdf,
                influenced_no_pass_layer,
            )

            print(
                f"Updated layer '{influenced_no_pass_layer}' "
                "with Step 2 inductive results."
            )

    else:
        print()
        print(
            f"Layer '{influenced_no_pass_layer}' not found; "
            "skipping influenced no-pass update."
        )

    # ============================================================
    # PASS THROUGH ALL OTHER INPUT LAYERS
    # ============================================================

    for layer_name in available_layers:

        if normalized_input:
            continue

        if layer_name in handled_layers:
            continue

        print(
            f"Passing through layer: {layer_name}"
        )

        gdf = gpd.read_file(
            input_path,
            layer=layer_name,
        )

        write_layer(
            gdf,
            layer_name,
        )

    # ============================================================
    # SUMMARY
    # ============================================================

    if normalized_input:
        copy_normalized_assessment(input_path, output_path)

        reference = read_table(output_path, REFERENCE_TABLE)
        influenced = read_table(output_path, INFLUENCED_TABLE)

        reference_updates = []
        for layer_name in (
            f"{HV_LAYER}_no_pass",
            f"{CABLE_LAYER}_no_pass",
        ):
            frame = normalized_writes.get(layer_name)
            if frame is None or frame.empty:
                continue
            update_columns = [
                column
                for column in frame.columns
                if column == "_assessment_id"
                or column == "step2_inductive"
                or column.startswith("UC_")
            ]
            if "_assessment_id" in update_columns:
                reference_updates.append(
                    frame[update_columns].drop_duplicates("_assessment_id")
                )

        if reference_updates:
            updates = pd.concat(reference_updates, ignore_index=True)
            updates = updates.drop_duplicates("_assessment_id")
            existing_update_columns = [
                column
                for column in updates.columns
                if column != "_assessment_id"
                and column in reference.columns
            ]
            reference = reference.drop(
                columns=existing_update_columns,
                errors="ignore",
            ).merge(
                updates,
                on="_assessment_id",
                how="left",
                validate="one_to_one",
            )

        influenced_frame = normalized_writes.get(
            f"{INFLUENCED_LAYER}_no_pass"
        )
        if (
            influenced_frame is not None
            and not influenced_frame.empty
            and "_assessment_id" in influenced_frame.columns
            and "step2_inductive" in influenced_frame.columns
        ):
            updates = influenced_frame[
                ["_assessment_id", "step2_inductive"]
            ].drop_duplicates("_assessment_id")
            influenced = influenced.drop(
                columns=["step2_inductive"],
                errors="ignore",
            ).merge(
                updates,
                on="_assessment_id",
                how="left",
                validate="one_to_one",
            )

        write_table(output_path, REFERENCE_TABLE, reference)
        write_table(output_path, INFLUENCED_TABLE, influenced)

    print()
    print("============================================================")
    print("STEP 2 NEN3654 COMPLETE")
    print("============================================================")

    print(
        f"Output written to: {output_path}"
    )

    if hv_no_pass_row_counts:
        for (
            layer_name,
            row_count,
        ) in hv_no_pass_row_counts.items():
            print()
            print(
                f"Step 1 HV no-pass rows enriched by Step 2 "
                f"('{layer_name}'): {row_count}"
            )
    else:
        print()
        print(
            "No Step 1 proposed HV or Elektriciteitskabel "
            "no-pass layers were found."
        )

    if influenced_no_pass_gdf is not None:
        print(
            "Influenced no-pass rows after Step 2: "
            f"{len(influenced_no_pass_gdf)}"
        )


# ============================================================
# RUN
# ============================================================

#if __name__ == "__main__":
#     step2_nen(
#         input_path="data/KLIC/KLIC_didam/temp/KLIC_temp19.gpkg",
#         output_path="data/KLIC/KLIC_didam/temp/KLIC_temp20.gpkg",
#     )