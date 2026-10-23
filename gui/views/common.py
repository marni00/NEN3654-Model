from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget
)
from gui.theme import SPACING

def card(parent=None) -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName("card")
    return frame

def section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionTitle")
    return label

def muted_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    return label

class PathChooser(QWidget):
    def __init__(self, button_text: str, callback, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["sm"])
        self.entry = QLineEdit()
        self.entry.setReadOnly(True)
        self.entry.setAccessibleName("Geselecteerd pad")
        self.button = QPushButton(button_text)
        self.button.setProperty("secondary", True)
        self.button.clicked.connect(callback)
        layout.addWidget(self.entry, 1)
        layout.addWidget(self.button)

    def set_path(self, path: Path | None) -> None:
        text = str(path or "")
        self.entry.setText(text)
        self.entry.setToolTip(text)

def vbox(widget: QWidget, margins: int = 16) -> QVBoxLayout:
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(SPACING["sm"])
    return layout
