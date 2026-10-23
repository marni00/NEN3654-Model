import json
import shutil
import tempfile
from datetime import datetime
from getpass import getpass
from pathlib import Path

from arcgis.gis import GIS

# GUI INTEGRATION CHANGE: resolve the actual repository root and the existing
# config location. The former parents[3]/symbology.json path pointed outside
# this project and prevented both CLI and GUI uploads.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
SYMBOL_JSON = PROJECT_ROOT / "config" / "symbology.json"

def get_dataset_location_name(gpkg_folder: Path) -> str:
    """
    Return the KLIC project/location name from the parent folder.

    Example:
        .../KLIC/KLIC_groenedijk/output
        -> KLIC_groenedijk
    """
    return gpkg_folder.parent.name

def normalise_layer_name(layer_name: str) -> str:
    """
    Return a business layer name without an optional SQLite schema prefix.

    Examples:
        main.Elektriciteitskabel -> Elektriciteitskabel
        Elektriciteitskabel      -> Elektriciteitskabel
    """
    return layer_name.removeprefix("main.")

def connect_to_arcgis(
    portal_url: str,
    username: str,
    password: str | None = None,
) -> GIS:
    """Connect to ArcGIS Online using supplied or interactively entered credentials."""

    # GUI INTEGRATION CHANGE: accept an in-memory password so the desktop GUI
    # can use a masked dialog without storing or logging the secret. CLI use
    # retains the existing getpass behavior.
    effective_password = password if password is not None else getpass(
        "ArcGIS Online password: "
    )

    gis = GIS(
        url=portal_url,
        username=username,
        password=effective_password,
    )

    user = gis.users.me

    if user is None:
        raise RuntimeError("Authentication failed.")

    print("Authentication successful!")
    print("Username:", user.username)
    print("Organisation:", gis.properties.name)

    return gis


def load_symbology_config(symbology_path: Path) -> dict:
    """Load symbology configuration from JSON."""
    if not symbology_path.is_file():
        raise FileNotFoundError(
            f"Symbology JSON file not found: {symbology_path}"
        )

    with symbology_path.open("r", encoding="utf-8") as json_file:
        return json.load(json_file)


def get_layer_color(
    layer_name: str,
    symbology_config: dict,
) -> list[int]:
    """
    Return the RGB colour for a layer.

    An optional 'main.' prefix is ignored when looking up colours.
    If the layer is not configured, the default colour is used.
    """
    layer_colors = symbology_config.get("layer_colors", {})
    default_color = symbology_config.get(
        "default_color",
        [80, 80, 80],
    )

    clean_layer_name = normalise_layer_name(layer_name)

    return layer_colors.get(clean_layer_name, default_color)


def create_line_symbol(
    color: list[int],
    style: str,
    width: float,
) -> dict:
    """Create an ArcGIS line symbol."""
    return {
        "type": "esriSLS",
        "style": style,
        "color": color + [255],
        "width": width,
    }

def create_marker_symbol(
    color: list[int],
    size: float = 7.0,
) -> dict:
    """Create an ArcGIS point marker symbol."""
    return {
        "type": "esriSMS",
        "style": "esriSMSCircle",
        "color": color + [255],
        "size": size,
        "outline": {
            "type": "esriSLS",
            "style": "esriSLSSolid",
            "color": [255, 255, 255, 255],
            "width": 1.0,
        },
    }

def create_standard_renderer(
    color: list[int],
    symbology_config: dict,
    geometry_type: str,
) -> dict:
    """Create a simple renderer appropriate for the layer geometry."""

    is_point_layer = geometry_type in {
        "esriGeometryPoint",
        "esriGeometryMultipoint",
    }

    if is_point_layer:
        point_config = symbology_config.get(
            "standard_point",
            {
                "size": 7.0,
            },
        )

        symbol = create_marker_symbol(
            color=color,
            size=point_config["size"],
        )

    else:
        standard_config = symbology_config.get(
            "standard_line",
            {
                "style": "esriSLSSolid",
                "width": 1.0,
            },
        )

        symbol = create_line_symbol(
            color=color,
            style=standard_config["style"],
            width=standard_config["width"],
        )

    return {
        "type": "simple",
        "symbol": symbol,
    }


