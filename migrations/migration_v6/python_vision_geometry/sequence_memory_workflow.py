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
import math
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
from .position_bias_planner import plan_biased_close_positions
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
YASE_STAGE_FOR_MACHINE_AXIS = {
    ("camera", "x"): "Camera_X",
    ("camera", "y"): "Camera_Y",
    ("camera", "z"): "Camera_Z",
    ("tower_1", "x"): "Align_X1",
    ("tower_1", "y"): "Align_Y1",
    ("tower_1", "z"): "Align_Z1",
    ("tower_2", "x"): "Align_X2",
    ("tower_2", "y"): "Align_Y2",
    ("tower_2", "z"): "Align_Z2",
}
MACHINE_MOVE_ORDER = [
    ("camera", "x"),
    ("camera", "z"),
    ("camera", "y"),
    ("tower_1", "x"),
    ("tower_1", "z"),
    ("tower_1", "y"),
    ("tower_2", "x"),
    ("tower_2", "z"),
    ("tower_2", "y"),
]
FOCUS_PLANE_BY_RESULT_USE = {
    "reference_focus_registration": "top_laser_reference",
    "machine_x_and_machine_z": "top_ball_focus",
    "machine_y_and_machine_x_consistency": "side_ball_focus",
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


class VisionSequenceReviewRecordStep(TMPythonStatementJ):
    """TMPython entrypoint that opens the operator review UI before recording."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return review_and_record_next_capture(params_in)
        except Exception as exc:  # fail closed for machine callers
            return abort_response(
                f"VisionSequenceReviewRecordStep failed: {exc}",
                traceback_text=traceback.format_exc(),
            )


def run_sequence_memory_workflow(params_in: JsonDict) -> JsonDict:
    """Run one capture-memory command from a JSON payload.

    Supported commands:

    - ``init``: return a skeleton memory file for the v5 capture sequence;
    - ``record``: add or update one capture record;
    - ``next_action``: report the next capture/baseline/solve step;
    - ``next_motion_or_capture``: return the next flat guarded YASE action;
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
        if command in {"review_and_record_next_capture", "review_record", "capture_review_record"}:
            return review_and_record_next_capture(params_in)

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
        if command in {"next", "next_action", "status"}:
            result = next_sequence_action_from_sequence_memory(memory)
            write_json_if_requested(result, params_in.get("output_path"))
            return result
        if command in {"next_motion_or_capture", "final_next_step"}:
            result = next_motion_or_capture_step(memory, params_in)
            write_json_if_requested(result, params_in.get("output_path"))
            return result
        if command == "solve_sequence":
            result = solve_sequence_geometry_from_sequence_memory(memory)
            write_json_if_requested(result, params_in.get("output_path"))
            return result
        if command in {"solve", "solve_macro"}:
            result = solve_macro_alignment_from_sequence_memory(memory)
            write_json_if_requested(result, params_in.get("output_path"))
            return result
        raise ValueError(
            "command must be init, record, review_and_record_next_capture, next_action, build_sequence_payload, "
            "build_macro_payload, next_motion_or_capture, solve_sequence, or solve_macro"
        )
    except Exception as exc:
        return abort_response(str(exc))


def next_motion_or_capture_step(memory: JsonDict, params_in: JsonDict) -> JsonDict:
    """Return the next flat YASE action for a final guarded workflow."""

    require_memory(memory)
    current_positions = required_machine_positions_payload(params_in)
    max_single_move_um = finite_float(params_in.get("max_single_move_um", 200000.0), "max_single_move_um")
    move_tolerance_um = finite_float(params_in.get("move_tolerance_um", 0.1), "move_tolerance_um")
    next_action = next_sequence_action_from_sequence_memory(memory)
    action = str(next_action.get("action") or "").strip()
    if action == "solve_ready":
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "solve_ready",
            "status": "all reviewed captures are recorded; run SUB_V5MacroAlignmentSolve_ReadOnly.xseq",
            "next_sequence": "SUB_V5MacroAlignmentSolve_ReadOnly.xseq",
            "stage1": "",
            "target1_um": 0.0,
            "distance1_um": 0.0,
            "delta1_um": 0.0,
            "move_mode1": "Absolute",
            "next_action": next_action,
            "machine_coordinate_output": "machine_coordinates_um",
        }
    if action not in {"capture_required", "official_baseline_required"}:
        return abort_response(f"cannot plan final workflow step from next action {action or 'unknown'}")

    capture = as_dict(next_action.get("next_capture"))
    target_positions = as_dict(capture.get("machine_positions_um"))
    move = first_required_machine_move(
        current_positions,
        target_positions,
        max_single_move_um=max_single_move_um,
        move_tolerance_um=move_tolerance_um,
    )
    if move.get("ok") is False:
        return move
    if move:
        move.update(
            {
                "schema_version": SCHEMA_VERSION,
                "action": "move_to_next_capture",
                "status": (
                    f"move {move['stage1']} to {move['target1_um']:.6g} um before capture "
                    f"{capture.get('capture_id')}"
                ),
                "capture_id": capture.get("capture_id"),
                "position_id": capture.get("position_id"),
                "next_capture": capture,
                "next_action": next_action,
                "next_sequence_after_move": "rerun next_motion_or_capture",
            }
        )
        return move

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": "capture_review_record_required",
        "status": (
            f"machine is at capture target for {capture.get('capture_id')}; "
            "run SUB_V5CaptureReviewRecord_ReadOnly.xseq"
        ),
        "capture_id": capture.get("capture_id"),
        "position_id": capture.get("position_id"),
        "official_baseline": bool(capture.get("official_baseline")),
        "next_capture": capture,
        "next_action": next_action,
        "next_sequence": "SUB_V5CaptureReviewRecord_ReadOnly.xseq",
        "stage1": "",
        "target1_um": 0.0,
        "distance1_um": 0.0,
        "delta1_um": 0.0,
        "move_mode1": "Absolute",
        "machine_coordinate_output": "machine_coordinates_um",
    }


