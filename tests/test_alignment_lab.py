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
from alignment_algorithms.blind_power_j import (
    BLIND_POWER_J_STEPS,
    DIRECTION_METHOD_BEST_OF_9,
    DIRECTION_METHOD_GRADIENT,
    DIRECTION_METHOD_NEWTON,
    BlindPowerJAlgorithm,
)
from alignment_algorithms.blind_power_j_best_of_9 import BlindPowerJBestOf9Algorithm
from alignment_algorithms.blind_power_j_gradient import BlindPowerJGradientAlgorithm
from alignment_algorithms.blind_power_j_newton import BlindPowerJNewtonAlgorithm
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
import interactive_setup as interactive_setup_module
from alignment_lab import (
    DEFAULT_ALIGNMENT_SEED,
    DEFAULT_ALIGNMENT_VIEW_Z_MARGIN,
    DEFAULT_ALIGNMENT_WORKSPACE_TRANSVERSE_LIMIT,
    DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN,
    DEFAULT_INITIAL_BALL_X_OFFSET,
    DEFAULT_INITIAL_BALL_Y_OFFSET,
    DEFAULT_LASER_NO_GO_Y_MAX,
    DEFAULT_LENS_POSE_TOLERANCE,
    DEFAULT_TAPER_NO_GO_Y_MAX,
    DEFAULT_TAPER_TRENCH_Y_MAX,
    DEFAULT_POWER_NOISE_PERCENT,
    DEFAULT_SOURCE_DETECTOR_TOLERANCE,
    LAB_ALGORITHM_NAMES,
    POWER_NOISE_DELTA_ADDED_COLOR,
    POWER_NOISE_DELTA_SUBTRACTED_COLOR,
    AlignmentLabEditor,
    alignment_no_go_zones_for_layout,
    ball_lens_no_go_violations,
    outside_trench_starting_poses,
    seeded_alignment_errors,
)
from interactive_setup import (
    AXIAL_TOLERANCE,
    DEFAULT_CLIPPING_RADIUS_FACTOR,
    DEFAULT_REFRACTIVE_INDEX,
    LaserSource,
    TRANSVERSE_TOLERANCE,
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


def _assert_layout_matches_snapshot(app: AlignmentLabEditor, snapshot):
    actual_snapshot = {
        "sources": app.sources,
        "lenses": app.lenses,
        "balls": app.balls,
        "fibers": app.fibers,
        "tapers": app.tapers,
        "final_z": app.final_z,
        "selected_uid": app.selected_uid,
    }
    _assert_snapshots_match(actual_snapshot, snapshot)


def _assert_snapshots_match(actual_snapshot, expected_snapshot):
    for collection_name in ("sources", "lenses", "balls", "fibers", "tapers"):
        actual_elements = actual_snapshot[collection_name]
        expected_elements = expected_snapshot[collection_name]
        assert len(actual_elements) == len(expected_elements)
        assert [_layout_element_state(element) for element in actual_elements] == [
            _layout_element_state(element) for element in expected_elements
        ]
    assert actual_snapshot["final_z"] == expected_snapshot["final_z"]
    assert actual_snapshot["selected_uid"] == expected_snapshot["selected_uid"]


def _axis_var_values(axis_vars):
    return tuple(variable.get() for variable in axis_vars)


def _layout_element_state(element):
    state = dict(element.__dict__)
    state.pop("received_power", None)
    return state


def _noiseless_power_at_pose(app: AlignmentLabEditor, poses) -> float:
    current = app.current_poses()
    app._apply_lens_poses(poses)  # pylint: disable=protected-access
    try:
        return app.evaluate_current_alignment().received_power
    finally:
        app._apply_lens_poses(current)  # pylint: disable=protected-access


class SimulatedAlignmentDevice:
    def __init__(self, seed: int, *, startup_out_of_beam: bool = False) -> None:
        self.source = LaserSource()
        self.balls, tapers, self.final_z = default_ball_lens_layout()
        self.taper = tapers[0]
        self._starting_poses = tuple((ball.x_offset, ball.y_offset, ball.position) for ball in self.balls)
        self._moves: list[AlignmentMove] = []
        self._move_count = 0
        self._measurement_count = 0
        self._pending_direction_method: str | None = None

        if startup_out_of_beam:
            for ball in self.balls:
                ball.x_offset = DEFAULT_INITIAL_BALL_X_OFFSET
                ball.y_offset = DEFAULT_INITIAL_BALL_Y_OFFSET
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

    def set_next_move_direction_method(self, direction_method: str | None) -> None:
        self._pending_direction_method = direction_method

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
                direction_method=self._pending_direction_method,
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


def test_alignment_algorithm_registry_matches_dropdown_algorithms():
    algorithms = available_algorithms()

    assert set(algorithms) == alignment_lab_module.LAB_ALGORITHM_NAMES
    assert get_algorithm("blind_power_j").display_name == "Blind power J"
    assert get_algorithm("blind_power_j_newton").display_name == "Blind power J: Newton"
    assert get_algorithm("blind_power_j_gradient").display_name == "Blind power J: Gradient"
    assert get_algorithm("blind_power_j_best_of_9").display_name == "Blind power J: Best-of-9"
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

        assert algorithm_names == LAB_ALGORITHM_NAMES
        assert all(not name.startswith("yase:") for name in algorithm_names)
    finally:
        app.destroy()


def test_alignment_lab_defaults_to_blind_power_j():
    app = _make_app()

    try:
        assert app.algorithm_var.get() == "Blind power J"
        assert app.blind_power_j_steps_var.get() == "5, 2, 1, 0.5, 0.25"
        assert set(app.blind_power_j_step_vars) == {
            "blind_power_j",
            "blind_power_j_newton",
            "blind_power_j_gradient",
            "blind_power_j_best_of_9",
        }
        for algorithm_name in app.blind_power_j_step_vars:
            assert app.blind_power_j_step_vars[algorithm_name].get() == "5, 2, 1, 0.5, 0.25"
            assert app.blind_power_j_attempt_vars[algorithm_name].get() == "1"
            assert app.blind_power_j_max_correction_vars[algorithm_name].get() == "25"
            assert app.blind_power_j_sample_vars[algorithm_name].get() == "1"
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
    assert DEFAULT_POWER_NOISE_PERCENT == 0.0


def test_alignment_no_go_zones_store_laser_taper_and_vacuum_constraints():
    balls, tapers, final_z = default_ball_lens_layout()
    poses = tuple((ball.x_offset, ball.y_offset, ball.position) for ball in balls)
    radii = tuple(ball.radius for ball in balls)
    zones = alignment_no_go_zones_for_layout(
        source_z=0.0,
        taper_z=tapers[0].position,
        final_z=final_z,
        ball_poses=poses,
        ball_radii=radii,
    )
    zones_by_name = {zone.name: zone for zone in zones}

    assert set(zones_by_name) == {
        "laser_side_no_go",
        "trench_floor",
        "taper_side_no_go",
        "vacuum_tweezer_1",
        "vacuum_tweezer_2",
    }
    assert zones_by_name["laser_side_no_go"].z_low < 0.0
    assert np.isclose(zones_by_name["laser_side_no_go"].z_high, 0.0)
    assert np.isclose(zones_by_name["laser_side_no_go"].y_max, DEFAULT_LASER_NO_GO_Y_MAX)
    assert np.isclose(zones_by_name["trench_floor"].z_low, 0.0)
    assert np.isclose(zones_by_name["trench_floor"].z_high, tapers[0].position)
    assert np.isclose(zones_by_name["trench_floor"].y_max, DEFAULT_TAPER_TRENCH_Y_MAX)
    assert np.isclose(zones_by_name["taper_side_no_go"].z_low, tapers[0].position)
    assert zones_by_name["taper_side_no_go"].z_high > tapers[0].position
    assert np.isclose(zones_by_name["taper_side_no_go"].y_max, DEFAULT_TAPER_NO_GO_Y_MAX)
    assert np.isclose(zones_by_name["vacuum_tweezer_1"].z_low, balls[0].entry_z)
    assert np.isclose(zones_by_name["vacuum_tweezer_1"].z_high, balls[0].exit_z)
    assert np.isclose(zones_by_name["vacuum_tweezer_1"].x_min, balls[0].x_offset - radii[0])
    assert np.isclose(zones_by_name["vacuum_tweezer_1"].x_max, balls[0].x_offset + radii[0])
    assert np.isclose(zones_by_name["vacuum_tweezer_1"].y_min, balls[0].y_offset + radii[0])
    assert zones_by_name["vacuum_tweezer_1"].y_max is None
    assert all(zone.applies_to_all_x for zone in zones)


def test_ball_lens_no_go_violations_use_ball_edges_not_centers():
    balls, tapers, final_z = default_ball_lens_layout()
    radii = tuple(ball.radius for ball in balls)
    names = tuple(ball.name for ball in balls)
    default_poses = tuple((ball.x_offset, ball.y_offset, ball.position) for ball in balls)
    zones = alignment_no_go_zones_for_layout(
        source_z=0.0,
        taper_z=tapers[0].position,
        final_z=final_z,
        ball_poses=default_poses,
        ball_radii=radii,
    )

    assert ball_lens_no_go_violations(default_poses, radii, zones, names) == ()

    laser_collision = list(default_poses)
    laser_collision[0] = (0.0, DEFAULT_LASER_NO_GO_Y_MAX + radii[0] - 1e-6, 0.0)
    laser_zones = alignment_no_go_zones_for_layout(
        source_z=0.0,
        taper_z=tapers[0].position,
        final_z=final_z,
        ball_poses=tuple(laser_collision),
        ball_radii=radii,
    )
    laser_violations = ball_lens_no_go_violations(tuple(laser_collision), radii, laser_zones, names)
    assert [violation.zone.name for violation in laser_violations] == ["laser_side_no_go"]

    taper_collision = list(default_poses)
    taper_collision[1] = (0.0, DEFAULT_TAPER_NO_GO_Y_MAX + radii[1] - 1e-6, tapers[0].position)
    taper_zones = alignment_no_go_zones_for_layout(
        source_z=0.0,
        taper_z=tapers[0].position,
        final_z=final_z,
        ball_poses=tuple(taper_collision),
        ball_radii=radii,
    )
    taper_violations = ball_lens_no_go_violations(tuple(taper_collision), radii, taper_zones, names)
    assert [violation.zone.name for violation in taper_violations] == ["taper_side_no_go"]

    floor_collision = list(default_poses)
    floor_collision[0] = (0.0, DEFAULT_TAPER_TRENCH_Y_MAX + radii[0] - 1e-6, balls[0].position)
    floor_zones = alignment_no_go_zones_for_layout(
        source_z=0.0,
        taper_z=tapers[0].position,
        final_z=final_z,
        ball_poses=tuple(floor_collision),
        ball_radii=radii,
    )
    floor_violations = ball_lens_no_go_violations(tuple(floor_collision), radii, floor_zones, names)
    assert [violation.zone.name for violation in floor_violations] == ["trench_floor"]

    vacuum_collision = list(default_poses)
    vacuum_collision[1] = (0.0, radii[0], balls[0].position)
    vacuum_zones = alignment_no_go_zones_for_layout(
        source_z=0.0,
        taper_z=tapers[0].position,
        final_z=final_z,
        ball_poses=tuple(vacuum_collision),
        ball_radii=radii,
    )
    vacuum_violations = ball_lens_no_go_violations(tuple(vacuum_collision), radii, vacuum_zones, names)
    assert [violation.zone.name for violation in vacuum_violations] == ["vacuum_tweezer_1"]


def test_alignment_lab_uses_return_to_start_button_not_lower_rescramble():
    app = _make_app()

    try:
        assert app.return_to_start_button["text"] == "Return to start"
        assert app.vision_script_scramble_button["text"] == "Vision script scramble"
    finally:
        app.destroy()


def test_scramble_tolerances_section_exists_with_defaults():
    app = _make_app()

    try:
        assert app.scramble_tolerances_frame["text"] == "Scramble tolerances"
        assert str(app.scramble_tolerances_frame.master) == str(app.parameters_frame)
        assert int(app.scramble_tolerances_frame.grid_info()["row"]) == 1
        assert set(app.scramble_tolerance_sections) == {
            "Seed scramble",
            "Vision script scramble",
            "Scramble laser/fibre",
            "Full scramble",
        }
        assert _axis_var_values(app.seed_laser_tolerance_vars) == ("5", "5", "0")
        assert _axis_var_values(app.seed_receiver_tolerance_vars) == ("5", "5", "0")
        assert all(_axis_var_values(axis_vars) == ("2", "2", "2") for axis_vars in app.seed_ball_tolerance_vars)
        assert _axis_var_values(app.vision_laser_tolerance_vars) == ("1", "1", "5")
        assert _axis_var_values(app.vision_receiver_tolerance_vars) == ("1", "5", "1")
        assert all(_axis_var_values(axis_vars) == ("0", "0", "0") for axis_vars in app.vision_ball_tolerance_vars)
        assert _axis_var_values(app.laser_fibre_laser_tolerance_vars) == ("5", "5", "0")
        assert _axis_var_values(app.laser_fibre_receiver_tolerance_vars) == ("5", "5", "0")
        assert all(_axis_var_values(axis_vars) == ("0", "0", "0") for axis_vars in app.laser_fibre_ball_tolerance_vars)
        assert _axis_var_values(app.full_scramble_laser_tolerance_vars) == ("50", "50", "0")
        assert _axis_var_values(app.full_scramble_receiver_tolerance_vars) == ("50", "50", "0")
        assert all(_axis_var_values(axis_vars) == ("50", "50", "5") for axis_vars in app.full_scramble_ball_tolerance_vars)
    finally:
        app.destroy()


def test_seeded_rescramble_uses_configured_tolerances():
    app = _make_app()

    try:
        app.seed_var.set("7")
        for variable, value in zip(app.seed_laser_tolerance_vars, ("1.25", "0.75", "0.5")):
            variable.set(value)
        for variable, value in zip(app.seed_receiver_tolerance_vars, ("0.9", "1.1", "0.4")):
            variable.set(value)
        for ball_vars in app.seed_ball_tolerance_vars:
            for variable, value in zip(ball_vars, ("0.5", "0.25", "0.75")):
                variable.set(value)
        source_nominal = app._nominal_for(app.sources[0])  # pylint: disable=protected-access
        taper_nominal = app._nominal_for(app.tapers[0])  # pylint: disable=protected-access

        app._rescramble_alignment_errors()  # pylint: disable=protected-access

        assert abs(app.sources[0].x_offset - source_nominal.x_offset) <= 1.25e-6
        assert abs(app.sources[0].y_offset - source_nominal.y_offset) <= 0.75e-6
        assert abs(app.sources[0].position - source_nominal.position) <= 0.5e-6
        assert abs(app.tapers[0].x_offset - taper_nominal.x_offset) <= 0.9e-6
        assert abs(app.tapers[0].y_offset - taper_nominal.y_offset) <= 1.1e-6
        assert abs(app.tapers[0].position - taper_nominal.position) <= 0.4e-6
        for offset in app._ball_pose_offsets_from_nominal():  # pylint: disable=protected-access
            assert abs(offset[0]) <= 0.5e-6
            assert abs(offset[1]) <= 0.25e-6
            assert abs(offset[2]) <= 0.75e-6
    finally:
        app.destroy()


def test_vision_script_scramble_uses_configured_axis_tolerances(monkeypatch):
    app = _make_app()

    try:
        for variable, value in zip(app.vision_laser_tolerance_vars, ("2", "4", "7")):
            variable.set(value)
        for variable, value in zip(app.vision_receiver_tolerance_vars, ("3", "11", "13")):
            variable.set(value)
        for variable, value in zip(app.vision_ball_tolerance_vars[0], ("0.5", "0.6", "0.7")):
            variable.set(value)
        amplitudes = []

        def record_amplitude(amplitude):
            amplitudes.append(amplitude)
            return 0.0

        monkeypatch.setattr(app, "next_vision_script_scramble_delta", record_amplitude)

        app._vision_script_scramble()  # pylint: disable=protected-access

        assert np.allclose(amplitudes, (2e-6, 4e-6, 7e-6, 3e-6, 11e-6, 13e-6, 0.5e-6, 0.6e-6, 0.7e-6))
    finally:
        app.destroy()


def test_base_toolbar_scrambles_use_configured_tolerances():
    app = _make_app()

    try:
        ball_start = app.current_poses()
        for variable, value in zip(app.laser_fibre_laser_tolerance_vars, ("0.25", "0.15", "0")):
            variable.set(value)
        for variable, value in zip(app.laser_fibre_receiver_tolerance_vars, ("0.35", "0.45", "0")):
            variable.set(value)

        app._scramble_laser_fibre()  # pylint: disable=protected-access

        assert 0.0 <= app.sources[0].x_offset <= 0.25e-6
        assert 0.0 <= app.sources[0].y_offset <= 0.15e-6
        assert 0.0 <= app.tapers[0].x_offset <= 0.35e-6
        assert 0.0 <= app.tapers[0].y_offset <= 0.45e-6
        _assert_pose_close(app.current_poses(), ball_start)

        for variable, value in zip(app.laser_fibre_ball_tolerance_vars[0], ("0.2", "0.1", "0")):
            variable.set(value)

        app._scramble_laser_fibre()  # pylint: disable=protected-access

        first_ball_nominal = app._nominal_for(app.balls[0])  # pylint: disable=protected-access
        assert 0.0 <= app.balls[0].x_offset <= 0.2e-6
        assert 0.0 <= app.balls[0].y_offset <= 0.1e-6
        assert np.isclose(app.balls[0].position, first_ball_nominal.position)

        for variable, value in zip(app.full_scramble_laser_tolerance_vars, ("0.3", "0.2", "0")):
            variable.set(value)
        for variable, value in zip(app.full_scramble_receiver_tolerance_vars, ("0.25", "0.15", "0")):
            variable.set(value)
        for ball_vars in app.full_scramble_ball_tolerance_vars:
            for variable, value in zip(ball_vars, ("0.4", "0.35", "0.2")):
                variable.set(value)

        app._scramble_full()  # pylint: disable=protected-access

        for ball in app.balls:
            nominal = app._nominal_for(ball)  # pylint: disable=protected-access
            assert nominal.position <= ball.position <= nominal.position + 0.2e-6
            assert 0.0 <= ball.x_offset <= 0.4e-6
            assert 0.0 <= ball.y_offset <= 0.35e-6
        assert 0.0 <= app.sources[0].x_offset <= 0.3e-6
        assert 0.0 <= app.sources[0].y_offset <= 0.2e-6
        assert 0.0 <= app.tapers[0].x_offset <= 0.25e-6
        assert 0.0 <= app.tapers[0].y_offset <= 0.15e-6
    finally:
        app.destroy()


def test_invalid_seeded_scramble_tolerance_does_not_mutate_layout(monkeypatch):
    app = _make_app()
    errors = []

    try:
        monkeypatch.setattr(alignment_lab_module.messagebox, "showerror", lambda title, message: errors.append((title, message)))
        app.source_detector_tolerance_var.set("inf")
        before = app._layout_snapshot()  # pylint: disable=protected-access

        app._rescramble_alignment_errors()  # pylint: disable=protected-access

        _assert_layout_matches_snapshot(app, before)
        assert errors
    finally:
        app.destroy()


def test_invalid_vision_script_tolerance_does_not_mutate_layout(monkeypatch):
    app = _make_app()
    errors = []

    try:
        monkeypatch.setattr(alignment_lab_module.messagebox, "showerror", lambda title, message: errors.append((title, message)))
        app.vision_laser_xy_tolerance_var.set("-1")
        before = app._layout_snapshot()  # pylint: disable=protected-access

        app._vision_script_scramble()  # pylint: disable=protected-access

        _assert_layout_matches_snapshot(app, before)
        assert errors
    finally:
        app.destroy()


def test_invalid_base_scramble_tolerance_does_not_mutate_layout(monkeypatch):
    app = _make_app()
    errors = []

    try:
        monkeypatch.setattr(interactive_setup_module.messagebox, "showerror", lambda title, message: errors.append((title, message)))
        app.full_scramble_lens_z_tolerance_var.set("bad")
        before = app._layout_snapshot()  # pylint: disable=protected-access

        app._scramble_full()  # pylint: disable=protected-access

        _assert_layout_matches_snapshot(app, before)
        assert errors
    finally:
        app.destroy()


def test_vision_script_scramble_moves_laser_and_receiver_relative_to_current(monkeypatch):
    app = _make_app()

    try:
        app.sources[0].x_offset = 11.0e-6
        app.sources[0].y_offset = -12.0e-6
        app.sources[0].position = 20.0e-6
        app.tapers[0].x_offset = -13.0e-6
        app.tapers[0].y_offset = 14.0e-6
        app.tapers[0].position += 15.0e-6
        source_start = (app.sources[0].x_offset, app.sources[0].y_offset, app.sources[0].position)
        receiver_start = (app.tapers[0].x_offset, app.tapers[0].position, app.tapers[0].y_offset)
        ball_start = app.current_poses()
        deltas = iter((0.4e-6, -0.8e-6, 3.0e-6, -0.6e-6, -4.0e-6, 0.7e-6))
        monkeypatch.setattr(app, "next_vision_script_scramble_delta", lambda _amplitude: next(deltas))

        app._vision_script_scramble()  # pylint: disable=protected-access

        source_delta = (
            app.sources[0].x_offset - source_start[0],
            app.sources[0].y_offset - source_start[1],
            app.sources[0].position - source_start[2],
        )
        receiver_delta = (
            app.tapers[0].x_offset - receiver_start[0],
            app.tapers[0].position - receiver_start[1],
            app.tapers[0].y_offset - receiver_start[2],
        )
        assert np.allclose(source_delta, (0.4e-6, -0.8e-6, 3.0e-6))
        assert np.allclose(receiver_delta, (-0.6e-6, 0.7e-6, -4.0e-6))
        assert abs(source_delta[0]) <= 1.0e-6
        assert abs(source_delta[1]) <= 1.0e-6
        assert abs(source_delta[2]) <= 5.0e-6
        assert abs(receiver_delta[0]) <= 1.0e-6
        assert abs(receiver_delta[1]) <= 1.0e-6
        assert abs(receiver_delta[2]) <= 5.0e-6
        _assert_pose_close(app.current_poses(), ball_start)
        assert app.status_var.get().startswith("Vision script scramble:")
    finally:
        app.destroy()


def test_vision_script_scramble_clamps_laser_z_to_minimum(monkeypatch):
    app = _make_app()

    try:
        source = app.sources[0]
        source.position = app._minimum_element_position(source)  # pylint: disable=protected-access
        deltas = iter((0.0, 0.0, -5.0e-6, 0.0, 0.0, 0.0))
        monkeypatch.setattr(app, "next_vision_script_scramble_delta", lambda _amplitude: next(deltas))

        app._vision_script_scramble()  # pylint: disable=protected-access

        assert np.isclose(source.position, app._minimum_element_position(source))  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_vision_script_scramble_cancels_active_algorithm_show(monkeypatch):
    app = _make_app()

    class MultiMoveAlgorithm:
        name = "multi_move"
        display_name = "Multi move"

        def run(self, device):
            first = device.move_lens(0, dx=2.0e-6)
            second = device.move_lens(1, dy=-2.0e-6)
            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=second if second.measurement_count >= first.measurement_count else first,
                move_history=device.move_history(),
            )

    monkeypatch.setattr(alignment_lab_module, "get_algorithm", lambda _name: MultiMoveAlgorithm())

    try:
        app.show_alignment_algorithm("multi_move", delay_ms=100000)
        assert app._algorithm_animation_after_id is not None  # pylint: disable=protected-access
        poses_after_first_move = app.current_poses()
        monkeypatch.setattr(app, "next_vision_script_scramble_delta", lambda _amplitude: 0.0)

        app._vision_script_scramble()  # pylint: disable=protected-access
        app.update()

        assert app._algorithm_animation_after_id is None  # pylint: disable=protected-access
        _assert_pose_close(app.current_poses(), poses_after_first_move)
    finally:
        app.destroy()


