from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Callable


class BackendAdapter:
    """Run the unchanged model pipelines in an isolated Python process.

    Isolation prevents the Qt/Python process and verbose model output from
    competing with the GIS calculations. Only coarse progress events cross
    the process boundary.
    """

    _POLL_INTERVAL_SECONDS = 0.10

    def run_part1(
        self,
        *,
        input_path: Path,
        temp_dir: Path,
        config_path: Path,
        proposed_hv_path: Path | None,
        progress: Callable[[int, int, str], None],
        check_cancelled: Callable[[], None],
    ) -> Path:
        arguments = {
            "input_path": str(Path(input_path).resolve()),
            "temp_dir": str(Path(temp_dir).resolve()),
            "config_path": str(Path(config_path).resolve()),
            "proposed_hv_path": (
                str(Path(proposed_hv_path).resolve())
                if proposed_hv_path is not None
                else None
            ),
        }
        result = self._run_model_worker(
            mode="part1",
            arguments=arguments,
            control_parent=Path(temp_dir),
            progress=progress,
            check_cancelled=check_cancelled,
        )
        return Path(result["output"])

    def run_part2(
        self,
        *,
        temp_dir: Path,
        output_dir: Path,
        selection_config: Path,
        model_config: Path,
        progress: Callable[[int, int, str], None],
        check_cancelled: Callable[[], None],
    ) -> list[Path]:
        arguments = {
            "temp_dir": str(Path(temp_dir).resolve()),
            "output_dir": str(Path(output_dir).resolve()),
            "selection_config": str(Path(selection_config).resolve()),
            "model_config": str(Path(model_config).resolve()),
        }
        result = self._run_model_worker(
            mode="part2",
            arguments=arguments,
            control_parent=Path(temp_dir),
            progress=progress,
            check_cancelled=check_cancelled,
        )
        return [Path(path) for path in result["outputs"]]

    def run_part2_step1(
        self,
        *,
        temp_dir: Path,
        output_dir: Path,
        selection_config: Path,
        model_config: Path,
        progress: Callable[[int, int, str], None],
        check_cancelled: Callable[[], None],
    ) -> tuple[list[Path], set[int]]:
        arguments = {
            "temp_dir": str(Path(temp_dir).resolve()),
            "output_dir": str(Path(output_dir).resolve()),
            "selection_config": str(Path(selection_config).resolve()),
            "model_config": str(Path(model_config).resolve()),
        }
        result = self._run_model_worker(
            mode="part2_step1",
            arguments=arguments,
            control_parent=Path(temp_dir),
            progress=progress,
            check_cancelled=check_cancelled,
        )
        outputs = [Path(path) for path in result["outputs"]]
        candidate_ids = {
            int(value) for value in result.get("electricity_candidate_ids", [])
        }
        return outputs, candidate_ids

    def run_part2_step2(
        self,
        *,
        temp_dir: Path,
        output_dir: Path,
        model_config: Path,
        progress: Callable[[int, int, str], None],
        check_cancelled: Callable[[], None],
    ) -> list[Path]:
        arguments = {
            "temp_dir": str(Path(temp_dir).resolve()),
            "output_dir": str(Path(output_dir).resolve()),
            "model_config": str(Path(model_config).resolve()),
        }
        result = self._run_model_worker(
            mode="part2_step2",
            arguments=arguments,
            control_parent=Path(temp_dir),
            progress=progress,
            check_cancelled=check_cancelled,
        )
        return [Path(path) for path in result["outputs"]]

    def _run_model_worker(
        self,
        *,
        mode: str,
        arguments: dict[str, str | None],
        control_parent: Path,
        progress: Callable[[int, int, str], None],
        check_cancelled: Callable[[], None],
    ) -> dict:
        check_cancelled()
        control_root = self._worker_control_root(control_parent)
        control_root.mkdir(parents=True, exist_ok=True)
        control_dir = control_root / f".gui_worker_{uuid.uuid4().hex}"
        control_dir.mkdir(parents=False, exist_ok=False)

        request_path = control_dir / "request.json"
        state_path = control_dir / "state.json"
        result_path = control_dir / "result.json"
        stderr_path = control_dir / "stderr.log"
        request_path.write_text(
            json.dumps({"mode": mode, "arguments": arguments}, ensure_ascii=False),
            encoding="utf-8",
        )

        project_root = Path(__file__).resolve().parents[2]
        command = [
            sys.executable,
            "-u",
            "-m",
            "gui.model_worker",
            "--request",
            str(request_path),
            "--state",
            str(state_path),
            "--result",
            str(result_path),
        ]
        inherit_stdout = self._can_inherit_stdout()
        creation_flags = 0
        if os.name == "nt" and not inherit_stdout:
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"

        process: subprocess.Popen | None = None
        last_state: tuple[int, int, str] | None = None
        try:
            with stderr_path.open("wb") as stderr_file:
                process = subprocess.Popen(
                    command,
                    cwd=project_root,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=None if inherit_stdout else subprocess.DEVNULL,
                    stderr=stderr_file,
                    shell=False,
                    creationflags=creation_flags,
                )
                while process.poll() is None:
                    try:
                        check_cancelled()
                    except BaseException:
                        self._terminate(process)
                        raise

                    state = self._read_json(state_path)
                    if state and state.get("kind") == "progress":
                        event = (
                            int(state["current"]),
                            int(state["total"]),
                            str(state["message"]),
                        )
                        if event != last_state:
                            progress(*event)
                            last_state = event
                    time.sleep(self._POLL_INTERVAL_SECONDS)

            state = self._read_json(state_path)
            if state and state.get("kind") == "progress":
                event = (
                    int(state["current"]),
                    int(state["total"]),
                    str(state["message"]),
                )
                if event != last_state:
                    progress(*event)

            result = self._read_json(result_path)
            if process.returncode == 0 and result and result.get("ok") is True:
                payload = result.get("payload")
                if not isinstance(payload, dict):
                    raise RuntimeError("Modelworker returned an invalid result payload.")
                return payload

            if result and result.get("error"):
                detail = str(result["error"])
            else:
                detail = self._stderr_tail(stderr_path)
            raise RuntimeError(
                f"Modelworker stopped with exit code {process.returncode}. "
                f"{detail or 'No diagnostic information was returned.'}"
            )
        finally:
            if process is not None and process.poll() is None:
                self._terminate(process)
            shutil.rmtree(control_dir, ignore_errors=True)
            try:
                control_root.rmdir()
            except OSError:
                pass

    @staticmethod
    def _worker_control_root(control_parent: Path) -> Path:
        """Keep worker protocol files outside the disposable model temp directory."""
        return Path(control_parent).resolve().parent / ".gui_workers"

    @staticmethod
    def _can_inherit_stdout() -> bool:
        """Return whether the worker can write directly to the launching terminal."""
        stream = sys.stdout
        if stream is None:
            return False
        try:
            stream.fileno()
        except (AttributeError, OSError, ValueError):
            return False
        return True

    @staticmethod
    def _terminate(process: subprocess.Popen) -> None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    @staticmethod
    def _read_json(path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _stderr_tail(path: Path, limit: int = 32_768) -> str:
        try:
            with path.open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                size = stream.tell()
                stream.seek(max(0, size - limit))
                return stream.read().decode("utf-8", errors="replace").strip()
        except OSError:
            return ""

    def upload(
        self,
        *,
        output_dir: Path,
        portal_url: str,
        username: str,
        password: str,
        symbology_path: Path,
        progress: Callable[[int, int, str], None],
    ) -> dict:
        from upload_ArcGIS_Online import upload_klic_project_to_arcgis_online

        return upload_klic_project_to_arcgis_online(
            output_dir=output_dir,
            portal_url=portal_url,
            username=username,
            password=password,
            symbology_path=symbology_path,
            progress=progress,
        )
