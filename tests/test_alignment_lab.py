"""Tests for the seeded alignment sandbox."""

import inspect
import tkinter as tk

import numpy as np
import pytest

from alignment_algorithms import available_algorithms, get_algorithm
from alignment_algorithms.base import AlignmentAlgorithmResult, PowerReading
import alignment_lab as alignment_lab_module
from alignment_lab import (
    DEFAULT_ALIGNMENT_SEED,
    DEFAULT_INITIAL_BALL_X_OFFSET,
    DEFAULT_LENS_POSE_TOLERANCE,
    DEFAULT_SOURCE_DETECTOR_TOLERANCE,
    AlignmentLabEditor,
    seeded_alignment_errors,
)


def _make_app():
    try:
        return AlignmentLabEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")


def _assert_pose_close(actual, expected):
    assert len(actual) == len(expected)
    for actual_pose, expected_pose in zip(actual, expected):
        assert np.allclose(actual_pose, expected_pose)


def test_alignment_algorithm_registry_includes_probe_algorithm():
    algorithms = available_algorithms()

    assert set(algorithms) == {"manual", "ball_lens_probe"}
    assert get_algorithm("manual").display_name == "Manual/no search"
    assert get_algorithm("ball_lens_probe").display_name == "Ball-lens probe"


def test_alignment_algorithms_only_accept_step_device():
    for algorithm in available_algorithms().values():
        signature = inspect.signature(algorithm.run)

        assert list(signature.parameters) == ["device"]


def test_default_alignment_seed_and_tolerances():
    assert DEFAULT_ALIGNMENT_SEED == 42
    assert DEFAULT_SOURCE_DETECTOR_TOLERANCE == 5.0e-6
    assert DEFAULT_LENS_POSE_TOLERANCE == 2.0e-6


def test_seeded_alignment_errors_are_repeatable_and_bounded():
    first = seeded_alignment_errors(DEFAULT_ALIGNMENT_SEED, lens_count=2)
    second = seeded_alignment_errors(DEFAULT_ALIGNMENT_SEED, lens_count=2)
    different = seeded_alignment_errors(DEFAULT_ALIGNMENT_SEED + 1, lens_count=2)

    assert first == second
    assert first != different

    source_detector_values = (
        first.source_x_offset,
        first.source_y_offset,
        first.taper_x_offset,
        first.taper_y_offset,
    )
    assert all(-DEFAULT_SOURCE_DETECTOR_TOLERANCE <= value <= DEFAULT_SOURCE_DETECTOR_TOLERANCE for value in source_detector_values)

    for pose_offset in first.ball_pose_offsets:
        assert all(-DEFAULT_LENS_POSE_TOLERANCE <= value <= DEFAULT_LENS_POSE_TOLERANCE for value in pose_offset)


def test_alignment_lab_startup_places_balls_out_of_beam_path():
    app = _make_app()

    try:
        assert all(np.isclose(ball.x_offset, DEFAULT_INITIAL_BALL_X_OFFSET) for ball in app.balls)
        assert all(np.isclose(ball.y_offset, 0.0) for ball in app.balls)
        assert app.source_detector_tolerance == DEFAULT_SOURCE_DETECTOR_TOLERANCE
        assert app.lens_pose_tolerance == DEFAULT_LENS_POSE_TOLERANCE
    finally:
        app.destroy()


def test_rescramble_does_not_accumulate_lens_pose_drift():
    app = _make_app()

    try:
        app.seed_var.set(str(DEFAULT_ALIGNMENT_SEED))
        app.source_detector_tolerance_var.set("5")
        app.lens_tolerance_var.set("2")

        app._rescramble_alignment_errors()  # pylint: disable=protected-access
        first_offsets = app._ball_pose_offsets_from_nominal()  # pylint: disable=protected-access

        app.balls[0].x_offset += 20e-6
        app.balls[0].y_offset -= 15e-6
        app.balls[0].position += 10e-6

        app._rescramble_alignment_errors()  # pylint: disable=protected-access
        second_offsets = app._ball_pose_offsets_from_nominal()  # pylint: disable=protected-access

        _assert_pose_close(second_offsets, first_offsets)
    finally:
        app.destroy()


def test_alignment_device_measure_does_not_move_or_increment_move_count():
    app = _make_app()

    try:
        device = app.create_alignment_device()
        before = app.current_poses()

        first = device.measure()
        second = device.measure()

        assert isinstance(first, PowerReading)
        assert first.move_count == 0
        assert first.measurement_count == 1
        assert second.move_count == 0
        assert second.measurement_count == 2
        _assert_pose_close(app.current_poses(), before)
    finally:
        app.destroy()


def test_alignment_device_move_lens_updates_xyz_and_counts_one_step():
    app = _make_app()

    try:
        device = app.create_alignment_device()
        before = app.current_poses()
        dx, dy, dz = 1.0e-6, -0.75e-6, 0.5e-6

        reading = device.move_lens(1, dx=dx, dy=dy, dz=dz)

        after = app.current_poses()
        assert reading.move_count == 1
        assert reading.measurement_count == 1
        assert np.allclose(after[1], (before[1][0] + dx, before[1][1] + dy, before[1][2] + dz))
        assert np.allclose(after[0], before[0])
        assert len(device.move_history()) == 1
    finally:
        app.destroy()