def test_outside_trench_starting_poses_place_balls_before_laser_edge():
    balls, _tapers, _final_z = default_ball_lens_layout()
    poses = tuple((ball.x_offset, ball.y_offset, ball.position) for ball in balls)
    radii = tuple(ball.radius for ball in balls)

    staged = outside_trench_starting_poses(poses, radii, source_z=0.0)

    assert len(staged) == len(poses)
    assert all(pose[0] == DEFAULT_INITIAL_BALL_X_OFFSET for pose in staged)
    assert all(pose[1] == DEFAULT_INITIAL_BALL_Y_OFFSET for pose in staged)
    assert all(pose[2] + radius < 0.0 for pose, radius in zip(staged, radii))
    assert staged[0][2] + radii[0] < staged[1][2] - radii[1]


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
        assert all(np.isclose(ball.y_offset, DEFAULT_INITIAL_BALL_Y_OFFSET) for ball in app.balls)
        assert app.source_detector_tolerance == (
            DEFAULT_SOURCE_DETECTOR_TOLERANCE,
            DEFAULT_SOURCE_DETECTOR_TOLERANCE,
            0.0,
        )
        assert app.lens_pose_tolerance == (
            DEFAULT_LENS_POSE_TOLERANCE,
            DEFAULT_LENS_POSE_TOLERANCE,
            DEFAULT_LENS_POSE_TOLERANCE,
        )
    finally:
        app.destroy()


