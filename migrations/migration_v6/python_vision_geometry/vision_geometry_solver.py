"""Fuse v5 vision captures into one read-only machine-coordinate frame.

The solver consumes reviewed vision recognition lab session payloads. It does
not capture images and does not move hardware.
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
DEFAULT_MAX_AXIS_DISAGREEMENT_UM = 10.0
AXES = ("machine_x_um", "machine_y_um", "machine_z_um")
PRECISE_VIEWS = {"top_xz", "mirror_side_xy"}
REQUIRED_TARGETS = ("ball_1", "ball_2")
DEFAULT_VIEW_MAPPINGS: dict[str, dict[str, dict[str, float | str]]] = {
    "top_xz": {
        "image_x": {"axis": "machine_z_um", "sign": 1.0},
        "image_y": {"axis": "machine_x_um", "sign": 1.0},
    },
    "mirror_side_xy": {
        "image_x": {"axis": "machine_x_um", "sign": 1.0},
        "image_y": {"axis": "machine_y_um", "sign": 1.0},
    },
}

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class ViewAxis:
    axis: str
    sign: float


@dataclass(frozen=True)
class ViewMapping:
    image_x: ViewAxis
    image_y: ViewAxis


@dataclass(frozen=True)
class Observation:
    capture_id: str
    target: str
    view: str
    image_delta_um: dict[str, float]
    machine_axes_um: dict[str, float]


class VisionGeometrySolverStep(TMPythonStatementJ):
    """TMPython entrypoint for read-only v5 geometry fusion."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return solve_common_geometry(params_in)
        except Exception as exc:  # fail closed for machine callers
            return abort_response(f"VisionGeometrySolverStep failed: {exc}", traceback_text=traceback.format_exc())


def solve_common_geometry(params_in: JsonDict) -> JsonDict:
    """Return one machine-coordinate frame from top/side captures."""

    try:
        require_schema(params_in)
        mappings = parse_view_mappings(params_in.get("view_mappings"))
        max_disagreement = positive_float(
            params_in.get("max_axis_disagreement_um", DEFAULT_MAX_AXIS_DISAGREEMENT_UM),
            "max_axis_disagreement_um",
        )
        observations = observations_from_payload(params_in, mappings)
        coordinates, disagreements = fuse_observations(
            observations,
            max_axis_disagreement_um=max_disagreement,
        )
        result: JsonDict = {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "geometry_solved",
            "status": "v5 vision geometry solved",
            "machine_coordinate_system": {
                "reference": "machine_reference",
                "reference_feature": "laser_rectangle_center",
                "units": "um",
                "axes": {
                    "machine_x_um": "Align_X optical propagation axis",
                    "machine_z_um": "Align_Z horizontal transverse axis",
                    "machine_y_um": "Align_Y vertical transverse / mirror-height axis",
                },
            },
            "machine_coordinates_um": coordinates,
            "observations": [observation_to_json(observation) for observation in observations],
            "axis_disagreements_um": disagreements,
            "max_axis_disagreement_um": max(
                (item["abs_disagreement_um"] for item in disagreements),
                default=0.0,
            ),
            "thresholds": {
                "max_axis_disagreement_um": max_disagreement,
            },
        }
        detector = optional_coordinate_block(params_in.get("detector_um") or params_in.get("detector"))
        if detector is not None:
            result["machine_geometry_um"] = fixed_z_geometry_payload(coordinates, detector)
        return result
    except Exception as exc:
        return abort_response(str(exc))


