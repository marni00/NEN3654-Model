from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Iterable

from shapely.geometry import mapping

from gui.models import FeatureKey, LoadedMapData, MapFeature
from gui.services.symbology import Symbology

PART2_OUTPUTS = {
    "part2_filtered": "KLIC_filtered.gpkg",
    "part2_step1": "KLIC_results_step1.gpkg",
    "part2_no_pass_step1": "KLIC_no_pass_step1.gpkg",
    "part2_step2": "KLIC_results_step2.gpkg",
    "part2_no_pass_step2": "KLIC_no_pass_step2.gpkg",
}

ProgressCallback = Callable[[int, int, str], None]


class GISRepository:
    """Read GeoPackages once and prepare both model metadata and MapLibre GeoJSON."""

    def list_layers(self, path: Path) -> list[str]:
        import pyogrio

        return [str(row[0]) for row in pyogrio.list_layers(path)]

    def discover_part2_outputs(self, output_dir: Path) -> dict[str, Path]:
        return {
            dataset: output_dir / filename
            for dataset, filename in PART2_OUTPUTS.items()
            if (output_dir / filename).is_file()
        }

    def discover_dataset_layers(
        self,
        sources: dict[str, Path],
    ) -> dict[str, tuple[str, ...]]:
        return {
            dataset: tuple(self.list_layers(path))
            for dataset, path in sources.items()
        }

    def load_dataset_bundle(
        self,
        path: Path,
        dataset: str,
        *,
        layers: Iterable[str] | None = None,
        progress: ProgressCallback | None = None,
        symbology: Symbology | None = None,
    ) -> LoadedMapData:
        import pyogrio

        available = self.list_layers(path)
        requested = set(layers) if layers is not None else None
        selected_layers = [
            layer for layer in available
            if requested is None or layer in requested
        ]
        features: list[MapFeature] = []
        geojson_by_layer: dict[tuple[str, str], str] = {}
        bounds_by_layer: dict[tuple[str, str], tuple[float, float, float, float]] = {}
        total = max(1, len(selected_layers))

        for layer_index, layer in enumerate(selected_layers, start=1):
            if progress:
                progress(layer_index - 1, total, f"Kaartlaag voorbereiden: {layer}")
            gdf = pyogrio.read_dataframe(
                path,
                layer=layer,
                fid_as_index=True,
                use_arrow=True,
            )
            layer_key = (dataset, layer)
            if gdf.empty:
                geojson_by_layer[layer_key] = _empty_feature_collection()
                continue

            metric = _as_metric(gdf)
            display = metric
            if display.crs is None:
                display = display.set_crs("EPSG:28992", allow_override=True)
            if display.crs.to_string().upper() != "EPSG:4326":
                display = display.to_crs("EPSG:4326")

            geometry_column = display.geometry.name
            attribute_columns = [
                column for column in display.columns if column != geometry_column
            ]
            attribute_records = display[attribute_columns].to_dict("records")
            native_fids = list(display.index)
            display_geometries = list(display.geometry.array)
            metric_geometries = list(metric.geometry.array)
            geojson_features: list[dict[str, Any]] = []
            seen_persistent_ids: set[int] = set()

            for position, geometry in enumerate(display_geometries):
                if geometry is None or geometry.is_empty:
                    continue
                try:
                    native_fid = int(native_fids[position])
                except (TypeError, ValueError):
                    native_fid = position
                raw_attributes = attribute_records[position]
                persistent_id = _read_persistent_id(
                    raw_attributes.get("id"),
                    dataset=dataset,
                    layer=layer,
                    native_fid=native_fid,
                    required=dataset == "part1",
                )
                if persistent_id is not None:
                    if dataset == "part1" and persistent_id in seen_persistent_ids:
                        raise ValueError(
                            f"Layer '{layer}' in dataset '{dataset}' contains "
                            f"duplicate persistent id {persistent_id}."
                        )
                    seen_persistent_ids.add(persistent_id)
                attributes = {
                    str(name): _display_value(value)
                    for name, value in raw_attributes.items()
                }
                attributes.setdefault("source_fid", native_fid)
                metric_geometry = metric_geometries[position]
                length_m = None
                if geometry.geom_type in {
                    "LineString", "MultiLineString", "Polygon", "MultiPolygon"
                }:
                    try:
                        length_m = float(metric_geometry.length)
                    except Exception:
                        pass
                feature = MapFeature(
                    key=FeatureKey(dataset, layer, native_fid, persistent_id),
                    geometry=geometry,
                    attributes=attributes,
                    geometry_type=geometry.geom_type,
                    length_m=length_m,
                )
                features.append(feature)

                # The browser only needs identity and rendering metadata.
                # Full attributes remain in MapFeature for the details panel.
                # Excluding them from GeoJSON substantially reduces Part 2
                # transfer, JSON parsing, and MapLibre worker overhead.
                properties = {
                    "__dataset": dataset,
                    "__layer": layer,
                    "__fid": native_fid,
                    "__color": symbology.feature_color(feature) if symbology else "#505050",
                    "__width": symbology.line_width(feature) if symbology else 2,
                    "__dashed": symbology.line_dash(feature) if symbology else False,
                    "__point_size": symbology.point_size() if symbology else 7,
                }
                geojson_features.append(
                    {
                        "type": "Feature",
                        "id": f"{dataset}|{layer}|{native_fid}",
                        "geometry": mapping(geometry),
                        "properties": properties,
                    }
                )

            min_x, min_y, max_x, max_y = display.total_bounds
            if all(math.isfinite(value) for value in (min_x, min_y, max_x, max_y)):
                bounds_by_layer[layer_key] = (
                    float(min_x), float(min_y), float(max_x), float(max_y)
                )
            geojson_by_layer[layer_key] = json.dumps(
                {"type": "FeatureCollection", "features": geojson_features},
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )

        if progress:
            progress(total, total, f"Kaartdata gereed: {dataset}")
        return LoadedMapData(
            features=tuple(features),
            geojson_by_layer=geojson_by_layer,
            bounds_by_layer=bounds_by_layer,
        )

    def load_dataset(
        self,
        path: Path,
        dataset: str,
        *,
        layers: Iterable[str] | None = None,
        progress: ProgressCallback | None = None,
    ) -> list[MapFeature]:
        """Compatibility API used by tests and non-GUI integrations."""
        return list(
            self.load_dataset_bundle(
                path,
                dataset,
                layers=layers,
                progress=progress,
            ).features
        )