def first_required_machine_move(
    current_positions: JsonDict,
    target_positions: JsonDict,
    *,
    max_single_move_um: float,
    move_tolerance_um: float,
) -> JsonDict:
    for stage_name, axis in MACHINE_MOVE_ORDER:
        target_value = axis_value_if_present(target_positions, stage_name, axis)
        if target_value is None:
            continue
        current_value = axis_value_if_present(current_positions, stage_name, axis)
        if current_value is None:
            raise ValueError(f"current machine position is missing {stage_name}.{axis}")
        delta_um = target_value - current_value
        if abs(delta_um) <= move_tolerance_um:
            continue
        if abs(delta_um) > max_single_move_um:
            return abort_response(
                f"planned {stage_name}.{axis} move delta {delta_um:.6g} um exceeds max_single_move_um "
                f"{max_single_move_um:.6g}"
            )
        yase_stage = YASE_STAGE_FOR_MACHINE_AXIS[(stage_name, axis)]
        return {
            "ok": True,
            "stage1": yase_stage,
            "target1_um": target_value,
            "distance1_um": target_value,
            "delta1_um": delta_um,
            "move_mode1": "Absolute",
            "move_count": 1,
            "confirm_text1": (
                f"V5 macro alignment capture move: confirm {yase_stage} absolute target "
                f"{target_value:.6g} um from current {current_value:.6g} um (delta {delta_um:.6g} um)."
            ),
        }
    return {}


def axis_value_if_present(machine_positions: JsonDict, stage_name: str, axis: str) -> float | None:
    raw_value = as_dict(machine_positions.get(stage_name)).get(axis)
    if raw_value is None:
        return None
    return finite_float(raw_value, f"machine_positions_um.{stage_name}.{axis}")


