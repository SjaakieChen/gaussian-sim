import json

import pytest

from migrations.migration_v6.tools import simulate_v6_standard_workflow as simulator_module
from migrations.migration_v6.tools.simulate_v6_standard_workflow import (
    STANDARD_BASELINE_DIR,
    SimulatorConfig,
    V6StandardWorkflowSimulator,
    parse_args,
    replace_simulator_baseline,
    run_standard_workflow_simulation,
)


CONVERGENCE_CAPTURE_IDS = ["2.1.1", "2.5.1", "2.6.1", "4.1.1", "4.5.1", "4.6.2"]


def _offset_steps(result, capture_id):
    return [
        step
        for step in result["trace"]
        if step["kind"] == "offset_correction" and step["capture_id"] == capture_id
    ]


def test_v6_full_standard_image_simulator_converges_and_verifies_final_geometry(tmp_path):
    memory_path = tmp_path / "memory.json"
    trace_path = tmp_path / "trace.json"
    result = run_standard_workflow_simulation(
        SimulatorConfig(
            workflow_target="all",
            headless=True,
            memory_path=memory_path,
            output_path=trace_path,
        )
    )

    assert result["ok"] is True
    assert trace_path.is_file()
    assert json.loads(trace_path.read_text(encoding="utf-8"))["ok"] is True

    for capture_id in CONVERGENCE_CAPTURE_IDS:
        steps = _offset_steps(result, capture_id)
        assert 1 <= len(steps) <= 9
        assert steps[-1]["result"]["action"] == "no_offset_correction_required"
        assert steps[-1]["result"]["move_count"] == 0
        for step in steps:
            for move in step["applied_moves"]:
                assert move["velocity_class"] == "slow_offset_correction"

    transition_steps = [step for step in result["trace"] if step["kind"] == "transition_move"]
    assert transition_steps
    for step in transition_steps:
        for move in step["applied_moves"]:
            assert move["velocity_class"] == "medium_transition"

    standard_steps = [
        step for step in result["trace"] if step["kind"] == "standard_position_move"
    ]
    assert standard_steps
    for step in standard_steps:
        stages = [move["stage"] for move in step["planned_moves"]]
        tower_suffix = "1" if "Align_Y1" in stages else "2"
        raise_index = stages.index(f"Align_Y{tower_suffix}")
        assert raise_index == 0
        assert raise_index < min(
            index for index, stage in enumerate(stages) if stage.startswith("Camera_")
        )
        assert stages.index(f"Align_Z{tower_suffix}") < stages.index(
            f"Align_X{tower_suffix}"
        )
        if stages.count(f"Align_Y{tower_suffix}") == 2:
            assert stages[-1] == f"Align_Y{tower_suffix}"

    ball_2_fine_steps = _offset_steps(result, "4.5.1")
    moved_ball_2_steps = [
        step
        for step in ball_2_fine_steps
        if step["result"]["action"] == "offset_correction_move"
    ]
    assert moved_ball_2_steps
    for step in moved_ball_2_steps:
        path = step["result"]["diagnostics"]["collision_path"]
        assert path["status"] == "strict_projected_ball_clearance_valid"
        assert path["selected_path"]["minimum_surface_gap_um"] > 0.0

    verification = [step for step in result["trace"] if step["kind"] == "final_verification"]
    assert len(verification) == 1
    verified = verification[0]["result"]
    assert verified["action"] == "final_geometry_verified"
    assert verified["read_only"] is True
    assert verified["move_count"] == 0
    assert verified["measured_coordinates_um"]["ball_1"] == pytest.approx(
        {"machine_x_um": 289.0, "machine_y_um": 0.0, "machine_z_um": 0.0}
    )
    assert verified["measured_coordinates_um"]["ball_2"] == pytest.approx(
        {"machine_x_um": 989.0, "machine_y_um": 0.0, "machine_z_um": 0.0}
    )
    assert verified["measured_center_spacing_um"] == pytest.approx(700.0)
    assert verified["collision_clearance"]["strictly_clear"] is True
    assert verified["collision_clearance"]["axial_surface_gaps_um"] == pytest.approx(
        {
            "source_to_ball_1_surface_gap_um": 39.0,
            "ball_1_to_ball_2_surface_gap_um": 200.0,
            "ball_2_to_taper_surface_gap_um": 39.0,
        }
    )

    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    assert memory["schema_version"] == 2
    assert memory["capture_records"]["2.5.1"]["revision"] > 1
    assert memory["capture_history"]["2.5.1"]
    assert (
        memory["capture_records"]["2.5.1"]["camera_settings"]["source"]
        == "reapplied_standard_position_before_operator_gate"
    )


