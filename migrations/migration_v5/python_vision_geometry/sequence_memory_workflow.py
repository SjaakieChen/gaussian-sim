"""Persist and replay v5 vision capture memory.

The v5 geometry solvers can already convert focused rectangle and ball
features into machine coordinates. This module adds the durable
layer that a real sequence needs: each capture can be recorded with its saved
vision session, image path, and queried machine positions, then later converted
into solver payloads.

It does not move hardware.
"""

from __future__ import annotations

import copy
import json
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - used on developer machines without TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback so the workflow can be tested outside TestMaster."""


from .macro_alignment_simulator import simulate_macro_alignment
from .sequence_geometry_memory import solve_sequence_geometry


SCHEMA_VERSION = 1
MEMORY_ACTION = "v5_sequence_capture_memory"
DEFAULT_STANDARD_POSITIONS_PATH = Path(__file__).resolve().parents[3] / "Standard position images" / "v4" / "standard_positions.json"
DEFAULT_MEASUREMENT_PLAN_PATH = Path(__file__).resolve().parents[1] / "measurement_plan.json"
DEFAULT_PHYSICAL_CONSTANTS_UM = {
    "laser_rectangle_short_edge_um": 500.0,
    "ball_diameter_um": 500.0,
    "trench_depth_um": 300.0,
}
DEFAULT_TARGETS = ["ball_1", "ball_2"]
GROSS_CAPTURE_BY_TARGET = {
    "ball_1": "2.1.1",
    "ball_2": "4.1.1",
}

JsonDict = dict[str, Any]


class VisionSequenceMemoryWorkflowStep(TMPythonStatementJ):
    """TMPython entrypoint for read-only capture-memory operations."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return run_sequence_memory_workflow(params_in)
        except Exception as exc:  # fail closed for machine callers
            return abort_response(
                f"VisionSequenceMemoryWorkflowStep failed: {exc}",
                traceback_text=traceback.format_exc(),
            )


def run_sequence_memory_workflow(params_in: JsonDict) -> JsonDict:
    """Run one capture-memory command from a JSON payload.

    Supported commands:

    - ``init``: return a skeleton memory file for the v5 capture sequence;
    - ``record``: add or update one capture record;
    - ``build_sequence_payload``: emit the payload for ``solve_sequence_geometry``;
    - ``build_macro_payload``: emit the payload for ``simulate_macro_alignment``;
    - ``solve_sequence``: solve focused feature memory only;
    - ``solve_macro`` or ``solve``: run the full gross + focused workflow.
    """

    try:
        require_schema(params_in)
        command = str(params_in.get("command") or "solve_macro").strip()
        if command == "init":
            memory = initialize_sequence_memory(params_in)
            write_json_if_requested(memory, params_in.get("memory_path") or params_in.get("output_path"))
            return memory
        if command == "record":
            memory = record_sequence_capture(params_in)
            write_json_if_requested(memory, params_in.get("output_path") or params_in.get("memory_path"))
            return memory

        memory = load_memory_from_params(params_in)
        if command == "build_sequence_payload":
            payload = build_sequence_geometry_payload_from_sequence_memory(memory)
            return {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "sequence_memory_payload_built",
                "payload": payload,
                "sequence_memory_summary": sequence_memory_summary(memory),
            }
        if command == "build_macro_payload":
            payload = build_macro_payload_from_sequence_memory(memory)
            return {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "action": "sequence_memory_macro_payload_built",
                "payload": payload,
                "sequence_memory_summary": sequence_memory_summary(memory),
            }
        if command == "solve_sequence":
            result = solve_sequence_geometry_from_sequence_memory(memory)
            write_json_if_requested(result, params_in.get("output_path"))
            return result
        if command in {"solve", "solve_macro"}:
            result = solve_macro_alignment_from_sequence_memory(memory)
            write_json_if_requested(result, params_in.get("output_path"))
            return result
        raise ValueError(
            "command must be init, record, build_sequence_payload, build_macro_payload, solve_sequence, or solve_macro"
        )
    except Exception as exc:
        return abort_response(str(exc))


