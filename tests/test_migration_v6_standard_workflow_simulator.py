import pytest

from migrations.migration_v6.tools.simulate_v6_standard_workflow import (
    SimulatorConfig,
    run_standard_workflow_simulation,
)


def _offset_steps(result, capture_id):
    return [
        step
        for step in result["trace"]
        if step["kind"] == "offset_correction" and step["capture_id"] == capture_id
    ]


def test_v6_standard_image_simulator_zero_shift_requests_no_offset_moves(tmp_path):
    result = run_standard_workflow_simulation(
        SimulatorConfig(
            workflow_target="ball_1",
            headless=True,
            memory_path=tmp_path / "memory.json",
            output_path=tmp_path / "trace.json",
        )
    )

    assert result["ok"] is True
    assert [step["capture_id"] for step in _offset_steps(result, "2.1.1")] == ["2.1.1", "2.1.1"]
    for step in result["trace"]:
        if step["kind"] != "offset_correction":
            continue
        assert step["result"]["ok"] is True
        assert step["result"]["action"] == "no_offset_correction_required"
        assert step["result"]["move_count"] == 0


def test_v6_standard_image_simulator_coarse_shift_is_removed_by_second_pass(tmp_path):
    result = run_standard_workflow_simulation(
        SimulatorConfig(
            workflow_target="ball_1",
            headless=True,
            memory_path=tmp_path / "memory.json",
            coarse_shift_x_px=10.0,
            coarse_shift_y_px=-10.0,
        )
    )

    first, second = _offset_steps(result, "2.1.1")
    first_result = first["result"]
    correction = first_result["diagnostics"]["correction"]
    um_per_pixel = correction["um_per_pixel"]

    assert first_result["action"] == "offset_correction_move"
    assert first_result["move_count"] == 2
    assert first_result["stage1"] == "Align_X1"
    assert first_result["delta1_um"] == pytest.approx(-10.0 * um_per_pixel)
    assert first_result["stage2"] == "Align_Z1"
    assert first_result["delta2_um"] == pytest.approx(10.0 * um_per_pixel)
    assert first["pixel_residuals_after"]["coarse_x_px"] == pytest.approx(0.0)
    assert first["pixel_residuals_after"]["coarse_y_px"] == pytest.approx(0.0)

    assert second["result"]["action"] == "no_offset_correction_required"
    assert second["result"]["diagnostics"]["correction"]["pixel_shift"] == {"x": 0.0, "y": 0.0}


def test_v6_standard_image_simulator_side_shift_uses_mirror_flip(tmp_path):
    result = run_standard_workflow_simulation(
        SimulatorConfig(
            workflow_target="ball_1",
            headless=True,
            memory_path=tmp_path / "memory.json",
            side_shift_y_px=10.0,
        )
    )

    first, second = _offset_steps(result, "2.6.1")
    first_result = first["result"]
    correction = first_result["diagnostics"]["correction"]
    um_per_pixel = correction["um_per_pixel"]

    assert correction["residual_flipped_y_px"] == pytest.approx(-10.0)
    assert first_result["action"] == "offset_correction_move"
    assert first_result["move_count"] == 1
    assert first_result["stage1"] == "Align_Y1"
    assert first_result["delta1_um"] == pytest.approx(-10.0 * um_per_pixel)
    assert first["pixel_residuals_after"]["side_full_y_px"] == pytest.approx(0.0)

    assert second["result"]["action"] == "no_offset_correction_required"
    assert second["result"]["diagnostics"]["correction"]["residual_flipped_y_px"] == pytest.approx(0.0)