def review_and_record_next_capture(params_in: JsonDict) -> JsonDict:
    """Open the vision UI for the next capture and record the reviewed result.

    This is the machine-facing bridge for live captures: YASE only grabs an
    image, writes it to disk, queries the current stage positions, and calls
    this TMPython class. Python decides which capture is next, opens the
    Tkinter review UI, then records only the operator-approved selection.
    """

    require_schema(params_in)
    image_path = required_text(params_in, "image_path")
    machine_positions = required_machine_positions_payload(params_in)
    memory = load_memory_from_params(params_in)
    require_memory(memory)

    next_action = next_sequence_action_from_sequence_memory(memory)
    action = str(next_action.get("action") or "").strip()
    if action not in {"capture_required", "official_baseline_required"}:
        return abort_response(
            f"review UI was not opened because next sequence action is {action or 'unknown'}"
        )

    capture = as_dict(next_action.get("next_capture"))
    capture_id = required_text(capture, "capture_id")
    official_baseline = action == "official_baseline_required" or bool(capture.get("official_baseline"))
    review_session = open_vision_review_ui(
        image_path,
        roi_output_path=params_in.get("roi_output_path"),
        result_output_path=params_in.get("review_session_output_path") or params_in.get("vision_session_output_path"),
    )
    if not reviewed_session_has_selected_shapes(review_session):
        return abort_response(
            f"operator review for {capture_id} saved no selected shapes; v5 sequence memory was not updated"
        )

    record_payload: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "memory": memory,
        "capture_id": capture_id,
        "position_id": capture.get("position_id"),
        "target": capture.get("target"),
        "view": capture.get("view"),
        "result_use": capture.get("result_use"),
        "label": capture.get("label"),
        "review_status": "official_baseline" if official_baseline else "reviewed",
        "official_baseline": official_baseline,
        "image_path": image_path,
        "session": review_session,
        "machine_positions_um": machine_positions,
    }
    for key in ("purpose", "human_selection", "role", "focus_plane_key", "close_position_ids"):
        if key in capture:
            record_payload[key] = deepcopy_json(capture[key])

    if official_baseline and as_dict(as_dict(memory.get("capture_records")).get(capture_id)).get("session"):
        updated_memory = record_official_baseline_only(memory, record_payload)
    else:
        updated_memory = record_sequence_capture(record_payload)
    memory_output_path = params_in.get("memory_output_path") or params_in.get("memory_path")
    write_json_if_requested(updated_memory, memory_output_path)
    next_after_record = next_sequence_action_from_sequence_memory(updated_memory)
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": "reviewed_capture_recorded",
        "status": f"recorded reviewed capture {capture_id} into v5 sequence memory",
        "capture_id": capture_id,
        "position_id": capture.get("position_id"),
        "target": capture.get("target"),
        "review_status": record_payload["review_status"],
        "official_baseline": official_baseline,
        "image_path": image_path,
        "machine_positions_um": deepcopy_json(machine_positions),
        "review_session": deepcopy_json(review_session),
        "next_action_before_record": next_action,
        "next_action_after_record": next_after_record,
        "sequence_memory_summary": sequence_memory_summary(updated_memory),
        "machine_coordinate_output": "machine_coordinates_um",
    }
    write_json_if_requested(result, params_in.get("result_output_path") or params_in.get("output_path"))
    return result


def record_official_baseline_only(memory: JsonDict, record_payload: JsonDict) -> JsonDict:
    """Save an official gross baseline without overwriting a live candidate."""

    updated = deepcopy_json(memory)
    baseline = official_baseline_payload(record_payload)
    if not baseline:
        raise ValueError("official_baseline capture must include a session or image_path")
    baselines = as_dict(updated.setdefault("official_baselines", {}))
    baselines[str(record_payload["capture_id"])] = baseline
    updated["official_baselines"] = baselines
    updated["updated_at_utc"] = utc_now_text()
    updated["ok"] = True
    updated["action"] = MEMORY_ACTION
    updated["status"] = f"recorded official baseline for capture {record_payload['capture_id']}"
    updated["focus_plane_memory"] = build_focus_plane_memory(updated)
    return updated


def open_vision_review_ui(
    image_path: str,
    *,
    roi_output_path: Any = None,
    result_output_path: Any = None,
) -> JsonDict:
    """Open the existing Tkinter vision lab and return its reviewed payload."""

    try:
        from vision_recognition_lab import run_vision_recognition_lab_session
    except Exception as first_exc:
        import sys

        repo_lab_dir = Path(__file__).resolve().parents[3] / "vision recognition lab"
        if repo_lab_dir.is_dir() and str(repo_lab_dir) not in sys.path:
            sys.path.insert(0, str(repo_lab_dir))
        try:
            from vision_recognition_lab import run_vision_recognition_lab_session
        except Exception as second_exc:  # pragma: no cover - environment dependent
            raise ImportError(
                "could not import vision_recognition_lab.run_vision_recognition_lab_session; "
                "copy vision_recognition_lab.py into the TMPython working directory or keep the repo lab path available"
            ) from (second_exc or first_exc)

    return as_dict(
        run_vision_recognition_lab_session(
            image_path,
            roi_output_path=roi_output_path,
            result_output_path=result_output_path,
        )
    )