def create_no_pass_renderer(
    symbology_config: dict,
) -> dict:
    """
    Create symbology for an aggregated no-pass layer.

    Rules:
        - Regular infrastructure uses its configured layer colour.
        - Types in no_pass_voltage_types are split by voltage class.
        - Voltage-dependent types use both the colour and line style
          of their voltage class.
    """
    infrastructure_config = symbology_config["infrastructure_type"]
    voltage_config = symbology_config["voltage_class"]

    infrastructure_field = infrastructure_config.get(
        "field",
        "infrastructure_type",
    )

    voltage_field = voltage_config.get(
        "field",
        "thema",
    )

    infrastructure_types = infrastructure_config.get(
        "types",
        [],
    )

    no_pass_voltage_types = set(
        symbology_config.get(
            "no_pass_voltage_types",
            ["Elektriciteitskabel"],
        )
    )

    voltage_classes = voltage_config.get(
        "classes",
        {},
    )

    standard_config = symbology_config.get(
        "standard_line",
        {
            "style": "esriSLSSolid",
            "width": 1.0,
        },
    )

    unique_value_infos = []

    # ------------------------------------------------------------
    # Regular infrastructure, including proposed_highvoltage
    # ------------------------------------------------------------
    for infrastructure_type in infrastructure_types:
        if infrastructure_type in no_pass_voltage_types:
            continue

        color = get_layer_color(
            layer_name=infrastructure_type,
            symbology_config=symbology_config,
        )

        symbol = create_line_symbol(
            color=color,
            style=standard_config["style"],
            width=standard_config["width"],
        )

        unique_value_infos.append(
            {
                "value": infrastructure_type,
                "label": infrastructure_type,
                "symbol": symbol,
            }
        )

    # ------------------------------------------------------------
    # Infrastructure that is split by voltage class
    # ------------------------------------------------------------
    for infrastructure_type in no_pass_voltage_types:
        for voltage_value, voltage_style in voltage_classes.items():
            symbol = create_line_symbol(
                color=voltage_style["color"],
                style=voltage_style.get(
                    "style",
                    standard_config["style"],
                ),
                width=voltage_style.get(
                    "width",
                    standard_config["width"],
                ),
            )

            unique_value_infos.append(
                {
                    "value": (
                        f"{infrastructure_type}|{voltage_value}"
                    ),
                    "label": voltage_style["label"],
                    "symbol": symbol,
                }
            )

    default_color = symbology_config.get(
        "default_color",
        [80, 80, 80],
    )

    default_symbol = create_line_symbol(
        color=default_color,
        style=standard_config["style"],
        width=standard_config["width"],
    )

    voltage_type_values = ", ".join(
        f"'{value}'"
        for value in sorted(no_pass_voltage_types)
    )

    value_expression = f"""
        var infrastructure = Text(
            $feature.{infrastructure_field}
        );

        var voltageTypes = [{voltage_type_values}];

        if (Includes(voltageTypes, infrastructure)) {{
            var voltage = Text(
                $feature.{voltage_field}
            );

            return Concatenate(
                [infrastructure, voltage],
                '|'
            );
        }}

        return infrastructure;
    """

    return {
        "type": "uniqueValue",
        "valueExpression": value_expression,
        "valueExpressionTitle": (
            "Infrastructure and electric voltage class"
        ),
        "defaultLabel": (
            "Other / unknown infrastructure or voltage class"
        ),
        "defaultSymbol": default_symbol,
        "uniqueValueInfos": unique_value_infos,
    }

