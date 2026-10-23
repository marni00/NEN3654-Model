from pathlib import Path
import sqlite3
import warnings
import tomllib

import geopandas as gpd
import pandas as pd
from shapely.geometry import GeometryCollection
from shapely.ops import linemerge


# ============================================================
# CONFIGURATION
# ============================================================

# These layers are selected using persistent IDs from the TOML file.
INFLUENCED_LAYERS = [
    "OlieGasChemicalienPijpleiding",
    "Overig",
    "Waterleiding",
    "Rioolleiding",
    "ThermischePijpleiding",
]

# Only these layers are filtered by theme, buffer, and length.
INFLUENCER_LAYERS = [
    "proposed_highvoltage",
    "Elektriciteitskabel",
]

# GUI selections are explicit for both conduit and influencer layers.
# Missing influencer tables keep the historical standalone behaviour ("all").
GUI_SELECTABLE_LAYERS = INFLUENCED_LAYERS + INFLUENCER_LAYERS

# These layers must be present as tables in the TOML file.
# "Overig" is optional. If absent, none of its features are selected.
REQUIRED_TOML_LAYERS = [
    "OlieGasChemicalienPijpleiding",
    "Waterleiding",
    "Rioolleiding",
    "ThermischePijpleiding",
]

THEME_COLUMN = "inductive_theme"

THEME_LE24KV = "inductive_le24kv"
THEME_GT24KV = "inductive_gt24kv"
THEME_ABOVEGROUND = "inductive_ag"

BUFFER_LE24KV_M = 550.0
BUFFER_GT24KV_M = 850.0
BUFFER_ABOVEGROUND_M = 1610.0

MIN_LENGTH_UG_LE_24KV_M = 350.0
MIN_LENGTH_UG_GT_24KV_M = 20.0
MIN_LENGTH_AG_M = 10.0

TARGET_CRS = "EPSG:28992"
GPD_ENGINE = "pyogrio"

# Empty layers are omitted when False.
WRITE_EMPTY_LAYERS = False


# ============================================================
# GEOPACKAGE FUNCTIONS
# ============================================================

def list_gpkg_layers(gpkg_path: Path) -> list[str]:
    """Return all feature layers in a GeoPackage."""
    with sqlite3.connect(gpkg_path) as connection:
        rows = connection.execute(
            """
            SELECT table_name
            FROM gpkg_contents
            WHERE data_type = 'features'
            ORDER BY table_name
            """
        ).fetchall()

    return [row[0] for row in rows]


def read_layer_with_fid(
    gpkg_path: Path,
    layer_name: str,
) -> gpd.GeoDataFrame:
    """
    Read a layer with the original GeoPackage/OGR FID as its index.

    TOML FID values are matched against this index.
    """
    gdf = gpd.read_file(
        gpkg_path,
        layer=layer_name,
        engine=GPD_ENGINE,
        fid_as_index=True,
    )

    gdf.index.name = "source_fid"

    return gdf


def write_layer(
    gdf: gpd.GeoDataFrame,
    output_path: Path,
    layer_name: str,
) -> bool:
    """
    Write a layer without writing the source FID index as an attribute.

    Returns True if the layer was written, otherwise False.
    """
    if gdf.empty and not WRITE_EMPTY_LAYERS:
        print(f"  skipped empty output layer: {layer_name}")
        return False

    gdf.to_file(
        output_path,
        layer=layer_name,
        driver="GPKG",
        engine=GPD_ENGINE,
        index=False,
    )

    return True


def ensure_analysis_crs(
    gdf: gpd.GeoDataFrame,
    layer_name: str,
) -> gpd.GeoDataFrame:
    """
    Return a copy suitable for distance and length analysis in metres.

    The original GeoDataFrame is still used for output writing.
    """
    if gdf.crs is None:
        warnings.warn(
            f"Layer '{layer_name}' has no CRS. "
            f"Assuming {TARGET_CRS}."
        )

        return gdf.set_crs(
            TARGET_CRS,
            allow_override=True,
        )

    if gdf.crs.to_string().upper() != TARGET_CRS.upper():
        return gdf.to_crs(TARGET_CRS)

    return gdf.copy()


# ============================================================
# TOML FUNCTIONS
# ============================================================