def reviewed_session_has_selected_shapes(session: JsonDict) -> bool:
    for selections in as_dict(session.get("selected_recognition")).values():
        if isinstance(selections, list) and selections:
            return True
    return False


def required_machine_positions_payload(params_in: JsonDict) -> JsonDict:
    machine_positions = as_dict(params_in.get("machine_positions_um"))
    if not machine_positions:
        machine_positions = machine_positions_from_flat_params(params_in)
    if not machine_positions:
        raise ValueError("machine_positions_um is required for review_and_record_next_capture")
    for stage_name, raw_axes in machine_positions.items():
        axes = as_dict(raw_axes)
        if not axes:
            raise ValueError(f"machine_positions_um.{stage_name} must contain axis values")
        for axis, value in axes.items():
            axes[axis] = finite_float(value, f"machine_positions_um.{stage_name}.{axis}")
    return deepcopy_json(machine_positions)


def machine_positions_from_flat_params(params_in: JsonDict) -> JsonDict:
    machine_positions: JsonDict = {}
    for stage_name in ("camera", "tower_1", "tower_2"):
        axes: JsonDict = {}
        for axis in ("x", "y", "z"):
            for key in (
                f"{stage_name}_{axis}_um",
                f"{stage_name}.{axis}",
                f"{stage_name}_{axis}",
            ):
                if key in params_in and params_in[key] is not None:
                    axes[axis] = params_in[key]
                    break
        if axes:
            machine_positions[stage_name] = axes
    return machine_positions


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
        "apply_remembered_focus_planes": bool(params.get("apply_remembered_focus_planes", False)),
        "capture_records": planned_records,
        "official_baselines": deepcopy_json(as_dict(params.get("official_baselines"))),
        "focus_plane_memory": {},
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
    memory["focus_plane_memory"] = build_focus_plane_memory(memory)
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

    if not record.get("review_status") or str(record.get("review_status")) == "pending":
        record["review_status"] = "recorded"
    record["updated_at_utc"] = utc_now_text()
    records[capture_id] = record
    memory["capture_records"] = records
    memory["updated_at_utc"] = utc_now_text()
    if "apply_remembered_focus_planes" in params_in:
        memory["apply_remembered_focus_planes"] = bool(params_in["apply_remembered_focus_planes"])

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
    memory["focus_plane_memory"] = build_focus_plane_memory(memory)
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


def next_sequence_action_from_sequence_memory(memory: JsonDict) -> JsonDict:
    """Return the next read-only operator action for a v5 capture memory file."""

    try:
        require_memory(memory)
        gross_plans: dict[str, JsonDict] = {}
        for target in targets_payload(memory.get("targets")):
            gross_capture_id = GROSS_CAPTURE_BY_TARGET.get(target)
            if gross_capture_id:
                gross_record = record_for_capture(memory, gross_capture_id)
                if capture_requires_manual_measurement(memory, gross_record, feature_kind="gross"):
                    return capture_required_response(
                        memory,
                        gross_record,
                        target=target,
                        reason="gross capture has no candidate session or image",
                    )
                if gross_official_baseline_required(memory, gross_capture_id):
                    return official_baseline_required_response(memory, gross_record, target=target)

                gross_result = gross_bias_result_for_target(memory, target)
                if not gross_result.get("ok"):
                    return abort_response(
                        f"gross bias planning failed before next capture for {target}: {gross_result.get('status')}"
                    )
                plan = first_plan(gross_result)
                if plan:
                    gross_plans[target] = plan

            for focused_record in focused_records_for_target(memory, target):
                if capture_requires_manual_measurement(memory, focused_record, feature_kind="focused"):
                    return capture_required_response(
                        memory,
                        focused_record,
                        target=target,
                        reason="focused capture has no reviewed session",
                        gross_plan=gross_plans.get(target),
                    )

        return solve_ready_response(memory)
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
        candidate_machine_positions = as_dict(record.get("machine_positions_um"))
        if candidate_machine_positions:
            observation["candidate_machine_positions_um"] = deepcopy_json(candidate_machine_positions)

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
        "focus_plane_keys": sorted(as_dict(memory.get("focus_plane_memory"))),
        "apply_remembered_focus_planes": bool(memory.get("apply_remembered_focus_planes", False)),
    }


