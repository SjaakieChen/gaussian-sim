"""Build read-only evidence from the v4 standard captures.

This module is for auditing the automatic v5 coordinate conversions. It runs
the existing gross and focused auto-detection paths against the checked-in
standard images, then summarizes the pixel evidence and the machine-position
relationships that the sequence memory uses later.

It does not move hardware.
"""

from __future__ import annotations

import json
import math
import traceback
from pathlib import Path
from typing import Any, Sequence


try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - used on developer machines without TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback so the evidence builder can be tested outside TestMaster."""


from .position_bias_planner import (
    DEFAULT_CLOSE_POSITION_IDS,
    DEFAULT_STANDARD_POSITIONS_PATH,
    TARGET_TOWER,
    plan_biased_close_positions,
)
from .sequence_geometry_memory import solve_sequence_geometry


SCHEMA_VERSION = 1
DEFAULT_PHYSICAL_CONSTANTS_UM = {
    "laser_rectangle_short_edge_um": 500.0,
    "ball_diameter_um": 500.0,
    "trench_depth_um": 300.0,
}
DEFAULT_GROSS_OBSERVATIONS = (
    {"target": "ball_1", "gross_capture_id": "2.1.1"},
    {"target": "ball_2", "gross_capture_id": "4.1.1"},
)
SAME_CAMERA_MOTION_CAPTURE_IDS = {
    "ball_1": ("2.1.1", "2.2.1", "2.3.1"),
    "ball_2": ("4.1.1", "4.2.1", "4.3.1"),
}

JsonDict = dict[str, Any]


class VisionStandardCaptureEvidenceStep(TMPythonStatementJ):
    """TMPython entrypoint for read-only v4 standard-capture evidence."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return build_standard_capture_evidence(params_in)
        except Exception as exc:  # fail closed for machine callers
            return abort_response(
                f"VisionStandardCaptureEvidenceStep failed: {exc}",
                traceback_text=traceback.format_exc(),
            )


def build_standard_capture_evidence(params_in: JsonDict | None = None) -> JsonDict:
    """Return an evidence report for the current v4 standard-image conversion."""

    try:
        params = as_dict(params_in)
        require_schema(params)
        standard_positions_path = str(params.get("standard_positions_path") or DEFAULT_STANDARD_POSITIONS_PATH)
        physical_constants = physical_constants_payload(params)
        machine_y_source = str(params.get("machine_y_source") or "trench_model")
        standard_positions = load_standard_positions_payload(standard_positions_path)
        positions_by_id = positions_by_id_from_payload(standard_positions)

        gross_observations = []
        for observation in DEFAULT_GROSS_OBSERVATIONS:
            prepared_observation = dict(observation)
            prepared_observation["estimate_um_per_pixel_from_ball_diameter"] = True
            prepared_observation["ball_diameter_um"] = physical_constants["ball_diameter_um"]
            gross_observations.append(prepared_observation)

        gross_result = plan_biased_close_positions(
            {
                "schema_version": SCHEMA_VERSION,
                "standard_positions_path": standard_positions_path,
                "gross_observations": gross_observations,
                "auto_detect_gross_sessions": True,
                "physical_constants_um": physical_constants,
            }
        )
        if not gross_result.get("ok"):
            return abort_response(f"gross standard evidence failed: {gross_result.get('status')}")

        geometry_result = solve_sequence_geometry(
            {
                "schema_version": SCHEMA_VERSION,
                "standard_positions_path": standard_positions_path,
                "auto_detect_missing_sessions": True,
                "auto_detect_side_references": True,
                "machine_y_source": machine_y_source,
                "physical_constants_um": physical_constants,
            }
        )
        if not geometry_result.get("ok"):
            return abort_response(f"focused standard evidence failed: {geometry_result.get('status')}")

        target_evidence = {
            target: target_standard_evidence(
                target,
                gross_plan=gross_plan_by_target(gross_result).get(target, {}),
                geometry_result=geometry_result,
                positions_by_id=positions_by_id,
                standard_positions_path=standard_positions_path,
                physical_constants=physical_constants,
            )
            for target in ("ball_1", "ball_2")
        }
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "standard_capture_evidence_built",
            "status": "v5 standard capture evidence built from v4 images and machine positions",
            "standard_positions_path": standard_positions_path,
            "physical_constants_um": {
                **physical_constants,
                "ball_radius_um": physical_constants["ball_diameter_um"] / 2.0,
                "assumed_ball_center_y_um": (
                    physical_constants["ball_diameter_um"] / 2.0 - physical_constants["trench_depth_um"]
                ),
            },
            "machine_coordinate_system": geometry_result["machine_coordinate_system"],
            "machine_coordinates_um": geometry_result["machine_coordinates_um"],
            "target_evidence": target_evidence,
            "gross_bias": gross_result,
            "sequence_geometry": geometry_result,
        }
    except Exception as exc:
        return abort_response(str(exc))