def test_alignment_lab_starting_position_helper_moves_balls_outside_trench():
    app = _make_app()

    try:
        source_z = app.sources[0].position
        before = app.current_poses()

        app._move_balls_to_starting_position()  # pylint: disable=protected-access

        after = app.current_poses()
        assert after != before
        assert all(ball.exit_z < source_z for ball in app.balls)
        assert "outside the trench" in app.status_var.get()
        assert app.algorithm_status_var.get() == "Algorithm: idle"
        assert app._best_evaluation is not None  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_alignment_lab_model_geometry_exposes_no_go_zones():
    app = _make_app()

    try:
        geometry = app.model_geometry()

        assert {zone.name for zone in geometry.no_go_zones} == {
            "laser_side_no_go",
            "trench_floor",
            "taper_side_no_go",
            "vacuum_tweezer_1",
            "vacuum_tweezer_2",
        }
        assert app.no_go_zones() == geometry.no_go_zones
        assert app.ball_no_go_violations() == ()
    finally:
        app.destroy()


def test_alignment_lab_detects_current_ball_no_go_collision():
    app = _make_app()

    try:
        poses = list(app.current_poses())
        poses[0] = (
            poses[0][0],
            DEFAULT_LASER_NO_GO_Y_MAX + app.balls[0].radius - 1e-6,
            app.sources[0].position,
        )

        violations = app.ball_no_go_violations(tuple(poses))

        assert [violation.zone.name for violation in violations] == ["laser_side_no_go"]
        assert "Laser" in violations[0].message
    finally:
        app.destroy()


