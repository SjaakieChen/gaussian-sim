"""Plan bounded close-position targets from gross v5 vision measurements.

This module compares a live gross capture such as 2.1 or 4.1 against a reviewed
official baseline for the same capture. The measured ball-center pixel shift is
converted into a small tower-only bias for the later close/focus positions.

It does not move hardware.
"""

from __future__ import annotations

import copy
import json
import math
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - used on developer machines without TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback so the planner can be tested outside TestMaster."""


SCHEMA_VERSION = 1
DEFAULT_MAX_BIAS_UM = 350.0
DEFAULT_FAIL_GROSS_OFFSET_UM = 900.0
DEFAULT_GROSS_VIEW_MAPPING = {
    "image_x": {"tower_axis": "z", "sign": 1.0},
    "image_y": {"tower_axis": "x", "sign": 1.0},
}
DEFAULT_STANDARD_POSITIONS_PATH = Path(__file__).resolve().parents[3] / "Standard position images" / "v4" / "standard_positions.json"
DEFAULT_GROSS_AUTO_FEATURE_SPECS: dict[str, dict[str, Any]] = {
    "2.1.1": {
        "roi": [1100, 1500, 1700, 1944],
        "min_radius_px": 30,
        "max_radius_px": 100,
        "expected_center_px": [1275.5, 1713.5],
    },
    "4.1.1": {
        "roi": [1700, 550, 2250, 1050],
        "min_radius_px": 30,
        "max_radius_px": 170,
        "expected_center_px": [1984.5, 869.5],
    },
}
TARGET_TOWER = {"ball_1": "tower_1", "ball_2": "tower_2"}
DEFAULT_CLOSE_POSITION_IDS = {
    "ball_1": ("2.2", "2.3", "2.4", "2.5", "2.6"),
    "ball_2": ("4.2", "4.3", "4.4", "4.5", "4.6.1", "4.6.2"),
}
TARGET_GROSS_CAPTURE_ID = {"ball_1": "2.1.1", "ball_2": "4.1.1"}
TOWER_AXES = {"x", "y", "z"}

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class BiasAxis:
    tower_axis: str
    sign: float


@dataclass(frozen=True)
class GrossMapping:
    image_x: BiasAxis
    image_y: BiasAxis


class VisionPositionBiasPlannerStep(TMPythonStatementJ):
    """TMPython entrypoint for read-only close-position bias planning."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return plan_biased_close_positions(params_in)
        except Exception as exc:  # fail closed for machine callers
            return abort_response(f"VisionPositionBiasPlannerStep failed: {exc}", traceback_text=traceback.format_exc())


def plan_biased_close_positions(params_in: JsonDict) -> JsonDict:
    try:
        require_schema(params_in)
        standard_positions = load_standard_positions(params_in)
        bias_settings = as_dict(params_in.get("default_close_position_bias"))
        max_bias_um = positive_float(bias_settings.get("max_bias_um", DEFAULT_MAX_BIAS_UM), "max_bias_um")
        fail_gross_offset_um = positive_float(
            bias_settings.get("fail_if_gross_offset_exceeds_um", DEFAULT_FAIL_GROSS_OFFSET_UM),
            "fail_if_gross_offset_exceeds_um",
        )
        if max_bias_um > fail_gross_offset_um:
            raise ValueError("max_bias_um must be less than or equal to fail_if_gross_offset_exceeds_um")
        default_mapping = parse_gross_mapping(
            params_in.get("gross_view_mapping")
            or as_dict(params_in.get("bias_mappings")).get("gross_dual")
            or bias_settings.get("default_gross_view_mapping")
            or DEFAULT_GROSS_VIEW_MAPPING
        )

        raw_observations = params_in.get("gross_observations")
        if not isinstance(raw_observations, list) or not raw_observations:
            raise ValueError("gross_observations must be a non-empty list")

        plans = [
            plan_one_gross_observation(
                as_dict(raw_observation),
                index=index,
                params_in=params_in,
                standard_positions=standard_positions,
                mapping=default_mapping,
                max_bias_um=max_bias_um,
                fail_gross_offset_um=fail_gross_offset_um,
            )
            for index, raw_observation in enumerate(raw_observations, start=1)
        ]
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "biased_close_positions_planned",
            "status": "v5 close-position bias planned",
            "plans": plans,
            "thresholds": {
                "max_bias_um": max_bias_um,
                "fail_if_gross_offset_exceeds_um": fail_gross_offset_um,
            },
        }
    except Exception as exc:
        return abort_response(str(exc))