def target_standard_evidence(
    target: str,
    *,
    gross_plan: JsonDict,
    geometry_result: JsonDict,
    positions_by_id: dict[str, JsonDict],
    standard_positions_path: str,
    physical_constants: JsonDict,
) -> JsonDict:
    feature_memory = as_dict(as_dict(geometry_result.get("feature_memory")).get(target))
    focus_memory = as_dict(as_dict(geometry_result.get("focus_memory")).get(target))
    close_position_ids = list(DEFAULT_CLOSE_POSITION_IDS[target])
    gross_capture_id = str(gross_plan.get("gross_capture_id") or "")
    gross_position_id = ".".join(gross_capture_id.split(".")[:2])
    gross_position = as_dict(positions_by_id.get(gross_position_id))
    machine_deltas = {
        position_id: machine_delta_from_gross(
            target,
            gross_position=gross_position,
            close_position=as_dict(positions_by_id.get(position_id)),
        )
        for position_id in close_position_ids
    }

    ball_diameter_um = float(physical_constants["ball_diameter_um"])
    gross_radius_px = gross_plan.get("baseline_radius_px") or gross_plan.get("candidate_radius_px")
    gross_um_per_pixel = gross_plan.get("um_per_pixel")
    gross_ball_scale: JsonDict = {
        "ball_diameter_um": ball_diameter_um,
        "um_per_pixel": gross_um_per_pixel,
    }
    if gross_radius_px:
        gross_ball_scale["radius_px"] = gross_radius_px
        gross_ball_scale["diameter_px"] = float(gross_radius_px) * 2.0
        gross_ball_scale["um_per_pixel_from_radius"] = ball_diameter_um / (2.0 * float(gross_radius_px))

    return {
        "gross_capture_id": gross_capture_id,
        "tower": TARGET_TOWER[target],
        "gross_ball_center_px": gross_plan.get("baseline_center_px"),
        "gross_ball_scale": gross_ball_scale,
        "close_position_ids": close_position_ids,
        "gross_to_close_machine_deltas_um": machine_deltas,
        "same_camera_motion_evidence": same_camera_motion_evidence(
            target,
            gross_plan=gross_plan,
            positions_by_id=positions_by_id,
            standard_positions_path=standard_positions_path,
        ),
        "reference_rectangle": feature_memory.get("reference_rectangle"),
        "top_ball_circle": feature_memory.get("top_ball_circle"),
        "side_ball_circle": feature_memory.get("side_ball_circle"),
        "side_height_candidate": feature_memory.get("side_height_candidate"),
        "focus_memory": focus_memory,
        "machine_coordinates_um": as_dict(as_dict(geometry_result.get("machine_coordinates_um")).get(target)),
        "conversion_chain": {
            "top_xz": "rectangle center px -> ball center px -> 500 um rectangle short-edge scale -> machine_x_um/machine_z_um",
            "machine_y_um": "ball radius 250 um minus trench depth 300 um unless machine_y_source=side_reference",
            "gross_bias": "gross ball pixel shift vs official baseline -> 500 um ball scale -> bounded same-fixture tower bias",
        },
        "assumptions": [
            "Tower coordinates are treated as repeatable same-fixture proxies, not direct ball-center coordinates.",
            "Top reference and top ball-focus captures must keep camera x/z unchanged.",
            "Camera y is remembered as a focus plane for features at the same physical height.",
        ],
    }


