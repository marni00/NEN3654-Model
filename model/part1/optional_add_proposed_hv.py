#Script 6

from pathlib import Path
from shutil import copy2

import geopandas as gpd


# Technical constants should normally not be changed by users.
MINIMUM_VOLTAGE = 1_000
MEDIUM_VOLTAGE_MAXIMUM = 35_000
MAXIMUM_VOLTAGE = 380_000

ALLOWED_VERTICAL_POSITIONS = {
    "underground",
    "aboveground",
    "TEV",
}

OUTPUT_LAYER_NAME = "proposed_highvoltage"


def set_voltage_class(voltage: int) -> str | None:
    """Derive the voltage class from the working voltage."""
    if MINIMUM_VOLTAGE <= voltage <= MEDIUM_VOLTAGE_MAXIMUM:
        return "middenspanning"

    if MEDIUM_VOLTAGE_MAXIMUM < voltage <= MAXIMUM_VOLTAGE:
        return "hoogspanning"

    return None


def validate_settings(
    work_voltage: int,
    vertical_position: str,
    code: str,
) -> None:
    """Validate the user-configurable settings."""
    if not isinstance(work_voltage, int):
        raise TypeError(
            "'work_voltage' in config.toml must be an integer."
        )

    if not MINIMUM_VOLTAGE <= work_voltage <= MAXIMUM_VOLTAGE:
        raise ValueError(
            f"'work_voltage' must be between "
            f"{MINIMUM_VOLTAGE} and {MAXIMUM_VOLTAGE}. "
            f"Received: {work_voltage}"
        )

    if vertical_position not in ALLOWED_VERTICAL_POSITIONS:
        allowed_values = ", ".join(
            sorted(ALLOWED_VERTICAL_POSITIONS)
        )

        raise ValueError(
            f"Invalid 'vertical_position': {vertical_position!r}. "
            f"Allowed values: {allowed_values}"
        )

    if not isinstance(code, str):
        raise TypeError(
            "'code' in config.toml must be a string."
        )


def add_proposed_highvoltage(
    input_path: Path,
    proposed_hv_path: Path,
    output_path: Path,
    work_voltage: int,
    vertical_position: str,
    code: str,
) -> None:
    """
    Copy a GeoPackage and add proposed high-voltage features as a new layer.
    """
    input_path = Path(input_path)
    proposed_hv_path = Path(proposed_hv_path)
    output_path = Path(output_path)

    validate_settings(
        work_voltage=work_voltage,
        vertical_position=vertical_position,
        code=code,
    )

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input GeoPackage does not exist: {input_path}"
        )

    if not proposed_hv_path.exists():
        raise FileNotFoundError(
            f"Proposed high-voltage file does not exist: "
            f"{proposed_hv_path}"
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Replace an existing output from a previous pipeline run.
    if output_path.exists():
        output_path.unlink()

    copy2(
        input_path,
        output_path,
    )

    # ---------------------------------------------------------
    # Read proposed high-voltage data
    # ---------------------------------------------------------
    proposed_hv = gpd.read_file(proposed_hv_path)

    proposed_hv = proposed_hv[
        proposed_hv.geometry.notna()
        & ~proposed_hv.geometry.is_empty
    ].copy()

    if proposed_hv.empty:
        raise ValueError(
            "The proposed high-voltage dataset contains "
            "no valid geometries."
        )

    # ---------------------------------------------------------
    # Add configured attributes
    # ---------------------------------------------------------
    proposed_hv["werkVoltage"] = work_voltage
    proposed_hv["verticalePositie"] = vertical_position
    proposed_hv["Code"] = code.strip()

    proposed_hv["voltage_class"] = proposed_hv[
        "werkVoltage"
    ].apply(set_voltage_class)

    # ---------------------------------------------------------
    # Match CRS with the existing GeoPackage
    # ---------------------------------------------------------
    layers = gpd.list_layers(output_path)

    if layers.empty:
        raise ValueError(
            f"No layers found in GeoPackage: {output_path}"
        )

    reference_layer = layers.iloc[0]["name"]

    reference_gdf = gpd.read_file(
        output_path,
        layer=reference_layer,
    )

    if reference_gdf.crs is None:
        raise ValueError(
            f"Reference layer '{reference_layer}' has no CRS."
        )

    if proposed_hv.crs is None:
        raise ValueError(
            f"Input file has no CRS: {proposed_hv_path}"
        )

    if proposed_hv.crs != reference_gdf.crs:
        proposed_hv = proposed_hv.to_crs(reference_gdf.crs)

    # ---------------------------------------------------------
    # Keep only required columns
    # ---------------------------------------------------------
    proposed_hv = proposed_hv[
        [
            "werkVoltage",
            "verticalePositie",
            "Code",
            "voltage_class",
            "geometry",
        ]
    ].copy()

    # ---------------------------------------------------------
    # Write the new layer
    # ---------------------------------------------------------
    proposed_hv.to_file(
        output_path,
        layer=OUTPUT_LAYER_NAME,
        driver="GPKG",
        mode="a",
    )

    print(f"Added layer '{OUTPUT_LAYER_NAME}' to:")
    print(output_path)
    print(f"Number of features written: {len(proposed_hv)}")
    print(f"CRS: {proposed_hv.crs}")