from __future__ import annotations

import time
import uuid
import argparse
import json
import os
import traceback
import time
import tomllib
from pathlib import Path
from typing import Any


def _configure_compute_process() -> None:
    """Keep the worker at normal compute QoS when launched by a GUI process.

    Windows can propagate background/EcoQoS scheduling to child processes.
    The command-line runners normally execute at normal priority without
    execution-speed throttling. Restore that same policy here before importing
    any numerical or GIS libraries. Failure is non-fatal because older Windows
    versions may not expose SetProcessInformation.
    """
    if os.name != "nt":
        return

    import ctypes
    from ctypes import wintypes

    normal_priority_class = 0x00000020
    process_power_throttling = 4
    execution_speed = 0x1

    class ProcessPowerThrottlingState(ctypes.Structure):
        _fields_ = [
            ("Version", wintypes.DWORD),
            ("ControlMask", wintypes.DWORD),
            ("StateMask", wintypes.DWORD),
        ]

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.restype = wintypes.HANDLE
        process_handle = get_current_process()

        set_priority_class = kernel32.SetPriorityClass
        set_priority_class.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        set_priority_class.restype = wintypes.BOOL
        set_priority_class(process_handle, normal_priority_class)

        set_process_information = kernel32.SetProcessInformation
        set_process_information.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        set_process_information.restype = wintypes.BOOL
        power_state = ProcessPowerThrottlingState(
            Version=1,
            ControlMask=execution_speed,
            StateMask=0,
        )
        set_process_information(
            process_handle,
            process_power_throttling,
            ctypes.byref(power_state),
            ctypes.sizeof(power_state),
        )
    except (AttributeError, OSError):
        return


