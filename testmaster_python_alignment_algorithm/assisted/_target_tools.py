"""Helpers for target-based TestMaster alignment statements."""

from __future__ import annotations

from typing import Iterable

try:
    from testmaster_python_alignment_algorithm.contracts import (
        JsonDict,
        as_dict,
        clipped_distance_um,
        configured_stages,
        finite_float,
        max_moves_per_call,
        max_step_um,
        model_block,
        targets_block,
        tolerance_um,
        validate_moves,
        vision_block,
    )
except ImportError:
    from contracts import (  # type: ignore[no-redef]
        JsonDict,
        as_dict,
        clipped_distance_um,
        configured_stages,
        finite_float,
        max_moves_per_call,
        max_step_um,
        model_block,
        targets_block,
        tolerance_um,
        validate_moves,
        vision_block,
    )


def target_positions(params_in: JsonDict) -> dict[str, float]:
    raw_targets = as_dict(params_in.get("target_positions_um"))
    if not raw_targets:
        raw_targets = as_dict(targets_block(params_in).get("positions_um"))
    if not raw_targets:
        raw_targets = as_dict(vision_block(params_in).get("target_positions_um") or vision_block(params_in).get("targets_um"))
    if not raw_targets:
        raw_targets = as_dict(model_block(params_in).get("target_positions_um") or model_block(params_in).get("positions_um"))
    return {str(stage): finite_float(value, f"target_positions_um.{stage}") for stage, value in raw_targets.items()}


def target_path(params_in: JsonDict) -> list[dict[str, float]]:
    raw_path = params_in.get("target_path_um")
    if raw_path is None:
        raw_path = targets_block(params_in).get("path_um")
    if raw_path is None:
        raw_path = model_block(params_in).get("target_path_um")
    if not isinstance(raw_path, list):
        return []
    path = []
    for index, raw_target in enumerate(raw_path):
        if not isinstance(raw_target, dict):
            raise ValueError(f"target_path_um[{index}] must be an object")
        path.append({str(stage): finite_float(value, f"target_path_um[{index}].{stage}") for stage, value in raw_target.items()})
    return path


def next_target_moves(
    params_in: JsonDict,
    positions: dict[str, float],
    targets: dict[str, float],
    *,
    default_stage_order: Iterable[str] | None = None,
    exclude_prefixes: tuple[str, ...] = (),
) -> list[tuple[str, float]]:
    if not targets:
        return []
    default = tuple(default_stage_order or targets)
    stages = [
        stage
        for stage in configured_stages(params_in, default)
        if stage in targets and stage in positions and not stage.startswith(exclude_prefixes)
    ]
    tolerance = tolerance_um(params_in)
    max_step = max_step_um(params_in)
    moves = []
    for stage in stages:
        delta = targets[stage] - positions[stage]
        if abs(delta) <= tolerance:
            continue
        moves.append((stage, clipped_distance_um(delta, max_step)))
        if len(moves) >= max_moves_per_call(params_in):
            break
    return validate_moves(params_in, moves)
