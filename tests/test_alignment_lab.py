"""Tests for the seeded alignment sandbox."""

import inspect
import tkinter as tk
from types import SimpleNamespace

import numpy as np
import pytest

import alignment_algorithms.position_solve as position_solve_module
from alignment_algorithms import available_algorithms, get_algorithm
from alignment_algorithms.base import (
    DEFAULT_TARGET_MODE_EFFICIENCY,
    AlignmentModelGeometry,
    AlignmentMove,
    AlignmentAlgorithmResult,
    BallLensGeometry,
    PowerReading,
    SourceGeometry,
    TaperGeometry,
)
from alignment_algorithms.coordinate_scan import CoordinateScanAlgorithm
from alignment_algorithms.given_positions import GivenPositionsAlgorithm
from alignment_algorithms.position_solve import (
    TRANSVERSE_RESPONSE_STEP,
    BeamErrorJMatrixAlgorithm,
    FixedZJMatrixAlgorithm,
    PositionSolveAlgorithm,
    PositionSolveWithJStepsAlgorithm,
    has_strict_axial_clearance,
)
from alignment_algorithms.yase import (
    DEFAULT_YASE_CONFIG,
    DEFAULT_YASE_ROOT,
    DeviceBackedYaseMachine,
    YaseAlignmentAlgorithm,
)
import alignment_lab as alignment_lab_module
from alignment_lab import (
    DEFAULT_ALIGNMENT_SEED,
    DEFAULT_INITIAL_BALL_X_OFFSET,
    DEFAULT_LENS_POSE_TOLERANCE,
    DEFAULT_SOURCE_DETECTOR_TOLERANCE,
    AlignmentLabEditor,
    seeded_alignment_errors,
)
from interactive_setup import (
    DEFAULT_CLIPPING_RADIUS_FACTOR,
    DEFAULT_REFRACTIVE_INDEX,
    LaserSource,
    default_ball_lens_layout,
    simulate_layout,
)
from yase_sim import YaseInterpreter


def _make_app():
    try:
        return AlignmentLabEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")


def _assert_pose_close(actual, expected):
    assert len(actual) == len(expected)
    for actual_pose, expected_pose in zip(actual, expected):
        assert np.allclose(actual_pose, expected_pose)