def same_camera_motion_evidence(
    target: str,
    *,
    gross_plan: JsonDict,
    positions_by_id: dict[str, JsonDict],
    standard_positions_path: str,
) -> JsonDict:
    capture_ids = SAME_CAMERA_MOTION_CAPTURE_IDS[target]
    gross_capture_id = str(gross_plan.get("gross_capture_id") or capture_ids[0])
    gross_feature = feature_from_gross_plan(gross_plan, gross_capture_id)
    features = {
        capture_id: (
            gross_feature
            if capture_id == gross_capture_id
            else feature_from_official_baseline(standard_positions_path, capture_id)
        )
        for capture_id in capture_ids
    }
    gross_position = as_dict(positions_by_id.get(position_id_from_capture_id(gross_capture_id)))
    gross_machine = as_dict(gross_position.get("machine_positions_um"))
    gross_tower = as_dict(gross_machine.get(TARGET_TOWER[target]))
    gross_camera = as_dict(gross_machine.get("camera"))
    missing_feature_capture_ids = sorted(capture_id for capture_id, feature in features.items() if not feature)
    samples = []
    for capture_id in capture_ids:
        if capture_id == gross_capture_id:
            continue
        feature = as_dict(features.get(capture_id))
        if not feature or not gross_feature:
            continue
        position = as_dict(positions_by_id.get(position_id_from_capture_id(capture_id)))
        machine = as_dict(position.get("machine_positions_um"))
        camera_delta = axis_delta(gross_camera, as_dict(machine.get("camera")))
        tower_delta = axis_delta(gross_tower, as_dict(machine.get(TARGET_TOWER[target])))
        samples.append(
            {
                "capture_id": capture_id,
                "position_id": position_id_from_capture_id(capture_id),
                "feature_source": feature.get("source"),
                "center_px": feature.get("center_px"),
                "radius_px": feature.get("radius_px"),
                "pixel_shift_from_gross_px": {
                    "x": float(feature["center_px"]["x"]) - float(gross_feature["center_px"]["x"]),
                    "y": float(feature["center_px"]["y"]) - float(gross_feature["center_px"]["y"]),
                },
                "tower_delta_from_gross_um": tower_delta,
                "camera_delta_from_gross_um": camera_delta,
                "same_camera": all((value == 0.0 for value in camera_delta.values() if value is not None)),
            }
        )

    fit = empirical_tower_to_pixel_fit(samples)
    return {
        "reference_capture_id": gross_capture_id,
        "capture_ids": list(capture_ids),
        "reference_feature": gross_feature,
        "missing_feature_capture_ids": missing_feature_capture_ids,
        "motion_samples": samples,
        "tower_to_pixel_fit": fit,
    }


def feature_from_gross_plan(gross_plan: JsonDict, capture_id: str) -> JsonDict:
    center = as_dict(gross_plan.get("baseline_center_px") or gross_plan.get("candidate_center_px"))
    if not center:
        return {}
    return {
        "capture_id": capture_id,
        "source": "gross_auto_detection",
        "center_px": {
            "x": finite_float(center.get("x"), f"{capture_id}.gross_center.x"),
            "y": finite_float(center.get("y"), f"{capture_id}.gross_center.y"),
        },
        "radius_px": gross_plan.get("baseline_radius_px") or gross_plan.get("candidate_radius_px"),
    }


def feature_from_official_baseline(standard_positions_path: str, capture_id: str) -> JsonDict:
    baseline_path = Path(standard_positions_path)
    if not baseline_path.is_absolute():
        baseline_path = Path.cwd() / baseline_path
    session_path = baseline_path.parent / "vision_baselines" / f"{capture_id}.json"
    if not session_path.is_file():
        return {}
    session = json.loads(session_path.read_text(encoding="utf-8"))
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
            if "x" in shape and "y" in shape:
                return {
                    "capture_id": capture_id,
                    "source": "official_baseline",
                    "center_px": {
                        "x": finite_float(shape.get("x"), f"{capture_id}.baseline.x"),
                        "y": finite_float(shape.get("y"), f"{capture_id}.baseline.y"),
                    },
                    "radius_px": shape.get("radius"),
                }
    return {}


def empirical_tower_to_pixel_fit(samples: list[JsonDict]) -> JsonDict:
    usable = [
        sample
        for sample in samples
        if sample.get("same_camera")
        and all(sample["tower_delta_from_gross_um"].get(axis) is not None for axis in ("x", "y", "z"))
    ]
    if not usable:
        return {
            "status": "missing_samples",
            "rank": 0,
            "required_rank": 3,
            "use_for_motion": False,
        }

    try:
        import numpy as np
    except Exception:
        return {
            "status": "numpy_unavailable",
            "rank": 0,
            "required_rank": 3,
            "use_for_motion": False,
        }

    axes = ("x", "y", "z")
    a = np.asarray(
        [[float(sample["tower_delta_from_gross_um"][axis]) for axis in axes] for sample in usable],
        dtype=float,
    )
    bx = np.asarray([float(sample["pixel_shift_from_gross_px"]["x"]) for sample in usable], dtype=float)
    by = np.asarray([float(sample["pixel_shift_from_gross_px"]["y"]) for sample in usable], dtype=float)
    rank = int(np.linalg.matrix_rank(a))
    coeff_x, _residuals_x, _rank_x, _singular_x = np.linalg.lstsq(a, bx, rcond=None)
    coeff_y, _residuals_y, _rank_y, _singular_y = np.linalg.lstsq(a, by, rcond=None)
    status = "calibrated" if rank >= 3 else "underconstrained_full_3_axis_calibration"
    return {
        "status": status,
        "rank": rank,
        "required_rank": 3,
        "use_for_motion": rank >= 3,
        "sample_count": len(usable),
        "input_axes": ["tower_x_um", "tower_y_um", "tower_z_um"],
        "output_axes": ["image_x_px", "image_y_px"],
        "least_squares_min_norm_um_to_px": {
            "image_x_px_per_um": {f"tower_{axis}_um": float(value) for axis, value in zip(axes, coeff_x)},
            "image_y_px_per_um": {f"tower_{axis}_um": float(value) for axis, value in zip(axes, coeff_y)},
        },
    }