def observations_from_payload(params_in: JsonDict, mappings: dict[str, ViewMapping]) -> list[Observation]:
    raw_captures = params_in.get("captures")
    if not isinstance(raw_captures, list) or not raw_captures:
        raise ValueError("captures must be a non-empty list")

    observations: list[Observation] = []
    for index, raw_capture in enumerate(raw_captures, start=1):
        capture = as_dict(raw_capture)
        view = str(capture.get("view") or "").strip()
        if view not in PRECISE_VIEWS:
            continue
        target = str(capture.get("target") or "").strip()
        if target not in REQUIRED_TARGETS:
            raise ValueError(f"captures[{index}].target must be ball_1 or ball_2")
        mapping = mappings.get(view)
        if mapping is None:
            raise ValueError(f"no view mapping configured for {view!r}")

        session = as_dict(capture.get("session") or capture.get("vision_session"))
        selection_index = int(capture.get("selection_index") or 1)
        image_delta = ball_relative_to_reference_from_session(session, selection_index=selection_index)
        machine_axes = map_image_delta_to_machine_axes(image_delta, mapping)
        observations.append(
            Observation(
                capture_id=str(capture.get("capture_id") or capture.get("id") or f"capture_{index}"),
                target=target,
                view=view,
                image_delta_um=image_delta,
                machine_axes_um=machine_axes,
            )
        )
    if not observations:
        raise ValueError("no precise top_xz or mirror_side_xy captures were provided")
    return observations


def ball_relative_to_reference_from_session(session: JsonDict, *, selection_index: int = 1) -> dict[str, float]:
    """Return selected circle position relative to the measured laser/reference edge.

    The vision lab currently reports the selected rectangle short-edge midpoint
    relative to the first selected circle. This function converts that into the
    selected circle relative to the reference edge/midpoint.
    """

    relative = as_dict(session.get("relative_measurement"))
    if not relative:
        raise ValueError("vision session relative_measurement is missing")
    edge = as_dict(relative.get("edge_midpoint_relative_um") or relative.get("measure_edge", {}).get("midpoint_relative_um"))
    edge_x = finite_float(first_present(edge, "x", "dx"), "relative_measurement.edge_midpoint_relative_um.x")
    edge_y = finite_float(first_present(edge, "y", "dy"), "relative_measurement.edge_midpoint_relative_um.y")

    circle_x = 0.0
    circle_y = 0.0
    circles = relative.get("circles")
    if isinstance(circles, list):
        selected_circle = None
        for raw_circle in circles:
            circle = as_dict(raw_circle)
            if int(circle.get("selection_index") or 0) == selection_index:
                selected_circle = circle
                break
        if selected_circle is None and selection_index == 1 and circles:
            selected_circle = as_dict(circles[0])
        if selected_circle is not None:
            circle_x = finite_float(selected_circle.get("x_um", 0.0), "relative_measurement.circles.x_um")
            circle_y = finite_float(selected_circle.get("y_um", 0.0), "relative_measurement.circles.y_um")

    return {
        "x": float(circle_x - edge_x),
        "y": float(circle_y - edge_y),
    }


def map_image_delta_to_machine_axes(image_delta_um: dict[str, float], mapping: ViewMapping) -> dict[str, float]:
    mapped: dict[str, float] = {}
    for image_axis_name, view_axis in (("x", mapping.image_x), ("y", mapping.image_y)):
        value = finite_float(image_delta_um[image_axis_name], f"image_delta_um.{image_axis_name}")
        mapped[view_axis.axis] = float(value * view_axis.sign)
    return mapped


def fuse_observations(
    observations: Sequence[Observation],
    *,
    max_axis_disagreement_um: float,
) -> tuple[dict[str, dict[str, float]], list[JsonDict]]:
    by_target: dict[str, dict[str, list[float]]] = {
        target: {axis: [] for axis in AXES} for target in REQUIRED_TARGETS
    }
    seen_views: dict[str, set[str]] = {target: set() for target in REQUIRED_TARGETS}
    for observation in observations:
        seen_views[observation.target].add(observation.view)
        for axis, value in observation.machine_axes_um.items():
            by_target[observation.target][axis].append(finite_float(value, f"{observation.target}.{axis}"))

    coordinates: dict[str, dict[str, float]] = {
        "machine_reference": {axis: 0.0 for axis in AXES},
    }
    disagreements: list[JsonDict] = []
    for target in REQUIRED_TARGETS:
        missing_views = sorted(PRECISE_VIEWS - seen_views[target])
        if missing_views:
            raise ValueError(f"{target} is missing precise view(s): {', '.join(missing_views)}")
        target_coordinates: dict[str, float] = {}
        for axis in AXES:
            values = by_target[target][axis]
            if not values:
                raise ValueError(f"{target} is missing {axis}")
            low = min(values)
            high = max(values)
            if len(values) > 1:
                disagreement = high - low
                disagreements.append(
                    {
                        "target": target,
                        "axis": axis,
                        "values_um": values,
                        "abs_disagreement_um": disagreement,
                    }
                )
                if disagreement > max_axis_disagreement_um:
                    raise ValueError(
                        f"{target} {axis} top/side disagreement {disagreement:.6g} um exceeds "
                        f"{max_axis_disagreement_um:.6g} um"
                    )
            target_coordinates[axis] = float(sum(values) / len(values))
        coordinates[target] = target_coordinates
    return coordinates, disagreements