def load_id_selection(
    config_path: Path,
) -> dict[str, dict[str, set[int] | str | bool]]:
    """
    Read persistent feature IDs and combine settings from the TOML file.

    Supported formats:

        [Waterleiding]
        ids = [1, 2, 3]
        combine = false

    or:

        [Waterleiding]
        ids = "all"
        combine = true

    The deprecated key ``fids`` is accepted as an alias for ``ids`` so
    existing standalone configuration files fail safely into the new
    persistent-ID matching logic rather than native GeoPackage FID matching.
    """
    if not config_path.exists():
        raise FileNotFoundError(
            f"Selection TOML file does not exist: {config_path}"
        )

    with config_path.open("rb") as config_file:
        config = tomllib.load(config_file)

    missing_tables = [
        layer_name
        for layer_name in REQUIRED_TOML_LAYERS
        if layer_name not in config
    ]

    if missing_tables:
        raise ValueError(
            "The following required layer tables are missing from "
            f"{config_path}:\n"
            f"{missing_tables}"
        )

    selection: dict[str, dict[str, set[int] | str | bool]] = {}

    for layer_name in GUI_SELECTABLE_LAYERS:
        default_ids = "all" if layer_name in INFLUENCER_LAYERS else []
        layer_config = config.get(layer_name, {"ids": default_ids})

        if not isinstance(layer_config, dict):
            raise TypeError(
                f"TOML entry [{layer_name}] must be a table."
            )

        has_ids = "ids" in layer_config
        has_legacy_fids = "fids" in layer_config
        if has_ids and has_legacy_fids:
            raise ValueError(
                f"[{layer_name}] contains both 'ids' and deprecated 'fids'. "
                "Keep only 'ids'."
            )
        if has_legacy_fids:
            warnings.warn(
                f"[{layer_name}].fids is deprecated; use 'ids'. Values are "
                "matched against the persistent 'id' attribute.",
                DeprecationWarning,
                stacklevel=2,
            )

        configured_ids = layer_config.get(
            "ids",
            layer_config.get("fids", default_ids),
        )
        combine = layer_config.get("combine", False)

        if not isinstance(combine, bool):
            raise TypeError(
                f"'{layer_name}.combine' must be either true or false."
            )

        if isinstance(configured_ids, str):
            if configured_ids.strip().lower() != "all":
                raise ValueError(
                    f"Invalid value for '{layer_name}.ids': "
                    f"{configured_ids!r}. Use an integer list such as "
                    '[1, 2, 3], or "all".'
                )

            selection[layer_name] = {
                "ids": "all",
                "combine": combine,
            }
            continue

        if not isinstance(configured_ids, list):
            raise TypeError(
                f"'{layer_name}.ids' must be either:\n"
                "  ids = [1, 2, 3]\n"
                "or:\n"
                '  ids = "all"'
            )

        invalid_ids = [
            feature_id
            for feature_id in configured_ids
            if isinstance(feature_id, bool) or not isinstance(feature_id, int)
        ]

        if invalid_ids:
            raise TypeError(
                f"All IDs for [{layer_name}] must be integers. "
                f"Invalid values: {invalid_ids}"
            )

        duplicate_count = len(configured_ids) - len(set(configured_ids))

        if duplicate_count:
            warnings.warn(
                f"[{layer_name}] contains {duplicate_count} duplicate "
                "ID value(s). Duplicates will be ignored."
            )

        selection[layer_name] = {
            "ids": set(configured_ids),
            "combine": combine,
        }

    return selection


# Compatibility alias for external callers. Selection now always uses the
# persistent 'id' attribute, even when an older caller uses this function name.
load_fid_selection = load_id_selection


def persistent_id_series(
    gdf: gpd.GeoDataFrame,
    layer_name: str,
) -> pd.Series:
    """Return validated integer persistent IDs indexed like ``gdf``."""
    if "id" not in gdf.columns:
        raise KeyError(
            f"Layer '{layer_name}' does not contain the required persistent "
            "'id' column."
        )

    numeric = pd.to_numeric(gdf["id"], errors="coerce")
    invalid = numeric.isna() | numeric.mod(1).ne(0) | numeric.le(0)
    if invalid.any():
        bad_values = gdf.loc[invalid, "id"].head(10).tolist()
        raise ValueError(
            f"Layer '{layer_name}' contains invalid persistent IDs: "
            f"{bad_values}"
        )

    ids = numeric.astype("int64")
    duplicates = ids[ids.duplicated(keep=False)]
    if not duplicates.empty:
        raise ValueError(
            f"Layer '{layer_name}' contains duplicate persistent IDs before "
            f"selection: {sorted(duplicates.unique().tolist())[:10]}"
        )
    return ids


