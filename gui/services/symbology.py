from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gui.models import MapFeature


def _rgb_to_hex(value: Any, fallback: str) -> str:
    if not isinstance(value, list) or len(value) < 3:
        return fallback
    try:
        red, green, blue = (
            max(0, min(255, int(component))) for component in value[:3]
        )
    except (TypeError, ValueError):
        return fallback
    return f"#{red:02X}{green:02X}{blue:02X}"


def _normalise(value: Any) -> str:
    return str(value or "").strip().casefold()


@dataclass(frozen=True)
class LegendEntry:
    label: str
    color: str
    dashed: bool = False


class Symbology:
    """Read-only map styling derived from config/symbology.json."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.default_color = "#505050"
        self.layer_colors: dict[str, str] = {}
        self.infrastructure_field = "infrastructure_type"
        self.voltage_field = "thema"
        self.voltage_classes: dict[str, dict[str, Any]] = {}
        self.standard_line_width = 1.0
        self.standard_point_size = 7.0
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return

        self.default_color = _rgb_to_hex(data.get("default_color"), self.default_color)
        self.layer_colors = {
            str(layer): _rgb_to_hex(rgb, self.default_color)
            for layer, rgb in data.get("layer_colors", {}).items()
        }
        infrastructure = data.get("infrastructure_type", {})
        self.infrastructure_field = str(
            infrastructure.get("field", "infrastructure_type")
        )
        voltage = data.get("voltage_class", {})
        self.voltage_field = str(voltage.get("field", "thema"))
        classes = voltage.get("classes", {})
        if isinstance(classes, dict):
            self.voltage_classes = {
                str(name): value
                for name, value in classes.items()
                if isinstance(value, dict)
            }
        try:
            self.standard_line_width = float(
                data.get("standard_line", {}).get("width", 1.0)
            )
        except (TypeError, ValueError):
            self.standard_line_width = 1.0
        try:
            self.standard_point_size = float(
                data.get("standard_point", {}).get("size", 7.0)
            )
        except (TypeError, ValueError):
            self.standard_point_size = 7.0

    @staticmethod
    def _attribute(feature: MapFeature, *names: str) -> Any:
        wanted = {_normalise(name) for name in names}
        for key, value in feature.attributes.items():
            if _normalise(key) in wanted and value not in (None, ""):
                return value
        return None

    def _canonical_layer(self, value: Any) -> str:
        normalised = _normalise(value)
        for layer in self.layer_colors:
            if _normalise(layer) == normalised:
                return layer
        return str(value or "")

    def infrastructure_type(self, feature: MapFeature) -> str:
        value = self._attribute(
            feature,
            self.infrastructure_field,
            "infrastructure_type",
            "infrastructuretype",
            "originalLayer",
            "original_layer",
        )
        return self._canonical_layer(value if value not in (None, "") else feature.key.layer)

    def voltage_class(self, feature: MapFeature) -> dict[str, Any] | None:
        infrastructure_type = self.infrastructure_type(feature)
        if infrastructure_type not in {"Elektriciteitskabel", "proposed_highvoltage"}:
            return None
        class_name = self._attribute(
            feature,
            self.voltage_field,
            "thema",
            "voltage_class",
            "voltageclass",
        )
        normalised = _normalise(class_name)
        for name, style in self.voltage_classes.items():
            if _normalise(name) == normalised:
                return style
        return None

    def feature_color(self, feature: MapFeature) -> str:
        infrastructure_type = self.infrastructure_type(feature)
        class_style = self.voltage_class(feature)
        if class_style:
            return _rgb_to_hex(
                class_style.get("color"),
                self.layer_colors.get(infrastructure_type, self.default_color),
            )
        return self.layer_colors.get(infrastructure_type, self.default_color)

    def line_width(self, feature: MapFeature) -> int:
        width = self.standard_line_width
        class_style = self.voltage_class(feature)
        if class_style:
            try:
                width = float(class_style.get("width", width))
            except (TypeError, ValueError):
                pass
        return max(1, round(width))

    def line_dash(self, feature: MapFeature) -> bool:
        class_style = self.voltage_class(feature)
        return bool(
            class_style
            and _normalise(class_style.get("style")) == "esrislsdash"
        )

    def point_size(self) -> int:
        return max(5, round(self.standard_point_size))

    def legend_entries(self) -> list[LegendEntry]:
        entries = [
            LegendEntry(layer, color)
            for layer, color in self.layer_colors.items()
            if layer not in {"Elektriciteitskabel", "proposed_highvoltage"}
        ]
        for name, style in self.voltage_classes.items():
            entries.append(
                LegendEntry(
                    str(style.get("label", name)),
                    _rgb_to_hex(
                        style.get("color"),
                        self.layer_colors.get("Elektriciteitskabel", self.default_color),
                    ),
                    dashed=_normalise(style.get("style")) == "esrislsdash",
                )
            )
        entries.append(
            LegendEntry(
                "Voorgestelde hoogspanning",
                self.layer_colors.get("proposed_highvoltage", self.default_color),
            )
        )
        return entries
