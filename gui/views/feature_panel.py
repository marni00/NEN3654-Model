from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QHeaderView, QPlainTextEdit, QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
)
from gui.models import AppState, FeatureKey
from gui.services.configuration import CONDUIT_LAYERS, ELECTRICITY_LAYER, INFLUENCER_LAYERS, SELECTABLE_LAYERS
from gui.views.common import card, muted_label, section_title
from gui.theme import SPACING

class FeaturePanel(QSplitter):
    def __init__(self, state: AppState, controller, parent=None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self.state, self.controller = state, controller
        self._updating = False
        self.detail = self._detail_widget()
        self.selection = self._selection_widget()
        self.groups = self._groups_widget()
        self.addWidget(self.detail)
        self.addWidget(self.selection)
        self.setStretchFactor(0, 2)
        self.setStretchFactor(1, 5)

    def _detail_widget(self):
        box = card()
        layout = QVBoxLayout(box)
        self.detail_title = section_title("Geen feature geselecteerd")
        layout.addWidget(self.detail_title)
        overlap_row = QHBoxLayout()
        self.overlap_status = QLabel()
        self.previous_overlap = QPushButton("Vorige")
        self.previous_overlap.setProperty("secondary", True)
        self.previous_overlap.clicked.connect(
            lambda: self.controller.cycle_overlapping_feature(-1)
        )
        self.next_overlap = QPushButton("Volgende")
        self.next_overlap.setProperty("secondary", True)
        self.next_overlap.clicked.connect(
            lambda: self.controller.cycle_overlapping_feature(1)
        )
        overlap_row.addWidget(self.overlap_status, 1)
        overlap_row.addWidget(self.previous_overlap)
        overlap_row.addWidget(self.next_overlap)
        layout.addLayout(overlap_row)
        self.detail_text = QPlainTextEdit()
        self.detail_text.setReadOnly(True)
        layout.addWidget(self.detail_text, 1)
        return box

    def _selection_widget(self):
        box = card()
        layout = QVBoxLayout(box)
        layout.addWidget(section_title("Selectie voor Part 2"))
        layout.addWidget(muted_label(
            "Vink features of een volledige laag aan. De kolom Samenvoegen geldt alleen "
            "voor de geselecteerde buisleidingen in die laag."
        ))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Laag / feature", "Meenemen", "Samenvoegen", "Code"])
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.tree.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.itemSelectionChanged.connect(self._selected)
        layout.addWidget(self.tree, 1)
        row = QHBoxLayout()
        self.summary = QLabel()
        self.clear_button = QPushButton("Alles deselecteren")
        self.clear_button.setProperty("secondary", True)
        self.clear_button.clicked.connect(self.controller.clear_analysis_selection)
        row.addWidget(self.summary, 1)
        row.addWidget(self.clear_button)
        layout.addLayout(row)
        return box

    def _groups_widget(self):
        box = card()
        layout = QVBoxLayout(box)
        layout.addWidget(section_title("Kabelgroepen"))
        layout.addWidget(muted_label("Selecteer elektriciteitskabels en wijs één gezamenlijke Code toe."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Code"))
        self.group_code = QLineEdit()
        row.addWidget(self.group_code, 1)
        assign = QPushButton("Code toewijzen")
        assign.clicked.connect(self._assign)
        remove = QPushButton("Uit groep verwijderen")
        remove.setProperty("secondary", True)
        remove.clicked.connect(self._remove)
        row.addWidget(assign)
        row.addWidget(remove)
        layout.addLayout(row)
        self.groups_text = QPlainTextEdit()
        self.groups_text.setReadOnly(True)
        layout.addWidget(self.groups_text, 1)
        return box

    def refresh(self) -> None:
        self._refresh_detail()
        self._refresh_tree()
        self._refresh_groups()

    def _refresh_detail(self) -> None:
        feature = self.state.features.get(self.state.selected_feature)
        overlaps = self.state.overlapping_features
        show_navigation = len(overlaps) > 1 and feature is not None
        self.overlap_status.setVisible(show_navigation)
        self.previous_overlap.setVisible(show_navigation)
        self.next_overlap.setVisible(show_navigation)
        self.previous_overlap.setEnabled(show_navigation and not self.state.busy)
        self.next_overlap.setEnabled(show_navigation and not self.state.busy)
        if not feature:
            self.detail_title.setText("Geen feature geselecteerd")
            self.overlap_status.clear()
            self.detail_text.setPlainText("Klik een feature op de kaart of kies een feature in de selectielijst.")
            return
        if show_navigation:
            try:
                position = overlaps.index(feature.key) + 1
            except ValueError:
                position = 1
            self.overlap_status.setText(
                f"Overlappend resultaat {position} van {len(overlaps)}"
            )
        else:
            self.overlap_status.clear()
        self.detail_title.setText(feature.key.label)
        rows = [
            ("Dataset", feature.key.dataset),
            ("Laag/type", feature.key.layer),
            (
                "ID",
                feature.key.persistent_id
                if feature.key.persistent_id is not None
                else "Niet beschikbaar",
            ),
            ("Geometrie", feature.geometry_type),
        ]
        if feature.length_m is not None:
            rows.append(("Lengte", f"{feature.length_m:,.2f} m"))
        code = self.controller.group_for(feature.key)
        if code:
            rows.append(("Toegewezen Code", code))
        rows.extend(
            sorted(
                (
                    item
                    for item in feature.attributes.items()
                    if item[0].casefold() != "id"
                ),
                key=lambda item: item[0].casefold(),
            )
        )
        self.detail_text.setPlainText("\n\n".join(f"{name}\n{value}" for name, value in rows))

    def _refresh_tree(self) -> None:
        expanded = {self.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
                    for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        self._updating = True
        self.tree.clear()
        by_layer = {layer: [] for layer in SELECTABLE_LAYERS}
        for key in self.state.features:
            if key.dataset == "part1" and key.layer in by_layer:
                by_layer[key.layer].append(key)
        for layer, keys in by_layer.items():
            if not keys:
                continue
            parent = QTreeWidgetItem([f"{layer} ({len(keys)})", "", "", ""])
            parent.setData(0, Qt.ItemDataRole.UserRole, layer)
            parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            selected = sum(k in self.state.selected_for_analysis for k in keys)
            state = Qt.CheckState.Checked if selected == len(keys) else (
                Qt.CheckState.PartiallyChecked if selected else Qt.CheckState.Unchecked
            )
            parent.setCheckState(1, state)
            if layer in CONDUIT_LAYERS:
                parent.setFlags(parent.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                parent.setCheckState(2, Qt.CheckState.Checked if layer in self.state.combine_selected_conduits else Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(layer in expanded)
            for key in sorted(
                keys,
                key=lambda item: (
                    item.persistent_id is None,
                    item.persistent_id if item.persistent_id is not None else item.fid,
                    item.fid,
                ),
            ):
                identifier = (
                    str(key.persistent_id)
                    if key.persistent_id is not None
                    else "niet beschikbaar"
                )
                child = QTreeWidgetItem([f"ID {identifier}", "", "", self.controller.group_for(key)])
                child.setData(0, Qt.ItemDataRole.UserRole, key)
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                child.setCheckState(1, Qt.CheckState.Checked if key in self.state.selected_for_analysis else Qt.CheckState.Unchecked)
                parent.addChild(child)
        self._updating = False
        conduits = sum(k.layer in CONDUIT_LAYERS for k in self.state.selected_for_analysis)
        influencers = sum(k.layer in INFLUENCER_LAYERS for k in self.state.selected_for_analysis)
        self.summary.setText(f"{conduits} buisleiding(en), {influencers} beïnvloeder(s)")
        self.clear_button.setEnabled(bool(self.state.selected_for_analysis) and not self.state.busy)

    def _refresh_groups(self) -> None:
        lines = []
        for code in sorted(self.state.cable_groups, key=str.casefold):
            ids = sorted(
                key.persistent_id
                for key in self.state.cable_groups[code]
                if key.persistent_id is not None
            )
            lines.append(
                f"Code {code}\n{len(ids)} feature(s), IDs: "
                f"{', '.join(map(str, ids))}"
            )
        self.groups_text.setPlainText("\n\n".join(lines) if lines else "Nog geen kabelgroepen aangemaakt.")

    def _item_changed(self, item, column) -> None:
        if self._updating or self.state.busy or column not in (1, 2):
            return
        value = item.data(0, Qt.ItemDataRole.UserRole)
        checked = item.checkState(column) == Qt.CheckState.Checked
        if isinstance(value, FeatureKey) and column == 1:
            self.controller.toggle_analysis_selection(value, checked)
        elif isinstance(value, str):
            if column == 1:
                self.controller.set_layer_analysis_selection(value, checked)
            elif column == 2 and value in CONDUIT_LAYERS:
                self.controller.set_combine_selected_conduits(value, checked)

    def _selected_keys(self):
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.tree.selectedItems()
                if isinstance(item.data(0, Qt.ItemDataRole.UserRole), FeatureKey)]

    def _selected(self) -> None:
        keys = self._selected_keys()
        if keys:
            self.controller.select_feature(keys[-1])

    def _assign(self) -> None:
        try:
            self.controller.assign_cable_group(self._selected_keys(), self.group_code.text())
        except ValueError as error:
            QMessageBox.warning(self, "Kabelgroep", str(error))

    def _remove(self) -> None:
        self.controller.remove_from_cable_groups(self._selected_keys())