def test_alignment_lab_vacuum_tweezer_zones_move_with_ball_pose():
    app = _make_app()

    try:
        start_zone = next(zone for zone in app.no_go_zones() if zone.name == "vacuum_tweezer_1")
        poses = list(app.current_poses())
        poses[0] = (poses[0][0], poses[0][1] + 12e-6, poses[0][2] + 34e-6)
        moved_zones = app._no_go_zones_for_poses(tuple(poses))  # pylint: disable=protected-access
        moved_zone = next(zone for zone in moved_zones if zone.name == "vacuum_tweezer_1")

        assert np.isclose(moved_zone.z_low - start_zone.z_low, 34e-6)
        assert np.isclose(moved_zone.z_high - start_zone.z_high, 34e-6)
        assert np.isclose(moved_zone.y_min - start_zone.y_min, 12e-6)
        assert start_zone.y_max is None
        assert moved_zone.y_max is None
    finally:
        app.destroy()


def test_alignment_lab_uses_larger_workspace_and_extends_fixed_zones():
    app = _make_app()

    try:
        app.update_idletasks()
        app.redraw()
        source_z = app.sources[0].position
        taper_z = app.tapers[0].position
        zones = {zone.name: zone for zone in app.no_go_zones()}

        assert np.isclose(app._base_z_min, source_z - DEFAULT_ALIGNMENT_VIEW_Z_MARGIN)  # pylint: disable=protected-access
        assert app._base_z_max < taper_z + DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN  # pylint: disable=protected-access
        assert app._limit_z_min <= source_z - DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN  # pylint: disable=protected-access
        assert app._limit_z_max >= taper_z + DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN  # pylint: disable=protected-access
        assert app._limit_x_min <= -DEFAULT_ALIGNMENT_WORKSPACE_TRANSVERSE_LIMIT  # pylint: disable=protected-access
        assert app._limit_x_max >= DEFAULT_ALIGNMENT_WORKSPACE_TRANSVERSE_LIMIT  # pylint: disable=protected-access
        assert np.isclose(app._z_min, app._base_z_min)  # pylint: disable=protected-access
        assert np.isclose(app._z_max, app._base_z_max)  # pylint: disable=protected-access
        assert np.isclose(zones["laser_side_no_go"].z_low, source_z - DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN)
        assert np.isclose(zones["laser_side_no_go"].z_high, source_z)
        assert np.isclose(zones["taper_side_no_go"].z_low, taper_z)
        assert np.isclose(zones["taper_side_no_go"].z_high, taper_z + DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN)
        assert np.isclose(zones["trench_floor"].z_low, source_z)
        assert np.isclose(zones["trench_floor"].z_high, taper_z)
    finally:
        app.destroy()