def test_v6_standard_image_simulator_coarse_shift_uses_canonical_axes_and_fresh_review(tmp_path):
    result = run_standard_workflow_simulation(
        SimulatorConfig(
            workflow_target="ball_1",
            headless=True,
            memory_path=tmp_path / "memory.json",
            coarse_shift_x_px=10.0,
            coarse_shift_y_px=-10.0,
        )
    )

    steps = _offset_steps(result, "2.1.1")
    assert len(steps) == 2
    first_result = steps[0]["result"]
    correction = first_result["diagnostics"]["correction"]
    um_per_pixel = correction["scale_context"]["um_per_pixel"]

    assert first_result["action"] == "offset_correction_move"
    assert first_result["move_count"] == 2
    assert first_result["stage1"] == "Align_Z1"
    assert first_result["delta1_um"] == pytest.approx(-10.0 * um_per_pixel)
    assert first_result["stage2"] == "Align_X1"
    assert first_result["delta2_um"] == pytest.approx(-10.0 * um_per_pixel)
    assert steps[0]["pixel_residuals_after"]["coarse_x_px"] == pytest.approx(0.0)
    assert steps[0]["pixel_residuals_after"]["coarse_y_px"] == pytest.approx(0.0)

    assert steps[1]["result"]["action"] == "no_offset_correction_required"
    assert correction["view_mapping"]["image_right"]["machine_axis"] == "machine_x_um"
    assert correction["view_mapping"]["image_up"]["machine_axis"] == "machine_z_um"


def test_v6_standard_image_simulator_side_shift_uses_two_line_mirror_ruler(tmp_path):
    result = run_standard_workflow_simulation(
        SimulatorConfig(
            workflow_target="ball_1",
            headless=True,
            memory_path=tmp_path / "memory.json",
            side_shift_y_px=10.0,
        )
    )

    steps = _offset_steps(result, "2.6.1")
    assert 2 <= len(steps) <= 9
    first = steps[0]["result"]
    correction = first["diagnostics"]["correction"]
    scale = correction["scale_context"]

    assert correction["mirror_view"] is True
    assert correction["mirror_flip_y"] is True
    assert scale["known_distance_um"] == pytest.approx(300.0)
    assert scale["measured_pixels"] == pytest.approx(169.0)
    assert scale["um_per_pixel"] == pytest.approx(300.0 / 169.0)
    assert correction["residual_flipped_y_px"] == pytest.approx(-146.0)
    assert first["stage1"] == "Align_Y1"
    assert first["delta1_um"] == pytest.approx(-75.0)

    for step in steps:
        for move in step["applied_moves"]:
            assert move["stage"] == "Align_Y1"
            assert abs(move["delta_um"]) <= 75.0
            assert move["velocity_class"] == "slow_offset_correction"
    assert steps[-1]["result"]["action"] == "no_offset_correction_required"
    assert (
        steps[-1]["result"]["diagnostics"]["correction"]["residual_flipped_y_px"]
        == pytest.approx(0.0)
    )