def initialize_sequence_memory(params_in: JsonDict | None = None) -> JsonDict:
    """Return a skeleton memory file for the v5 measurement sequence."""

    params = as_dict(params_in)
    require_schema(params)
    planned_records = planned_capture_records(params)
    provided_records = as_dict(params.get("capture_records"))
    for capture_id, raw_record in provided_records.items():
        record = planned_records.setdefault(str(capture_id), {"capture_id": str(capture_id)})
        record.update(deepcopy_json(as_dict(raw_record)))

    memory: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": MEMORY_ACTION,
        "status": "initialized v5 sequence capture memory",
        "standard_positions_path": str(params.get("standard_positions_path") or DEFAULT_STANDARD_POSITIONS_PATH),
        "physical_constants_um": physical_constants_payload(params),
        "targets": targets_payload(params.get("targets")),
        "machine_y_source": str(params.get("machine_y_source") or "trench_model"),
        "auto_detect_gross_sessions": bool(params.get("auto_detect_gross_sessions", False)),
        "auto_detect_missing_sessions": bool(params.get("auto_detect_missing_sessions", False)),
        "auto_detect_side_references": bool(params.get("auto_detect_side_references", False)),
        "capture_records": planned_records,
        "official_baselines": deepcopy_json(as_dict(params.get("official_baselines"))),
        "created_at_utc": utc_now_text(),
        "updated_at_utc": utc_now_text(),
    }
    for key in (
        "standard_positions",
        "baseline_dir",
        "view_mappings",
        "top_camera_lateral_tolerance_um",
        "standard_image_paths",
        "auto_feature_specs",
        "gross_auto_feature_specs",
        "default_close_position_bias",
        "gross_view_mapping",
        "bias_mappings",
    ):
        if key in params:
            memory[key] = deepcopy_json(params[key])
    return memory


def record_sequence_capture(params_in: JsonDict) -> JsonDict:
    """Add or update one capture record in an inline or on-disk memory file."""

    require_schema(params_in)
    memory = load_memory_from_params(params_in, allow_init=True)
    require_memory(memory)
    capture_id = required_text(params_in, "capture_id")
    records = as_dict(memory.setdefault("capture_records", {}))
    record = deepcopy_json(as_dict(records.get(capture_id)))
    if not record:
        record = capture_record_template(capture_id, params_in)
    record["capture_id"] = capture_id

    for key in ("target", "view", "position_id", "label", "result_use", "role", "image_path", "review_status"):
        if key in params_in:
            record[key] = deepcopy_json(params_in[key])
    if "machine_positions_um" in params_in:
        record["machine_positions_um"] = deepcopy_json(as_dict(params_in["machine_positions_um"]))

    session = session_from_params(params_in)
    if session:
        side_reference = params_in.get("side_reference_line")
        if isinstance(side_reference, dict):
            session = deepcopy_json(session)
            session["side_reference_line"] = deepcopy_json(side_reference)
        record["session"] = session
    if "session_path" in params_in:
        record["session_path"] = str(params_in["session_path"])
    if "image_path" in params_in:
        record["image_path"] = str(params_in["image_path"])

    if not record.get("review_status"):
        record["review_status"] = "recorded"
    record["updated_at_utc"] = utc_now_text()
    records[capture_id] = record
    memory["capture_records"] = records
    memory["updated_at_utc"] = utc_now_text()

    if bool(params_in.get("official_baseline")) or bool(params_in.get("is_official_baseline")):
        baseline = official_baseline_payload(record)
        if not baseline:
            raise ValueError("official_baseline capture must include a session or image_path")
        baselines = as_dict(memory.setdefault("official_baselines", {}))
        baselines[capture_id] = baseline
        memory["official_baselines"] = baselines

    memory["ok"] = True
    memory["action"] = MEMORY_ACTION
    memory["status"] = f"recorded capture {capture_id}"
    return memory


def build_sequence_geometry_payload_from_sequence_memory(memory: JsonDict) -> JsonDict:
    """Convert sequence memory into a ``solve_sequence_geometry`` payload."""

    require_memory(memory)
    payload = common_solver_payload(memory)
    sessions = sessions_from_memory(memory)
    if sessions:
        payload["sessions"] = sessions
    positions = standard_positions_from_memory(memory)
    if positions is not None:
        payload["standard_positions"] = positions
    return payload


