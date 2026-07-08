"""Move toward absolute stage targets supplied by vision or a recipe."""

from __future__ import annotations

from typing import Any

try:
    from testmaster_alignment.contracts import (
        JsonDict,
        abort_response,
        done_response,
        move_response,
        positions_um,
        require_schema,
    )
    from testmaster_alignment.assisted._target_tools import next_target_moves, target_positions
    from testmaster_alignment.tmpython_compat import TMPythonStatementJ
except ImportError:
    from contracts import (  # type: ignore[no-redef]
        JsonDict,
        abort_response,
        done_response,
        move_response,
        positions_um,
        require_schema,
    )
    from assisted._target_tools import next_target_moves, target_positions  # type: ignore[no-redef]
    from tmpython_compat import TMPythonStatementJ  # type: ignore[no-redef]


class TargetPositionStep(TMPythonStatementJ):
    """Return relative moves that walk current stages toward absolute targets."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(params_in)
        except Exception as exc:
            return abort_response(f"TargetPositionStep failed: {exc}")

    def _run(self, params_in: JsonDict) -> JsonDict:
        require_schema(params_in)
        positions = positions_um(params_in)
        targets = target_positions(params_in)
        if not targets:
            return abort_response("target_positions_um is required for TargetPositionStep")

        moves = next_target_moves(params_in, positions, targets)

        if not moves:
            return done_response("all target stages are within tolerance")
        return move_response(moves, "moving toward supplied absolute target positions")
