from __future__ import annotations
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication
from gui.app import NEN3654App, enable_dpi_awareness
from gui.theme import apply_theme

def main() -> int:
    enable_dpi_awareness()
    app = QApplication(sys.argv)
    app.setApplicationName("NEN 3654 Analyse")
    app.setOrganizationName("Sweco")
    apply_theme(app)
    window = NEN3654App(Path(__file__).resolve().parent)
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