def test_v6_simulator_defaults_to_vision_only_popups_and_all_is_explicit(tmp_path):
    default_config = parse_args(["--headless", "--output", str(tmp_path / "default.json")])
    assert default_config.popup_scope == "vision"
    assert default_config.baseline_replacements == {}

    yase_config = parse_args(
        [
            "--headless",
            "--output",
            str(tmp_path / "yase.json"),
            "--popup-scope",
            "yase",
        ]
    )
    assert yase_config.popup_scope == "yase"

    all_config = parse_args(
        [
            "--headless",
            "--output",
            str(tmp_path / "all.json"),
            "--popup-scope",
            "all",
            "--replace-baseline",
            "2.1.1=reviewed.json",
        ]
    )
    assert all_config.popup_scope == "all"
    assert all_config.baseline_replacements == {"2.1.1": all_config.baseline_replacements["2.1.1"]}
    assert str(all_config.baseline_replacements["2.1.1"]) == "reviewed.json"


def test_v6_simulator_baseline_replacement_is_explicit_and_backed_up(tmp_path):
    baseline_dir = tmp_path / "baselines"
    baseline_dir.mkdir()
    backup_root = tmp_path / "backups"
    destination = baseline_dir / "2.1.1.json"
    replacement_path = tmp_path / "replacement.json"
    original = json.loads((STANDARD_BASELINE_DIR / "2.1.1.json").read_text(encoding="utf-8"))
    original["revision_marker"] = "original"
    replacement = json.loads(json.dumps(original))
    replacement["revision_marker"] = "replacement"
    destination.write_text(json.dumps(original), encoding="utf-8")
    replacement_path.write_text(json.dumps(replacement), encoding="utf-8")

    backup_path = replace_simulator_baseline(
        "2.1.1",
        replacement_path,
        baseline_dir=baseline_dir,
        backup_root=backup_root,
    )

    assert backup_path.parent.parent == backup_root
    assert json.loads(backup_path.read_text(encoding="utf-8")) == original
    assert json.loads(destination.read_text(encoding="utf-8")) == replacement


def test_v6_simulator_never_silently_creates_a_missing_baseline(tmp_path):
    baseline_dir = tmp_path / "baselines"
    baseline_dir.mkdir()
    replacement_path = tmp_path / "replacement.json"
    replacement_path.write_text(
        (STANDARD_BASELINE_DIR / "2.1.1.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with pytest.raises(FileNotFoundError, match="will not be silently created"):
        replace_simulator_baseline(
            "2.1.1",
            replacement_path,
            baseline_dir=baseline_dir,
            backup_root=tmp_path / "backups",
        )
    assert not (baseline_dir / "2.1.1.json").exists()


def test_v6_simulator_rejects_baseline_for_the_wrong_capture_id(tmp_path):
    baseline_dir = tmp_path / "baselines"
    baseline_dir.mkdir()
    destination = baseline_dir / "2.1.1.json"
    destination.write_text(
        (STANDARD_BASELINE_DIR / "2.1.1.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    replacement = json.loads(destination.read_text(encoding="utf-8"))
    replacement["capture_id"] = "4.1.1"
    replacement_path = tmp_path / "wrong.json"
    replacement_path.write_text(json.dumps(replacement), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        replace_simulator_baseline(
            "2.1.1",
            replacement_path,
            baseline_dir=baseline_dir,
            backup_root=tmp_path / "backups",
        )
    assert not (tmp_path / "backups").exists()


def test_v6_simulator_stops_when_a_transition_planner_fails(tmp_path, monkeypatch):
    simulator = V6StandardWorkflowSimulator(
        SimulatorConfig(headless=True, memory_path=tmp_path / "memory.json")
    )
    monkeypatch.setattr(
        simulator_module,
        "run_v6_vision_workflow",
        lambda _payload: {
            "schema_version": 2,
            "ok": False,
            "action": "abort",
            "status": "simulated transition failure",
            "move_count": 0,
        },
    )

    with pytest.raises(RuntimeError, match="simulated transition failure"):
        simulator.transition_move_loop("2.1_to_2.4")
