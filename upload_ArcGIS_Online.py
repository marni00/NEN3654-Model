from __future__ import annotations

from pathlib import Path
from typing import Callable

from model.post.to_arcgis_online import to_arcgis_online


KLIC_PROJECT_NAME = "KLIC_groenedijk"
PORTAL_URL = "https://ggc.maps.arcgis.com"
USERNAME = "voor.achternaam@sweco.nl"


# GUI INTEGRATION CHANGE: parameters allow secure GUI credential entry and
# runtime output folders; defaults preserve standalone use.
def upload_klic_project_to_arcgis_online(
    *,
    output_dir: Path | None = None,
    portal_url: str = PORTAL_URL,
    username: str = USERNAME,
    password: str | None = None,
    symbology_path: Path | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    project_dir = Path(__file__).resolve().parent
    if output_dir is None:
        output_dir = project_dir / "data" / "KLIC" / KLIC_PROJECT_NAME / "output"

    output_dir = Path(output_dir)
    if not output_dir.is_dir():
        raise FileNotFoundError(f"Output folder not found: {output_dir}")

    return to_arcgis_online(
        gpkg_folder=output_dir,
        portal_url=portal_url,
        username=username,
        password=password,
        symbology_path=symbology_path or project_dir / "config" / "symbology.json",
        progress=progress,
    )


if __name__ == "__main__":
    upload_klic_project_to_arcgis_online()
