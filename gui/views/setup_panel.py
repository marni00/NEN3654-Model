from __future__ import annotations
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox,
    QHeaderView, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QVBoxLayout, QWidget
)
from gui.models import AppState, FeatureKey, WorkflowStage
from gui.services.configuration import (
    CONDUIT_LAYERS, ELECTRICITY_LAYER, INFLUENCER_LAYERS, load_step2_codes,
)
from gui.views.common import PathChooser, muted_label, section_title
from gui.theme import SPACING

class SetupPanel(QTabWidget):
    FILTERING_TAB = "Part 1 · Filtering"
    PROCESSING_TAB = "Part 2 · NEN stap 1"
    STEP2_TAB = "Part 2 · NEN stap 2"
    UPLOAD_TAB = "ArcGIS Online"

    def __init__(self, state: AppState, controller, on_upload, parent=None) -> None:
        super().__init__(parent)
        self.state, self.controller, self.on_upload = state, controller, on_upload
        self.filtering_tab = self._scroll_tab()
        self.processing_tab = self._scroll_tab()
        self.step2_tab = self._scroll_tab()
        self.upload_tab = self._scroll_tab()
        self.addTab(self.filtering_tab, self.FILTERING_TAB)
        self.addTab(self.processing_tab, self.PROCESSING_TAB)
        self.addTab(self.step2_tab, self.STEP2_TAB)
        self.addTab(self.upload_tab, self.UPLOAD_TAB)
        self._build_filtering()
        self._build_processing()
        self._build_step2()
        self._build_upload()

    def _scroll_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setLayout(QVBoxLayout())
        content.layout().setContentsMargins(SPACING["lg"], SPACING["lg"], SPACING["lg"], SPACING["lg"])
        content.layout().setSpacing(SPACING["sm"])
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _content(scroll) -> QVBoxLayout:
        return scroll.widget().layout()

    def _build_filtering(self) -> None:
        layout = self._content(self.filtering_tab)
        layout.addWidget(section_title("Ruwe KLIC-dataset"))
        self.input_path = PathChooser("GeoPackage…", self._browse_input)
        layout.addWidget(self.input_path)
        input_actions = QHBoxLayout()
        input_actions.addStretch()
        self.input_gdb_button = QPushButton("FileGDB…")
        self.input_gdb_button.setProperty("secondary", True)
        self.input_gdb_button.clicked.connect(self._browse_input_gdb)
        input_actions.addWidget(self.input_gdb_button)
        layout.addLayout(input_actions)
        self.input_help = muted_label(
            "Selecteer de ruwe KLIC-dataset als GeoPackage (.gpkg) "
            "of Esri File Geodatabase (.gdb)."
        )
        layout.addWidget(self.input_help)

        group = QGroupBox("Voorgestelde hoogspanning")
        form = QFormLayout(group)
        self.proposed_enabled = QCheckBox("Voorgestelde hoogspanning toevoegen")
        self.proposed_enabled.setChecked(self.state.proposed_hv.enabled)
        self.proposed_enabled.toggled.connect(self._sync_filtering)
        form.addRow(self.proposed_enabled)
        self.proposed_path = PathChooser("Bronbestand…", self._browse_proposed)
        form.addRow("Geometrie", self.proposed_path)
        self.code = QLineEdit(self.state.proposed_hv.code)
        self.code.editingFinished.connect(self._sync_filtering)
        form.addRow("Code", self.code)
        self.position = QComboBox()
        self.position.addItems(["underground", "aboveground"])
        self.position.setCurrentText(self.state.proposed_hv.vertical_position)
        self.position.currentTextChanged.connect(self._sync_filtering)
        form.addRow("Verticale positie", self.position)
        self.voltage = QSpinBox()
        self.voltage.setRange(1, 2_000_000)
        self.voltage.setValue(self.state.proposed_hv.work_voltage)
        self.voltage.valueChanged.connect(self._sync_filtering)
        form.addRow("Werkspanning (V)", self.voltage)
        layout.addWidget(group)

        self.filtering_button = QPushButton("Start NEN3654 Filtering")
        self.filtering_button.clicked.connect(self.start_filtering)
        layout.addWidget(self.filtering_button)
        layout.addSpacing(SPACING["md"])
        layout.addWidget(section_title("Optioneel: KLIC FileGDB's combineren"))
        layout.addWidget(muted_label(
            "Los hulpmiddel buiten de NEN 3654-workflow. Gelijknamige lagen worden "
            "samengevoegd; alleen features met gelijke geometrie én attributen gelden als duplicaat."
        ))
        self.gdb_button = QPushButton("FileGDB's combineren…")
        self.gdb_button.setProperty("secondary", True)
        self.gdb_button.clicked.connect(self._combine_gdbs)
        layout.addWidget(self.gdb_button)
        layout.addStretch()

    def _build_processing(self) -> None:
        layout = self._content(self.processing_tab)
        layout.addWidget(section_title("Invoer Part 2 · uitvoer Part 1"))
        self.part2_path = PathChooser("Part 1-resultaat…", self._browse_part2)
        layout.addWidget(self.part2_path)
        self.part2_help = muted_label(
            "Na Filtering wordt de aangemaakte KLIC_temp9.gpkg automatisch als "
            "basisinvoer gebruikt. Alleen voor een eerdere run hoeft u handmatig "
            "KLIC_temp9.gpkg te selecteren; KLIC_temp5.gpkg moet dan in dezelfde map staan."
        )
        layout.addWidget(self.part2_help)
        layout.addWidget(section_title("Selectieregels voor NEN stap 1"))
        layout.addWidget(muted_label(
            "Selecteer minimaal één buisleiding. Elektriciteitskabel en "
            "proposed_highvoltage zijn expliciet selecteerbaar. Per buisleidinglaag "
            "kan Samenvoegen worden geactiveerd. Kabelcodes zijn voor stap 1 nog niet nodig."
        ))
        self.processing_summary = muted_label("Nog geen Part 1-resultaat geladen.")
        layout.addWidget(self.processing_summary)
        self.processing_button = QPushButton("Start NEN 3654 stap 1")
        self.processing_button.clicked.connect(self.start_processing)
        layout.addWidget(self.processing_button)
        layout.addStretch()


    def _build_step2(self) -> None:
        layout = self._content(self.step2_tab)
        layout.addWidget(section_title("Invoer Part 2 · uitvoer Part 2"))
        self.step2_input_path = PathChooser("NEN stap 1-resultaat…", self._browse_step2_input)
        layout.addWidget(self.step2_input_path)
        self.step2_input_help = muted_label(
            "Na NEN stap 1 wordt KLIC_temp19.gpkg automatisch gebruikt. "
            "Om een eerdere run te vervolgen selecteert u handmatig KLIC_temp19.gpkg; "
            "KLIC_temp9.gpkg moet in dezelfde map staan."
        )
        layout.addWidget(self.step2_input_help)

        layout.addWidget(section_title("Resterende elektriciteitskabels"))
        layout.addWidget(muted_label(
            "Na stap 1 worden hier uitsluitend de elektriciteitskabels getoond "
            "die nog voor stap 2 relevant zijn. Code wordt gekoppeld aan dezelfde "
            "persistente ID als in de bestaande workflow."
        ))
        self.step2_tree = QTreeWidget()
        self.step2_tree.setColumnCount(2)
        self.step2_tree.setHeaderLabels(["Elektriciteitskabel-ID", "Code"])
        self.step2_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.step2_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.step2_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.step2_tree, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("Code"))
        self.step2_code = QComboBox()
        self.step2_code.addItem("Selecteer Code…", "")
        try:
            for code in load_step2_codes(
                self.state.project_root / "utils" / "Step2_Table.csv"
            ):
                self.step2_code.addItem(code, code)
        except (OSError, ValueError) as error:
            self.step2_code.setToolTip(str(error))
        row.addWidget(self.step2_code, 1)
        self.assign_step2_code_button = QPushButton("Code toewijzen")
        self.assign_step2_code_button.clicked.connect(self._assign_step2_code)
        row.addWidget(self.assign_step2_code_button)
        layout.addLayout(row)

        self.remove_step2_code_button = QPushButton("Uit Codegroep verwijderen")
        self.remove_step2_code_button.setProperty("secondary", True)
        self.remove_step2_code_button.clicked.connect(self._remove_step2_code)
        layout.addWidget(self.remove_step2_code_button)

        self.step2_summary = muted_label("Voer eerst NEN 3654 stap 1 uit.")
        layout.addWidget(self.step2_summary)
        self.step2_button = QPushButton("Start NEN 3654 stap 2")
        self.step2_button.clicked.connect(self.start_step2)
        layout.addWidget(self.step2_button)
        layout.addStretch()

    def _build_upload(self) -> None:
        layout = self._content(self.upload_tab)
        layout.addWidget(section_title("ArcGIS-resultaten"))
        self.upload_path = PathChooser("Outputmap…", self._browse_upload)
        layout.addWidget(self.upload_path)
        self.upload_help = muted_label("Selecteer een outputmap of gebruik de resultaten van de huidige run.")
        layout.addWidget(self.upload_help)
        form = QFormLayout()
        self.portal = QLineEdit(self.state.arcgis.portal_url)
        self.username = QLineEdit(self.state.arcgis.username)
        form.addRow("Portal", self.portal)
        form.addRow("", muted_label('Sweco: "https://ggc.maps.arcgis.com"'))
        form.addRow("Gebruikersnaam", self.username)
        form.addRow("", muted_label('"voornaam.achternaam@sweco.nl"'))
        layout.addLayout(form)
        self.upload_button = QPushButton("Upload naar ArcGIS Online")
        self.upload_button.setProperty("secondary", True)
        self.upload_button.clicked.connect(self.on_upload)
        layout.addWidget(self.upload_button)
        layout.addStretch()

    def start_filtering(self) -> None:
        self.setCurrentWidget(self.filtering_tab)
        self._sync_filtering()
        self.controller.run_part1()

    def start_processing(self) -> None:
        self.setCurrentWidget(self.processing_tab)
        self.controller.run_part2_step1()

    def start_step2(self) -> None:
        self.setCurrentWidget(self.step2_tab)
        self.controller.run_part2_step2()

    def _selected_step2_ids(self) -> list[int]:
        selected_ids: list[int] = []
        for item in self.step2_tree.selectedItems():
            value = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(value, bool):
                continue
            try:
                feature_id = int(value)
            except (TypeError, ValueError):
                continue
            if feature_id > 0:
                selected_ids.append(feature_id)
        return selected_ids

    def _assign_step2_code(self) -> None:
        try:
            self.controller.assign_cable_group_ids(
                self._selected_step2_ids(),
                str(self.step2_code.currentData() or ""),
            )
        except ValueError as error:
            QMessageBox.warning(self, "Kabelcode", str(error))

    def _remove_step2_code(self) -> None:
        self.controller.remove_cable_group_ids(self._selected_step2_ids())

    def upload_credentials(self):
        self.state.arcgis.portal_url = self.portal.text().strip()
        self.state.arcgis.username = self.username.text().strip()
        return self.state.arcgis.portal_url, self.state.arcgis.username

    def _sync_filtering(self, *_args) -> None:
        self.state.proposed_hv.enabled = self.proposed_enabled.isChecked()
        self.state.proposed_hv.code = self.code.text()
        self.state.proposed_hv.vertical_position = self.position.currentText()
        self.state.proposed_hv.work_voltage = self.voltage.value()
        self.state.dirty = True
        self.state.notify()

    def _browse_input(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Selecteer ruwe KLIC GeoPackage", "", "GeoPackage (*.gpkg)")
        if filename:
            self._set_part1_input(Path(filename))

    def _browse_input_gdb(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self,
            "Selecteer ruwe KLIC File Geodatabase",
        )
        if folder:
            self._set_part1_input(Path(folder))

    def _set_part1_input(self, path: Path) -> None:
        valid, message = self.controller.choose_input(path)
        self.input_help.setText(message)
        self.input_help.setObjectName("muted" if valid else "error")
        self.input_help.style().polish(self.input_help)

    def _browse_part2(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(self, "Selecteer KLIC_temp9.gpkg", "", "GeoPackage (*.gpkg)")
        if filename:
            valid, message = self.controller.choose_part2_input(Path(filename))
            self.part2_help.setText(message)
            self.part2_help.setObjectName("muted" if valid else "error")
            self.part2_help.style().polish(self.part2_help)

    def _browse_step2_input(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Selecteer KLIC_temp19.gpkg",
            "",
            "GeoPackage (*.gpkg)",
        )
        if filename:
            valid, message = self.controller.choose_step2_input(Path(filename))
            self.step2_input_help.setText(message)
            self.step2_input_help.setObjectName("muted" if valid else "error")
            self.step2_input_help.style().polish(self.step2_input_help)

    def _browse_upload(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Selecteer map met ArcGIS-resultaten")
        if folder:
            valid, message = self.controller.choose_upload_output(Path(folder))
            self.upload_help.setText(message)
            self.upload_help.setObjectName("muted" if valid else "error")
            self.upload_help.style().polish(self.upload_help)

    def _browse_proposed(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Selecteer voorgestelde hoogspanningsgeometrie", "",
            "GIS-bestanden (*.shp *.gpkg)"
        )
        if filename:
            self.state.proposed_hv.source_path = Path(filename).resolve()
            self.state.dirty = True
            self.state.notify()

    def _combine_gdbs(self) -> None:
        inputs = []
        while True:
            folder = QFileDialog.getExistingDirectory(self, "Selecteer een invoer-FileGDB")
            if not folder:
                break
            path = Path(folder).resolve()
            if path.suffix.lower() != ".gdb":
                QMessageBox.warning(self, "FileGDB combineren", "Selecteer een map die eindigt op .gdb.")
                continue
            if path not in inputs:
                inputs.append(path)
            if QMessageBox.question(self, "FileGDB combineren", "Nog een FileGDB toevoegen?") != QMessageBox.StandardButton.Yes:
                break
        if len(inputs) < 2:
            if inputs:
                QMessageBox.warning(self, "FileGDB combineren", "Selecteer minimaal twee File Geodatabases.")
            return
        output, _ = QFileDialog.getSaveFileName(self, "Kies de uitvoer-FileGDB", "", "Esri File Geodatabase (*.gdb)")
        if not output:
            return
        target = Path(output).resolve()
        if target.suffix.lower() != ".gdb":
            target = target.with_suffix(".gdb")
        overwrite = False
        if target.exists():
            overwrite = QMessageBox.question(
                self, "Uitvoer overschrijven", f"{target}\n\nVolledig overschrijven?"
            ) == QMessageBox.StandardButton.Yes
            if not overwrite:
                return
        self.controller.combine_file_geodatabases(inputs, target, overwrite)

    def _refresh_step2_tree(self) -> None:
        selected_ids = set(self._selected_step2_ids())
        self.step2_tree.clear()
        for feature_id in sorted(self.state.step2_electricity_candidate_ids):
            item = QTreeWidgetItem(
                [f"ID {feature_id}", self.controller.group_for_id(feature_id)]
            )
            item.setData(0, Qt.ItemDataRole.UserRole, feature_id)
            self.step2_tree.addTopLevelItem(item)
            if feature_id in selected_ids:
                item.setSelected(True)

    def refresh(self) -> None:
        busy = self.state.busy
        self.input_path.set_path(self.state.input_path)
        self.part2_path.set_path(self.state.part2_input_path)
        self.step2_input_path.set_path(self.state.step2_input_path)
        self.upload_path.set_path(self.state.upload_output_dir)
        self.proposed_path.set_path(self.state.proposed_hv.source_path)
        for widget in (
            self.input_path.button,
            self.input_gdb_button,
            self.part2_path.button,
            self.step2_input_path.button,
            self.upload_path.button,
            self.gdb_button,
            self.proposed_enabled,
            self.portal,
            self.username,
        ):
            widget.setEnabled(not busy)
        proposed = self.proposed_enabled.isChecked() and not busy
        for widget in (self.proposed_path.button, self.code, self.position, self.voltage):
            widget.setEnabled(proposed)

        conduits = sum(k.layer in CONDUIT_LAYERS for k in self.state.selected_for_analysis)
        influencers = sum(k.layer in INFLUENCER_LAYERS for k in self.state.selected_for_analysis)
        self.processing_summary.setText(
            f"{conduits} buisleiding(en) en {influencers} elektriciteits-/hoogspanningsfeature(s) geselecteerd."
            if self.state.features else "Nog geen Part 1-resultaat geladen."
        )

        self._refresh_step2_tree()
        step2_ready = self.state.stage in {
            WorkflowStage.STEP1_READY,
            WorkflowStage.PART2_READY,
        }
        candidate_count = len(self.state.step2_electricity_candidate_ids)
        assigned_ids = {
            key.persistent_id
            for members in self.state.cable_groups.values()
            for key in members
            if key.persistent_id is not None
        }
        self.step2_summary.setText(
            f"{candidate_count} kabel-ID(s) over na stap 1; "
            f"{len(assigned_ids)} ID(s) hebben een Code."
            if step2_ready else "Voer eerst NEN 3654 stap 1 uit."
        )
        has_step2_code = bool(self.state.cable_groups) or (
            self.state.proposed_hv.enabled and bool(self.state.proposed_hv.code.strip())
        )

        self.filtering_button.setEnabled(self.state.input_valid and not busy)
        self.processing_button.setEnabled(
            not busy and conduits > 0 and self.state.workspace_dir is not None
        )
        self.step2_tree.setEnabled(step2_ready and not busy)
        self.step2_code.setEnabled(step2_ready and not busy)
        self.assign_step2_code_button.setEnabled(step2_ready and not busy)
        self.remove_step2_code_button.setEnabled(step2_ready and not busy)
        self.step2_button.setEnabled(step2_ready and has_step2_code and not busy)
        self.upload_button.setEnabled(not busy and self.state.upload_ready)
