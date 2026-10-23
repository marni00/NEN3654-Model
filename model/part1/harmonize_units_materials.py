#Script 2

#pipeline/hormonize_units_materials.py

from pathlib import Path
import re

import pandas as pd
import pyogrio


# =============================================================================
# Configuration
# =============================================================================

MATERIAL_OUTPUT_COL = "material"
MATERIAL_NORMALIZED_COL = "material_norm"
CONDUCTIVE_COL = "conductive"

# Preferred material columns.
# If both ever exist in one layer, values are combined row-wise without duplicates.
MATERIAL_SOURCE_CANDIDATES = [
    "buismateriaalType",
    "telecomKabelMateriaalType",
]

# Fallback text columns.
# Including "lable" because this typo sometimes appears in converted data.
FALLBACK_TEXT_CANDIDATES = [
    "label",
    "lable",
    "toelichting",
]

# Diameter columns
DIAMETER_VALUE_CANDIDATES = [
    "buisDiameter",
]

DIAMETER_UNIT_CANDIDATES = [
    "buisDiameterM",
]

# Conversion factors to mm
DIAMETER_UNIT_FACTORS = {
    "mm": 1.0,
    "cm": 10.0,
    "m": 1000.0,
}

# Strings that should be treated as empty / NULL.
EMPTY_STRINGS = {
    "",
    " ",
    "nan",
    "none",
    "null",
    "<null>",
    "<nul>",
    "nul",
    "n/a",
    "na",
    "n.v.t.",
    "nvt",
    "-",
    "onbekent",
    "onbekend",
    "onbekendt",
}

# Normalized material values used for conductivity classification.
MATERIAL_MAP = {
    "PVC-O": "PVC-O",
    "SPVC": "PVC",
    "PVC": "PVC",
    "PE 100": "PE",
    "PE 80": "PE",
    "PE 40": "PE",
    "PE100": "PE",
    "PE80": "PE",
    "PE40": "PE",
    "HDPE": "HDPE",
    "HPE": "HDPE",
    "PE": "PE",
    "POLYETHEEN": "PE",
    "PP": "PP",
    "POLYPROPYLEEN": "PP",
    "PB": "PB",
    "CU": "copper",
    "KOPER": "copper",
    "RVS": "stainless_steel",
    "ST": "steel",
    "STEEL": "steel",
    "GALVANIZEDSTEEL": "galvanized_steel",
    "GIJ": "grey_cast_iron",
    "GRIJSGIETIJZER": "grey_cast_iron",
    "CASTIRON": "cast_iron",
    "NODULAIRGIETIJZER": "ductile_iron",
    "IJZER": "iron",
    "REINFORCEDCONCRETE": "reinforced_concrete",
    "CONCRETE": "concrete",
    "BE": "concrete",
    "GRES": "vitrified_clay",
    "ASBESTOS-CEMENT": "asbestos_cement",
    "AC": "asbestos_cement",
    "ASBESTOS": "asbestos",
    "OPTICALFIBER": "optical_fiber",
    "GLASVEZEL": "optical_fiber",
    "GV": "optical_fiber",
    "COAXIAL": "coaxial",
    "TWISTEDPAIR": "twisted_pair",
    "GLASVEZELVERSTERKTEKUNSTSTOF": "glass_reinforced_plastic",
    "GVK": "glass_reinforced_plastic",
    "BITUMEN": "bitumen",

    # Explicitly preserve "other/unknown material" as a material class
    "OVERIG": "overig",
}

UNKNOWN_MATERIAL_VALUES = EMPTY_STRINGS | {
    "niet van toepassing",
    "unknown",
}

MATERIAL_TOKENS = sorted(
    MATERIAL_MAP,
    key=len,
    reverse=True
)

CONDUCTIVE_MATERIALS = {
    "copper",
    "stainless_steel",
    "steel",
    "galvanized_steel",
    "grey_cast_iron",
    "cast_iron",
    "ductile_iron",
    "iron",
}


# =============================================================================
# Helper functions
# =============================================================================

