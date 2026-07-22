"""Migration v6 reviewed-vision offset workflow.

This module is the motion-planning boundary for v6. It opens no hardware
interfaces and never moves stages directly. YASE captures images, queries
machine positions, calls these TMPython entrypoints, validates the returned
flat fields, asks the operator, and then issues MoveStage.
"""

from __future__ import annotations

import copy
import json
import math
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - used on developer machines without TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback so the workflow can be tested outside TestMaster."""


from .sequence_geometry_memory import (
    selected_circle_feature,
    selected_rectangle_feature,
    selected_side_reference_feature,
)
from .sequence_memory_workflow import open_vision_review_ui


SCHEMA_VERSION = 1
MEMORY_ACTION = "v6_vision_workflow_memory"
DEFAULT_STANDARD_POSITIONS_PATH = (
    Path(__file__).resolve().parents[3] / "Standard position images" / "v4" / "standard_positions.json"
)
DEFAULT_STANDARD_BASELINE_DIR = (
    Path(__file__).resolve().parents[3] / "Standard position images" / "v4" / "vision_baselines"
)
DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM = 500.0
DEFAULT_BALL_DIAMETER_UM = 500.0
DEFAULT_IMAGE_HEIGHT_PX = 1944.0
DEFAULT_MOVE_TOLERANCE_UM = 1.0
DEFAULT_MAX_TRANSITION_MOVE_UM = 200000.0
TRANSITION_STATUSES = {"in_progress", "complete"}

JsonDict = dict[str, Any]


STAGE_FOR_AXIS = {
    ("camera", "x"): "Camera_X",
    ("camera", "y"): "Camera_Y",
    ("camera", "z"): "Camera_Z",
    ("tower_1", "x"): "Align_X1",
    ("tower_1", "y"): "Align_Y1",
    ("tower_1", "z"): "Align_Z1",
    ("tower_2", "x"): "Align_X2",
    ("tower_2", "y"): "Align_Y2",
    ("tower_2", "z"): "Align_Z2",
    ("zoom", "value"): "Zoom",
}
AXIS_FOR_STAGE = {stage: axis for axis, stage in STAGE_FOR_AXIS.items()}
TARGET_TOWER = {"ball_1": "tower_1", "ball_2": "tower_2"}


CAPTURE_SPECS: dict[str, JsonDict] = {
    "2.1.1": {
        "position_id": "2.1",
        "target": "ball_1",
        "view": "gross_dual",
        "result_use": "coarse_offset_correction",
    },
    "2.4.1": {
        "position_id": "2.4",
        "target": "ball_1",
        "view": "top_xz",
        "result_use": "reference_focus_registration",
    },
    "2.5.1": {
        "position_id": "2.5",
        "target": "ball_1",
        "view": "top_xz",
        "result_use": "top_fine_offset_correction",
    },
    "2.6.1": {
        "position_id": "2.6",
        "target": "ball_1",
        "view": "mirror_side_xy",
        "result_use": "side_mirror_y_offset_correction",
    },
    "4.1.1": {
        "position_id": "4.1",
        "target": "ball_2",
        "view": "gross_dual",
        "result_use": "coarse_offset_correction",
    },
    "4.4.1": {
        "position_id": "4.4",
        "target": "ball_2",
        "view": "top_xz",
        "result_use": "reference_focus_registration",
    },
    "4.5.1": {
        "position_id": "4.5",
        "target": "ball_2",
        "view": "top_xz",
        "result_use": "top_fine_offset_correction",
    },
    "4.6.2": {
        "position_id": "4.6.2",
        "target": "ball_2",
        "view": "mirror_side_xy",
        "result_use": "side_mirror_y_offset_correction",
    },
}


OFFSET_SPECS: dict[str, JsonDict] = {
    "2.1.1": {
        "kind": "coarse_top",
        "target": "ball_1",
        "tower": "tower_1",
        "max_correction_um": 350.0,
        "tolerance_um": 2.0,
    },
    "4.1.1": {
        "kind": "coarse_top",
        "target": "ball_2",
        "tower": "tower_2",
        "max_correction_um": 350.0,
        "tolerance_um": 2.0,
    },
    "2.5.1": {
        "kind": "top_fine",
        "target": "ball_1",
        "tower": "tower_1",
        "reference_capture_id": "2.4.1",
        "standard_reference_capture_id": "2.4.1",
        "max_correction_um": 150.0,
        "tolerance_um": 0.75,
    },
    "4.5.1": {
        "kind": "top_fine",
        "target": "ball_2",
        "tower": "tower_2",
        "reference_capture_id": "4.4.1",
        "standard_reference_capture_id": "4.4.1",
        "max_correction_um": 150.0,
        "tolerance_um": 0.75,
    },
    "2.6.1": {
        "kind": "side_mirror_y",
        "target": "ball_1",
        "tower": "tower_1",
        "max_correction_um": 75.0,
        "tolerance_um": 0.75,
        "mirror_flip_y": True,
    },
    "4.6.2": {
        "kind": "side_mirror_y",
        "target": "ball_2",
        "tower": "tower_2",
        "max_correction_um": 75.0,
        "tolerance_um": 0.75,
        "mirror_flip_y": True,
    },
}


TRANSITION_SPECS: dict[str, JsonDict] = {
    "2.1_to_2.4": {"from_position_id": "2.1", "to_position_id": "2.4", "target": "ball_1"},
    "2.4_to_2.5": {"from_position_id": "2.4", "to_position_id": "2.5", "target": "ball_1"},
    "2.5_to_2.6": {"from_position_id": "2.5", "to_position_id": "2.6", "target": "ball_1"},
    "4.1_to_4.4": {"from_position_id": "4.1", "to_position_id": "4.4", "target": "ball_2"},
    "4.4_to_4.5": {"from_position_id": "4.4", "to_position_id": "4.5", "target": "ball_2"},
    "4.5_to_4.6.2": {"from_position_id": "4.5", "to_position_id": "4.6.2", "target": "ball_2"},
}


@dataclass(frozen=True)
class PlannedMove:
    stage: str
    target_um: float
    delta_um: float
    phase: str


class V6VisionWorkflowStep(TMPythonStatementJ):
    """TMPython entrypoint for v6 non-UI commands."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return run_v6_vision_workflow(params_in)
        except Exception as exc:  # fail closed for machine callers
            return abort_response(f"V6VisionWorkflowStep failed: {exc}", traceback_text=traceback.format_exc())


