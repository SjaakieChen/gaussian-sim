"""Apply relative stage offsets calculated by a vision script."""

from __future__ import annotations

from typing import Any

try:
    from testmaster_python_alignment_algorithm.contracts import (
        JsonDict,
        abort_response,
        algorithm_block,
        as_dict,
        clipped_distance_um,
        done_response,
        finite_float,
        max_moves_per_call,
        max_step_um,
        move_response,
        require_schema,
        validate_moves,
    )
    from testmaster_python_alignment_algorithm.tmpython_compat import TMPythonStatementJ
except ImportError:
    from contracts import (  # type: ignore[no-redef]
        JsonDict,
        abort_response,
        algorithm_block,
        as_dict,
        clipped_distance_um,
        done_response,
        finite_float,
        max_moves_per_call,
        max_step_um,
        move_response,
        require_schema,
        validate_moves,
    )
    from tmpython_compat import TMPythonStatementJ  # type: ignore[no-redef]


class VisionOffsetStep(TMPythonStatementJ):
    """Return relative moves from vision-computed stage offsets."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(params_in)
        except Exception as exc:
            return abort_response(f"VisionOffsetStep failed: {exc}")

    def _run(self, params_in: JsonDict) -> JsonDict:
        require_schema(params_in)
        vision = as_dict(params_in.get("vision"))
        confidence = finite_float(vision.get("confidence"), "vision.confidence", 1.0)
        min_confidence = finite_float(algorithm_block(params_in).get("min_confidence"), "algorithm.min_confidence", 0.0)
        if confidence < min_confidence:
            return abort_response(f"vision confidence {confidence:.3g} is below required {min_confidence:.3g}")

        offsets = _stage_offsets(params_in)
        if not offsets:
            return abort_response("vision.stage_offsets_um is required for VisionOffsetStep")

        max_step = max_step_um(params_in)
        moves = []
        for stage, distance in offsets.items():
            clipped = clipped_distance_um(distance, max_step)
            if clipped != 0.0:
                moves.append((stage, clipped))
            if len(moves) >= max_moves_per_call(params_in):
                break

        if not moves:
            return done_response("vision offsets are zero")
        return move_response(validate_moves(params_in, moves), "applying vision-computed relative stage offsets")


def _stage_offsets(params_in: JsonDict) -> dict[str, float]:
    raw_offsets = as_dict(params_in.get("stage_offsets_um"))
    if not raw_offsets:
        raw_offsets = as_dict(as_dict(params_in.get("vision")).get("stage_offsets_um"))
    return {str(stage): finite_float(value, f"vision.stage_offsets_um.{stage}") for stage, value in raw_offsets.items()}
