from __future__ import annotations

import hashlib
import json

from PySide6.QtCore import QObject, Qt, QUrl, Signal, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from gui.models import AppState, FeatureKey
from gui.services.map_server import LocalMapServer
from gui.views.common import card, muted_label, section_title


DATASET_LABELS = {
    "part1": "Part 1 · filtering",
    "part2_filtered": "Part 2 · relevante features",
    "part2_step1": "Part 2 · resultaten stap 1",
    "part2_no_pass_step1": "Part 2 · no-pass stap 1",
    "part2_step2": "Part 2 · resultaten stap 2",
    "part2_no_pass_step2": "Part 2 · no-pass stap 2",
}


class SecureMapPage(QWebEnginePage):
    ALLOWED_REMOTE_HOSTS = {
        "unpkg.com",
        "a.tile.openstreetmap.org",
        "b.tile.openstreetmap.org",
        "c.tile.openstreetmap.org",
    }

    def __init__(self, local_host: str, local_port: int, parent=None) -> None:
        super().__init__(parent)
        self._local_host = local_host
        self._local_port = local_port

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if url.scheme() in {"qrc", "about", "data"}:
            return True
        if (
            url.scheme() == "http"
            and url.host() == self._local_host
            and url.port() == self._local_port
        ):
            return True
        return (
            url.scheme() == "https"
            and url.host().lower() in self.ALLOWED_REMOTE_HOSTS
        )

    def javaScriptConsoleMessage(self, level, message, line_number, source_id):
        # Keep diagnostics available in the terminal without exposing KLIC data.
        print(f"[kaart-js] {source_id}:{line_number}: {message}")


class MapBridge(QObject):
    featureClicked = Signal(str, str, int)
    overlappingFeaturesSelected = Signal(str)
    selectionCleared = Signal()
    mapReady = Signal()
    mapError = Signal(str)

    @Slot(str, str, int)
    def selectFeature(self, dataset: str, layer: str, fid: int) -> None:
        self.featureClicked.emit(dataset, layer, fid)

    @Slot(str)
    def selectOverlappingFeatures(self, payload: str) -> None:
        self.overlappingFeaturesSelected.emit(payload)

    @Slot()
    def clearFeatureSelection(self) -> None:
        self.selectionCleared.emit()

    @Slot()
    def ready(self) -> None:
        self.mapReady.emit()

    @Slot(str)
    def reportError(self, message: str) -> None:
        self.mapError.emit(message)


