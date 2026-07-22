"""End-to-end read-only macro alignment simulation for v5 vision captures.

This wrapper connects the two v5 stages:

1. gross dual-view ball detection -> bounded close-position bias plan;
2. close focused captures -> ball positions in machine coordinates.

It is an offline simulator and does not move hardware.
"""

from __future__ import annotations

import json
import traceback
from pathlib import Path
from typing import Any, Sequence

try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - used on developer machines without TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback so the simulator can be tested outside TestMaster."""


from .position_bias_planner import plan_biased_close_positions
from .sequence_geometry_memory import solve_sequence_geometry


SCHEMA_VERSION = 1
DEFAULT_STANDARD_POSITIONS_PATH = Path(__file__).resolve().parents[3] / "Standard position images" / "v4" / "standard_positions.json"
DEFAULT_BALL_DIAMETER_UM = 500.0
DEFAULT_TRENCH_DEPTH_UM = 300.0
DEFAULT_GROSS_OBSERVATIONS = (
    {"target": "ball_1", "gross_capture_id": "2.1.1"},
    {"target": "ball_2", "gross_capture_id": "4.1.1"},
)

JsonDict = dict[str, Any]


class VisionMacroAlignmentSimulatorStep(TMPythonStatementJ):
    """TMPython entrypoint for read-only v5 macro alignment simulation."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return simulate_macro_alignment(params_in)
        except Exception as exc:  # fail closed for machine callers
            return abort_response(
                f"VisionMacroAlignmentSimulatorStep failed: {exc}",
                traceback_text=traceback.format_exc(),
            )


def simulate_macro_alignment(params_in: JsonDict) -> JsonDict:
    """Run gross bias planning and final sequence geometry as one machine-coordinate workflow."""

    try:
        require_schema(params_in)
        standard_positions_path = str(params_in.get("standard_positions_path") or DEFAULT_STANDARD_POSITIONS_PATH)
        physical_constants = physical_constants_payload(params_in)

        gross_payload = build_gross_bias_payload(
            params_in,
            standard_positions_path=standard_positions_path,
            ball_diameter_um=float(physical_constants["ball_diameter_um"]),
        )
        gross_result = plan_biased_close_positions(gross_payload)
        if not gross_result.get("ok"):
            return abort_response(f"gross bias planning failed: {gross_result.get('status')}", gross_result=gross_result)

        geometry_payload = build_sequence_geometry_payload(
            params_in,
            standard_positions_path=standard_positions_path,
            physical_constants=physical_constants,
        )
        geometry_result = solve_sequence_geometry(geometry_payload)
        if not geometry_result.get("ok"):
            return abort_response(
                f"sequence geometry solving failed: {geometry_result.get('status')}",
                gross_result=gross_result,
                geometry_result=geometry_result,
            )

        close_position_summary = close_position_summary_from_gross_result(gross_result)
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "action": "macro_alignment_simulated",
            "status": "v5 macro alignment simulation solved",
            "standard_positions_path": standard_positions_path,
            "workflow": [
                "gross_ball_detection",
                "bounded_close_position_bias",
                "reference_rectangle_memory",
                "top_ball_memory",
                "side_reference_diagnostics",
                "final_ball_coordinates",
            ],
            "physical_constants_um": physical_constants,
            "gross_bias": gross_result,
            "close_position_summary": close_position_summary,
            "sequence_geometry": geometry_result,
            "machine_coordinate_system": geometry_result["machine_coordinate_system"],
            "machine_coordinates_um": geometry_result["machine_coordinates_um"],
            "feature_memory": geometry_result["feature_memory"],
            "focus_memory": geometry_result["focus_memory"],
            "machine_y_source": geometry_result.get("machine_y_source", "trench_model"),
        }
    except Exception as exc:
        return abort_response(str(exc))


