from __future__ import annotations
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout
from gui.models import AppState, TaskState
from gui.theme import SPACING

class StatusBar(QFrame):
    def __init__(self, state: AppState, on_cancel, on_error, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self.setObjectName("card")
        outer = QHBoxLayout(self)
        outer.setContentsMargins(SPACING["lg"], SPACING["sm"], SPACING["lg"], SPACING["sm"])
        text_box = QVBoxLayout()
        self.label = QLabel()
        self.label.setWordWrap(True)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        text_box.addWidget(self.label)
        text_box.addWidget(self.progress)
        outer.addLayout(text_box, 1)
        self.error_button = QPushButton("Technische details")
        self.error_button.setProperty("danger", True)
        self.error_button.clicked.connect(on_error)
        self.cancel_button = QPushButton("Annuleren")
        self.cancel_button.setProperty("danger", True)
        self.cancel_button.clicked.connect(on_cancel)
        outer.addWidget(self.error_button)
        outer.addWidget(self.cancel_button)

    def refresh(self) -> None:
        self.label.setText(self.state.status)
        running = self.state.task_state in {TaskState.RUNNING, TaskState.CANCELLING}
        self.progress.setVisible(running or self.state.task_state == TaskState.COMPLETED)
        if running and self.state.progress is None:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(100 if self.state.task_state == TaskState.COMPLETED else int((self.state.progress or 0) * 100))
        self.cancel_button.setEnabled(self.state.task_state == TaskState.RUNNING)
        self.error_button.setVisible(bool(self.state.error_detail))