def plan_one_gross_observation(
    observation: JsonDict,
    *,
    index: int,
    params_in: JsonDict,
    standard_positions: dict[str, JsonDict],
    mapping: GrossMapping,
    max_bias_um: float,
    fail_gross_offset_um: float,
) -> JsonDict:
    target = str(observation.get("target") or "").strip()
    if target not in TARGET_TOWER:
        raise ValueError(f"gross_observations[{index}].target must be ball_1 or ball_2")
    gross_capture_id = str(observation.get("gross_capture_id") or observation.get("capture_id") or "").strip()
    expected_capture_id = TARGET_GROSS_CAPTURE_ID[target]
    if gross_capture_id and gross_capture_id != expected_capture_id:
        raise ValueError(
            f"gross_observations[{index}].gross_capture_id must be {expected_capture_id} for {target}"
        )

    baseline_session = resolve_gross_session(
        params_in,
        observation,
        session_key="baseline_session",
        image_path_key="baseline_image_path",
        capture_id=gross_capture_id or expected_capture_id,
        required=True,
        label=f"gross_observations[{index}].baseline_session",
    )
    candidate_session = resolve_gross_session(
        params_in,
        observation,
        session_key="candidate_session",
        image_path_key="candidate_image_path",
        capture_id=gross_capture_id or expected_capture_id,
        required=True,
        label=f"gross_observations[{index}].candidate_session",
    )
    baseline_center = selected_ball_center_px(
        baseline_session,
        f"gross_observations[{index}].baseline_session",
    )
    candidate_center = selected_ball_center_px(
        candidate_session,
        f"gross_observations[{index}].candidate_session",
    )
    baseline_radius_px = selected_ball_radius_px(
        baseline_session,
        f"gross_observations[{index}].baseline_session",
    )
    candidate_radius_px = selected_ball_radius_px(
        candidate_session,
        f"gross_observations[{index}].candidate_session",
    )
    pixel_shift = {
        "x": candidate_center["x"] - baseline_center["x"],
        "y": candidate_center["y"] - baseline_center["y"],
    }
    um_per_pixel = scale_um_per_pixel(
        observation,
        index,
        baseline_session=baseline_session,
        candidate_session=candidate_session,
    )
    bias_mapping = gross_mapping_payload(mapping)
    bias_mapping_evidence = bias_mapping_evidence_payload(params_in, observation, target=target)
    raw_bias = map_pixel_shift_to_tower_bias(pixel_shift, um_per_pixel, mapping)
    largest_raw_bias = max((abs(value) for value in raw_bias.values()), default=0.0)
    if largest_raw_bias > fail_gross_offset_um:
        raise ValueError(
            f"{target} gross offset {largest_raw_bias:.6g} um exceeds fail_if_gross_offset_exceeds_um "
            f"{fail_gross_offset_um:.6g} um"
        )
    applied_bias = {
        axis: float(max(-max_bias_um, min(max_bias_um, value))) for axis, value in raw_bias.items()
    }
    clipped_axes = sorted(axis for axis, value in raw_bias.items() if not math.isclose(value, applied_bias[axis]))
    close_position_ids = close_ids_for_observation(observation, target)
    gross_position_id = position_id_from_capture_id(gross_capture_id or expected_capture_id)
    gross_standard_position = standard_positions.get(gross_position_id)
    candidate_machine_positions = as_dict(observation.get("candidate_machine_positions_um"))
    planned_positions = [
        biased_position_payload(
            standard_positions[position_id],
            target=target,
            applied_bias_um=applied_bias,
            bias_mapping=bias_mapping,
            bias_mapping_evidence=bias_mapping_evidence,
            gross_standard_position=gross_standard_position,
            candidate_gross_machine_positions=candidate_machine_positions,
        )
        for position_id in close_position_ids
    ]
    return {
        "target": target,
        "tower": TARGET_TOWER[target],
        "gross_capture_id": gross_capture_id or expected_capture_id,
        "baseline_center_px": baseline_center,
        "candidate_center_px": candidate_center,
        "baseline_radius_px": baseline_radius_px,
        "candidate_radius_px": candidate_radius_px,
        "baseline_session_auto_detected": bool(baseline_session.get("auto_detected")),
        "candidate_session_auto_detected": bool(candidate_session.get("auto_detected")),
        "pixel_shift": pixel_shift,
        "um_per_pixel": um_per_pixel,
        "bias_mapping": bias_mapping,
        "bias_mapping_evidence": bias_mapping_evidence,
        "raw_bias_um": raw_bias,
        "applied_bias_um": applied_bias,
        "bias_clipped": bool(clipped_axes),
        "clipped_axes": clipped_axes,
        "close_position_ids": list(close_position_ids),
        "planned_positions": planned_positions,
    }


