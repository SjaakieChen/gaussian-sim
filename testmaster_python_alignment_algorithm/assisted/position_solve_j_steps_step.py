"""Position-solve visible-step machine statement."""

from __future__ import annotations

from typing import Any

try:
    from testmaster_python_alignment_algorithm.assisted._target_tools import (
        next_target_moves,
        target_path,
        target_positions,
    )
    from testmaster_python_alignment_algorithm.contracts import (
        JsonDict,
        abort_response,
        done_response,
        move_response,
        positions_um,
        require_schema,
    )
    from testmaster_python_alignment_algorithm.tmpython_compat import TMPythonStatementJ
except ImportError:
    from assisted._target_tools import next_target_moves, target_path, target_positions  # type: ignore[no-redef]
    from contracts import (  # type: ignore[no-redef]
        JsonDict,
        abort_response,
        done_response,
        move_response,
        positions_um,
        require_schema,
    )
    from tmpython_compat import TMPythonStatementJ  # type: ignore[no-redef]


class PositionSolveJStepsStep(TMPythonStatementJ):
    """Walk through a supplied position-solve path, one target point at a time."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(params_in)
        except Exception as exc:
            state = params_in.get("state") if isinstance(params_in, dict) else None
            return abort_response(f"PositionSolveJStepsStep failed: {exc}", state if isinstance(state, dict) else None)

    def _run(self, params_in: JsonDict) -> JsonDict:
        require_schema(params_in)
        positions = positions_um(params_in)
        path = target_path(params_in)
        if not path:
            final_target = target_positions(params_in)
            if not final_target:
                return abort_response("position_solve_j_steps requires targets.path_um or target positions")
            path = [final_target]

        state = dict(params_in.get("state") if isinstance(params_in.get("state"), dict) else {})
        if state.get("algorithm") != "position_solve_j_steps":
            state = {"algorithm": "position_solve_j_steps", "path_index": 0}

        path_index = int(state.get("path_index", 0))
        if path_index >= len(path):
            return done_response("position_solve_j_steps path is complete", state)

        target = path[path_index]
        moves = next_target_moves(params_in, positions, target)
        if not moves:
            state["path_index"] = path_index + 1
            if state["path_index"] >= len(path):
                return done_response("position_solve_j_steps path is complete", state)
            target = path[state["path_index"]]
            moves = next_target_moves(params_in, positions, target)

        if not moves:
            return done_response("position_solve_j_steps path is within tolerance", state)
        return move_response(moves, f"position_solve_j_steps moving toward path point {state['path_index'] + 1}", state)