def record_for_capture(memory: JsonDict, capture_id: str) -> JsonDict:
    records = planned_and_recorded_capture_records(memory)
    record = as_dict(records.get(capture_id))
    if record:
        return record
    return capture_record_template(capture_id, memory)


def focused_records_for_target(memory: JsonDict, target: str) -> list[JsonDict]:
    records = planned_and_recorded_capture_records(memory)
    filtered = [
        record
        for record in records.values()
        if str(record.get("target") or "").strip() == target
        and str(record.get("result_use") or "").strip() != "coarse_position_bias_only"
    ]
    return sorted(filtered, key=capture_record_sort_key)


def planned_and_recorded_capture_records(memory: JsonDict) -> dict[str, JsonDict]:
    planned = planned_capture_records(memory)
    recorded = capture_records(memory)
    merged: dict[str, JsonDict] = deepcopy_json(planned)
    for capture_id, record in recorded.items():
        base = deepcopy_json(as_dict(merged.get(capture_id)))
        base.update(deepcopy_json(record))
        merged[capture_id] = base
    return merged


def capture_record_sort_key(record: JsonDict) -> tuple[int, str]:
    order = record.get("sequence_order")
    try:
        order_value = int(order)
    except (TypeError, ValueError):
        order_value = 999999
    return (order_value, str(record.get("capture_id") or ""))


def capture_requires_manual_measurement(memory: JsonDict, record: JsonDict, *, feature_kind: str) -> bool:
    if as_dict(record.get("session")):
        return False
    if feature_kind == "gross":
        if str(record.get("image_path") or "").strip():
            return False
        return not bool(memory.get("auto_detect_gross_sessions"))
    if str(record.get("image_path") or "").strip() and bool(memory.get("auto_detect_missing_sessions")):
        return False
    return not bool(memory.get("auto_detect_missing_sessions"))


def gross_official_baseline_required(memory: JsonDict, capture_id: str) -> bool:
    if bool(memory.get("auto_detect_gross_sessions")):
        return False
    baseline = as_dict(as_dict(memory.get("official_baselines")).get(capture_id))
    return not (as_dict(baseline.get("session")) or str(baseline.get("image_path") or "").strip())


def gross_bias_result_for_target(memory: JsonDict, target: str) -> JsonDict:
    observations = [
        observation
        for observation in gross_observations_from_memory(memory)
        if str(observation.get("target") or "").strip() == target
    ]
    if not observations:
        capture_id = GROSS_CAPTURE_BY_TARGET.get(target)
        observations = [{"target": target, "gross_capture_id": capture_id}]
    payload = common_solver_payload(memory)
    payload["gross_observations"] = observations
    return plan_biased_close_positions(payload)


def first_plan(gross_result: JsonDict) -> JsonDict:
    raw_plans = gross_result.get("plans")
    if isinstance(raw_plans, list) and raw_plans:
        return as_dict(raw_plans[0])
    return {}


def capture_required_response(
    memory: JsonDict,
    record: JsonDict,
    *,
    target: str,
    reason: str,
    gross_plan: JsonDict | None = None,
) -> JsonDict:
    capture = capture_instruction_payload(memory, record, target=target, gross_plan=as_dict(gross_plan))
    response: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": "capture_required",
        "status": f"{capture['capture_id']} is the next required v5 capture for {target}: {reason}",
        "next_capture": capture,
        "machine_coordinate_output": "machine_coordinates_um",
        "sequence_memory_summary": sequence_memory_summary(memory),
    }
    if gross_plan:
        response["gross_bias"] = compact_gross_plan(as_dict(gross_plan))
    return response


def official_baseline_required_response(memory: JsonDict, record: JsonDict, *, target: str) -> JsonDict:
    capture = capture_instruction_payload(memory, record, target=target)
    capture["official_baseline"] = True
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "action": "official_baseline_required",
        "status": (
            f"{capture['capture_id']} needs a reviewed official gross baseline before "
            "close-position bias can be planned"
        ),
        "next_capture": capture,
        "machine_coordinate_output": "machine_coordinates_um",
        "sequence_memory_summary": sequence_memory_summary(memory),
    }