def selected_ball_center_px(session: JsonDict, name: str) -> dict[str, float]:
    selected = as_dict(session.get("selected_recognition"))
    selected_items = []
    for roi_key in sorted(selected, key=roi_sort_key):
        raw_items = selected.get(roi_key)
        if isinstance(raw_items, list):
            selected_items.extend(as_dict(item) for item in raw_items)
    for item in selected_items:
        shape_kind = str(item.get("shape_kind") or "").strip()
        if shape_kind not in {"circle", "silhouette_circle"}:
            continue
        shape = as_dict(item.get("shape"))
        if "x" in shape and "y" in shape:
            return {
                "x": finite_float(shape["x"], f"{name}.selected_recognition.shape.x"),
                "y": finite_float(shape["y"], f"{name}.selected_recognition.shape.y"),
            }
        center = as_dict(shape.get("center"))
        if "x" in center and "y" in center:
            return {
                "x": finite_float(center["x"], f"{name}.selected_recognition.shape.center.x"),
                "y": finite_float(center["y"], f"{name}.selected_recognition.shape.center.y"),
            }

    relative = as_dict(session.get("relative_measurement"))
    origin = as_dict(relative.get("origin_circle"))
    center_px = as_dict(origin.get("center_px"))
    if "x" in center_px and "y" in center_px:
        return {
            "x": finite_float(center_px["x"], f"{name}.relative_measurement.origin_circle.center_px.x"),
            "y": finite_float(center_px["y"], f"{name}.relative_measurement.origin_circle.center_px.y"),
        }
    raise ValueError(f"{name} must contain a selected ball circle center")


def resolve_gross_session(
    params_in: JsonDict,
    observation: JsonDict,
    *,
    session_key: str,
    image_path_key: str,
    capture_id: str,
    required: bool,
    label: str,
) -> JsonDict:
    inline = observation.get(session_key)
    if isinstance(inline, dict) and inline:
        return inline
    image_path = observation.get(image_path_key)
    if image_path is None:
        image_paths = as_dict(params_in.get(image_path_key + "s"))
        image_path = image_paths.get(capture_id)
    if image_path is not None:
        return auto_detect_gross_ball_session(params_in, capture_id, image_path)
    if bool(params_in.get("auto_detect_gross_sessions")) or bool(params_in.get("auto_detect_missing_sessions")):
        return auto_detect_gross_ball_session(params_in, capture_id, standard_capture_image_path(params_in, capture_id))
    if required:
        raise ValueError(f"{label} must contain a selected ball circle center or {image_path_key}")
    return {}


