#Script 3

from pathlib import Path

import pandas as pd
import geopandas as gpd
import pyogrio

# ==============================================================
# Config
# ==============================================================
DATA_DIR = Path("../data/KLIC/KLIC_groenedijk/temp")

LAYERS_TO_USE = [
    "Waterleiding",
    "ThermischePijpleiding",
    "Rioolleiding",
    "OlieGasChemicalienPijpleiding",
    "Kabelbed",
    "Elektriciteitskabel",
    "Duct",
    "Overig",
    "Mast",
]

# Layers considered by the cleaning step (post-routing, so Duct/Kabelbed
# have already been merged into the layers below)
CLEAN_LAYERS = [
    "Waterleiding",
    "ThermischePijpleiding",
    "Rioolleiding",
    "OlieGasChemicalienPijpleiding",
    "Elektriciteitskabel",
    "Overig",
    "Mast",
]

# True  = route all layers based on utilityNetworkType
# False = only route layers listed in MOVE_LAYERS
ROUTE_ALL_LAYERS = False
MOVE_LAYERS = ["Duct", "Kabelbed"]

UTILITY_COL = "utilityNetworkType"
ADD_ORIGINAL_LAYER_FIELD = True

UTILITY_TO_LAYER = {
    "water": "Waterleiding",
    "thermal": "ThermischePijpleiding",
    "sewer": "Rioolleiding",
    "oilGasChemical": "OlieGasChemicalienPijpleiding",
    "electricity": "Elektriciteitskabel",
    "telecommunications": "Telecommunicatiekabel",
    "overig": "Overig",
    "Mast": "Mast",
    "ExtraGeometrie": "ExtraGeometrie",
}

OUTPUT_LAYERS = sorted(set(UTILITY_TO_LAYER.values()))

VERT_COL = "verticalePositie"

# If the left-side token is found in the vertical position text,
# the value is replaced by the right-side normalized value.
VERTICAL_POSITION_MAP = {
    # Underground
    " underground": "underground",
    "UNDERGROUND": "underground",
    "BURIED": "underground",
    "IN GROUND": "underground",
    "SUBTERRANEAN": "underground",

    # Aboveground
    "suspendedOrElevated": "aboveground",
    "SUSPENDED": "aboveground",
    "ELEVATED": "aboveground",
    "ABOVEGROUND": "aboveground",
    "OVERGROUND": "aboveground",
}

GML_COL = "gml_id"
NAME_COL = "name"
STATION_LAYER = "ExtraGeometrie"


# ==============================================================
# Helpers - routing
# ==============================================================
def _target_layer_from_utility(value):
    if pd.isna(value) or str(value).strip() == "":
        return "Overig"

    value = str(value).strip()
    return UTILITY_TO_LAYER.get(value, "Overig")


def _read_layer(path, layer):
    gdf = pyogrio.read_dataframe(path, layer=layer, read_geometry=True)

    if gdf.geometry.name != "geometry":
        gdf = gdf.rename_geometry("geometry")

    return gdf


def _route_layers(input_path):
    """Route KLIC layers into standardized layers by utilityNetworkType, in memory.

    Layers not selected for routing (see MOVE_LAYERS / ROUTE_ALL_LAYERS above)
    are copied through unchanged. Returns a dict of {layer_name: GeoDataFrame}
    for every non-empty output layer. Nothing is written to disk here.
    """
    available_layers = [name for name, _ in pyogrio.list_layers(input_path)]

    parts_by_output_layer = {layer: [] for layer in OUTPUT_LAYERS}
    routing_report = []

    if ROUTE_ALL_LAYERS:
        layers_to_route = LAYERS_TO_USE
        layers_to_copy = []
    else:
        layers_to_route = MOVE_LAYERS
        # Copy all normal output layers as-is.
        # Duct/Kabelbed are not copied as separate output layers.
        layers_to_copy = [
            layer for layer in OUTPUT_LAYERS
            if layer in LAYERS_TO_USE and layer not in MOVE_LAYERS
        ]

    # Copy layers that should not be routed
    for source_layer in layers_to_copy:
        if source_layer not in available_layers:
            print(f"Layer missing, skipped: {source_layer}")
            continue

        gdf = _read_layer(input_path, source_layer)

        if ADD_ORIGINAL_LAYER_FIELD:
            gdf["originalLayer"] = source_layer

        parts_by_output_layer[source_layer].append(gdf)

        routing_report.append({
            "source_layer": source_layer,
            "target_layer": source_layer,
            "feature_count": len(gdf),
            "action": "copied",
        })

        print(f"Copied {len(gdf)} features from {source_layer} to {source_layer}")

    # Route selected layers by utilityNetworkType
    for source_layer in layers_to_route:
        if source_layer not in available_layers:
            print(f"Layer missing, skipped: {source_layer}")
            continue

        gdf = _read_layer(input_path, source_layer)

        if UTILITY_COL not in gdf.columns:
            print(f"Column '{UTILITY_COL}' missing, sending whole layer to Overig: {source_layer}")
            gdf["_target_layer"] = "Overig"
        else:
            gdf["_target_layer"] = gdf[UTILITY_COL].apply(_target_layer_from_utility)

        for target_layer, part in gdf.groupby("_target_layer", dropna=False):
            if target_layer not in parts_by_output_layer:
                target_layer = "Overig"

            part = part.drop(columns=["_target_layer"])

            if ADD_ORIGINAL_LAYER_FIELD:
                # The layer where this feature ends up after processing
                part["originalLayer"] = target_layer

            parts_by_output_layer[target_layer].append(part)

            routing_report.append({
                "source_layer": source_layer,
                "target_layer": target_layer,
                "feature_count": len(part),
                "action": "routed",
            })

            if source_layer == target_layer:
                print(f"Kept {len(part)} features in {target_layer}")
            else:
                print(f"Moved {len(part)} features from {source_layer} to {target_layer}")

    # Combine parts into a single in-memory GeoDataFrame per output layer
    routed_layers = {}
    for output_layer, parts in parts_by_output_layer.items():
        if not parts:
            print(f"Skipping empty output layer: {output_layer}")
            continue

        combined = pd.concat(parts, ignore_index=True, sort=False)

        crs = None
        for part in parts:
            if getattr(part, "crs", None) is not None:
                crs = part.crs
                break

        combined = gpd.GeoDataFrame(combined, geometry="geometry", crs=crs)

        cols = [col for col in combined.columns if col != "geometry"] + ["geometry"]
        combined = combined[cols]

        routed_layers[output_layer] = combined

        print(f"Routed {len(combined)} features into {output_layer}")

    report = pd.DataFrame(routing_report)
    print("\nRouting summary:")
    print(
        report
        .groupby(["action", "source_layer", "target_layer"], as_index=False)["feature_count"]
        .sum()
    )

    return routed_layers