class V6VisionReviewRecordStep(TMPythonStatementJ):
    """TMPython entrypoint that opens the UI and records one fixed capture."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return review_and_record_capture(params_in)
        except Exception as exc:  # fail closed for machine callers
            return abort_response(f"V6VisionReviewRecordStep failed: {exc}", traceback_text=traceback.format_exc())


def run_v6_vision_workflow(params_in: JsonDict) -> JsonDict:
    try:
        require_schema(params_in)
        command = str(params_in.get("command") or "").strip()
        if command == "init":
            memory = initialize_v6_memory(params_in)
            write_json_if_requested(memory, params_in.get("memory_path") or params_in.get("output_path"))
            return memory
        if command in {"record_capture", "review_and_record_capture"}:
            return review_and_record_capture(params_in)
        if command == "next_offset_correction":
            result = next_offset_correction(params_in)
            write_json_if_requested(result, params_in.get("output_path"))
            return result
        if command == "next_transition_move":
            result = next_transition_move(params_in)
            write_json_if_requested(result, params_in.get("output_path"))
            return result
        raise ValueError("command must be init, record_capture, next_offset_correction, or next_transition_move")
    except Exception as exc:
        return abort_response(str(exc))


def initialize_v6_memory(params_in: JsonDict) -> JsonDict:
    standard_positions_path = str(params_in.get("standard_positions_path") or DEFAULT_STANDARD_POSITIONS_PATH)
    standard_baseline_dir = str(params_in.get("standard_baseline_dir") or DEFAULT_STANDARD_BASELINE_DIR)
    memory: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": MEMORY_ACTION,
        "status": "v6 vision workflow memory initialized",
        "standard_positions_path": standard_positions_path,
        "standard_baseline_dir": standard_baseline_dir,
        "capture_records": {},
        "capture_specs": deepcopy_json(CAPTURE_SPECS),
        "updated_at_utc": utc_now_text(),
    }
    return memory


def review_and_record_capture(params_in: JsonDict) -> JsonDict:
    require_schema(params_in)
    capture_id = required_text(params_in, "capture_id")
    if capture_id not in CAPTURE_SPECS:
        raise ValueError(f"unsupported v6 capture_id {capture_id!r}")
    image_path = required_text(params_in, "image_path")
    machine_positions = required_machine_positions_payload(params_in)
    memory = load_or_initialize_memory(params_in)
    review_session = open_vision_review_ui(
        image_path,
        roi_output_path=params_in.get("roi_output_path"),
        result_output_path=params_in.get("review_session_output_path") or params_in.get("vision_session_output_path"),
    )
    if not reviewed_session_has_selected_shapes(review_session):
        return abort_response(
            f"operator review for {capture_id} saved no selected shapes; v6 memory was not updated"
        )

    record = deepcopy_json(CAPTURE_SPECS[capture_id])
    record.update(
        {
            "capture_id": capture_id,
            "review_status": "reviewed",
            "image_path": image_path,
            "session": review_session,
            "machine_positions_um": machine_positions,
            "reviewed_at_utc": utc_now_text(),
        }
    )
    records = as_dict(memory.setdefault("capture_records", {}))
    records[capture_id] = record
    memory["capture_records"] = records
    clear_transition_records_from_position(memory, str(record["position_id"]))
    memory["updated_at_utc"] = utc_now_text()
    memory["ok"] = True
    memory["action"] = MEMORY_ACTION
    memory["status"] = f"recorded reviewed capture {capture_id}"
    write_json_if_requested(memory, params_in.get("memory_output_path") or params_in.get("memory_path"))

    result = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": "reviewed_capture_recorded",
        "status": f"recorded reviewed capture {capture_id} into v6 memory",
        "capture_id": capture_id,
        "position_id": record["position_id"],
        "target": record["target"],
        "view": record["view"],
        "result_use": record["result_use"],
        "machine_positions_um": deepcopy_json(machine_positions),
        "sequence_memory_summary": memory_summary(memory),
    }
    write_json_if_requested(result, params_in.get("result_output_path") or params_in.get("output_path"))
    return result


def next_offset_correction(params_in: JsonDict) -> JsonDict:
    require_schema(params_in)
    capture_id = required_text(params_in, "capture_id")
    spec = as_dict(OFFSET_SPECS.get(capture_id))
    if not spec:
        raise ValueError(f"capture_id {capture_id!r} does not have a v6 offset correction")
    memory = load_memory_from_params(params_in)
    current_positions = required_machine_positions_payload(params_in)
    max_correction_um = positive_float(
        params_in.get("max_correction_um", spec.get("max_correction_um", 100.0)),
        "max_correction_um",
    )
    tolerance_um = non_negative_float(
        params_in.get("correction_tolerance_um", spec.get("tolerance_um", DEFAULT_MOVE_TOLERANCE_UM)),
        "correction_tolerance_um",
    )

    if spec["kind"] == "coarse_top":
        correction = coarse_top_correction(capture_id, spec, memory, params_in)
    elif spec["kind"] == "top_fine":
        correction = top_fine_correction(capture_id, spec, memory, params_in)
    elif spec["kind"] == "side_mirror_y":
        correction = side_mirror_y_correction(capture_id, spec, memory, params_in)
    else:
        raise ValueError(f"unsupported correction kind {spec['kind']!r}")

    planned_moves = moves_from_axis_corrections(
        current_positions,
        tower=str(spec["tower"]),
        axis_deltas_um=as_dict(correction.get("axis_corrections_um")),
        max_correction_um=max_correction_um,
        tolerance_um=tolerance_um,
        phase=f"v6_{spec['kind']}_offset_correction",
    )
    return correction_response(
        capture_id=capture_id,
        spec=spec,
        correction=correction,
        planned_moves=planned_moves,
        tolerance_um=tolerance_um,
        max_correction_um=max_correction_um,
    )


def coarse_top_correction(capture_id: str, spec: JsonDict, memory: JsonDict, params_in: JsonDict) -> JsonDict:
    standard_session = standard_session_for_capture(capture_id, memory, params_in)
    live_session = recorded_session(memory, capture_id)
    standard_ball = selected_circle_feature(
        standard_session,
        f"{capture_id} standard gross ball",
        target=str(spec["target"]),
        role_context="gross_ball",
    )
    live_ball = selected_circle_feature(
        live_session,
        f"{capture_id} live gross ball",
        target=str(spec["target"]),
        role_context="gross_ball",
    )
    um_per_pixel = ball_um_per_pixel(live_ball.radius_px or standard_ball.radius_px)
    pixel_shift = {
        "x": live_ball.center_px["x"] - standard_ball.center_px["x"],
        "y": live_ball.center_px["y"] - standard_ball.center_px["y"],
    }
    axis_corrections = {
        "z": pixel_shift["x"] * um_per_pixel,
        "x": pixel_shift["y"] * um_per_pixel,
    }
    return {
        "source": "live_gross_ball_center_minus_standard_gross_ball_center",
        "standard_ball_center_px": standard_ball.center_px,
        "live_ball_center_px": live_ball.center_px,
        "pixel_shift": pixel_shift,
        "um_per_pixel": um_per_pixel,
        "view_mapping": {
            "image_x": {"tower_axis": "z", "sign": 1.0},
            "image_y": {"tower_axis": "x", "sign": 1.0},
        },
        "axis_corrections_um": axis_corrections,
    }


def top_fine_correction(capture_id: str, spec: JsonDict, memory: JsonDict, params_in: JsonDict) -> JsonDict:
    reference_capture_id = required_text(spec, "reference_capture_id")
    standard_reference_capture_id = required_text(spec, "standard_reference_capture_id")
    standard_reference_session = standard_session_for_capture(standard_reference_capture_id, memory, params_in)
    standard_ball_session = standard_session_for_capture(capture_id, memory, params_in)
    live_reference_session = recorded_session(memory, reference_capture_id)
    live_ball_session = recorded_session(memory, capture_id)

    standard_rectangle = selected_rectangle_feature(
        standard_reference_session,
        DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM,
    )
    live_rectangle = selected_rectangle_feature(live_reference_session, DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM)
    standard_ball = selected_circle_feature(
        standard_ball_session,
        f"{capture_id} standard top ball",
        target=str(spec["target"]),
        role_context="top_ball",
    )
    live_ball = selected_circle_feature(
        live_ball_session,
        f"{capture_id} live top ball",
        target=str(spec["target"]),
        role_context="top_ball",
    )
    standard_delta_px = {
        "x": standard_ball.center_px["x"] - standard_rectangle.center_px["x"],
        "y": standard_ball.center_px["y"] - standard_rectangle.center_px["y"],
    }
    live_delta_px = {
        "x": live_ball.center_px["x"] - live_rectangle.center_px["x"],
        "y": live_ball.center_px["y"] - live_rectangle.center_px["y"],
    }
    residual_px = {
        "x": live_delta_px["x"] - standard_delta_px["x"],
        "y": live_delta_px["y"] - standard_delta_px["y"],
    }
    um_per_pixel = live_rectangle.um_per_pixel
    axis_corrections = {
        "z": residual_px["x"] * um_per_pixel,
        "x": residual_px["y"] * um_per_pixel,
    }
    return {
        "source": "live_ball_to_reference_delta_minus_standard_ball_to_reference_delta",
        "reference_capture_id": reference_capture_id,
        "standard_reference_capture_id": standard_reference_capture_id,
        "standard_reference_center_px": standard_rectangle.center_px,
        "live_reference_center_px": live_rectangle.center_px,
        "standard_ball_center_px": standard_ball.center_px,
        "live_ball_center_px": live_ball.center_px,
        "standard_delta_px": standard_delta_px,
        "live_delta_px": live_delta_px,
        "residual_px": residual_px,
        "um_per_pixel": um_per_pixel,
        "view_mapping": {
            "image_x": {"tower_axis": "z", "sign": 1.0},
            "image_y": {"tower_axis": "x", "sign": 1.0},
        },
        "axis_corrections_um": axis_corrections,
    }


def side_mirror_y_correction(capture_id: str, spec: JsonDict, memory: JsonDict, params_in: JsonDict) -> JsonDict:
    standard_session = standard_session_for_capture(capture_id, memory, params_in)
    live_session = recorded_session(memory, capture_id)
    target = str(spec["target"])
    standard_ball = selected_circle_feature(
        standard_session,
        f"{capture_id} standard side ball",
        target=target,
        role_context="side_ball",
    )
    live_ball = selected_circle_feature(
        live_session,
        f"{capture_id} live side ball",
        target=target,
        role_context="side_ball",
    )
    standard_reference = single_side_reference_feature(standard_session, f"{capture_id} standard side session")
    live_reference = single_side_reference_feature(live_session, f"{capture_id} live side session")

    standard_roi = mirror_roi_for_session(standard_session, params_in, capture_id)
    live_roi = mirror_roi_for_session(live_session, params_in, capture_id)
    ensure_side_feature_inside_mirror_roi(standard_ball.center_px["y"], standard_roi, f"{capture_id} standard ball")
    ensure_side_feature_inside_mirror_roi(standard_reference.y_px, standard_roi, f"{capture_id} standard reference")
    ensure_side_feature_inside_mirror_roi(live_ball.center_px["y"], live_roi, f"{capture_id} live ball")
    ensure_side_feature_inside_mirror_roi(live_reference.y_px, live_roi, f"{capture_id} live reference")
    standard_delta = mirror_ball_reference_delta_px(
        ball_y_px=standard_ball.center_px["y"],
        reference_y_px=standard_reference.y_px,
        mirror_roi=standard_roi,
    )
    live_delta = mirror_ball_reference_delta_px(
        ball_y_px=live_ball.center_px["y"],
        reference_y_px=live_reference.y_px,
        mirror_roi=live_roi,
    )
    residual_px = live_delta["flipped_delta_y_px"] - standard_delta["flipped_delta_y_px"]
    um_per_pixel = ball_um_per_pixel(live_ball.radius_px or standard_ball.radius_px)
    axis_corrections = {"y": residual_px * um_per_pixel}
    return {
        "source": "mirror_flipped_live_ball_to_side_reference_delta_minus_standard_delta",
        "mirror_view": True,
        "mirror_flip_y": True,
        "standard_mirror_transform": standard_delta,
        "live_mirror_transform": live_delta,
        "residual_flipped_y_px": residual_px,
        "um_per_pixel": um_per_pixel,
        "view_mapping": {
            "mirror_image_y_after_flip": {"tower_axis": "y", "sign": 1.0},
            "physical_note": "top of the mirror is treated as trench-bottom/chip-side after flipping",
        },
        "axis_corrections_um": axis_corrections,
    }


def next_transition_move(params_in: JsonDict) -> JsonDict:
    require_schema(params_in)
    transition_id = required_text(params_in, "transition_id")
    spec = as_dict(TRANSITION_SPECS.get(transition_id))
    if not spec:
        raise ValueError(f"unsupported v6 transition_id {transition_id!r}")
    current_positions = required_machine_positions_payload(params_in)
    standard_positions = load_standard_positions(params_in)
    from_position = as_dict(standard_positions.get(required_text(spec, "from_position_id")))
    to_position = as_dict(standard_positions.get(required_text(spec, "to_position_id")))
    if not from_position or not to_position:
        raise ValueError(f"transition {transition_id} could not find both standard positions")
    memory = load_transition_memory_if_available(params_in)
    transition_record = transition_record_for_current_plan(
        transition_id=transition_id,
        spec=spec,
        memory=memory,
        from_position=from_position,
        to_position=to_position,
        current_positions=current_positions,
        params_in=params_in,
    )
    target_positions = as_dict(transition_record["target_positions_um"])
    clearance_y_by_tower = tower_clearance_y_by_tower(params_in, standard_positions)
    max_single_move_um = positive_float(
        params_in.get("max_single_move_um", DEFAULT_MAX_TRANSITION_MOVE_UM),
        "max_single_move_um",
    )
    move_tolerance_um = non_negative_float(
        params_in.get("move_tolerance_um", DEFAULT_MOVE_TOLERANCE_UM),
        "move_tolerance_um",
    )
    move = first_transition_move(
        current_positions,
        target_positions,
        max_single_move_um=max_single_move_um,
        move_tolerance_um=move_tolerance_um,
        clearance_y_by_tower=clearance_y_by_tower,
    )
    if not move:
        transition_record["status"] = "complete"
        transition_record["completed_at_utc"] = utc_now_text()
        transition_record["last_checked_positions_um"] = deepcopy_json(current_positions)
        write_transition_memory_if_requested(memory, params_in)
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "transition_complete",
            "status": f"v6 transition {transition_id} is at the rebased target",
            "transition_id": transition_id,
            "from_position_id": from_position["id"],
            "to_position_id": to_position["id"],
            "target_positions_um": target_positions,
            "transition_anchor_positions_um": deepcopy_json(as_dict(transition_record.get("anchor_positions_um"))),
            "tower_clearance_y_um": clearance_y_by_tower,
            "stage1": "",
            "target1_um": 0.0,
            "distance1_um": 0.0,
            "delta1_um": 0.0,
            "move_mode1": "Absolute",
            "move_count": 0,
        }
    transition_record["status"] = "in_progress"
    transition_record["last_planned_move"] = {
        "stage": move.stage,
        "target_um": move.target_um,
        "delta_um": move.delta_um,
        "phase": move.phase,
        "planned_at_utc": utc_now_text(),
    }
    transition_record["last_checked_positions_um"] = deepcopy_json(current_positions)
    write_transition_memory_if_requested(memory, params_in)
    return flat_move_response(
        action="transition_move",
        status=f"v6 transition {transition_id}: move {move.stage} before continuing",
        moves=[move],
        extra={
            "transition_id": transition_id,
            "from_position_id": from_position["id"],
            "to_position_id": to_position["id"],
            "target_positions_um": target_positions,
            "transition_anchor_positions_um": deepcopy_json(as_dict(transition_record.get("anchor_positions_um"))),
            "tower_clearance_y_um": clearance_y_by_tower,
            "next_sequence_after_move": "rerun next_transition_move",
        },
    )


def correction_response(
    *,
    capture_id: str,
    spec: JsonDict,
    correction: JsonDict,
    planned_moves: list[PlannedMove],
    tolerance_um: float,
    max_correction_um: float,
) -> JsonDict:
    diagnostics = {
        "capture_id": capture_id,
        "target": spec["target"],
        "tower": spec["tower"],
        "correction_kind": spec["kind"],
        "tolerance_um": tolerance_um,
        "max_correction_um": max_correction_um,
        "correction": correction,
        "motion_policy": "operator_confirmed_yase_movestage_only",
    }
    if not planned_moves:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "no_offset_correction_required",
            "status": f"{capture_id} offset residual is within tolerance; no tower move requested",
            "stage1": "",
            "target1_um": 0.0,
            "distance1_um": 0.0,
            "delta1_um": 0.0,
            "move_mode1": "Absolute",
            "move_count": 0,
            "diagnostics": diagnostics,
        }
    return flat_move_response(
        action="offset_correction_move",
        status=f"{capture_id} requires {len(planned_moves)} reviewed offset correction move(s)",
        moves=planned_moves,
        extra={"diagnostics": diagnostics, "capture_id": capture_id},
    )


def moves_from_axis_corrections(
    current_positions: JsonDict,
    *,
    tower: str,
    axis_deltas_um: JsonDict,
    max_correction_um: float,
    tolerance_um: float,
    phase: str,
) -> list[PlannedMove]:
    moves: list[PlannedMove] = []
    for axis, raw_delta in axis_deltas_um.items():
        delta = finite_float(raw_delta, f"axis_corrections_um.{axis}")
        if abs(delta) <= tolerance_um:
            continue
        if abs(delta) > max_correction_um:
            raise ValueError(
                f"{tower}.{axis} correction {delta:.6g} um exceeds max_correction_um {max_correction_um:.6g}"
            )
        current = axis_value(current_positions, tower, axis)
        stage = STAGE_FOR_AXIS[(tower, axis)]
        moves.append(PlannedMove(stage=stage, target_um=current + delta, delta_um=delta, phase=phase))
    return sorted(moves, key=offset_move_sort_key)


def offset_move_sort_key(move: PlannedMove) -> tuple[int, str]:
    stage_axis = AXIS_FOR_STAGE[move.stage]
    stage_name, axis = stage_axis
    if axis == "y" and move.delta_um > 0.0:
        return (0, move.stage)
    if axis in {"x", "z"}:
        return (1, move.stage)
    if axis == "y":
        return (2, move.stage)
    return (3, move.stage)


def first_transition_move(
    current_positions: JsonDict,
    target_positions: JsonDict,
    *,
    max_single_move_um: float,
    move_tolerance_um: float,
    clearance_y_by_tower: JsonDict | None = None,
) -> PlannedMove | None:
    clearance_move = tower_clearance_move_before_lateral_motion(
        current_positions,
        target_positions,
        clearance_y_by_tower=as_dict(clearance_y_by_tower),
        max_single_move_um=max_single_move_um,
        move_tolerance_um=move_tolerance_um,
    )
    if clearance_move is not None:
        return clearance_move

    candidates: list[PlannedMove] = []
    for stage_name, raw_axes in target_positions.items():
        for axis, raw_target in as_dict(raw_axes).items():
            target = finite_float(raw_target, f"target_positions_um.{stage_name}.{axis}")
            current = axis_value(current_positions, stage_name, axis)
            delta = target - current
            if abs(delta) <= move_tolerance_um:
                continue
            if abs(delta) > max_single_move_um:
                raise ValueError(
                    f"transition move {stage_name}.{axis} delta {delta:.6g} um exceeds "
                    f"max_single_move_um {max_single_move_um:.6g}"
                )
            candidates.append(
                PlannedMove(
                    stage=STAGE_FOR_AXIS[(stage_name, axis)],
                    target_um=target,
                    delta_um=delta,
                    phase="v6_rebased_transition_move",
                )
            )
    if not candidates:
        return None
    return sorted(candidates, key=transition_move_sort_key)[0]


def tower_clearance_move_before_lateral_motion(
    current_positions: JsonDict,
    target_positions: JsonDict,
    *,
    clearance_y_by_tower: JsonDict,
    max_single_move_um: float,
    move_tolerance_um: float,
) -> PlannedMove | None:
    candidates: list[PlannedMove] = []
    for tower in ("tower_1", "tower_2"):
        target_axes = as_dict(target_positions.get(tower))
        if not any(
            abs(finite_float(target_axes.get(axis), f"target_positions_um.{tower}.{axis}") - axis_value(current_positions, tower, axis))
            > move_tolerance_um
            for axis in ("x", "z")
            if target_axes.get(axis) is not None
        ):
            continue
        clearance = clearance_y_by_tower.get(tower)
        target_y = target_axes.get("y")
        if clearance is None and target_y is None:
            continue
        clearance_target = finite_float(clearance if clearance is not None else target_y, f"tower_clearance_y_um.{tower}")
        if target_y is not None:
            clearance_target = max(clearance_target, finite_float(target_y, f"target_positions_um.{tower}.y"))
        current_y = axis_value(current_positions, tower, "y")
        delta = clearance_target - current_y
        if delta <= move_tolerance_um:
            continue
        if abs(delta) > max_single_move_um:
            raise ValueError(
                f"transition clearance move {tower}.y delta {delta:.6g} um exceeds "
                f"max_single_move_um {max_single_move_um:.6g}"
            )
        candidates.append(
            PlannedMove(
                stage=STAGE_FOR_AXIS[(tower, "y")],
                target_um=clearance_target,
                delta_um=delta,
                phase="v6_raise_tower_y_clearance_before_lateral_transition",
            )
        )
    return sorted(candidates, key=transition_move_sort_key)[0] if candidates else None


def transition_move_sort_key(move: PlannedMove) -> tuple[int, str]:
    stage_name, axis = AXIS_FOR_STAGE[move.stage]
    if stage_name.startswith("tower") and axis == "y" and move.delta_um > 0.0:
        return (0, move.stage)
    if stage_name == "camera" and axis in {"x", "z"}:
        return (10, move.stage)
    if stage_name == "zoom":
        return (20, move.stage)
    if stage_name == "camera" and axis == "y":
        return (30, move.stage)
    if stage_name.startswith("tower") and axis in {"x", "z"}:
        return (40, move.stage)
    if stage_name.startswith("tower") and axis == "y":
        return (50, move.stage)
    return (99, move.stage)


def flat_move_response(action: str, status: str, moves: list[PlannedMove], *, extra: JsonDict | None = None) -> JsonDict:
    response: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": action,
        "status": status,
        "move_count": len(moves),
        "planned_moves": [
            {
                "index": index,
                "stage": move.stage,
                "target_um": move.target_um,
                "distance_um": move.target_um,
                "delta_um": move.delta_um,
                "move_mode": "Absolute",
                "phase": move.phase,
                "confirm_text": confirm_text_for_move(action, move, index),
            }
            for index, move in enumerate(moves, start=1)
        ],
    }
    for index in range(1, 4):
        if index <= len(moves):
            move = moves[index - 1]
            response[f"stage{index}"] = move.stage
            response[f"target{index}_um"] = move.target_um
            response[f"distance{index}_um"] = move.target_um
            response[f"delta{index}_um"] = move.delta_um
            response[f"move_mode{index}"] = "Absolute"
            response[f"confirm_text{index}"] = confirm_text_for_move(action, move, index)
        else:
            response[f"stage{index}"] = ""
            response[f"target{index}_um"] = 0.0
            response[f"distance{index}_um"] = 0.0
            response[f"delta{index}_um"] = 0.0
            response[f"move_mode{index}"] = "Absolute"
            response[f"confirm_text{index}"] = ""
    if extra:
        response.update(deepcopy_json(extra))
    return response


def confirm_text_for_move(action: str, move: PlannedMove, index: int) -> str:
    action_label = "offset correction" if action == "offset_correction_move" else "transition"
    return (
        f"V6 {action_label} move {index}: confirm {move.stage} absolute target "
        f"{move.target_um:.6g} um (delta {move.delta_um:.6g} um)."
    )


def rebased_transition_target_positions(from_position: JsonDict, to_position: JsonDict, current_positions: JsonDict) -> JsonDict:
    from_targets = position_target_axes(from_position)
    to_targets = position_target_axes(to_position)
    result: JsonDict = {}
    for stage_name, raw_axes in to_targets.items():
        axes: JsonDict = {}
        for axis, to_value_raw in as_dict(raw_axes).items():
            to_value = finite_float(to_value_raw, f"{to_position.get('id')}.{stage_name}.{axis}")
            from_value_raw = as_dict(from_targets.get(stage_name)).get(axis)
            if from_value_raw is None:
                axes[axis] = to_value
                continue
            current_value = axis_value(current_positions, stage_name, axis)
            from_value = finite_float(from_value_raw, f"{from_position.get('id')}.{stage_name}.{axis}")
            axes[axis] = current_value + (to_value - from_value)
        if axes:
            result[stage_name] = axes
    return result


def position_target_axes(position: JsonDict) -> JsonDict:
    result: JsonDict = {}
    for stage_name, raw_axes in as_dict(position.get("machine_positions_um")).items():
        axes = {
            axis: value
            for axis, value in as_dict(raw_axes).items()
            if value is not None
        }
        if axes:
            result[stage_name] = axes
    zoom = camera_setting_value(position, "zoom")
    if zoom is not None:
        result["zoom"] = {"value": zoom}
    return result


def load_transition_memory_if_available(params_in: JsonDict) -> JsonDict | None:
    if isinstance(params_in.get("memory"), dict) or params_in.get("memory_path"):
        return load_or_initialize_memory(params_in)
    return None


def transition_record_for_current_plan(
    *,
    transition_id: str,
    spec: JsonDict,
    memory: JsonDict | None,
    from_position: JsonDict,
    to_position: JsonDict,
    current_positions: JsonDict,
    params_in: JsonDict,
) -> JsonDict:
    if bool(params_in.get("reset_transition_plan")):
        clear_transition_record(memory, transition_id)
    record = existing_transition_record(memory, transition_id, spec)
    if record is not None:
        return record

    target_positions = rebased_transition_target_positions(from_position, to_position, current_positions)
    record = {
        "transition_id": transition_id,
        "status": "in_progress",
        "from_position_id": from_position["id"],
        "to_position_id": to_position["id"],
        "anchor_positions_um": deepcopy_json(current_positions),
        "target_positions_um": target_positions,
        "standard_delta_um": standard_transition_delta_positions(from_position, to_position),
        "created_at_utc": utc_now_text(),
    }
    if memory is not None:
        records = as_dict(memory.setdefault("transition_records", {}))
        records[transition_id] = record
        memory["transition_records"] = records
        memory["updated_at_utc"] = utc_now_text()
    return record


def existing_transition_record(memory: JsonDict | None, transition_id: str, spec: JsonDict) -> JsonDict | None:
    if memory is None:
        return None
    record = as_dict(as_dict(memory.get("transition_records")).get(transition_id))
    if not record:
        return None
    if str(record.get("status") or "") not in TRANSITION_STATUSES:
        return None
    if str(record.get("from_position_id") or "") != required_text(spec, "from_position_id"):
        return None
    if str(record.get("to_position_id") or "") != required_text(spec, "to_position_id"):
        return None
    target_positions = as_dict(record.get("target_positions_um"))
    anchor_positions = as_dict(record.get("anchor_positions_um"))
    if not target_positions or not anchor_positions:
        return None
    return record


def clear_transition_record(memory: JsonDict | None, transition_id: str) -> None:
    if memory is None:
        return
    records = as_dict(memory.get("transition_records"))
    if transition_id in records:
        records.pop(transition_id, None)
        memory["transition_records"] = records
        memory["updated_at_utc"] = utc_now_text()


def clear_transition_records_from_position(memory: JsonDict, position_id: str) -> None:
    records = as_dict(memory.get("transition_records"))
    if not records:
        return
    retained = {
        transition_id: record
        for transition_id, record in records.items()
        if str(as_dict(record).get("from_position_id") or "") != position_id
    }
    if len(retained) != len(records):
        memory["transition_records"] = retained


def write_transition_memory_if_requested(memory: JsonDict | None, params_in: JsonDict) -> None:
    if memory is None:
        return
    memory["updated_at_utc"] = utc_now_text()
    write_json_if_requested(memory, params_in.get("memory_output_path") or params_in.get("memory_path"))


def standard_transition_delta_positions(from_position: JsonDict, to_position: JsonDict) -> JsonDict:
    from_targets = position_target_axes(from_position)
    to_targets = position_target_axes(to_position)
    result: JsonDict = {}
    for stage_name, raw_to_axes in to_targets.items():
        axes: JsonDict = {}
        from_axes = as_dict(from_targets.get(stage_name))
        for axis, to_value_raw in as_dict(raw_to_axes).items():
            from_value_raw = from_axes.get(axis)
            if from_value_raw is None:
                continue
            axes[axis] = finite_float(to_value_raw, f"{to_position.get('id')}.{stage_name}.{axis}") - finite_float(
                from_value_raw,
                f"{from_position.get('id')}.{stage_name}.{axis}",
            )
        if axes:
            result[stage_name] = axes
    return result


def tower_clearance_y_by_tower(params_in: JsonDict, standard_positions: dict[str, JsonDict]) -> JsonDict:
    override = as_dict(params_in.get("tower_clearance_y_um"))
    result: JsonDict = {}
    for tower in ("tower_1", "tower_2"):
        if tower in override and override[tower] is not None:
            result[tower] = finite_float(override[tower], f"tower_clearance_y_um.{tower}")
            continue
        values = [
            finite_float(as_dict(as_dict(position.get("machine_positions_um")).get(tower)).get("y"), f"{position.get('id')}.{tower}.y")
            for position in standard_positions.values()
            if as_dict(as_dict(position.get("machine_positions_um")).get(tower)).get("y") is not None
        ]
        if values:
            result[tower] = max(values)
    return result


def camera_setting_value(position: JsonDict, key: str) -> float | None:
    raw = as_dict(position.get("camera_settings")).get(key)
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw is None:
        return None
    return finite_float(raw, f"{position.get('id')}.camera_settings.{key}")


def standard_session_for_capture(capture_id: str, memory: JsonDict, params_in: JsonDict) -> JsonDict:
    baselines = as_dict(memory.get("standard_baselines"))
    raw_baseline = baselines.get(capture_id)
    if isinstance(raw_baseline, dict) and raw_baseline:
        return raw_baseline
    baseline_dir = Path(str(params_in.get("standard_baseline_dir") or memory.get("standard_baseline_dir") or DEFAULT_STANDARD_BASELINE_DIR))
    if not baseline_dir.is_absolute():
        baseline_dir = Path.cwd() / baseline_dir
    path = baseline_dir / f"{capture_id}.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"standard baseline for {capture_id} was not found at {path}")


def single_side_reference_feature(session: JsonDict, label: str) -> Any:
    explicit = isinstance(session.get("side_reference_line"), dict)
    line_count = 0
    for item in selected_items(session):
        shape_kind = str(item.get("shape_kind") or "").strip()
        source = str(item.get("source") or "").strip()
        if shape_kind == "line" or source == "side_reference_line":
            line_count += 1
    if not explicit and line_count > 1:
        raise ValueError(f"{label} has ambiguous side_reference selections; select exactly one side_reference line")
    reference = selected_side_reference_feature(session)
    if reference is None:
        raise ValueError(f"{label} must include exactly one side_reference line")
    return reference


def recorded_session(memory: JsonDict, capture_id: str) -> JsonDict:
    record = as_dict(as_dict(memory.get("capture_records")).get(capture_id))
    session = as_dict(record.get("session"))
    if not session:
        raise ValueError(f"v6 memory does not contain a reviewed session for {capture_id}")
    return session


def ensure_side_feature_inside_mirror_roi(y_px: float, mirror_roi: JsonDict, label: str) -> None:
    y = finite_float(y_px, f"{label}.y_px")
    y1 = finite_float(mirror_roi["y1"], "mirror_roi.y1")
    y2 = finite_float(mirror_roi["y2"], "mirror_roi.y2")
    if y < y1 or y > y2:
        raise ValueError(f"{label} y={y:.6g} px is outside the mirror ROI y-range {y1:.6g}..{y2:.6g}")


def mirror_ball_reference_delta_px(*, ball_y_px: float, reference_y_px: float, mirror_roi: JsonDict) -> JsonDict:
    bottom = finite_float(mirror_roi["y2"], "mirror_roi.y2")
    ball_flipped = bottom - finite_float(ball_y_px, "ball_y_px")
    reference_flipped = bottom - finite_float(reference_y_px, "reference_y_px")
    return {
        "mirror_roi": deepcopy_json(mirror_roi),
        "full_image": {
            "ball_y_px": finite_float(ball_y_px, "ball_y_px"),
            "reference_y_px": finite_float(reference_y_px, "reference_y_px"),
            "raw_delta_y_px": finite_float(ball_y_px, "ball_y_px") - finite_float(reference_y_px, "reference_y_px"),
        },
        "mirror_flipped": {
            "ball_y_px": ball_flipped,
            "reference_y_px": reference_flipped,
        },
        "flipped_delta_y_px": ball_flipped - reference_flipped,
    }


def mirror_roi_for_session(session: JsonDict, params_in: JsonDict, capture_id: str) -> JsonDict:
    mirror_rois = as_dict(params_in.get("mirror_rois"))
    raw_roi = mirror_rois.get(capture_id) or params_in.get("mirror_roi") or as_dict(session.get("mirror_roi"))
    if raw_roi:
        return normalize_roi(raw_roi, source="params_or_session_mirror_roi")
    y_values: list[float] = []
    x_values: list[float] = []
    for item in selected_items(session):
        roi = as_dict(item.get("roi"))
        if roi:
            normalized = normalize_roi(roi, source="selected_item_roi")
            x_values.extend([normalized["x1"], normalized["x2"]])
            y_values.extend([normalized["y1"], normalized["y2"]])
    if x_values and y_values:
        return {
            "x1": min(x_values),
            "y1": min(y_values),
            "x2": max(x_values),
            "y2": max(y_values),
            "source": "union_of_selected_item_rois",
        }
    return {
        "x1": 0.0,
        "y1": 0.0,
        "x2": 2592.0,
        "y2": DEFAULT_IMAGE_HEIGHT_PX,
        "source": "full_image_default",
    }


def normalize_roi(raw_roi: Any, *, source: str) -> JsonDict:
    if isinstance(raw_roi, list) and len(raw_roi) == 4:
        x1, y1, x2, y2 = [finite_float(value, "mirror_roi") for value in raw_roi]
    else:
        roi = as_dict(raw_roi)
        x1 = finite_float(roi.get("x1"), "mirror_roi.x1")
        y1 = finite_float(roi.get("y1"), "mirror_roi.y1")
        x2 = finite_float(roi.get("x2"), "mirror_roi.x2")
        y2 = finite_float(roi.get("y2"), "mirror_roi.y2")
    if x2 <= x1 or y2 <= y1:
        raise ValueError("mirror_roi must have x2>x1 and y2>y1")
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "source": source}


def selected_items(session: JsonDict) -> Iterable[JsonDict]:
    selected = as_dict(session.get("selected_recognition"))
    for key in sorted(selected):
        values = selected.get(key)
        if isinstance(values, list):
            for value in values:
                yield as_dict(value)


def ball_um_per_pixel(radius_px: float | None) -> float:
    if radius_px is None:
        raise ValueError("selected ball circle must include radius for um-per-pixel scaling")
    return DEFAULT_BALL_DIAMETER_UM / (2.0 * positive_float(radius_px, "ball.radius_px"))


def load_or_initialize_memory(params_in: JsonDict) -> JsonDict:
    try:
        memory = load_memory_from_params(params_in)
    except FileNotFoundError:
        memory = initialize_v6_memory(params_in)
    require_memory(memory)
    return memory


def load_memory_from_params(params_in: JsonDict) -> JsonDict:
    raw_memory = params_in.get("memory")
    if isinstance(raw_memory, dict):
        memory = deepcopy_json(raw_memory)
    else:
        memory_path = params_in.get("memory_path")
        if not memory_path:
            raise ValueError("memory or memory_path is required")
        path = Path(str(memory_path))
        if not path.is_absolute():
            path = Path.cwd() / path
        memory = json.loads(path.read_text(encoding="utf-8"))
    require_memory(memory)
    return memory


def load_standard_positions(params_in: JsonDict) -> dict[str, JsonDict]:
    payload: JsonDict
    if isinstance(params_in.get("standard_positions"), dict):
        payload = as_dict(params_in["standard_positions"])
    else:
        memory: JsonDict = {}
        try:
            memory = load_memory_from_params(params_in)
        except Exception:
            memory = {}
        path_value = params_in.get("standard_positions_path") or memory.get("standard_positions_path") or DEFAULT_STANDARD_POSITIONS_PATH
        path = Path(str(path_value))
        if not path.is_absolute():
            path = Path.cwd() / path
        payload = json.loads(path.read_text(encoding="utf-8"))
    positions = {}
    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, list):
        raise ValueError("standard positions payload must contain a positions list")
    for raw_position in raw_positions:
        position = as_dict(raw_position)
        position_id = str(position.get("id") or "").strip()
        if position_id:
            positions[position_id] = position
    return positions


def required_machine_positions_payload(params_in: JsonDict) -> JsonDict:
    machine_positions = as_dict(params_in.get("machine_positions_um"))
    if not machine_positions:
        machine_positions = machine_positions_from_flat_params(params_in)
    if not machine_positions:
        raise ValueError("machine_positions_um or flat machine position fields are required")
    normalized = deepcopy_json(machine_positions)
    for stage_name, raw_axes in list(normalized.items()):
        axes = as_dict(raw_axes)
        if not axes:
            raise ValueError(f"machine_positions_um.{stage_name} must contain axis values")
        for axis, value in list(axes.items()):
            axes[axis] = finite_float(value, f"machine_positions_um.{stage_name}.{axis}")
        normalized[stage_name] = axes
    return normalized


def machine_positions_from_flat_params(params_in: JsonDict) -> JsonDict:
    result: JsonDict = {}
    for stage_name in ("camera", "tower_1", "tower_2"):
        axes: JsonDict = {}
        for axis in ("x", "y", "z"):
            for key in (f"{stage_name}_{axis}_um", f"{stage_name}.{axis}", f"{stage_name}_{axis}"):
                if key in params_in and params_in[key] is not None:
                    axes[axis] = finite_float(params_in[key], key)
                    break
        if axes:
            result[stage_name] = axes
    if "zoom_um" in params_in and params_in["zoom_um"] is not None:
        result["zoom"] = {"value": finite_float(params_in["zoom_um"], "zoom_um")}
    return result


def axis_value(machine_positions: JsonDict, stage_name: str, axis: str) -> float:
    value = as_dict(machine_positions.get(stage_name)).get(axis)
    if value is None:
        raise ValueError(f"current machine position is missing {stage_name}.{axis}")
    return finite_float(value, f"machine_positions_um.{stage_name}.{axis}")


def reviewed_session_has_selected_shapes(session: JsonDict) -> bool:
    return any(True for _item in selected_items(session))


def memory_summary(memory: JsonDict) -> JsonDict:
    return {
        "capture_count": len(as_dict(memory.get("capture_records"))),
        "recorded_capture_ids": sorted(as_dict(memory.get("capture_records"))),
        "standard_positions_path": memory.get("standard_positions_path"),
        "standard_baseline_dir": memory.get("standard_baseline_dir"),
    }


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
    if str(memory.get("action") or "") != MEMORY_ACTION:
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


def positive_float(value: Any, name: str) -> float:
    result = finite_float(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def non_negative_float(value: Any, name: str) -> float:
    result = finite_float(value, name)
    if result < 0.0:
        raise ValueError(f"{name} must be non-negative")
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
        "stage1": "",
        "target1_um": 0.0,
        "distance1_um": 0.0,
        "delta1_um": 0.0,
        "move_mode1": "Absolute",
        "move_count": 0,
    }
    if traceback_text:
        response["traceback"] = traceback_text
    return response


def _parse_args(argv: Sequence[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Run a v6 vision workflow JSON payload.")
    parser.add_argument("input_json", help="ParamIn payload path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> JsonDict:
    args = _parse_args(argv)
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    result = run_v6_vision_workflow(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":  # pragma: no cover - manual CLI helper
    main()
