from __future__ import annotations

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

COLORS = {
    "gray_50": "#F9FAFB", "gray_100": "#F2F2F2", "gray_300": "#E1E1E1",
    "gray_500": "#B2B2B2", "gray_700": "#575656", "gray_900": "#111111",
    "green_100": "#EEF9E9", "green_300": "#BDE3AF", "green_500": "#87BE73",
    "green_700": "#538840", "green_900": "#3F6730", "blue_100": "#F3F8FC",
    "blue_500": "#98BDDC", "blue_700": "#3A7DBF", "peach_100": "#FCF3F0",
    "warning": "#A70707", "sand_100": "#F7F6ED", "sand_500": "#C6B37C",
    "sand_700": "#989077", "white": "#FFFFFF", "black": "#000000",
}
SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}
FONT_FAMILY = "Arial"

STYLESHEET = """
QWidget { color: #111111; font-family: Arial; font-size: 10.5pt; }
QMainWindow, QDialog { background: #F9FAFB; }
QFrame#card { background: #FFFFFF; border: 1px solid #E1E1E1; border-radius: 2px; }
QLabel#sectionTitle { font-size: 13.5pt; font-weight: 600; }
QLabel#muted { color: #575656; }
QLabel#error { color: #A70707; }
QTabWidget::pane { border: 1px solid #E1E1E1; background: #FFFFFF; }
QTabBar::tab { background: #F2F2F2; padding: 9px 14px; }
QTabBar::tab:selected { background: #87BE73; }
QPushButton { background: #87BE73; border: 0; border-radius: 16px; padding: 7px 14px; }
QPushButton:hover { background: #BDE3AF; }
QPushButton:disabled { background: #E1E1E1; color: #575656; }
QPushButton[secondary="true"] { background: #FFFFFF; border: 2px solid #87BE73; }
QPushButton[danger="true"] { background: #FCF3F0; border: 2px solid #A70707; color: #A70707; }
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTreeWidget {
    background: #FFFFFF; border: 1px solid #B2B2B2; padding: 5px;
}
QLineEdit:read-only { background: #F9FAFB; color: #575656; }
QHeaderView::section { background: #F2F2F2; border: 0; border-bottom: 1px solid #E1E1E1; padding: 6px; font-weight: 600; }
QTreeWidget::item { min-height: 25px; }
QTreeWidget::item:selected { background: #BDE3AF; color: #111111; }
QProgressBar { border: 0; background: #E1E1E1; min-height: 7px; max-height: 7px; }
QProgressBar::chunk { background: #87BE73; }
QSplitter::handle { background: #E1E1E1; }
QToolBar { background: #F2F2F2; border: 0; spacing: 6px; padding: 4px; }
"""

def apply_theme(app: QApplication) -> None:
    app.setFont(QFont(FONT_FAMILY, 10))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["gray_50"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["white"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLORS["green_300"]))
    app.setPalette(palette)