def create_voltage_renderer(
    symbology_config: dict,
) -> dict:
    """
    Create a unique-value renderer based on voltage_class.
    """

    voltage_config = symbology_config["voltage_class"]
    voltage_field = voltage_config["field"]

    unique_value_infos = []

    for value, class_config in voltage_config["classes"].items():
        symbol = create_line_symbol(
            color=class_config["color"],
            style=class_config["style"],
            width=class_config["width"],
        )

        unique_value_infos.append(
            {
                "value": value,
                "label": class_config["label"],
                "symbol": symbol,
            }
        )

    default_symbol = create_line_symbol(
        color=[80, 80, 80],
        style=voltage_config["default_style"],
        width=voltage_config["default_width"],
    )

    return {
        "type": "uniqueValue",
        "field1": voltage_field,
        "defaultLabel": "Other / unknown voltage class",
        "defaultSymbol": default_symbol,
        "uniqueValueInfos": unique_value_infos,
    }

def create_infrastructure_type_renderer(
    symbology_config: dict,
) -> dict:
    """
    Create a unique-value renderer based on infrastructure_type.

    Each infrastructure type receives the colour configured under
    layer_colors in symbology.json.
    """
    infrastructure_config = symbology_config.get(
        "infrastructure_type",
        {},
    )

    infrastructure_field = infrastructure_config.get(
        "field",
        "infrastructure_type",
    )

    infrastructure_types = infrastructure_config.get(
        "types",
        [
            "OlieGasChemicalienPijpleiding",
            "Overig",
            "Waterleiding",
            "Rioolleiding",
            "ThermischePijpleiding",
        ],
    )

    standard_config = symbology_config.get(
        "standard_line",
        {
            "style": "esriSLSSolid",
            "width": 2.0,
        },
    )

    unique_value_infos = []

    for infrastructure_type in infrastructure_types:
        color = get_layer_color(
            layer_name=infrastructure_type,
            symbology_config=symbology_config,
        )

        symbol = create_line_symbol(
            color=color,
            style=standard_config["style"],
            width=standard_config["width"],
        )

        unique_value_infos.append(
            {
                "value": infrastructure_type,
                "label": infrastructure_type,
                "symbol": symbol,
            }
        )

    default_color = symbology_config.get(
        "default_color",
        [80, 80, 80],
    )

    default_symbol = create_line_symbol(
        color=default_color,
        style=standard_config["style"],
        width=standard_config["width"],
    )

    return {
        "type": "uniqueValue",
        "field1": infrastructure_field,
        "defaultLabel": "Other / unknown infrastructure type",
        "defaultSymbol": default_symbol,
        "uniqueValueInfos": unique_value_infos,
    }

def get_field_names(layer) -> set[str]:
    """Return the field names available in an ArcGIS layer."""
    return {
        field["name"]
        for field in layer.properties.fields
    }