# ==============================================================
# Helpers - cleaning
# ==============================================================
def _normalize_vertical_position(value):
    if pd.isna(value):
        return None

    text = str(value).strip()
    if text == "":
        return None

    upper_text = text.upper()
    for token, normalized in VERTICAL_POSITION_MAP.items():
        if token.upper() in upper_text:
            return normalized

    return None


def _infer_station_name_from_gml_id(value):
    if pd.isna(value):
        return None

    text = str(value).strip().lower()
    if text == "":
        return None

    # Normalize separators so that:
    # HSStation, HS_Station, HS-Station, HS Station all become comparable
    normalized = (
        text.replace("_", "").replace("-", "").replace(" ", "").replace(".", "")
    )

    if "hsstation" in normalized or "hoogspanningsstation" in normalized:
        return "HVStation"

    if "msstation" in normalized or "middenspanningsstation" in normalized:
        return "MSStation"

    return None


def _clean_layers(routed_layers, output_path):
    """Normalize vertical position values and derive station names, writing
    the result straight to output_path.

    Applies _normalize_vertical_position() to VERT_COL on every layer in
    CLEAN_LAYERS, and additionally derives a NAME_COL for STATION_LAYER
    (ExtraGeometrie) based on its gml_id.
    """
    if output_path.exists():
        output_path.unlink()

    for layer in CLEAN_LAYERS:
        gdf = routed_layers.get(layer)
        if gdf is None:
            print(f"Layer missing or empty, skipped: {layer}")
            continue

        if VERT_COL in gdf.columns:
            gdf[VERT_COL] = gdf[VERT_COL].apply(_normalize_vertical_position)
        else:
            print(f"Column '{VERT_COL}' missing in layer: {layer}")

        if layer == STATION_LAYER:
            if GML_COL in gdf.columns:
                gdf[NAME_COL] = gdf[GML_COL].apply(_infer_station_name_from_gml_id)
                print(gdf[[GML_COL, NAME_COL]].drop_duplicates().head(20))
            else:
                print(f"Column '{GML_COL}' missing in layer: {layer}")
                gdf[NAME_COL] = None

        pyogrio.write_dataframe(
            gdf, output_path, layer=layer, driver="GPKG", geometry_type="Unknown"
        )

        print(f"Wrote layer: {layer}")

    print(f"\nDone. Wrote cleaned GeoPackage to: {output_path}")


# ==============================================================
# Public entry point
# ==============================================================
def reorder_utility_types_and_vertical(
    input_path: Path,
    output_path: Path,
) -> Path:
    """Run the full KLIC pipeline: route layers, then clean + normalize them.

    Import and call this from your own main.py, e.g.:

        from reorder_ut import process_klic_data
        process_klic_data(Path("data/KLIC_in.gpkg"), Path("data/KLIC_out.gpkg"))
    """
    print("=== Step 1: Routing layers by utility network type ===")
    routed_layers = _route_layers(input_path)

    print("\n=== Step 2: Normalizing vertical position + station names ===")
    _clean_layers(routed_layers, output_path)

    return output_path

#if __name__ == "__main__":
#     reorder_utility_types_and_vertical(
#        input_path= "data/KLIC/KLIC_groenedijk/temp/KLIC_temp2.gpkg",
#        output_path= "data/KLIC/KLIC_groenedijk/temp/KLIC_temp3.gpkg",
#    )


