from __future__ import annotations

import sqlite3
from pathlib import Path


def validate_part1_input(path: Path) -> tuple[bool, str]:
    """Validate a Part 1 datasource without changing the model input contract."""
    path = Path(path)

    if path.suffix.casefold() == ".gpkg":
        return validate_geopackage(path)

    if path.suffix.casefold() == ".gdb":
        if not path.exists():
            return False, "De geselecteerde File Geodatabase bestaat niet."
        if not path.is_dir():
            return False, "Een File Geodatabase moet een map zijn die eindigt op .gdb."
        try:
            has_table = any(path.glob("*.gdbtable"))
        except OSError as error:
            return False, f"De File Geodatabase kan niet worden gelezen: {error}"
        if not has_table:
            return False, "De geselecteerde map bevat geen geldige FileGDB-tabellen."
        return True, "Geldige Esri File Geodatabase geselecteerd."

    return False, "Selecteer een GeoPackage (.gpkg) of Esri File Geodatabase (.gdb)."


def validate_geopackage(path: Path) -> tuple[bool, str]:
    path = Path(path)
    if path.suffix.casefold() != ".gpkg":
        return False, "Selecteer een bestand met de extensie .gpkg."
    if not path.is_file():
        return False, "Het geselecteerde bestand bestaat niet."

    try:
        with path.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                return False, "Het bestand is geen geldige SQLite/GeoPackage-database."
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
            gpkg = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gpkg_contents'"
            ).fetchone()
            if gpkg is None:
                return False, "Het bestand bevat geen GeoPackage-structuur."
            feature_count = connection.execute(
                "SELECT COUNT(*) FROM gpkg_contents WHERE data_type='features'"
            ).fetchone()[0]
            if feature_count < 1:
                return False, "Het GeoPackage bevat geen featurelagen."
    except (OSError, sqlite3.Error) as error:
        return False, f"Het GeoPackage kan niet worden gelezen: {error}"

    return True, f"Geldig GeoPackage met {feature_count} featurelaag/-lagen."
