"""Shared JSON contract helpers for machine-facing alignment statements."""

from __future__ import annotations

import math
from typing import Any


SCHEMA_VERSION = 1
DEFAULT_MAX_STEP_UM = 2.0
DEFAULT_TOLERANCE_UM = 0.05
DEFAULT_ALLOWED_STAGES = ("Align_X1", "Align_Z1", "Align_X2", "Align_Z2")


JsonDict = dict[str, Any]
MoveList = list[tuple[str, float]]


def require_schema(params: JsonDict) -> None:
    version = params.get("schema_version")
    if int(version or 0) != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}")


def as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def finite_float(value: Any, name: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def finite_positive_float(value: Any, name: str, default: float) -> float:
    result = finite_float(value, name, default)
    if result <= 0.0:
        return default
    return result


def positions_um(params: JsonDict) -> dict[str, float]:
    raw_positions = as_dict(params.get("positions_um"))
    if not raw_positions:
        raw_positions = as_dict(as_dict(params.get("machine")).get("positions_um"))
    positions: dict[str, float] = {}
    for stage, value in raw_positions.items():
        positions[str(stage)] = finite_float(value, f"positions_um.{stage}")
    if not positions:
        raise ValueError("positions_um must contain the current absolute stage positions")
    return positions


def power_mw(params: JsonDict) -> float:
    value = params.get("power_mw")
    if value is None:
        value = as_dict(params.get("machine")).get("power_mw")
    return finite_float(value, "power_mw")


def algorithm_block(params: JsonDict) -> JsonDict:
    return as_dict(params.get("algorithm"))


def limits_block(params: JsonDict) -> JsonDict:
    return as_dict(params.get("limits"))


def vision_block(params: JsonDict) -> JsonDict:
    return as_dict(params.get("vision"))


def targets_block(params: JsonDict) -> JsonDict:
    return as_dict(params.get("targets"))


def model_block(params: JsonDict) -> JsonDict:
    return as_dict(params.get("model"))


def configured_stages(params: JsonDict, default: tuple[str, ...] = DEFAULT_ALLOWED_STAGES) -> list[str]:
    algorithm = algorithm_block(params)
    limits = limits_block(params)
    raw_stages = algorithm.get("axis_stages") or algorithm.get("stage_order") or limits.get("allowed_stages") or default
    stages = [str(stage) for stage in raw_stages if str(stage)]
    return list(dict.fromkeys(stages))


def max_step_um(params: JsonDict, default: float = DEFAULT_MAX_STEP_UM) -> float:
    algorithm = algorithm_block(params)
    limits = limits_block(params)
    value = algorithm.get("max_step_um", limits.get("max_step_um", default))
    return finite_positive_float(value, "max_step_um", default)


def tolerance_um(params: JsonDict, default: float = DEFAULT_TOLERANCE_UM) -> float:
    algorithm = algorithm_block(params)
    value = algorithm.get("tolerance_um", default)
    return finite_positive_float(value, "tolerance_um", default)


def max_moves_per_call(params: JsonDict, default: int = 1) -> int:
    value = algorithm_block(params).get("max_moves_per_call", default)
    try:
        count = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(2, count))


def clipped_distance_um(distance_um: float, max_abs_um: float) -> float:
    if abs(distance_um) <= max_abs_um:
        return distance_um
    return math.copysign(max_abs_um, distance_um)


def allowed_stage_set(params: JsonDict) -> set[str]:
    raw_allowed = limits_block(params).get("allowed_stages")
    if raw_allowed is None:
        return set()
    return {str(stage) for stage in raw_allowed}


def validate_moves(params: JsonDict, moves: MoveList) -> MoveList:
    allowed = allowed_stage_set(params)
    max_abs = max_step_um(params)
    validated: MoveList = []
    for stage, distance in moves:
        stage = str(stage)
        if allowed and stage not in allowed:
            raise ValueError(f"stage {stage!r} is not in limits.allowed_stages")
        finite_distance = finite_float(distance, f"distance for {stage}")
        if abs(finite_distance) > max_abs + 1.0e-12:
            raise ValueError(f"distance for {stage} exceeds max_step_um")
        if not math.isclose(finite_distance, 0.0, rel_tol=0.0, abs_tol=1.0e-12):
            validated.append((stage, finite_distance))
    return validated


def move_response(moves: MoveList, message: str, state: JsonDict | None = None) -> JsonDict:
    if len(moves) > 2:
        raise ValueError("output contract supports at most two moves per call")
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "move" if moves else "done",
        "move_count": len(moves),
        "stage1": moves[0][0] if moves else "",
        "distance1_um": moves[0][1] if moves else 0.0,
        "moves": [
            {"stage": stage, "distance_um": distance, "mode": "relative"}
            for stage, distance in moves
        ],
        "message": message,
    }
    if len(moves) == 2:
        result["stage2"] = moves[1][0]
        result["distance2_um"] = moves[1][1]
    if state is not None:
        result["state"] = state
    return result


def done_response(message: str, state: JsonDict | None = None) -> JsonDict:
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "done",
        "move_count": 0,
        "stage1": "",
        "distance1_um": 0.0,
        "moves": [],
        "message": message,
    }
    if state is not None:
        result["state"] = state
    return result


def abort_response(message: str, state: JsonDict | None = None) -> JsonDict:
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "abort",
        "move_count": 0,
        "stage1": "",
        "distance1_um": 0.0,
        "moves": [],
        "message": message,
    }
    if state is not None:
        result["state"] = state
    return result