def list_layer_names(gpkg_path: Path) -> list[str]:
    """
    Return all layer names from a GeoPackage.
    """
    layers_info = pyogrio.list_layers(gpkg_path)
    return [row[0] for row in layers_info]


def resolve_existing_columns(existing_columns, candidate_columns) -> list[str]:
    """
    Resolve candidate column names against actual dataframe columns.

    This is case-insensitive but returns the actual original column names.
    """
    existing_columns = list(existing_columns)

    lower_to_actual = {}
    for col in existing_columns:
        lower_to_actual.setdefault(col.lower(), col)

    resolved = []

    for candidate in candidate_columns:
        if candidate in existing_columns:
            actual = candidate
        else:
            actual = lower_to_actual.get(candidate.lower())

        if actual is not None and actual not in resolved:
            resolved.append(actual)

    return resolved


def clean_text_series(series: pd.Series) -> pd.Series:
    """
    Convert a series to clean nullable strings.

    - Keeps real NULL values as NULL
    - Strips whitespace
    - Collapses repeated whitespace
    - Converts known empty-like strings to NULL
    """
    cleaned = (
        series
        .astype("string")
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    empty_mask = cleaned.isna() | cleaned.str.lower().isin(EMPTY_STRINGS)
    cleaned = cleaned.mask(empty_mask, pd.NA)

    return cleaned


def combine_nonempty_values(values, separator: str = " | "):
    """
    Combine non-empty values from one row into one string.

    Duplicate values are removed case-insensitively while preserving order.
    """
    result = []
    seen = set()

    for value in values:
        if pd.isna(value):
            continue

        text = str(value).strip()

        if not text:
            continue

        key = text.casefold()

        if key not in seen:
            result.append(text)
            seen.add(key)

    if result:
        return separator.join(result)

    return pd.NA


def combine_columns(
    df: pd.DataFrame,
    columns: list[str],
    separator: str = " | "
) -> pd.Series:
    """
    Combine multiple text columns row-wise.
    """
    if not columns:
        return pd.Series(pd.NA, index=df.index, dtype="string")

    cleaned_df = pd.DataFrame(
        {col: clean_text_series(df[col]) for col in columns},
        index=df.index
    )

    combined = cleaned_df.apply(
        lambda row: combine_nonempty_values(
            row.to_list(),
            separator=separator
        ),
        axis=1
    )

    return combined.astype("string")


def clean_material(value):
    """Normalize a raw material value using the known material tokens."""
    if pd.isna(value):
        return pd.NA

    text = re.sub(r"\s+", " ", str(value).strip())

    if text.lower() in UNKNOWN_MATERIAL_VALUES:
        return pd.NA

    upper_text = text.upper()
    compact_text = re.sub(r"[^A-Z0-9\-]", "", upper_text)

    for token in MATERIAL_TOKENS:
        token_upper = token.upper()
        token_compact = re.sub(r"[^A-Z0-9\-]", "", token_upper)
        pattern = (
            r"(?<![A-Z0-9])"
            + re.escape(token_upper)
            + r"(?![A-Z0-9])"
        )

        if re.search(pattern, upper_text):
            return MATERIAL_MAP[token]

        if len(token_compact) >= 4 and token_compact in compact_text:
            return MATERIAL_MAP[token]

    return pd.NA


def determine_conductive(material_norm):
    """Classify normalized material as conductive, non-conductive, or NULL."""

    if pd.isna(material_norm):
        return pd.NA

    if str(material_norm).strip().lower() == "overig":
        return pd.NA

    if str(material_norm).strip().lower() in CONDUCTIVE_MATERIALS:
        return "yes"

    return "no"


def add_material_classification(gdf):
    """Add normalized material and conductivity columns to a dataframe."""
    if MATERIAL_OUTPUT_COL not in gdf.columns:
        return gdf, {
            "n_material_normalized": 0,
            "n_conductive_yes": 0,
            "n_conductive_no": 0,
            "n_conductive_null": 0,
        }

    gdf[MATERIAL_NORMALIZED_COL] = (
        gdf[MATERIAL_OUTPUT_COL]
        .apply(clean_material)
        .astype("string")
    )
    gdf[CONDUCTIVE_COL] = (
        gdf[MATERIAL_NORMALIZED_COL]
        .apply(determine_conductive)
        .astype("string")
    )

    return gdf, {
        "n_material_normalized": int(gdf[MATERIAL_NORMALIZED_COL].notna().sum()),
        "n_conductive_yes": int((gdf[CONDUCTIVE_COL] == "yes").sum()),
        "n_conductive_no": int((gdf[CONDUCTIVE_COL] == "no").sum()),
        "n_conductive_null": int(gdf[CONDUCTIVE_COL].isna().sum()),
    }


# =============================================================================
# Diameter conversion
# =============================================================================

def convert_diameter_to_mm(gdf):
    """
    Convert buisDiameter to millimetres using buisDiameterM.

    Expected units in buisDiameterM:
        mm -> x1
        cm -> x10
        m  -> x1000

    The converted value replaces buisDiameter.

    If the diameter or unit is missing/unknown, the converted diameter
    is set to NULL for that feature.

    Returns:
        gdf, stats
    """

    diameter_cols = resolve_existing_columns(
        gdf.columns,
        DIAMETER_VALUE_CANDIDATES
    )

    unit_cols = resolve_existing_columns(
        gdf.columns,
        DIAMETER_UNIT_CANDIDATES
    )

    # If either column does not exist, nothing to convert.
    if not diameter_cols or not unit_cols:
        return gdf, {
            "diameter_value_column": diameter_cols[0] if diameter_cols else None,
            "diameter_unit_column": unit_cols[0] if unit_cols else None,
            "n_converted": 0,
            "n_missing_diameter": 0,
            "n_missing_unit": 0,
            "n_unknown_unit": 0,
        }

    diameter_col = diameter_cols[0]
    unit_col = unit_cols[0]

    # Convert diameter values to numeric.
    #
    # errors="coerce" means invalid text becomes NaN instead of crashing.
    diameter_numeric = pd.to_numeric(
        gdf[diameter_col],
        errors="coerce"
    )

    # Clean and normalize units.
    units = (
        gdf[unit_col]
        .astype("string")
        .str.strip()
        .str.lower()
    )

    # Apply conversion factors.
    factors = units.map(DIAMETER_UNIT_FACTORS)

    # Calculate converted diameter.
    converted = diameter_numeric * factors

    # Statistics
    has_diameter = diameter_numeric.notna()
    has_unit = units.notna()
    known_unit = factors.notna()

    n_converted = int(
        (has_diameter & has_unit & known_unit).sum()
    )

    n_missing_diameter = int(
        (~has_diameter).sum()
    )

    n_missing_unit = int(
        (has_diameter & ~has_unit).sum()
    )

    n_unknown_unit = int(
        (has_diameter & has_unit & ~known_unit).sum()
    )

    # Replace original diameter with converted mm value and update the unit
    # column for rows that were successfully converted.
    gdf[diameter_col] = converted
    gdf.loc[converted.notna(), unit_col] = "mm"

    return gdf, {
        "diameter_value_column": diameter_col,
        "diameter_unit_column": unit_col,
        "n_converted": n_converted,
        "n_missing_diameter": n_missing_diameter,
        "n_missing_unit": n_missing_unit,
        "n_unknown_unit": n_unknown_unit,
    }


# =============================================================================
# Material
# =============================================================================

def add_material_column(gdf):
    """
    Add or replace the material column.

    Priority:
    1. buismateriaalType / telecomKabelMateriaalType
    2. label / lable / toelichting
    3. NULL
    """
    material_source_cols = resolve_existing_columns(
        gdf.columns,
        MATERIAL_SOURCE_CANDIDATES
    )

    fallback_text_cols = resolve_existing_columns(
        gdf.columns,
        FALLBACK_TEXT_CANDIDATES
    )

    direct_material = combine_columns(
        gdf,
        material_source_cols
    )

    fallback_material = combine_columns(
        gdf,
        fallback_text_cols
    )

    material = direct_material.where(
        direct_material.notna(),
        fallback_material
    )

    gdf[MATERIAL_OUTPUT_COL] = material.astype("string")

    # Put material as the last attribute column, before geometry if geometry exists.
    geometry_col = None

    if hasattr(gdf, "geometry") and gdf.geometry.name in gdf.columns:
        geometry_col = gdf.geometry.name

    non_geometry_cols = [
        col for col in gdf.columns
        if col != geometry_col
    ]

    if MATERIAL_OUTPUT_COL in non_geometry_cols:
        non_geometry_cols.remove(MATERIAL_OUTPUT_COL)

    ordered_cols = non_geometry_cols + [MATERIAL_OUTPUT_COL]

    if geometry_col is not None:
        ordered_cols += [geometry_col]

    gdf = gdf[ordered_cols]

    return gdf, {
        "material_source_columns": material_source_cols,
        "fallback_text_columns": fallback_text_cols,
        "n_features": len(gdf),
        "n_from_material_columns": int(
            direct_material.notna().sum()
        ),
        "n_from_label_or_toelichting": int(
            direct_material.isna().sum()
            - material.isna().sum()
        ),
        "n_material_null": int(
            material.isna().sum()
        ),
    }


def harmonize_units_materials(
    input_path: Path,
    output_path: Path,
) -> Path:

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input GeoPackage does not exist: {input_path}"
        )

    # Make sure output directory exists
    output_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # Remove old output
    if output_path.exists():
        output_path.unlink()

    layers = list_layer_names(input_path)

    print(f"Found {len(layers)} layers:")

    for layer in layers:
        print(f" - {layer}")

    report_rows = []

    for layer in layers:

        print(f"\nProcessing layer: {layer}")

        gdf = pyogrio.read_dataframe(
            input_path,
            layer=layer
        )

        # --------------------------------------------------------
        # Convert pipe diameters to mm
        # --------------------------------------------------------

        gdf, diameter_stats = convert_diameter_to_mm(gdf)

        # --------------------------------------------------------
        # Add material column
        # --------------------------------------------------------

        gdf, material_stats = add_material_column(gdf)

        # --------------------------------------------------------
        # Normalize material and classify conductivity
        # --------------------------------------------------------

        gdf, classification_stats = add_material_classification(gdf)

        # --------------------------------------------------------
        # Write layer
        # --------------------------------------------------------

        pyogrio.write_dataframe(
            gdf,
            output_path,
            layer=layer,
            driver="GPKG"
        )

        # --------------------------------------------------------
        # Report
        # --------------------------------------------------------

        report_rows.append({
            "layer": layer,

            "n_features": material_stats["n_features"],

            "material_source_columns": ", ".join(
                material_stats["material_source_columns"]
            ),

            "fallback_text_columns": ", ".join(
                material_stats["fallback_text_columns"]
            ),

            "n_from_material_columns":
                material_stats["n_from_material_columns"],

            "n_from_label_or_toelichting":
                material_stats["n_from_label_or_toelichting"],

            "n_material_null":
                material_stats["n_material_null"],

            "n_material_normalized":
                classification_stats["n_material_normalized"],

            "n_conductive_yes":
                classification_stats["n_conductive_yes"],

            "n_conductive_no":
                classification_stats["n_conductive_no"],

            "n_conductive_null":
                classification_stats["n_conductive_null"],

            "diameter_value_column":
                diameter_stats["diameter_value_column"],

            "diameter_unit_column":
                diameter_stats["diameter_unit_column"],

            "n_diameter_converted":
                diameter_stats["n_converted"],

            "n_diameter_missing":
                diameter_stats["n_missing_diameter"],

            "n_diameter_missing_unit":
                diameter_stats["n_missing_unit"],

            "n_diameter_unknown_unit":
                diameter_stats["n_unknown_unit"],
        })

        # Your existing print statements can stay here.

    # ------------------------------------------------------------
    # Write processing report
    # ------------------------------------------------------------

    report_df = pd.DataFrame(report_rows)

    report_csv = output_path.with_name(
        f"{output_path.stem}_material_report.csv"
    )

    report_df.to_csv(
        report_csv,
        index=False
    )

    print("\nDone.")
    print(f"Written GeoPackage: {output_path}")
    print(f"Written report:     {report_csv}")

    return output_path