def solve_ready_response(memory: JsonDict) -> JsonDict:
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": "solve_ready",
        "status": "all required v5 capture measurements are available; run solve_macro for machine_coordinates_um",
        "next_command": "solve_macro",
        "machine_coordinate_output": "machine_coordinates_um",
        "sequence_memory_summary": sequence_memory_summary(memory),
    }


def capture_instruction_payload(
    memory: JsonDict,
    record: JsonDict,
    *,
    target: str,
    gross_plan: JsonDict | None = None,
) -> JsonDict:
    capture_id = str(record.get("capture_id") or "").strip()
    position_id = str(record.get("position_id") or position_id_from_capture_id(capture_id))
    position = planned_or_standard_position_for_capture(memory, position_id, gross_plan=as_dict(gross_plan))
    payload: JsonDict = {
        "capture_id": capture_id,
        "position_id": position_id,
        "target": target,
        "view": str(record.get("view") or ""),
        "result_use": str(record.get("result_use") or ""),
        "label": str(record.get("label") or ""),
        "review_status": str(record.get("review_status") or "pending"),
    }
    for key in ("purpose", "human_selection", "role", "close_position_ids"):
        if key in record:
            payload[key] = deepcopy_json(record[key])
    if position:
        payload["machine_positions_um"] = deepcopy_json(as_dict(position.get("machine_positions_um")))
        bias_plan = as_dict(position.get("bias_plan"))
        if as_dict(gross_plan) and str(bias_plan.get("target") or "") == target:
            payload["planned_from_gross_bias"] = True
            payload["bias_plan"] = deepcopy_json(bias_plan)
        focus_guidance = remembered_focus_plane_guidance(memory, record, position)
        if focus_guidance:
            if bool(memory.get("apply_remembered_focus_planes")):
                apply_remembered_focus_plane_to_capture(payload, focus_guidance)
            payload["focus_plane_key"] = focus_guidance["focus_plane_key"]
            payload["remembered_focus_plane"] = focus_guidance
    payload["record_command"] = "record"
    return payload


def planned_or_standard_position_for_capture(
    memory: JsonDict,
    position_id: str,
    *,
    gross_plan: JsonDict | None = None,
) -> JsonDict:
    plan = as_dict(gross_plan)
    for raw_position in plan.get("planned_positions") or ():
        position = as_dict(raw_position)
        if str(position.get("id") or "") == position_id:
            return position

    payload = standard_positions_from_memory(memory)
    for raw_position in as_dict(payload).get("positions") or ():
        position = as_dict(raw_position)
        if str(position.get("id") or "") == position_id:
            return position
    return {}


def compact_gross_plan(plan: JsonDict) -> JsonDict:
    return {
        "target": plan.get("target"),
        "tower": plan.get("tower"),
        "gross_capture_id": plan.get("gross_capture_id"),
        "pixel_shift": plan.get("pixel_shift"),
        "um_per_pixel": plan.get("um_per_pixel"),
        "bias_mapping": plan.get("bias_mapping"),
        "bias_mapping_evidence": plan.get("bias_mapping_evidence"),
        "applied_bias_um": plan.get("applied_bias_um"),
        "bias_clipped": plan.get("bias_clipped"),
        "close_position_ids": plan.get("close_position_ids"),
    }


def build_focus_plane_memory(memory: JsonDict) -> JsonDict:
    focus_planes: dict[str, JsonDict] = {}
    for record in planned_and_recorded_capture_records(memory).values():
        observation = focus_plane_observation_from_record(record)
        if not observation:
            continue
        key = str(observation["focus_plane_key"])
        block = focus_planes.setdefault(
            key,
            {
                "focus_plane_key": key,
                "camera_axis": "camera.y",
                "same_height_focus_rule": "camera y can be reused for later captures of the same feature height",
                "observations": [],
            },
        )
        block["observations"].append(observation)

    for block in focus_planes.values():
        observations = block["observations"]
        observations.sort(key=lambda item: (int(item.get("sequence_order") or 999999), str(item["capture_id"])))
        values = [finite_float(item["camera_y_um"], f"{block['focus_plane_key']}.camera_y_um") for item in observations]
        block["observation_count"] = len(values)
        block["latest_camera_y_um"] = values[-1]
        block["latest_source_capture_id"] = observations[-1]["capture_id"]
        block["mean_camera_y_um"] = float(sum(values) / len(values))
        block["min_camera_y_um"] = float(min(values))
        block["max_camera_y_um"] = float(max(values))
        block["range_camera_y_um"] = float(max(values) - min(values))
    return focus_planes