def auto_detect_gross_ball_session(params_in: JsonDict, capture_id: str, image_path: str | Path) -> JsonDict:
    spec = gross_auto_feature_spec(params_in, capture_id)
    circle = auto_detect_gross_ball_circle(read_grayscale_u8(image_path), spec, capture_id)
    roi = roi_payload(spec["roi"], "circle")
    return {
        "schema_version": 3,
        "ok": True,
        "action": "auto_gross_ball_session",
        "status": f"auto detected gross ball for {capture_id}",
        "image_path": str(image_path),
        "standard_capture_id": capture_id,
        "auto_detected": True,
        "auto_feature_kind": "gross_ball_circle",
        "rois": [roi],
        "selected_recognition": {
            "roi_1": [
                {
                    "roi_index": 1,
                    "shape_kind": "circle",
                    "source": "auto_gross_ball_circle",
                    "roi": roi,
                    "shape": circle,
                }
            ]
        },
    }


def gross_auto_feature_spec(params_in: JsonDict, capture_id: str) -> JsonDict:
    raw_specs = as_dict(params_in.get("gross_auto_feature_specs"))
    spec = dict(DEFAULT_GROSS_AUTO_FEATURE_SPECS.get(capture_id) or {})
    spec.update(as_dict(raw_specs.get(capture_id)))
    if not spec:
        raise ValueError(f"no gross auto feature spec is configured for {capture_id}")
    roi = spec.get("roi")
    if not isinstance(roi, list) or len(roi) != 4:
        raise ValueError(f"gross auto feature spec for {capture_id} must include roi [x1, y1, x2, y2]")
    spec["roi"] = [finite_float(value, f"{capture_id}.gross_auto_feature.roi") for value in roi]
    expected = spec.get("expected_center_px")
    if expected is not None:
        if not isinstance(expected, list) or len(expected) != 2:
            raise ValueError(f"gross auto feature spec for {capture_id} expected_center_px must be [x, y]")
        spec["expected_center_px"] = [
            finite_float(expected[0], f"{capture_id}.gross_auto_feature.expected_center_px.x"),
            finite_float(expected[1], f"{capture_id}.gross_auto_feature.expected_center_px.y"),
        ]
    return spec


def auto_detect_gross_ball_circle(gray: Any, spec: JsonDict, capture_id: str) -> JsonDict:
    import cv2

    x1, y1, x2, y2 = clipped_roi(spec["roi"], gray.shape[1], gray.shape[0], capture_id)
    crop = gray[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"{capture_id} gross ball ROI is empty")
    blur = cv2.medianBlur(crop, 5)
    min_radius = int(spec.get("min_radius_px", 1))
    max_radius = int(spec.get("max_radius_px", 999999))
    min_dist = int(spec.get("min_dist_px", max(80, min_radius)))
    param1_values = tuple(spec.get("param1_values") or (40, 60, 80, 100))
    param2_values = tuple(spec.get("param2_values") or (10, 14, 18, 22, 26, 30, 36, 44))
    dp_values = tuple(spec.get("dp_values") or (1.0, 1.2, 1.5, 2.0))
    candidates: list[dict[str, float]] = []
    for dp in dp_values:
        for param1 in param1_values:
            for param2 in param2_values:
                circles = cv2.HoughCircles(
                    blur,
                    cv2.HOUGH_GRADIENT,
                    dp=float(dp),
                    minDist=min_dist,
                    param1=float(param1),
                    param2=float(param2),
                    minRadius=min_radius,
                    maxRadius=max_radius,
                )
                if circles is None:
                    continue
                for circle in circles[0]:
                    candidates.append(
                        {
                            "x": float(circle[0] + x1),
                            "y": float(circle[1] + y1),
                            "radius": float(circle[2]),
                            "dp": float(dp),
                            "param1": float(param1),
                            "param2": float(param2),
                        }
                    )
                return gross_circle_payload(best_gross_circle_candidate(candidates, spec), capture_id)
    raise ValueError(f"{capture_id} auto gross circle detection found no circle")


