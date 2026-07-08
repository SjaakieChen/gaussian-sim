"""TMPython checkout statement that requests one small relative stage move."""

from __future__ import annotations

from typing import Any

try:
    from testmaster_alignment.contracts import (
        JsonDict,
        abort_response,
        algorithm_block,
        finite_float,
        limits_block,
        max_step_um,
        move_response,
        require_schema,
        validate_moves,
    )
    from testmaster_alignment.tmpython_compat import TMPythonStatementJ
except ImportError:
    from contracts import (  # type: ignore[no-redef]
        JsonDict,
        abort_response,
        algorithm_block,
        finite_float,
        limits_block,
        max_step_um,
        move_response,
        require_schema,
        validate_moves,
    )
    from tmpython_compat import TMPythonStatementJ  # type: ignore[no-redef]


class MovementCommandTestStep(TMPythonStatementJ):
    """Return exactly one validated relative move request for machine checkout."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(params_in)
        except Exception as exc:
            return abort_response(f"MovementCommandTestStep failed: {exc}")

    def _run(self, params_in: JsonDict) -> JsonDict:
        require_schema(params_in)

        algorithm = algorithm_block(params_in)
        allowed_stages = _allowed_stages(params_in)
        if not allowed_stages:
            return abort_response("limits.allowed_stages must contain the approved test stage")

        stage = str(algorithm.get("stage") or allowed_stages[0])
        if stage not in allowed_stages:
            return abort_response(f"algorithm.stage {stage!r} is not in limits.allowed_stages")

        distance_um = finite_float(algorithm.get("distance_um", 0.1), "algorithm.distance_um")
        if abs(distance_um) <= 1.0e-12:
            return abort_response("algorithm.distance_um must be non-zero for the movement checkout")

        maximum_step_um = max_step_um(params_in)
        if abs(distance_um) > maximum_step_um:
            return abort_response(
                f"algorithm.distance_um {distance_um:.6g} exceeds limits.max_step_um {maximum_step_um:.6g}"
            )

        moves = validate_moves(params_in, [(stage, distance_um)])
        return move_response(
            moves,
            f"movement command checkout requested {stage} relative move by {distance_um:.6g} um",
            {
                "algorithm": "movement_command_test",
                "requested_stage": stage,
                "requested_distance_um": distance_um,
                "max_step_um": maximum_step_um,
                "test_only": True,
            },
        )


def _allowed_stages(params_in: JsonDict) -> list[str]:
    raw_stages = limits_block(params_in).get("allowed_stages", [])
    return [str(stage) for stage in raw_stages if str(stage)]