def test_alignment_lab_algorithm_handoff_hides_source_and_detector_positions(monkeypatch):
    app = _make_app()

    class InterfaceCheckingAlgorithm:
        name = "interface_check"
        display_name = "Interface check"

        def run(self, device):
            public_methods = {
                name
                for name in dir(device)
                if not name.startswith("_") and callable(getattr(device, name))
            }
            assert public_methods == {
                "coordinate_reference_point",
                "current_poses",
                "measure",
                "move_history",
                "move_lens",
                "move_lens_to",
            }

            expected_poses = app.current_poses()
            assert device.current_poses() == expected_poses

            first = device.measure()
            moved = device.move_lens(0, dx=1.0e-6, dy=-0.5e-6, dz=0.25e-6)

            assert isinstance(first, PowerReading)
            assert isinstance(moved, PowerReading)
            assert first.move_count == 0
            assert moved.move_count == 1
            assert moved.measurement_count == 2
            assert not hasattr(first, "source_x_offset")
            assert not hasattr(first, "taper_x_offset")

            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=moved,
                move_history=device.move_history(),
                message="interface checked",
            )

    monkeypatch.setattr(alignment_lab_module, "get_algorithm", lambda _name: InterfaceCheckingAlgorithm())

    try:
        before = app.current_poses()
        evaluation = app.run_alignment_algorithm("interface_check")
        after = app.current_poses()

        assert evaluation.received_power > 0.0
        assert not np.allclose(after[0], before[0])
        assert "Interface check" in app.algorithm_status_var.get()
    finally:
        app.destroy()


def test_alignment_device_absolute_move_and_reference_point():
    app = _make_app()

    try:
        device = app.create_alignment_device()
        reference = device.coordinate_reference_point()
        assert len(reference) == 3

        target = (reference[0] + 2.0e-6, reference[1] - 1.0e-6, reference[2] + 0.5e-6)
        reading = device.move_lens_to(0, *target)

        assert reading.move_count == 1
        assert reading.measurement_count == 1
        assert np.allclose(app.current_poses()[0], target)
    finally:
        app.destroy()


def test_ball_lens_probe_algorithm_moves_both_lenses_from_safe_defaults():
    app = _make_app()

    try:
        device = app.create_alignment_device()
        result = get_algorithm("ball_lens_probe").run(device)

        assert isinstance(result, AlignmentAlgorithmResult)
        assert result.move_count > 0
        assert result.evaluations >= result.move_count
        assert "safe default" in result.message
        moved_lenses = {move.lens_index for move in result.move_history}
        assert moved_lenses == {0, 1}
    finally:
        app.destroy()

def test_moving_lens_in_x_changes_evaluated_power():
    app = _make_app()

    try:
        first = app.evaluate_current_alignment()
        app.balls[0].x_offset -= 50e-6
        second = app.evaluate_current_alignment()

        assert first.received_power >= 0.0
        assert second.received_power >= 0.0
        assert second.received_power != first.received_power
    finally:
        app.destroy()


def test_alignment_lab_live_simulation_updates_power_and_pose_readouts():
    app = _make_app()

    try:
        app.tapers[0].received_power = 0.0
        evaluation = app._run_alignment_simulation(update_report=True)  # pylint: disable=protected-access

        assert evaluation.received_power > 0.0
        assert np.isclose(app.tapers[0].received_power, evaluation.received_power)
        assert "RECEIVED:" in app.power_percent_var.get()
        assert "MODE:" in app.power_percent_var.get()
        assert "%" in app.power_percent_var.get()
        assert "mW" in app.power_percent_var.get()
        assert "Received power:" in app.received_power_var.get()
        assert "Coupling total:" in app.total_efficiency_var.get()
        assert "Mode match:" in app.mode_efficiency_var.get()
        assert "Source error: x" in app.source_height_var.get()
        assert "Detector error: x" in app.detector_height_var.get()
        assert "Ball pose errors:" in app.lens_offsets_var.get()
    finally:
        app.destroy()


def test_alignment_lab_parameter_table_shows_y_units_and_mfd_without_size_or_power_columns():
    app = _make_app()

    try:
        columns = tuple(app.tree["columns"])

        assert columns == ("kind", "z", "x", "y", "mfd", "extra")
        assert app.tree.heading("z")["text"] == "z"
        assert "size" not in columns
        assert "power" not in columns

        source_values = app.tree.item(app.sources[0].uid, "values")
        taper_values = app.tree.item(app.tapers[0].uid, "values")

        assert source_values[1].endswith("um")
        assert source_values[2].endswith("um")
        assert source_values[3].endswith("um")
        assert "x" in source_values[4]
        assert source_values[4].endswith("um")

        assert taper_values[1].endswith("um")
        assert taper_values[3].endswith("um")
        assert "x" in taper_values[4]
        assert taper_values[4].endswith("um")
    finally:
        app.destroy()


def test_alignment_lab_show_manual_algorithm_completes_without_hanging():
    app = _make_app()

    try:
        before = app.evaluate_current_alignment()

        app.show_alignment_algorithm("manual", delay_ms=0)
        app.update()

        evaluation = app.evaluate_current_alignment()
        assert np.isclose(evaluation.received_power, before.received_power)
        assert app._algorithm_animation_after_id is None  # pylint: disable=protected-access
        assert "Manual/no search" in app.algorithm_status_var.get()
    finally:
        app.destroy()