def best_gross_circle_candidate(candidates: Sequence[JsonDict], spec: JsonDict) -> JsonDict:
    if not candidates:
        raise ValueError("no gross circle candidates")
    expected = spec.get("expected_center_px")
    if isinstance(expected, list) and len(expected) == 2:
        expected_x = finite_float(expected[0], "expected_center_px.x")
        expected_y = finite_float(expected[1], "expected_center_px.y")
        return min(
            candidates,
            key=lambda candidate: math.hypot(
                finite_float(candidate["x"], "candidate.x") - expected_x,
                finite_float(candidate["y"], "candidate.y") - expected_y,
            ),
        )
    return candidates[0]


def gross_circle_payload(candidate: JsonDict, capture_id: str) -> JsonDict:
    return {
        "label": "auto gross hough ball circle",
        "score": float(candidate["param2"]),
        "x": float(candidate["x"]),
        "y": float(candidate["y"]),
        "radius": float(candidate["radius"]),
        "auto_hough": {
            "capture_id": capture_id,
            "dp": float(candidate["dp"]),
            "param1": float(candidate["param1"]),
            "param2": float(candidate["param2"]),
        },
    }


def read_grayscale_u8(path: str | Path) -> Any:
    try:
        import cv2
    except Exception as exc:  # pragma: no cover - dependency failure path
        raise RuntimeError("OpenCV is required for gross auto feature detection") from exc

    image_path = Path(path)
    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise FileNotFoundError(f"could not read gross image: {image_path}")
    return gray


def standard_capture_image_path(params_in: JsonDict, capture_id: str) -> Path:
    image_paths = as_dict(params_in.get("standard_image_paths"))
    if capture_id in image_paths:
        path = Path(str(image_paths[capture_id]))
        return path if path.is_absolute() else Path.cwd() / path

    source_path = Path(str(params_in.get("standard_positions_path") or DEFAULT_STANDARD_POSITIONS_PATH))
    if not source_path.is_absolute():
        source_path = Path.cwd() / source_path
    payload = json.loads(source_path.read_text(encoding="utf-8"))
    for raw_position in payload.get("positions") or ():
        position = as_dict(raw_position)
        for raw_image in position.get("captured_images") or ():
            image_rel = str(raw_image).replace("\\", "/")
            if Path(image_rel).stem == capture_id:
                return source_path.parent / image_rel
    raise FileNotFoundError(f"standard image for {capture_id} was not found")


def clipped_roi(raw_roi: Sequence[Any], width: int, height: int, capture_id: str) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = [int(round(finite_float(value, f"{capture_id}.roi"))) for value in raw_roi]
    x1 = max(0, min(width, x1))
    x2 = max(0, min(width, x2))
    y1 = max(0, min(height, y1))
    y2 = max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"{capture_id} ROI is invalid after clipping")
    return x1, y1, x2, y2


def roi_payload(raw_roi: Sequence[Any], kind: str) -> JsonDict:
    x1, y1, x2, y2 = [float(value) for value in raw_roi]
    return {
        "kind": kind,
        "x1": min(x1, x2),
        "y1": min(y1, y2),
        "x2": max(x1, x2),
        "y2": max(y1, y2),
        "orientation": "right",
    }


