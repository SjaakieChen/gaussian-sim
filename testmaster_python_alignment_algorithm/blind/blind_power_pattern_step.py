"""Blind power-only pattern search statement for TMPython_ExecuteScript."""

from __future__ import annotations

import math
from typing import Any

try:
    from testmaster_python_alignment_algorithm.contracts import (
        JsonDict,
        abort_response,
        algorithm_block,
        clipped_distance_um,
        configured_stages,
        done_response,
        finite_float,
        max_step_um,
        move_response,
        positions_um,
        power_mw,
        require_schema,
        validate_moves,
    )
    from testmaster_python_alignment_algorithm.tmpython_compat import TMPythonStatementJ
except ImportError:
    from contracts import (  # type: ignore[no-redef]
        JsonDict,
        abort_response,
        algorithm_block,
        clipped_distance_um,
        configured_stages,
        done_response,
        finite_float,
        max_step_um,
        move_response,
        positions_um,
        power_mw,
        require_schema,
        validate_moves,
    )
    from tmpython_compat import TMPythonStatementJ  # type: ignore[no-redef]


ALGORITHM_NAME = "blind_power_j"
DEFAULT_STEPS_UM = (2.0, 1.0, 0.5, 0.25)
DEFAULT_DIRECTIONS = (1.0, -1.0)
DEFAULT_MAX_ITERATIONS = 200


class BlindPowerPatternStep(TMPythonStatementJ):
    """Return one relative probing move for a blind power-only alignment loop."""

    algorithm_name = ALGORITHM_NAME
    default_steps_um = DEFAULT_STEPS_UM
    default_directions = DEFAULT_DIRECTIONS

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(params_in)
        except Exception as exc:  # TestMaster should receive JSON even on bad input.
            state = params_in.get("state") if isinstance(params_in, dict) else None
            return abort_response(f"BlindPowerPatternStep failed: {exc}", state if isinstance(state, dict) else None)

    def _run(self, params_in: JsonDict) -> JsonDict:
        require_schema(params_in)
        positions = positions_um(params_in)
        power = power_mw(params_in)
        axes = [stage for stage in configured_stages(params_in) if stage in positions]
        if not axes:
            return abort_response("no configured stages are present in positions_um")

        algorithm = algorithm_block(params_in)
        steps = _configured_steps(algorithm, max_step_um(params_in), self.default_steps_um)
        directions = _configured_directions(algorithm, self.default_directions)
        if not steps:
            return abort_response("algorithm.step_um must contain at least one positive step")

        state = _state_for(params_in, power, positions, self.algorithm_name)
        max_iterations = int(algorithm.get("max_iterations", DEFAULT_MAX_ITERATIONS))
        if int(params_in.get("iteration", 0)) >= max_iterations:
            return done_response("blind search reached max_iterations", state)

        pending = state.get("pending")
        if isinstance(pending, dict) and pending.get("kind") == "probe":
            handled = self._handle_probe_result(params_in, state, positions, power, axes, steps, directions)
            if handled is not None:
                return handled

        pending = state.get("pending")
        if isinstance(pending, dict) and pending.get("kind") == "return":
            handled = self._handle_return_to_best(params_in, state, positions)
            if handled is not None:
                return handled

        return self._propose_next_probe(params_in, state, axes, steps, directions)

    def _handle_probe_result(
        self,
        params_in: JsonDict,
        state: JsonDict,
        positions: dict[str, float],
        power: float,
        axes: list[str],
        steps: list[float],
        directions: list[float],
    ) -> JsonDict | None:
        pending = state["pending"]
        stage = str(pending["stage"])
        previous_best = finite_float(state.get("best_power_mw"), "state.best_power_mw", power)
        if _is_better(power, previous_best, algorithm_block(params_in)):
            state["best_power_mw"] = power
            state["best_positions_um"] = dict(positions)
            state["accepted_moves"] = int(state.get("accepted_moves", 0)) + 1
            state["pending"] = None
            _advance_cursor(state, axes, directions)
            return None

        best_positions = state.get("best_positions_um") if isinstance(state.get("best_positions_um"), dict) else {}
        target = finite_float(best_positions.get(stage), f"state.best_positions_um.{stage}", positions[stage])
        distance = clipped_distance_um(target - positions[stage], max_step_um(params_in))
        _advance_cursor(state, axes, directions)
        state["pending"] = {"kind": "return", "stage": stage}
        moves = validate_moves(params_in, [(stage, distance)])
        if not moves:
            state["pending"] = None
            return None
        return move_response(moves, f"probe did not improve power; returning {stage} to best position", state)

    def _handle_return_to_best(
        self,
        params_in: JsonDict,
        state: JsonDict,
        positions: dict[str, float],
    ) -> JsonDict | None:
        pending = state["pending"]
        stage = str(pending["stage"])
        best_positions = state.get("best_positions_um") if isinstance(state.get("best_positions_um"), dict) else {}
        target = finite_float(best_positions.get(stage), f"state.best_positions_um.{stage}", positions[stage])
        remaining = target - positions[stage]
        if abs(remaining) > 0.01:
            distance = clipped_distance_um(remaining, max_step_um(params_in))
            moves = validate_moves(params_in, [(stage, distance)])
            if moves:
                return move_response(moves, f"continuing return of {stage} to best position", state)
        state["pending"] = None
        return None

    def _propose_next_probe(
        self,
        params_in: JsonDict,
        state: JsonDict,
        axes: list[str],
        steps: list[float],
        directions: list[float],
    ) -> JsonDict:
        step_index = int(state.get("step_index", 0))
        if step_index >= len(steps):
            return done_response(
                f"blind search finished; best power {state.get('best_power_mw', 0.0):.6g} mW",
                state,
            )

        stage_index = int(state.get("stage_index", 0)) % len(axes)
        direction_index = int(state.get("direction_index", 0)) % len(directions)
        stage = axes[stage_index]
        distance = clipped_distance_um(steps[step_index] * directions[direction_index], max_step_um(params_in))
        if math.isclose(distance, 0.0, rel_tol=0.0, abs_tol=1.0e-12):
            _advance_cursor(state, axes, directions)
            return self._propose_next_probe(params_in, state, axes, steps, directions)

        state["step_index"] = step_index
        state["stage_index"] = stage_index
        state["direction_index"] = direction_index
        state["pending"] = {"kind": "probe", "stage": stage, "distance_um": distance}
        state["probe_count"] = int(state.get("probe_count", 0)) + 1
        moves = validate_moves(params_in, [(stage, distance)])
        return move_response(moves, f"probing {stage} by {distance:.6g} um", state)


