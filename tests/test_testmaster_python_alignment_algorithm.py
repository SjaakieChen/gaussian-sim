from __future__ import annotations

import json
from pathlib import Path

from testmaster_python_alignment_algorithm.alignment_step import BlindAlignStep
from testmaster_python_alignment_algorithm.assisted.fixed_z_j_matrix_step import FixedZJMatrixStep
from testmaster_python_alignment_algorithm.assisted.position_solve_j_steps_step import PositionSolveJStepsStep
from testmaster_python_alignment_algorithm.assisted.position_solve_step import PositionSolveStep
from testmaster_python_alignment_algorithm.assisted.target_position_step import TargetPositionStep
from testmaster_python_alignment_algorithm.assisted.vision_offset_step import VisionOffsetStep
from testmaster_python_alignment_algorithm.blind.blind_power_j_best_of_9_step import BlindPowerJBestOf9Step
from testmaster_python_alignment_algorithm.blind.blind_power_j_gradient_step import BlindPowerJGradientStep
from testmaster_python_alignment_algorithm.blind.blind_power_j_newton_step import BlindPowerJNewtonStep
from testmaster_python_alignment_algorithm.blind.blind_power_j_step import BlindPowerJStep


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "testmaster_python_alignment_algorithm" / "examples"


def _load_example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def test_blind_alignment_step_returns_stateful_move_contract():
    result = BlindAlignStep().run(_load_example("blind_power_input.json"))

    assert result["schema_version"] == 1
    assert result["action"] == "move"
    assert result["move_count"] == 1
    assert result["stage1"] == "Align_X1"
    assert result["distance1_um"] == 2.0
    assert result["moves"] == [{"stage": "Align_X1", "distance_um": 2.0, "mode": "relative"}]
    assert result["state"]["algorithm"] == "blind_power_j"


def test_target_position_step_moves_toward_absolute_target():
    result = TargetPositionStep().run(_load_example("target_position_input.json"))

    assert result["schema_version"] == 1
    assert result["action"] == "move"
    assert result["stage1"] == "Align_X1"
    assert result["distance1_um"] == 1.5
    assert result["moves"][0]["stage"] == "Align_X1"


def test_vision_offset_step_applies_relative_vision_offset():
    result = VisionOffsetStep().run(_load_example("vision_offset_input.json"))

    assert result["schema_version"] == 1
    assert result["action"] == "move"
    assert result["stage1"] == "Align_X1"
    assert result["distance1_um"] == 1.5
    assert result["moves"][0]["stage"] == "Align_X1"


def test_each_blind_dropdown_method_has_independent_statement_class():
    expected = [
        (BlindPowerJStep, "blind_power_j"),
        (BlindPowerJNewtonStep, "blind_power_j_newton"),
        (BlindPowerJGradientStep, "blind_power_j_gradient"),
        (BlindPowerJBestOf9Step, "blind_power_j_best_of_9"),
    ]

    for statement_class, algorithm_name in expected:
        result = statement_class().run(_load_example("blind_power_input.json"))

        assert result["schema_version"] == 1
        assert result["action"] == "move"
        assert result["state"]["algorithm"] == algorithm_name


def test_position_solve_step_moves_toward_nested_targets():
    result = PositionSolveStep().run(_load_example("position_solve_input.json"))

    assert result["schema_version"] == 1
    assert result["action"] == "move"
    assert result["stage1"] == "Align_Y1"
    assert result["distance1_um"] == 1.0
    assert result["moves"][0]["mode"] == "relative"


def test_position_solve_j_steps_uses_target_path_and_state():
    result = PositionSolveJStepsStep().run(_load_example("position_solve_j_steps_input.json"))

    assert result["schema_version"] == 1
    assert result["action"] == "move"
    assert result["stage1"] == "Align_Y1"
    assert result["distance1_um"] == 1.0
    assert result["state"]["algorithm"] == "position_solve_j_steps"


def test_fixed_z_j_matrix_step_never_requests_axial_align_y_move():
    result = FixedZJMatrixStep().run(_load_example("fixed_z_j_matrix_input.json"))

    assert result["schema_version"] == 1
    assert result["action"] == "move"
    assert result["stage1"] == "Align_X1"
    assert result["distance1_um"] == 0.4
    assert all(not move["stage"].startswith("Align_Y") for move in result["moves"])