def apply_symbology(
    feature_layer_item,
    gpkg_name: str,
    symbology_config: dict,
):
    """
    Apply symbology to all published feature layers.

    Priority:

    1. Standalone influencer layers with a voltage-class field.
    2. Line layers in NEN3654 no-pass/result GeoPackages containing
       both infrastructure_type and the configured voltage field.
    3. Standard layer-based symbology.

    Point layers, such as Mast, use standard point symbology.
    """

    gpkg_name_lower = gpkg_name.lower()

    # Both no-pass and complete result files require the aggregated
    # infrastructure/voltage renderer.
    is_nen3654_result_file = (
        "no_pass" in gpkg_name_lower
        or "results_step" in gpkg_name_lower
    )

    influencer_layers = set(
        symbology_config.get("influencer_layers", [])
    )

    voltage_config = symbology_config.get(
        "voltage_class",
        {},
    )
    voltage_field = voltage_config.get(
        "field",
        "thema",
    )

    infrastructure_config = symbology_config.get(
        "infrastructure_type",
        {},
    )
    infrastructure_field = infrastructure_config.get(
        "field",
        "infrastructure_type",
    )

    print("\nApplying symbology...")
    print(f"GeoPackage: {gpkg_name}")
    print(
        "NEN3654 no-pass/result file: "
        f"{is_nen3654_result_file}"
    )

    for layer in feature_layer_item.layers:
        layer_name = layer.properties.name
        clean_layer_name = normalise_layer_name(layer_name)
        geometry_type = layer.properties.geometryType
        field_names = get_field_names(layer)

        is_point_layer = geometry_type in {
            "esriGeometryPoint",
            "esriGeometryMultipoint",
        }

        # --------------------------------------------------------
        # 1. Standalone influencer symbology based on voltage
        # --------------------------------------------------------
        if (
            not is_nen3654_result_file
            and clean_layer_name in influencer_layers
            and voltage_field in field_names
        ):
            renderer = create_voltage_renderer(
                symbology_config=symbology_config,
            )

            print(
                f" - {layer_name}: voltage-class symbology "
                f"based on '{voltage_field}'"
            )

        # --------------------------------------------------------
        # 2. NEN3654 no-pass/result line symbology
        #
        #    Regular infrastructure:
        #        colour based on infrastructure_type
        #
        #    Electricity:
        #        colour and line style based on voltage class
        # --------------------------------------------------------
        elif (
            is_nen3654_result_file
            and not is_point_layer
            and infrastructure_field in field_names
            and voltage_field in field_names
        ):
            renderer = create_no_pass_renderer(
                symbology_config=symbology_config,
            )

            print(
                f" - {layer_name}: infrastructure symbology "
                f"with voltage colours for electricity, based on "
                f"'{infrastructure_field}' and '{voltage_field}'"
            )

        # --------------------------------------------------------
        # 3. Standard layer-based symbology
        # --------------------------------------------------------
        else:
            color = get_layer_color(
                layer_name=layer_name,
                symbology_config=symbology_config,
            )

            renderer = create_standard_renderer(
                color=color,
                symbology_config=symbology_config,
                geometry_type=geometry_type,
            )

            print(
                f" - {layer_name}: standard symbology "
                f"with colour {color}"
            )

            # Explain why an expected result layer received the
            # standard fallback renderer.
            if (
                is_nen3654_result_file
                and not is_point_layer
            ):
                missing_fields = {
                    infrastructure_field,
                    voltage_field,
                } - field_names

                if missing_fields:
                    print(
                        "   Warning: aggregated result symbology "
                        "was not applied because the following "
                        "field(s) are missing: "
                        f"{', '.join(sorted(missing_fields))}"
                    )
                    print(
                        "   Available fields: "
                        f"{', '.join(sorted(field_names))}"
                    )

        update_result = layer.manager.update_definition(
            {
                "drawingInfo": {
                    "renderer": renderer,
                }
            }
        )

        if not update_result.get("success", False):
            print(
                f"   Warning: symbology update may have failed "
                f"for {layer_name}: {update_result}"
            )

    print("Symbology applied successfully.")


def publish_geopackage(
    gis: GIS,
    gpkg_path: Path,
    gpkg_folder: Path,
    symbology_config: dict,
    upload_timestamp: str,
):
    if not gpkg_path.is_file():
        raise FileNotFoundError(
            f"GeoPackage not found: {gpkg_path}"
        )

    location_name = get_dataset_location_name(gpkg_folder)

    title = (
        f"{gpkg_path.stem}_"
        f"{location_name}_"
        f"{upload_timestamp}"
    )

    folder_name = "KLIC"

    folder = gis.content.folders.get(folder_name)

    if folder is None:
        print(f"Creating ArcGIS folder: {folder_name}")
        folder = gis.content.folders.create(folder_name)

    # ArcGIS checks the uploaded filename separately from the item title.
    # Therefore, create a physical copy with the unique timestamped name.
    staging_folder = gpkg_folder / "_arcgis_upload"
    staging_folder.mkdir(parents=True, exist_ok=True)

    upload_path = staging_folder / f"{title}.gpkg"

    shutil.copy2(gpkg_path, upload_path)

    print("\n" + "=" * 60)
    print("Source GeoPackage:", gpkg_path)
    print("Upload filename:", upload_path.name)
    print("Item title:", title)

    try:
        upload_job = folder.add(
            item_properties={
                "title": title,
                "type": "GeoPackage",
                "tags": [
                    "KLIC",
                    "GeoPackage",
                    location_name,
                    upload_timestamp,
                ],
                "description": (
                    "KLIC data uploaded automatically "
                    "from the processing pipeline. "
                    f"Upload timestamp: {upload_timestamp}."
                ),
            },
            file=str(upload_path),
        )

        source_item = upload_job.result()

    finally:
        # Cleanup failure should not mark a successful upload as failed.
        try:
            upload_path.unlink(missing_ok=True)
        except PermissionError:
            print(
                "Warning: the temporary upload copy is still locked "
                f"and could not be removed: {upload_path}"
            )

    print("GeoPackage uploaded successfully.")
    print("Source item ID:", source_item.id)
    print("Source item page:", source_item.homepage)

    print("Publishing hosted feature layer...")

    feature_layer_item = source_item.publish(
        publish_parameters={
            "name": title,
        }
    )

    print("Publishing successful!")
    print("Feature layer ID:", feature_layer_item.id)
    print("Feature layer page:", feature_layer_item.homepage)
    print("Feature layer URL:", feature_layer_item.url)

    apply_symbology(
        feature_layer_item=feature_layer_item,
        gpkg_name=gpkg_path.name,
        symbology_config=symbology_config,
    )

    return source_item, feature_layer_item