def build_macro_payload_from_sequence_memory(memory: JsonDict) -> JsonDict:
    """Convert sequence memory into a full macro simulator payload."""

    require_memory(memory)
    payload = common_solver_payload(memory)
    sessions = sessions_from_memory(memory)
    if sessions:
        payload["sessions"] = sessions
    positions = standard_positions_from_memory(memory)
    if positions is not None:
        payload["standard_positions"] = positions
    gross_observations = gross_observations_from_memory(memory)
    if gross_observations:
        payload["gross_observations"] = gross_observations
    return payload


def solve_sequence_geometry_from_sequence_memory(memory: JsonDict) -> JsonDict:
    """Solve focused capture memory and attach a compact memory summary."""

    try:
        payload = build_sequence_geometry_payload_from_sequence_memory(memory)
        result = solve_sequence_geometry(payload)
        result["sequence_memory_summary"] = sequence_memory_summary(memory)
        return result
    except Exception as exc:
        return abort_response(str(exc))


def solve_macro_alignment_from_sequence_memory(memory: JsonDict) -> JsonDict:
    """Solve the full gross + focused macro workflow from sequence memory."""

    try:
        payload = build_macro_payload_from_sequence_memory(memory)
        result = simulate_macro_alignment(payload)
        result["sequence_memory_summary"] = sequence_memory_summary(memory)
        return result
    except Exception as exc:
        return abort_response(str(exc))


def common_solver_payload(memory: JsonDict) -> JsonDict:
    payload: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "standard_positions_path": str(memory.get("standard_positions_path") or DEFAULT_STANDARD_POSITIONS_PATH),
        "physical_constants_um": physical_constants_payload(memory),
        "targets": targets_payload(memory.get("targets")),
        "machine_y_source": str(memory.get("machine_y_source") or "trench_model"),
        "auto_detect_gross_sessions": bool(memory.get("auto_detect_gross_sessions", False)),
        "auto_detect_missing_sessions": bool(memory.get("auto_detect_missing_sessions", False)),
        "auto_detect_side_references": bool(memory.get("auto_detect_side_references", False)),
    }
    for key in (
        "baseline_dir",
        "view_mappings",
        "top_camera_lateral_tolerance_um",
        "standard_image_paths",
        "auto_feature_specs",
        "gross_auto_feature_specs",
        "default_close_position_bias",
        "gross_view_mapping",
        "bias_mappings",
    ):
        if key in memory:
            payload[key] = deepcopy_json(memory[key])
    image_paths = image_paths_from_memory(memory)
    if image_paths:
        payload.setdefault("standard_image_paths", {}).update(image_paths)
    return payload


def sessions_from_memory(memory: JsonDict) -> dict[str, JsonDict]:
    sessions: dict[str, JsonDict] = {}
    for capture_id, record in capture_records(memory).items():
        session = as_dict(record.get("session"))
        if session:
            sessions[capture_id] = deepcopy_json(session)
    return sessions


def image_paths_from_memory(memory: JsonDict) -> dict[str, str]:
    image_paths: dict[str, str] = {}
    for capture_id, record in capture_records(memory).items():
        image_path = str(record.get("image_path") or "").strip()
        if image_path:
            image_paths[capture_id] = image_path
    return image_paths


def gross_observations_from_memory(memory: JsonDict) -> list[JsonDict]:
    observations: list[JsonDict] = []
    records = capture_records(memory)
    official_baselines = as_dict(memory.get("official_baselines"))
    for target in targets_payload(memory.get("targets")):
        capture_id = GROSS_CAPTURE_BY_TARGET.get(target)
        if not capture_id:
            continue
        record = as_dict(records.get(capture_id))
        baseline = as_dict(official_baselines.get(capture_id))
        observation: JsonDict = {
            "target": target,
            "gross_capture_id": capture_id,
        }
        candidate_session = as_dict(record.get("session"))
        if candidate_session:
            observation["candidate_session"] = deepcopy_json(candidate_session)
        if record.get("image_path"):
            observation["candidate_image_path"] = str(record["image_path"])

        baseline_session = as_dict(baseline.get("session"))
        if baseline_session:
            observation["baseline_session"] = deepcopy_json(baseline_session)
        if baseline.get("image_path"):
            observation["baseline_image_path"] = str(baseline["image_path"])

        if "um_per_pixel" in record:
            observation["um_per_pixel"] = record["um_per_pixel"]
        elif "pixels_per_um" in record:
            observation["pixels_per_um"] = record["pixels_per_um"]
        else:
            observation["estimate_um_per_pixel_from_ball_diameter"] = True
            observation["ball_diameter_um"] = physical_constants_payload(memory)["ball_diameter_um"]
        observations.append(observation)
    return observations