def focus_plane_observation_from_record(record: JsonDict) -> JsonDict:
    key = focus_plane_key_for_record(record)
    if not key:
        return {}
    camera = as_dict(as_dict(record.get("machine_positions_um")).get("camera"))
    if "y" not in camera or camera.get("y") is None:
        return {}
    return {
        "focus_plane_key": key,
        "capture_id": str(record.get("capture_id") or ""),
        "position_id": str(record.get("position_id") or ""),
        "target": str(record.get("target") or ""),
        "view": str(record.get("view") or ""),
        "result_use": str(record.get("result_use") or ""),
        "sequence_order": record.get("sequence_order"),
        "camera_y_um": finite_float(camera.get("y"), f"{record.get('capture_id')}.camera.y"),
        "camera_position_um": deepcopy_json(camera),
    }


def focus_plane_key_for_record(record: JsonDict) -> str:
    explicit = str(record.get("focus_plane_key") or "").strip()
    if explicit:
        return explicit
    return FOCUS_PLANE_BY_RESULT_USE.get(str(record.get("result_use") or "").strip(), "")


def remembered_focus_plane_guidance(memory: JsonDict, record: JsonDict, position: JsonDict) -> JsonDict:
    key = focus_plane_key_for_record(record)
    if not key:
        return {}
    focus_plane = as_dict(as_dict(memory.get("focus_plane_memory")).get(key))
    if not focus_plane:
        return {}
    machine_positions = as_dict(position.get("machine_positions_um"))
    camera = as_dict(machine_positions.get("camera"))
    planned_camera_y = camera.get("y")
    suggested_camera_y = finite_float(focus_plane.get("latest_camera_y_um"), f"{key}.latest_camera_y_um")
    guidance: JsonDict = {
        "focus_plane_key": key,
        "source": "sequence_focus_plane_memory",
        "same_height_focus_rule": focus_plane.get("same_height_focus_rule"),
        "suggested_camera_y_um": suggested_camera_y,
        "source_capture_id": focus_plane.get("latest_source_capture_id"),
        "observation_count": focus_plane.get("observation_count"),
        "mean_camera_y_um": focus_plane.get("mean_camera_y_um"),
        "range_camera_y_um": focus_plane.get("range_camera_y_um"),
        "applied_to_machine_positions": False,
        "operator_review_required": True,
    }
    if planned_camera_y is not None:
        planned = finite_float(planned_camera_y, f"{record.get('capture_id')}.planned_camera.y")
        guidance["planned_camera_y_um"] = planned
        guidance["delta_from_planned_camera_y_um"] = suggested_camera_y - planned
    return guidance


def apply_remembered_focus_plane_to_capture(payload: JsonDict, guidance: JsonDict) -> None:
    if not guidance:
        return
    machine_positions = as_dict(payload.get("machine_positions_um"))
    if not machine_positions:
        return
    camera = deepcopy_json(as_dict(machine_positions.get("camera")))
    if not camera:
        return
    camera["y"] = finite_float(guidance["suggested_camera_y_um"], "remembered_focus_plane.suggested_camera_y_um")
    machine_positions = deepcopy_json(machine_positions)
    machine_positions["camera"] = camera
    payload["machine_positions_um"] = machine_positions
    guidance["applied_to_machine_positions"] = True


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
        for key in ("purpose", "human_selection", "role", "focus_plane_key"):
            if key in step:
                records[capture_id][key] = deepcopy_json(step[key])
        if not records[capture_id].get("focus_plane_key"):
            focus_plane_key = FOCUS_PLANE_BY_RESULT_USE.get(records[capture_id]["result_use"])
            if focus_plane_key:
                records[capture_id]["focus_plane_key"] = focus_plane_key
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


def finite_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


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


def machine_positions_from_cli_args(args: Any) -> JsonDict:
    machine_positions: JsonDict = {}
    for stage_name in ("camera", "tower_1", "tower_2"):
        axes: JsonDict = {}
        for axis in ("x", "y", "z"):
            value = getattr(args, f"{stage_name}_{axis}", None)
            if value is not None:
                axes[axis] = finite_float(value, f"{stage_name}.{axis}")
        if axes:
            machine_positions[stage_name] = axes
    return machine_positions