def _write_json_atomic(path: Path, payload: dict) -> None:
    """
    Write JSON atomically, retrying temporary Windows file-lock errors.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Use a unique temporary filename for each write.
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )

    try:
        # Finish writing and close the file before replacing the target.
        with temporary.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False)
            file.flush()

        # Windows may temporarily prevent replacement if the target
        # file is being accessed by another process.
        for attempt in range(8):
            try:
                os.replace(temporary, path)
                return

            except PermissionError:
                if attempt == 7:
                    raise

                # Retry with a gradually increasing delay.
                time.sleep(0.1 * (attempt + 1))

    finally:
        # Remove the temporary file if replacement did not succeed.
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _progress_writer(state_path: Path):
    def report(current: int, total: int, message: str) -> None:
        _write_json_atomic(
            state_path,
            {
                "kind": "progress",
                "current": int(current),
                "total": int(total),
                "message": str(message),
            },
        )

    return report


def _run_part1(arguments: dict[str, Any], state_path: Path) -> dict[str, Any]:
    from run_filtering import run_filtering_pipeline

    proposed_hv = arguments.get("proposed_hv_path")
    output = run_filtering_pipeline(
        input_path=Path(arguments["input_path"]),
        temp_dir=Path(arguments["temp_dir"]),
        config_path=Path(arguments["config_path"]),
        proposed_hv_path=Path(proposed_hv) if proposed_hv else None,
        progress=_progress_writer(state_path),
    )
    return {"output": str(output.resolve())}


def _has_step2_code_input(config_path: Path) -> bool:
    """Return whether the GUI supplied any active Code used by NEN Step 2."""
    with Path(config_path).open("rb") as stream:
        config = tomllib.load(stream)

    proposed = config.get("proposed_highvoltage", {})
    if (
        isinstance(proposed, dict)
        and bool(proposed.get("enabled", False))
        and str(proposed.get("code", "")).strip()
    ):
        return True

    cable_entries = config.get("Elektriciteitskabel", [])
    if isinstance(cable_entries, dict):
        cable_entries = [cable_entries]
    if not isinstance(cable_entries, list):
        return False
    return any(
        isinstance(entry, dict) and str(entry.get("code", "")).strip()
        for entry in cable_entries
    )


def _run_part2(arguments: dict[str, Any], state_path: Path) -> dict[str, Any]:
    """Backward-compatible combined worker mode."""
    from prepare_for_arcgis import prepare_for_arcgis_online
    from run_model import run_model_pipeline

    progress = _progress_writer(state_path)
    model_config = Path(arguments["model_config"])
    run_step2 = _has_step2_code_input(model_config)
    post_steps = 6 if run_step2 else 4

    def model_progress(current: int, total: int, message: str) -> None:
        progress(current, total + post_steps, message)

    run_model_pipeline(
        temp_dir=Path(arguments["temp_dir"]),
        selection_config=Path(arguments["selection_config"]),
        cable_code_config=model_config,
        progress=model_progress,
        run_step2=run_step2,
    )

    def post_progress(current: int, total: int, message: str) -> None:
        progress(12 + current, 12 + total, message)

    outputs = prepare_for_arcgis_online(
        temp_dir=Path(arguments["temp_dir"]),
        output_dir=Path(arguments["output_dir"]),
        progress=post_progress,
        include_step2=run_step2,
    )
    return {
        "outputs": [str(path.resolve()) for path in outputs],
        "step2_executed": run_step2,
    }


def _run_part2_step1(
    arguments: dict[str, Any],
    state_path: Path,
) -> dict[str, Any]:
    from model.part2.assign_electricity_cable_codes import (
        step1_electricity_candidate_ids,
    )
    from prepare_for_arcgis import prepare_step1_for_arcgis_online
    from run_model import run_model_step1_pipeline

    progress = _progress_writer(state_path)

    def model_progress(current: int, total: int, message: str) -> None:
        progress(current, total + 4, message)

    step1_output = run_model_step1_pipeline(
        temp_dir=Path(arguments["temp_dir"]),
        selection_config=Path(arguments["selection_config"]),
        cable_code_config=Path(arguments["model_config"]),
        progress=model_progress,
    )

    def post_progress(current: int, total: int, message: str) -> None:
        progress(12 + current, 12 + total, message)

    outputs = prepare_step1_for_arcgis_online(
        temp_dir=Path(arguments["temp_dir"]),
        output_dir=Path(arguments["output_dir"]),
        progress=post_progress,
    )
    candidate_ids = step1_electricity_candidate_ids(step1_output)
    return {
        "outputs": [str(path.resolve()) for path in outputs],
        "electricity_candidate_ids": candidate_ids,
    }


def _run_part2_step2(
    arguments: dict[str, Any],
    state_path: Path,
) -> dict[str, Any]:
    from prepare_for_arcgis import prepare_step2_for_arcgis_online
    from run_model import run_model_step2_pipeline

    progress = _progress_writer(state_path)

    def model_progress(current: int, total: int, message: str) -> None:
        progress(current, total + 2, message)

    run_model_step2_pipeline(
        temp_dir=Path(arguments["temp_dir"]),
        cable_code_config=Path(arguments["model_config"]),
        progress=model_progress,
    )

    def post_progress(current: int, total: int, message: str) -> None:
        progress(2 + current, 2 + total, message)

    outputs = prepare_step2_for_arcgis_online(
        temp_dir=Path(arguments["temp_dir"]),
        output_dir=Path(arguments["output_dir"]),
        progress=post_progress,
    )
    return {"outputs": [str(path.resolve()) for path in outputs]}


def run(request_path: Path, state_path: Path, result_path: Path) -> int:
    started = time.perf_counter()
    try:
        _configure_compute_process()
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if not isinstance(request, dict):
            raise ValueError("Worker request must be a JSON object.")
        mode = request.get("mode")
        arguments = request.get("arguments")
        if not isinstance(arguments, dict):
            raise ValueError("Worker request is missing its arguments object.")

        print(f"[GUI worker] {mode} gestart.", flush=True)
        if mode == "part1":
            payload = _run_part1(arguments, state_path)
        elif mode == "part2":
            payload = _run_part2(arguments, state_path)
        elif mode == "part2_step1":
            payload = _run_part2_step1(arguments, state_path)
        elif mode == "part2_step2":
            payload = _run_part2_step2(arguments, state_path)
        else:
            raise ValueError(f"Unsupported worker mode: {mode!r}")

        elapsed = time.perf_counter() - started
        print(f"[GUI worker] {mode} gereed in {elapsed:.1f} s.", flush=True)
        _write_json_atomic(
            result_path,
            {"ok": True, "payload": payload, "elapsed_seconds": elapsed},
        )
        return 0
    except Exception:
        print(
            f"[GUI worker] uitvoering mislukt na {time.perf_counter() - started:.1f} s.",
            flush=True,
        )
        _write_json_atomic(
            result_path,
            {
                "ok": False,
                "error": traceback.format_exc(),
            },
        )
        return 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Internal worker for the NEN 3654 desktop application."
    )
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return run(args.request, args.state, args.result)


if __name__ == "__main__":
    raise SystemExit(main())