def standard_positions_from_memory(memory: JsonDict) -> JsonDict | None:
    """Return standard positions with any live queried positions overlaid."""

    base = load_standard_positions_payload(memory)
    records = capture_records(memory)
    if base is None and not any(as_dict(record.get("machine_positions_um")) for record in records.values()):
        return None
    payload = deepcopy_json(base or {"positions": []})
    raw_positions = payload.setdefault("positions", [])
    if not isinstance(raw_positions, list):
        raise ValueError("standard_positions.positions must be a list")
    positions_by_id: dict[str, JsonDict] = {}
    for raw_position in raw_positions:
        position = as_dict(raw_position)
        position_id = str(position.get("id") or "").strip()
        if position_id:
            positions_by_id[position_id] = position

    for capture_id, record in records.items():
        machine_positions = as_dict(record.get("machine_positions_um"))
        if not machine_positions:
            continue
        position_id = str(record.get("position_id") or position_id_from_capture_id(capture_id))
        position = deepcopy_json(as_dict(positions_by_id.get(position_id)))
        if not position:
            position = {"id": position_id, "captured_images": [default_capture_image_name(capture_id)]}
        captured_images = position.setdefault("captured_images", [])
        if isinstance(captured_images, list):
            capture_name = str(record.get("image_path") or default_capture_image_name(capture_id))
            if not any(Path(str(value).replace("\\", "/")).stem == capture_id for value in captured_images):
                captured_images.append(capture_name)
        position["machine_positions_um"] = merged_machine_positions(
            as_dict(position.get("machine_positions_um")),
            machine_positions,
        )
        positions_by_id[position_id] = position

    payload["positions"] = list(positions_by_id.values())
    return payload


def load_standard_positions_payload(memory: JsonDict) -> JsonDict | None:
    if isinstance(memory.get("standard_positions"), dict):
        return deepcopy_json(memory["standard_positions"])
    path_value = memory.get("standard_positions_path")
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def sequence_memory_summary(memory: JsonDict) -> JsonDict:
    records = capture_records(memory)
    return {
        "capture_count": len(records),
        "recorded_capture_ids": sorted(
            capture_id
            for capture_id, record in records.items()
            if as_dict(record.get("session")) or record.get("image_path") or as_dict(record.get("machine_positions_um"))
        ),
        "missing_session_capture_ids": sorted(
            capture_id
            for capture_id, record in records.items()
            if not as_dict(record.get("session"))
        ),
        "machine_position_capture_ids": sorted(
            capture_id
            for capture_id, record in records.items()
            if as_dict(record.get("machine_positions_um"))
        ),
        "targets": targets_payload(memory.get("targets")),
        "auto_detect_gross_sessions": bool(memory.get("auto_detect_gross_sessions", False)),
        "auto_detect_missing_sessions": bool(memory.get("auto_detect_missing_sessions", False)),
        "auto_detect_side_references": bool(memory.get("auto_detect_side_references", False)),
        "machine_y_source": str(memory.get("machine_y_source") or "trench_model"),
    }