def fixed_z_geometry_payload(coordinates: dict[str, dict[str, float]], detector: dict[str, float]) -> JsonDict:
    reference = coordinates["machine_reference"]
    return {
        "machine_reference": {
            "machine_x_um": reference["machine_x_um"],
            "machine_z_um": reference["machine_z_um"],
            "machine_y_um": reference["machine_y_um"],
            "x_angle_mrad": 0.0,
            "z_angle_mrad": 0.0,
        },
        "detector": detector,
        "balls": [
            {
                "name": target,
                "machine_x_um": coordinates[target]["machine_x_um"],
                "machine_z_um": coordinates[target]["machine_z_um"],
                "machine_y_um": coordinates[target]["machine_y_um"],
                "diameter_um": 500.0,
                "refractive_index": 1.76,
            }
            for target in REQUIRED_TARGETS
        ],
    }


def parse_view_mappings(raw_mappings: Any) -> dict[str, ViewMapping]:
    mapping_source = raw_mappings if isinstance(raw_mappings, dict) and raw_mappings else DEFAULT_VIEW_MAPPINGS
    return {
        str(name): ViewMapping(
            image_x=parse_view_axis(as_dict(value).get("image_x"), f"view_mappings.{name}.image_x"),
            image_y=parse_view_axis(as_dict(value).get("image_y"), f"view_mappings.{name}.image_y"),
        )
        for name, value in mapping_source.items()
    }


def parse_view_axis(value: Any, name: str) -> ViewAxis:
    block = as_dict(value)
    axis = str(block.get("axis") or "").strip()
    if axis not in AXES:
        raise ValueError(f"{name}.axis must be one of {', '.join(AXES)}")
    sign = finite_float(block.get("sign", 1.0), f"{name}.sign")
    if not math.isclose(abs(sign), 1.0, rel_tol=0.0, abs_tol=1.0e-12):
        raise ValueError(f"{name}.sign must be 1 or -1")
    return ViewAxis(axis=axis, sign=sign)


def optional_coordinate_block(value: Any) -> dict[str, float] | None:
    if value is None:
        return None
    block = as_dict(value)
    if not block:
        return None
    return {axis: finite_float(block[axis], f"detector.{axis}") for axis in AXES}


def observation_to_json(observation: Observation) -> JsonDict:
    return {
        "capture_id": observation.capture_id,
        "target": observation.target,
        "view": observation.view,
        "image_delta_um": observation.image_delta_um,
        "machine_axes_um": observation.machine_axes_um,
    }


def abort_response(message: str, *, traceback_text: str | None = None) -> JsonDict:
    response: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "action": "abort",
        "status": message,
        "machine_coordinates_um": {},
        "observations": [],
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


def first_present(block: JsonDict, *names: str, default: Any = None) -> Any:
    for name in names:
        if name in block:
            return block[name]
    return default


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

    parser = argparse.ArgumentParser(description="Fuse v5 vision geometry JSON.")
    parser.add_argument("input_json", help="ParamIn payload path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> JsonDict:
    args = _parse_args(argv)
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    result = solve_common_geometry(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":  # pragma: no cover - manual CLI helper
    main()