class SimulatedAlignmentDevice:
    def __init__(self, seed: int, *, startup_out_of_beam: bool = False) -> None:
        self.source = LaserSource()
        self.balls, tapers, self.final_z = default_ball_lens_layout()
        self.taper = tapers[0]
        self._starting_poses = tuple((ball.x_offset, ball.y_offset, ball.position) for ball in self.balls)
        self._moves: list[AlignmentMove] = []
        self._move_count = 0
        self._measurement_count = 0

        if startup_out_of_beam:
            for ball in self.balls:
                ball.x_offset = DEFAULT_INITIAL_BALL_X_OFFSET
            return

        scramble = seeded_alignment_errors(seed, lens_count=len(self.balls))
        self.source.x_offset = scramble.source_x_offset
        self.source.y_offset = scramble.source_y_offset
        self.taper.x_offset = scramble.taper_x_offset
        self.taper.y_offset = scramble.taper_y_offset
        for ball, starting_pose, pose_offset in zip(self.balls, self._starting_poses, scramble.ball_pose_offsets):
            ball.x_offset = starting_pose[0] + pose_offset[0]
            ball.y_offset = starting_pose[1] + pose_offset[1]
            ball.position = starting_pose[2] + pose_offset[2]

    def starting_poses(self):
        return self._starting_poses

    def current_poses(self):
        return tuple((ball.x_offset, ball.y_offset, ball.position) for ball in self.balls)

    def model_geometry(self):
        source = self.source
        taper = self.taper
        return AlignmentModelGeometry(
            source=SourceGeometry(
                name=source.name,
                position=source.position,
                wavelength=source.wavelength,
                waist_radius=source.waist_radius,
                waist_radius_y=source.waist_radius_y,
                rayleigh_range=source.rayleigh_range,
                rayleigh_range_y=source.rayleigh_range_y,
                waist_position=source.waist_position,
                power=source.power,
                x_offset=source.x_offset,
                y_offset=source.y_offset,
                x_angle=source.x_angle,
                y_angle=source.y_angle,
            ),
            taper=TaperGeometry(
                name=taper.name,
                position=taper.position,
                width=taper.width,
                height=taper.height,
                mode_radius_x=taper.mode_radius_x,
                mode_radius_y=taper.mode_radius_y,
                extra_transmission=taper.extra_transmission,
                facet_refractive_index=taper.facet_refractive_index,
                x_offset=taper.x_offset,
                y_offset=taper.y_offset,
            ),
            balls=tuple(
                BallLensGeometry(
                    name=ball.name,
                    position=ball.position,
                    diameter=ball.diameter,
                    refractive_index=ball.refractive_index,
                    x_offset=ball.x_offset,
                    y_offset=ball.y_offset,
                )
                for ball in self.balls
            ),
            current_poses=self.current_poses(),
            starting_poses=self.starting_poses(),
            clipping_radius_factor=DEFAULT_CLIPPING_RADIUS_FACTOR,
        )

    def measure(self):
        self._measurement_count += 1
        return self._reading()

    def move_lens(self, lens_index, dx=0.0, dy=0.0, dz=0.0):
        ball = self.balls[lens_index]
        ball.x_offset += dx
        ball.y_offset += dy
        ball.position += dz
        self._move_count += 1
        self._measurement_count += 1
        reading = self._reading()
        self._moves.append(
            AlignmentMove(
                lens_index=lens_index,
                dx=dx,
                dy=dy,
                dz=dz,
                poses=self.current_poses(),
                reading=reading,
            )
        )
        return reading

    def move_history(self):
        return tuple(self._moves)

    def _reading(self):
        results = simulate_layout(
            [self.source],
            [],
            [],
            self.final_z,
            balls=self.balls,
            tapers=[self.taper],
            refractive_index=DEFAULT_REFRACTIVE_INDEX,
            clipping_radius_factor=DEFAULT_CLIPPING_RADIUS_FACTOR,
        )
        taper_result = results[0].taper_results[0]
        total_efficiency = taper_result.received_power / self.source.power
        return PowerReading(
            received_power=taper_result.received_power,
            total_efficiency=total_efficiency,
            mode_efficiency=taper_result.mode_efficiency,
            move_count=self._move_count,
            measurement_count=self._measurement_count,
        )


def test_alignment_algorithm_registry_has_coordinate_scan_manual_and_yase_subprocesses():
    algorithms = available_algorithms()

    assert "coordinate_scan" in algorithms
    assert "given_positions" in algorithms
    assert "manual" in algorithms
    assert "beam_error_j_matrix" in algorithms
    assert "fixed_z_j_matrix" in algorithms
    assert "position_solve" in algorithms
    assert "position_solve_j_steps" in algorithms
    assert "yase:SUB_Alignment/SUB_GivenPositionsReferencePose.xseq" in algorithms
    assert "yase:SUB_Alignment/SUB_PositionSolveNoiselessModel.xseq" in algorithms
    assert "yase:SUB_Alignment/SUB_PowerOnlyCoordinateScan.xseq" in algorithms
    assert "yase:SUB_Positioning/SUB_Test_DrawCircle_AlignX1Z1.xseq" in algorithms
    assert get_algorithm("coordinate_scan").display_name == "Power-only coordinate scan"
    assert get_algorithm("given_positions").display_name == "Reference pose only"
    assert get_algorithm("manual").display_name == "Manual/no search"
    assert get_algorithm("beam_error_j_matrix").display_name == "Beam-error J-matrix local solve"
    assert get_algorithm("fixed_z_j_matrix").display_name == "Fixed-Z J-matrix local solve"
    assert get_algorithm("position_solve").display_name == "Position solve/noiseless model"
    assert get_algorithm("position_solve_j_steps").display_name == "Position solve/show J steps"


def test_alignment_algorithms_only_accept_step_device():
    for algorithm in available_algorithms().values():
        signature = inspect.signature(algorithm.run)

        assert list(signature.parameters) == ["device"]


