"""Tests for the interactive setup simulation wiring."""

import math
import tkinter as tk

import numpy as np
import pytest

from interactive_setup import (
    BallLensElement,
    L808H1_X_DIVERGENCE_FWHM,
    L808H1_Y_DIVERGENCE_FWHM,
    L808H1_WAVELENGTH,
    L808H1_X_WAIST,
    L808H1_Y_WAIST,
    FiberElement,
    LaserSource,
    LensElement,
    OpticalLayoutEditor,
    SAPPHIRE_REFRACTIVE_INDEX,
    TaperDetectorElement,
    _one_over_e2_half_angle_from_fwhm,
    ball_lens_matrix,
    default_ball_lens_layout,
    fiber_to_spec,
    format_simulation_report,
    lens_to_spec,
    propagate_astigmatic_through_balls,
    simulate_source_to_taper,
    simulate_layout,
    simulate_source_to_fiber,
    source_to_alignment,
    source_to_beam,
    square_detector_collection_fraction,
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


def test_default_source_uses_l808h1_astigmatic_waists():
    source = LaserSource()
    fiber = FiberElement(position=0.0, mode_field_diameter=20e-6)

    results = simulate_layout([source], [], [fiber], final_z=0.01)
    report = format_simulation_report(results)

    expected_x_theta = _one_over_e2_half_angle_from_fwhm(L808H1_X_DIVERGENCE_FWHM)
    expected_y_theta = _one_over_e2_half_angle_from_fwhm(L808H1_Y_DIVERGENCE_FWHM)
    assert np.isclose(source.wavelength, L808H1_WAVELENGTH)
    assert np.isclose(source.waist_radius, L808H1_WAVELENGTH / (math.pi * expected_x_theta))
    assert np.isclose(source.waist_radius_y, L808H1_WAVELENGTH / (math.pi * expected_y_theta))
    assert np.isclose(source.rayleigh_range, math.pi * source.waist_radius**2 / source.wavelength)
    assert np.isclose(source.rayleigh_range_y, math.pi * source.waist_radius_y**2 / source.wavelength)
    assert "x/y waist radius:" in report
    assert "x/y Rayleigh length:" in report


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
    assert not np.isclose(state.q_x, before_q + (700e-6 - ball.entry_z))


def test_astigmatic_beam_missing_ball_skips_lens_and_reports_miss():
    source = LaserSource(x_offset=300e-6)
    ball = BallLensElement(position=300e-6, diameter=500e-6)
    taper = TaperDetectorElement(position=800e-6)

    result = simulate_source_to_taper(source, [ball], taper)

    assert result.ball_reports[0].status == "MISS"
    assert result.aperture_transmission == 0.0
    assert result.detector_fraction == 0.0
    assert result.received_power == 0.0


def test_astigmatic_beam_near_ball_edge_reports_clipping():
    source = LaserSource(x_offset=240e-6)
    ball = BallLensElement(position=300e-6, diameter=500e-6)

    _state, reports, missed, _path = propagate_astigmatic_through_balls(source, [ball], target_z=800e-6)

    assert not missed
    assert reports[0].status == "CLIPPING"
    assert reports[0].transmission < 1.0


def test_square_taper_collection_decreases_with_offset():
    detector = TaperDetectorElement(width=200e-9, height=200e-9)
    centered = square_detector_collection_fraction(
        beam_x=0.0,
        beam_y=0.0,
        beam_radius_x=2e-6,
        beam_radius_y=1e-6,
        detector=detector,
    )
    offset_detector = TaperDetectorElement(width=200e-9, height=200e-9, x_offset=500e-9)
    offset = square_detector_collection_fraction(
        beam_x=0.0,
        beam_y=0.0,
        beam_radius_x=2e-6,
        beam_radius_y=1e-6,
        detector=offset_detector,
    )

    assert centered > offset
    assert centered > 0.0


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


def test_tk_app_simulate_updates_fiber_received_power():
    try:
        app = OpticalLayoutEditor()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")

    try:
        app._simulate()  # pylint: disable=protected-access
        assert len(app.balls) == 2
        assert len(app.tapers) == 1
        assert app.tapers[0].width == 200e-9
        assert app.tapers[0].received_power > 0.0
        assert app._beam_paths  # pylint: disable=protected-access
        path = app._beam_paths[0]  # pylint: disable=protected-access
        assert len(path.z) == len(path.x) == len(path.w)
        assert path.waist_radius == min(path.w)
        assert path.waist_radius > 0.0
        output = app.output.get("1.0", tk.END)
        assert "COUPLING SUMMARY" in output
        assert "Ball lens checks" in output
        assert "Taper detector collection" in output
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