def _as_metric(gdf):
    if gdf.crs is None:
        return gdf.set_crs("EPSG:28992", allow_override=True)
    if not gdf.crs.is_projected:
        return gdf.to_crs("EPSG:28992")
    return gdf


def _empty_feature_collection() -> str:
    return '{"type":"FeatureCollection","features":[]}'


def _read_persistent_id(
    value: Any,
    *,
    dataset: str,
    layer: str,
    native_fid: int,
    required: bool,
) -> int | None:
    if value is None:
        if required:
            raise ValueError(
                f"Layer '{layer}' in dataset '{dataset}' has no persistent "
                f"'id' for native feature {native_fid}."
            )
        return None

    try:
        if bool(math.isnan(value)):
            if required:
                raise ValueError(
                    f"Layer '{layer}' in dataset '{dataset}' has a null "
                    f"persistent 'id' for native feature {native_fid}."
                )
            return None
    except (TypeError, ValueError):
        pass

    if isinstance(value, bool):
        raise ValueError(
            f"Layer '{layer}' in dataset '{dataset}' has invalid boolean "
            f"persistent 'id' for native feature {native_fid}."
        )

    try:
        numeric = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Layer '{layer}' in dataset '{dataset}' has non-integer "
            f"persistent 'id' {value!r} for native feature {native_fid}."
        ) from error

    try:
        if float(value) != numeric:
            raise ValueError
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"Layer '{layer}' in dataset '{dataset}' has non-integer "
            f"persistent 'id' {value!r} for native feature {native_fid}."
        ) from error

    if numeric <= 0:
        raise ValueError(
            f"Layer '{layer}' in dataset '{dataset}' has non-positive "
            f"persistent 'id' {numeric} for native feature {native_fid}."
        )
    return numeric


def _display_value(value: Any) -> Any:
    if value is None:
        return ""
    try:
        if bool(math.isnan(value)):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value)
    return text if len(text) <= 1000 else f"{text[:997]}..."