def scale_um_per_pixel(
    observation: JsonDict,
    index: int,
    *,
    baseline_session: JsonDict | None = None,
    candidate_session: JsonDict | None = None,
) -> float:
    if "um_per_pixel" in observation:
        return positive_float(observation["um_per_pixel"], f"gross_observations[{index}].um_per_pixel")
    if "pixels_per_um" in observation:
        return 1.0 / positive_float(observation["pixels_per_um"], f"gross_observations[{index}].pixels_per_um")
    if bool(observation.get("estimate_um_per_pixel_from_ball_diameter")):
        ball_diameter_um = positive_float(
            observation.get("ball_diameter_um", 500.0),
            f"gross_observations[{index}].ball_diameter_um",
        )
        for session_name, session in (
            ("baseline_session", baseline_session),
            ("candidate_session", candidate_session),
        ):
            radius_px = selected_ball_radius_px(as_dict(session), f"gross_observations[{index}].{session_name}")
            if radius_px is not None:
                return ball_diameter_um / (2.0 * radius_px)
    for session_name in ("candidate_session", "baseline_session"):
        relative = as_dict(as_dict(observation.get(session_name)).get("relative_measurement"))
        if "um_per_pixel" in relative:
            return positive_float(
                relative["um_per_pixel"],
                f"gross_observations[{index}].{session_name}.relative_measurement.um_per_pixel",
            )
    raise ValueError(
        f"gross_observations[{index}] must include um_per_pixel, pixels_per_um, "
        "or estimate_um_per_pixel_from_ball_diameter"
    )


def selected_ball_radius_px(session: JsonDict, name: str) -> float | None:
    selected = as_dict(session.get("selected_recognition"))
    for roi_key in sorted(selected, key=roi_sort_key):
        raw_items = selected.get(roi_key)
        if not isinstance(raw_items, list):
            continue
        for raw_item in raw_items:
            item = as_dict(raw_item)
            shape_kind = str(item.get("shape_kind") or "").strip()
            if shape_kind not in {"circle", "silhouette_circle"}:
                continue
            shape = as_dict(item.get("shape"))
            if "radius" in shape:
                return positive_float(shape["radius"], f"{name}.selected_recognition.shape.radius")
            if "circle_radius" in shape:
                return positive_float(shape["circle_radius"], f"{name}.selected_recognition.shape.circle_radius")
    return None


def map_pixel_shift_to_tower_bias(
    pixel_shift: dict[str, float],
    um_per_pixel: float,
    mapping: GrossMapping,
) -> dict[str, float]:
    result: dict[str, float] = {}
    for image_axis, bias_axis in (("x", mapping.image_x), ("y", mapping.image_y)):
        delta_um = finite_float(pixel_shift[image_axis], f"pixel_shift.{image_axis}") * um_per_pixel * bias_axis.sign
        result[bias_axis.tower_axis] = result.get(bias_axis.tower_axis, 0.0) + float(delta_um)
    return result


def gross_mapping_payload(mapping: GrossMapping) -> JsonDict:
    return {
        "image_x": {
            "tower_axis": mapping.image_x.tower_axis,
            "sign": mapping.image_x.sign,
        },
        "image_y": {
            "tower_axis": mapping.image_y.tower_axis,
            "sign": mapping.image_y.sign,
        },
    }


def bias_mapping_evidence_payload(params_in: JsonDict, observation: JsonDict, *, target: str) -> JsonDict:
    raw_evidence = (
        observation.get("bias_mapping_evidence")
        or as_dict(params_in.get("bias_mapping_evidence_by_target")).get(target)
        or params_in.get("bias_mapping_evidence")
        or as_dict(params_in.get("default_close_position_bias")).get("bias_mapping_evidence")
    )
    if isinstance(raw_evidence, dict) and raw_evidence:
        evidence = copy.deepcopy(raw_evidence)
        evidence.setdefault("source", "caller_supplied")
        evidence.setdefault("use_for_motion", False)
        evidence.setdefault("operator_review_required", not bool(evidence.get("use_for_motion")))
        return evidence
    return {
        "source": "configured_gross_view_mapping",
        "calibration_status": "not_motion_calibrated",
        "use_for_motion": False,
        "operator_review_required": True,
        "basis": "500 um ball scale plus configured same-fixture image-axis mapping",
        "reason": "No motion-approved gross-view calibration was supplied; use only as a bounded read-only close-position proposal.",
    }