def test_alignment_lab_reset_view_returns_to_compact_default_view():
    app = _make_app()

    try:
        app.update_idletasks()
        app.redraw()
        default_bounds = (app._base_z_min, app._base_z_max, app._base_x_min, app._base_x_max)  # pylint: disable=protected-access

        app._zoom_anchor_z = app._limit_z_min  # pylint: disable=protected-access
        app._zoom_anchor_x = app._limit_x_min  # pylint: disable=protected-access
        app.redraw()

        assert app._z_min < default_bounds[0]  # pylint: disable=protected-access

        app._reset_view()  # pylint: disable=protected-access

        assert np.isclose(app._z_min, default_bounds[0])  # pylint: disable=protected-access
        assert np.isclose(app._z_max, default_bounds[1])  # pylint: disable=protected-access
        assert np.isclose(app._x_min, default_bounds[2])  # pylint: disable=protected-access
        assert np.isclose(app._x_max, default_bounds[3])  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_alignment_lab_scroll_zoom_out_reaches_extended_workspace():
    app = _make_app()

    try:
        app.update_idletasks()
        app.redraw()
        default_z_range = app._z_max - app._z_min  # pylint: disable=protected-access
        default_x_range = app._x_max - app._x_min  # pylint: disable=protected-access
        min_zoom = app._minimum_view_zoom()  # pylint: disable=protected-access
        left, right, top, bottom = app._plot_pixel_bounds(app.y_canvas)  # pylint: disable=protected-access

        for _index in range(24):
            app._on_mouse_wheel(  # pylint: disable=protected-access
                SimpleNamespace(
                    widget=app.y_canvas,
                    x=0.5 * (left + right),
                    y=0.5 * (top + bottom),
                    delta=-120,
                )
            )

        assert app._view_zoom < 1.0  # pylint: disable=protected-access
        assert np.isclose(app._view_zoom, min_zoom)  # pylint: disable=protected-access
        assert app._z_max - app._z_min > default_z_range  # pylint: disable=protected-access
        assert app._x_max - app._x_min > default_x_range  # pylint: disable=protected-access
        assert app._z_min >= app._limit_z_min  # pylint: disable=protected-access
        assert app._z_max <= app._limit_z_max  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_alignment_device_allows_no_go_overlap_and_displays_warning():
    app = _make_app()

    try:
        device = app.create_alignment_device()
        before = app.current_poses()
        target_y = DEFAULT_LASER_NO_GO_Y_MAX + app.balls[0].radius - 1e-6
        target_z = app.sources[0].position

        reading = device.move_lens(
            0,
            dy=target_y - before[0][1],
            dz=target_z - before[0][2],
        )

        after = app.current_poses()
        assert reading.move_count == 1
        assert np.isclose(after[0][1], target_y)
        assert np.isclose(after[0][2], target_z)
        assert [violation.zone.name for violation in app.ball_no_go_violations()] == ["laser_side_no_go"]

        evaluation = app._run_alignment_simulation(update_report=False)  # pylint: disable=protected-access

        assert any("Laser no-go" in warning for warning in evaluation.warnings)
        assert "No-go warning:" in app.no_go_warning_var.get()
        assert "Laser no-go" in app.no_go_warning_var.get()
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


def test_return_to_simulation_start_without_previous_run_does_nothing():
    app = _make_app()

    try:
        app.sources[0].x_offset += 10e-6
        app.tapers[0].y_offset -= 7e-6
        app.balls[0].x_offset += 20e-6
        app.balls[1].position += 15e-6
        before = app._layout_snapshot()  # pylint: disable=protected-access

        app._return_to_simulation_start()  # pylint: disable=protected-access

        _assert_layout_matches_snapshot(app, before)
        assert app.status_var.get() == "No previous run."
    finally:
        app.destroy()


def test_return_to_simulation_start_restores_previous_algorithm_run_snapshot(monkeypatch):
    app = _make_app()

    class SnapshotCheckAlgorithm:
        name = "snapshot_check"
        display_name = "Snapshot check"

        def run(self, device):
            moved = device.move_lens(0, dx=1.0e-6, dy=-0.5e-6, dz=0.25e-6)
            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=moved,
                move_history=device.move_history(),
            )

    monkeypatch.setattr(alignment_lab_module, "get_algorithm", lambda _name: SnapshotCheckAlgorithm())

    try:
        app.sources[0].x_offset = 2.0e-6
        app.sources[0].y_offset = -3.0e-6
        app.sources[0].position = 12.0e-6
        app.tapers[0].x_offset = -2.0e-6
        app.tapers[0].y_offset = 4.0e-6
        app.tapers[0].position += 1.5e-6
        app.final_z += 8.0e-6
        app.selected_uid = app.tapers[0].uid
        app._apply_lens_poses(  # pylint: disable=protected-access
            tuple(
                (
                    pose[0] + (index + 1) * 0.6e-6,
                    pose[1] - (index + 1) * 0.4e-6,
                    pose[2] + (index + 1) * 0.2e-6,
                )
                for index, pose in enumerate(app.current_poses())
            )
        )
        run_start_snapshot = app._layout_snapshot()  # pylint: disable=protected-access

        app.run_alignment_algorithm("snapshot_check")

        assert app._last_algorithm_run is not None  # pylint: disable=protected-access
        _assert_snapshots_match(app._last_algorithm_run.initial_snapshot, run_start_snapshot)  # pylint: disable=protected-access

        app.sources[0].x_offset += 20.0e-6
        app.sources[0].y_offset += 21.0e-6
        app.sources[0].position += 3.0e-6
        app.tapers[0].x_offset -= 22.0e-6
        app.tapers[0].y_offset -= 23.0e-6
        app.tapers[0].position += 4.0e-6
        app.final_z += 5.0e-6
        app.selected_uid = app.balls[1].uid
        app._apply_lens_poses(  # pylint: disable=protected-access
            tuple((pose[0] + 30.0e-6, pose[1] - 31.0e-6, pose[2] + 32.0e-6) for pose in app.current_poses())
        )

        app._return_to_simulation_start()  # pylint: disable=protected-access

        _assert_layout_matches_snapshot(app, run_start_snapshot)
        assert app.algorithm_status_var.get() == "Algorithm: idle"
        assert app.status_var.get() == "Returned to run start."
    finally:
        app.destroy()