def build_gross_bias_payload(
    params_in: JsonDict,
    *,
    standard_positions_path: str,
    ball_diameter_um: float,
) -> JsonDict:
    observations = params_in.get("gross_observations")
    if observations is None:
        observations = [dict(observation) for observation in DEFAULT_GROSS_OBSERVATIONS]
    if not isinstance(observations, list) or not observations:
        raise ValueError("gross_observations must be a non-empty list when provided")

    candidate_image_paths = as_dict(params_in.get("gross_candidate_image_paths"))
    baseline_image_paths = as_dict(params_in.get("gross_baseline_image_paths"))
    prepared_observations: list[JsonDict] = []
    for raw_observation in observations:
        observation = dict(as_dict(raw_observation))
        capture_id = str(observation.get("gross_capture_id") or observation.get("capture_id") or "").strip()
        if capture_id:
            if "candidate_image_path" not in observation and capture_id in candidate_image_paths:
                observation["candidate_image_path"] = candidate_image_paths[capture_id]
            if "baseline_image_path" not in observation and capture_id in baseline_image_paths:
                observation["baseline_image_path"] = baseline_image_paths[capture_id]
        if "um_per_pixel" not in observation and "pixels_per_um" not in observation:
            observation.setdefault("estimate_um_per_pixel_from_ball_diameter", True)
            observation.setdefault("ball_diameter_um", ball_diameter_um)
        prepared_observations.append(observation)

    payload: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "standard_positions_path": standard_positions_path,
        "gross_observations": prepared_observations,
        "auto_detect_gross_sessions": bool(params_in.get("auto_detect_gross_sessions", True)),
    }
    for key in (
        "standard_positions",
        "default_close_position_bias",
        "gross_view_mapping",
        "bias_mappings",
        "standard_image_paths",
        "gross_auto_feature_specs",
    ):
        if key in params_in:
            payload[key] = params_in[key]
    return payload


def build_sequence_geometry_payload(
    params_in: JsonDict,
    *,
    standard_positions_path: str,
    physical_constants: JsonDict,
) -> JsonDict:
    payload: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "standard_positions_path": standard_positions_path,
        "physical_constants_um": physical_constants,
        "auto_detect_missing_sessions": bool(params_in.get("auto_detect_missing_sessions", True)),
        "auto_detect_side_references": bool(params_in.get("auto_detect_side_references", True)),
        "machine_y_source": params_in.get("machine_y_source", "trench_model"),
    }
    for key in (
        "standard_positions",
        "targets",
        "sessions",
        "baseline_dir",
        "view_mappings",
        "top_camera_lateral_tolerance_um",
        "standard_image_paths",
        "auto_feature_specs",
    ):
        if key in params_in:
            payload[key] = params_in[key]
    return payload


def physical_constants_payload(params_in: JsonDict) -> JsonDict:
    source = as_dict(params_in.get("physical_constants_um"))
    return {
        "laser_rectangle_short_edge_um": float(source.get("laser_rectangle_short_edge_um", 500.0)),
        "ball_diameter_um": float(source.get("ball_diameter_um", DEFAULT_BALL_DIAMETER_UM)),
        "trench_depth_um": float(source.get("trench_depth_um", DEFAULT_TRENCH_DEPTH_UM)),
    }


def close_position_summary_from_gross_result(gross_result: JsonDict) -> JsonDict:
    summary: JsonDict = {}
    for raw_plan in gross_result.get("plans") or ():
        plan = as_dict(raw_plan)
        target = str(plan.get("target") or "")
        if not target:
            continue
        summary[target] = {
            "gross_capture_id": plan.get("gross_capture_id"),
            "pixel_shift": plan.get("pixel_shift"),
            "um_per_pixel": plan.get("um_per_pixel"),
            "applied_bias_um": plan.get("applied_bias_um"),
            "bias_clipped": plan.get("bias_clipped"),
            "close_position_ids": plan.get("close_position_ids"),
        }
    return summary


def abort_response(
    message: str,
    *,
    traceback_text: str | None = None,
    gross_result: JsonDict | None = None,
    geometry_result: JsonDict | None = None,
) -> JsonDict:
    response: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "ok": False,
        "action": "abort",
        "status": message,
        "machine_coordinates_um": {},
    }
    if gross_result is not None:
        response["gross_bias"] = gross_result
    if geometry_result is not None:
        response["sequence_geometry"] = geometry_result
    if traceback_text:
        response["traceback"] = traceback_text
    return response


def require_schema(params_in: JsonDict) -> None:
    version = params_in.get("schema_version")
    if int(version or 0) != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}")


def as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def _parse_args(argv: Sequence[str] | None = None) -> Any:
    import argparse

    parser = argparse.ArgumentParser(description="Run the v5 macro alignment simulator.")
    parser.add_argument("input_json", help="Input JSON payload path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> JsonDict:
    args = _parse_args(argv)
    payload = json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    result = simulate_macro_alignment(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


if __name__ == "__main__":  # pragma: no cover - manual CLI helper
    main()