# ============================================================
# SPATIAL FUNCTIONS
# ============================================================

def create_dissolved_buffer(
    geometries: gpd.GeoSeries,
    distance_m: float,
):
    """Create one dissolved buffer around all valid geometries."""
    valid_geometries = geometries[
        geometries.notna() & ~geometries.is_empty
    ]

    if valid_geometries.empty:
        return GeometryCollection()

    buffered = valid_geometries.buffer(
        distance_m,
        cap_style="round",
    )

    try:
        return buffered.union_all()
    except AttributeError:
        # Compatibility with older GeoPandas/Shapely versions.
        return buffered.unary_union


def intersects_buffer(
    gdf: gpd.GeoDataFrame,
    buffer_geometry,
) -> pd.Series:
    """
    Return True when a feature intersects or touches a buffer.

    Intersects includes overlap, containment, crossing, and touching
    the buffer boundary.
    """
    result = pd.Series(
        False,
        index=gdf.index,
        dtype=bool,
    )

    if (
        gdf.empty
        or buffer_geometry is None
        or buffer_geometry.is_empty
    ):
        return result

    valid_geometry = (
        gdf.geometry.notna()
        & ~gdf.geometry.is_empty
    )

    if valid_geometry.any():
        result.loc[valid_geometry] = (
            gdf.loc[valid_geometry, "geometry"]
            .intersects(buffer_geometry)
        )

    return result


# ============================================================
# MAIN FILTER FUNCTION
# ============================================================

def combine_selected_features(
    gdf: gpd.GeoDataFrame,
) -> gpd.GeoDataFrame:
    """
    Combine all selected features into one feature.

    The geometries are unioned and then line-merged where possible.
    Non-geometry attributes are retained from the first selected
    feature. The original source FIDs are stored in 'combined_fids'.
    Persistent source IDs are stored in 'source_ids'; the scalar 'id'
    remains the first feature's ID for backward-compatible attributes.
    """
    if gdf.empty or len(gdf) == 1:
        return gdf.copy()

    valid_geometry = gdf.geometry[
        gdf.geometry.notna() & ~gdf.geometry.is_empty
    ]

    if valid_geometry.empty:
        return gdf.iloc[[0]].copy()

    try:
        combined_geometry = valid_geometry.union_all()
    except AttributeError:
        combined_geometry = valid_geometry.unary_union

    try:
        combined_geometry = linemerge(combined_geometry)
    except (TypeError, ValueError):
        pass

    combined = gdf.iloc[[0]].copy()
    combined.index = [gdf.index[0]]
    combined.at[gdf.index[0], "geometry"] = combined_geometry

    combined_fids = ",".join(
        str(fid) for fid in gdf.index.tolist()
    )

    combined["combined_fids"] = combined_fids

    if "id" in gdf.columns:
        if gdf["id"].isna().any():
            raise ValueError(
                "Cannot combine selected features with missing persistent IDs."
            )
        combined["source_ids"] = "|".join(
            str(value)
            for value in dict.fromkeys(gdf["id"].tolist())
        )

    return combined