def test_alignment_lab_dropdown_hides_yase_algorithms():
    app = _make_app()

    try:
        algorithm_names = set(app._algorithm_label_to_name.values())  # pylint: disable=protected-access

        assert "beam_error_j_matrix" in algorithm_names
        assert "fixed_z_j_matrix" in algorithm_names
        assert all(not name.startswith("yase:") for name in algorithm_names)
    finally:
        app.destroy()


def test_alignment_lab_defaults_to_position_solve_show_j_steps():
    app = _make_app()

    try:
        assert app.algorithm_var.get() == "Position solve/show J steps"
    finally:
        app.destroy()


def test_alignment_lab_hides_layout_edit_and_zoom_toolbar_buttons():
    app = _make_app()

    try:
        hidden_buttons = (
            "add_source",
            "add_ball",
            "add_taper",
            "edit_selected",
            "delete_selected",
            "zoom_out",
            "zoom_in",
        )

        assert all(not app._toolbar_buttons[name].grid_info() for name in hidden_buttons)  # pylint: disable=protected-access
    finally:
        app.destroy()


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


def test_alignment_lab_algorithm_handoff_exposes_power_only_reference_interface(monkeypatch):
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
            assert public_methods == {"current_poses", "measure", "model_geometry", "move_history", "move_lens", "starting_poses"}

            expected_poses = app.current_poses()
            assert device.current_poses() == expected_poses
            assert device.starting_poses() == app.starting_poses()
            geometry = device.model_geometry()
            assert geometry.source.position == app.sources[0].position
            assert geometry.taper.position == app.tapers[0].position
            assert geometry.current_poses == expected_poses

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


def test_alignment_lab_show_replays_last_run_from_captured_current_position(monkeypatch):
    app = _make_app()

    class ReplayCheckingAlgorithm:
        name = "replay_check"
        display_name = "Replay check"

        def __init__(self):
            self.run_count = 0

        def run(self, device):
            self.run_count += 1
            first = device.move_lens(0, dx=2.0e-6)
            second = device.move_lens(1, dy=-1.0e-6)
            assert first.received_power >= 0.0
            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=second,
                move_history=device.move_history(),
                message="replay checked",
            )

    algorithm = ReplayCheckingAlgorithm()
    monkeypatch.setattr(alignment_lab_module, "get_algorithm", lambda _name: algorithm)

    try:
        run_start = app.current_poses()
        app.run_alignment_algorithm("replay_check")
        run_final = app.current_poses()

        assert algorithm.run_count == 1
        assert app._last_algorithm_run is not None  # pylint: disable=protected-access
        _assert_pose_close(app._last_algorithm_run.initial_poses, run_start)  # pylint: disable=protected-access

        disturbed = list(run_final)
        disturbed[0] = (disturbed[0][0] + 20.0e-6, disturbed[0][1], disturbed[0][2])
        app._apply_lens_poses(tuple(disturbed))  # pylint: disable=protected-access

        app.show_alignment_algorithm(delay_ms=0)
        for _index in range(10):
            app.update()
            if app._algorithm_animation_after_id is None:  # pylint: disable=protected-access
                break

        assert algorithm.run_count == 1
        _assert_pose_close(app.current_poses(), run_final)
        assert "Replay check" in app.algorithm_status_var.get()
    finally:
        app.destroy()


def test_coordinate_scan_improves_startup_out_of_beam_alignment():
    app = _make_app()

    try:
        before = app.evaluate_current_alignment()
        evaluation = app.run_alignment_algorithm("coordinate_scan")

        assert evaluation.received_power > before.received_power
        assert evaluation.mode_efficiency > 0.95
    finally:
        app.destroy()


def test_position_solve_is_selectable_and_runs_from_simulation_ui():
    app = _make_app()

    try:
        app._rescramble_alignment_errors()  # pylint: disable=protected-access
        before = app.evaluate_current_alignment()

        assert app._algorithm_label_to_name["Position solve/noiseless model"] == "position_solve"  # pylint: disable=protected-access
        app.algorithm_var.set("Position solve/noiseless model")
        evaluation = app.run_alignment_algorithm()

        assert evaluation.received_power > before.received_power
        assert evaluation.mode_efficiency > 0.9
        assert "Position solve/noiseless model" in app.algorithm_status_var.get()
    finally:
        app.destroy()


