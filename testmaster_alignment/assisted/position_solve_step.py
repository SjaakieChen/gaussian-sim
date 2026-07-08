"""Position-solve machine statement using supplied model or vision targets."""

from __future__ import annotations

from typing import Any

try:
    from testmaster_alignment.assisted._target_tools import next_target_moves, target_positions
    from testmaster_alignment.contracts import (
        JsonDict,
        abort_response,
        done_response,
        move_response,
        positions_um,
        require_schema,
    )
    from testmaster_alignment.tmpython_compat import TMPythonStatementJ
except ImportError:
    from assisted._target_tools import next_target_moves, target_positions  # type: ignore[no-redef]
    from contracts import (  # type: ignore[no-redef]
        JsonDict,
        abort_response,
        done_response,
        move_response,
        positions_um,
        require_schema,
    )
    from tmpython_compat import TMPythonStatementJ  # type: ignore[no-redef]


class PositionSolveStep(TMPythonStatementJ):
    """Move toward position-solve targets one bounded relative move at a time."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(params_in)
        except Exception as exc:
            return abort_response(f"PositionSolveStep failed: {exc}")

    def _run(self, params_in: JsonDict) -> JsonDict:
        require_schema(params_in)
        positions = positions_um(params_in)
        targets = target_positions(params_in)
        if not targets:
            return abort_response("position_solve requires targets.positions_um or model.target_positions_um")

        moves = next_target_moves(params_in, positions, targets)
        if not moves:
            return done_response("position_solve targets are within tolerance")
        return move_response(moves, "position_solve moving toward supplied target pose")
