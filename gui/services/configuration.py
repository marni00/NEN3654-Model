from __future__ import annotations

import csv
import os
import tempfile
import tomllib
from pathlib import Path

from gui.models import FeatureKey, ProposedHighVoltageConfig

CONDUIT_LAYERS = (
    "Waterleiding",
    "ThermischePijpleiding",
    "Rioolleiding",
    "OlieGasChemicalienPijpleiding",
    "Overig",
)
ELECTRICITY_LAYER = "Elektriciteitskabel"
PROPOSED_HV_LAYER = "proposed_highvoltage"
INFLUENCER_LAYERS = (ELECTRICITY_LAYER, PROPOSED_HV_LAYER)
SELECTABLE_LAYERS = CONDUIT_LAYERS + INFLUENCER_LAYERS

# Kept as a compatibility alias for integrations importing the old name.
# These layers are no longer automatically included by the GUI.
AUTO_INCLUDED_LAYERS = INFLUENCER_LAYERS



def load_step2_codes(path: Path) -> tuple[str, ...]:
    """Load the allowed NEN Step 2 Codes in table order."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"NEN stap 2 Codetabel niet gevonden: {path}")

    codes: list[str] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        if reader.fieldnames is None:
            raise ValueError(f"NEN stap 2 Codetabel heeft geen kolomkoppen: {path}")
        code_column = next(
            (
                name
                for name in reader.fieldnames
                if str(name).strip().casefold() == "code"
            ),
            None,
        )
        if code_column is None:
            raise ValueError(f"NEN stap 2 Codetabel bevat geen kolom 'Code': {path}")

        for row in reader:
            code = str(row.get(code_column, "") or "").strip().upper()
            if not code or code.casefold() == "nan" or code in seen:
                continue
            seen.add(code)
            codes.append(code)

    if not codes:
        raise ValueError(f"NEN stap 2 Codetabel bevat geen bruikbare Codes: {path}")
    return tuple(codes)

def is_automatic_part2_layer(layer: str) -> bool:
    return False


def load_initial_config(path: Path) -> ProposedHighVoltageConfig:
    if not path.is_file():
        return ProposedHighVoltageConfig()

    with path.open("rb") as stream:
        raw = tomllib.load(stream)

    proposed = raw.get("proposed_highvoltage", {})
    return ProposedHighVoltageConfig(
        enabled=bool(proposed.get("enabled", True)),
        code=str(proposed.get("code", "")),
        vertical_position=str(proposed.get("vertical_position", "underground")),
        work_voltage=int(proposed.get("work_voltage", 110000)),
    )


def validate_configuration(config: ProposedHighVoltageConfig) -> list[str]:
    errors: list[str] = []
    if config.work_voltage <= 0:
        errors.append("Werkspanning moet groter zijn dan 0.")
    if config.enabled:
        if not config.code.strip():
            errors.append("Vul een Code in voor de voorgestelde hoogspanning.")
        if config.source_path is None or not config.source_path.is_file():
            errors.append("Selecteer een geldig bronbestand voor voorgestelde hoogspanning.")
    return errors


def write_runtime_config(
    path: Path,
    proposed: ProposedHighVoltageConfig,
    cable_groups: dict[str, set[FeatureKey]],
) -> None:
    lines = [
        "[proposed_highvoltage]",
        f"enabled = {_toml_bool(proposed.enabled)}",
        f"code = {_toml_string(proposed.code.strip())}",
        f"vertical_position = {_toml_string(proposed.vertical_position)}",
        f"work_voltage = {int(proposed.work_voltage)}",
        "",
    ]

    for code in sorted(cable_groups, key=str.casefold):
        ids = _persistent_ids(
            cable_groups[code],
            layer=ELECTRICITY_LAYER,
        )
        if not ids:
            continue
        lines.extend(
            [
                "[[Elektriciteitskabel]]",
                f"id = [{', '.join(str(feature_id) for feature_id in ids)}]",
                f"code = {_toml_string(code)}",
                "",
            ]
        )

    _atomic_write(path, "\n".join(lines))


def write_runtime_selection(
    path: Path,
    selected: set[FeatureKey],
    combine_layers: set[str] | None = None,
) -> None:
    by_layer = {
        layer: _persistent_ids(selected, layer=layer)
        for layer in SELECTABLE_LAYERS
    }
    combine_layers = combine_layers or set()
    lines: list[str] = []
    for layer in SELECTABLE_LAYERS:
        lines.extend(
            [
                f"[{layer}]",
                f"ids = [{', '.join(str(feature_id) for feature_id in by_layer[layer])}]",
            ]
        )
        if layer in CONDUIT_LAYERS:
            lines.append(f"combine = {_toml_bool(layer in combine_layers)}")
        lines.append("")
    _atomic_write(path, "\n".join(lines))


def _persistent_ids(
    keys: set[FeatureKey],
    *,
    layer: str,
) -> list[int]:
    matching = [
        key
        for key in keys
        if key.dataset == "part1" and key.layer == layer
    ]
    missing = [key.fid for key in matching if key.persistent_id is None]
    if missing:
        raise ValueError(
            f"Layer '{layer}' contains selected GUI records without a "
            f"persistent id: native FIDs {sorted(missing)}"
        )
    return sorted({int(key.persistent_id) for key in matching})


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise
