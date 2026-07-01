"""Tests for the interactive setup simulation wiring."""

import math
import random
import tkinter as tk
from types import SimpleNamespace

import numpy as np
import pytest

from interactive_setup import (
    BallLensElement,
    DEFAULT_BALL1_FRONT_GAP,
    DEFAULT_BALL2_TAPER_GAP,
    DEFAULT_BALL_DIAMETER,
    DEFAULT_BALL_GAP,
    DEFAULT_MODE_RADIUS_X,
    DEFAULT_MODE_RADIUS_Y,
    DEFAULT_SOURCE_POWER,
    DEFAULT_TAPER_EXTRA_TRANSMISSION,
    DEFAULT_TAPER_FACET_REFRACTIVE_INDEX,
    DEFAULT_TAPER_PHYSICAL_THICKNESS,
    DEFAULT_TAPER_PHYSICAL_WIDTH,
    DEFAULT_WAVELENGTH,
    FiberElement,
    LaserSource,
    LensElement,
    NominalElementState,
    OpticalLayoutEditor,
    SAPPHIRE_REFRACTIVE_INDEX,
    AXIAL_TOLERANCE,
    TRANSVERSE_TOLERANCE,
    TaperDetectorElement,
    apply_nominal_state,
    ball_lens_matrix,
    capture_element_nominal,
    default_ball_lens_layout,
    elliptical_gaussian_mode_overlap_efficiency,
    fiber_to_spec,
    format_simulation_report,
    fresnel_power_transmission,
    fresnel_reflection_loss,
    lens_to_spec,
    propagate_astigmatic_through_balls,
    random_positive,
    scramble_element_positive,
    simulate_source_to_taper,
    simulate_layout,
    simulate_source_to_fiber,
    source_to_alignment,
    source_to_beam,
)
from layout import analyze_fiber_coupling, beam_center_at_z, gaussian_mode_overlap_efficiency


def test_interactive_elements_convert_to_physics_specs():
    source = LaserSource(
        position=0.01,
        wavelength=1064e-9,
        waist_radius=0.7e-3,
        waist_position=-0.02,
        power=2.5e-3,
        x_offset=0.1e-3,
        y_offset=-0.2e-3,
        x_angle=1.0e-3,
        y_angle=-2.0e-3,
    )
    lens = LensElement(position=0.05, focal_length=0.025, aperture_radius=3e-3, x_offset=0.4e-3)
    fiber = FiberElement(position=0.08, mode_field_diameter=10.4e-6, x_offset=1e-6)

    beam = source_to_beam(source)
    alignment = source_to_alignment(source)
    lens_spec = lens_to_spec(lens)
    fiber_spec = fiber_to_spec(fiber)

    assert np.isclose(beam.wavelength, source.wavelength)
    assert np.isclose(beam.w0, source.waist_radius)
    assert np.isclose(beam.z0, source.waist_position)
    assert np.isclose(alignment.x_angle, source.x_angle)
    assert np.isclose(lens_spec.aperture_radius, lens.aperture_radius)
    assert np.isclose(fiber_spec.mode_field_diameter, fiber.mode_field_diameter)


def test_default_source_uses_corrected_elliptical_gaussian_waist():
    source = LaserSource()
    fiber = FiberElement(position=0.0, mode_field_diameter=20e-6)

    results = simulate_layout([source], [], [fiber], final_z=0.01)
    report = format_simulation_report(results)

    assert np.isclose(source.wavelength, DEFAULT_WAVELENGTH)
    assert np.isclose(source.waist_radius, DEFAULT_MODE_RADIUS_X)
    assert np.isclose(source.waist_radius_y, DEFAULT_MODE_RADIUS_Y)
    assert np.isclose(source.power, DEFAULT_SOURCE_POWER)
    assert np.isclose(source.rayleigh_range, math.pi * source.waist_radius**2 / source.wavelength)
    assert np.isclose(source.rayleigh_range_y, math.pi * source.waist_radius_y**2 / source.wavelength)
    assert "x/y waist radius:" in report
    assert "x/y Rayleigh length:" in report


def test_default_two_ball_taper_geometry_matches_corrected_model():
    balls, tapers, final_z = default_ball_lens_layout()
    radius = 0.5 * DEFAULT_BALL_DIAMETER

    assert len(balls) == 2
    assert len(tapers) == 1
    assert np.isclose(balls[0].diameter, DEFAULT_BALL_DIAMETER)
    assert np.isclose(balls[1].diameter, DEFAULT_BALL_DIAMETER)
    assert np.isclose(balls[0].entry_z, DEFAULT_BALL1_FRONT_GAP)
    assert np.isclose(balls[0].exit_z, DEFAULT_BALL1_FRONT_GAP + DEFAULT_BALL_DIAMETER)
    assert np.isclose(balls[1].entry_z - balls[0].exit_z, DEFAULT_BALL_GAP)
    assert np.isclose(tapers[0].position - balls[1].exit_z, DEFAULT_BALL2_TAPER_GAP)
    assert np.isclose(balls[1].position - balls[0].position, 700e-6)
    assert np.isclose(balls[0].position, DEFAULT_BALL1_FRONT_GAP + radius)
    assert np.isclose(final_z, 1278e-6)
    assert np.isclose(tapers[0].width, DEFAULT_TAPER_PHYSICAL_WIDTH)
    assert np.isclose(tapers[0].height, DEFAULT_TAPER_PHYSICAL_THICKNESS)
    assert np.isclose(tapers[0].mode_radius_x, DEFAULT_MODE_RADIUS_X)
    assert np.isclose(tapers[0].mode_radius_y, DEFAULT_MODE_RADIUS_Y)
    assert np.isclose(tapers[0].extra_transmission, DEFAULT_TAPER_EXTRA_TRANSMISSION)
    assert np.isclose(tapers[0].facet_refractive_index, DEFAULT_TAPER_FACET_REFRACTIVE_INDEX)


def test_fresnel_helpers_return_normal_incidence_power_loss_and_transmission():
    expected_sapphire_loss = ((1.0 - SAPPHIRE_REFRACTIVE_INDEX) / (1.0 + SAPPHIRE_REFRACTIVE_INDEX)) ** 2
    expected_sin_loss = ((1.0 - DEFAULT_TAPER_FACET_REFRACTIVE_INDEX) / (1.0 + DEFAULT_TAPER_FACET_REFRACTIVE_INDEX)) ** 2

    assert np.isclose(fresnel_reflection_loss(1.0, SAPPHIRE_REFRACTIVE_INDEX), expected_sapphire_loss)
    assert np.isclose(fresnel_power_transmission(1.0, SAPPHIRE_REFRACTIVE_INDEX), 1.0 - expected_sapphire_loss)
    assert np.isclose(fresnel_reflection_loss(1.0, DEFAULT_TAPER_FACET_REFRACTIVE_INDEX), expected_sin_loss)
    assert np.isclose(fresnel_power_transmission(1.0, DEFAULT_TAPER_FACET_REFRACTIVE_INDEX), 1.0 - expected_sin_loss)


def test_source_rayleigh_length_can_define_waist_radius():
    wavelength = 1550e-9
    rayleigh_range = 500e-6
    source = LaserSource(wavelength=wavelength, rayleigh_range=rayleigh_range)
    beam = source_to_beam(source)

    expected_waist = math.sqrt(wavelength * rayleigh_range / math.pi)

    assert np.isclose(source.waist_radius, expected_waist)
    assert np.isclose(beam.w0, expected_waist)
    assert np.isclose(beam.rayleigh_range(), rayleigh_range)


def test_sapphire_ball_lens_matrix_uses_thick_sphere_formula():
    ball = BallLensElement(diameter=500e-6, refractive_index=SAPPHIRE_REFRACTIVE_INDEX)

    matrix = ball_lens_matrix(ball)
    expected_a = (2.0 - ball.refractive_index) / ball.refractive_index
    expected_b = ball.diameter / ball.refractive_index
    expected_c = -4.0 * (ball.refractive_index - 1.0) / (ball.diameter * ball.refractive_index)

    assert np.isclose(matrix[0][0], expected_a)
    assert np.isclose(matrix[0][1], expected_b)
    assert np.isclose(matrix[1][0], expected_c)
    assert np.isclose(matrix[1][1], expected_a)
    assert np.isclose(ball.effective_focal_length, ball.refractive_index * ball.diameter / (4.0 * (ball.refractive_index - 1.0)))


def test_centered_astigmatic_beam_hits_ball_and_changes_q():
    source = LaserSource()
    ball = BallLensElement(position=300e-6)

    before_q = source_q = source_to_beam(source).q(ball.entry_z)
    state, reports, missed, _path = propagate_astigmatic_through_balls(source, [ball], target_z=700e-6)

    assert not missed
    assert reports[0].status in {"OK", "CLIPPING"}
    expected_surface = fresnel_power_transmission(1.0, ball.refractive_index)
    assert np.isclose(reports[0].reflection_transmission, expected_surface**2)
    assert np.isclose(reports[0].transmission, reports[0].aperture_transmission * reports[0].reflection_transmission)
    assert not np.isclose(state.q_x, before_q + (700e-6 - ball.entry_z))


def test_astigmatic_beam_missing_ball_skips_lens_and_reports_miss():
    source = LaserSource(x_offset=300e-6)
    ball = BallLensElement(position=300e-6, diameter=500e-6)
    taper = TaperDetectorElement(position=800e-6)

    result = simulate_source_to_taper(source, [ball], taper)

    assert result.ball_reports[0].status == "MISS"
    assert result.aperture_transmission == 1.0
    assert result.ball_reflection_transmission == 1.0
    assert result.ball_reports[0].reflection_transmission == 1.0
    assert result.mode_efficiency >= 0.0
    assert np.isclose(
        result.received_power,
        source.power * result.mode_efficiency * result.extra_transmission * result.taper_reflection_transmission,
    )


def test_astigmatic_beam_near_ball_edge_reports_clipping():
    source = LaserSource(x_offset=240e-6)
    ball = BallLensElement(position=300e-6, diameter=500e-6)

    _state, reports, missed, _path = propagate_astigmatic_through_balls(source, [ball], target_z=800e-6)

    assert not missed
    assert reports[0].status == "CLIPPING"
    assert reports[0].transmission < 1.0
    assert np.isclose(reports[0].transmission, reports[0].aperture_transmission * reports[0].reflection_transmission)


def test_elliptical_taper_mode_overlap_decreases_with_offset():
    centered = elliptical_gaussian_mode_overlap_efficiency(
        beam_radius_x=2e-6,
        beam_radius_y=1e-6,
        mode_radius_x=2e-6,
        mode_radius_y=1e-6,
        wavelength=808e-9,
    )
    offset = elliptical_gaussian_mode_overlap_efficiency(
        beam_radius_x=2e-6,
        beam_radius_y=1e-6,
        mode_radius_x=2e-6,
        mode_radius_y=1e-6,
        wavelength=808e-9,
        x_offset=500e-9,
    )
    expected_offset = math.exp(-(500e-9 / 2e-6) ** 2)

    assert np.isclose(centered, 1.0)
    assert np.isclose(offset, expected_offset)
    assert centered > offset


def test_elliptical_taper_angle_overlap_matches_gaussian_formula():
    wavelength = 808e-9
    mode_radius_x = 2.18e-6
    mode_radius_y = 0.965e-6
    angle = 0.03

    efficiency = elliptical_gaussian_mode_overlap_efficiency(
        beam_radius_x=mode_radius_x,
        beam_radius_y=mode_radius_y,
        mode_radius_x=mode_radius_x,
        mode_radius_y=mode_radius_y,
        wavelength=wavelength,
        x_angle=angle,
    )
    expected = math.exp(-((math.pi * mode_radius_x * angle / wavelength) ** 2))

    assert np.isclose(efficiency, expected)


def test_matched_lateral_offset_matches_gaussian_formula():
    mode_radius = 5.2e-6
    offset = 1.3e-6

    efficiency = gaussian_mode_overlap_efficiency(
        beam_radius=mode_radius,
        fiber_mode_radius=mode_radius,
        wavelength=1550e-9,
        x_offset=offset,
    )

    assert np.isclose(efficiency, math.exp(-(offset / mode_radius) ** 2))


def test_matched_angular_offset_matches_gaussian_formula_with_index():
    mode_radius = 5.2e-6
    wavelength = 1550e-9
    angle = 3.0e-3
    refractive_index = 1.2

    efficiency = gaussian_mode_overlap_efficiency(
        beam_radius=mode_radius,
        fiber_mode_radius=mode_radius,
        wavelength=wavelength,
        x_angle=angle,
        refractive_index=refractive_index,
    )
    expected = math.exp(-((math.pi * refractive_index * mode_radius * angle / wavelength) ** 2))

    assert np.isclose(efficiency, expected)


def test_decentered_lens_changes_angle_but_not_q_size():
    source = LaserSource(wavelength=1550e-9, waist_radius=1.0e-3)
    centered_lens = LensElement(position=0.05, focal_length=0.05, aperture_radius=10e-3)
    decentered_lens = LensElement(position=0.05, focal_length=0.05, aperture_radius=10e-3, x_offset=1e-3)
    fiber = FiberElement(position=0.08, mode_field_diameter=10.4e-6)

    centered = analyze_fiber_coupling(
        source_to_beam(source),
        [lens_to_spec(centered_lens)],
        fiber_to_spec(fiber),
        laser=source_to_alignment(source),
    )
    decentered = analyze_fiber_coupling(
        source_to_beam(source),
        [lens_to_spec(decentered_lens)],
        fiber_to_spec(fiber),
        laser=source_to_alignment(source),
    )
    decentered_center = beam_center_at_z(
        [lens_to_spec(decentered_lens)],
        z=fiber.position,
        laser=source_to_alignment(source),
    )

    assert np.isclose(centered.beam_radius, decentered.beam_radius)
    assert np.isclose(centered.beam_R, decentered.beam_R)
    assert not np.isclose(decentered_center.x_angle, 0.0)


def test_received_power_uses_source_power_aperture_and_coupling():
    source = LaserSource(wavelength=1550e-9, waist_radius=5.2e-6, power=2.0e-3)
    lens = LensElement(position=0.0, focal_length=1.0e6, aperture_radius=4.0e-6)
    fiber = FiberElement(position=0.0, mode_field_diameter=10.4e-6)

    result = simulate_source_to_fiber(source, [lens], fiber)

    assert result.coupling_report is not None
    expected = source.power * result.aperture_transmission * result.coupling_report.total_efficiency
    assert np.isclose(result.received_power, expected)
    assert result.aperture_transmission < 1.0


def test_taper_received_power_uses_mode_overlap_and_extra_transmission():
    source = LaserSource(
        wavelength=DEFAULT_WAVELENGTH,
        waist_radius=DEFAULT_MODE_RADIUS_X,
        waist_radius_y=DEFAULT_MODE_RADIUS_Y,
        power=1.0,
    )
    taper = TaperDetectorElement(position=0.0)

    result = simulate_source_to_taper(source, [], taper)

    assert np.isclose(result.mode_efficiency, 1.0)
    assert np.isclose(result.extra_transmission, DEFAULT_TAPER_EXTRA_TRANSMISSION)
    assert np.isclose(result.taper_reflection_transmission, fresnel_power_transmission(1.0, taper.facet_refractive_index))
    expected = DEFAULT_TAPER_EXTRA_TRANSMISSION * result.taper_reflection_transmission
    assert np.isclose(result.received_power, expected)


def test_taper_facet_index_one_removes_taper_reflection_loss():
    source = LaserSource(
        wavelength=DEFAULT_WAVELENGTH,
        waist_radius=DEFAULT_MODE_RADIUS_X,
        waist_radius_y=DEFAULT_MODE_RADIUS_Y,
        power=1.0,
    )
    taper = TaperDetectorElement(position=0.0, facet_refractive_index=1.0)

    result = simulate_source_to_taper(source, [], taper)

    assert np.isclose(result.taper_reflection_transmission, 1.0)
    assert np.isclose(result.received_power, result.mode_efficiency * result.extra_transmission)


def test_simulation_report_contains_source_lens_and_fiber_results():
    source = LaserSource(wavelength=1550e-9, waist_radius=5.2e-6, power=1.0e-3)
    fiber = FiberElement(position=0.0, mode_field_diameter=10.4e-6)

    results = simulate_layout([source], [], [fiber], final_z=0.01)
    report = format_simulation_report(results)

    assert "COUPLING SUMMARY" in report
    assert "COUPLING =" in report
    assert "Source 1" in report
    assert "Lens aperture checks" in report
    assert "Fiber coupling" in report
    assert results[0].fiber_results[0].received_power > 0.0


def test_random_positive_stays_in_one_sided_range():
    rng = random.Random(0)
    samples = [random_positive(500e-6, rng) for _ in range(200)]
    assert all(0.0 <= sample <= 500e-6 for sample in samples)
    assert any(sample > 0.0 for sample in samples)


def test_scramble_element_positive_uses_nominal_and_positive_offsets():
    ball = BallLensElement(position=0.001, x_offset=0.0, y_offset=0.0)
    nominal = capture_element_nominal(ball)
    rng = random.Random(1)
    scramble_element_positive(ball, nominal, rng=rng)
    assert 0.001 <= ball.position <= 0.001 + AXIAL_TOLERANCE
    assert 0.0 <= ball.x_offset <= TRANSVERSE_TOLERANCE
    assert 0.0 <= ball.y_offset <= TRANSVERSE_TOLERANCE


def test_scramble_laser_source_keeps_nominal_z_and_moves_xy_only():
    source = LaserSource(position=0.0, x_offset=0.0, y_offset=0.0)
    nominal = capture_element_nominal(source)
    rng = random.Random(2)
    scramble_element_positive(source, nominal, rng=rng)
    assert source.position == nominal.position
    assert 0.0 <= source.x_offset <= TRANSVERSE_TOLERANCE
    assert 0.0 <= source.y_offset <= TRANSVERSE_TOLERANCE


def test_apply_nominal_state_restores_aligned_layout():
    source = LaserSource(x_offset=20e-6, y_offset=30e-6, x_angle=1e-3, y_angle=2e-3)
    nominal = NominalElementState(position=0.0, x_offset=0.0, y_offset=0.0, x_angle=0.0, y_angle=0.0)
    apply_nominal_state(source, nominal)
    assert source.x_offset == 0.0
    assert source.y_offset == 0.0
    assert source.x_angle == 0.0
    assert source.y_angle == 0.0


def test_tk_app_align_and_scramble_buttons():
    try:
        app = OpticalLayoutEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")

    try:
        nominal_ball_z = app.balls[0].position
        app.balls[0].x_offset = 25e-6
        app.balls[0].position = nominal_ball_z + 100e-6
        app._align_all()  # pylint: disable=protected-access
        assert app.balls[0].x_offset == 0.0
        assert np.isclose(app.balls[0].position, nominal_ball_z)

        app._scramble_full()  # pylint: disable=protected-access
        assert app.balls[0].position >= nominal_ball_z
        assert app.balls[0].position <= nominal_ball_z + AXIAL_TOLERANCE
        assert 0.0 <= app.balls[0].x_offset <= TRANSVERSE_TOLERANCE
        assert 0.0 <= app.balls[0].y_offset <= TRANSVERSE_TOLERANCE
        assert np.isclose(app.sources[0].position, 0.0)
        assert app.sources[0].x_offset >= 0.0
        assert app.sources[0].x_offset <= TRANSVERSE_TOLERANCE
    finally:
        app.destroy()


def test_tk_app_simulate_updates_fiber_received_power():
    try:
        app = OpticalLayoutEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")

    try:
        app._simulate()  # pylint: disable=protected-access
        assert len(app.balls) == 2
        assert len(app.tapers) == 1
        assert np.isclose(app.tapers[0].width, DEFAULT_TAPER_PHYSICAL_WIDTH)
        assert np.isclose(app.tapers[0].height, DEFAULT_TAPER_PHYSICAL_THICKNESS)
        assert np.isclose(app.tapers[0].mode_radius_x, DEFAULT_MODE_RADIUS_X)
        assert np.isclose(app.tapers[0].mode_radius_y, DEFAULT_MODE_RADIUS_Y)
        assert np.isclose(app.tapers[0].extra_transmission, DEFAULT_TAPER_EXTRA_TRANSMISSION)
        assert np.isclose(app.tapers[0].facet_refractive_index, DEFAULT_TAPER_FACET_REFRACTIVE_INDEX)
        assert app.tapers[0].received_power > 0.0
        assert app.tapers[0].received_power < app.sources[0].power * DEFAULT_TAPER_EXTRA_TRANSMISSION
        assert app._beam_paths  # pylint: disable=protected-access
        path = app._beam_paths[0]  # pylint: disable=protected-access
        assert len(path.z) == len(path.x) == len(path.w)
        assert path.waist_radius == min(path.w)
        assert path.waist_radius > 0.0
        output = app.output.get("1.0", tk.END)
        assert "COUPLING SUMMARY" in output
        assert "Ball lens checks" in output
        assert "Taper Gaussian mode matching" in output
        assert "refl(%)" in output
        assert "ball refl(%)" in output
        canvas_text = "\n".join(
            app.canvas.itemcget(item_id, "text")
            for item_id in app.canvas.find_all()
            if app.canvas.type(item_id) == "text"
        )
        assert "optical mode 1.24 x 2.9 um" in canvas_text
    finally:
        app.destroy()


def test_tk_app_zoom_changes_visible_range():
    try:
        app = OpticalLayoutEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")

    try:
        app.redraw()
        initial_range = app._z_max - app._z_min  # pylint: disable=protected-access
        app._zoom_in()  # pylint: disable=protected-access
        zoomed_range = app._z_max - app._z_min  # pylint: disable=protected-access
        assert app._view_zoom > 1.0  # pylint: disable=protected-access
        assert zoomed_range < initial_range
        app._reset_view()  # pylint: disable=protected-access
        assert np.isclose(app._view_zoom, 1.0)  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_tk_app_undo_reverts_add_final_z_editor_save_drag_delete_and_reset(monkeypatch):
    try:
        app = OpticalLayoutEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")

    class DummyDialog:
        def __init__(self):
            self.destroyed = False

        def destroy(self):
            self.destroyed = True

    try:
        initial_ball_count = len(app.balls)
        app._add_ball()  # pylint: disable=protected-access
        assert len(app.balls) == initial_ball_count + 1
        app._undo()  # pylint: disable=protected-access
        assert len(app.balls) == initial_ball_count

        original_final_z = app.final_z
        app.final_z_var.set("2000")
        app._apply_final_z()  # pylint: disable=protected-access
        assert np.isclose(app.final_z, 2000e-6)
        app._undo()  # pylint: disable=protected-access
        assert np.isclose(app.final_z, original_final_z)
        assert app.final_z_var.get() == f"{original_final_z * 1e6:.2f}"

        source = app.sources[0]
        fields = app._field_specs_for(source)  # pylint: disable=protected-access
        variables = {}
        for spec in fields:
            raw_value = getattr(source, spec.attr)
            display_value = str(raw_value) if spec.is_text else f"{raw_value * spec.scale:.9g}"
            variables[spec.attr] = tk.StringVar(value=display_value)
        variables["power"].set("123")
        dialog = DummyDialog()
        app._save_editor(dialog, source, fields, variables)  # pylint: disable=protected-access
        assert dialog.destroyed
        assert np.isclose(app.sources[0].power, 123e-3)
        app._undo()  # pylint: disable=protected-access
        assert np.isclose(app.sources[0].power, DEFAULT_SOURCE_POWER)

        app.update_idletasks()
        app.redraw()
        ball = app.balls[0]
        old_position = ball.position
        old_x = ball.x_offset
        undo_count = len(app._undo_stack)  # pylint: disable=protected-access
        app._drag = {"uid": ball.uid, "mode": "move", "undo_pushed": False}  # pylint: disable=protected-access
        app._on_canvas_drag(  # pylint: disable=protected-access
            SimpleNamespace(
                x=app._z_to_px(old_position + 10e-6),  # pylint: disable=protected-access
                y=app._x_to_px(old_x + 5e-6),  # pylint: disable=protected-access
            )
        )
        app._on_canvas_drag(  # pylint: disable=protected-access
            SimpleNamespace(
                x=app._z_to_px(old_position + 20e-6),  # pylint: disable=protected-access
                y=app._x_to_px(old_x + 10e-6),  # pylint: disable=protected-access
            )
        )
        app._on_canvas_release(None)  # pylint: disable=protected-access
        assert len(app._undo_stack) == undo_count + 1  # pylint: disable=protected-access
        assert not np.isclose(app.balls[0].position, old_position)
        app._undo()  # pylint: disable=protected-access
        restored_ball = app._element_by_uid(ball.uid)  # pylint: disable=protected-access
        assert np.isclose(restored_ball.position, old_position)
        assert np.isclose(restored_ball.x_offset, old_x)

        monkeypatch.setattr("interactive_setup.messagebox.askyesno", lambda *args, **kwargs: True)
        deleted_uid = app.balls[0].uid
        assert app._delete_element(app.balls[0])  # pylint: disable=protected-access
        assert app._element_by_uid(deleted_uid) is None  # pylint: disable=protected-access
        app._undo()  # pylint: disable=protected-access
        assert app._element_by_uid(deleted_uid) is not None  # pylint: disable=protected-access

        app._add_ball()  # pylint: disable=protected-access
        edited_ball_count = len(app.balls)
        app._reset_defaults()  # pylint: disable=protected-access
        assert len(app.balls) == 2
        app._undo()  # pylint: disable=protected-access
        assert len(app.balls) == edited_ball_count
    finally:
        app.destroy()


