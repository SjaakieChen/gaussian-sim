from __future__ import annotations

import json
from pathlib import Path

from testmaster_alignment.alignment_step import BlindAlignStep
from testmaster_alignment.assisted.fixed_z_j_matrix_step import FixedZJMatrixStep
from testmaster_alignment.assisted.position_solve_j_steps_step import PositionSolveJStepsStep
from testmaster_alignment.assisted.position_solve_step import PositionSolveStep
from testmaster_alignment.assisted.target_position_step import TargetPositionStep
from testmaster_alignment.assisted.vision_offset_step import VisionOffsetStep
from testmaster_alignment.blind.blind_power_j_best_of_9_step import BlindPowerJBestOf9Step
from testmaster_alignment.blind.blind_power_j_gradient_step import BlindPowerJGradientStep
from testmaster_alignment.blind.blind_power_j_newton_step import BlindPowerJNewtonStep
from testmaster_alignment.blind.blind_power_j_step import BlindPowerJStep
from testmaster_alignment.movement_command_test_step import MovementCommandTestStep
from testmaster_vision.image_step import ImageRecognitionStep


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "testmaster_alignment" / "examples"


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


def test_movement_command_test_step_returns_one_relative_move():
    result = MovementCommandTestStep().run(_load_example("movement_command_test_input.json"))

    assert result["schema_version"] == 1
    assert result["action"] == "move"
    assert result["move_count"] == 1
    assert result["stage1"] == "Align_X1"
    assert result["distance1_um"] == 0.1
    assert result["moves"] == [{"stage": "Align_X1", "distance_um": 0.1, "mode": "relative"}]
    assert result["state"]["algorithm"] == "movement_command_test"
    assert result["state"]["test_only"] is True


def test_movement_command_test_step_requires_allowed_stage():
    params = _load_example("movement_command_test_input.json")
    params["algorithm"]["stage"] = "Align_Z1"

    result = MovementCommandTestStep().run(params)

    assert result["schema_version"] == 1
    assert result["action"] == "abort"
    assert "not in limits.allowed_stages" in result["message"]


def test_movement_command_test_step_rejects_move_larger_than_limit():
    params = _load_example("movement_command_test_input.json")
    params["algorithm"]["distance_um"] = 1.0

    result = MovementCommandTestStep().run(params)

    assert result["schema_version"] == 1
    assert result["action"] == "abort"
    assert "exceeds limits.max_step_um" in result["message"]


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


def test_image_recognition_step_reads_saved_image_and_returns_feature(tmp_path):
    import numpy as np
    from PIL import Image

    image = np.zeros((20, 30), dtype=np.uint8)
    image[6:10, 12:18] = 240
    image_path = tmp_path / "camera.png"
    Image.fromarray(image).save(image_path)

    result = ImageRecognitionStep().run(
        {
            "schema_version": 1,
            "phase": "vision_recognition",
            "vision": {"image_path": str(image_path)},
            "algorithm": {"polarity": "bright", "threshold": 200, "min_area_px": 5},
            "limits": {"allowed_stages": [], "max_step_um": 0.0},
        }
    )

    assert result["schema_version"] == 1
    assert result["action"] == "done"
    assert result["move_count"] == 0
    assert result["vision"]["width_px"] == 30
    assert result["vision"]["height_px"] == 20
    assert result["vision"]["feature"]["area_px"] == 24
    assert result["vision"]["feature"]["centroid_x_px"] == 14.5
    assert result["vision"]["feature"]["centroid_y_px"] == 7.5


def test_image_recognition_step_aborts_when_image_missing():
    result = ImageRecognitionStep().run(
        {
            "schema_version": 1,
            "vision": {"image_path": "Z:\\does-not-exist\\missing.tif"},
            "algorithm": {"polarity": "bright"},
            "limits": {"allowed_stages": [], "max_step_um": 0.0},
        }
    )

    assert result["schema_version"] == 1
    assert result["action"] == "abort"
    assert "does not exist" in result["message"]