def to_arcgis_online(
    gpkg_folder: Path,
    portal_url: str,
    username: str,
    password: str | None = None,
    symbology_path: Path | None = None,
    progress=None,
):
    """
    Upload and publish all GeoPackages from the provided folder
    to ArcGIS Online.
    """

    if not gpkg_folder.is_dir():
        raise NotADirectoryError(
            f"GeoPackage folder not found:\n{gpkg_folder}"
        )

    # GUI INTEGRATION CHANGE: injectable symbology and progress reporting
    # preserve the publisher while making orchestration observable.
    symbology_config = load_symbology_config(symbology_path or SYMBOL_JSON)

    gpkg_files = sorted(gpkg_folder.glob("*.gpkg"))

    if not gpkg_files:
        raise FileNotFoundError(
            f"No .gpkg files found in: {gpkg_folder}"
        )

    print(f"Found {len(gpkg_files)} GeoPackage file(s):")

    for gpkg_path in gpkg_files:
        print(f" - {gpkg_path.name}")

    gis = connect_to_arcgis(
        portal_url=portal_url,
        username=username,
        password=password,
    )

    # Create one timestamp for this complete upload run.
    upload_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    print(f"Upload timestamp: {upload_timestamp}")

    successful_uploads = []
    failed_uploads = []

    for index, gpkg_path in enumerate(gpkg_files, start=1):
        if progress:
            progress(index - 1, len(gpkg_files), f"Uploaden: {gpkg_path.name}")
        try:
            source_item, feature_layer_item = publish_geopackage(
                gis=gis,
                gpkg_path=gpkg_path,
                gpkg_folder=gpkg_folder,
                symbology_config=symbology_config,
                upload_timestamp=upload_timestamp,
            )

            successful_uploads.append(
                {
                    "file": gpkg_path.name,
                    "source_id": source_item.id,
                    "layer_id": feature_layer_item.id,
                    "layer_url": feature_layer_item.url,
                }
            )

        except Exception as error:
            print(f"\nFAILED: {gpkg_path.name}")
            print(f"Reason: {error}")
            failed_uploads.append(gpkg_path.name)

    print("\n" + "=" * 60)
    print("UPLOAD SUMMARY")
    print("=" * 60)
    print(f"Successful: {len(successful_uploads)}")
    print(f"Failed: {len(failed_uploads)}")

    for result in successful_uploads:
        print(
            f"\n✓ {result['file']}"
            f"\n  Feature layer: {result['layer_url']}"
        )

    if failed_uploads:
        print("\nFailed files:")

        for filename in failed_uploads:
            print(f"✗ {filename}")

    if progress:
        progress(len(gpkg_files), len(gpkg_files), "ArcGIS Online-upload afgerond")

    return {
        "successful": successful_uploads,
        "failed": failed_uploads,
    }


#if __name__ == "__main__":
#    main()