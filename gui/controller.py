from __future__ import annotations

import re
import shutil
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from gui.models import AppState, FeatureKey, TaskState, WorkflowStage
from gui.services.backend_adapter import BackendAdapter
from gui.services.configuration import (
    CONDUIT_LAYERS,
    ELECTRICITY_LAYER,
    INFLUENCER_LAYERS,
    SELECTABLE_LAYERS,
    validate_configuration,
    write_runtime_config,
    write_runtime_selection,
)
from gui.services.gis_repository import GISRepository
from gui.services.symbology import Symbology
from gui.services.validation import validate_geopackage, validate_part1_input


class TaskCancelled(RuntimeError):
    pass


class WorkflowController:
    def __init__(
        self,
        root,
        state: AppState,
        *,
        backend: BackendAdapter | None = None,
        repository: GISRepository | None = None,
    ) -> None:
        self.root = root
        self.state = state
        self.backend = backend or BackendAdapter()
        self.repository = repository or GISRepository()
        self.symbology = Symbology(state.project_root / "config" / "symbology.json")
        self._cancel_event = threading.Event()
        self._on_features_changed: Callable[[], None] | None = None
        self._on_visual_state_changed: Callable[[], None] | None = None

    def set_feature_refresh_callback(self, callback: Callable[[], None]) -> None:
        self._on_features_changed = callback

    def set_visual_refresh_callback(self, callback: Callable[[], None]) -> None:
        self._on_visual_state_changed = callback

    def choose_input(self, path: Path) -> tuple[bool, str]:
        valid, message = validate_part1_input(path)
        if not valid:
            return False, message
        self.state.input_path = path.resolve()
        self.state.part2_input_path = None
        self.state.step2_input_path = None
        self.state.workspace_dir = None
        self.state.stage = WorkflowStage.INPUT_READY
        self._clear_loaded_features()
        self.state.status = message
        self.state.error_summary = None
        self.state.error_detail = None
        self.state.dirty = False
        self.state.notify()
        self._notify_features_changed()
        return True, message

    def choose_part2_input(self, path: Path) -> tuple[bool, str]:
        """Load an earlier Part 1 result, reusing its GUI run when possible."""
        path = path.resolve()
        valid, message = validate_geopackage(path)
        if not valid:
            return False, message

        temp5_source = path.with_name("KLIC_temp5.gpkg")
        if not temp5_source.is_file():
            return (
                False,
                "KLIC_temp5.gpkg ontbreekt naast het gekozen Part 1-resultaat. "
                "Dit tussenbestand is nodig om na Processing de ArcGIS-resultaten voor te bereiden.",
            )

        existing_workspace = self._gui_workspace_for_part2_input(path)
        workspace: Path | None = existing_workspace
        created_workspace = False
        try:
            if workspace is None:
                workspace = self._create_workspace(path)
                created_workspace = True
                temp_dir = workspace / "temp"
                part2_input = temp_dir / "KLIC_temp9.gpkg"
                shutil.copy2(path, part2_input)
                shutil.copy2(temp5_source, temp_dir / "KLIC_temp5.gpkg")
            else:
                part2_input = path

            bundle = self._load_dataset_bundle(part2_input, "part1")
            features = list(bundle.features)
        except Exception as error:
            if created_workspace and workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)
            return False, f"Het Part 1-resultaat kon niet worden geladen: {error}"

        # A KLIC_temp9.gpkg selected from data/gui_runs keeps using that exact
        # run. Part 2 intermediates and outputs therefore stay with Part 1.
        self.state.part2_input_path = part2_input
        self.state.step2_input_path = None
        self.state.workspace_dir = workspace
        self.state.features = {feature.key: feature for feature in features}
        self.state.map_geojson = dict(bundle.geojson_by_layer)
        self.state.map_bounds = dict(bundle.bounds_by_layer)
        self.state.visible_datasets = {"part1"}
        self.state.result_sources.clear()
        self.state.result_layers.clear()
        self.state.visible_result_layers.clear()
        self.state.selected_for_analysis = self._default_analysis_selection(features)
        self.state.cable_groups.clear()
        self.state.step2_electricity_candidate_ids.clear()
        self.state.combine_selected_conduits.clear()
        self.state.selected_feature = None
        self.state.overlapping_features = ()
        self.state.stage = WorkflowStage.PART1_READY
        self.state.status = (
            f"Part 1-resultaat geladen: {len(features)} features. "
            "Elektriciteitskabels en voorgestelde hoogspanning zijn vooraf geselecteerd "
            "en kunnen in het tabblad Selectie worden uitgevinkt."
        )
        self.state.error_summary = None
        self.state.error_detail = None
        self.state.dirty = False
        self.state.notify()
        self._notify_features_changed()
        return True, message

    def choose_step2_input(self, path: Path) -> tuple[bool, str]:
        """Load an earlier NEN Step 1 result so Step 2 can continue."""
        path = path.resolve()
        valid, message = validate_geopackage(path)
        if not valid:
            return False, message
        if path.name != "KLIC_temp19.gpkg":
            return False, "Selecteer de uitvoer van NEN stap 1: KLIC_temp19.gpkg."

        temp9_source = path.with_name("KLIC_temp9.gpkg")
        if not temp9_source.is_file():
            return (
                False,
                "KLIC_temp9.gpkg ontbreekt naast KLIC_temp19.gpkg. "
                "Dit bestand is nodig voor de persistente IDs en de kaartselectie.",
            )

        existing_workspace = self._gui_workspace_for_model_input(
            path,
            expected_name="KLIC_temp19.gpkg",
        )
        workspace: Path | None = existing_workspace
        created_workspace = False
        try:
            if workspace is None:
                workspace = self._create_workspace(path)
                created_workspace = True
                temp_dir = workspace / "temp"
                step2_input = temp_dir / "KLIC_temp19.gpkg"
                part2_input = temp_dir / "KLIC_temp9.gpkg"
                shutil.copy2(path, step2_input)
                shutil.copy2(temp9_source, part2_input)

                source_output = path.parent.parent / "output"
                if source_output.is_dir():
                    for filename in (
                        "KLIC_filtered.gpkg",
                        "KLIC_results_step1.gpkg",
                        "KLIC_no_pass_step1.gpkg",
                    ):
                        source = source_output / filename
                        if source.is_file():
                            shutil.copy2(source, workspace / "output" / filename)
            else:
                step2_input = path
                part2_input = path.with_name("KLIC_temp9.gpkg")

            bundle = self._load_dataset_bundle(part2_input, "part1")
            features = list(bundle.features)

            from model.part2.assign_electricity_cable_codes import (
                step1_electricity_candidate_ids,
            )
            candidate_ids = set(step1_electricity_candidate_ids(step2_input))

            sources = self.repository.discover_part2_outputs(workspace / "output")
            sources = {
                dataset: source
                for dataset, source in sources.items()
                if dataset not in {"part2_step2", "part2_no_pass_step2"}
            }
            layers = self.repository.discover_dataset_layers(sources)
        except Exception as error:
            if created_workspace and workspace is not None:
                shutil.rmtree(workspace, ignore_errors=True)
            return False, f"De uitvoer van NEN stap 1 kon niet worden geladen: {error}"

        self.state.part2_input_path = part2_input
        self.state.step2_input_path = step2_input
        self.state.workspace_dir = workspace
        self.state.features = {feature.key: feature for feature in features}
        self.state.map_geojson = dict(bundle.geojson_by_layer)
        self.state.map_bounds = dict(bundle.bounds_by_layer)
        self.state.visible_datasets = {"part1"}
        self.state.result_sources = sources
        self.state.result_layers = layers
        self.state.visible_result_layers.clear()
        self.state.selected_for_analysis = self._default_analysis_selection(features)
        self.state.cable_groups.clear()
        self.state.step2_electricity_candidate_ids = candidate_ids
        self.state.combine_selected_conduits.clear()
        self.state.selected_feature = None
        self.state.overlapping_features = ()
        self.state.upload_output_dir = workspace / "output"
        self.state.stage = WorkflowStage.STEP1_READY
        self.state.status = (
            f"Eerdere uitvoer van NEN stap 1 geladen: {len(candidate_ids)} "
            "elektriciteitskabel-ID(s) kunnen een Code krijgen voor stap 2."
        )
        self.state.error_summary = None
        self.state.error_detail = None
        self.state.dirty = False
        self.state.notify()
        self._notify_features_changed()
        return True, self.state.status

    def choose_upload_output(self, path: Path) -> tuple[bool, str]:
        path = path.resolve()
        if not path.is_dir():
            return False, "Selecteer een bestaande outputmap."
        geopackages = sorted(path.glob("*.gpkg"))
        if not geopackages:
            return False, "De geselecteerde map bevat geen GeoPackage-resultaten."
        self.state.upload_output_dir = path
        self.state.status = (
            f"ArcGIS-outputmap geladen: {len(geopackages)} GeoPackage-bestand(en)."
        )
        self.state.error_summary = None
        self.state.error_detail = None
        self.state.notify()
        return True, self.state.status

    def run_part1(self) -> None:
        if self.state.busy or not self.state.input_valid:
            return
        errors = validate_configuration(self.state.proposed_hv)
        if errors:
            self._fail_validation("\n".join(f"• {item}" for item in errors))
            return

        workspace = self._create_workspace(self.state.input_path)
        self.state.workspace_dir = workspace
        runtime_config = workspace / "config" / "config.toml"
        write_runtime_config(runtime_config, self.state.proposed_hv, {})

        def work():
            output = self.backend.run_part1(
                input_path=self.state.input_path,
                temp_dir=workspace / "temp",
                config_path=runtime_config,
                proposed_hv_path=self.state.proposed_hv.source_path,
                progress=self._thread_progress,
                check_cancelled=self._check_cancelled,
            )
            bundle = self._load_dataset_bundle(output, "part1")
            return output, bundle

        def success(result) -> None:
            output, bundle = result
            features = list(bundle.features)
            self.state.part2_input_path = output
            self.state.step2_input_path = None
            self.state.features = {feature.key: feature for feature in features}
            self.state.map_geojson = dict(bundle.geojson_by_layer)
            self.state.map_bounds = dict(bundle.bounds_by_layer)
            self.state.visible_datasets = {"part1"}
            self.state.result_sources.clear()
            self.state.result_layers.clear()
            self.state.visible_result_layers.clear()
            self.state.selected_for_analysis = self._default_analysis_selection(features)
            self.state.cable_groups.clear()
            self.state.step2_electricity_candidate_ids.clear()
            self.state.combine_selected_conduits.clear()
            self.state.selected_feature = None
            self.state.overlapping_features = ()
            self.state.stage = WorkflowStage.PART1_READY
            self.state.status = (
                f"Filtering gereed: {len(features)} features geladen. "
                "Elektriciteitskabels en voorgestelde hoogspanning zijn vooraf geselecteerd "
                "en kunnen in het tabblad Selectie worden uitgevinkt."
            )
            self.state.dirty = False
            self._notify_features_changed()

        self._start_task("NEN3654 Filtering", "Part 1 uitvoeren...", work, success)

    def run_part2_step1(self) -> None:
        if self.state.busy or self.state.stage not in {
            WorkflowStage.PART1_READY,
            WorkflowStage.STEP1_READY,
            WorkflowStage.PART2_READY,
        }:
            return
        selected_conduits = {
            key
            for key in self.state.selected_for_analysis
            if key.dataset == "part1" and key.layer in CONDUIT_LAYERS
        }
        if not selected_conduits:
            self._fail_validation(
                "Selecteer minimaal één buisleiding voor NEN 3654 stap 1."
            )
            return
        if self.state.workspace_dir is None:
            self._fail_validation(
                "De Processing-werkmap ontbreekt. Selecteer een Part 1-resultaat "
                "of voer Filtering uit."
            )
            return

        part2_input = self.state.workspace_dir / "temp" / "KLIC_temp9.gpkg"
        if not part2_input.is_file():
            self._fail_validation(
                "De uitvoer van Part 1 ontbreekt: KLIC_temp9.gpkg kan niet als "
                "invoer voor NEN 3654 stap 1 worden gebruikt."
            )
            return
        self.state.part2_input_path = part2_input

        config_dir = self.state.workspace_dir / "config"
        runtime_config = config_dir / "config.toml"
        runtime_selection = config_dir / "selection.toml"

        # Cable Codes are intentionally absent during Step 1. They are assigned
        # afterwards only to the persistent IDs that remain relevant to Step 2.
        write_runtime_config(runtime_config, self.state.proposed_hv, {})
        write_runtime_selection(
            runtime_selection,
            self.state.selected_for_analysis,
            self.state.combine_selected_conduits,
        )

        def work():
            outputs, candidate_ids = self.backend.run_part2_step1(
                temp_dir=self.state.workspace_dir / "temp",
                output_dir=self.state.workspace_dir / "output",
                selection_config=runtime_selection,
                model_config=runtime_config,
                progress=self._thread_progress,
                check_cancelled=self._check_cancelled,
            )
            output_dir = self.state.workspace_dir / "output"
            sources = self.repository.discover_part2_outputs(output_dir)
            layers = self.repository.discover_dataset_layers(sources)
            return outputs, candidate_ids, sources, layers

        def success(result) -> None:
            outputs, candidate_ids, sources, layers = result
            self._retain_part1_features()
            self.state.result_sources = sources
            self.state.result_layers = layers
            self.state.visible_result_layers.clear()
            self.state.visible_datasets = {"part1"}
            self.state.upload_output_dir = self.state.workspace_dir / "output"
            self.state.step2_input_path = self.state.workspace_dir / "temp" / "KLIC_temp19.gpkg"
            self.state.step2_electricity_candidate_ids = set(candidate_ids)
            self.state.cable_groups.clear()
            self.state.stage = WorkflowStage.STEP1_READY
            self.state.status = (
                f"NEN 3654 stap 1 gereed: {len(outputs)} resultaatbestanden. "
                f"{len(candidate_ids)} elektriciteitskabel-ID(s) blijven over "
                "voor mogelijke Code-toewijzing in stap 2."
            )
            self.state.dirty = False
            self._notify_features_changed()

        self._start_task(
            "NEN 3654 stap 1",
            "Gebruikersselectie voor stap 1 verwerken...",
            work,
            success,
        )

    def run_part2_step2(self) -> None:
        if self.state.busy or self.state.stage not in {
            WorkflowStage.STEP1_READY,
            WorkflowStage.PART2_READY,
        }:
            return
        if self.state.workspace_dir is None:
            self._fail_validation("De werkmap van NEN 3654 stap 1 ontbreekt.")
            return

        has_proposed_code = (
            self.state.proposed_hv.enabled
            and bool(self.state.proposed_hv.code.strip())
        )
        if not self.state.cable_groups and not has_proposed_code:
            self._fail_validation(
                "Wijs minimaal één Code toe aan een resterende "
                "elektriciteitskabel voordat u NEN 3654 stap 2 start."
            )
            return

        step1_output = self.state.workspace_dir / "temp" / "KLIC_temp19.gpkg"
        if not step1_output.is_file():
            self._fail_validation(
                "De uitvoer van NEN 3654 stap 1 ontbreekt: KLIC_temp19.gpkg."
            )
            return

        runtime_config = self.state.workspace_dir / "config" / "config.toml"
        write_runtime_config(
            runtime_config,
            self.state.proposed_hv,
            self.state.cable_groups,
        )

        def work():
            outputs = self.backend.run_part2_step2(
                temp_dir=self.state.workspace_dir / "temp",
                output_dir=self.state.workspace_dir / "output",
                model_config=runtime_config,
                progress=self._thread_progress,
                check_cancelled=self._check_cancelled,
            )
            output_dir = self.state.workspace_dir / "output"
            sources = self.repository.discover_part2_outputs(output_dir)
            layers = self.repository.discover_dataset_layers(sources)
            return outputs, sources, layers

        def success(result) -> None:
            outputs, sources, layers = result
            self._retain_part1_features()
            self.state.result_sources = sources
            self.state.result_layers = layers
            self.state.visible_result_layers.clear()
            self.state.visible_datasets = {"part1"}
            self.state.upload_output_dir = self.state.workspace_dir / "output"
            self.state.stage = WorkflowStage.PART2_READY
            self.state.status = (
                f"NEN 3654 stap 2 gereed: {len(outputs)} nieuwe resultaatbestanden. "
                "Schakel een resultaatlaag in via de legenda om deze op de kaart te laden."
            )
            self.state.dirty = False
            self._notify_features_changed()

        self._start_task(
            "NEN 3654 stap 2",
            "Codes verwerken en NEN 3654 stap 2 uitvoeren...",
            work,
            success,
        )

    def run_part2(self) -> None:
        """Compatibility alias: start the first phase of the split workflow."""
        self.run_part2_step1()


    def set_dataset_visibility(self, dataset: str, visible: bool) -> None:
        """Toggle a fully loaded dataset such as the Part 1 input."""
        if self.state.busy:
            return
        if visible:
            self.state.visible_datasets.add(dataset)
        else:
            self.state.visible_datasets.discard(dataset)
        self.state.notify()
        self._notify_visual_state_changed()

    def set_result_layer_visibility(
        self,
        dataset: str,
        layer: str,
        visible: bool,
    ) -> None:
        """Toggle and lazily load one layer from a Part 2 result dataset."""
        if self.state.busy:
            return

        layer_key = (dataset, layer)
        if not visible:
            if layer_key in self.state.visible_result_layers:
                self.state.visible_result_layers.discard(layer_key)
                self.state.notify()
                self._notify_visual_state_changed()
            return

        loaded = any(
            key.dataset == dataset and key.layer == layer
            for key in self.state.features
        )
        if loaded:
            self.state.visible_result_layers.add(layer_key)
            self.state.notify()
            self._notify_visual_state_changed()
            return

        source = self.state.result_sources.get(dataset)
        if source is None:
            self._fail_validation(f"Geen resultaatbestand beschikbaar voor {dataset}.")
            return
        if layer not in self.state.result_layers.get(dataset, ()):
            self._fail_validation(
                f"Laag {layer} is niet beschikbaar in resultaatbestand {source.name}."
            )
            return

        def work():
            return self._load_dataset_bundle(
                source,
                dataset,
                layers=(layer,),
                progress=self._thread_progress,
            )

        def success(bundle) -> None:
            features = list(bundle.features)
            self.state.features.update(
                {feature.key: feature for feature in features}
            )
            self.state.map_geojson.update(bundle.geojson_by_layer)
            self.state.map_bounds.update(bundle.bounds_by_layer)
            self.state.visible_result_layers.add(layer_key)
            self.state.status = (
                f"Resultaatlaag geladen: {source.name} / {layer} "
                f"({len(features)} features)."
            )
            self._notify_features_changed()

        self._start_task(
            "Resultaatlaag laden",
            f"Resultaatlaag laden: {source.name} / {layer}",
            work,
            success,
        )

    def combine_file_geodatabases(
        self,
        input_gdbs: list[Path],
        output_gdb: Path,
        overwrite: bool,
    ) -> None:
        """Run the optional FileGDB merge utility outside the model workflow."""
        if self.state.busy:
            return

        def work():
            from gui.services.gdb_combiner import combine_file_geodatabases
            return combine_file_geodatabases(
                input_gdbs=input_gdbs,
                output_gdb=output_gdb,
                overwrite=overwrite,
                progress=self._thread_progress,
                check_cancelled=self._check_cancelled,
            )

        def success(summary) -> None:
            self.state.status = (
                f"FileGDB-combinatie gereed: {summary['layers']} laag/lagen, "
                f"{summary['written']} features geschreven en "
                f"{summary['duplicates']} duplicaten overgeslagen."
            )

        self._start_task(
            "FileGDB's combineren",
            "File Geodatabases combineren...",
            work,
            success,
        )

    def upload(self, portal_url: str, username: str, password: str) -> None:
        if self.state.busy or not self.state.upload_ready:
            return
        if not portal_url.strip() or not username.strip() or not password:
            self._fail_validation("Portal, gebruikersnaam en wachtwoord zijn verplicht.")
            return

        self.state.arcgis.portal_url = portal_url.strip()
        self.state.arcgis.username = username.strip()
        output_dir = self.state.upload_output_dir
        if output_dir is None:
            self._fail_validation("De ArcGIS-outputmap ontbreekt.")
            return

        def work():
            return self.backend.upload(
                output_dir=output_dir,
                portal_url=portal_url.strip(),
                username=username.strip(),
                password=password,
                symbology_path=self.state.project_root / "config" / "symbology.json",
                progress=self._thread_progress,
            )

        def success(result: dict) -> None:
            failed = result.get("failed", [])
            successful = result.get("successful", [])
            if failed:
                self.state.status = (
                    f"Upload deels gereed: {len(successful)} gelukt, "
                    f"{len(failed)} mislukt."
                )
            else:
                self.state.status = f"Upload gereed: {len(successful)} bestanden gepubliceerd."

        self._start_task("ArcGIS Online-upload", "Authenticeren...", work, success)

    def cancel(self) -> None:
        if self.state.task_state != TaskState.RUNNING:
            return
        self._cancel_event.set()
        self.state.set_task(
            TaskState.CANCELLING,
            name=self.state.task_name,
            status="Annuleren na de huidige backendstap...",
            progress=self.state.progress,
        )

    def select_feature(self, key: FeatureKey | None) -> None:
        changed = (
            key != self.state.selected_feature
            or bool(self.state.overlapping_features)
        )
        if not changed:
            return
        self.state.selected_feature = key
        self.state.overlapping_features = ()
        self.state.notify()
        self._notify_visual_state_changed()

    def select_overlapping_features(self, keys) -> None:
        """Select the first map hit and retain all hits for pane navigation."""
        unique = tuple(dict.fromkeys(
            key for key in keys if key in self.state.features
        ))
        if not unique:
            self.select_feature(None)
            return
        if (
            unique == self.state.overlapping_features
            and self.state.selected_feature in unique
        ):
            return
        self.state.overlapping_features = unique
        self.state.selected_feature = unique[0]
        self.state.notify()
        self._notify_visual_state_changed()

    def cycle_overlapping_feature(self, offset: int) -> None:
        keys = self.state.overlapping_features
        if len(keys) < 2:
            return
        try:
            current = keys.index(self.state.selected_feature)
        except ValueError:
            current = 0
        self.state.selected_feature = keys[(current + offset) % len(keys)]
        self.state.notify()
        self._notify_visual_state_changed()

    def toggle_analysis_selection(self, key: FeatureKey, selected: bool) -> None:
        self.set_analysis_selection((key,), selected)

    def set_analysis_selection(
        self,
        keys,
        selected: bool,
    ) -> None:
        if self.state.busy:
            return
        valid = {
            key
            for key in keys
            if key.dataset == "part1" and key.layer in SELECTABLE_LAYERS
        }
        if not valid:
            return
        before = set(self.state.selected_for_analysis)
        if selected:
            self.state.selected_for_analysis.update(valid)
        else:
            self.state.selected_for_analysis.difference_update(valid)
        if self.state.selected_for_analysis == before:
            return
        self._invalidate_step1_results()
        self.state.dirty = True
        self.state.notify()
        self._notify_visual_state_changed()

    def set_layer_analysis_selection(self, layer: str, selected: bool) -> None:
        if layer not in SELECTABLE_LAYERS:
            return
        keys = (
            key
            for key in self.state.features
            if key.dataset == "part1" and key.layer == layer
        )
        self.set_analysis_selection(keys, selected)

    def set_combine_selected_conduits(self, layer: str, combine: bool) -> None:
        """Configure whether selected features in a conduit layer are dissolved."""
        if self.state.busy or layer not in CONDUIT_LAYERS:
            return
        before = set(self.state.combine_selected_conduits)
        if combine:
            self.state.combine_selected_conduits.add(layer)
        else:
            self.state.combine_selected_conduits.discard(layer)
        if self.state.combine_selected_conduits == before:
            return
        self._invalidate_step1_results()
        self.state.dirty = True
        self.state.notify()

    def clear_analysis_selection(self) -> None:
        if self.state.busy or not self.state.selected_for_analysis:
            return
        self.state.selected_for_analysis.clear()
        self._invalidate_step1_results()
        self.state.dirty = True
        self.state.notify()
        self._notify_visual_state_changed()

    def assign_cable_group(self, keys: list[FeatureKey], code: str) -> None:
        """Assign a Step 2 Code using persistent electricity-cable IDs."""
        persistent_ids = {
            int(key.persistent_id)
            for key in keys
            if key.layer == ELECTRICITY_LAYER and key.persistent_id is not None
        }
        preferred_keys = {
            int(key.persistent_id): key
            for key in keys
            if (
                key.dataset == "part1"
                and key.layer == ELECTRICITY_LAYER
                and key.persistent_id is not None
            )
        }
        self.assign_cable_group_ids(
            persistent_ids,
            code,
            preferred_keys=preferred_keys,
        )

    def assign_cable_group_ids(
        self,
        persistent_ids: set[int] | list[int] | tuple[int, ...],
        code: str,
        *,
        preferred_keys: dict[int, FeatureKey] | None = None,
    ) -> None:
        """Assign one Code to Step 1 no-pass cables by persistent ID."""
        if self.state.busy:
            raise ValueError("Kabelgroepen kunnen niet tijdens een actief proces worden gewijzigd.")
        code = code.strip()
        if not code:
            raise ValueError("Code mag niet leeg zijn.")
        if self.state.stage not in {
            WorkflowStage.STEP1_READY,
            WorkflowStage.PART2_READY,
        }:
            raise ValueError(
                "Voer eerst NEN 3654 stap 1 uit; Codes kunnen alleen aan "
                "de resterende elektriciteitskabels worden toegewezen."
            )

        requested_ids = {
            int(feature_id)
            for feature_id in persistent_ids
            if not isinstance(feature_id, bool) and int(feature_id) > 0
        }
        valid_ids = requested_ids.intersection(
            self.state.step2_electricity_candidate_ids
        )
        if not valid_ids:
            raise ValueError(
                "Selecteer minimaal één elektriciteitskabel die na stap 1 "
                "voor stap 2 is overgebleven."
            )

        rejected_ids = requested_ids.difference(valid_ids)
        if rejected_ids:
            rejected = ", ".join(map(str, sorted(rejected_ids)))
            raise ValueError(
                "De volgende elektriciteitskabel-ID(s) behoren niet tot de "
                f"no-pass selectie van stap 1: {rejected}."
            )

        updated_groups = {
            group_code: {
                member
                for member in members
                if member.persistent_id not in valid_ids
            }
            for group_code, members in self.state.cable_groups.items()
        }
        self.state.cable_groups = {
            group_code: members
            for group_code, members in updated_groups.items()
            if members
        }
        self.state.cable_groups.setdefault(code, set()).update(
            self._step2_cable_key(feature_id, preferred_keys=preferred_keys)
            for feature_id in valid_ids
        )
        self._invalidate_step2_results()
        self.state.dirty = True
        self.state.notify()
        self._notify_visual_state_changed()

    def remove_from_cable_groups(self, keys: list[FeatureKey]) -> None:
        persistent_ids = {
            int(key.persistent_id)
            for key in keys
            if key.layer == ELECTRICITY_LAYER and key.persistent_id is not None
        }
        self.remove_cable_group_ids(persistent_ids)

    def remove_cable_group_ids(
        self,
        persistent_ids: set[int] | list[int] | tuple[int, ...],
    ) -> None:
        if self.state.busy:
            return
        target_ids = {
            int(feature_id)
            for feature_id in persistent_ids
            if not isinstance(feature_id, bool) and int(feature_id) > 0
        }
        if not target_ids:
            return

        updated_groups = {
            code: {
                member
                for member in members
                if member.persistent_id not in target_ids
            }
            for code, members in self.state.cable_groups.items()
        }
        self.state.cable_groups = {
            code: members
            for code, members in updated_groups.items()
            if members
        }
        self._invalidate_step2_results()
        self.state.dirty = True
        self.state.notify()
        self._notify_visual_state_changed()

    def group_for_id(self, persistent_id: int) -> str:
        for code, members in self.state.cable_groups.items():
            if any(member.persistent_id == persistent_id for member in members):
                return code
        return ""

    def group_for(self, key: FeatureKey) -> str:
        if key.layer == ELECTRICITY_LAYER and key.persistent_id is not None:
            return self.group_for_id(int(key.persistent_id))
        for code, members in self.state.cable_groups.items():
            if key in members:
                return code
        return ""

    def _step2_cable_key(
        self,
        persistent_id: int,
        *,
        preferred_keys: dict[int, FeatureKey] | None = None,
    ) -> FeatureKey:
        """Return the loaded Part 1 key, or a stable ID-only fallback key."""
        if preferred_keys and persistent_id in preferred_keys:
            return preferred_keys[persistent_id]
        for key in self.state.features:
            if (
                key.dataset == "part1"
                and key.layer == ELECTRICITY_LAYER
                and key.persistent_id == persistent_id
            ):
                return key
        return FeatureKey(
            "part1",
            ELECTRICITY_LAYER,
            persistent_id,
            persistent_id,
        )

    def _retain_part1_features(self) -> None:
        self.state.features = {
            key: value
            for key, value in self.state.features.items()
            if key.dataset == "part1"
        }
        self.state.map_geojson = {
            key: value
            for key, value in self.state.map_geojson.items()
            if key[0] == "part1"
        }
        self.state.map_bounds = {
            key: value
            for key, value in self.state.map_bounds.items()
            if key[0] == "part1"
        }

    def _invalidate_step1_results(self) -> None:
        if self.state.stage not in {
            WorkflowStage.STEP1_READY,
            WorkflowStage.PART2_READY,
        }:
            return
        self._retain_part1_features()
        self.state.step2_electricity_candidate_ids.clear()
        self.state.step2_input_path = None
        self.state.cable_groups.clear()
        self.state.result_sources.clear()
        self.state.result_layers.clear()
        self.state.visible_result_layers.clear()
        self.state.stage = WorkflowStage.PART1_READY

    def _invalidate_step2_results(self) -> None:
        if self.state.stage != WorkflowStage.PART2_READY:
            return
        step2_datasets = {"part2_step2", "part2_no_pass_step2"}
        self.state.features = {
            key: value
            for key, value in self.state.features.items()
            if key.dataset not in step2_datasets
        }
        self.state.map_geojson = {
            key: value
            for key, value in self.state.map_geojson.items()
            if key[0] not in step2_datasets
        }
        self.state.map_bounds = {
            key: value
            for key, value in self.state.map_bounds.items()
            if key[0] not in step2_datasets
        }
        for dataset in step2_datasets:
            self.state.result_sources.pop(dataset, None)
            self.state.result_layers.pop(dataset, None)
        self.state.visible_result_layers = {
            key
            for key in self.state.visible_result_layers
            if key[0] not in step2_datasets
        }
        self.state.stage = WorkflowStage.STEP1_READY

    def _start_task(self, name: str, status: str, work, success) -> None:
        self._cancel_event.clear()
        self.state.error_summary = None
        self.state.error_detail = None
        self.state.set_task(TaskState.RUNNING, name=name, status=status, progress=None)

        def runner() -> None:
            try:
                result = work()
            except TaskCancelled:
                self.root.after(0, self._finish_cancelled)
            except Exception as error:
                detail = traceback.format_exc()

                self.root.after(
                    0,
                    lambda name=name, error=error, detail=detail:
                        self._finish_failed(name, error, detail)
                )
            else:
                self.root.after(0, lambda: self._finish_success(success, result))

        threading.Thread(target=runner, name=f"nen3654-{name}", daemon=True).start()

    def _finish_success(self, success, result) -> None:
        success(result)
        self.state.set_task(
            TaskState.COMPLETED,
            name=self.state.task_name,
            status=self.state.status,
            progress=1.0,
        )

    def _finish_failed(self, name: str, error: Exception, detail: str) -> None:
        self.state.error_summary = f"{name} is mislukt: {error}"
        self.state.error_detail = detail
        self.state.set_task(
            TaskState.FAILED,
            name=name,
            status=self.state.error_summary,
            progress=None,
        )

    def _finish_cancelled(self) -> None:
        self.state.set_task(
            TaskState.CANCELLED,
            name=self.state.task_name,
            status="Proces geannuleerd. Reeds afgeronde tussenstappen zijn niet als resultaat geladen.",
            progress=None,
        )

    def _thread_progress(self, current: int, total: int, message: str) -> None:
        progress = current / total if total > 0 else None
        self.root.after(
            0,
            lambda: self.state.set_task(
                self.state.task_state,
                name=self.state.task_name,
                status=message,
                progress=progress,
            ),
        )

    def _check_cancelled(self) -> None:
        if self._cancel_event.is_set():
            raise TaskCancelled()

    def _fail_validation(self, message: str) -> None:
        self.state.error_summary = message
        self.state.error_detail = None
        self.state.set_task(TaskState.FAILED, status=message, progress=None)

    def _gui_workspace_for_part2_input(self, input_path: Path) -> Path | None:
        """Return the existing GUI run that owns the selected Part 1 output."""
        return self._gui_workspace_for_model_input(
            input_path,
            expected_name="KLIC_temp9.gpkg",
        )

    def _gui_workspace_for_model_input(
        self,
        input_path: Path,
        *,
        expected_name: str,
    ) -> Path | None:
        """Return the existing GUI run that owns a selected model intermediate."""
        input_path = Path(input_path).resolve()
        gui_runs_dir = (self.state.project_root / "data" / "gui_runs").resolve()
        if input_path.name != expected_name or input_path.parent.name != "temp":
            return None

        workspace = input_path.parent.parent
        try:
            workspace.relative_to(gui_runs_dir)
        except ValueError:
            return None

        if workspace == gui_runs_dir:
            return None
        if not (workspace / "config").is_dir() or not (workspace / "output").is_dir():
            return None
        return workspace

    def _create_workspace(self, input_path: Path) -> Path:
        safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", input_path.stem).strip("_") or "klic"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        workspace = self.state.project_root / "data" / "gui_runs" / f"{safe_name}_{timestamp}"
        (workspace / "temp").mkdir(parents=True, exist_ok=False)
        (workspace / "output").mkdir()
        (workspace / "config").mkdir()
        return workspace

    @staticmethod
    def _default_analysis_selection(features) -> set[FeatureKey]:
        """Preselect influencer features while keeping them explicitly editable."""
        return {
            feature.key
            for feature in features
            if feature.key.dataset == "part1"
            and feature.key.layer in INFLUENCER_LAYERS
        }

    def _clear_loaded_features(self) -> None:
        self.state.step2_input_path = None
        self.state.features.clear()
        self.state.map_geojson.clear()
        self.state.map_bounds.clear()
        self.state.selected_for_analysis.clear()
        self.state.cable_groups.clear()
        self.state.step2_electricity_candidate_ids.clear()
        self.state.combine_selected_conduits.clear()
        self.state.selected_feature = None
        self.state.overlapping_features = ()
        self.state.visible_datasets.clear()
        self.state.result_sources.clear()
        self.state.result_layers.clear()
        self.state.visible_result_layers.clear()

    def _load_dataset_bundle(self, path: Path, dataset: str, **kwargs):
        """Use the MapLibre-aware repository API, with test-double compatibility."""
        loader = getattr(self.repository, "load_dataset_bundle", None)
        if loader is not None:
            return loader(
                path,
                dataset,
                symbology=self.symbology,
                **kwargs,
            )
        from gui.models import LoadedMapData
        features = tuple(self.repository.load_dataset(path, dataset, **kwargs))
        return LoadedMapData(features, {}, {})

    def _notify_features_changed(self) -> None:
        if self._on_features_changed:
            self._on_features_changed()

    def _notify_visual_state_changed(self) -> None:
        if self._on_visual_state_changed:
            self._on_visual_state_changed()
