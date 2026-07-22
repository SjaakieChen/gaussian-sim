"""Solve v5 machine coordinates from remembered multi-capture vision features.

This module handles the real v5 capture pattern where the laser/reference
rectangle and the ball edge are often not measured in the same focused image.
It combines:

- the reviewed standard-position machine/camera coordinates;
- selected rectangle features from the laser/reference focus image;
- selected ball-circle features from the ball focus image;
- fixed physical constants for this chip setup.

It does not move hardware.
"""

from __future__ import annotations

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
        """Local fallback so the solver can be tested outside TestMaster."""


SCHEMA_VERSION = 1
DEFAULT_STANDARD_POSITIONS_PATH = Path(__file__).resolve().parents[3] / "Standard position images" / "v4" / "standard_positions.json"
DEFAULT_BASELINE_DIR = Path(__file__).resolve().parents[3] / "Standard position images" / "v4" / "vision_baselines"
DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM = 500.0
DEFAULT_BALL_DIAMETER_UM = 500.0
DEFAULT_TRENCH_DEPTH_UM = 300.0
DEFAULT_TOP_CAMERA_LATERAL_TOLERANCE_UM = 1.0
AXES = ("machine_x_um", "machine_y_um", "machine_z_um")
DEFAULT_AUTO_FEATURE_SPECS: dict[str, dict[str, Any]] = {
    "2.4.1": {
        "kind": "reference_rectangle",
        "roi": [64, 924, 1860, 1696],
        "min_area_px": 200000,
    },
    "4.4.1": {
        "kind": "reference_rectangle",
        "roi": [0, 20, 1796, 796],
        "min_area_px": 200000,
    },
    "2.5.1": {
        "kind": "top_ball_circle",
        "roi": [400, 50, 1700, 1300],
        "min_radius_px": 180,
        "max_radius_px": 450,
    },
    "4.5.1": {
        "kind": "top_ball_circle",
        "roi": [700, 800, 1800, 1850],
        "min_radius_px": 180,
        "max_radius_px": 450,
    },
    "2.6.1": {
        "kind": "side_ball_circle",
        "roi": [1250, 420, 1750, 930],
        "min_radius_px": 100,
        "max_radius_px": 220,
        "side_reference_roi": [800, 250, 2100, 950],
    },
    "4.6.2": {
        "kind": "side_ball_circle",
        "roi": [650, 500, 1600, 1600],
        "min_radius_px": 180,
        "max_radius_px": 450,
        "side_reference_roi": [650, 250, 1800, 1250],
    },
}
DEFAULT_VIEW_MAPPINGS: dict[str, dict[str, dict[str, float | str]]] = {
    "top_xz": {
        "image_x": {"axis": "machine_z_um", "sign": 1.0},
        "image_y": {"axis": "machine_x_um", "sign": 1.0},
    },
}
DEFAULT_TARGET_SPECS: dict[str, dict[str, str]] = {
    "ball_1": {
        "reference_capture_id": "2.4.1",
        "top_ball_capture_id": "2.5.1",
        "side_capture_id": "2.6.1",
    },
    "ball_2": {
        "reference_capture_id": "4.4.1",
        "top_ball_capture_id": "4.5.1",
        "side_capture_id": "4.6.2",
    },
}

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class PhysicalConstants:
    laser_rectangle_short_edge_um: float
    ball_diameter_um: float
    trench_depth_um: float

    @property
    def ball_radius_um(self) -> float:
        return self.ball_diameter_um / 2.0

    @property
    def assumed_ball_center_y_um(self) -> float:
        """Machine-y ball center offset when the ball rests in the trench."""

        return self.ball_radius_um - self.trench_depth_um


@dataclass(frozen=True)
class ViewAxis:
    axis: str
    sign: float


@dataclass(frozen=True)
class ViewMapping:
    image_x: ViewAxis
    image_y: ViewAxis


@dataclass(frozen=True)
class RectangleFeature:
    corners_px: tuple[tuple[float, float], ...]
    center_px: dict[str, float]
    short_edge_length_px: float
    long_edge_length_px: float
    um_per_pixel: float


@dataclass(frozen=True)
class CircleFeature:
    center_px: dict[str, float]
    radius_px: float | None
    source: str


@dataclass(frozen=True)
class SideReferenceFeature:
    y_px: float
    x1_px: float
    x2_px: float
    source: str
    score: float