def machine_delta_from_gross(target: str, *, gross_position: JsonDict, close_position: JsonDict) -> JsonDict:
    tower_name = TARGET_TOWER[target]
    return {
        "tower_delta_um": axis_delta(
            as_dict(as_dict(gross_position.get("machine_positions_um")).get(tower_name)),
            as_dict(as_dict(close_position.get("machine_positions_um")).get(tower_name)),
        ),
        "camera_delta_um": axis_delta(
            as_dict(as_dict(gross_position.get("machine_positions_um")).get("camera")),
            as_dict(as_dict(close_position.get("machine_positions_um")).get("camera")),
        ),
        "close_camera_settings": as_dict(close_position.get("camera_settings")),
    }


def axis_delta(start: JsonDict, end: JsonDict) -> JsonDict:
    deltas: JsonDict = {}
    for axis in ("x", "y", "z"):
        if start.get(axis) is None or end.get(axis) is None:
            deltas[axis] = None
        else:
            deltas[axis] = float(end[axis]) - float(start[axis])
    return deltas


def position_id_from_capture_id(capture_id: str) -> str:
    return ".".join(str(capture_id).split(".")[:2])


def gross_plan_by_target(gross_result: JsonDict) -> dict[str, JsonDict]:
    return {
        str(plan.get("target")): as_dict(plan)
        for plan in gross_result.get("plans") or ()
        if as_dict(plan).get("target")
    }


def load_standard_positions_payload(path_value: str) -> JsonDict:
    path = Path(path_value)
    if not path.is_absolute():
        path = Path.cwd() / path
    return json.loads(path.read_text(encoding="utf-8"))


def positions_by_id_from_payload(payload: JsonDict) -> dict[str, JsonDict]:
    positions = payload.get("positions")
    if not isinstance(positions, list):
        raise ValueError("standard positions payload must contain a positions list")
    return {
        str(position.get("id")): as_dict(position)
        for position in positions
        if as_dict(position).get("id")
    }


def physical_constants_payload(source: JsonDict) -> JsonDict:
    constants = as_dict(source.get("physical_constants_um"))
    payload = dict(DEFAULT_PHYSICAL_CONSTANTS_UM)
    payload.update(constants)
    return {
        "laser_rectangle_short_edge_um": positive_float(
            payload["laser_rectangle_short_edge_um"],
            "physical_constants_um.laser_rectangle_short_edge_um",
        ),
        "ball_diameter_um": positive_float(payload["ball_diameter_um"], "physical_constants_um.ball_diameter_um"),
        "trench_depth_um": positive_float(payload["trench_depth_um"], "physical_constants_um.trench_depth_um"),
    }


def require_schema(params_in: JsonDict) -> None:
    version = int(params_in.get("schema_version") or SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
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
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if result <= 0.0 or not math.isfinite(result):
        raise ValueError(f"{name} must be positive and finite")
    return result


def abort_response(message: str, *, traceback_text: str | None = None) -> JsonDict:
    response: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "action": "abort",
        "status": message,
        "machine_coordinates_um": {},
    }
    if traceback_text:
        response["traceback"] = traceback_text
    return response


def _parse_args(argv: Sequence[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Build v5 evidence from the v4 standard captures.")
    parser.add_argument("input_json", nargs="?", help="Optional ParamIn payload path.")
    parser.add_argument("--standard-positions", dest="standard_positions_path")
    parser.add_argument("--output", dest="output_path")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> JsonDict:
    args = _parse_args(argv)
    payload: JsonDict = {"schema_version": SCHEMA_VERSION}
    if args.input_json:
        payload.update(json.loads(Path(args.input_json).read_text(encoding="utf-8")))
    if args.standard_positions_path:
        payload["standard_positions_path"] = args.standard_positions_path
    result = build_standard_capture_evidence(payload)
    if args.output_path:
        output_path = Path(args.output_path)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":  # pragma: no cover - manual CLI helper
    main()