def test_tk_app_zoom_anchors_on_last_canvas_click():
    try:
        app = OpticalLayoutEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")

    try:
        app.update_idletasks()
        app.redraw()
        left, right, top, bottom = app._plot_pixel_bounds()  # pylint: disable=protected-access
        click_x = left + 0.60 * (right - left)
        click_y = top + 0.40 * (bottom - top)
        app._store_zoom_anchor_from_canvas(click_x, click_y)  # pylint: disable=protected-access
        anchor_z = app._zoom_anchor_z  # pylint: disable=protected-access
        anchor_x = app._zoom_anchor_x  # pylint: disable=protected-access

        app._zoom_in()  # pylint: disable=protected-access

        assert np.isclose(app._z_to_px(anchor_z), click_x)  # pylint: disable=protected-access
        assert np.isclose(app._x_to_px(anchor_x), click_y)  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_tk_app_empty_canvas_drag_pans_zoomed_view():
    try:
        app = OpticalLayoutEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")

    try:
        app.update_idletasks()
        app.redraw()
        app._zoom_in()  # pylint: disable=protected-access
        z_min_before = app._z_min  # pylint: disable=protected-access
        x_min_before = app._x_min  # pylint: disable=protected-access
        z_range_before = app._z_max - app._z_min  # pylint: disable=protected-access
        x_range_before = app._x_max - app._x_min  # pylint: disable=protected-access

        app._pan_view_by_pixels(40.0, 30.0)  # pylint: disable=protected-access
        app.redraw()

        assert app._z_min < z_min_before  # pylint: disable=protected-access
        assert app._x_min > x_min_before  # pylint: disable=protected-access
        assert np.isclose(app._z_max - app._z_min, z_range_before)  # pylint: disable=protected-access
        assert np.isclose(app._x_max - app._x_min, x_range_before)  # pylint: disable=protected-access
    finally:
        app.destroy()


def test_tk_app_view_bounds_refit_only_after_apply():
    try:
        app = OpticalLayoutEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")

    try:
        app.redraw()
        initial_x_range = app._x_max - app._x_min  # pylint: disable=protected-access
        initial_z_range = app._z_max - app._z_min  # pylint: disable=protected-access

        app.balls[0].diameter = 0.05
        app.balls[0].position = app.final_z * 1.5
        app.redraw()

        assert np.isclose(app._x_max - app._x_min, initial_x_range)  # pylint: disable=protected-access
        assert np.isclose(app._z_max - app._z_min, initial_z_range)  # pylint: disable=protected-access

        app._apply_final_z()  # pylint: disable=protected-access

        assert app._x_max - app._x_min > initial_x_range  # pylint: disable=protected-access
        assert app._z_max - app._z_min > initial_z_range  # pylint: disable=protected-access
    finally:
        app.destroy()
