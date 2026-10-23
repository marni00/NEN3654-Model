from __future__ import annotations
import ctypes
import os
from pathlib import Path
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
    QSplitter, QVBoxLayout, QWidget
)
from gui.controller import WorkflowController
from gui.models import AppState
from gui.qt_dispatcher import QtDispatcher
from gui.services.configuration import load_initial_config
from gui.theme import COLORS, SPACING
from gui.views.feature_panel import FeaturePanel
from gui.views.map_panel import MapPanel
from gui.views.setup_panel import SetupPanel
from gui.views.status_bar import StatusBar
from gui.views.upload_dialog import UploadDialog

def enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass

class NEN3654App(QMainWindow):
    def __init__(self, project_root: Path) -> None:
        super().__init__()
        self.setWindowTitle("NEN 3654 Analyse")
        self.state_model = AppState(project_root=project_root)
        self.state_model.proposed_hv = load_initial_config(project_root / "config" / "config.toml")
        default_hv = project_root / "data" / "Proposed_Highvoltage" / "Proposed_Highvoltage_Groenedijk.shp"
        if default_hv.is_file():
            self.state_model.proposed_hv.source_path = default_hv
        self.dispatcher = QtDispatcher(self)
        self.controller = WorkflowController(self.dispatcher, self.state_model)
        self._last_busy: bool | None = None
        self._build()
        self.controller.set_feature_refresh_callback(self._features_changed)
        self.controller.set_visual_refresh_callback(self.map_panel.refresh_visual_state)
        self.state_model.subscribe(self.refresh)
        QTimer.singleShot(0, self._size_window)
        self.refresh()

    def _build(self) -> None:
        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        header = QFrame()
        header.setObjectName("card")
        header_layout = QVBoxLayout(header)
        title = QLabel("NEN 3654 Analyse")
        title.setStyleSheet("font-size: 18pt; font-weight: 600;")
        subtitle = QLabel("KLIC-preparatie, featureselectie, analyse en publicatie in één workflow")
        subtitle.setObjectName("muted")
        subtitle.setWordWrap(True)
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        outer.addWidget(header)
        self.status_bar = StatusBar(self.state_model, self.controller.cancel, self._show_error)
        outer.addWidget(self.status_bar)

        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setup_panel = SetupPanel(self.state_model, self.controller, self._open_upload)
        self.feature_panel = FeaturePanel(self.state_model, self.controller)
        self.map_panel = MapPanel(self.state_model, self.controller)
        self.main_splitter.addWidget(self.setup_panel)
        self.main_splitter.addWidget(self.feature_panel)
        self.main_splitter.addWidget(self.map_panel)
        self.main_splitter.setStretchFactor(0, 2)
        self.main_splitter.setStretchFactor(1, 2)
        self.main_splitter.setStretchFactor(2, 6)
        outer.addWidget(self.main_splitter, 1)

        footer = QFrame()
        footer.setStyleSheet(f"background:{COLORS['gray_700']}; color:white;")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(SPACING["lg"], SPACING["sm"], SPACING["lg"], SPACING["sm"])
        footer_layout.addWidget(QLabel("Sweco · NEN 3654 desktoptool"))
        outer.addWidget(footer)
        self.setCentralWidget(central)

    def refresh(self) -> None:
        """Refresh expensive feature widgets only when their interaction state changes."""
        busy = self.state_model.busy
        busy_changed = busy != self._last_busy
        if busy_changed:
            self.map_panel.set_computation_active(busy)
        self.setup_panel.refresh()
        if not busy or busy_changed:
            self.feature_panel.refresh()
        self.status_bar.refresh()
        self._last_busy = busy

    def _features_changed(self) -> None:
        self.map_panel.refresh_data()
        if not self.state_model.busy:
            self.feature_panel.refresh()
        self.setup_panel.refresh()

    def _open_upload(self) -> None:
        portal, username = self.setup_panel.upload_credentials()
        UploadDialog(portal, username, self.controller.upload, self).exec()

    def _show_error(self) -> None:
        QMessageBox.critical(
            self, "Technische foutinformatie",
            self.state_model.error_detail or self.state_model.error_summary or "Geen details."
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.state_model.busy:
            answer = QMessageBox.question(
                self, "Proces actief",
                "Er is een backendproces actief. Sluiten kan een onvolledige werkmap achterlaten. Toch sluiten?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        elif self.state_model.dirty:
            answer = QMessageBox.question(
                self, "Niet-uitgevoerde wijzigingen",
                "Selecties of configuratie zijn gewijzigd maar nog niet verwerkt. Toch sluiten?"
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.map_panel.shutdown()
        event.accept()

    def _size_window(self) -> None:
        screen = self.screen() or QApplication.primaryScreen()
        work = screen.availableGeometry()
        natural = self.sizeHint()
        width = min(natural.width(), int(work.width() * 0.90))
        height = min(natural.height(), int(work.height() * 0.90))
        minimum = self.minimumSizeHint()
        self.setMinimumSize(
            min(minimum.width(), width),
            min(minimum.height(), height),
        )
        self.resize(max(width, self.minimumWidth()), max(height, self.minimumHeight()))
        frame = self.frameGeometry()
        frame.moveCenter(work.center())
        self.move(frame.topLeft())
        self.main_splitter.setSizes([width * 2 // 10, width * 2 // 10, width * 6 // 10])
