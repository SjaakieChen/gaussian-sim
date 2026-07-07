"""Fixed-Z J-matrix machine statement."""

from __future__ import annotations

import math
from typing import Any

try:
    from testmaster_python_alignment_algorithm.assisted._target_tools import next_target_moves, target_positions
    from testmaster_python_alignment_algorithm.contracts import (
        JsonDict,
        abort_response,
        as_dict,
        clipped_distance_um,
        done_response,
        finite_float,
        max_moves_per_call,
        max_step_um,
        model_block,
        move_response,
        positions_um,
        require_schema,
        validate_moves,
    )
    from testmaster_python_alignment_algorithm.tmpython_compat import TMPythonStatementJ
except ImportError:
    from assisted._target_tools import next_target_moves, target_positions  # type: ignore[no-redef]
    from contracts import (  # type: ignore[no-redef]
        JsonDict,
        abort_response,
        as_dict,
        clipped_distance_um,
        done_response,
        finite_float,
        max_moves_per_call,
        max_step_um,
        model_block,
        move_response,
        positions_um,
        require_schema,
        validate_moves,
    )
    from tmpython_compat import TMPythonStatementJ  # type: ignore[no-redef]


DEFAULT_X_STAGES = ("Align_X1", "Align_X2")
DEFAULT_Z_STAGES = ("Align_Z1", "Align_Z2")


class FixedZJMatrixStep(TMPythonStatementJ):
    """Return transverse-only J-matrix corrections; never requests Align_Y moves."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(params_in)
        except Exception as exc:
            return abort_response(f"FixedZJMatrixStep failed: {exc}")

    def _run(self, params_in: JsonDict) -> JsonDict:
        require_schema(params_in)
        positions = positions_um(params_in)

        matrix_moves = _matrix_correction_moves(params_in)
        if matrix_moves:
            return move_response(matrix_moves, "fixed_z_j_matrix applying supplied matrix correction")

        targets = _transverse_targets(target_positions(params_in))
        if targets:
            moves = next_target_moves(params_in, positions, targets, exclude_prefixes=("Align_Y",))
            if moves:
                return move_response(moves, "fixed_z_j_matrix moving toward transverse target positions")
            return done_response("fixed_z_j_matrix transverse targets are within tolerance")

        return abort_response("fixed_z_j_matrix requires model.j_matrix+model.beam_error or transverse target positions")


def _transverse_targets(targets: dict[str, float]) -> dict[str, float]:
    return {stage: value for stage, value in targets.items() if not stage.startswith("Align_Y")}


def _matrix_correction_moves(params_in: JsonDict) -> list[tuple[str, float]]:
    model = model_block(params_in)
    j_matrix = as_dict(model.get("j_matrix"))
    beam_error = as_dict(model.get("beam_error"))
    if not j_matrix or not beam_error:
        return []

    max_step = max_step_um(params_in)
    max_moves = max_moves_per_call(params_in)
    moves: list[tuple[str, float]] = []
    for axis_name, default_stages, error_names in (
        ("x", DEFAULT_X_STAGES, ("x_um", "x_angle_mrad")),
        ("z", DEFAULT_Z_STAGES, ("z_um", "z_angle_mrad")),
    ):
        raw_matrix = j_matrix.get(axis_name)
        if raw_matrix is None:
            continue
        error = (
            finite_float(beam_error.get(error_names[0]), f"model.beam_error.{error_names[0]}", 0.0),
            finite_float(beam_error.get(error_names[1]), f"model.beam_error.{error_names[1]}", 0.0),
        )
        correction = _solve_2x2(raw_matrix, (-error[0], -error[1]))
        stages = tuple(as_dict(model.get("response_stages")).get(axis_name, default_stages))
        for stage, distance in zip(stages, correction):
            clipped = clipped_distance_um(distance, max_step)
            if not math.isclose(clipped, 0.0, rel_tol=0.0, abs_tol=1.0e-12):
                moves.append((str(stage), clipped))
            if len(moves) >= max_moves:
                return validate_moves(params_in, moves)
    return validate_moves(params_in, moves)


def _solve_2x2(raw_matrix: Any, target: tuple[float, float]) -> tuple[float, float]:
    if not isinstance(raw_matrix, list) or len(raw_matrix) != 2:
        raise ValueError("model.j_matrix entries must be 2x2 arrays")
    row0, row1 = raw_matrix
    if not isinstance(row0, list) or not isinstance(row1, list) or len(row0) != 2 or len(row1) != 2:
        raise ValueError("model.j_matrix entries must be 2x2 arrays")
    a = finite_float(row0[0], "model.j_matrix[0][0]")
    b = finite_float(row0[1], "model.j_matrix[0][1]")
    c = finite_float(row1[0], "model.j_matrix[1][0]")
    d = finite_float(row1[1], "model.j_matrix[1][1]")
    det = a * d - b * c
    if abs(det) < 1.0e-12:
        raise ValueError("model.j_matrix is singular or nearly singular")
    y0, y1 = target
    return ((d * y0 - b * y1) / det, (-c * y0 + a * y1) / det)