def planned_capture_records(params_in: JsonDict) -> dict[str, JsonDict]:
    records: dict[str, JsonDict] = {}
    plan = load_measurement_plan(params_in)
    for raw_step in plan.get("steps") or ():
        step = as_dict(raw_step)
        capture_id = str(step.get("capture_id") or "").strip()
        if not capture_id:
            continue
        records[capture_id] = {
            "capture_id": capture_id,
            "position_id": str(step.get("position_id") or position_id_from_capture_id(capture_id)),
            "target": str(step.get("target") or ""),
            "view": str(step.get("view") or ""),
            "label": str(step.get("label") or ""),
            "result_use": str(step.get("result_use") or ""),
            "sequence_order": step.get("order"),
            "review_status": "pending",
        }
        if isinstance(step.get("close_position_ids"), list):
            records[capture_id]["close_position_ids"] = deepcopy_json(step["close_position_ids"])
    if records:
        return records
    for target, capture_id in GROSS_CAPTURE_BY_TARGET.items():
        records[capture_id] = {
            "capture_id": capture_id,
            "position_id": position_id_from_capture_id(capture_id),
            "target": target,
            "view": "gross_dual",
            "result_use": "coarse_position_bias_only",
            "review_status": "pending",
        }
    return records


def capture_record_template(capture_id: str, params_in: JsonDict) -> JsonDict:
    for record in planned_capture_records(params_in).values():
        if record.get("capture_id") == capture_id:
            return deepcopy_json(record)
    return {
        "capture_id": capture_id,
        "position_id": position_id_from_capture_id(capture_id),
        "review_status": "pending",
    }


def load_measurement_plan(params_in: JsonDict) -> JsonDict:
    if isinstance(params_in.get("measurement_plan"), dict):
        return as_dict(params_in["measurement_plan"])
    path_value = params_in.get("measurement_plan_path") or DEFAULT_MEASUREMENT_PLAN_PATH
    path = Path(str(path_value))
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_memory_from_params(params_in: JsonDict, *, allow_init: bool = False) -> JsonDict:
    raw_memory = params_in.get("memory")
    if isinstance(raw_memory, dict):
        return deepcopy_json(raw_memory)
    memory_path = params_in.get("memory_path")
    if memory_path:
        path = Path(str(memory_path))
        if not path.is_absolute():
            path = Path.cwd() / path
        return json.loads(path.read_text(encoding="utf-8"))
    if allow_init:
        return initialize_sequence_memory(params_in)
    raise ValueError("memory or memory_path is required")


def session_from_params(params_in: JsonDict) -> JsonDict:
    if isinstance(params_in.get("session"), dict):
        return deepcopy_json(params_in["session"])
    if isinstance(params_in.get("vision_session"), dict):
        return deepcopy_json(params_in["vision_session"])
    session_path = params_in.get("session_path")
    if session_path:
        path = Path(str(session_path))
        if not path.is_absolute():
            path = Path.cwd() / path
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def official_baseline_payload(record: JsonDict) -> JsonDict:
    payload: JsonDict = {}
    session = as_dict(record.get("session"))
    if session:
        payload["session"] = deepcopy_json(session)
    if record.get("image_path"):
        payload["image_path"] = str(record["image_path"])
    payload["capture_id"] = record.get("capture_id")
    payload["updated_at_utc"] = utc_now_text()
    return payload


def merged_machine_positions(base: JsonDict, overlay: JsonDict) -> JsonDict:
    merged = deepcopy_json(base)
    for stage, raw_axes in overlay.items():
        axes = as_dict(raw_axes)
        if axes and isinstance(merged.get(stage), dict):
            stage_payload = deepcopy_json(as_dict(merged[stage]))
            stage_payload.update(deepcopy_json(axes))
            merged[stage] = stage_payload
        else:
            merged[stage] = deepcopy_json(raw_axes)
    return merged


def capture_records(memory: JsonDict) -> dict[str, JsonDict]:
    return {
        str(capture_id): as_dict(record)
        for capture_id, record in as_dict(memory.get("capture_records")).items()
    }


def targets_payload(raw_targets: Any) -> list[str]:
    if raw_targets is None:
        return list(DEFAULT_TARGETS)
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("targets must be a non-empty list when provided")
    targets = [str(target).strip() for target in raw_targets]
    invalid = sorted(target for target in targets if target not in DEFAULT_TARGETS)
    if invalid:
        raise ValueError(f"targets must contain only ball_1 or ball_2, got {', '.join(invalid)}")
    return targets