def biased_position_payload(
    standard_position: JsonDict,
    *,
    target: str,
    applied_bias_um: dict[str, float],
    bias_mapping: JsonDict,
    bias_mapping_evidence: JsonDict,
    gross_standard_position: JsonDict | None = None,
    candidate_gross_machine_positions: JsonDict | None = None,
) -> JsonDict:
    tower = TARGET_TOWER[target]
    planned = copy.deepcopy(standard_position)
    rebase = gross_rebase_payload(
        standard_position,
        gross_standard_position=as_dict(gross_standard_position),
        candidate_gross_machine_positions=as_dict(candidate_gross_machine_positions),
    )
    if rebase:
        planned["machine_positions_um"] = rebase["machine_positions_um"]
    planned["bias_plan"] = {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "tower": tower,
        "applied_bias_um": applied_bias_um,
        "bias_mapping": copy.deepcopy(bias_mapping),
        "bias_mapping_evidence": copy.deepcopy(bias_mapping_evidence),
        "gross_rebase": rebase or None,
        "use_for_motion": bool(bias_mapping_evidence.get("use_for_motion")),
        "operator_review_required": bool(bias_mapping_evidence.get("operator_review_required", True)),
        "camera_positions_unchanged": not bool(rebase),
    }
    machine_positions = as_dict(planned.get("machine_positions_um"))
    tower_positions = as_dict(machine_positions.get(tower))
    for axis, bias_um in applied_bias_um.items():
        if axis not in TOWER_AXES:
            raise ValueError(f"unsupported tower axis {axis!r}")
        if tower_positions.get(axis) is None:
            continue
        tower_positions[axis] = finite_float(tower_positions[axis], f"{planned.get('id')}.{tower}.{axis}") + bias_um
    machine_positions[tower] = tower_positions
    planned["machine_positions_um"] = machine_positions
    return planned


def gross_rebase_payload(
    standard_position: JsonDict,
    *,
    gross_standard_position: JsonDict,
    candidate_gross_machine_positions: JsonDict,
) -> JsonDict:
    """Rebase a close target from the live gross capture instead of fixed absolutes."""

    if not gross_standard_position or not candidate_gross_machine_positions:
        return {}
    standard_close_positions = as_dict(standard_position.get("machine_positions_um"))
    standard_gross_positions = as_dict(gross_standard_position.get("machine_positions_um"))
    rebased_positions: JsonDict = {}
    deltas: JsonDict = {}
    for stage, raw_close_axes in standard_close_positions.items():
        close_axes = as_dict(raw_close_axes)
        gross_axes = as_dict(standard_gross_positions.get(stage))
        candidate_axes = as_dict(candidate_gross_machine_positions.get(stage))
        rebased_stage: JsonDict = {}
        delta_stage: JsonDict = {}
        for axis, close_value in close_axes.items():
            if close_value is None:
                rebased_stage[axis] = None
                continue
            if axis in gross_axes and axis in candidate_axes and gross_axes[axis] is not None and candidate_axes[axis] is not None:
                delta = finite_float(close_value, f"{standard_position.get('id')}.{stage}.{axis}") - finite_float(
                    gross_axes[axis], f"{gross_standard_position.get('id')}.{stage}.{axis}"
                )
                rebased_stage[axis] = finite_float(candidate_axes[axis], f"candidate_gross.{stage}.{axis}") + delta
                delta_stage[axis] = delta
            else:
                rebased_stage[axis] = copy.deepcopy(close_value)
        if rebased_stage:
            rebased_positions[stage] = rebased_stage
        if delta_stage:
            deltas[stage] = delta_stage
    return {
        "source": "candidate_gross_machine_positions_plus_standard_gross_to_close_delta",
        "standard_gross_position_id": gross_standard_position.get("id"),
        "standard_close_position_id": standard_position.get("id"),
        "standard_gross_to_close_delta_um": deltas,
        "candidate_gross_machine_positions_um": copy.deepcopy(candidate_gross_machine_positions),
        "machine_positions_um": rebased_positions,
    }


