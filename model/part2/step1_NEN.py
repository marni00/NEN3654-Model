# Script 19
# ============================================================
# IMPORT DEPENDENCIES
# ============================================================

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from .relational_tables import read_table, table_exists
from .assessment_storage import initialize_normalized_output

def _arrow_strings_to_objects(frame):
    """Detach Arrow-backed strings without using Arrow's NumPy conversion."""
    if frame is None:
        return None

    result = frame.copy()
    geometry_column = (
        result.geometry.name
        if isinstance(result, gpd.GeoDataFrame)
        else None
    )

    for column in result.columns:
        if column == geometry_column:
            continue

        series = result[column]
        dtype = series.dtype
        if not isinstance(dtype, pd.StringDtype):
            continue

        storage = str(getattr(dtype, "storage", ""))
        if storage.startswith("pyarrow"):
            arrow_array = getattr(series.array, "_pa_array", None)
            if arrow_array is None:
                arrow_array = series.array.__arrow_array__()

            # to_pylist converts directly to Python objects. In contrast,
            # astype(object/string[python]) and np.asarray() route through
            # ChunkedArray.to_numpy(), which can fail for fragmented Arrow
            # string arrays after pandas concat/assignment operations.
            values = arrow_array.to_pylist()
            result[column] = pd.Series(
                values,
                index=result.index,
                dtype=object,
                name=column,
            )
        else:
            result[column] = series.astype(object)

    return result


def _normalise_text_series(series, *, lowercase=False):
    """Return stripped text as object dtype without Arrow/NumPy conversion."""
    detached = _arrow_strings_to_objects(
        series.to_frame(name="_value")
    )["_value"]

    values = []
    for value in detached.array:
        if value is None or value is pd.NA:
            values.append(pd.NA)
            continue

        missing = pd.isna(value)
        if isinstance(missing, (bool, np.bool_)) and missing:
            values.append(pd.NA)
            continue

        text = str(value).strip()
        values.append(text.lower() if lowercase else text)

    return pd.Series(
        values,
        index=series.index,
        dtype=object,
        name=series.name,
    )


# ============================================================