def test_return_to_simulation_start_cancels_active_algorithm_show(monkeypatch):
    app = _make_app()

    class MultiMoveAlgorithm:
        name = "multi_move"
        display_name = "Multi move"

        def run(self, device):
            first = device.move_lens(0, dx=2.0e-6)
            second = device.move_lens(1, dy=-2.0e-6)
            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=second if second.measurement_count >= first.measurement_count else first,
                move_history=device.move_history(),
            )

    monkeypatch.setattr(alignment_lab_module, "get_algorithm", lambda _name: MultiMoveAlgorithm())

    try:
        app._rescramble_alignment_errors()  # pylint: disable=protected-access
        start_poses = app.current_poses()

        app.show_alignment_algorithm("multi_move", delay_ms=100000)
        assert app._algorithm_animation_after_id is not None  # pylint: disable=protected-access
        assert not np.allclose(app.current_poses()[0], start_poses[0])

        app._return_to_simulation_start()  # pylint: disable=protected-access
        app.update()

        assert app._algorithm_animation_after_id is None  # pylint: disable=protected-access
        _assert_pose_close(app.current_poses(), start_poses)
        assert app.algorithm_status_var.get() == "Algorithm: idle"
        assert app.status_var.get() == "Returned to run start."
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


def test_alignment_device_noise_disabled_matches_noiseless_power():
    app = _make_app()

    try:
        app.power_noise_enabled_var.set(False)
        device = app.create_alignment_device()
        noiseless = app.evaluate_current_alignment()

        reading = device.measure()

        assert np.isclose(reading.received_power, noiseless.received_power)
        assert np.isclose(reading.total_efficiency, noiseless.total_efficiency)
        assert np.isclose(reading.mode_efficiency, noiseless.mode_efficiency)
        assert reading.noise_delta == 0.0
    finally:
        app.destroy()


def test_alignment_device_noise_enabled_fluctuates_power_against_max_coupling():
    app = _make_app()

    try:
        app.power_noise_enabled_var.set(True)
        app.power_noise_percent_var.set("10")
        app._power_noise_rng.seed(123)  # pylint: disable=protected-access
        app._apply_lens_poses(app.starting_poses())  # pylint: disable=protected-access
        device = app.create_alignment_device()
        noiseless = app.evaluate_current_alignment()
        amplitude = 0.10 * app.max_coupled_power()

        first = device.measure()
        second = device.measure()

        assert first.received_power != second.received_power
        assert abs(first.received_power - noiseless.received_power) <= amplitude
        assert abs(second.received_power - noiseless.received_power) <= amplitude
        assert 0.0 <= first.received_power <= app.max_coupled_power()
        assert 0.0 <= second.received_power <= app.max_coupled_power()
        assert np.isclose(first.total_efficiency, first.received_power / app.max_coupled_power())
        assert np.isclose(second.total_efficiency, second.received_power / app.max_coupled_power())
        assert np.isclose(first.mode_efficiency, noiseless.mode_efficiency)
        assert np.isclose(second.mode_efficiency, noiseless.mode_efficiency)
    finally:
        app.destroy()


def test_alignment_device_move_lens_applies_power_noise():
    app = _make_app()

    try:
        app.power_noise_enabled_var.set(True)
        app.power_noise_percent_var.set("10")
        app._power_noise_rng.seed(456)  # pylint: disable=protected-access
        app._apply_lens_poses(app.starting_poses())  # pylint: disable=protected-access
        device = app.create_alignment_device()
        amplitude = 0.10 * app.max_coupled_power()

        noisy = device.move_lens(0, dx=0.25e-6)
        noiseless = app.evaluate_current_alignment()

        assert noisy.received_power != noiseless.received_power
        assert abs(noisy.received_power - noiseless.received_power) <= amplitude
        assert np.isclose(noisy.mode_efficiency, noiseless.mode_efficiency)
        assert noisy.noise_delta != 0.0
        assert np.isclose(
            noisy.received_power,
            min(max(noiseless.received_power + noisy.noise_delta, 0.0), app.max_coupled_power()),
        )
    finally:
        app.destroy()


def test_show_playback_displays_noise_delta_suffix_when_noise_enabled(monkeypatch):
    class MultiMoveAlgorithm:
        name = "multi_move"
        display_name = "Multi move"

        def run(self, device):
            first = device.move_lens(0, dx=2.0e-6)
            second = device.move_lens(1, dy=-2.0e-6)
            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=second if second.measurement_count >= first.measurement_count else first,
                move_history=device.move_history(),
            )

    fixed_delta_holder: dict[str, float] = {}

    def fixed_noise(amplitude: float) -> float:
        fixed_delta_holder["value"] = 0.5 * amplitude
        return fixed_delta_holder["value"]

    monkeypatch.setattr(alignment_lab_module, "get_algorithm", lambda _name: MultiMoveAlgorithm())

    app = _make_app()

    try:
        app.power_noise_enabled_var.set(True)
        app.power_noise_percent_var.set("10")
        monkeypatch.setattr(app, "next_power_noise", fixed_noise)

        app.run_alignment_algorithm("multi_move")
        move = app._last_algorithm_run.moves[0]  # pylint: disable=protected-access
        assert move.reading.noise_delta != 0.0
        assert np.isclose(move.reading.noise_delta, fixed_delta_holder["value"])

        app.show_alignment_algorithm(delay_ms=100000)
        app.update()

        suffix = app.power_noise_delta_label.cget("text")
        assert suffix
        assert suffix[0] in {"+", "−"}
        assert "mW" in suffix
        assert np.isclose(move.reading.noise_delta * 1e3, float(suffix.split()[0].replace("−", "-").replace("+", "")))
        expected_color = (
            POWER_NOISE_DELTA_ADDED_COLOR
            if move.reading.noise_delta > 0.0
            else POWER_NOISE_DELTA_SUBTRACTED_COLOR
        )
        assert app.power_noise_delta_label.cget("fg") == expected_color
    finally:
        app.destroy()


def test_show_playback_clears_noise_delta_suffix_when_noise_disabled(monkeypatch):
    class MultiMoveAlgorithm:
        name = "multi_move"
        display_name = "Multi move"

        def run(self, device):
            reading = device.move_lens(0, dx=2.0e-6)
            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=reading,
                move_history=device.move_history(),
            )

    monkeypatch.setattr(alignment_lab_module, "get_algorithm", lambda _name: MultiMoveAlgorithm())

    app = _make_app()

    try:
        app.power_noise_enabled_var.set(False)
        app.run_alignment_algorithm("multi_move")
        move = app._last_algorithm_run.moves[0]  # pylint: disable=protected-access
        assert move.reading.noise_delta == 0.0

        app.show_alignment_algorithm(delay_ms=100000)
        app.update()

        assert app.power_noise_delta_label.cget("text") == ""
    finally:
        app.destroy()


def test_show_playback_clears_noise_delta_suffix_after_complete(monkeypatch):
    class MultiMoveAlgorithm:
        name = "multi_move"
        display_name = "Multi move"

        def run(self, device):
            reading = device.move_lens(0, dx=2.0e-6)
            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=reading,
                move_history=device.move_history(),
            )

    monkeypatch.setattr(alignment_lab_module, "get_algorithm", lambda _name: MultiMoveAlgorithm())

    app = _make_app()

    try:
        app.power_noise_enabled_var.set(True)
        app.power_noise_percent_var.set("10")
        app._power_noise_rng.seed(321)  # pylint: disable=protected-access
        app.run_alignment_algorithm("multi_move")

        app.show_alignment_algorithm(delay_ms=0)
        for _index in range(10):
            app.update()
            if app._algorithm_animation_after_id is None:  # pylint: disable=protected-access
                break

        assert app._algorithm_animation_after_id is None  # pylint: disable=protected-access
        assert app.power_noise_delta_label.cget("text") == ""
    finally:
        app.destroy()