def close_ids_for_observation(observation: JsonDict, target: str) -> tuple[str, ...]:
    raw_ids = observation.get("close_position_ids")
    if raw_ids is None:
        return DEFAULT_CLOSE_POSITION_IDS[target]
    if not isinstance(raw_ids, list) or not raw_ids:
        raise ValueError("close_position_ids must be a non-empty list when provided")
    return tuple(str(value) for value in raw_ids)


def position_id_from_capture_id(capture_id: str) -> str:
    parts = str(capture_id).split(".")
    if len(parts) >= 2:
        return ".".join(parts[:2])
    return str(capture_id)


def load_standard_positions(params_in: JsonDict) -> dict[str, JsonDict]:
    positions = None
    if isinstance(params_in.get("standard_positions"), dict):
        positions = as_dict(params_in["standard_positions"]).get("positions")
    if positions is None:
        path_value = params_in.get("standard_positions_path")
        if not path_value:
            raise ValueError("standard_positions_path is required")
        path = Path(str(path_value))
        if not path.is_absolute():
            path = Path.cwd() / path
        data = json.loads(path.read_text(encoding="utf-8"))
        positions = data.get("positions")
    if not isinstance(positions, list):
        raise ValueError("standard positions payload must contain a positions list")
    result: dict[str, JsonDict] = {}
    for raw_position in positions:
        position = as_dict(raw_position)
        position_id = str(position.get("id") or "").strip()
        if position_id:
            result[position_id] = position
    missing = sorted(
        {
            position_id
            for ids in DEFAULT_CLOSE_POSITION_IDS.values()
            for position_id in ids
        }
        - set(result)
    )
    if missing:
        raise ValueError(f"standard positions are missing required close position ids: {', '.join(missing)}")
    return result


def parse_gross_mapping(value: Any) -> GrossMapping:
    mapping = as_dict(value)
    return GrossMapping(
        image_x=parse_bias_axis(mapping.get("image_x"), "gross_view_mapping.image_x"),
        image_y=parse_bias_axis(mapping.get("image_y"), "gross_view_mapping.image_y"),
    )


def parse_bias_axis(value: Any, name: str) -> BiasAxis:
    block = as_dict(value)
    tower_axis = str(block.get("tower_axis") or "").strip()
    if tower_axis not in TOWER_AXES:
        raise ValueError(f"{name}.tower_axis must be one of x, y, z")
    sign = finite_float(block.get("sign", 1.0), f"{name}.sign")
    if not math.isclose(abs(sign), 1.0, rel_tol=0.0, abs_tol=1.0e-12):
        raise ValueError(f"{name}.sign must be 1 or -1")
    return BiasAxis(tower_axis=tower_axis, sign=sign)


def abort_response(message: str, *, traceback_text: str | None = None) -> JsonDict:
    response: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "action": "abort",
        "status": message,
        "plans": [],
    }
    if traceback_text:
        response["traceback"] = traceback_text
    return response


def require_schema(params_in: JsonDict) -> None:
    version = params_in.get("schema_version")
    if int(version or 0) != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}")


def as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def roi_sort_key(value: str) -> tuple[int, str]:
    try:
        return (int(str(value).split("_", maxsplit=1)[1]), str(value))
    except (IndexError, ValueError):
        return (999999, str(value))


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


def _parse_args(argv: Sequence[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Plan v5 biased close-position targets.")
    parser.add_argument("input_json", help="ParamIn payload path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> JsonDict:
    args = _parse_args(argv)
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    result = plan_biased_close_positions(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":  # pragma: no cover - manual CLI helper
    main()