def _parse_args(argv: Sequence[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Manage and solve v5 vision sequence memory.")
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Create a sequence-memory skeleton.")
    init_parser.add_argument("--output", dest="output_path", help="Optional output JSON path.")
    init_parser.add_argument("--standard-positions", dest="standard_positions_path", default=str(DEFAULT_STANDARD_POSITIONS_PATH))
    init_parser.add_argument("--allow-standard-auto", action="store_true", help="Enable auto detection from standard images.")
    init_parser.add_argument(
        "--apply-remembered-focus-planes",
        action="store_true",
        help="Apply remembered camera-y focus planes to returned next-capture targets.",
    )

    record_parser = subparsers.add_parser("record", help="Record one capture into a memory file.")
    record_parser.add_argument("memory_path", help="Memory JSON path to read/update.")
    record_parser.add_argument("--capture-id", required=True)
    record_parser.add_argument("--session", dest="session_path")
    record_parser.add_argument("--image", dest="image_path")
    record_parser.add_argument("--official-baseline", action="store_true")
    record_parser.add_argument("--output", dest="output_path")
    for stage_name in ("camera", "tower-1", "tower-2"):
        for axis in ("x", "y", "z"):
            record_parser.add_argument(
                f"--{stage_name}-{axis}",
                dest=f"{stage_name.replace('-', '_')}_{axis}",
                type=float,
                help=f"Record {stage_name.replace('-', '_')}.{axis} machine coordinate in um.",
            )

    solve_parser = subparsers.add_parser("solve", help="Solve full macro alignment from memory.")
    solve_parser.add_argument("memory_path")
    solve_parser.add_argument("--output", dest="output_path")
    solve_parser.add_argument("--sequence-only", action="store_true")

    payload_parser = subparsers.add_parser("payload", help="Build a solver payload from memory.")
    payload_parser.add_argument("memory_path")
    payload_parser.add_argument("--macro", action="store_true")

    next_parser = subparsers.add_parser("next", help="Report the next capture, baseline, or solve action.")
    next_parser.add_argument("memory_path")
    next_parser.add_argument("--output", dest="output_path")

    next_motion_parser = subparsers.add_parser(
        "next-motion",
        help="Return the next flat YASE move/capture/solve action from current machine coordinates.",
    )
    next_motion_parser.add_argument("memory_path")
    next_motion_parser.add_argument("--output", dest="output_path")
    next_motion_parser.add_argument("--max-single-move-um", type=float, default=200000.0)
    next_motion_parser.add_argument("--move-tolerance-um", type=float, default=0.1)
    for stage_name in ("camera", "tower-1", "tower-2"):
        for axis in ("x", "y", "z"):
            next_motion_parser.add_argument(
                f"--{stage_name}-{axis}",
                dest=f"{stage_name.replace('-', '_')}_{axis}",
                type=float,
                help=f"Current {stage_name.replace('-', '_')}.{axis} machine coordinate in um.",
            )

    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> JsonDict:
    import sys

    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] not in {"init", "record", "solve", "payload", "next", "next-motion", "-h", "--help"}:
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
            "apply_remembered_focus_planes": bool(args.apply_remembered_focus_planes),
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
        machine_positions = machine_positions_from_cli_args(args)
        if machine_positions:
            payload["machine_positions_um"] = machine_positions
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
    elif args.command == "next":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "command": "next_action",
            "memory_path": args.memory_path,
            "output_path": args.output_path,
        }
    elif args.command == "next-motion":
        payload = {
            "schema_version": SCHEMA_VERSION,
            "command": "next_motion_or_capture",
            "memory_path": args.memory_path,
            "max_single_move_um": args.max_single_move_um,
            "move_tolerance_um": args.move_tolerance_um,
            "output_path": args.output_path,
        }
        machine_positions = machine_positions_from_cli_args(args)
        if machine_positions:
            payload["machine_positions_um"] = machine_positions
    else:
        raise SystemExit("provide a subcommand or workflow JSON payload")
    result = run_sequence_memory_workflow(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":  # pragma: no cover - manual CLI helper
    main()