def step1_nen(input_path, output_path):

    # INPUT
    # ============================================================

    klic_path = Path(input_path)
    output_path = Path(output_path)

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_path != klic_path and output_path.exists():
        output_path.unlink()

    mast = "Mast"
    hv_layer = "proposed_hv_parts_with_runid"
    cable_layer = "electricity_cable_parts_with_runid"
    influenced_layer = "influenced_parts_with_runid_distance"


    # ============================================================
    # CHECK LAYER AVAILABILITY
    # ============================================================
    #
    # HV and cable are each optional: a given run may not have
    # produced one of them upstream. Detect presence up front so
    # a missing layer is skipped instead of raising.
    # ============================================================

    available_layers = set(
        gpd.list_layers(klic_path)["name"].tolist()
    )

    hv_present = hv_layer in available_layers
    cable_present = cable_layer in available_layers
    mast_present = mast in available_layers


    # ============================================================
    # READ LAYERS
    # ============================================================

    print("============================================================")
    print("READING LAYERS")
    print("============================================================")

    if hv_present:
        hv_gdf = gpd.read_file(
            klic_path,
            layer=hv_layer,
        )
    else:
        hv_gdf = None
        print(
            f"Layer '{hv_layer}' not found in {klic_path.name}; "
            "skipping HV processing."
        )

    if cable_present:
        cable_gdf = gpd.read_file(
            klic_path,
            layer=cable_layer,
        )
    else:
        cable_gdf = None
        print(
            f"Layer '{cable_layer}' not found in {klic_path.name}; "
            "skipping cable processing."
        )

    if mast_present:
        mast_gdf = gpd.read_file(
            klic_path,
            layer=mast,
        )
    else:
        mast_gdf = None
        print(
            f"Layer '{mast}' not found in {klic_path.name}; "
            "skipping mast processing."
        )

    influenced_gdf = gpd.read_file(
        klic_path,
        layer=influenced_layer,
    )

    # Pandas can keep text columns in Arrow-backed arrays. Detach them
    # before concat/merge/assignment operations; otherwise fragmented
    # Arrow strings may later fail during NumPy conversion in Pyogrio.
    mast_gdf = _arrow_strings_to_objects(mast_gdf)
    hv_gdf = _arrow_strings_to_objects(hv_gdf)
    cable_gdf = _arrow_strings_to_objects(cable_gdf)
    influenced_gdf = _arrow_strings_to_objects(influenced_gdf)


    # ============================================================
    # CHECK REQUIRED COLUMNS
    # ============================================================


    # Temp18 stores physical geometry once and run-specific values in
    # normalized tables. Expand only in memory here so the established
    # NEN formulas and output schema remain unchanged.
    if table_exists(input_path, "run_relationships"):
        run_relationships = read_table(
            input_path,
            "run_relationships",
        )
        run_memberships = read_table(
            input_path,
            "run_memberships",
        )

        run_relationships = _arrow_strings_to_objects(run_relationships)
        run_memberships = _arrow_strings_to_objects(run_memberships)

        for frame in [run_relationships, run_memberships]:
            frame["run_id"] = pd.to_numeric(
                frame["run_id"],
                errors="coerce",
            )
            frame["influenced_run_id"] = (
                frame["influenced_run_id"]
                .str.strip()
            )

        def expand_reference_layer(gdf, reference_type):
            if gdf is None:
                return None
            relationship_columns = [
                "reference_feature_id",
                "run_id",
                "influenced_run_id",
                "a",
                "l",
                "min_distance",
                "max_distance",
            ]
            relationship_subset = run_relationships.loc[
                run_relationships["reference_type"].eq(reference_type),
                relationship_columns,
            ].rename(
                columns={"reference_feature_id": "feature_id"}
            )
            for column in [
                "influenced_run_id",
                "a",
                "l",
                "min_distance",
                "max_distance",
            ]:
                if column in gdf.columns:
                    gdf = gdf.drop(columns=column)
            return gdf.merge(
                relationship_subset,
                on=["feature_id", "run_id"],
                how="inner",
            )

        hv_gdf = expand_reference_layer(hv_gdf, "hv")
        cable_gdf = expand_reference_layer(cable_gdf, "cable")

        influenced_gdf = influenced_gdf.merge(
            run_memberships,
            on=["source_layer", "feature_id", "part_no"],
            how="inner",
        )
        influenced_values = run_relationships[
            [
                "run_id",
                "influenced_run_id",
                "a",
                "l",
                "influencer_class",
            ]
        ].drop_duplicates(
            ["run_id", "influenced_run_id"]
        )
        influenced_gdf = influenced_gdf.drop(
            columns=["a", "l", "influencer_class"],
            errors="ignore",
        ).merge(
            influenced_values,
            on=["run_id", "influenced_run_id"],
            how="left",
            validate="many_to_one",
        )

    required_influencer_columns = [
        "feature_id",
        "part_no",
        "influenced_run_id",
        "influencer_class",
        "a",
        "l",
        "min_distance",
    ]

    required_influenced_columns = [
        "feature_id",
        "part_no",
        "influenced_run_id",
        "influencer_class",
        "min_distance",
    ]


    if hv_gdf is not None:
        for col in required_influencer_columns:

            if col not in hv_gdf.columns:
                raise ValueError(
                    f"HV layer is missing required column: {col}"
                )


    if cable_gdf is not None:
        for col in required_influencer_columns:

            if col not in cable_gdf.columns:
                raise ValueError(
                    f"Cable layer is missing required column: {col}"
                )


    for col in required_influenced_columns:

        if col not in influenced_gdf.columns:
            raise ValueError(
                f"Influenced layer is missing required column: {col}"
            )


    # ============================================================
    # NORMALISE RUN IDs
    # ============================================================

    def normalise_run_id(df, column):

        df = df.copy()

        df[column] = _normalise_text_series(
            df[column]
        )

        df.loc[
            df[column].isin(
                ["", "nan", "None", "<NA>"]
            ),
            column,
        ] = pd.NA

        return df


    if hv_gdf is not None:
        hv_gdf = normalise_run_id(
            hv_gdf,
            "influenced_run_id",
        )

    if cable_gdf is not None:
        cable_gdf = normalise_run_id(
            cable_gdf,
            "influenced_run_id",
        )

    influenced_gdf = normalise_run_id(
        influenced_gdf,
        "influenced_run_id",
    )


    # ============================================================
    # NORMALISE INFLUENCER CLASS
    # ============================================================

    def normalise_influencer_class(df):

        df = df.copy()

        df["influencer_class"] = _normalise_text_series(
            df["influencer_class"],
            lowercase=True,
        )

        return df


    if hv_gdf is not None:
        hv_gdf = normalise_influencer_class(
            hv_gdf
        )

    if cable_gdf is not None:
        cable_gdf = normalise_influencer_class(
            cable_gdf
        )

    influenced_gdf = normalise_influencer_class(
        influenced_gdf
    )


    # ============================================================
    # CALCULATE INDUCTIVE STATUS
    # ============================================================

    def inductive_values(
        a,
        length_m,
        influencer_class,
    ):
        """
        Calculate the required 'a' value and the resulting
        inductive pass/no-pass status.

        Rules:

        ug_le24kv:
            required = 210.92 * ln(length_km) + 148.99

        ug_gt24kv:
            required = 125.88 * ln(length_km) + 408.64

        ag:
            required = 264.62 * ln(length_km) + 1236.1

        Pass:
            a >= required

        No-pass:
            a < required

        Invalid/missing values:
            status = None
        """

        a = pd.to_numeric(
            a,
            errors="coerce",
        )

        length_m = pd.to_numeric(
            length_m,
            errors="coerce",
        )

        length_km = length_m / 1000

        valid_length = length_m.gt(0)

        required = np.select(
            [
                influencer_class.eq("ug_le24kv"),
                influencer_class.eq("ug_gt24kv"),
                influencer_class.eq("ag"),
            ],
            [
                210.92 * np.log(length_km) + 148.99,
                125.88 * np.log(length_km) + 408.64,
                264.62 * np.log(length_km) + 1236.1,
            ],
            default=np.nan,
        )

        required = pd.Series(
            np.where(
                valid_length,
                required,
                np.nan,
            ),
            index=a.index,
        )

        known = (
            a.notna()
            & required.notna()
        )

        status = pd.Series(
            np.select(
                [
                    known & (a >= required),
                    known,
                ],
                [
                    "pass",
                    "no-pass",
                ],
                default=None,
            ),
            index=a.index,
        )

        return required, status


    # ============================================================
    # CALCULATE INDUCTIVE FOR HV
    # ============================================================

    if hv_gdf is not None:
        hv_gdf[
            "a_required"
        ], hv_gdf[
            "inductive"
        ] = inductive_values(
            hv_gdf["a"],
            hv_gdf["l"],
            hv_gdf["influencer_class"],
        )


    # ============================================================
    # CALCULATE INDUCTIVE FOR CABLE
    # ============================================================

    if cable_gdf is not None:
        cable_gdf[
            "a_required"
        ], cable_gdf[
            "inductive"
        ] = inductive_values(
            cable_gdf["a"],
            cable_gdf["l"],
            cable_gdf["influencer_class"],
        )


    # ============================================================
    # BUILD RUN_ID -> INDUCTIVE LOOKUP
    # ============================================================

    inductive_frames = []

    if hv_gdf is not None:
        inductive_frames.append(
            hv_gdf[
                [
                    "influenced_run_id",
                    "influencer_class",
                    "inductive",
                ]
            ].copy()
        )

    if cable_gdf is not None:
        inductive_frames.append(
            cable_gdf[
                [
                    "influenced_run_id",
                    "influencer_class",
                    "inductive",
                ]
            ].copy()
        )

    if inductive_frames:
        inductive_lookup = pd.concat(
            inductive_frames,
            ignore_index=True,
        )
    else:
        inductive_lookup = pd.DataFrame(
            columns=[
                "influenced_run_id",
                "influencer_class",
                "inductive",
            ]
        )


    # ============================================================
    # CHECK FOR CONFLICTING RUN IDS
    # ============================================================

    inductive_conflicts = (
        inductive_lookup
        .groupby(
            "influenced_run_id",
            dropna=False,
        )["inductive"]
        .nunique()
        .reset_index(
            name="n_inductive_values"
        )
    )


    inductive_conflicts = inductive_conflicts[
        inductive_conflicts["n_inductive_values"] > 1
    ]


    if not inductive_conflicts.empty:

        print()
        print("WARNING:")

        print(
            "Some influenced_run_ids have multiple inductive "
            "results."
        )

        print(
            inductive_conflicts
            .head(20)
            .to_string(index=False)
        )


    # ============================================================
    # REDUCE TO ONE VALUE PER RUN_ID
    # ============================================================

    inductive_lookup = (
        inductive_lookup
        .dropna(
            subset=[
                "influenced_run_id"
            ]
        )
        .groupby(
            "influenced_run_id",
            as_index=False,
        )
        .agg(
            inductive=("inductive", "first")
        )
    )

    # ============================================================
    # INDUCTIVE: INITIAL ASSUMPTION IS PASS (HV / CABLE)
    # ============================================================
    #
    # Same as for the other influencers: pass unless the
    # calculation explicitly gives no-pass (a < required).
    # A null means there is no calculated result (no match with an
    # influenced feature), so it counts as pass.
    #
    # This is done AFTER the run lookup above has been built, so
    # the lookup still contains only real results and is unchanged.
    # ============================================================

    for gdf in (hv_gdf, cable_gdf):
        if gdf is not None:
            gdf["inductive"] = gdf["inductive"].fillna("pass")

    # ============================================================
    # HELPER FUNCTION:
    # CALCULATE THERMAL / CAPACITIVE / RESISTIVE
    # ============================================================

    def calculate_thermal_capacitive_resistive(
        gdf
    ):
        """
        Calculate thermal, capacitive and resistive status
        from influencer_class and min_distance.

        Rules:

        THERMAL:
            ug_gt24kv OR ug_le24kv
            AND min_distance < 5
                -> no-pass

            Otherwise -> pass

        CAPACITIVE:
            ag
            AND min_distance < 5
                -> no-pass

            Otherwise -> pass

        RESISTIVE:
            ug_gt24kv
            AND min_distance < 30
                -> no-pass

            Otherwise -> pass
        """

        gdf = gdf.copy()

        # --------------------------------------------------------
        # Make sure min_distance is numeric
        # --------------------------------------------------------

        min_distance = pd.to_numeric(
            gdf["min_distance"],
            errors="coerce",
        )

        # --------------------------------------------------------
        # THERMAL
        # --------------------------------------------------------

        thermal_no_pass = (
            gdf["influencer_class"].isin(
                [
                    "ug_gt24kv",
                    "ug_le24kv",
                ]
            )
            & min_distance.lt(5)
        )

        gdf["thermal"] = "pass"

        gdf.loc[
            thermal_no_pass,
            "thermal",
        ] = "no-pass"

        # --------------------------------------------------------
        # CAPACITIVE
        # --------------------------------------------------------

        capacitive_no_pass = (
            gdf["influencer_class"].eq("ag")
            & min_distance.lt(5)
        )

        gdf["capacitive"] = "pass"

        gdf.loc[
            capacitive_no_pass,
            "capacitive",
        ] = "no-pass"

        # --------------------------------------------------------
        # RESISTIVE
        # --------------------------------------------------------

        resistive_no_pass = (
            gdf["influencer_class"].eq("ug_gt24kv")
            & min_distance.lt(30)
        )

        gdf["resistive"] = "pass"

        gdf.loc[
            resistive_no_pass,
            "resistive",
        ] = "no-pass"

        return gdf


    # ============================================================
    # REMOVE EXISTING STATUS COLUMNS IF PRESENT
    # ============================================================

    for gdf_name, gdf in [
        ("HV", hv_gdf),
        ("Cable", cable_gdf),
        ("Influenced", influenced_gdf),
    ]:

        if gdf is None:
            continue

        gdf.drop(
            columns=[
                "thermal",
                "capacitive",
                "resistive",
            ],
            errors="ignore",
            inplace=True,
        )


    # ============================================================
    # CALCULATE THERMAL / CAPACITIVE / RESISTIVE
    # ============================================================
    #
    # IMPORTANT:
    #
    # This is done for whichever of HV / cable are present,
    # plus the influenced layer.
    #
    # The calculation uses the min_distance already stored
    # on each row.
    #
    # Since the HV and cable min_distance values are
    # run-specific, the resulting statuses are also
    # run-specific.
    # ============================================================

    print()
    print("Calculating thermal/capacitive/resistive...")

    if hv_gdf is not None:
        hv_gdf = calculate_thermal_capacitive_resistive(
            hv_gdf
        )

    if cable_gdf is not None:
        cable_gdf = calculate_thermal_capacitive_resistive(
            cable_gdf
        )

    influenced_gdf = calculate_thermal_capacitive_resistive(
        influenced_gdf
    )


    # ============================================================
    # APPLY MAST RESISTIVE INFLUENCE
    # ============================================================
    #
    # Every Mast gets a 50 m buffer.
    #
    # Any influenced feature that intersects or touches one of
    # these buffers gets resistive = no-pass.
    # ============================================================

    if mast_gdf is not None and not mast_gdf.empty:

        mast_buffer = mast_gdf.copy()

        mast_buffer["geometry"] = mast_buffer.geometry.buffer(50)

        mast_union = mast_buffer.geometry.union_all()

        mast_resistive_no_pass = influenced_gdf.geometry.intersects(
            mast_union
        )

        influenced_gdf.loc[
            mast_resistive_no_pass,
            "resistive",
        ] = "no-pass"


    # ============================================================
    # JOIN INDUCTIVE STATUS TO INFLUENCED LAYER
    # ============================================================
    #
    # Existing logic preserved. Works unchanged even when
    # inductive_lookup ended up empty (both HV and cable
    # missing) -> influenced_gdf["inductive"] is simply NA.
    # ============================================================

    inductive_lookup = inductive_lookup.rename(
        columns={
            "inductive": "inductive_lookup"
        }
    )


    influenced_gdf = influenced_gdf.merge(
        inductive_lookup,
        on="influenced_run_id",
        how="left",
        validate="many_to_one",
    )


    # ============================================================
    # WRITE INDUCTIVE STATUS
    # ============================================================

    influenced_gdf["inductive"] = (
        influenced_gdf["inductive_lookup"]
        .fillna("pass")
    )


    # ============================================================
    # CLEAN TEMPORARY COLUMN
    # ============================================================

    influenced_gdf = influenced_gdf.drop(
        columns=[
            "inductive_lookup",
        ],
        errors="ignore",
    )


    # ============================================================
    # IMPORTANT:
    # HV AND CABLE ALREADY HAVE THEIR OWN INDUCTIVE VALUES
    # ============================================================
    #
    # Therefore we do NOT overwrite their inductive values
    # with the run lookup.
    # ============================================================


    # ============================================================
    # STORE NORMALIZED ASSESSMENTS
    # ============================================================
    #
    # Temp18 stores each physical geometry once. Persist only narrow,
    # run-specific assessment rows instead of duplicating geometry for every
    # relationship and duplicating it again in *_no_pass layers.
    # ============================================================

    assessment_columns = [
        "feature_id",
        "part_no",
        "run_id",
        "influenced_run_id",
        "influencer_class",
        "a",
        "l",
        "min_distance",
        "max_distance",
        "thermal",
        "capacitive",
        "resistive",
        "inductive",
    ]

    reference_frames = []
    for reference_type, frame in (("hv", hv_gdf), ("cable", cable_gdf)):
        if frame is None or frame.empty:
            continue
        columns = [
            column for column in assessment_columns
            if column in frame.columns
        ]
        assessment = frame[columns].copy()
        assessment.insert(0, "reference_type", reference_type)
        reference_frames.append(assessment)

    if reference_frames:
        reference_assessments = pd.concat(
            reference_frames,
            ignore_index=True,
        )
    else:
        reference_assessments = pd.DataFrame(
            columns=["reference_type", *assessment_columns]
        )

    influenced_columns = [
        "source_layer",
        *assessment_columns,
    ]
    influenced_columns = [
        column for column in influenced_columns
        if column in influenced_gdf.columns
    ]
    influenced_assessments = influenced_gdf[
        influenced_columns
    ].copy()

    reference_assessments.insert(
        0,
        "_assessment_id",
        np.arange(len(reference_assessments), dtype=np.int64),
    )
    influenced_assessments.insert(
        0,
        "_assessment_id",
        np.arange(len(influenced_assessments), dtype=np.int64),
    )

    reference_assessments = _arrow_strings_to_objects(
        reference_assessments
    )
    influenced_assessments = _arrow_strings_to_objects(
        influenced_assessments
    )

    print()
    print("============================================================")
    print("SAVING NORMALIZED NEN STEP 1 RESULT")
    print("============================================================")
    print(
        f"Reference assessment rows: {len(reference_assessments):,}; "
        f"influenced assessment rows: {len(influenced_assessments):,}"
    )

    initialize_normalized_output(
        input_path=klic_path,
        output_path=output_path,
        reference_assessments=reference_assessments,
        influenced_assessments=influenced_assessments,
    )

    def print_status_summary(label, frame):
        print()
        print(label)
        if frame is None:
            print("Skipped (layer not present).")
            return
        for column in (
            "thermal",
            "capacitive",
            "resistive",
            "inductive",
        ):
            if column not in frame.columns:
                continue
            counts = frame[column].value_counts(dropna=False)
            print(f"{column.capitalize()}:")
            print(counts.to_string())

    print_status_summary("HV:", hv_gdf)
    print_status_summary("CABLE:", cable_gdf)
    print_status_summary("INFLUENCED:", influenced_gdf)

    print()
    print("============================================================")
    print("NEN 3654 STEP 1 COMPLETE (NORMALIZED STORAGE)")
    print("============================================================")
    print(f"Output written to: {output_path}")