def _state_for(params_in: JsonDict, power: float, positions: dict[str, float], algorithm_name: str) -> JsonDict:
    state = params_in.get("state") if isinstance(params_in.get("state"), dict) else {}
    if state.get("algorithm") != algorithm_name:
        return {
            "algorithm": algorithm_name,
            "best_power_mw": power,
            "best_positions_um": dict(positions),
            "step_index": 0,
            "stage_index": 0,
            "direction_index": 0,
            "pending": None,
            "probe_count": 0,
            "accepted_moves": 0,
        }
    return dict(state)


def _configured_steps(algorithm: JsonDict, max_distance_um: float, default_steps_um: tuple[float, ...]) -> list[float]:
    raw_steps = algorithm.get("step_um", default_steps_um)
    steps = []
    for raw_step in raw_steps:
        step = abs(finite_float(raw_step, "algorithm.step_um"))
        if step > 0.0:
            steps.append(min(step, max_distance_um))
    return steps


def _configured_directions(algorithm: JsonDict, default_directions: tuple[float, ...]) -> list[float]:
    raw_directions = algorithm.get("directions", default_directions)
    directions = []
    for raw_direction in raw_directions:
        direction = finite_float(raw_direction, "algorithm.directions")
        if direction != 0.0:
            directions.append(math.copysign(1.0, direction))
    return directions or list(default_directions)


def _advance_cursor(state: JsonDict, axes: list[str], directions: list[float]) -> None:
    direction_index = int(state.get("direction_index", 0)) + 1
    stage_index = int(state.get("stage_index", 0))
    step_index = int(state.get("step_index", 0))
    if direction_index >= len(directions):
        direction_index = 0
        stage_index += 1
    if stage_index >= len(axes):
        stage_index = 0
        step_index += 1
    state["direction_index"] = direction_index
    state["stage_index"] = stage_index
    state["step_index"] = step_index


def _is_better(candidate_power: float, best_power: float, algorithm: JsonDict) -> bool:
    abs_tol = finite_float(algorithm.get("power_abs_tolerance_mw"), "algorithm.power_abs_tolerance_mw", 1.0e-9)
    rel_tol = finite_float(algorithm.get("power_rel_tolerance"), "algorithm.power_rel_tolerance", 1.0e-4)
    threshold = max(abs_tol, abs(best_power) * rel_tol)
    return candidate_power > best_power + threshold
