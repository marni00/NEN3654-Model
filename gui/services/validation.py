"""
validation

Validates that Part 1 input is an existing .gpkg or .gdb and that selected GeoPackages can be listed/read.

Input:
    Fail-fast GUI validation.
"""

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
            return False, "Selected File GDB does not exist."
        if not path.is_dir():
            return False, "A File GDB must be a file that ends with .gdb"
        try:
            has_table = any(path.glob("*.gdbtable"))
        except OSError as error:
            return False, f"The file GDB cannot be read: {error}"
        if not has_table:
            return False, "The selected folder does not contain valid FileGDB tables."
        return True, "Valid Esri File Geodatabase selected."

    return False, "Select a GeoPackage (.gpkg) or Esri File Geodatabase (.gdb)."


def validate_geopackage(path: Path) -> tuple[bool, str]:
    path = Path(path)
    if path.suffix.casefold() != ".gpkg":
        return False, "Select a file with the extension .gpkg."
    if not path.is_file():
        return False, "The selected file does not exist."

    try:
        with path.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                return False, "The file is not a valid SQLite/GeoPackage database."
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
            gpkg = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='gpkg_contents'"
            ).fetchone()
            if gpkg is None:
                return False, "The file does not contain a valid GeoPackage structure."
            feature_count = connection.execute(
                "SELECT COUNT(*) FROM gpkg_contents WHERE data_type='features'"
            ).fetchone()[0]
            if feature_count < 1:
                return False, "The GeoPackage does not contain any feature layers."
    except (OSError, sqlite3.Error) as error:
        return False, f"The GeoPackage cannot be read: {error}"

    return True, f"Valid GeoPackage with {feature_count} feature layer(s)."
