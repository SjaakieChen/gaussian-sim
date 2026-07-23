"""Migration v6 reviewed-vision alignment workflow.

Python validates reviewed measurements and proposes bounded absolute targets.
It does not open a hardware interface or move a stage. YASE remains the only
machine-motion boundary and confirms every image-derived move.
"""

from __future__ import annotations

import copy
import json
import math
import os
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - developer machines do not have TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback for repository tests."""


from .sequence_geometry_memory import selected_circle_feature, selected_rectangle_feature


JsonDict = dict[str, Any]

SCHEMA_VERSION = 2
LEGACY_MEMORY_SCHEMA_VERSION = 1
MEMORY_ACTION = "v6_vision_workflow_memory"

DEFAULT_V6_STANDARD_DATA_DIR = Path(__file__).resolve().parents[1] / "standard_positions_v4"
DEFAULT_STANDARD_POSITIONS_PATH = DEFAULT_V6_STANDARD_DATA_DIR / "standard_positions.json"
DEFAULT_STANDARD_BASELINE_DIR = DEFAULT_V6_STANDARD_DATA_DIR / "vision_baselines"

DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM = 500.0
DEFAULT_BALL_DIAMETER_UM = 500.0
DEFAULT_TRENCH_HEIGHT_UM = 300.0
DEFAULT_MOVE_TOLERANCE_UM = 1.0
DEFAULT_CAPTURE_STABILITY_TOLERANCE_UM = 1.0
DEFAULT_CAPTURE_POSE_TOLERANCE_UM = 2.0
DEFAULT_ZOOM_TOLERANCE_UM = 1.0
DEFAULT_DIVERGENCE_TOLERANCE_UM = 2.0
DEFAULT_MAX_CORRECTION_ATTEMPTS = 8
DEFAULT_MAX_TRANSITION_MOVE_UM = 200000.0
DEFAULT_MIN_TRENCH_LINE_SEPARATION_PX = 10.0
DEFAULT_MAX_TRENCH_LINE_SEPARATION_PX = 1000.0
DEFAULT_MIN_SIDE_SCALE_UM_PER_PX = 0.05
DEFAULT_MAX_SIDE_SCALE_UM_PER_PX = 20.0
TRANSITION_STATUSES = {"in_progress", "complete"}

MACHINE_AXES = ("machine_x_um", "machine_y_um", "machine_z_um")
AXIS_ALIASES = {
    "machine_x_um": "machine_x_um",
    "machine_y_um": "machine_y_um",
    "machine_z_um": "machine_z_um",
    "x": "machine_x_um",
    "y": "machine_y_um",
    "z": "machine_z_um",
}
STAGE_FOR_AXIS = {
    ("camera", "machine_x_um"): "Camera_X",
    ("camera", "machine_y_um"): "Camera_Y",
    ("camera", "machine_z_um"): "Camera_Z",
    ("tower_1", "machine_x_um"): "Align_X1",
    ("tower_1", "machine_y_um"): "Align_Y1",
    ("tower_1", "machine_z_um"): "Align_Z1",
    ("tower_2", "machine_x_um"): "Align_X2",
    ("tower_2", "machine_y_um"): "Align_Y2",
    ("tower_2", "machine_z_um"): "Align_Z2",
    ("zoom", "zoom_um"): "Zoom",
}
AXIS_FOR_STAGE = {stage: stage_axis for stage_axis, stage in STAGE_FOR_AXIS.items()}
TARGET_TOWER = {"ball_1": "tower_1", "ball_2": "tower_2"}
FINAL_TARGETS_UM = {
    "ball_1": {"machine_x_um": 289.0, "machine_y_um": 0.0, "machine_z_um": 0.0},
    "ball_2": {"machine_x_um": 989.0, "machine_y_um": 0.0, "machine_z_um": 0.0},
}
FINAL_CENTER_SPACING_UM = 700.0


CAPTURE_SPECS: dict[str, JsonDict] = {
    "2.1.1": {
        "position_id": "2.1",
        "target": "ball_1",
        "view": "gross_top_xz",
        "result_use": "coarse_offset_correction",
    },
    "2.4.1": {
        "position_id": "2.4",
        "target": "ball_1",
        "view": "fine_top_xz",
        "result_use": "laser_reference_registration",
    },
    "2.5.1": {
        "position_id": "2.5",
        "target": "ball_1",
        "view": "fine_top_xz",
        "result_use": "top_fine_offset_correction",
    },
    "2.6.1": {
        "position_id": "2.6",
        "target": "ball_1",
        "view": "mirror_side_y",
        "result_use": "side_mirror_y_offset_correction",
    },
    "4.1.1": {
        "position_id": "4.1",
        "target": "ball_2",
        "view": "gross_top_xz",
        "result_use": "coarse_offset_correction",
    },
    "4.4.1": {
        "position_id": "4.4",
        "target": "ball_2",
        "view": "fine_top_xz",
        "result_use": "laser_reference_registration",
    },
    "4.5.1": {
        "position_id": "4.5",
        "target": "ball_2",
        "view": "fine_top_xz",
        "result_use": "top_fine_offset_correction",
    },
    "4.6.2": {
        "position_id": "4.6.2",
        "target": "ball_2",
        "view": "mirror_side_y",
        "result_use": "side_mirror_y_offset_correction",
    },
}

OFFSET_SPECS: dict[str, JsonDict] = {
    "2.1.1": {
        "kind": "coarse_top",
        "target": "ball_1",
        "tower": "tower_1",
        "max_step_um": 350.0,
        "max_total_residual_um": 900.0,
        "tolerance_um": 2.0,
    },
    "4.1.1": {
        "kind": "coarse_top",
        "target": "ball_2",
        "tower": "tower_2",
        "max_step_um": 350.0,
        "max_total_residual_um": 900.0,
        "tolerance_um": 2.0,
    },
    "2.5.1": {
        "kind": "top_fine",
        "target": "ball_1",
        "tower": "tower_1",
        "reference_capture_id": "2.4.1",
        "max_step_um": 150.0,
        "max_total_residual_um": 1200.0,
        "tolerance_um": 0.75,
    },
    "4.5.1": {
        "kind": "top_fine",
        "target": "ball_2",
        "tower": "tower_2",
        "reference_capture_id": "4.4.1",
        "max_step_um": 150.0,
        "max_total_residual_um": 1200.0,
        "tolerance_um": 0.75,
    },
    "2.6.1": {
        "kind": "side_mirror_y",
        "target": "ball_1",
        "tower": "tower_1",
        "max_step_um": 75.0,
        "max_total_residual_um": 400.0,
        "tolerance_um": 0.75,
    },
    "4.6.2": {
        "kind": "side_mirror_y",
        "target": "ball_2",
        "tower": "tower_2",
        "max_step_um": 75.0,
        "max_total_residual_um": 400.0,
        "tolerance_um": 0.75,
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


@dataclass(frozen=True)
class ReviewedLine:
    role: str
    image_y_px: float
    image_x1_px: float
    image_x2_px: float
    image_y1_px: float
    image_y2_px: float
    source: str


class V6VisionWorkflowStep(TMPythonStatementJ):
    """TMPython entrypoint for V6 non-UI commands."""

    def run(self, params_in: JsonDict) -> JsonDict:
        try:
            return run_v6_vision_workflow(params_in)
        except Exception as exc:  # pragma: no cover - machine fail-closed boundary
            return abort_response(f"V6VisionWorkflowStep failed: {exc}", traceback_text=traceback.format_exc())


class V6VisionReviewRecordStep(TMPythonStatementJ):
    """TMPython entrypoint that opens review and records one stable capture."""

    def run(self, params_in: JsonDict) -> JsonDict:
        try:
            return review_and_record_capture(params_in)
        except Exception as exc:  # pragma: no cover - machine fail-closed boundary
            return abort_response(f"V6VisionReviewRecordStep failed: {exc}", traceback_text=traceback.format_exc())


def run_v6_vision_workflow(params_in: JsonDict) -> JsonDict:
    """Run one schema-v2 V6 command and convert every failure to no-motion output."""

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
        if command == "verify_final_geometry":
            result = verify_final_geometry(params_in)
            write_json_if_requested(result, params_in.get("output_path"))
            return result
        raise ValueError(
            "command must be init, record_capture, next_offset_correction, "
            "next_transition_move, or verify_final_geometry"
        )
    except Exception as exc:
        return abort_response(str(exc))


def initialize_v6_memory(params_in: JsonDict) -> JsonDict:
    """Create empty V6 memory. The main YASE workflow calls this exactly once."""

    require_schema(params_in)
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": MEMORY_ACTION,
        "status": "v6 reviewed vision workflow memory initialized",
        "standard_positions_path": str(params_in.get("standard_positions_path") or DEFAULT_STANDARD_POSITIONS_PATH),
        "standard_baseline_dir": str(params_in.get("standard_baseline_dir") or DEFAULT_STANDARD_BASELINE_DIR),
        "capture_records": {},
        "capture_history": {},
        "capture_specs": deepcopy_json(CAPTURE_SPECS),
        "correction_plans": {},
        "correction_history": [],
        "convergence": {},
        "transition_records": {},
        "invalidation_history": [],
        "final_targets_um": deepcopy_json(FINAL_TARGETS_UM),
        "final_center_spacing_um": FINAL_CENTER_SPACING_UM,
        "updated_at_utc": utc_now_text(),
    }


def review_and_record_capture(params_in: JsonDict) -> JsonDict:
    """Validate one stable grab, open operator review, then version its record."""

    require_schema(params_in)
    capture_id = required_text(params_in, "capture_id")
    spec = as_dict(CAPTURE_SPECS.get(capture_id))
    if not spec:
        raise ValueError(f"unsupported v6 capture_id {capture_id!r}")
    image_path = required_text(params_in, "image_path")
    memory = load_or_initialize_memory(params_in)

    before, after = capture_pose_pair(params_in)
    stability = validate_capture_stability(
        before,
        after,
        tolerance_um=non_negative_float(
            params_in.get("capture_stability_tolerance_um", DEFAULT_CAPTURE_STABILITY_TOLERANCE_UM),
            "capture_stability_tolerance_um",
        ),
    )

    review_session = as_dict(params_in.get("review_session"))
    if not review_session:
        baseline = standard_session_for_capture(capture_id, memory, params_in)
        review_session = open_v6_vision_review_ui(
            image_path,
            capture_id=capture_id,
            initial_session=baseline,
            roi_output_path=params_in.get("roi_output_path"),
            result_output_path=params_in.get("review_session_output_path")
            or params_in.get("vision_session_output_path"),
        )
    if review_was_cancelled(review_session):
        return abort_response(f"operator cancelled review for {capture_id}; v6 memory was not updated")
    validate_reviewed_capture_session(capture_id, review_session, params_in)

    image_dimensions = image_dimensions_payload(image_path, review_session)
    settings = capture_settings_payload(params_in, after)
    records = as_dict(memory.setdefault("capture_records", {}))
    previous = as_dict(records.get(capture_id))
    revision = int(previous.get("revision") or 0) + 1
    if previous:
        history = as_dict(memory.setdefault("capture_history", {}))
        prior_versions = history.get(capture_id)
        if not isinstance(prior_versions, list):
            prior_versions = []
        archived = deepcopy_json(previous)
        archived["superseded_at_utc"] = utc_now_text()
        prior_versions.append(archived)
        history[capture_id] = prior_versions
        memory["capture_history"] = history

    now = utc_now_text()
    record = {
        **deepcopy_json(spec),
        "capture_id": capture_id,
        "review_status": "reviewed",
        "revision": revision,
        "recorded_at_utc": now,
        "image_path": image_path,
        "image_dimensions_px": image_dimensions,
        "session": deepcopy_json(review_session),
        "machine_positions_before_grab_um": before,
        "machine_positions_after_grab_um": after,
        "machine_positions_um": after,
        "capture_stability": stability,
        "camera_settings": settings,
        "calibration_context": {
            "view": spec["view"],
            "zoom_um": axis_value(after, "zoom", "zoom_um"),
            "image_width_px": image_dimensions.get("image_width_px"),
            "image_height_px": image_dimensions.get("image_height_px"),
        },
        "scale_source": scale_source_for_capture(capture_id),
    }
    records[capture_id] = record
    memory["capture_records"] = records
    invalidated = invalidate_dependent_plans(memory, capture_id, revision)
    memory["updated_at_utc"] = now
    memory["ok"] = True
    memory["action"] = MEMORY_ACTION
    memory["status"] = f"recorded reviewed capture {capture_id} revision {revision}"
    write_json_if_requested(memory, params_in.get("memory_output_path") or params_in.get("memory_path"))

    result = {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": "reviewed_capture_recorded",
        "status": f"recorded reviewed capture {capture_id} revision {revision}",
        "capture_id": capture_id,
        "revision": revision,
        "position_id": spec["position_id"],
        "target": spec["target"],
        "view": spec["view"],
        "result_use": spec["result_use"],
        "machine_positions_um": deepcopy_json(after),
        "capture_stability": stability,
        "invalidated_plan_ids": invalidated,
        "sequence_memory_summary": memory_summary(memory),
    }
    write_json_if_requested(result, params_in.get("result_output_path") or params_in.get("output_path"))
    return result


def next_offset_correction(params_in: JsonDict) -> JsonDict:
    """Return the next bounded correction step for one fresh reviewed capture."""

    require_schema(params_in)
    capture_id = required_text(params_in, "capture_id")
    spec = as_dict(OFFSET_SPECS.get(capture_id))
    if not spec:
        raise ValueError(f"capture_id {capture_id!r} does not have a v6 offset correction")
    memory = load_memory_from_params(params_in)
    current_positions = required_machine_positions_payload(params_in)
    record = recorded_capture(memory, capture_id)
    validate_current_pose_matches_capture(
        current_positions,
        record,
        tower=str(spec["tower"]),
        tolerance_um=non_negative_float(
            params_in.get("capture_pose_tolerance_um", DEFAULT_CAPTURE_POSE_TOLERANCE_UM),
            "capture_pose_tolerance_um",
        ),
    )

    max_step_um = positive_float(
        params_in.get("max_step_um", params_in.get("max_correction_um", spec["max_step_um"])),
        "max_step_um",
    )
    max_total_residual_um = positive_float(
        params_in.get("max_total_residual_um", spec["max_total_residual_um"]),
        "max_total_residual_um",
    )
    tolerance_um = non_negative_float(
        params_in.get("correction_tolerance_um", spec["tolerance_um"]),
        "correction_tolerance_um",
    )

    if spec["kind"] == "coarse_top":
        correction = coarse_top_correction(capture_id, spec, memory, params_in)
    elif spec["kind"] == "top_fine":
        correction = top_fine_correction(capture_id, spec, memory, params_in)
    elif spec["kind"] == "side_mirror_y":
        correction = side_mirror_y_correction(capture_id, spec, memory, params_in)
    else:  # pragma: no cover - constant table guard
        raise ValueError(f"unsupported correction kind {spec['kind']!r}")

    residuals = as_dict(correction.get("axis_residuals_um"))
    convergence = evaluate_convergence(
        memory,
        capture_id=capture_id,
        capture_revision=int(record.get("revision") or 1),
        residuals_um=residuals,
        tolerance_um=tolerance_um,
        params_in=params_in,
    )
    try:
        planned_moves = moves_from_axis_corrections(
            current_positions,
            tower=str(spec["tower"]),
            axis_deltas_um=residuals,
            max_step_um=max_step_um,
            max_total_residual_um=max_total_residual_um,
            tolerance_um=tolerance_um,
            phase=f"v6_{spec['kind']}_offset_correction",
        )
    except Exception:
        convergence["status"] = "bounds_rejected"
        convergence["updated_at_utc"] = utc_now_text()
        persist_memory(memory, params_in)
        raise

    response = correction_response(
        capture_id=capture_id,
        spec=spec,
        correction=correction,
        planned_moves=planned_moves,
        tolerance_um=tolerance_um,
        max_step_um=max_step_um,
        max_total_residual_um=max_total_residual_um,
        convergence=convergence,
    )
    save_correction_plan(memory, capture_id, record, response)
    persist_memory(memory, params_in)
    return response


def coarse_top_correction(capture_id: str, spec: JsonDict, memory: JsonDict, params_in: JsonDict) -> JsonDict:
    """Place the gross ball at the reviewed standard pixels in the same view."""

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
    if standard_ball is None or live_ball is None:  # pragma: no cover - required=True
        raise ValueError(f"{capture_id} gross ball is missing")
    um_per_pixel = ball_um_per_pixel(live_ball.radius_px or standard_ball.radius_px)
    image_shift = {
        "image_x_px": live_ball.center_px["x"] - standard_ball.center_px["x"],
        "image_y_px": live_ball.center_px["y"] - standard_ball.center_px["y"],
    }
    # Screen right is +machine X. Screen up is +machine Z, while image Y grows down.
    residuals = {
        "machine_x_um": -image_shift["image_x_px"] * um_per_pixel,
        "machine_z_um": image_shift["image_y_px"] * um_per_pixel,
    }
    return {
        "source": "standard_gross_ball_pixels_minus_live_gross_ball_pixels",
        "standard_ball_center_px": image_point(standard_ball.center_px),
        "live_ball_center_px": image_point(live_ball.center_px),
        "image_shift_live_minus_standard_px": image_shift,
        "scale_context": {
            "view": CAPTURE_SPECS[capture_id]["view"],
            "source": "reviewed_ball_diameter",
            "known_distance_um": DEFAULT_BALL_DIAMETER_UM,
            "um_per_pixel": um_per_pixel,
        },
        "view_mapping": {
            "image_right": {"machine_axis": "machine_x_um", "sign": 1.0},
            "image_up": {"machine_axis": "machine_z_um", "sign": 1.0},
            "image_y_down": {"machine_axis": "machine_z_um", "sign": -1.0},
        },
        "axis_residuals_um": residuals,
        "axis_corrections_um": deepcopy_json(residuals),
    }


def top_fine_correction(capture_id: str, spec: JsonDict, memory: JsonDict, params_in: JsonDict) -> JsonDict:
    """Move one ball to its absolute target relative to the laser rectangle."""

    measurement = top_ball_measurement(
        memory,
        reference_capture_id=required_text(spec, "reference_capture_id"),
        ball_capture_id=capture_id,
        target=str(spec["target"]),
        params_in=params_in,
    )
    desired = FINAL_TARGETS_UM[str(spec["target"])]
    residuals = {
        "machine_x_um": desired["machine_x_um"] - measurement["measured_machine_x_um"],
        "machine_z_um": desired["machine_z_um"] - measurement["measured_machine_z_um"],
    }
    return {
        "source": "reviewed_ball_center_relative_to_reviewed_laser_rectangle_with_camera_compensation",
        "reference_capture_id": spec["reference_capture_id"],
        "ball_capture_id": capture_id,
        "target_coordinates_um": {
            "machine_x_um": desired["machine_x_um"],
            "machine_z_um": desired["machine_z_um"],
        },
        **measurement,
        "axis_residuals_um": residuals,
        "axis_corrections_um": deepcopy_json(residuals),
    }


def top_ball_measurement(
    memory: JsonDict,
    *,
    reference_capture_id: str,
    ball_capture_id: str,
    target: str,
    params_in: JsonDict | None = None,
) -> JsonDict:
    """Measure machine X/Z from two same-view, same-zoom reviewed captures."""

    params_in = params_in or {}
    reference_record = recorded_capture(memory, reference_capture_id)
    ball_record = recorded_capture(memory, ball_capture_id)
    assert_matching_top_calibration_context(
        reference_record,
        ball_record,
        zoom_tolerance_um=non_negative_float(
            params_in.get("zoom_tolerance_um", DEFAULT_ZOOM_TOLERANCE_UM),
            "zoom_tolerance_um",
        ),
    )
    rectangle = selected_rectangle_feature(
        as_dict(reference_record.get("session")),
        DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM,
    )
    ball = selected_circle_feature(
        as_dict(ball_record.get("session")),
        f"{ball_capture_id} reviewed top ball",
        target=target,
        role_context="top_ball",
    )
    if ball is None:  # pragma: no cover - required=True
        raise ValueError(f"{ball_capture_id} top ball is missing")

    reference_pose = normalize_machine_positions(as_dict(reference_record.get("machine_positions_um")))
    ball_pose = normalize_machine_positions(as_dict(ball_record.get("machine_positions_um")))
    camera_compensation = {
        "machine_x_um": axis_value(ball_pose, "camera", "machine_x_um")
        - axis_value(reference_pose, "camera", "machine_x_um"),
        "machine_z_um": axis_value(ball_pose, "camera", "machine_z_um")
        - axis_value(reference_pose, "camera", "machine_z_um"),
    }
    image_delta = {
        "image_x_px": ball.center_px["x"] - rectangle.center_px["x"],
        "image_y_px": ball.center_px["y"] - rectangle.center_px["y"],
    }
    measured_machine_x_um = camera_compensation["machine_x_um"] + image_delta["image_x_px"] * rectangle.um_per_pixel
    measured_machine_z_um = camera_compensation["machine_z_um"] - image_delta["image_y_px"] * rectangle.um_per_pixel
    return {
        "reference_center_px": image_point(rectangle.center_px),
        "ball_center_px": image_point(ball.center_px),
        "image_delta_ball_minus_reference_px": image_delta,
        "camera_compensation_um": camera_compensation,
        "measured_machine_x_um": measured_machine_x_um,
        "measured_machine_z_um": measured_machine_z_um,
        "scale_context": {
            "view": "fine_top_xz",
            "zoom_um": zoom_value(reference_pose),
            "source": "laser_rectangle_short_edge",
            "known_distance_um": DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM,
            "measured_pixels": rectangle.short_edge_length_px,
            "um_per_pixel": rectangle.um_per_pixel,
        },
        "view_mapping": {
            "image_right": {"machine_axis": "machine_x_um", "sign": 1.0},
            "image_up": {"machine_axis": "machine_z_um", "sign": 1.0},
            "image_y_down": {"machine_axis": "machine_z_um", "sign": -1.0},
        },
    }


def side_mirror_y_correction(capture_id: str, spec: JsonDict, memory: JsonDict, params_in: JsonDict) -> JsonDict:
    """Align the reviewed ball center to the physical trench top in the mirror."""

    record = recorded_capture(memory, capture_id)
    session = as_dict(record.get("session"))
    target = str(spec["target"])
    ball = selected_circle_feature(
        session,
        f"{capture_id} reviewed side ball",
        target=target,
        role_context="side_ball",
    )
    if ball is None:  # pragma: no cover - required=True
        raise ValueError(f"{capture_id} side ball is missing")
    geometry = reviewed_trench_geometry(session, params_in=params_in, capture_id=capture_id)
    ball_transform = mirror_point_transform(
        image_x_px=ball.center_px["x"],
        image_y_px=ball.center_px["y"],
        mirror_roi=geometry["mirror_roi"],
    )
    top_transform = geometry["trench_top_transform"]
    residual_flipped_px = ball_transform["mirror_flipped"]["image_y_px"] - top_transform["mirror_flipped"][
        "image_y_px"
    ]
    # Mirror-flipped image Y grows toward the physical trench floor (down).
    # Machine +Y is up, so the measured machine-Y height has the opposite sign.
    measured_machine_y_um = -residual_flipped_px * geometry["um_per_pixel"]
    residuals = {"machine_y_um": -measured_machine_y_um}
    return {
        "source": "reviewed_mirror_ball_center_to_physical_trench_top",
        "mirror_view": True,
        "mirror_flip_y": True,
        "target_machine_y_um": 0.0,
        "measured_machine_y_um": measured_machine_y_um,
        "ball_transform": ball_transform,
        "trench_top_transform": top_transform,
        "trench_floor_transform": geometry["trench_floor_transform"],
        "residual_flipped_y_px": residual_flipped_px,
        "scale_context": geometry["scale_context"],
        "mirror_transform": {
            "mirror_roi": geometry["mirror_roi"],
            "formula": "flipped_y_px = mirror_roi.y2 - full_image_y_px",
            "physical_order": "trench floor is above trench top after mirror flip",
        },
        "view_mapping": {
            "mirror_corrected_vertical": {"machine_axis": "machine_y_um", "sign": 1.0},
            "machine_note": "smaller machine_y_um is downward",
        },
        "axis_residuals_um": residuals,
        "axis_corrections_um": deepcopy_json(residuals),
    }


def reviewed_trench_geometry(
    session: JsonDict,
    *,
    params_in: JsonDict | None = None,
    capture_id: str = "",
) -> JsonDict:
    """Return fail-closed two-line side calibration in mirror-corrected pixels."""

    params_in = params_in or {}
    mirror_roi = mirror_roi_for_session(session, params_in, capture_id, required=True)
    top_line, floor_line = trench_lines_for_session(session)
    validate_reviewed_line(top_line, mirror_roi, "trench_top_surface")
    validate_reviewed_line(floor_line, mirror_roi, "trench_bottom_floor")
    if floor_line.image_y_px >= top_line.image_y_px:
        raise ValueError(
            "reversed side features: in the raw bottom-mirror image the physical trench floor "
            "must be above the physical trench top"
        )

    top_transform = mirror_point_transform(
        image_x_px=0.5 * (top_line.image_x1_px + top_line.image_x2_px),
        image_y_px=top_line.image_y_px,
        mirror_roi=mirror_roi,
    )
    floor_transform = mirror_point_transform(
        image_x_px=0.5 * (floor_line.image_x1_px + floor_line.image_x2_px),
        image_y_px=floor_line.image_y_px,
        mirror_roi=mirror_roi,
    )
    separation_px = floor_transform["mirror_flipped"]["image_y_px"] - top_transform["mirror_flipped"][
        "image_y_px"
    ]
    minimum = positive_float(
        params_in.get("min_trench_line_separation_px", DEFAULT_MIN_TRENCH_LINE_SEPARATION_PX),
        "min_trench_line_separation_px",
    )
    maximum = positive_float(
        params_in.get("max_trench_line_separation_px", DEFAULT_MAX_TRENCH_LINE_SEPARATION_PX),
        "max_trench_line_separation_px",
    )
    if separation_px < minimum or separation_px > maximum:
        raise ValueError(
            f"implausible trench-line separation {separation_px:.6g} px; expected {minimum:.6g}..{maximum:.6g} px"
        )
    um_per_pixel = DEFAULT_TRENCH_HEIGHT_UM / separation_px
    minimum_scale = positive_float(
        params_in.get("min_side_scale_um_per_px", DEFAULT_MIN_SIDE_SCALE_UM_PER_PX),
        "min_side_scale_um_per_px",
    )
    maximum_scale = positive_float(
        params_in.get("max_side_scale_um_per_px", DEFAULT_MAX_SIDE_SCALE_UM_PER_PX),
        "max_side_scale_um_per_px",
    )
    if um_per_pixel < minimum_scale or um_per_pixel > maximum_scale:
        raise ValueError(
            f"implausible side scale {um_per_pixel:.6g} um/px; expected {minimum_scale:.6g}..{maximum_scale:.6g}"
        )
    return {
        "mirror_roi": mirror_roi,
        "trench_top_line": line_payload(top_line),
        "trench_floor_line": line_payload(floor_line),
        "trench_top_transform": top_transform,
        "trench_floor_transform": floor_transform,
        "line_separation_px": separation_px,
        "um_per_pixel": um_per_pixel,
        "scale_context": {
            "view": "mirror_side_y",
            "source": "reviewed_trench_top_to_floor",
            "known_distance_um": DEFAULT_TRENCH_HEIGHT_UM,
            "measured_pixels": separation_px,
            "um_per_pixel": um_per_pixel,
            "valid_only_for_this_view_and_zoom": True,
        },
    }


def trench_lines_for_session(session: JsonDict) -> tuple[ReviewedLine, ReviewedLine]:
    """Resolve exactly one reviewed physical top line and one physical floor line."""

    explicit_top = as_dict(session.get("trench_top_line"))
    explicit_floor = as_dict(session.get("trench_floor_line") or session.get("trench_bottom_line"))
    top_lines: list[ReviewedLine] = []
    floor_lines: list[ReviewedLine] = []
    if explicit_top:
        top_lines.append(reviewed_line_from_payload(explicit_top, "trench_top_surface"))
    if explicit_floor:
        floor_lines.append(reviewed_line_from_payload(explicit_floor, "trench_bottom_floor"))

    for item in selected_items(session):
        if str(item.get("shape_kind") or "").strip() != "line":
            continue
        role = normalized_role(item)
        if role not in {"trench_top_surface", "trench_top", "trench_bottom_floor", "trench_floor"}:
            continue
        line = reviewed_line_from_payload(as_dict(item.get("shape")), role, source=str(item.get("source") or "selected_line"))
        if role in {"trench_top_surface", "trench_top"}:
            top_lines.append(line)
        else:
            floor_lines.append(line)

    if len(top_lines) != 1:
        raise ValueError(
            "side review must contain exactly one line with role trench_top_surface; "
            f"found {len(top_lines)}"
        )
    if len(floor_lines) != 1:
        raise ValueError(
            "side review must contain exactly one line with role trench_bottom_floor; "
            f"found {len(floor_lines)}"
        )
    return top_lines[0], floor_lines[0]


def reviewed_line_from_payload(payload: JsonDict, role: str, *, source: str = "reviewed_line") -> ReviewedLine:
    if "y_px" in payload:
        y1 = y2 = finite_float(payload["y_px"], f"{role}.y_px")
        x1 = finite_float(payload.get("x1_px", payload.get("x1", 0.0)), f"{role}.x1_px")
        x2 = finite_float(payload.get("x2_px", payload.get("x2", 0.0)), f"{role}.x2_px")
    else:
        x1 = finite_float(payload.get("x1"), f"{role}.x1")
        x2 = finite_float(payload.get("x2"), f"{role}.x2")
        y1 = finite_float(payload.get("y1"), f"{role}.y1")
        y2 = finite_float(payload.get("y2"), f"{role}.y2")
    return ReviewedLine(
        role=role,
        image_y_px=0.5 * (y1 + y2),
        image_x1_px=x1,
        image_x2_px=x2,
        image_y1_px=y1,
        image_y2_px=y2,
        source=str(payload.get("source") or source),
    )


def validate_reviewed_line(line: ReviewedLine, mirror_roi: JsonDict, label: str) -> None:
    length = abs(line.image_x2_px - line.image_x1_px)
    if length < 5.0:
        raise ValueError(f"{label} is too short to be a reviewed trench line")
    max_vertical_delta = max(3.0, 0.02 * length)
    if abs(line.image_y2_px - line.image_y1_px) > max_vertical_delta:
        raise ValueError(f"{label} is not horizontal enough for side scaling")
    for x, y in (
        (line.image_x1_px, line.image_y1_px),
        (line.image_x2_px, line.image_y2_px),
    ):
        if x < mirror_roi["x1"] or x > mirror_roi["x2"] or y < mirror_roi["y1"] or y > mirror_roi["y2"]:
            raise ValueError(f"{label} endpoint ({x:.6g}, {y:.6g}) is outside the reviewed mirror ROI")


def mirror_point_transform(*, image_x_px: float, image_y_px: float, mirror_roi: JsonDict) -> JsonDict:
    x = finite_float(image_x_px, "image_x_px")
    y = finite_float(image_y_px, "image_y_px")
    roi = normalize_roi(mirror_roi, source=str(mirror_roi.get("source") or "reviewed_mirror_roi"))
    if x < roi["x1"] or x > roi["x2"] or y < roi["y1"] or y > roi["y2"]:
        raise ValueError(f"side feature ({x:.6g}, {y:.6g}) is outside the reviewed mirror ROI")
    local_x = x - roi["x1"]
    local_y = y - roi["y1"]
    return {
        "mirror_roi": roi,
        "full_image": {"image_x_px": x, "image_y_px": y},
        "mirror_local": {"image_x_px": local_x, "image_y_px": local_y},
        "mirror_flipped": {"image_x_px": local_x, "image_y_px": roi["y2"] - y},
    }


def mirror_ball_reference_delta_px(*, ball_y_px: float, reference_y_px: float, mirror_roi: JsonDict) -> JsonDict:
    """Compatibility helper exposing the corrected mirror-local transform."""

    roi = normalize_roi(mirror_roi, source="reviewed_mirror_roi")
    center_x = 0.5 * (roi["x1"] + roi["x2"])
    ball = mirror_point_transform(image_x_px=center_x, image_y_px=ball_y_px, mirror_roi=roi)
    reference = mirror_point_transform(image_x_px=center_x, image_y_px=reference_y_px, mirror_roi=roi)
    return {
        "mirror_roi": roi,
        "full_image": {
            "ball_y_px": finite_float(ball_y_px, "ball_y_px"),
            "reference_y_px": finite_float(reference_y_px, "reference_y_px"),
            "raw_delta_y_px": finite_float(ball_y_px, "ball_y_px")
            - finite_float(reference_y_px, "reference_y_px"),
        },
        "mirror_local": {
            "ball_y_px": ball["mirror_local"]["image_y_px"],
            "reference_y_px": reference["mirror_local"]["image_y_px"],
        },
        "mirror_flipped": {
            "ball_y_px": ball["mirror_flipped"]["image_y_px"],
            "reference_y_px": reference["mirror_flipped"]["image_y_px"],
        },
        "flipped_delta_y_px": ball["mirror_flipped"]["image_y_px"]
        - reference["mirror_flipped"]["image_y_px"],
    }


def verify_final_geometry(params_in: JsonDict) -> JsonDict:
    """Read-only proof of the two final centers and their 700 um spacing."""

    require_schema(params_in)
    memory = load_memory_from_params(params_in)
    ball_1_top = top_ball_measurement(
        memory,
        reference_capture_id="2.4.1",
        ball_capture_id="2.5.1",
        target="ball_1",
        params_in=params_in,
    )
    ball_2_top = top_ball_measurement(
        memory,
        reference_capture_id="4.4.1",
        ball_capture_id="4.5.1",
        target="ball_2",
        params_in=params_in,
    )
    ball_1_y = side_measurement(memory, "2.6.1", "ball_1", params_in)
    ball_2_y = side_measurement(memory, "4.6.2", "ball_2", params_in)
    measured = {
        "ball_1": {
            "machine_x_um": ball_1_top["measured_machine_x_um"],
            "machine_y_um": ball_1_y["measured_machine_y_um"],
            "machine_z_um": ball_1_top["measured_machine_z_um"],
        },
        "ball_2": {
            "machine_x_um": ball_2_top["measured_machine_x_um"],
            "machine_y_um": ball_2_y["measured_machine_y_um"],
            "machine_z_um": ball_2_top["measured_machine_z_um"],
        },
    }
    residuals = {
        target: {
            axis: measured[target][axis] - FINAL_TARGETS_UM[target][axis]
            for axis in MACHINE_AXES
        }
        for target in ("ball_1", "ball_2")
    }
    spacing = measured["ball_2"]["machine_x_um"] - measured["ball_1"]["machine_x_um"]
    spacing_residual = spacing - FINAL_CENTER_SPACING_UM
    tolerance = non_negative_float(params_in.get("final_tolerance_um", 1.0), "final_tolerance_um")
    spacing_tolerance = non_negative_float(
        params_in.get("spacing_tolerance_um", tolerance),
        "spacing_tolerance_um",
    )
    within = all(
        abs(residuals[target][axis]) <= tolerance
        for target in ("ball_1", "ball_2")
        for axis in MACHINE_AXES
    ) and abs(spacing_residual) <= spacing_tolerance
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": True,
        "action": "final_geometry_verified" if within else "final_geometry_out_of_tolerance",
        "status": (
            "final reviewed geometry is within tolerance"
            if within
            else "final reviewed geometry is outside tolerance; read-only verification requested no move"
        ),
        "read_only": True,
        "move_count": 0,
        "stage1": "",
        "target1_um": 0.0,
        "delta1_um": 0.0,
        "move_mode1": "Absolute",
        "target_coordinates_um": deepcopy_json(FINAL_TARGETS_UM),
        "measured_coordinates_um": measured,
        "coordinate_residuals_um": residuals,
        "measured_center_spacing_um": spacing,
        "target_center_spacing_um": FINAL_CENTER_SPACING_UM,
        "spacing_residual_um": spacing_residual,
        "tolerance_um": tolerance,
        "spacing_tolerance_um": spacing_tolerance,
        "diagnostics": {
            "ball_1_top": ball_1_top,
            "ball_2_top": ball_2_top,
            "ball_1_side": ball_1_y,
            "ball_2_side": ball_2_y,
        },
    }


def side_measurement(memory: JsonDict, capture_id: str, target: str, params_in: JsonDict) -> JsonDict:
    record = recorded_capture(memory, capture_id)
    session = as_dict(record.get("session"))
    ball = selected_circle_feature(
        session,
        f"{capture_id} reviewed side ball",
        target=target,
        role_context="side_ball",
    )
    if ball is None:  # pragma: no cover - required=True
        raise ValueError(f"{capture_id} side ball is missing")
    geometry = reviewed_trench_geometry(session, params_in=params_in, capture_id=capture_id)
    ball_transform = mirror_point_transform(
        image_x_px=ball.center_px["x"],
        image_y_px=ball.center_px["y"],
        mirror_roi=geometry["mirror_roi"],
    )
    residual_px = ball_transform["mirror_flipped"]["image_y_px"] - geometry["trench_top_transform"][
        "mirror_flipped"
    ]["image_y_px"]
    return {
        "capture_id": capture_id,
        "target": target,
        "measured_machine_y_um": -residual_px * geometry["um_per_pixel"],
        "residual_flipped_y_px": residual_px,
        "ball_transform": ball_transform,
        "trench_geometry": geometry,
    }


def evaluate_convergence(
    memory: JsonDict,
    *,
    capture_id: str,
    capture_revision: int,
    residuals_um: JsonDict,
    tolerance_um: float,
    params_in: JsonDict,
) -> JsonDict:
    convergence_by_capture = as_dict(memory.setdefault("convergence", {}))
    previous = as_dict(convergence_by_capture.get(capture_id))
    previous_revision = int(previous.get("capture_revision") or 0)
    if previous_revision == capture_revision and previous.get("status") == "move_planned":
        raise ValueError(
            f"capture {capture_id} revision {capture_revision} already produced a correction plan; "
            "apply or abort it, then take a fresh reviewed capture before requesting another correction"
        )
    current_vector = {
        canonical_axis(axis): finite_float(value, f"axis_residuals_um.{axis}")
        for axis, value in residuals_um.items()
    }
    current_max = max((abs(value) for value in current_vector.values()), default=0.0)
    divergence_tolerance = non_negative_float(
        params_in.get("divergence_tolerance_um", DEFAULT_DIVERGENCE_TOLERANCE_UM),
        "divergence_tolerance_um",
    )
    if previous and previous_revision != capture_revision and previous.get("status") == "move_planned":
        previous_max = non_negative_float(previous.get("max_abs_residual_um", 0.0), "previous residual")
        previous_vector = {
            canonical_axis(axis): finite_float(value, f"previous.axis_residuals_um.{axis}")
            for axis, value in as_dict(previous.get("axis_residuals_um")).items()
        }
        diverged_axes = sorted(
            axis
            for axis, value in current_vector.items()
            if axis in previous_vector
            and abs(value) > abs(previous_vector[axis]) + divergence_tolerance
        )
        if diverged_axes or current_max > previous_max + divergence_tolerance:
            state = {
                **deepcopy_json(previous),
                "status": "diverged",
                "capture_revision": capture_revision,
                "previous_max_abs_residual_um": previous_max,
                "previous_axis_residuals_um": previous_vector,
                "max_abs_residual_um": current_max,
                "axis_residuals_um": current_vector,
                "diverged_axes": diverged_axes,
                "updated_at_utc": utc_now_text(),
            }
            convergence_by_capture[capture_id] = state
            memory["convergence"] = convergence_by_capture
            persist_memory(memory, params_in)
            axis_detail = (
                f"; increased axes: {', '.join(diverged_axes)}"
                if diverged_axes
                else ""
            )
            raise ValueError(
                f"{capture_id} residual increased from {previous_max:.6g} to {current_max:.6g} um "
                f"after correction{axis_detail}; possible sign, scale, or feature-selection error"
            )

    attempt_count = int(previous.get("attempt_count") or 0)
    needs_move = any(abs(value) > tolerance_um for value in current_vector.values())
    max_attempts = int(
        positive_float(
            params_in.get("max_correction_attempts", DEFAULT_MAX_CORRECTION_ATTEMPTS),
            "max_correction_attempts",
        )
    )
    if needs_move and attempt_count >= max_attempts:
        state = {
            "capture_id": capture_id,
            "capture_revision": capture_revision,
            "attempt_count": attempt_count,
            "max_attempts": max_attempts,
            "axis_residuals_um": current_vector,
            "max_abs_residual_um": current_max,
            "tolerance_um": tolerance_um,
            "divergence_tolerance_um": divergence_tolerance,
            "status": "max_attempts_exhausted",
            "updated_at_utc": utc_now_text(),
        }
        convergence_by_capture[capture_id] = state
        memory["convergence"] = convergence_by_capture
        persist_memory(memory, params_in)
        raise ValueError(
            f"{capture_id} still requires motion after {max_attempts} reviewed correction attempts; "
            "the final fresh verification capture was not moved"
        )
    if needs_move:
        attempt_count += 1
    state = {
        "capture_id": capture_id,
        "capture_revision": capture_revision,
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "axis_residuals_um": current_vector,
        "max_abs_residual_um": current_max,
        "tolerance_um": tolerance_um,
        "divergence_tolerance_um": divergence_tolerance,
        "status": "move_planned" if needs_move else "converged",
        "updated_at_utc": utc_now_text(),
    }
    convergence_by_capture[capture_id] = state
    memory["convergence"] = convergence_by_capture
    return state


def moves_from_axis_corrections(
    current_positions: JsonDict,
    *,
    tower: str,
    axis_deltas_um: JsonDict,
    max_step_um: float,
    max_total_residual_um: float,
    tolerance_um: float,
    phase: str,
) -> list[PlannedMove]:
    moves: list[PlannedMove] = []
    for raw_axis, raw_delta in axis_deltas_um.items():
        axis = canonical_axis(raw_axis)
        delta = finite_float(raw_delta, f"axis_residuals_um.{axis}")
        if abs(delta) <= tolerance_um:
            continue
        if abs(delta) > max_total_residual_um:
            raise ValueError(
                f"{tower}.{axis} residual {delta:.6g} um exceeds max_total_residual_um "
                f"{max_total_residual_um:.6g}; no move was planned"
            )
        bounded_delta = math.copysign(min(abs(delta), max_step_um), delta)
        current = axis_value(current_positions, tower, axis)
        moves.append(
            PlannedMove(
                stage=STAGE_FOR_AXIS[(tower, axis)],
                target_um=current + bounded_delta,
                delta_um=bounded_delta,
                phase=phase,
            )
        )
    return sorted(moves, key=offset_move_sort_key)


def correction_response(
    *,
    capture_id: str,
    spec: JsonDict,
    correction: JsonDict,
    planned_moves: list[PlannedMove],
    tolerance_um: float,
    max_step_um: float,
    max_total_residual_um: float,
    convergence: JsonDict,
) -> JsonDict:
    diagnostics = {
        "capture_id": capture_id,
        "target": spec["target"],
        "tower": spec["tower"],
        "correction_kind": spec["kind"],
        "tolerance_um": tolerance_um,
        "max_step_um": max_step_um,
        "max_total_residual_um": max_total_residual_um,
        "correction": correction,
        "convergence": convergence,
        "motion_policy": "operator_confirmed_yase_movestage_only",
    }
    if not planned_moves:
        return no_move_response(
            action="no_offset_correction_required",
            status=f"{capture_id} reviewed residual is within tolerance",
            extra={"diagnostics": diagnostics, "capture_id": capture_id},
        )
    return flat_move_response(
        action="offset_correction_move",
        status=(
            f"{capture_id} correction attempt {convergence['attempt_count']}/"
            f"{convergence['max_attempts']} requires {len(planned_moves)} bounded move(s)"
        ),
        moves=planned_moves,
        extra={"diagnostics": diagnostics, "capture_id": capture_id},
    )


def save_correction_plan(memory: JsonDict, capture_id: str, record: JsonDict, response: JsonDict) -> None:
    plans = as_dict(memory.setdefault("correction_plans", {}))
    plan_id = f"{capture_id}:r{int(record.get('revision') or 1)}"
    plans[plan_id] = {
        "plan_id": plan_id,
        "capture_id": capture_id,
        "capture_revision": int(record.get("revision") or 1),
        "action": response.get("action"),
        "planned_moves": deepcopy_json(response.get("planned_moves") or []),
        "diagnostics": deepcopy_json(response.get("diagnostics") or {}),
        "status": "active",
        "created_at_utc": utc_now_text(),
    }
    memory["correction_plans"] = plans
    history = memory.get("correction_history")
    if not isinstance(history, list):
        history = []
    history.append(deepcopy_json(plans[plan_id]))
    memory["correction_history"] = history
    memory["updated_at_utc"] = utc_now_text()


def next_transition_move(params_in: JsonDict) -> JsonDict:
    """Return at most one medium-speed transition target from a fixed anchor."""

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
    if bool(params_in.get("reset_transition_plan")):
        clear_transition_record(memory, transition_id)
    validate_transition_source_prerequisite(
        memory,
        spec=spec,
        current_positions=current_positions,
        tolerance_um=non_negative_float(
            params_in.get("capture_pose_tolerance_um", DEFAULT_CAPTURE_POSE_TOLERANCE_UM),
            "capture_pose_tolerance_um",
        ),
    )
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
    extra = {
        "transition_id": transition_id,
        "from_position_id": from_position["id"],
        "to_position_id": to_position["id"],
        "target_positions_um": target_positions,
        "transition_anchor_positions_um": deepcopy_json(transition_record["anchor_positions_um"]),
        "tower_clearance_y_um": clearance_y_by_tower,
    }
    if not move:
        transition_record["status"] = "complete"
        transition_record["completed_at_utc"] = utc_now_text()
        transition_record["last_checked_positions_um"] = deepcopy_json(current_positions)
        write_transition_memory_if_requested(memory, params_in)
        return no_move_response(
            action="transition_complete",
            status=f"v6 transition {transition_id} is at the rebased target",
            extra=extra,
        )
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
        status=f"v6 transition {transition_id}: move {move.stage}, then rerun this subsequence",
        moves=[move],
        extra={**extra, "next_sequence_after_move": "rerun next_transition_move"},
    )


def rebased_transition_target_positions(
    from_position: JsonDict,
    to_position: JsonDict,
    current_positions: JsonDict,
) -> JsonDict:
    from_targets = position_target_axes(from_position)
    to_targets = position_target_axes(to_position)
    result: JsonDict = {}
    for stage_name, raw_axes in to_targets.items():
        axes: JsonDict = {}
        for axis, raw_to_value in as_dict(raw_axes).items():
            to_value = finite_float(raw_to_value, f"{to_position.get('id')}.{stage_name}.{axis}")
            raw_from_value = as_dict(from_targets.get(stage_name)).get(axis)
            if raw_from_value is None:
                axes[axis] = to_value
            else:
                axes[axis] = axis_value(current_positions, stage_name, axis) + (
                    to_value - finite_float(raw_from_value, f"{from_position.get('id')}.{stage_name}.{axis}")
                )
        if axes:
            result[stage_name] = axes
    return result


def position_target_axes(position: JsonDict) -> JsonDict:
    result: JsonDict = {}
    raw_machine = as_dict(position.get("machine_positions_um"))
    for stage_name in ("camera", "tower_1", "tower_2"):
        raw_axes = as_dict(raw_machine.get(stage_name))
        axes: JsonDict = {}
        for raw_axis, value in raw_axes.items():
            if value is not None:
                axes[canonical_axis(raw_axis)] = finite_float(
                    value,
                    f"{position.get('id')}.{stage_name}.{raw_axis}",
                )
        if axes:
            result[stage_name] = axes
    zoom = camera_setting_value(position, "zoom")
    if zoom is not None:
        result["zoom"] = {"zoom_um": zoom}
    return result


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
        for raw_axis, raw_target in as_dict(raw_axes).items():
            axis = canonical_stage_axis(stage_name, raw_axis)
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
    return sorted(candidates, key=transition_move_sort_key)[0] if candidates else None


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
        lateral = any(
            abs(
                finite_float(target_axes[axis], f"target_positions_um.{tower}.{axis}")
                - axis_value(current_positions, tower, axis)
            )
            > move_tolerance_um
            for axis in ("machine_x_um", "machine_z_um")
            if axis in target_axes
        )
        if not lateral:
            continue
        clearance = clearance_y_by_tower.get(tower)
        target_y = target_axes.get("machine_y_um")
        if clearance is None and target_y is None:
            continue
        clearance_target = finite_float(
            clearance if clearance is not None else target_y,
            f"tower_clearance_y_um.{tower}",
        )
        if target_y is not None:
            clearance_target = max(
                clearance_target,
                finite_float(target_y, f"target_positions_um.{tower}.machine_y_um"),
            )
        current_y = axis_value(current_positions, tower, "machine_y_um")
        delta = clearance_target - current_y
        if delta <= move_tolerance_um:
            continue
        if abs(delta) > max_single_move_um:
            raise ValueError(
                f"transition clearance move {tower}.machine_y_um delta {delta:.6g} um exceeds "
                f"max_single_move_um {max_single_move_um:.6g}"
            )
        candidates.append(
            PlannedMove(
                stage=STAGE_FOR_AXIS[(tower, "machine_y_um")],
                target_um=clearance_target,
                delta_um=delta,
                phase="v6_raise_tower_y_clearance_before_lateral_transition",
            )
        )
    return sorted(candidates, key=transition_move_sort_key)[0] if candidates else None


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
    record = {
        "transition_id": transition_id,
        "status": "in_progress",
        "from_position_id": from_position["id"],
        "to_position_id": to_position["id"],
        "anchor_positions_um": deepcopy_json(current_positions),
        "target_positions_um": rebased_transition_target_positions(
            from_position,
            to_position,
            current_positions,
        ),
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
    if not record or str(record.get("status") or "") not in TRANSITION_STATUSES:
        return None
    if str(record.get("from_position_id") or "") != required_text(spec, "from_position_id"):
        return None
    if str(record.get("to_position_id") or "") != required_text(spec, "to_position_id"):
        return None
    if not as_dict(record.get("target_positions_um")) or not as_dict(record.get("anchor_positions_um")):
        return None
    return record


def validate_transition_source_prerequisite(
    memory: JsonDict | None,
    *,
    spec: JsonDict,
    current_positions: JsonDict,
    tolerance_um: float,
) -> None:
    """Require a current reviewed source pose before anchoring a YASE transition."""

    if memory is None:
        return
    transition_id = f"{required_text(spec, 'from_position_id')}_to_{required_text(spec, 'to_position_id')}"
    if existing_transition_record(memory, transition_id, spec) is not None:
        return

    from_position_id = required_text(spec, "from_position_id")
    matching_capture_ids = [
        capture_id
        for capture_id, capture_spec in CAPTURE_SPECS.items()
        if str(capture_spec["position_id"]) == from_position_id
    ]
    if len(matching_capture_ids) != 1:  # pragma: no cover - constant table guard
        raise ValueError(
            f"transition source position {from_position_id} does not map to exactly one V6 capture"
        )
    capture_id = matching_capture_ids[0]
    record = recorded_capture(memory, capture_id)
    validate_full_pose_matches_capture(
        current_positions,
        record,
        tolerance_um=tolerance_um,
        context=f"transition {transition_id}",
    )
    if capture_id not in OFFSET_SPECS:
        return

    convergence = as_dict(as_dict(memory.get("convergence")).get(capture_id))
    if (
        str(convergence.get("status") or "") != "converged"
        or int(convergence.get("capture_revision") or 0) != int(record.get("revision") or 0)
    ):
        raise ValueError(
            f"transition {transition_id} requires capture {capture_id} revision "
            f"{int(record.get('revision') or 0)} to be converged"
        )


def standard_transition_delta_positions(from_position: JsonDict, to_position: JsonDict) -> JsonDict:
    from_targets = position_target_axes(from_position)
    to_targets = position_target_axes(to_position)
    result: JsonDict = {}
    for stage_name, raw_to_axes in to_targets.items():
        axes: JsonDict = {}
        from_axes = as_dict(from_targets.get(stage_name))
        for axis, raw_to in as_dict(raw_to_axes).items():
            raw_from = from_axes.get(axis)
            if raw_from is not None:
                axes[axis] = finite_float(raw_to, f"{to_position.get('id')}.{stage_name}.{axis}") - finite_float(
                    raw_from,
                    f"{from_position.get('id')}.{stage_name}.{axis}",
                )
        if axes:
            result[stage_name] = axes
    return result


def flat_move_response(
    action: str,
    status: str,
    moves: list[PlannedMove],
    *,
    extra: JsonDict | None = None,
) -> JsonDict:
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


def no_move_response(action: str, status: str, *, extra: JsonDict | None = None) -> JsonDict:
    return flat_move_response(action, status, [], extra=extra)


def confirm_text_for_move(action: str, move: PlannedMove, index: int) -> str:
    label = "reviewed vision offset" if action == "offset_correction_move" else "transition"
    return (
        f"V6 {label} move {index}: confirm {move.stage} absolute target "
        f"{move.target_um:.6g} um (delta {move.delta_um:.6g} um)."
    )


def offset_move_sort_key(move: PlannedMove) -> tuple[int, str]:
    stage_name, axis = AXIS_FOR_STAGE[move.stage]
    if axis == "machine_y_um" and move.delta_um > 0.0:
        return (0, move.stage)
    if axis in {"machine_x_um", "machine_z_um"}:
        return (1, move.stage)
    if axis == "machine_y_um":
        return (2, move.stage)
    return (3, f"{stage_name}.{move.stage}")


def transition_move_sort_key(move: PlannedMove) -> tuple[int, str]:
    stage_name, axis = AXIS_FOR_STAGE[move.stage]
    if stage_name.startswith("tower") and axis == "machine_y_um" and move.delta_um > 0.0:
        return (0, move.stage)
    if stage_name == "camera" and axis in {"machine_x_um", "machine_z_um"}:
        return (10, move.stage)
    if stage_name == "zoom":
        return (20, move.stage)
    if stage_name == "camera" and axis == "machine_y_um":
        return (30, move.stage)
    if stage_name.startswith("tower") and axis in {"machine_x_um", "machine_z_um"}:
        return (40, move.stage)
    if stage_name.startswith("tower") and axis == "machine_y_um":
        return (50, move.stage)
    return (99, move.stage)


def capture_pose_pair(params_in: JsonDict) -> tuple[JsonDict, JsonDict]:
    common = as_dict(params_in.get("machine_positions_um"))
    before_raw = as_dict(params_in.get("machine_positions_before_grab_um"))
    after_raw = as_dict(params_in.get("machine_positions_after_grab_um"))
    before = normalize_machine_positions(before_raw or common or machine_positions_from_flat_params(params_in, "before_"))
    after = normalize_machine_positions(after_raw or common or machine_positions_from_flat_params(params_in, "after_"))
    if not before:
        before = required_machine_positions_payload(params_in)
    if not after:
        after = deepcopy_json(before)
    require_complete_machine_pose(before, "machine_positions_before_grab_um")
    require_complete_machine_pose(after, "machine_positions_after_grab_um")
    return before, after


def validate_capture_stability(before: JsonDict, after: JsonDict, *, tolerance_um: float) -> JsonDict:
    deltas: JsonDict = {}
    maximum = 0.0
    for stage_name, axis in STAGE_FOR_AXIS:
        delta = axis_value(after, stage_name, axis) - axis_value(before, stage_name, axis)
        deltas.setdefault(stage_name, {})[axis] = delta
        maximum = max(maximum, abs(delta))
    if maximum > tolerance_um:
        raise ValueError(
            f"capture rejected: stage motion during grab reached {maximum:.6g} um, "
            f"above stability tolerance {tolerance_um:.6g} um"
        )
    return {
        "stable": True,
        "tolerance_um": tolerance_um,
        "max_abs_delta_um": maximum,
        "axis_deltas_um": deltas,
    }


def validate_current_pose_matches_capture(
    current: JsonDict,
    record: JsonDict,
    *,
    tower: str,
    tolerance_um: float,
) -> None:
    capture_pose = as_dict(record.get("machine_positions_um"))
    if not capture_pose:
        raise ValueError(
            "reviewed capture is missing its exact machine_positions_um; "
            "take a fresh stable reviewed image before correction"
        )
    capture_pose = normalize_machine_positions(capture_pose)
    checked = [
        ("camera", "machine_x_um"),
        ("camera", "machine_z_um"),
        ("zoom", "zoom_um"),
        (tower, "machine_x_um"),
        (tower, "machine_y_um"),
        (tower, "machine_z_um"),
    ]
    stale = []
    for stage_name, axis in checked:
        delta = axis_value(current, stage_name, axis) - axis_value(capture_pose, stage_name, axis)
        if abs(delta) > tolerance_um:
            stale.append(f"{stage_name}.{axis} changed by {delta:.6g} um")
    if stale:
        raise ValueError(
            "capture pose is stale; take a fresh reviewed image before correction: " + "; ".join(stale)
        )


def validate_full_pose_matches_capture(
    current: JsonDict,
    record: JsonDict,
    *,
    tolerance_um: float,
    context: str,
) -> None:
    capture_pose = as_dict(record.get("machine_positions_um"))
    if not capture_pose:
        raise ValueError(
            f"{context} source capture is missing its exact machine_positions_um; "
            "take a fresh stable reviewed image"
        )
    capture_pose = normalize_machine_positions(capture_pose)
    require_complete_machine_pose(capture_pose, f"{context} source capture machine_positions_um")
    stale = []
    for stage_name, axis in STAGE_FOR_AXIS:
        delta = axis_value(current, stage_name, axis) - axis_value(capture_pose, stage_name, axis)
        if abs(delta) > tolerance_um:
            stale.append(f"{stage_name}.{axis} changed by {delta:.6g} um")
    if stale:
        raise ValueError(
            f"{context} source capture pose is stale; re-record it before anchoring: "
            + "; ".join(stale)
        )


def assert_matching_top_calibration_context(
    reference_record: JsonDict,
    ball_record: JsonDict,
    *,
    zoom_tolerance_um: float,
) -> None:
    reference_view = str(reference_record.get("view") or "")
    ball_view = str(ball_record.get("view") or "")
    if reference_view != "fine_top_xz" or ball_view != "fine_top_xz":
        raise ValueError(
            f"top calibration context mismatch: expected fine_top_xz, got {reference_view!r} and {ball_view!r}"
        )
    reference_pose = normalize_machine_positions(as_dict(reference_record.get("machine_positions_um")))
    ball_pose = normalize_machine_positions(as_dict(ball_record.get("machine_positions_um")))
    zoom_delta = zoom_value(ball_pose) - zoom_value(reference_pose)
    if abs(zoom_delta) > zoom_tolerance_um:
        raise ValueError(
            f"top calibration zoom mismatch {zoom_delta:.6g} um exceeds tolerance "
            f"{zoom_tolerance_um:.6g} um; pixel scale cannot be transferred"
        )
    reference_dimensions = as_dict(reference_record.get("image_dimensions_px"))
    ball_dimensions = as_dict(ball_record.get("image_dimensions_px"))
    for key in ("image_width_px", "image_height_px"):
        reference_value = reference_dimensions.get(key)
        ball_value = ball_dimensions.get(key)
        if reference_value is None or ball_value is None:
            raise ValueError(
                f"top calibration context is missing {key}; take fresh reviewed reference and ball captures"
            )
        if int(reference_value) != int(ball_value):
            raise ValueError(
                f"top calibration image-size mismatch for {key}: "
                f"{reference_value} vs {ball_value}"
            )


def invalidate_dependent_plans(memory: JsonDict, capture_id: str, revision: int) -> list[str]:
    dependent_capture_ids = {capture_id}
    for candidate_id, spec in OFFSET_SPECS.items():
        if spec.get("reference_capture_id") == capture_id:
            dependent_capture_ids.add(candidate_id)
    invalidated: list[str] = []
    plans = as_dict(memory.get("correction_plans"))
    for plan_id, raw_plan in plans.items():
        plan = as_dict(raw_plan)
        if str(plan.get("capture_id") or "") in dependent_capture_ids and plan.get("status") == "active":
            plan["status"] = "invalidated"
            plan["invalidated_by_capture_id"] = capture_id
            plan["invalidated_by_revision"] = revision
            plan["invalidated_at_utc"] = utc_now_text()
            plans[plan_id] = plan
            invalidated.append(plan_id)
    memory["correction_plans"] = plans
    position_id = str(CAPTURE_SPECS[capture_id]["position_id"])
    invalidated.extend(clear_transition_records_from_position(memory, position_id))
    if memory.pop("final_verification", None) is not None:
        invalidated.append("final_verification")
    if invalidated:
        history = memory.get("invalidation_history")
        if not isinstance(history, list):
            history = []
        history.append(
            {
                "capture_id": capture_id,
                "capture_revision": revision,
                "invalidated_plan_ids": sorted(set(invalidated)),
                "timestamp_utc": utc_now_text(),
            }
        )
        memory["invalidation_history"] = history
    return sorted(set(invalidated))


def clear_transition_records_from_position(memory: JsonDict, position_id: str) -> list[str]:
    records = as_dict(memory.get("transition_records"))
    removed = [
        transition_id
        for transition_id, record in records.items()
        if str(as_dict(record).get("from_position_id") or "") == position_id
    ]
    for transition_id in removed:
        records.pop(transition_id, None)
    memory["transition_records"] = records
    return [f"transition:{transition_id}" for transition_id in removed]


def clear_transition_record(memory: JsonDict | None, transition_id: str) -> None:
    if memory is None:
        return
    records = as_dict(memory.get("transition_records"))
    records.pop(transition_id, None)
    memory["transition_records"] = records
    memory["updated_at_utc"] = utc_now_text()


def load_transition_memory_if_available(params_in: JsonDict) -> JsonDict | None:
    if isinstance(params_in.get("memory"), dict) or params_in.get("memory_path"):
        return load_or_initialize_memory(params_in)
    return None


def write_transition_memory_if_requested(memory: JsonDict | None, params_in: JsonDict) -> None:
    if memory is not None:
        persist_memory(memory, params_in)


def persist_memory(memory: JsonDict, params_in: JsonDict) -> None:
    memory["schema_version"] = SCHEMA_VERSION
    memory["updated_at_utc"] = utc_now_text()
    write_json_if_requested(memory, params_in.get("memory_output_path") or params_in.get("memory_path"))


def standard_session_for_capture(capture_id: str, memory: JsonDict, params_in: JsonDict) -> JsonDict:
    inline = as_dict(memory.get("standard_baselines")).get(capture_id)
    if isinstance(inline, dict) and inline:
        return deepcopy_json(inline)
    baseline_dir = Path(
        str(
            params_in.get("standard_baseline_dir")
            or memory.get("standard_baseline_dir")
            or DEFAULT_STANDARD_BASELINE_DIR
        )
    )
    if not baseline_dir.is_absolute():
        baseline_dir = Path.cwd() / baseline_dir
    path = baseline_dir / f"{capture_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"standard baseline for {capture_id} was not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def recorded_capture(memory: JsonDict, capture_id: str) -> JsonDict:
    record = as_dict(as_dict(memory.get("capture_records")).get(capture_id))
    if not record or not as_dict(record.get("session")):
        raise ValueError(f"v6 memory does not contain a reviewed capture record for {capture_id}")
    return record


def recorded_session(memory: JsonDict, capture_id: str) -> JsonDict:
    return as_dict(recorded_capture(memory, capture_id).get("session"))


def validate_reviewed_capture_session(capture_id: str, session: JsonDict, params_in: JsonDict) -> None:
    if not reviewed_session_has_selected_shapes(session):
        raise ValueError(f"operator review for {capture_id} saved no selected shapes")
    spec = CAPTURE_SPECS[capture_id]
    if spec["result_use"] == "laser_reference_registration":
        selected_rectangle_feature(session, DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM)
        return
    role_context = {
        "coarse_offset_correction": "gross_ball",
        "top_fine_offset_correction": "top_ball",
        "side_mirror_y_offset_correction": "side_ball",
    }[str(spec["result_use"])]
    selected_circle_feature(
        session,
        f"{capture_id} reviewed ball",
        target=str(spec["target"]),
        role_context=role_context,
    )
    if spec["result_use"] == "side_mirror_y_offset_correction":
        reviewed_trench_geometry(session, params_in=params_in, capture_id=capture_id)


def review_was_cancelled(session: JsonDict) -> bool:
    if not session:
        return True
    if session.get("ok") is False:
        return True
    return str(session.get("action") or "").strip() in {
        "cancel",
        "vision_lab_cancelled",
        "closed_without_save",
    }


def open_v6_vision_review_ui(
    image_path: str,
    *,
    capture_id: str,
    initial_session: JsonDict,
    roi_output_path: Any = None,
    result_output_path: Any = None,
) -> JsonDict:
    try:
        from migrations.migration_v6.vision_recognition_lab import run_vision_recognition_lab_session
    except ImportError:
        from vision_recognition_lab import run_vision_recognition_lab_session  # type: ignore[no-redef]
    return run_vision_recognition_lab_session(
        image_path,
        roi_output_path=roi_output_path,
        result_output_path=result_output_path,
        initial_session=initial_session,
        capture_id=capture_id,
    )


def mirror_roi_for_session(
    session: JsonDict,
    params_in: JsonDict,
    capture_id: str,
    *,
    required: bool,
) -> JsonDict:
    mirror_rois = as_dict(params_in.get("mirror_rois"))
    raw_roi = mirror_rois.get(capture_id) or params_in.get("mirror_roi") or session.get("mirror_roi")
    if raw_roi:
        return normalize_roi(raw_roi, source="reviewed_mirror_roi")
    if required:
        raise ValueError("side review must include a reviewed mirror_roi; no Y correction was planned")
    return {}


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
                item = as_dict(value)
                if normalized_role(item) != "ignore":
                    yield item


def normalized_role(item: JsonDict) -> str:
    for key in ("feature_role", "semantic_role", "role", "target_role"):
        value = str(item.get(key) or "").strip()
        if value:
            return value.lower().replace("-", "_").replace(" ", "_")
    return ""


def line_payload(line: ReviewedLine) -> JsonDict:
    return {
        "role": line.role,
        "image_x1_px": line.image_x1_px,
        "image_y1_px": line.image_y1_px,
        "image_x2_px": line.image_x2_px,
        "image_y2_px": line.image_y2_px,
        "image_y_px": line.image_y_px,
        "source": line.source,
    }


def image_point(point: JsonDict) -> JsonDict:
    return {
        "image_x_px": finite_float(point.get("x"), "point.x"),
        "image_y_px": finite_float(point.get("y"), "point.y"),
    }


def ball_um_per_pixel(radius_px: float | None) -> float:
    if radius_px is None:
        raise ValueError("selected ball circle must include radius for same-view gross scaling")
    return DEFAULT_BALL_DIAMETER_UM / (2.0 * positive_float(radius_px, "ball.radius_px"))


def scale_source_for_capture(capture_id: str) -> JsonDict:
    use = CAPTURE_SPECS[capture_id]["result_use"]
    if use == "coarse_offset_correction":
        return {"kind": "ball_diameter", "known_distance_um": DEFAULT_BALL_DIAMETER_UM}
    if use == "laser_reference_registration":
        return {
            "kind": "laser_rectangle_short_edge",
            "known_distance_um": DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM,
        }
    if use == "top_fine_offset_correction":
        return {"kind": "paired_laser_reference_capture", "valid_only_same_view_and_zoom": True}
    return {
        "kind": "trench_top_to_floor",
        "known_distance_um": DEFAULT_TRENCH_HEIGHT_UM,
        "mirror_flip_y": True,
    }


def capture_settings_payload(params_in: JsonDict, pose: JsonDict) -> JsonDict:
    settings = as_dict(params_in.get("camera_settings"))
    result: JsonDict = {"zoom_um": zoom_value(pose)}
    if settings.get("source"):
        result["source"] = str(settings["source"])
    aliases = {
        "exposure": ("exposure", "exposure_ms", "camera_exposure"),
        "Illu_Coax": ("Illu_Coax", "illu_coax"),
        "Illu_1": ("Illu_1", "illu_1"),
        "Illu_2": ("Illu_2", "illu_2"),
    }
    for output_name, keys in aliases.items():
        raw = next(
            (
                settings.get(key, params_in.get(key))
                for key in keys
                if settings.get(key, params_in.get(key)) is not None
            ),
            None,
        )
        if isinstance(raw, dict):
            raw = raw.get("value")
        if raw is not None:
            result[output_name] = finite_float(raw, f"camera_settings.{output_name}")
    return result


def image_dimensions_payload(image_path: str, session: JsonDict) -> JsonDict:
    dimensions = as_dict(session.get("image_dimensions_px"))
    width = dimensions.get("image_width_px") or session.get("image_width_px")
    height = dimensions.get("image_height_px") or session.get("image_height_px")
    if width is not None and height is not None:
        return {"image_width_px": int(width), "image_height_px": int(height)}
    path = Path(image_path)
    if path.is_file():
        try:
            from PIL import Image

            with Image.open(path) as image:
                width_value, height_value = image.size
            return {"image_width_px": int(width_value), "image_height_px": int(height_value)}
        except Exception:
            pass
    return {"image_width_px": None, "image_height_px": None}


def required_machine_positions_payload(params_in: JsonDict) -> JsonDict:
    raw = as_dict(params_in.get("machine_positions_um"))
    if not raw:
        raw = machine_positions_from_flat_params(params_in)
    normalized = normalize_machine_positions(raw)
    if not normalized:
        raise ValueError("machine_positions_um or flat machine-position fields are required")
    require_complete_machine_pose(normalized, "machine_positions_um")
    return normalized


def normalize_machine_positions(raw_positions: JsonDict) -> JsonDict:
    if not raw_positions:
        return {}
    result: JsonDict = {}
    for stage_name in ("camera", "tower_1", "tower_2"):
        raw_axes = as_dict(raw_positions.get(stage_name))
        axes: JsonDict = {}
        for raw_axis, value in raw_axes.items():
            if value is None:
                continue
            axis = canonical_axis(raw_axis)
            axes[axis] = finite_float(value, f"machine_positions_um.{stage_name}.{raw_axis}")
        if axes:
            result[stage_name] = axes
    raw_zoom = as_dict(raw_positions.get("zoom"))
    if raw_zoom:
        value = raw_zoom.get("zoom_um", raw_zoom.get("value"))
        if value is not None:
            result["zoom"] = {"zoom_um": finite_float(value, "machine_positions_um.zoom.zoom_um")}
    elif raw_positions.get("zoom_um") is not None:
        result["zoom"] = {"zoom_um": finite_float(raw_positions["zoom_um"], "machine_positions_um.zoom_um")}
    return result


def machine_positions_from_flat_params(params_in: JsonDict, prefix: str = "") -> JsonDict:
    result: JsonDict = {}
    for stage_name in ("camera", "tower_1", "tower_2"):
        axes: JsonDict = {}
        for short_axis, canonical in (("x", "machine_x_um"), ("y", "machine_y_um"), ("z", "machine_z_um")):
            keys = (
                f"{prefix}{stage_name}_{canonical}",
                f"{prefix}{stage_name}_{short_axis}_um",
                f"{prefix}{stage_name}.{canonical}",
                f"{prefix}{stage_name}.{short_axis}",
                f"{prefix}{stage_name}_{short_axis}",
            )
            for key in keys:
                if params_in.get(key) is not None:
                    axes[canonical] = finite_float(params_in[key], key)
                    break
        if axes:
            result[stage_name] = axes
    for key in (f"{prefix}zoom_um", f"{prefix}zoom_value"):
        if params_in.get(key) is not None:
            result["zoom"] = {"zoom_um": finite_float(params_in[key], key)}
            break
    return result


def require_complete_machine_pose(positions: JsonDict, label: str) -> None:
    missing = []
    for stage_name in ("camera", "tower_1", "tower_2"):
        for axis in MACHINE_AXES:
            if as_dict(positions.get(stage_name)).get(axis) is None:
                missing.append(f"{stage_name}.{axis}")
    if as_dict(positions.get("zoom")).get("zoom_um") is None:
        missing.append("zoom.zoom_um")
    if missing:
        raise ValueError(f"{label} is missing: {', '.join(missing)}")


def axis_value(machine_positions: JsonDict, stage_name: str, axis: str) -> float:
    canonical = canonical_stage_axis(stage_name, axis)
    value = as_dict(machine_positions.get(stage_name)).get(canonical)
    if value is None:
        raise ValueError(f"current machine position is missing {stage_name}.{canonical}")
    return finite_float(value, f"machine_positions_um.{stage_name}.{canonical}")


def zoom_value(machine_positions: JsonDict) -> float:
    return axis_value(machine_positions, "zoom", "zoom_um")


def canonical_axis(axis: str) -> str:
    value = AXIS_ALIASES.get(str(axis))
    if value is None:
        raise ValueError(f"unsupported machine axis alias {axis!r}")
    return value


def canonical_stage_axis(stage_name: str, axis: str) -> str:
    if stage_name == "zoom":
        if axis in {"zoom_um", "value"}:
            return "zoom_um"
        raise ValueError(f"unsupported zoom axis alias {axis!r}")
    return canonical_axis(axis)


def load_or_initialize_memory(params_in: JsonDict) -> JsonDict:
    try:
        return load_memory_from_params(params_in)
    except FileNotFoundError:
        return initialize_v6_memory(params_in)


def load_memory_from_params(params_in: JsonDict) -> JsonDict:
    raw_memory = params_in.get("memory")
    if isinstance(raw_memory, dict):
        memory = deepcopy_json(raw_memory)
    else:
        raw_path = params_in.get("memory_path")
        if not raw_path:
            raise ValueError("memory or memory_path is required")
        path = Path(str(raw_path))
        if not path.is_absolute():
            path = Path.cwd() / path
        memory = json.loads(path.read_text(encoding="utf-8"))
    memory = upgrade_legacy_memory(memory)
    require_memory(memory)
    return memory


def upgrade_legacy_memory(memory: JsonDict) -> JsonDict:
    version = int(memory.get("schema_version") or 0)
    if version == SCHEMA_VERSION:
        return memory
    if version != LEGACY_MEMORY_SCHEMA_VERSION or str(memory.get("action") or "") != MEMORY_ACTION:
        return memory
    upgraded = deepcopy_json(memory)
    upgraded["schema_version"] = SCHEMA_VERSION
    upgraded.setdefault("capture_history", {})
    upgraded.setdefault("correction_plans", {})
    upgraded.setdefault("correction_history", [])
    upgraded.setdefault("convergence", {})
    upgraded.setdefault("transition_records", {})
    upgraded.setdefault("invalidation_history", [])
    upgraded.setdefault("final_targets_um", deepcopy_json(FINAL_TARGETS_UM))
    upgraded.setdefault("final_center_spacing_um", FINAL_CENTER_SPACING_UM)
    for capture_id, raw_record in as_dict(upgraded.get("capture_records")).items():
        record = as_dict(raw_record)
        if as_dict(record.get("machine_positions_um")):
            record["machine_positions_um"] = normalize_machine_positions(as_dict(record["machine_positions_um"]))
        record.setdefault("revision", 1)
        record.setdefault("view", as_dict(CAPTURE_SPECS.get(capture_id)).get("view"))
    upgraded["status"] = "legacy v6 memory normalized to schema version 2"
    return upgraded


def load_standard_positions(params_in: JsonDict) -> dict[str, JsonDict]:
    if isinstance(params_in.get("standard_positions"), dict):
        payload = as_dict(params_in["standard_positions"])
    else:
        memory: JsonDict = {}
        try:
            memory = load_memory_from_params(params_in)
        except Exception:
            pass
        path = Path(
            str(
                params_in.get("standard_positions_path")
                or memory.get("standard_positions_path")
                or DEFAULT_STANDARD_POSITIONS_PATH
            )
        )
        if not path.is_absolute():
            path = Path.cwd() / path
        payload = json.loads(path.read_text(encoding="utf-8"))
    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, list):
        raise ValueError("standard positions payload must contain a positions list")
    return {
        str(position["id"]): as_dict(position)
        for position in raw_positions
        if isinstance(position, dict) and str(position.get("id") or "").strip()
    }


def tower_clearance_y_by_tower(params_in: JsonDict, standard_positions: dict[str, JsonDict]) -> JsonDict:
    override = as_dict(params_in.get("tower_clearance_y_um"))
    result: JsonDict = {}
    for tower in ("tower_1", "tower_2"):
        if override.get(tower) is not None:
            result[tower] = finite_float(override[tower], f"tower_clearance_y_um.{tower}")
            continue
        values = []
        for position in standard_positions.values():
            raw = as_dict(as_dict(position.get("machine_positions_um")).get(tower))
            if raw.get("y") is not None:
                values.append(finite_float(raw["y"], f"{position.get('id')}.{tower}.y"))
            elif raw.get("machine_y_um") is not None:
                values.append(
                    finite_float(raw["machine_y_um"], f"{position.get('id')}.{tower}.machine_y_um")
                )
        if values:
            result[tower] = max(values)
    return result


def camera_setting_value(position: JsonDict, key: str) -> float | None:
    raw = as_dict(position.get("camera_settings")).get(key)
    if isinstance(raw, dict):
        raw = raw.get("value")
    return None if raw is None else finite_float(raw, f"{position.get('id')}.camera_settings.{key}")


def reviewed_session_has_selected_shapes(session: JsonDict) -> bool:
    return any(True for _ in selected_items(session))


def memory_summary(memory: JsonDict) -> JsonDict:
    return {
        "schema_version": SCHEMA_VERSION,
        "capture_count": len(as_dict(memory.get("capture_records"))),
        "recorded_capture_ids": sorted(as_dict(memory.get("capture_records"))),
        "capture_history_count": sum(
            len(value) for value in as_dict(memory.get("capture_history")).values() if isinstance(value, list)
        ),
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
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def require_memory(memory: JsonDict) -> None:
    if int(memory.get("schema_version") or 0) != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported memory schema_version {memory.get('schema_version')!r}; expected {SCHEMA_VERSION}"
        )
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
    response = no_move_response("abort", message)
    response["ok"] = False
    if traceback_text:
        response["traceback"] = traceback_text
    return response


def _parse_args(argv: Sequence[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Run one V6 reviewed-vision JSON command.")
    parser.add_argument("input_json", help="ParamIn payload path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> JsonDict:
    args = _parse_args(argv)
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    result = run_v6_vision_workflow(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":  # pragma: no cover - manual CLI helper
    main()
