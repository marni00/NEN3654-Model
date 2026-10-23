from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

class UploadDialog(QDialog):
    def __init__(self, portal_url: str, username: str, on_submit, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Uploaden naar ArcGIS Online")
        self.setModal(True)
        self.on_submit = on_submit
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.portal = QLineEdit(portal_url)
        self.username = QLineEdit(username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Portal", self.portal)
        portal_hint = QLabel('Sweco: "https://ggc.maps.arcgis.com"')
        portal_hint.setObjectName("muted")
        form.addRow("", portal_hint)
        form.addRow("Gebruikersnaam", self.username)
        username_hint = QLabel('"voornaam.achternaal@sweco.nl"')
        username_hint.setObjectName("muted")
        form.addRow("", username_hint)
        form.addRow("Wachtwoord", self.password)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Uploaden")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._submit)
        layout.addWidget(buttons)
        self.password.setFocus()

    def _submit(self) -> None:
        password = self.password.text()
        self.password.clear()
        self.accept()
        self.on_submit(self.portal.text(), self.username.text(), password)
