from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable


class TaskState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStage(str, Enum):
    EMPTY = "empty"
    INPUT_READY = "input_ready"
    PART1_READY = "part1_ready"
    STEP1_READY = "step1_ready"
    PART2_READY = "part2_ready"


@dataclass(frozen=True, order=True)
class FeatureKey:
    dataset: str
    layer: str
    fid: int
    persistent_id: int | None = field(default=None, compare=False)

    @property
    def label(self) -> str:
        if self.persistent_id is None:
            return self.layer
        return f"{self.layer} · ID {self.persistent_id}"


@dataclass
class MapFeature:
    key: FeatureKey
    geometry: Any
    attributes: dict[str, Any]
    geometry_type: str
    length_m: float | None = None


@dataclass(frozen=True)
class LoadedMapData:
    features: tuple[MapFeature, ...]
    geojson_by_layer: dict[tuple[str, str], str]
    bounds_by_layer: dict[tuple[str, str], tuple[float, float, float, float]]


@dataclass
class ProposedHighVoltageConfig:
    enabled: bool = True
    code: str = ""
    vertical_position: str = "underground"
    work_voltage: int = 110000
    source_path: Path | None = None


@dataclass
class ArcGISConfig:
    portal_url: str = "https://ggc.maps.arcgis.com"
    username: str = ""


@dataclass
class AppState:
    project_root: Path
    input_path: Path | None = None
    part2_input_path: Path | None = None
    step2_input_path: Path | None = None
    upload_output_dir: Path | None = None
    workspace_dir: Path | None = None
    stage: WorkflowStage = WorkflowStage.EMPTY
    task_state: TaskState = TaskState.IDLE
    task_name: str = ""
    status: str = "Selecteer een KLIC GeoPackage om te beginnen."
    progress: float | None = None
    error_summary: str | None = None
    error_detail: str | None = None
    selected_feature: FeatureKey | None = None
    overlapping_features: tuple[FeatureKey, ...] = ()
    selected_for_analysis: set[FeatureKey] = field(default_factory=set)
    cable_groups: dict[str, set[FeatureKey]] = field(default_factory=dict)
    step2_electricity_candidate_ids: set[int] = field(default_factory=set)
    combine_selected_conduits: set[str] = field(default_factory=set)
    features: dict[FeatureKey, MapFeature] = field(default_factory=dict)
    map_geojson: dict[tuple[str, str], str] = field(default_factory=dict)
    map_bounds: dict[tuple[str, str], tuple[float, float, float, float]] = field(default_factory=dict)
    visible_datasets: set[str] = field(default_factory=set)
    result_sources: dict[str, Path] = field(default_factory=dict)
    result_layers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    visible_result_layers: set[tuple[str, str]] = field(default_factory=set)
    proposed_hv: ProposedHighVoltageConfig = field(default_factory=ProposedHighVoltageConfig)
    arcgis: ArcGISConfig = field(default_factory=ArcGISConfig)
    dirty: bool = False
    listeners: list[Callable[[], None]] = field(default_factory=list, repr=False)

    def subscribe(self, listener: Callable[[], None]) -> None:
        self.listeners.append(listener)

    def notify(self) -> None:
        for listener in tuple(self.listeners):
            listener()

    @property
    def busy(self) -> bool:
        return self.task_state in {TaskState.RUNNING, TaskState.CANCELLING}

    @property
    def input_valid(self) -> bool:
        return (
            self.input_path is not None
            and self.input_path.exists()
            and (
                (
                    self.input_path.suffix.casefold() == ".gpkg"
                    and self.input_path.is_file()
                )
                or (
                    self.input_path.suffix.casefold() == ".gdb"
                    and self.input_path.is_dir()
                )
            )
        )

    @property
    def available_datasets(self) -> set[str]:
        return {key.dataset for key in self.features} | set(self.result_sources)

    def feature_is_visible(self, key: FeatureKey) -> bool:
        if key.dataset == "part1":
            return "part1" in self.visible_datasets
        return (key.dataset, key.layer) in self.visible_result_layers

    @property
    def upload_ready(self) -> bool:
        return (
            self.upload_output_dir is not None
            and self.upload_output_dir.is_dir()
            and any(self.upload_output_dir.glob("*.gpkg"))
        )

    def set_task(
        self,
        state: TaskState,
        *,
        name: str = "",
        status: str | None = None,
        progress: float | None = None,
    ) -> None:
        self.task_state = state
        self.task_name = name
        if status is not None:
            self.status = status
        self.progress = progress
        self.notify()