class MapPanel(QSplitter):
    def __init__(self, state: AppState, controller, parent=None) -> None:
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.state = state
        self.controller = controller
        self._ready = False
        self._computation_active = False
        self._payload_hashes: dict[tuple[str, str], str] = {}
        self._updating_tree = False
        self._map_server = LocalMapServer(
            self.state.project_root / "gui" / "web"
        )
        self._build_map()
        self._build_layers()
        self.setStretchFactor(0, 5)
        self.setStretchFactor(1, 1)

    def _build_map(self) -> None:
        container = card()
        layout = QVBoxLayout(container)
        header = QHBoxLayout()
        header.addWidget(section_title("Interactieve kaart"), 1)
        fit = QPushButton("Zoom naar zichtbare lagen")
        fit.setProperty("secondary", True)
        fit.clicked.connect(self.fit_visible)
        header.addWidget(fit)
        layout.addLayout(header)

        self.map_status = QLabel()
        self.map_status.setWordWrap(True)
        self.map_status.setStyleSheet(
            "background:#FDEBEC; color:#A70707; padding:8px; border-radius:3px;"
        )
        self.map_status.hide()
        layout.addWidget(self.map_status)

        self.web = QWebEngineView()
        local_url = QUrl(self._map_server.map_url)
        page = SecureMapPage(local_url.host(), local_url.port(), self.web)
        self.web.setPage(page)
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,
            False,
        )
        page.settings().setAttribute(
            QWebEngineSettings.WebAttribute.JavascriptCanOpenWindows,
            False,
        )

        self.bridge = MapBridge(self)
        self.channel = QWebChannel(page)
        self.channel.registerObject("bridge", self.bridge)
        page.setWebChannel(self.channel)
        self.bridge.featureClicked.connect(self._feature_clicked)
        self.bridge.overlappingFeaturesSelected.connect(self._overlapping_features_selected)
        self.bridge.selectionCleared.connect(self._selection_cleared)
        self.bridge.mapReady.connect(self._map_ready)
        self.bridge.mapError.connect(self._show_map_error)
        self.web.loadFinished.connect(self._page_loaded)
        self.web.renderProcessTerminated.connect(self._render_process_terminated)
        self.web.load(local_url)
        layout.addWidget(self.web, 1)
        self.addWidget(container)

    def _build_layers(self) -> None:
        box = card()
        layout = QVBoxLayout(box)
        layout.addWidget(section_title("Kaartlagen"))
        layout.addWidget(
            muted_label(
                "Part 2-lagen worden pas gelezen wanneer ze worden aangevinkt."
            )
        )
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Dataset / laag"])
        self.tree.itemChanged.connect(self._visibility_changed)
        layout.addWidget(self.tree, 1)
        layout.addWidget(section_title("Symbologie"))
        self.legend = QVBoxLayout()
        layout.addLayout(self.legend)
        layout.addStretch()
        self.addWidget(box)

    def shutdown(self) -> None:
        self._map_server.close()

    def set_computation_active(self, active: bool) -> None:
        """Keep the base map alive while computation runs.

        Reloading Qt WebEngine and every GeoJSON source around each model run
        adds avoidable startup and parsing work. An idle MapLibre map does not
        continuously recompute the loaded sources, so retaining it is cheaper
        and keeps the base map visible.
        """
        self._computation_active = bool(active)

    def refresh_data(self) -> None:
        self._rebuild_tree()
        self._sync_sources()
        self._sync_visibility()
        self._sync_selection()
        self.fit_visible()

    def refresh_visual_state(self) -> None:
        self._sync_visibility()
        self._sync_selection()

    def refresh_selection(self) -> None:
        self._sync_selection()

    def _page_loaded(self, success: bool) -> None:
        if not success:
            self._show_map_error(
                "De lokale kaartpagina kon niet worden geladen."
            )

    def _render_process_terminated(self, status, exit_code: int) -> None:
        self._ready = False
        self._show_map_error(
            f"Het kaartproces is gestopt (status {status}, code {exit_code})."
        )

    def _map_ready(self) -> None:
        self._ready = True
        self.map_status.hide()
        self.refresh_data()

    def _show_map_error(self, message: str) -> None:
        self.map_status.setText(message)
        self.map_status.show()

    def _run(self, function: str, *args) -> None:
        if not self._ready:
            return
        encoded = ", ".join(
            json.dumps(arg, ensure_ascii=False) for arg in args
        )
        script = (
            "(() => {"
            "try {"
            f"if (!window.nenMap || typeof window.nenMap.{function} !== 'function') "
            f"throw new Error('Kaartfunctie niet beschikbaar: {function}');"
            f"window.nenMap.{function}({encoded});"
            "return {ok:true};"
            "} catch (error) {"
            "return {ok:false,error:String(error && error.stack || error)};"
            "}"
            "})()"
        )

        def completed(result) -> None:
            if isinstance(result, dict) and not result.get("ok", False):
                self._show_map_error(
                    f"Kaartopdracht {function} is mislukt: "
                    f"{result.get('error', 'onbekende fout')}"
                )

        self.web.page().runJavaScript(script, completed)

    def _sync_sources(self) -> None:
        for key, payload in self.state.map_geojson.items():
            signature = hashlib.sha256(payload.encode("utf-8")).hexdigest()
            if self._payload_hashes.get(key) == signature:
                continue
            source_url = self._map_server.publish_geojson(
                key[0], key[1], payload
            )
            self._run("setLayerUrl", key[0], key[1], source_url)
            self._payload_hashes[key] = signature

        stale = set(self._payload_hashes) - set(self.state.map_geojson)
        for key in stale:
            self._run("removeLayer", key[0], key[1])
            self._map_server.remove_geojson(key[0], key[1])
            del self._payload_hashes[key]

    def _sync_visibility(self) -> None:
        for dataset, layer in self.state.map_geojson:
            visible = (
                dataset == "part1"
                and dataset in self.state.visible_datasets
            ) or (
                dataset != "part1"
                and (dataset, layer) in self.state.visible_result_layers
            )
            self._run(
                "setLayerVisibility",
                dataset,
                layer,
                visible,
            )

    def _sync_selection(self) -> None:
        key = self.state.selected_feature
        feature = self.state.features.get(key) if key else None
        if not feature:
            self._run("clearSelection")
            return
        from shapely.geometry import mapping

        self._run(
            "setSelection",
            {
                "type": "Feature",
                "geometry": mapping(feature.geometry),
                "properties": {"__color": "#A70707"},
            },
        )

    def fit_visible(self) -> None:
        bounds = []
        for key, value in self.state.map_bounds.items():
            visible = (
                key[0] == "part1"
                and "part1" in self.state.visible_datasets
            ) or key in self.state.visible_result_layers
            if visible:
                bounds.append(value)
        if bounds:
            self._run(
                "fitBounds",
                [
                    min(bound[0] for bound in bounds),
                    min(bound[1] for bound in bounds),
                    max(bound[2] for bound in bounds),
                    max(bound[3] for bound in bounds),
                ],
            )

    def _feature_clicked(
        self,
        dataset: str,
        layer: str,
        fid: int,
    ) -> None:
        key = FeatureKey(dataset, layer, fid)
        if key in self.state.features:
            self.controller.select_feature(key)

    def _overlapping_features_selected(self, payload: str) -> None:
        try:
            raw = json.loads(payload)
            if not isinstance(raw, list):
                raise ValueError("verwacht een lijst")
            keys = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                key = FeatureKey(
                    str(item["dataset"]),
                    str(item["layer"]),
                    int(item["fid"]),
                )
                if key in self.state.features:
                    keys.append(key)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._show_map_error(
                f"Overlappende kaartselectie kon niet worden verwerkt: {error}"
            )
            return
        self.controller.select_overlapping_features(keys)

    def _selection_cleared(self) -> None:
        self.controller.select_feature(None)

    def _rebuild_tree(self) -> None:
        expanded = {
            self.tree.topLevelItem(index).data(
                0, Qt.ItemDataRole.UserRole
            )
            for index in range(self.tree.topLevelItemCount())
            if self.tree.topLevelItem(index).isExpanded()
        }
        self._updating_tree = True
        self.tree.clear()

        if any(key[0] == "part1" for key in self.state.map_geojson):
            item = QTreeWidgetItem([DATASET_LABELS["part1"]])
            item.setData(
                0,
                Qt.ItemDataRole.UserRole,
                ("dataset", "part1"),
            )
            item.setFlags(
                item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            )
            item.setCheckState(
                0,
                Qt.CheckState.Checked
                if "part1" in self.state.visible_datasets
                else Qt.CheckState.Unchecked,
            )
            self.tree.addTopLevelItem(item)

        for dataset in sorted(
            self.state.result_sources,
            key=lambda value: DATASET_LABELS.get(value, value),
        ):
            parent = QTreeWidgetItem(
                [DATASET_LABELS.get(dataset, dataset)]
            )
            parent.setData(
                0,
                Qt.ItemDataRole.UserRole,
                ("heading", dataset),
            )
            self.tree.addTopLevelItem(parent)
            parent.setExpanded(
                ("heading", dataset) in expanded
                or dataset in expanded
            )
            for layer in self.state.result_layers.get(dataset, ()):
                child = QTreeWidgetItem([layer])
                child.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    ("layer", dataset, layer),
                )
                child.setFlags(
                    child.flags() | Qt.ItemFlag.ItemIsUserCheckable
                )
                child.setCheckState(
                    0,
                    Qt.CheckState.Checked
                    if (dataset, layer)
                    in self.state.visible_result_layers
                    else Qt.CheckState.Unchecked,
                )
                parent.addChild(child)

        self._updating_tree = False
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for entry in self.controller.symbology.legend_entries():
            label = QLabel(
                f"<span style='color:{entry.color}; "
                f"font-weight:700'>━━</span> {entry.label}"
            )
            self.legend.addWidget(label)

    def _visibility_changed(self, item, column) -> None:
        if self._updating_tree:
            return
        value = item.data(0, Qt.ItemDataRole.UserRole)
        checked = (
            item.checkState(0) == Qt.CheckState.Checked
        )
        if value and value[0] == "dataset":
            self.controller.set_dataset_visibility(
                value[1], checked
            )
        elif value and value[0] == "layer":
            self.controller.set_result_layer_visibility(
                value[1], value[2], checked
            )