def test_given_positions_moves_to_reference_pose_without_scan():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED, startup_out_of_beam=True)
    before = device.measure()

    result = GivenPositionsAlgorithm().run(device)

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.5
    _assert_pose_close(result.final_poses, device.starting_poses())
    assert len(result.move_history) == 2


def test_reference_pose_only_does_not_solve_seeded_source_taper_offsets():
    device = SimulatedAlignmentDevice(3)

    result = GivenPositionsAlgorithm().run(device)

    _assert_pose_close(result.final_poses, device.starting_poses())
    assert result.final_reading.mode_efficiency < DEFAULT_TARGET_MODE_EFFICIENCY


@pytest.mark.parametrize("seed", range(8))
def test_coordinate_scan_reaches_mode_match_from_seeded_power_only_simulation(seed):
    device = SimulatedAlignmentDevice(seed)
    before = device.measure()

    result = CoordinateScanAlgorithm().run(device)

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.9


@pytest.mark.parametrize("seed", range(8))
def test_position_solve_reaches_mode_match_from_seeded_noiseless_model(seed):
    device = SimulatedAlignmentDevice(seed)
    before = device.measure()

    result = PositionSolveAlgorithm().run(device)
    geometry = device.model_geometry()

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.9
    assert has_strict_axial_clearance(
        result.final_poses,
        geometry.balls,
        geometry.source.position,
        geometry.taper.position,
    )