def test_show_playback_clears_noise_delta_suffix_on_cancel(monkeypatch):
    class MultiMoveAlgorithm:
        name = "multi_move"
        display_name = "Multi move"

        def run(self, device):
            first = device.move_lens(0, dx=2.0e-6)
            device.move_lens(1, dy=-2.0e-6)
            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=first,
                move_history=device.move_history(),
            )

    monkeypatch.setattr(alignment_lab_module, "get_algorithm", lambda _name: MultiMoveAlgorithm())

    app = _make_app()

    try:
        app.power_noise_enabled_var.set(True)
        app.power_noise_percent_var.set("10")
        app._power_noise_rng.seed(654)  # pylint: disable=protected-access
        app.run_alignment_algorithm("multi_move")

        app.show_alignment_algorithm(delay_ms=100000)
        app.update()
        assert app.power_noise_delta_label.cget("text") != ""

        app._cancel_algorithm_animation()  # pylint: disable=protected-access

        assert app.power_noise_delta_label.cget("text") == ""
    finally:
        app.destroy()


def test_alignment_lab_noise_controls_stay_editable_after_algorithm_run():
    app = _make_app()

    try:
        app.run_alignment_algorithm("fixed_z_j_matrix")

        assert app.power_noise_enabled_check.grid_info()
        assert app.power_noise_percent_entry.grid_info()
        assert int(app.power_noise_percent_entry.grid_info()["row"]) != int(
            app.algorithm_status_label.grid_info()["row"]
        )

        app.power_noise_enabled_var.set(True)
        app.power_noise_percent_var.set("7.5")
        app._update_noise_status()  # pylint: disable=protected-access

        assert app.power_noise_enabled()
        assert np.isclose(app.power_noise_fraction(), 0.075)
        assert app.power_noise_status_var.get() == "ON"
        assert "Reference-pose bootstrap" not in app.algorithm_status_var.get()
    finally:
        app.destroy()


def test_alignment_lab_noise_status_words_are_on_off():
    app = _make_app()

    try:
        assert app.power_noise_enabled_check["text"] == "Noise"
        assert app.power_noise_status_var.get() == "OFF"

        app.power_noise_enabled_var.set(True)
        app._update_noise_status()  # pylint: disable=protected-access

        assert app.power_noise_status_var.get() == "ON"
    finally:
        app.destroy()


def test_alignment_device_rejects_negative_power_noise_percent():
    app = _make_app()

    try:
        app.power_noise_enabled_var.set(True)
        app.power_noise_percent_var.set("-1")

        with pytest.raises(ValueError, match="Power noise"):
            app.create_alignment_device()
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
            assert public_methods == {
                "current_poses",
                "measure",
                "model_geometry",
                "move_history",
                "move_lens",
                "set_next_move_direction_method",
                "starting_poses",
            }

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


@pytest.mark.parametrize("algorithm_name", sorted(LAB_ALGORITHM_NAMES))
def test_alignment_lab_algorithms_capture_current_setup_position_as_run_start(algorithm_name):
    app = _make_app()

    try:
        app._apply_lens_poses(  # pylint: disable=protected-access
            tuple(
                (
                    pose[0] + (index + 1) * 0.3e-6,
                    pose[1] - (index + 1) * 0.2e-6,
                    pose[2] + (index - 0.5) * 0.4e-6,
                )
                for index, pose in enumerate(app.starting_poses())
            )
        )
        run_start = app.current_poses()

        app.run_alignment_algorithm(algorithm_name)

        assert app._last_algorithm_run is not None  # pylint: disable=protected-access
        _assert_pose_close(app._last_algorithm_run.initial_poses, run_start)  # pylint: disable=protected-access
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


def test_blind_power_j_uses_only_power_and_ball_coordinates():
    class NoModelDevice(SimulatedAlignmentDevice):
        def starting_poses(self):
            raise AssertionError("blind power-J must not use reference starting poses")

        def model_geometry(self):
            raise AssertionError("blind power-J must not use model geometry")

    device = NoModelDevice(DEFAULT_ALIGNMENT_SEED)
    before = device.measure()

    result = BlindPowerJAlgorithm(max_attempts=1).run(device)
    moves = result.move_history

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.9
    assert "power-only" in result.message
    assert all(np.isclose(move.dz, 0.0) for move in moves)
    assert any(np.isclose(abs(move.dx), BLIND_POWER_J_STEPS[0]) for move in moves)
    assert any(np.isclose(abs(move.dy), BLIND_POWER_J_STEPS[0]) for move in moves)


def test_blind_power_j_records_direction_selection_methods():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)

    result = BlindPowerJAlgorithm(max_attempts=1).run(device)
    tagged_methods = {move.direction_method for move in result.move_history if move.direction_method}

    assert tagged_methods
    assert tagged_methods <= {
        DIRECTION_METHOD_NEWTON,
        DIRECTION_METHOD_GRADIENT,
        DIRECTION_METHOD_BEST_OF_9,
    }


@pytest.mark.parametrize(
    ("algorithm_class", "expected_method"),
    (
        (BlindPowerJNewtonAlgorithm, DIRECTION_METHOD_NEWTON),
        (BlindPowerJGradientAlgorithm, DIRECTION_METHOD_GRADIENT),
        (BlindPowerJBestOf9Algorithm, DIRECTION_METHOD_BEST_OF_9),
    ),
)
def test_blind_power_j_method_algorithms_only_use_their_direction_method(algorithm_class, expected_method):
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)

    result = algorithm_class(max_attempts=1, steps=(BLIND_POWER_J_STEPS[0],)).run(device)
    tagged_methods = {move.direction_method for move in result.move_history if move.direction_method}

    assert tagged_methods
    assert tagged_methods == {expected_method}


def test_blind_power_j_first_move_starts_from_current_pose():
    device = SimulatedAlignmentDevice(DEFAULT_ALIGNMENT_SEED)
    current = list(device.current_poses())
    current[0] = (current[0][0] + 1.2e-6, current[0][1] - 0.4e-6, current[0][2] + 0.8e-6)
    current[1] = (current[1][0] - 0.7e-6, current[1][1] + 0.9e-6, current[1][2] - 0.6e-6)
    for ball, pose in zip(device.balls, current):
        ball.x_offset, ball.y_offset, ball.position = pose

    result = BlindPowerJAlgorithm(max_attempts=1, steps=(BLIND_POWER_J_STEPS[0],)).run(device)
    first_move = result.move_history[0]
    expected_after_first = (
        (current[0][0] + first_move.dx, current[0][1] + first_move.dy, current[0][2] + first_move.dz),
        current[1],
    )

    _assert_pose_close(first_move.poses, expected_after_first)


@pytest.mark.parametrize("seed", range(8))
def test_blind_power_j_reaches_mode_match_from_seeded_power_only_simulation(seed):
    device = SimulatedAlignmentDevice(seed)
    before = device.measure()

    result = BlindPowerJAlgorithm().run(device)

    assert result.final_reading.received_power > before.received_power
    assert result.final_reading.mode_efficiency > 0.9