def filter_influencers_from_selected_features(
    input_path: Path,
    output_path: Path,
    config_path: Path,
) -> Path:
    """
    Select influenced features using TOML FIDs and filter electrical
    influencer layers using theme-specific buffer and length rules.

    Retention rules:

    - inductive_le24kv:
        intersects the 550 m buffer AND total feature length >= 350 m

    - inductive_gt24kv:
        intersects the 850 m buffer AND total feature length >= 20 m

    - inductive_ag:
        intersects the 1,610 m buffer AND total feature length >= 10 m

    The complete geometry is retained. Length is measured over the
    complete feature, not only the portion inside the buffer.

    Layers that are neither influenced nor influencer layers are
    copied unchanged.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    config_path = Path(config_path)

    if not input_path.exists():
        raise FileNotFoundError(
            f"Input GeoPackage does not exist: {input_path}"
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if output_path.exists():
        output_path.unlink()

    layers = list_gpkg_layers(input_path)
    id_selection = load_id_selection(config_path)

    print("============================================================")
    print("Selected-feature inductive spatial filtering")
    print("============================================================")
    print(f"Input : {input_path}")
    print(f"TOML  : {config_path}")
    print(f"Output: {output_path}")
    print()

    # --------------------------------------------------------
    # Validate layer availability
    # --------------------------------------------------------

    missing_influenced_layers = [
        layer_name
        for layer_name in INFLUENCED_LAYERS
        if layer_name not in layers
    ]

    if missing_influenced_layers:
        warnings.warn(
            "The following influenced layers are not present in the "
            "input GeoPackage:\n"
            f"{missing_influenced_layers}"
        )

    missing_influencer_layers = [
        layer_name
        for layer_name in INFLUENCER_LAYERS
        if layer_name not in layers
    ]

    if missing_influencer_layers:
        warnings.warn(
            "The following influencer layers are not present in the "
            "input GeoPackage:\n"
            f"{missing_influencer_layers}"
        )

    # --------------------------------------------------------
    # Select influenced features
    # --------------------------------------------------------

    selected_layers: dict[str, gpd.GeoDataFrame] = {}
    influenced_input_counts: dict[str, int] = {}
    selected_analysis_parts: list[gpd.GeoDataFrame] = []

    print("Selecting influenced features by persistent ID:")

    for layer_name in INFLUENCED_LAYERS:
        if layer_name not in layers:
            continue

        gdf = read_layer_with_fid(
            input_path,
            layer_name,
        )

        influenced_input_counts[layer_name] = len(gdf)

        layer_selection = id_selection.get(
            layer_name,
            {
                "ids": set(),
                "combine": False,
            },
        )

        requested_ids = layer_selection["ids"]
        combine_selected = layer_selection["combine"]

        feature_ids = persistent_id_series(gdf, layer_name)
        available_ids = set(feature_ids.tolist())

        if requested_ids == "all":
            selected = gdf.copy()
            requested_description = "all"
            found_description = str(len(selected))

        else:
            found_ids = requested_ids & available_ids
            missing_ids = requested_ids - available_ids

            if missing_ids:
                warnings.warn(
                    f"Layer '{layer_name}' does not contain the following "
                    f"requested IDs: {sorted(missing_ids)}"
                )

            selected = gdf.loc[
                feature_ids.isin(found_ids)
            ].copy()

            if combine_selected and len(selected) > 1:
                selected = combine_selected_features(selected)

            requested_description = str(len(requested_ids))
            found_description = str(len(found_ids))

        selected_layers[layer_name] = selected

        if not selected.empty:
            selected_analysis = ensure_analysis_crs(
                selected,
                layer_name,
            )

            selected_analysis_parts.append(
                selected_analysis[["geometry"]].copy()
            )

        print(
            f"  {layer_name}: "
            f"requested={requested_description}, "
            f"found={found_description}, "
            f"selected={len(selected)}, "
            f"combine={combine_selected}"
        )

    # --------------------------------------------------------
    # Combine selected geometries
    # --------------------------------------------------------

    if selected_analysis_parts:
        selected_features = gpd.GeoDataFrame(
            pd.concat(
                selected_analysis_parts,
                ignore_index=True,
            ),
            geometry="geometry",
            crs=TARGET_CRS,
        )
    else:
        selected_features = gpd.GeoDataFrame(
            {"geometry": []},
            geometry="geometry",
            crs=TARGET_CRS,
        )

    selected_feature_count = len(selected_features)

    print()
    print(f"Total selected features: {selected_feature_count}")

    if selected_features.empty:
        warnings.warn(
            "No influenced features were selected. The buffers will "
            "be empty, so all features from the influencer layers "
            "will be removed."
        )

    # --------------------------------------------------------
    # Build buffers
    # --------------------------------------------------------

    print()
    print("Creating dissolved buffers around selected features...")

    buffer_550m = create_dissolved_buffer(
        selected_features.geometry,
        BUFFER_LE24KV_M,
    )

    buffer_850m = create_dissolved_buffer(
        selected_features.geometry,
        BUFFER_GT24KV_M,
    )

    buffer_1610m = create_dissolved_buffer(
        selected_features.geometry,
        BUFFER_ABOVEGROUND_M,
    )

    print(f"  {BUFFER_LE24KV_M:.0f} m buffer created")
    print(f"  {BUFFER_GT24KV_M:.0f} m buffer created")
    print(f"  {BUFFER_ABOVEGROUND_M:.0f} m buffer created")

    # --------------------------------------------------------
    # Process all input layers
    # --------------------------------------------------------

    print()
    print("Writing filtered output:")

    summary_rows: list[dict] = []

    for layer_name in layers:
        print("------------------------------------------------------------")
        print(f"Processing layer: {layer_name}")

        # ====================================================
        # BRANCH 1: influenced layers
        # Write only the TOML-selected features.
        # ====================================================

        if layer_name in INFLUENCED_LAYERS:
            selected = selected_layers[layer_name]
            input_count = influenced_input_counts[layer_name]
            kept_count = len(selected)
            removed_count = input_count - kept_count

            write_layer(
                selected,
                output_path,
                layer_name,
            )

            summary_rows.append(
                {
                    "layer": layer_name,
                    "mode": "selected_by_id",
                    "input": input_count,
                    "kept": kept_count,
                    "removed": removed_count,
                    "too_short_total": 0,
                    "rejected_by_length": 0,
                    "unknown_theme": 0,
                }
            )

            print(f"  input  : {input_count}")
            print(f"  kept   : {kept_count}")
            print(f"  removed: {removed_count}")

            continue

        # Read non-influenced layers once.
        gdf = read_layer_with_fid(
            input_path,
            layer_name,
        )

        input_count = len(gdf)

        # ====================================================
        # BRANCH 2: influencer layers
        # Apply theme-specific buffer and minimum-length rules.
        # ====================================================

        if layer_name in INFLUENCER_LAYERS:
            layer_selection = id_selection.get(
                layer_name,
                {"ids": "all", "combine": False},
            )
            requested_ids = layer_selection["ids"]
            feature_ids = persistent_id_series(gdf, layer_name)
            available_ids = set(feature_ids.tolist())
            if requested_ids == "all":
                candidates = gdf.copy()
            else:
                found_ids = requested_ids & available_ids
                missing_ids = requested_ids - available_ids
                if missing_ids:
                    warnings.warn(
                        f"Layer '{layer_name}' does not contain the following "
                        f"requested IDs: {sorted(missing_ids)}"
                    )
                candidates = gdf.loc[feature_ids.isin(found_ids)].copy()

            if THEME_COLUMN not in candidates.columns:
                raise KeyError(
                    f"Layer '{layer_name}' does not contain the required "
                    f"column '{THEME_COLUMN}'."
                )

            analysis_gdf = ensure_analysis_crs(
                candidates,
                layer_name,
            )

            theme = (
                analysis_gdf[THEME_COLUMN]
                .astype("string")
                .str.strip()
                .str.lower()
            )

            theme_is_le24kv = theme.eq(THEME_LE24KV).fillna(False)
            theme_is_gt24kv = theme.eq(THEME_GT24KV).fillna(False)
            theme_is_aboveground = (
                theme.eq(THEME_ABOVEGROUND).fillna(False)
            )

            recognised_theme = (
                theme_is_le24kv
                | theme_is_gt24kv
                | theme_is_aboveground
            )

            # EPSG:28992 uses metres. This is the length of the
            # complete feature geometry.
            feature_length_m = analysis_gdf.geometry.length

            too_short_le24kv = (
                theme_is_le24kv
                & feature_length_m.lt(MIN_LENGTH_UG_LE_24KV_M)
            ).fillna(False)

            too_short_gt24kv = (
                theme_is_gt24kv
                & feature_length_m.lt(MIN_LENGTH_UG_GT_24KV_M)
            ).fillna(False)

            too_short_aboveground = (
                theme_is_aboveground
                & feature_length_m.lt(MIN_LENGTH_AG_M)
            ).fillna(False)

            intersects_550m = intersects_buffer(
                analysis_gdf,
                buffer_550m,
            )

            intersects_850m = intersects_buffer(
                analysis_gdf,
                buffer_850m,
            )

            intersects_1610m = intersects_buffer(
                analysis_gdf,
                buffer_1610m,
            )

            # Passed the applicable buffer test but failed the
            # minimum-length test.
            rejected_by_length_le24kv = (
                theme_is_le24kv
                & intersects_550m
                & too_short_le24kv
            )

            rejected_by_length_gt24kv = (
                theme_is_gt24kv
                & intersects_850m
                & too_short_gt24kv
            )

            rejected_by_length_aboveground = (
                theme_is_aboveground
                & intersects_1610m
                & too_short_aboveground
            )

            # A feature is retained only if it passes both its
            # applicable buffer test and minimum-length test.
            keep_le24kv = (
                theme_is_le24kv
                & intersects_550m
                & ~too_short_le24kv
            )

            keep_gt24kv = (
                theme_is_gt24kv
                & intersects_850m
                & ~too_short_gt24kv
            )

            keep_aboveground = (
                theme_is_aboveground
                & intersects_1610m
                & ~too_short_aboveground
            )

            keep_feature = (
                keep_le24kv
                | keep_gt24kv
                | keep_aboveground
            ).fillna(False)

            filtered = candidates.loc[keep_feature].copy()

            kept_count = len(filtered)
            removed_count = input_count - kept_count

            too_short_total = int(
                too_short_le24kv.sum()
                + too_short_gt24kv.sum()
                + too_short_aboveground.sum()
            )

            rejected_by_length_total = int(
                rejected_by_length_le24kv.sum()
                + rejected_by_length_gt24kv.sum()
                + rejected_by_length_aboveground.sum()
            )

            unclassified_count = int(
                (~recognised_theme).sum()
            )

            write_layer(
                filtered,
                output_path,
                layer_name,
            )

            summary_rows.append(
                {
                    "layer": layer_name,
                    "mode": "filtered_by_theme_buffer_and_length",
                    "input": input_count,
                    "kept": kept_count,
                    "removed": removed_count,
                    "too_short_total": too_short_total,
                    "rejected_by_length": rejected_by_length_total,
                    "unknown_theme": unclassified_count,
                }
            )

            print(f"  input                     : {input_count}")
            print(f"  selected candidates       : {len(candidates)}")
            print(f"  kept                      : {kept_count}")
            print(f"  removed                   : {removed_count}")

            print(
                "  kept inductive_le24kv     : "
                f"{int(keep_le24kv.sum())}"
            )
            print(
                "  kept inductive_gt24kv     : "
                f"{int(keep_gt24kv.sum())}"
            )
            print(
                "  kept inductive_ag         : "
                f"{int(keep_aboveground.sum())}"
            )

            print(
                "  too short le24kv (total)  : "
                f"{int(too_short_le24kv.sum())}"
            )
            print(
                "  too short gt24kv (total)  : "
                f"{int(too_short_gt24kv.sum())}"
            )
            print(
                "  too short ag (total)      : "
                f"{int(too_short_aboveground.sum())}"
            )

            print(
                "  rejected by length le24kv : "
                f"{int(rejected_by_length_le24kv.sum())}"
            )
            print(
                "  rejected by length gt24kv : "
                f"{int(rejected_by_length_gt24kv.sum())}"
            )
            print(
                "  rejected by length ag     : "
                f"{int(rejected_by_length_aboveground.sum())}"
            )
            print(
                "  rejected by length total  : "
                f"{rejected_by_length_total}"
            )
            print(
                "  unknown/unclassified      : "
                f"{unclassified_count}"
            )

            continue

        # ====================================================
        # BRANCH 3: every other layer
        # Copy unchanged. This includes Mast.
        # ====================================================

        write_layer(
            gdf,
            output_path,
            layer_name,
        )

        summary_rows.append(
            {
                "layer": layer_name,
                "mode": "copied_unchanged",
                "input": input_count,
                "kept": input_count,
                "removed": 0,
                "too_short_total": 0,
                "rejected_by_length": 0,
                "unknown_theme": 0,
            }
        )

        print(f"  copied unchanged: {input_count}")

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print("============================================================")
    print("SUMMARY")
    print("============================================================")

    summary = pd.DataFrame(
        summary_rows,
        columns=[
            "layer",
            "mode",
            "input",
            "kept",
            "removed",
            "too_short_total",
            "rejected_by_length",
            "unknown_theme",
        ],
    )

    print(summary.to_string(index=False))

    print()
    print("Finished.")
    print(f"Output written to: {output_path}")

    return output_path


# ============================================================
# STANDALONE EXECUTION
# ============================================================

#if __name__ == "__main__":
    project_dir = Path(__file__).resolve().parents[3]

    temp_dir = (
        project_dir
        / "data"
        / "KLIC"
        / "KLIC_didam"
        / "temp"
    )

    input_path = temp_dir / "KLIC_temp9.gpkg"
    output_path = temp_dir / "KLIC_temp9_selected.gpkg"
    config_path = project_dir / "selection.toml"

    print(f"Script       : {Path(__file__).resolve()}")
    print(f"Project dir  : {project_dir}")
    print(f"Input        : {input_path}")
    print(f"Input exists : {input_path.exists()}")
    print(f"Config       : {config_path}")
    print(f"Config exists: {config_path.exists()}")
    print(f"Output       : {output_path}")
    print()

    filter_influencers_from_selected_features(
        input_path=input_path,
        output_path=output_path,
        config_path=config_path,
    )