def test_position_solve_j_steps_records_visible_probe_moves():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)

    result = PositionSolveWithJStepsAlgorithm().run(device)
    moves = result.move_history

    assert result.final_reading.mode_efficiency > 0.9
    assert "J-matrix probe and solution moves" in result.message
    assert sum(not np.isclose(move.dz, 0.0) for move in moves) >= 4
    assert sum(np.isclose(abs(move.dx), TRANSVERSE_RESPONSE_STEP) for move in moves) >= 8
    assert sum(np.isclose(abs(move.dy), TRANSVERSE_RESPONSE_STEP) for move in moves) >= 8
    assert any(np.isclose(move.dx, TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(np.isclose(move.dx, -TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(np.isclose(move.dy, TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(np.isclose(move.dy, -TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(abs(move.dx) > 1.1 * TRANSVERSE_RESPONSE_STEP for move in moves)
    assert any(abs(move.dy) > 1.1 * TRANSVERSE_RESPONSE_STEP for move in moves)


def test_beam_error_j_matrix_uses_probe_matrices_and_reaches_mode_match():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)
    before = device.measure()

    result = BeamErrorJMatrixAlgorithm().run(device)
    moves = result.move_history

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.9
    assert "beam-error local Jx/Jy probe moves" in result.message
    assert any(np.isclose(move.dx, TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(np.isclose(move.dx, -TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(np.isclose(move.dy, TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(np.isclose(move.dy, -TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(abs(move.dx) > 1.1 * TRANSVERSE_RESPONSE_STEP for move in moves)
    assert any(abs(move.dy) > 1.1 * TRANSVERSE_RESPONSE_STEP for move in moves)


def test_fixed_z_j_matrix_uses_probe_matrices_without_z_moves():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)
    before = device.measure()
    starting_z = tuple(pose[2] for pose in device.current_poses())

    result = FixedZJMatrixAlgorithm().run(device)
    moves = result.move_history

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.9
    assert "no z moves" in result.message
    assert all(np.isclose(move.dz, 0.0) for move in moves)
    assert tuple(pose[2] for pose in result.final_poses) == starting_z
    assert any(np.isclose(move.dx, TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(np.isclose(move.dx, -TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(np.isclose(move.dy, TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(np.isclose(move.dy, -TRANSVERSE_RESPONSE_STEP) for move in moves)
    assert any(abs(move.dx) > 1.1 * TRANSVERSE_RESPONSE_STEP for move in moves)
    assert any(abs(move.dy) > 1.1 * TRANSVERSE_RESPONSE_STEP for move in moves)


def test_real_alignment_algorithms_reach_good_mode_match_across_10_case_sweep():
    failures = []
    algorithm_factories = (PositionSolveAlgorithm, CoordinateScanAlgorithm)

    for algorithm_factory in algorithm_factories:
        for seed in range(10):
            device = SimulatedAlignmentDevice(seed, startup_out_of_beam=(seed % 10 == 0))
            result = algorithm_factory().run(device)
            if result.final_reading.mode_efficiency < DEFAULT_TARGET_MODE_EFFICIENCY:
                failures.append(
                    (
                        result.name,
                        seed,
                        result.final_reading.mode_efficiency,
                        result.final_reading.received_power * 1e3,
                    )
                )

    assert failures == []


def test_position_solve_retries_after_low_mode_attempt(monkeypatch):
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)
    search_windows = []
    move_modes = iter((0.1, 0.75))

    def fake_solve_candidate(geometry, axial_search_window=0.0):
        search_windows.append(axial_search_window)
        return position_solve_module.PositionSolveCandidate(
            poses=geometry.starting_poses,
            reading=PowerReading(
                received_power=1.0e-3,
                total_efficiency=1.0,
                mode_efficiency=1.0,
            ),
        )

    def fake_move_to_target_poses(fake_device, _geometry, target_poses):
        apply_test_poses(target_poses)
        mode = next(move_modes)
        return PowerReading(
            received_power=mode * 1.0e-3,
            total_efficiency=mode,
            mode_efficiency=mode,
            move_count=len(fake_device.move_history()),
            measurement_count=999,
        )

    def apply_test_poses(poses):
        for ball, pose in zip(device.balls, poses):
            ball.x_offset, ball.y_offset, ball.position = pose

    monkeypatch.setattr(position_solve_module, "solve_position_candidate", fake_solve_candidate)
    monkeypatch.setattr(position_solve_module, "move_to_target_poses", fake_move_to_target_poses)

    result = PositionSolveAlgorithm(search_windows=(1.0, 2.0, 3.0)).run(device)

    assert result.final_reading.mode_efficiency == 0.75
    assert search_windows == [1.0, 2.0]
    assert "2 attempt" in result.message


def test_yase_given_positions_runs_from_startup_out_of_beam_simulation():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED, startup_out_of_beam=True)
    before = device.measure()
    algorithm = YaseAlignmentAlgorithm(
        "SUB_Alignment/SUB_GivenPositionsReferencePose.xseq",
        root=DEFAULT_YASE_ROOT,
        config_path=DEFAULT_YASE_CONFIG,
    )

    result = algorithm.run(device)

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.5
    _assert_pose_close(result.final_poses, device.starting_poses())


def test_yase_position_solve_runs_in_seeded_simulation():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)
    before = device.measure()
    algorithm = YaseAlignmentAlgorithm(
        "SUB_Alignment/SUB_PositionSolveNoiselessModel.xseq",
        root=DEFAULT_YASE_ROOT,
        config_path=DEFAULT_YASE_CONFIG,
        max_steps=20000,
    )

    result = algorithm.run(device)

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.9


def test_yase_position_solve_returns_robust_alignment_fields():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)
    machine = DeviceBackedYaseMachine.from_config_and_device(DEFAULT_YASE_ROOT, DEFAULT_YASE_CONFIG, device)
    interpreter = YaseInterpreter(machine)

    result = interpreter.run("SUB_Alignment/SUB_PositionSolveNoiselessModel.xseq", max_steps=20000)

    assert result.return_parameters["Success"] == 1.0
    assert result.return_parameters["Attempts"] >= 1.0
    assert result.return_parameters["FinalMode"] >= DEFAULT_TARGET_MODE_EFFICIENCY
    assert result.return_parameters["FinalPower"] > 0.0
    assert result.return_parameters["ModelPower"] > 0.0


def test_yase_power_only_coordinate_scan_runs_in_seeded_simulation():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)
    before = device.measure()
    algorithm = YaseAlignmentAlgorithm(
        "SUB_Alignment/SUB_PowerOnlyCoordinateScan.xseq",
        root=DEFAULT_YASE_ROOT,
        config_path=DEFAULT_YASE_CONFIG,
        max_steps=20000,
    )

    result = algorithm.run(device)

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.9


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
        assert "MODE MATCH:" in app.power_percent_var.get()
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


def test_alignment_lab_shows_x_and_y_plane_views():
    app = _make_app()

    try:
        app.update_idletasks()
        app.redraw()

        assert app.canvas.grid_info()
        assert app.y_canvas.grid_info()  # pylint: disable=protected-access
        assert int(app.canvas.grid_info()["row"]) == int(app.y_canvas.grid_info()["row"])  # pylint: disable=protected-access
        assert int(app.canvas.grid_info()["column"]) != int(app.y_canvas.grid_info()["column"])  # pylint: disable=protected-access
        assert app.canvas.find_all()
        assert app.y_canvas.find_all()  # pylint: disable=protected-access
        assert app._beam_paths  # pylint: disable=protected-access
        assert all(path.y and path.wy for path in app._beam_paths)  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_alignment_lab_collapses_parameters_and_output_by_default_and_can_reopen():
    app = _make_app()

    try:
        app.update_idletasks()

        assert not app._parameters_panel_open  # pylint: disable=protected-access
        assert not app._output_panel_open  # pylint: disable=protected-access
        assert not app.parameters_frame.winfo_ismapped()
        assert not app.output_frame.winfo_ismapped()
        assert str(app._side_pane) not in {str(pane) for pane in app._main_paned.panes()}  # pylint: disable=protected-access

        app._toggle_parameters_panel()  # pylint: disable=protected-access
        app._toggle_output_panel()  # pylint: disable=protected-access
        app.update_idletasks()

        assert app._parameters_panel_open  # pylint: disable=protected-access
        assert app._output_panel_open  # pylint: disable=protected-access
        assert str(app._side_pane) in {str(pane) for pane in app._main_paned.panes()}  # pylint: disable=protected-access
        assert app.parameters_frame.winfo_ismapped()
        assert app.output_frame.winfo_ismapped()
    finally:
        app.destroy()


def test_alignment_lab_y_plane_drag_updates_y_offset_not_x_offset():
    app = _make_app()

    try:
        ball = app.balls[0]
        start_x = ball.x_offset
        target_z = ball.position + 1.0e-6
        target_y = ball.y_offset + 3.0e-6

        app._drag = {  # pylint: disable=protected-access
            "uid": ball.uid,
            "mode": "move",
            "plane": "y",
            "canvas": app.y_canvas,
            "undo_pushed": False,
        }
        app._on_canvas_drag(  # pylint: disable=protected-access
            SimpleNamespace(
                widget=app.y_canvas,
                x=app._z_to_px(target_z, app.y_canvas),  # pylint: disable=protected-access
                y=app._x_to_px(target_y, app.y_canvas),  # pylint: disable=protected-access
            )
        )

        assert np.isclose(ball.position, target_z)
        assert np.isclose(ball.y_offset, target_y)
        assert np.isclose(ball.x_offset, start_x)
    finally:
        app.destroy()


def test_alignment_lab_y_plane_double_click_selects_element(monkeypatch):
    app = _make_app()

    try:
        app.redraw()
        ball = app.balls[0]
        y_move_actions = [
            action
            for (canvas_name, _item_id), action in app._item_actions.items()  # pylint: disable=protected-access
            if canvas_name == str(app.y_canvas) and action == (ball.uid, "move", "y")
        ]
        opened: list[str] = []

        assert y_move_actions
        monkeypatch.setattr(app, "_current_action", lambda _event=None: y_move_actions[0])
        monkeypatch.setattr(app, "_edit_selected", lambda: opened.append(app.selected_uid))

        app._on_canvas_double_click(SimpleNamespace(widget=app.y_canvas))  # pylint: disable=protected-access

        assert app.selected_uid == ball.uid
        assert opened == [ball.uid]
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