def physical_constants_payload(source: JsonDict) -> JsonDict:
    constants = as_dict(source.get("physical_constants_um"))
    payload = dict(DEFAULT_PHYSICAL_CONSTANTS_UM)
    payload.update(deepcopy_json(constants))
    return payload


def position_id_from_capture_id(capture_id: str) -> str:
    parts = capture_id.split(".")
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return capture_id


def default_capture_image_name(capture_id: str) -> str:
    return f"{capture_id}.PNG"


def write_json_if_requested(payload: JsonDict, raw_path: Any) -> None:
    if not raw_path:
        return
    path = Path(str(raw_path))
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def require_memory(memory: JsonDict) -> None:
    require_schema(memory)
    action = str(memory.get("action") or "").strip()
    if action != MEMORY_ACTION:
        raise ValueError(f"memory action must be {MEMORY_ACTION}")


def require_schema(params_in: JsonDict) -> None:
    version = params_in.get("schema_version")
    if int(version or 0) != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}")


def required_text(mapping: JsonDict, key: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def deepcopy_json(value: Any) -> Any:
    return copy.deepcopy(value)


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def abort_response(message: str, *, traceback_text: str | None = None) -> JsonDict:
    response: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "action": "abort",
        "status": message,
    }
    if traceback_text:
        response["traceback"] = traceback_text
    return response


def _parse_args(argv: Sequence[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Manage and solve v5 vision sequence memory.")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Create a sequence-memory skeleton.")
    init_parser.add_argument("--output", dest="output_path", help="Optional output JSON path.")
    init_parser.add_argument("--standard-positions", dest="standard_positions_path", default=str(DEFAULT_STANDARD_POSITIONS_PATH))
    init_parser.add_argument("--allow-standard-auto", action="store_true", help="Enable auto detection from standard images.")

    record_parser = subparsers.add_parser("record", help="Record one capture into a memory file.")
    record_parser.add_argument("memory_path", help="Memory JSON path to read/update.")
    record_parser.add_argument("--capture-id", required=True)
    record_parser.add_argument("--session", dest="session_path")
    record_parser.add_argument("--image", dest="image_path")
    record_parser.add_argument("--official-baseline", action="store_true")
    record_parser.add_argument("--output", dest="output_path")

    solve_parser = subparsers.add_parser("solve", help="Solve full macro alignment from memory.")
    solve_parser.add_argument("memory_path")
    solve_parser.add_argument("--output", dest="output_path")
    solve_parser.add_argument("--sequence-only", action="store_true")

    payload_parser = subparsers.add_parser("payload", help="Build a solver payload from memory.")
    payload_parser.add_argument("memory_path")
    payload_parser.add_argument("--macro", action="store_true")

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> JsonDict:
    import sys

    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] not in {"init", "record", "solve", "payload", "-h", "--help"}:
        payload = json.loads(Path(raw_args[0]).read_text(encoding="utf-8"))
        result = run_sequence_memory_workflow(payload)
        print(json.dumps(result, indent=2, sort_keys=True))
        return result

    args = _parse_args(raw_args)
    if args.command == "init":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "command": "init",
            "standard_positions_path": args.standard_positions_path,
            "auto_detect_gross_sessions": bool(args.allow_standard_auto),
            "auto_detect_missing_sessions": bool(args.allow_standard_auto),
            "auto_detect_side_references": bool(args.allow_standard_auto),
            "output_path": args.output_path,
        }
    elif args.command == "record":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "command": "record",
            "memory_path": args.memory_path,
            "capture_id": args.capture_id,
            "session_path": args.session_path,
            "image_path": args.image_path,
            "official_baseline": bool(args.official_baseline),
            "output_path": args.output_path,
        }
    elif args.command == "solve":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "command": "solve_sequence" if args.sequence_only else "solve_macro",
            "memory_path": args.memory_path,
            "output_path": args.output_path,
        }
    elif args.command == "payload":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "command": "build_macro_payload" if args.macro else "build_sequence_payload",
            "memory_path": args.memory_path,
        }
    else:
        raise SystemExit("provide a subcommand or workflow JSON payload")
    result = run_sequence_memory_workflow(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":  # pragma: no cover - manual CLI helper
    main()