def test_blind_power_j_runs_from_alignment_lab_and_experiences_noise(monkeypatch):
    app = _make_app()

    try:
        app._rescramble_alignment_errors()  # pylint: disable=protected-access
        app.power_noise_enabled_var.set(True)
        app.power_noise_percent_var.set("5")
        monkeypatch.setattr(app, "next_power_noise", lambda amplitude: 0.5 * amplitude)

        evaluation = app.run_alignment_algorithm("blind_power_j")
        algorithm_run = app._last_algorithm_run  # pylint: disable=protected-access

        assert algorithm_run is not None
        assert algorithm_run.algorithm.name == "blind_power_j"
        assert algorithm_run.algorithm.steps == BLIND_POWER_J_STEPS
        assert evaluation.mode_efficiency > 0.9
        assert any(
            not np.isclose(move.reading.received_power, _noiseless_power_at_pose(app, move.poses))
            for move in algorithm_run.moves
        )
        assert app.algorithm_direction_method_var.get().startswith("Blind direction:")
    finally:
        app.destroy()


def test_alignment_lab_passes_custom_blind_power_j_steps():
    app = _make_app()

    try:
        app.blind_power_j_steps_var.set("10, 3, 1")
        app._rescramble_alignment_errors()  # pylint: disable=protected-access

        app.run_alignment_algorithm("blind_power_j")
        algorithm_run = app._last_algorithm_run  # pylint: disable=protected-access

        assert algorithm_run is not None
        assert algorithm_run.algorithm.steps == (10e-6, 3e-6, 1e-6)
    finally:
        app.destroy()


def test_alignment_lab_passes_custom_parameters_to_separate_blind_methods():
    app = _make_app()

    try:
        algorithm_name = "blind_power_j_gradient"
        app.blind_power_j_step_vars[algorithm_name].set("8, 4")
        app.blind_power_j_attempt_vars[algorithm_name].set("2")
        app.blind_power_j_max_correction_vars[algorithm_name].set("12")
        app.blind_power_j_sample_vars[algorithm_name].set("3")

        algorithm = app._create_algorithm_instance(algorithm_name)  # pylint: disable=protected-access

        assert isinstance(algorithm, BlindPowerJGradientAlgorithm)
        assert algorithm.steps == (8e-6, 4e-6)
        assert algorithm.max_attempts == 2
        assert algorithm.max_correction == 12e-6
        assert algorithm.samples_per_point == 3
    finally:
        app.destroy()


def test_alignment_lab_rejects_invalid_blind_power_j_steps():
    app = _make_app()

    try:
        app.blind_power_j_steps_var.set("0, -1")

        with pytest.raises(ValueError, match="positive finite"):
            app._parse_blind_power_j_steps()  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_alignment_lab_rejects_invalid_blind_power_j_numeric_parameters():
    app = _make_app()

    try:
        app.blind_power_j_attempt_vars["blind_power_j_newton"].set("0")
        with pytest.raises(ValueError, match="positive integer"):
            app._parse_blind_power_j_parameters("blind_power_j_newton")  # pylint: disable=protected-access

        app.blind_power_j_attempt_vars["blind_power_j_newton"].set("1")
        app.blind_power_j_max_correction_vars["blind_power_j_newton"].set("nan")
        with pytest.raises(ValueError, match="positive finite"):
            app._parse_blind_power_j_parameters("blind_power_j_newton")  # pylint: disable=protected-access

        app.blind_power_j_max_correction_vars["blind_power_j_newton"].set("25")
        app.blind_power_j_sample_vars["blind_power_j_newton"].set("-2")
        with pytest.raises(ValueError, match="positive integer"):
            app._parse_blind_power_j_parameters("blind_power_j_newton")  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_blind_power_j_show_updates_direction_selection_label():
    app = _make_app()

    try:
        app._rescramble_alignment_errors()  # pylint: disable=protected-access
        app.run_alignment_algorithm("blind_power_j")
        algorithm_run = app._last_algorithm_run  # pylint: disable=protected-access
        tagged_moves = [move for move in algorithm_run.moves if move.direction_method]

        app.show_alignment_algorithm(delay_ms=0)
        for _index in range(200):
            app.update()
            if app._algorithm_animation_after_id is None:  # pylint: disable=protected-access
                break

        assert tagged_moves
        text, color = app._blind_direction_method_display(tagged_moves[-1].direction_method)  # pylint: disable=protected-access
        assert app.algorithm_direction_method_var.get() == text
        assert app.algorithm_direction_method_label.cget("fg") == color
    finally:
        app.destroy()


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

    for seed in range(10):
        device = SimulatedAlignmentDevice(seed, startup_out_of_beam=(seed % 10 == 0))
        result = PositionSolveAlgorithm().run(device)
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
        app.balls[0].x_offset = 0.0
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


def test_alignment_lab_draws_no_go_regions_on_y_plane():
    app = _make_app()

    try:
        app.update_idletasks()
        app.redraw()
        canvas_text = [
            app.y_canvas.itemcget(item_id, "text")
            for item_id in app.y_canvas.find_all()
            if app.y_canvas.type(item_id) == "text"
        ]

        assert any("Laser no-go" in text for text in canvas_text)
        assert any("Trench floor" in text for text in canvas_text)
        assert any("Taper no-go" in text for text in canvas_text)
        assert any("Vacuum tweezer B1" in text for text in canvas_text)
        assert any("Vacuum tweezer B2" in text for text in canvas_text)

        _left, _right, top, _bottom = app._plot_pixel_bounds(app.y_canvas)  # pylint: disable=protected-access
        vacuum_rectangles = [
            item_id
            for item_id in app.y_canvas.find_withtag("vacuum_tweezer_1")
            if app.y_canvas.type(item_id) == "rectangle"
        ]
        assert vacuum_rectangles
        bbox = app.y_canvas.bbox(vacuum_rectangles[0])
        assert bbox is not None
        assert abs(bbox[1] - top) <= 1.0
    finally:
        app.destroy()


def test_alignment_lab_draws_vacuum_tweezers_as_local_x_plane_squares():
    app = _make_app()

    try:
        app.update_idletasks()
        app.redraw()
        _left, _right, top, bottom = app._plot_pixel_bounds(app.canvas)  # pylint: disable=protected-access
        plot_height = bottom - top
        vacuum_rectangles = [
            item_id
            for item_id in app.canvas.find_withtag("vacuum_tweezer_1")
            if app.canvas.type(item_id) == "rectangle"
        ]

        assert vacuum_rectangles
        bbox = app.canvas.bbox(vacuum_rectangles[0])
        assert bbox is not None
        rect_height = bbox[3] - bbox[1]
        assert rect_height < 0.75 * plot_height
    finally:
        app.destroy()


def test_alignment_lab_draws_fixed_no_go_regions_across_full_x_plane():
    app = _make_app()

    try:
        app.update_idletasks()
        app.redraw()
        _left, _right, top, bottom = app._plot_pixel_bounds(app.canvas)  # pylint: disable=protected-access
        plot_height = bottom - top

        for zone_name in ("laser_side_no_go", "trench_floor", "taper_side_no_go"):
            rectangles = [
                item_id
                for item_id in app.canvas.find_withtag(zone_name)
                if app.canvas.type(item_id) == "rectangle"
            ]
            assert rectangles
            bbox = app.canvas.bbox(rectangles[0])
            assert bbox is not None
            rect_height = bbox[3] - bbox[1]
            assert rect_height >= 0.95 * plot_height
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


def test_alignment_lab_y_plane_drag_allows_ball_into_negative_z_no_go_region():
    app = _make_app()

    try:
        app.update_idletasks()
        app.redraw()
        ball = app.balls[0]
        target_z = -100e-6
        target_y = ball.y_offset

        assert app._z_min < target_z < 0.0  # pylint: disable=protected-access
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