class VisionSequenceGeometryMemoryStep(TMPythonStatementJ):
    """TMPython entrypoint for read-only multi-capture geometry memory solving."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return solve_sequence_geometry(params_in)
        except Exception as exc:  # fail closed for machine callers
            return abort_response(
                f"VisionSequenceGeometryMemoryStep failed: {exc}",
                traceback_text=traceback.format_exc(),
            )


def solve_sequence_geometry(params_in: JsonDict) -> JsonDict:
    """Return ball positions in the machine-coordinate frame."""

    try:
        require_schema(params_in)
        constants = parse_physical_constants(params_in.get("physical_constants_um"))
        mappings = parse_view_mappings(params_in.get("view_mappings"))
        top_mapping = mappings["top_xz"]
        top_lateral_tolerance_um = non_negative_float(
            params_in.get("top_camera_lateral_tolerance_um", DEFAULT_TOP_CAMERA_LATERAL_TOLERANCE_UM),
            "top_camera_lateral_tolerance_um",
        )
        positions, capture_to_position = load_standard_position_lookup(params_in)
        target_specs = target_specs_from_payload(params_in)
        machine_y_source = parse_machine_y_source(params_in.get("machine_y_source", "trench_model"))

        machine_coordinates: dict[str, dict[str, float]] = {
            "machine_reference": {axis: 0.0 for axis in AXES},
        }
        feature_memory: dict[str, Any] = {}
        focus_memory: dict[str, Any] = {}

        for spec in target_specs:
            target_result = solve_target_geometry(
                spec,
                params_in=params_in,
                constants=constants,
                positions=positions,
                capture_to_position=capture_to_position,
                top_mapping=top_mapping,
                top_lateral_tolerance_um=top_lateral_tolerance_um,
                machine_y_source=machine_y_source,
            )
            target = target_result["target"]
            machine_coordinates[target] = target_result["machine_coordinates_um"]
            feature_memory[target] = target_result["feature_memory"]
            focus_memory[target] = target_result["focus_memory"]

        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "sequence_geometry_solved",
            "status": "v5 sequence geometry solved from remembered capture features",
            "machine_coordinate_system": {
                "reference": "machine_reference",
                "reference_feature": "laser_rectangle_center",
                "units": "um",
                "axes": {
                    "machine_x_um": "Align_X optical propagation axis from top-view image y",
                    "machine_z_um": "Align_Z horizontal transverse axis from top-view image x",
                    "machine_y_um": "vertical axis; default estimated from ball radius minus trench depth",
                },
            },
            "physical_constants_um": {
                "laser_rectangle_short_edge_um": constants.laser_rectangle_short_edge_um,
                "ball_diameter_um": constants.ball_diameter_um,
                "ball_radius_um": constants.ball_radius_um,
                "trench_depth_um": constants.trench_depth_um,
                "assumed_ball_center_y_um": constants.assumed_ball_center_y_um,
            },
            "machine_coordinates_um": machine_coordinates,
            "feature_memory": feature_memory,
            "focus_memory": focus_memory,
            "thresholds": {
                "top_camera_lateral_tolerance_um": top_lateral_tolerance_um,
            },
            "machine_y_source": machine_y_source,
        }
    except Exception as exc:
        return abort_response(str(exc))


def solve_target_geometry(
    spec: JsonDict,
    *,
    params_in: JsonDict,
    constants: PhysicalConstants,
    positions: dict[str, JsonDict],
    capture_to_position: dict[str, str],
    top_mapping: ViewMapping,
    top_lateral_tolerance_um: float,
    machine_y_source: str,
) -> JsonDict:
    target = str(spec.get("target") or "").strip()
    if not target:
        raise ValueError("target spec is missing target")
    reference_capture_id = required_text(spec, "reference_capture_id", f"{target}.reference_capture_id")
    top_ball_capture_id = required_text(spec, "top_ball_capture_id", f"{target}.top_ball_capture_id")
    side_capture_id = str(spec.get("side_capture_id") or "").strip()

    reference_session = resolve_session(
        params_in,
        spec,
        "reference_session",
        reference_capture_id,
        required=True,
        feature_kind="reference_rectangle",
    )
    top_ball_session = resolve_session(
        params_in,
        spec,
        "top_ball_session",
        top_ball_capture_id,
        required=True,
        feature_kind="top_ball_circle",
    )
    side_session = (
        resolve_session(
            params_in,
            spec,
            "side_session",
            side_capture_id,
            required=False,
            feature_kind="side_ball_circle",
        )
        if side_capture_id
        else {}
    )

    rectangle = selected_rectangle_feature(reference_session, constants.laser_rectangle_short_edge_um)
    top_ball = selected_circle_feature(top_ball_session, f"{target} top ball")
    side_ball = selected_circle_feature(side_session, f"{target} side ball", required=False) if side_session else None
    side_reference = resolve_side_reference(params_in, spec, side_capture_id) if side_capture_id else None

    reference_position = position_for_capture(reference_capture_id, capture_to_position, positions)
    top_ball_position = position_for_capture(top_ball_capture_id, capture_to_position, positions)
    assert_same_top_lateral_camera(
        reference_position,
        top_ball_position,
        reference_capture_id=reference_capture_id,
        top_ball_capture_id=top_ball_capture_id,
        tolerance_um=top_lateral_tolerance_um,
    )

    top_delta_px = {
        "x": top_ball.center_px["x"] - rectangle.center_px["x"],
        "y": top_ball.center_px["y"] - rectangle.center_px["y"],
    }
    top_delta_um = {
        "x": top_delta_px["x"] * rectangle.um_per_pixel,
        "y": top_delta_px["y"] * rectangle.um_per_pixel,
    }
    top_machine = map_image_delta_to_machine_axes(top_delta_um, top_mapping)
    machine_coordinates = {
        "machine_x_um": top_machine["machine_x_um"],
        "machine_y_um": constants.assumed_ball_center_y_um,
        "machine_z_um": top_machine["machine_z_um"],
    }

    focus_delta_um = camera_axis(top_ball_position, "y", top_ball_capture_id) - camera_axis(
        reference_position,
        "y",
        reference_capture_id,
    )
    feature_memory: JsonDict = {
        "reference_capture_id": reference_capture_id,
        "top_ball_capture_id": top_ball_capture_id,
        "side_capture_id": side_capture_id or None,
        "reference_rectangle": {
            "center_px": rectangle.center_px,
            "corners_px": [{"x": x, "y": y} for x, y in rectangle.corners_px],
            "short_edge_length_px": rectangle.short_edge_length_px,
            "long_edge_length_px": rectangle.long_edge_length_px,
            "short_edge_length_um": constants.laser_rectangle_short_edge_um,
            "um_per_pixel": rectangle.um_per_pixel,
        },
        "top_ball_circle": {
            "center_px": top_ball.center_px,
            "radius_px": top_ball.radius_px,
            "source": top_ball.source,
        },
        "top_delta_rectangle_center_to_ball": {
            "px": top_delta_px,
            "um": top_delta_um,
            "machine_axes_um": top_machine,
        },
        "height_estimate": {
            "source": "assumed_trench_bottom_contact",
            "formula": "ball_radius_um - trench_depth_um",
            "machine_y_um": constants.assumed_ball_center_y_um,
        },
    }
    if top_ball.radius_px is not None and top_ball.radius_px > 0.0:
        ball_diameter_scale = constants.ball_diameter_um / (2.0 * top_ball.radius_px)
        feature_memory["top_ball_scale_check"] = {
            "ball_diameter_um": constants.ball_diameter_um,
            "radius_px": top_ball.radius_px,
            "um_per_pixel_from_ball_diameter": ball_diameter_scale,
            "um_per_pixel_from_rectangle": rectangle.um_per_pixel,
            "signed_um_per_pixel_error": ball_diameter_scale - rectangle.um_per_pixel,
        }
    if side_ball is not None:
        side_payload: JsonDict = {
            "center_px": side_ball.center_px,
            "radius_px": side_ball.radius_px,
            "source": side_ball.source,
        }
        if side_ball.radius_px is not None and side_ball.radius_px > 0.0:
            side_payload["um_per_pixel_from_ball_diameter"] = constants.ball_diameter_um / (2.0 * side_ball.radius_px)
        feature_memory["side_ball_circle"] = side_payload
    if side_reference is not None:
        feature_memory["side_reference_line"] = {
            "y_px": side_reference.y_px,
            "x1_px": side_reference.x1_px,
            "x2_px": side_reference.x2_px,
            "source": side_reference.source,
            "score": side_reference.score,
        }
    side_height: JsonDict | None = None
    if side_ball is not None and side_reference is not None and side_ball.radius_px is not None:
        side_height = side_height_candidate(side_ball, side_reference, constants)
        if machine_y_source == "side_reference":
            machine_coordinates["machine_y_um"] = side_height["measured_machine_y_um_candidate"]
            side_height["used_for_coordinate"] = True
        feature_memory["side_height_candidate"] = side_height
    elif machine_y_source == "side_reference":
        raise ValueError(f"{target} cannot use side_reference machine_y without side ball and side reference")

    return {
        "target": target,
        "machine_coordinates_um": machine_coordinates,
        "feature_memory": feature_memory,
        "focus_memory": {
            "reference_capture_id": reference_capture_id,
            "top_ball_capture_id": top_ball_capture_id,
            "reference_camera_y_um": camera_axis(reference_position, "y", reference_capture_id),
            "top_ball_camera_y_um": camera_axis(top_ball_position, "y", top_ball_capture_id),
            "camera_focus_delta_um": focus_delta_um,
            "abs_focus_delta_vs_ball_diameter_um": abs(abs(focus_delta_um) - constants.ball_diameter_um),
            "reference_camera_position_um": as_dict(reference_position.get("machine_positions_um")).get("camera"),
            "top_ball_camera_position_um": as_dict(top_ball_position.get("machine_positions_um")).get("camera"),
        },
    }


def selected_rectangle_feature(session: JsonDict, short_edge_length_um: float) -> RectangleFeature:
    rectangles = [
        item
        for item in selected_recognition_items(session)
        if str(item.get("shape_kind") or "").strip() == "rectangle"
    ]
    if not rectangles:
        raise ValueError("reference session must contain a selected rectangle")
    shape = as_dict(rectangles[0].get("shape"))
    corners = rectangle_corners(shape)
    lengths = []
    for index, start in enumerate(corners):
        end = corners[(index + 1) % len(corners)]
        lengths.append(math.hypot(end[0] - start[0], end[1] - start[1]))
    short_edge_px = min(lengths)
    long_edge_px = max(lengths)
    if short_edge_px <= 0.0 or not math.isfinite(short_edge_px):
        raise ValueError("selected rectangle short edge has invalid pixel length")
    center = {
        "x": sum(x for x, _y in corners) / len(corners),
        "y": sum(y for _x, y in corners) / len(corners),
    }
    return RectangleFeature(
        corners_px=corners,
        center_px=center,
        short_edge_length_px=short_edge_px,
        long_edge_length_px=long_edge_px,
        um_per_pixel=positive_float(short_edge_length_um, "laser_rectangle_short_edge_um") / short_edge_px,
    )


def selected_circle_feature(session: JsonDict, label: str, *, required: bool = True) -> CircleFeature | None:
    circles = [
        item
        for item in selected_recognition_items(session)
        if str(item.get("shape_kind") or "").strip() == "circle"
        or str(item.get("source") or "").strip() == "silhouette_circle"
    ]
    if not circles:
        if required:
            raise ValueError(f"{label} session must contain a selected circle")
        return None
    item = circles[0]
    shape = as_dict(item.get("shape"))
    if "x" in shape and "y" in shape:
        radius = shape.get("radius")
        return CircleFeature(
            center_px={
                "x": finite_float(shape["x"], f"{label}.shape.x"),
                "y": finite_float(shape["y"], f"{label}.shape.y"),
            },
            radius_px=None if radius is None else positive_float(radius, f"{label}.shape.radius"),
            source=str(item.get("source") or item.get("shape_kind") or "circle"),
        )
    if all(key in shape for key in ("circle_x", "circle_y", "circle_radius")):
        return CircleFeature(
            center_px={
                "x": finite_float(shape["circle_x"], f"{label}.shape.circle_x"),
                "y": finite_float(shape["circle_y"], f"{label}.shape.circle_y"),
            },
            radius_px=positive_float(shape["circle_radius"], f"{label}.shape.circle_radius"),
            source="silhouette_circle",
        )
    raise ValueError(f"{label} selected circle is missing a fitted center")


def resolve_side_reference(params_in: JsonDict, spec: JsonDict, side_capture_id: str) -> SideReferenceFeature | None:
    if not side_capture_id:
        return None
    inline = spec.get("side_reference")
    if isinstance(inline, dict):
        return side_reference_from_payload(inline, f"{side_capture_id}.side_reference")
    sessions = as_dict(params_in.get("sessions"))
    session = as_dict(sessions.get(side_capture_id))
    if session:
        selected = selected_side_reference_feature(session)
        if selected is not None:
            return selected

    baseline_dir = Path(str(params_in.get("baseline_dir") or DEFAULT_BASELINE_DIR))
    session_path = baseline_dir / f"{side_capture_id}.json"
    if session_path.is_file():
        selected = selected_side_reference_feature(json.loads(session_path.read_text(encoding="utf-8")))
        if selected is not None:
            return selected

    if bool(params_in.get("auto_detect_side_references")) or bool(params_in.get("auto_detect_missing_sessions")):
        side_spec = auto_feature_spec(params_in, side_capture_id, "side_ball_circle")
        image_path = standard_capture_image_path(params_in, side_capture_id)
        gray = read_grayscale_u8(image_path)
        return auto_detect_side_reference_line(gray, side_spec, side_capture_id)
    return None


def selected_side_reference_feature(session: JsonDict) -> SideReferenceFeature | None:
    raw_reference = session.get("side_reference_line")
    if isinstance(raw_reference, dict):
        return side_reference_from_payload(raw_reference, "side_reference_line")
    for item in selected_recognition_items(session):
        shape_kind = str(item.get("shape_kind") or "").strip()
        source = str(item.get("source") or "").strip()
        if shape_kind != "line" and source != "side_reference_line":
            continue
        shape = as_dict(item.get("shape"))
        if all(key in shape for key in ("x1", "y1", "x2", "y2")):
            y1 = finite_float(shape["y1"], "side_reference_line.y1")
            y2 = finite_float(shape["y2"], "side_reference_line.y2")
            return SideReferenceFeature(
                y_px=0.5 * (y1 + y2),
                x1_px=finite_float(shape["x1"], "side_reference_line.x1"),
                x2_px=finite_float(shape["x2"], "side_reference_line.x2"),
                source=source or "selected_line",
                score=finite_float(shape.get("score", 1.0), "side_reference_line.score"),
            )
    return None


def side_reference_from_payload(payload: JsonDict, label: str) -> SideReferenceFeature:
    return SideReferenceFeature(
        y_px=finite_float(payload.get("y_px"), f"{label}.y_px"),
        x1_px=finite_float(payload.get("x1_px", 0.0), f"{label}.x1_px"),
        x2_px=finite_float(payload.get("x2_px", 0.0), f"{label}.x2_px"),
        source=str(payload.get("source") or "provided_side_reference"),
        score=finite_float(payload.get("score", 1.0), f"{label}.score"),
    )


def side_height_candidate(
    side_ball: CircleFeature,
    side_reference: SideReferenceFeature,
    constants: PhysicalConstants,
) -> JsonDict:
    if side_ball.radius_px is None:
        raise ValueError("side ball radius is required for side height candidate")
    um_per_pixel = constants.ball_diameter_um / (2.0 * positive_float(side_ball.radius_px, "side_ball.radius_px"))
    delta_px_down_from_reference = side_ball.center_px["y"] - side_reference.y_px
    measured_y_um = -delta_px_down_from_reference * um_per_pixel
    return {
        "source": "side_reference_line_to_ball_center",
        "side_reference_y_px": side_reference.y_px,
        "side_ball_center_y_px": side_ball.center_px["y"],
        "delta_px_down_from_reference": delta_px_down_from_reference,
        "um_per_pixel_from_side_ball_diameter": um_per_pixel,
        "measured_machine_y_um_candidate": measured_y_um,
        "trench_model_machine_y_um": constants.assumed_ball_center_y_um,
        "candidate_minus_trench_model_um": measured_y_um - constants.assumed_ball_center_y_um,
        "used_for_coordinate": False,
        "review_required": True,
    }


def selected_recognition_items(session: JsonDict) -> list[JsonDict]:
    selected = as_dict(session.get("selected_recognition"))
    items: list[JsonDict] = []
    for roi_key in sorted(selected, key=roi_sort_key):
        raw_items = selected.get(roi_key)
        if isinstance(raw_items, list):
            items.extend(as_dict(item) for item in raw_items)
    return items


def rectangle_corners(shape: JsonDict) -> tuple[tuple[float, float], ...]:
    raw_corners = shape.get("corners")
    corners: list[tuple[float, float]] = []
    if isinstance(raw_corners, list) and len(raw_corners) == 4:
        for index, raw_corner in enumerate(raw_corners, start=1):
            corner = as_dict(raw_corner)
            corners.append(
                (
                    finite_float(corner.get("x"), f"rectangle.corners[{index}].x"),
                    finite_float(corner.get("y"), f"rectangle.corners[{index}].y"),
                )
            )
    else:
        corners = [
            (finite_float(shape.get("x1"), "rectangle.x1"), finite_float(shape.get("y1"), "rectangle.y1")),
            (finite_float(shape.get("x2"), "rectangle.x2"), finite_float(shape.get("y1"), "rectangle.y1")),
            (finite_float(shape.get("x2"), "rectangle.x2"), finite_float(shape.get("y2"), "rectangle.y2")),
            (finite_float(shape.get("x1"), "rectangle.x1"), finite_float(shape.get("y2"), "rectangle.y2")),
        ]
    return tuple(corners)


def resolve_session(
    params_in: JsonDict,
    spec: JsonDict,
    session_key: str,
    capture_id: str,
    *,
    required: bool,
    feature_kind: str,
) -> JsonDict:
    if not capture_id:
        if required:
            raise ValueError(f"{session_key} capture id is required")
        return {}
    inline = spec.get(session_key)
    if isinstance(inline, dict):
        return inline
    sessions = as_dict(params_in.get("sessions"))
    raw_session = sessions.get(capture_id)
    if isinstance(raw_session, dict):
        return raw_session
    baseline_dir = Path(str(params_in.get("baseline_dir") or DEFAULT_BASELINE_DIR))
    session_path = baseline_dir / f"{capture_id}.json"
    if session_path.is_file():
        return json.loads(session_path.read_text(encoding="utf-8"))
    if bool(params_in.get("auto_detect_missing_sessions")):
        return auto_detect_standard_session(params_in, capture_id, feature_kind)
    if required:
        raise FileNotFoundError(f"{session_key} for {capture_id} was not provided and {session_path} does not exist")
    return {}


def auto_detect_standard_session(params_in: JsonDict, capture_id: str, feature_kind: str) -> JsonDict:
    spec = auto_feature_spec(params_in, capture_id, feature_kind)
    image_path = standard_capture_image_path(params_in, capture_id)
    gray = read_grayscale_u8(image_path)
    if feature_kind == "reference_rectangle":
        rectangle = auto_detect_reference_rectangle(gray, spec, capture_id)
        selected_item = {
            "roi_index": 1,
            "shape_kind": "rectangle",
            "source": "auto_reference_rectangle",
            "roi": roi_payload(spec["roi"], "rectangle"),
            "shape": rectangle,
        }
    elif feature_kind in {"top_ball_circle", "side_ball_circle"}:
        circle = auto_detect_ball_circle(gray, spec, capture_id)
        selected_item = {
            "roi_index": 1,
            "shape_kind": "circle",
            "source": "auto_ball_circle",
            "roi": roi_payload(spec["roi"], "circle"),
            "shape": circle,
        }
    else:
        raise ValueError(f"unsupported auto feature kind {feature_kind!r}")
    return {
        "schema_version": 3,
        "ok": True,
        "action": "auto_feature_session",
        "status": f"auto detected {feature_kind} for {capture_id}",
        "image_path": str(image_path),
        "standard_capture_id": capture_id,
        "auto_detected": True,
        "auto_feature_kind": feature_kind,
        "rois": [roi_payload(spec["roi"], "rectangle" if feature_kind == "reference_rectangle" else "circle")],
        "selected_recognition": {
            "roi_1": [selected_item],
        },
    }


def auto_feature_spec(params_in: JsonDict, capture_id: str, expected_kind: str) -> JsonDict:
    raw_specs = as_dict(params_in.get("auto_feature_specs"))
    spec = dict(DEFAULT_AUTO_FEATURE_SPECS.get(capture_id) or {})
    spec.update(as_dict(raw_specs.get(capture_id)))
    if not spec:
        raise ValueError(f"no auto feature spec is configured for {capture_id}")
    kind = str(spec.get("kind") or "").strip()
    if kind != expected_kind:
        raise ValueError(f"auto feature spec for {capture_id} is {kind!r}, expected {expected_kind!r}")
    roi = spec.get("roi")
    if not isinstance(roi, list) or len(roi) != 4:
        raise ValueError(f"auto feature spec for {capture_id} must include roi [x1, y1, x2, y2]")
    spec["roi"] = [finite_float(value, f"{capture_id}.auto_feature.roi") for value in roi]
    return spec


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


def read_grayscale_u8(path: str | Path) -> Any:
    try:
        import cv2
    except Exception as exc:  # pragma: no cover - dependency failure path
        raise RuntimeError("OpenCV is required for auto feature detection") from exc

    image_path = Path(path)
    gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise FileNotFoundError(f"could not read image for auto feature detection: {image_path}")
    return gray


def auto_detect_reference_rectangle(gray: Any, spec: JsonDict, capture_id: str) -> JsonDict:
    import cv2
    import numpy as np

    x1, y1, x2, y2 = clipped_roi(spec["roi"], gray.shape[1], gray.shape[0], capture_id)
    crop = gray[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"{capture_id} reference rectangle ROI is empty")
    blur = cv2.GaussianBlur(crop, (5, 5), 0)
    _threshold, mask = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats((mask > 0).astype("uint8"), 8)
    min_area = int(spec.get("min_area_px", 0))
    best_index = None
    best_area = -1
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        if area >= min_area and area > best_area:
            best_area = area
            best_index = index
    if best_index is None:
        raise ValueError(f"{capture_id} auto rectangle detection found no component above {min_area} px")

    component_mask = (labels == best_index).astype("uint8")
    contours, _hierarchy = cv2.findContours(component_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        raise ValueError(f"{capture_id} auto rectangle detection found no contour")
    rect = cv2.minAreaRect(max(contours, key=cv2.contourArea))
    corners = cv2.boxPoints(rect) + np.asarray([x1, y1], dtype=float)
    ordered = order_rectangle_corners(corners)
    xs = [corner[0] for corner in ordered]
    ys = [corner[1] for corner in ordered]
    return {
        "label": "auto bright reference rectangle",
        "score": float(best_area),
        "x1": min(xs),
        "y1": min(ys),
        "x2": max(xs),
        "y2": max(ys),
        "missing_side": None,
        "corners": [{"x": x, "y": y} for x, y in ordered],
    }


def auto_detect_ball_circle(gray: Any, spec: JsonDict, capture_id: str) -> JsonDict:
    import cv2

    x1, y1, x2, y2 = clipped_roi(spec["roi"], gray.shape[1], gray.shape[0], capture_id)
    crop = gray[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"{capture_id} ball circle ROI is empty")
    blur = cv2.medianBlur(crop, 5)
    min_radius = int(spec.get("min_radius_px", 1))
    max_radius = int(spec.get("max_radius_px", 999999))
    min_dist = int(spec.get("min_dist_px", max(100, min_radius)))
    param1_values = tuple(spec.get("param1_values") or (60, 80, 100, 120))
    param2_values = tuple(spec.get("param2_values") or (18, 22, 26, 30, 36, 44, 55))
    dp_values = tuple(spec.get("dp_values") or (1.2, 1.5, 2.0, 2.5))
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
                circle = circles[0, 0]
                return {
                    "label": "auto hough ball circle",
                    "score": float(param2),
                    "x": float(circle[0] + x1),
                    "y": float(circle[1] + y1),
                    "radius": float(circle[2]),
                    "auto_hough": {
                        "dp": float(dp),
                        "param1": float(param1),
                        "param2": float(param2),
                    },
                }
    raise ValueError(f"{capture_id} auto circle detection found no circle")


def auto_detect_side_reference_line(gray: Any, spec: JsonDict, capture_id: str) -> SideReferenceFeature:
    import cv2
    import numpy as np

    raw_roi = spec.get("side_reference_roi")
    if not isinstance(raw_roi, list) or len(raw_roi) != 4:
        raise ValueError(f"{capture_id} auto side reference spec must include side_reference_roi")
    x1, y1, x2, y2 = clipped_roi(raw_roi, gray.shape[1], gray.shape[0], capture_id)
    crop = gray[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"{capture_id} side reference ROI is empty")
    blur = cv2.GaussianBlur(crop, (7, 7), 0)
    profile = blur.mean(axis=1).astype(float)
    gradient = np.gradient(profile)
    candidate_index = int(np.argmin(gradient))
    score = float(abs(gradient[candidate_index]))
    min_score = float(spec.get("side_reference_min_gradient", 3.0))
    if score < min_score:
        raise ValueError(
            f"{capture_id} side reference edge score {score:.6g} is below {min_score:.6g}"
        )
    y_px = float(y1 + candidate_index)
    return SideReferenceFeature(
        y_px=y_px,
        x1_px=float(x1),
        x2_px=float(x2),
        source="auto_horizontal_bright_to_dark_edge",
        score=score,
    )


def clipped_roi(raw_roi: Sequence[Any], width: int, height: int, capture_id: str) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = [int(round(finite_float(value, f"{capture_id}.roi"))) for value in raw_roi]
    x1 = max(0, min(width, x1))
    x2 = max(0, min(width, x2))
    y1 = max(0, min(height, y1))
    y2 = max(0, min(height, y2))
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"{capture_id} ROI is invalid after clipping")
    return x1, y1, x2, y2


def order_rectangle_corners(corners: Any) -> tuple[tuple[float, float], ...]:
    points = [(float(point[0]), float(point[1])) for point in corners]
    center_x = sum(point[0] for point in points) / len(points)
    center_y = sum(point[1] for point in points) / len(points)
    points.sort(key=lambda point: math.atan2(point[1] - center_y, point[0] - center_x))
    top_index = min(range(len(points)), key=lambda index: (points[index][1], points[index][0]))
    return tuple(points[top_index:] + points[:top_index])


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


def load_standard_position_lookup(params_in: JsonDict) -> tuple[dict[str, JsonDict], dict[str, str]]:
    if isinstance(params_in.get("standard_positions"), dict):
        payload = as_dict(params_in["standard_positions"])
        base_dir = None
    else:
        source_path = Path(str(params_in.get("standard_positions_path") or DEFAULT_STANDARD_POSITIONS_PATH))
        if not source_path.is_absolute():
            source_path = Path.cwd() / source_path
        payload = json.loads(source_path.read_text(encoding="utf-8"))
        base_dir = source_path.parent
    positions: dict[str, JsonDict] = {}
    capture_to_position: dict[str, str] = {}
    raw_positions = payload.get("positions")
    if not isinstance(raw_positions, list):
        raise ValueError("standard positions payload must contain a positions list")
    for raw_position in raw_positions:
        position = as_dict(raw_position)
        position_id = str(position.get("id") or "").strip()
        if not position_id:
            continue
        positions[position_id] = position
        for raw_image in position.get("captured_images") or ():
            capture_id = Path(str(raw_image).replace("\\", "/")).stem
            capture_to_position[capture_id] = position_id
        if base_dir is None:
            continue
    if not positions:
        raise ValueError("no standard positions were loaded")
    return positions, capture_to_position


def target_specs_from_payload(params_in: JsonDict) -> list[JsonDict]:
    raw_targets = params_in.get("targets")
    if raw_targets is None:
        return [target_spec_from_value({"target": target}) for target in DEFAULT_TARGET_SPECS]
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("targets must be a non-empty list when provided")
    return [target_spec_from_value(raw_target) for raw_target in raw_targets]


def target_spec_from_value(raw_target: Any) -> JsonDict:
    if isinstance(raw_target, str):
        raw = {"target": raw_target}
    else:
        raw = as_dict(raw_target)
    target = str(raw.get("target") or "").strip()
    if target not in DEFAULT_TARGET_SPECS:
        raise ValueError("target must be ball_1 or ball_2")
    merged = dict(DEFAULT_TARGET_SPECS[target])
    merged.update(raw)
    merged["target"] = target
    return merged


def parse_machine_y_source(value: Any) -> str:
    source = str(value or "").strip()
    if source not in {"trench_model", "side_reference"}:
        raise ValueError("machine_y_source must be trench_model or side_reference")
    return source


def parse_physical_constants(raw_constants: Any) -> PhysicalConstants:
    constants = as_dict(raw_constants)
    return PhysicalConstants(
        laser_rectangle_short_edge_um=positive_float(
            constants.get("laser_rectangle_short_edge_um", DEFAULT_LASER_RECTANGLE_SHORT_EDGE_UM),
            "physical_constants_um.laser_rectangle_short_edge_um",
        ),
        ball_diameter_um=positive_float(
            constants.get("ball_diameter_um", DEFAULT_BALL_DIAMETER_UM),
            "physical_constants_um.ball_diameter_um",
        ),
        trench_depth_um=positive_float(
            constants.get("trench_depth_um", DEFAULT_TRENCH_DEPTH_UM),
            "physical_constants_um.trench_depth_um",
        ),
    )


def parse_view_mappings(raw_mappings: Any) -> dict[str, ViewMapping]:
    mapping_source = raw_mappings if isinstance(raw_mappings, dict) and raw_mappings else DEFAULT_VIEW_MAPPINGS
    mappings: dict[str, ViewMapping] = {}
    for name, raw_mapping in mapping_source.items():
        mapping = as_dict(raw_mapping)
        mappings[str(name)] = ViewMapping(
            image_x=parse_view_axis(mapping.get("image_x"), f"view_mappings.{name}.image_x"),
            image_y=parse_view_axis(mapping.get("image_y"), f"view_mappings.{name}.image_y"),
        )
    if "top_xz" not in mappings:
        raise ValueError("view_mappings must include top_xz")
    return mappings


def parse_view_axis(value: Any, name: str) -> ViewAxis:
    block = as_dict(value)
    axis = str(block.get("axis") or "").strip()
    if axis not in AXES:
        raise ValueError(f"{name}.axis must be one of {', '.join(AXES)}")
    sign = finite_float(block.get("sign", 1.0), f"{name}.sign")
    if not math.isclose(abs(sign), 1.0, rel_tol=0.0, abs_tol=1.0e-12):
        raise ValueError(f"{name}.sign must be 1 or -1")
    return ViewAxis(axis=axis, sign=sign)


def map_image_delta_to_machine_axes(image_delta_um: dict[str, float], mapping: ViewMapping) -> dict[str, float]:
    mapped: dict[str, float] = {}
    for image_axis, view_axis in (("x", mapping.image_x), ("y", mapping.image_y)):
        mapped[view_axis.axis] = float(image_delta_um[image_axis] * view_axis.sign)
    return mapped


def position_for_capture(capture_id: str, capture_to_position: dict[str, str], positions: dict[str, JsonDict]) -> JsonDict:
    position_id = capture_to_position.get(capture_id)
    if position_id is None:
        position_id = ".".join(capture_id.split(".")[:2])
    position = positions.get(position_id)
    if position is None:
        raise ValueError(f"standard position for capture {capture_id} was not found")
    return position


def assert_same_top_lateral_camera(
    reference_position: JsonDict,
    top_ball_position: JsonDict,
    *,
    reference_capture_id: str,
    top_ball_capture_id: str,
    tolerance_um: float,
) -> None:
    deltas = {
        axis: camera_axis(top_ball_position, axis, top_ball_capture_id)
        - camera_axis(reference_position, axis, reference_capture_id)
        for axis in ("x", "z")
    }
    too_large = {axis: value for axis, value in deltas.items() if abs(value) > tolerance_um}
    if too_large:
        raise ValueError(
            f"{reference_capture_id}->{top_ball_capture_id} camera x/z changed without a calibrated registration: {too_large}"
        )


def camera_axis(position: JsonDict, axis: str, capture_id: str) -> float:
    machine_positions = as_dict(position.get("machine_positions_um"))
    camera = as_dict(machine_positions.get("camera"))
    return finite_float(camera.get(axis), f"{capture_id}.camera.{axis}")


def required_text(mapping: JsonDict, key: str, label: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise ValueError(f"{label} is required")
    return value


def abort_response(message: str, *, traceback_text: str | None = None) -> JsonDict:
    response: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "action": "abort",
        "status": message,
        "machine_coordinates_um": {},
        "feature_memory": {},
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
    text = str(value)
    if text.startswith("roi_"):
        try:
            return (int(text[4:]), text)
        except ValueError:
            pass
    return (999999, text)


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


def _parse_args(argv: Sequence[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Solve v5 sequence geometry from remembered capture features.")
    parser.add_argument("input_json", help="Input JSON payload path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> JsonDict:
    args = _parse_args(argv)
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    result = solve_sequence_geometry(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":  # pragma: no cover - manual CLI helper
    main()
