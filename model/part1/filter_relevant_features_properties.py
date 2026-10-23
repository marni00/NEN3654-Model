#Script 7
from pathlib import Path

import geopandas as gpd
import pandas as pd

copy_unchanged_layers = {"Mast", "proposed_highvoltage"}
electricity_layer = "Elektriciteitskabel"

MIN_BUISDIAMETER_MM = 80


def get_layers(gpkg_path):
    """
    List layers in a GeoPackage without Fiona.
    """
    layers_df = gpd.list_layers(gpkg_path)
    return layers_df["name"].tolist()

def parse_max_diameter(value):
    """
    Parse a single or pipe-separated diameter value and return the maximum.

    Examples:
        "914"         -> 914.0
        "914,4"       -> 914.4
        "914 | 914,4" -> 914.4
        None          -> NaN
    """
    if pd.isna(value):
        return float("nan")

    values = []

    for part in str(value).split("|"):
        part = part.strip().replace(",", ".")

        try:
            values.append(float(part))
        except ValueError:
            continue

    return max(values) if values else float("nan")

def filter_relevant_layer(gdf, layer_name):
    """
    Filtering rules:

    1. Mast and ExtraGeometrie:
       copied unchanged.

    2. proposed_highvoltage:
       copied unchanged.

    3. Elektriciteitskabel:
       keep only features where werkVoltage > 10000.
       So werkVoltage <= 10000 or null values are removed.

    4. All other layers:
       keep only features where conductive == YES.
       conductive == No, null, empty, or missing values are removed.

    5. Buisdiameter:
       For layers containing buisDiameter, remove features where
       buisDiameter < 80 mm.

       Missing/null buisDiameter values are kept.
    """

    if gdf.empty:
        return gdf.copy()

    # -------------------------------------------------------------------------
    # Unchanged layers
    # -------------------------------------------------------------------------

    if layer_name in copy_unchanged_layers:
        print(f"{layer_name}: copied unchanged.")
        return gdf.copy()

    # -------------------------------------------------------------------------
    # Electricity layer
    # -------------------------------------------------------------------------

    if layer_name == electricity_layer:

        if "werkVoltage" not in gdf.columns:
            print(
                f"{layer_name}: no werkVoltage column, "
                f"keeping no features."
            )
            return gdf.iloc[0:0].copy()

        mask = gdf["werkVoltage"] >= 10000

        relevant_gdf = gdf[mask].copy()

        # Diameter filter
        if "buisDiameter" in relevant_gdf.columns:
            diameter_numeric = relevant_gdf["buisDiameter"].apply(
                parse_max_diameter
            )

            diameter_mask = (
                diameter_numeric.isna()
                | (diameter_numeric >= MIN_BUISDIAMETER_MM)
            )

            removed_diameter = int((~diameter_mask).sum())

            relevant_gdf = relevant_gdf[diameter_mask].copy()

            print(
                f"{layer_name}: removed {removed_diameter} "
                f"features with buisDiameter < "
                f"{MIN_BUISDIAMETER_MM} mm."
            )

        print(
            f"{layer_name}: {len(gdf)} input, "
            f"{len(relevant_gdf)} kept, "
            f"{len(gdf) - len(relevant_gdf)} removed total."
        )

        return relevant_gdf

    # -------------------------------------------------------------------------
    # Conductive filter for all other layers
    # -------------------------------------------------------------------------

    if "conductive" not in gdf.columns:
        print(
            f"{layer_name}: no conductive column, "
            f"keeping no features."
        )
        return gdf.iloc[0:0].copy()

    conductive_clean = (
        gdf["conductive"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )

    mask = conductive_clean.eq("YES")

    relevant_gdf = gdf[mask].copy()

    removed_conductive = len(gdf) - len(relevant_gdf)

    # -------------------------------------------------------------------------
    # Diameter filter
    # -------------------------------------------------------------------------

    removed_diameter = 0

    if "buisDiameter" in relevant_gdf.columns:

        diameter_numeric = relevant_gdf["buisDiameter"].apply(
            parse_max_diameter
        )

        # Keep:
        #   - NULL / missing diameter
        #   - diameter >= 80 mm
        #
        # Remove:
        #   - diameter < 80 mm
        diameter_mask = (
            diameter_numeric.isna()
            | (diameter_numeric >= MIN_BUISDIAMETER_MM)
        )

        removed_diameter = int((~diameter_mask).sum())

        relevant_gdf = relevant_gdf[diameter_mask].copy()

        print(
            f"{layer_name}: removed {removed_diameter} "
            f"features with buisDiameter < "
            f"{MIN_BUISDIAMETER_MM} mm."
        )

    print(
        f"{layer_name}: {len(gdf)} input, "
        f"{len(relevant_gdf)} kept, "
        f"{len(gdf) - len(relevant_gdf)} removed total."
    )

    return relevant_gdf


# =============================================================================
# Main processing
# =============================================================================

def filter_relevant_features_properties(
    input_path: Path,
    output_path: Path,
) -> Path:

    # Remove existing relevant output
    if output_path.exists():
        output_path.unlink()

    layers = get_layers(input_path)

    for layer in layers:

        gdf = gpd.read_file(
            input_path,
            layer=layer,
            engine="pyogrio",
        )

        relevant_gdf = filter_relevant_layer(
            gdf,
            layer
        )

        relevant_gdf.to_file(
            output_path,
            layer=layer,
            driver="GPKG",
            engine="pyogrio",
        )

    print(
        f"Done. Relevant GeoPackage written to: "
        f"{output_path}"
    )

    return output_path