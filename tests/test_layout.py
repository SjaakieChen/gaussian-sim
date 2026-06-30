"""Tests for positioned lens layouts and aperture estimates."""

import numpy as np
import pytest

from beam import GaussianBeam
from elements import FreeSpace, ThinLens
from layout import (
    FiberSpec,
    LaserAlignment,
    LensSpec,
    analyze_lens_apertures,
    analyze_fiber_coupling,
    beam_center_at_z,
    build_elements_from_lenses,
    gaussian_aperture_transmission,
    gaussian_mode_overlap_efficiency,
    mode_overlap_efficiency,
    sample_beam_centroid,
)


def _numeric_gaussian_overlap(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    beam_radius,
    fiber_mode_radius,
    wavelength,
    beam_R=np.inf,
    fiber_R=np.inf,
    x_offset=0.0,
    y_offset=0.0,
    x_angle=0.0,
    y_angle=0.0,
):
    grid_radius = max(
        8.0 * beam_radius,
        8.0 * fiber_mode_radius,
        abs(x_offset) + 8.0 * beam_radius,
        abs(y_offset) + 8.0 * beam_radius,
    )
    grid = np.linspace(-grid_radius, grid_radius, 420)
    dx = grid[1] - grid[0]
    x_grid, y_grid = np.meshgrid(grid, grid)
    beam_r2 = (x_grid - x_offset) ** 2 + (y_grid - y_offset) ** 2
    fiber_r2 = x_grid**2 + y_grid**2
    beam_inv_R = 0.0 if np.isinf(beam_R) else 1.0 / beam_R
    fiber_inv_R = 0.0 if np.isinf(fiber_R) else 1.0 / fiber_R
    k = 2.0 * np.pi / wavelength

    beam_field = (
        np.sqrt(2.0 / np.pi)
        / beam_radius
        * np.exp(-beam_r2 / beam_radius**2)
        * np.exp(-1j * np.pi * beam_inv_R * beam_r2 / wavelength)
        * np.exp(1j * k * (x_angle * x_grid + y_angle * y_grid))
    )
    fiber_field = (
        np.sqrt(2.0 / np.pi)
        / fiber_mode_radius
        * np.exp(-fiber_r2 / fiber_mode_radius**2)
        * np.exp(-1j * np.pi * fiber_inv_R * fiber_r2 / wavelength)
    )
    overlap = np.sum(beam_field * np.conj(fiber_field)) * dx * dx
    return abs(overlap) ** 2


def test_positioned_lenses_convert_to_abcd_sequence():
    lenses = [
        LensSpec(position=0.30, focal_length=0.20, aperture_radius=5e-3),
        LensSpec(position=0.10, focal_length=0.05, aperture_radius=5e-3),
    ]

    elements = build_elements_from_lenses(lenses, final_z=0.50)

    assert isinstance(elements[0], FreeSpace)
    assert np.isclose(elements[0].length, 0.10)
    assert isinstance(elements[1], ThinLens)
    assert np.isclose(elements[1].focal_length, 0.05)
    assert isinstance(elements[2], FreeSpace)
    assert np.isclose(elements[2].length, 0.20)
    assert isinstance(elements[3], ThinLens)
    assert np.isclose(elements[3].focal_length, 0.20)
    assert isinstance(elements[4], FreeSpace)
    assert np.isclose(elements[4].length, 0.20)


def test_lens_positions_must_be_valid():
    with pytest.raises(ValueError):
        LensSpec(position=-0.10, focal_length=0.10, aperture_radius=5e-3)

    lenses = [
        LensSpec(position=0.10, focal_length=0.10, aperture_radius=5e-3),
        LensSpec(position=0.10, focal_length=0.20, aperture_radius=5e-3),
    ]
    with pytest.raises(ValueError):
        build_elements_from_lenses(lenses, final_z=0.30)

    with pytest.raises(ValueError):
        build_elements_from_lenses(
            [LensSpec(position=0.40, focal_length=0.10, aperture_radius=5e-3)],
            final_z=0.30,
        )


def test_aperture_transmission_limits():
    beam_radius = 1.0e-3

    wide_open = gaussian_aperture_transmission(beam_radius, 10.0 * beam_radius)
    small_aperture = gaussian_aperture_transmission(beam_radius, 0.5 * beam_radius)

    assert wide_open > 0.999999999
    assert np.isclose(small_aperture, 1.0 - np.exp(-0.5))
    assert small_aperture < wide_open


def test_aperture_report_flags_small_aperture():
    beam = GaussianBeam(wavelength=1064e-9, w0=1.0e-3)
    lenses = [LensSpec(position=0.0, focal_length=0.10, aperture_radius=0.5e-3)]

    report = analyze_lens_apertures(beam, lenses)[0]

    assert np.isclose(report.beam_radius, beam.w0)
    assert report.transmission < 0.5
    assert report.clips


def test_zero_offset_centroid_stays_centered():
    sample = sample_beam_centroid([], final_z=0.20, z_samples_per_space=20)

    assert np.allclose(sample.x, 0.0)
    assert np.allclose(sample.y, 0.0)
    assert np.allclose(sample.x_angle, 0.0)
    assert np.allclose(sample.y_angle, 0.0)


def test_free_space_offset_with_zero_angle_stays_parallel():
    laser = LaserAlignment(x_offset=1.0e-3, y_offset=-2.0e-3)

    state = beam_center_at_z([], z=0.50, laser=laser)

    assert np.isclose(state.x, 1.0e-3)
    assert np.isclose(state.y, -2.0e-3)
    assert np.isclose(state.x_angle, 0.0)
    assert np.isclose(state.y_angle, 0.0)


def test_initial_angle_changes_centroid_linearly():
    laser = LaserAlignment(x_offset=1.0e-3, y_offset=0.5e-3, x_angle=2.0e-3, y_angle=-1.0e-3)

    state = beam_center_at_z([], z=2.0, laser=laser)

    assert np.isclose(state.x, 5.0e-3)
    assert np.isclose(state.y, -1.5e-3)


def test_decentered_lens_changes_chief_ray_angle():
    lens = LensSpec(
        position=0.0,
        focal_length=0.10,
        aperture_radius=5e-3,
        x_offset=1.0e-3,
        y_offset=-2.0e-3,
    )

    state = beam_center_at_z([lens], z=0.0)

    assert np.isclose(state.x_angle, 0.010)
    assert np.isclose(state.y_angle, -0.020)


def test_aperture_report_flags_decentered_beam_near_edge():
    beam = GaussianBeam(wavelength=1064e-9, w0=1.0e-3)
    laser = LaserAlignment(x_offset=1.0e-3)
    lens = LensSpec(position=0.0, focal_length=0.10, aperture_radius=2.0e-3)

    report = analyze_lens_apertures(beam, [lens], laser=laser)[0]

    assert np.isclose(report.radial_mismatch, 1.0e-3)
    assert report.transmission > 0.99
    assert report.clips


def test_mode_overlap_perfect_match_and_curvature_penalty():
    perfect = mode_overlap_efficiency(
        beam_radius=5.2e-6,
        fiber_mode_radius=5.2e-6,
        wavelength=1550e-9,
    )
    curved = mode_overlap_efficiency(
        beam_radius=5.2e-6,
        fiber_mode_radius=5.2e-6,
        wavelength=1550e-9,
        beam_R=0.001,
    )

    assert np.isclose(perfect, 1.0)
    assert curved < perfect


def test_exact_gaussian_overlap_matches_centered_formula():
    exact = gaussian_mode_overlap_efficiency(
        beam_radius=6.0e-6,
        fiber_mode_radius=5.2e-6,
        wavelength=1550e-9,
        beam_R=0.003,
    )
    centered = mode_overlap_efficiency(
        beam_radius=6.0e-6,
        fiber_mode_radius=5.2e-6,
        wavelength=1550e-9,
        beam_R=0.003,
    )

    assert np.isclose(exact, centered)


def test_exact_gaussian_overlap_matches_numerical_field_integral():
    kwargs = {
        "beam_radius": 6.0e-6,
        "fiber_mode_radius": 5.2e-6,
        "wavelength": 1550e-9,
        "beam_R": 0.0025,
        "x_offset": 1.1e-6,
        "y_offset": -0.7e-6,
        "x_angle": 1.5e-3,
        "y_angle": -0.8e-3,
    }

    analytic = gaussian_mode_overlap_efficiency(**kwargs)
    numerical = _numeric_gaussian_overlap(**kwargs)

    assert np.isclose(analytic, numerical, rtol=2e-3, atol=2e-3)


def test_fiber_transverse_offset_lowers_coupling():
    beam = GaussianBeam(wavelength=1550e-9, w0=5.2e-6)
    centered_fiber = FiberSpec(position=0.0, mode_field_diameter=10.4e-6)
    offset_fiber = FiberSpec(position=0.0, mode_field_diameter=10.4e-6, x_offset=5.2e-6)

    centered = analyze_fiber_coupling(beam, [], centered_fiber)
    offset = analyze_fiber_coupling(beam, [], offset_fiber)

    assert np.isclose(centered.total_efficiency, 1.0)
    assert offset.mode_efficiency == centered.mode_efficiency
    assert offset.offset_efficiency < centered.offset_efficiency
    assert offset.total_efficiency < centered.total_efficiency


def test_fiber_angular_mismatch_lowers_coupling():
    beam = GaussianBeam(wavelength=1550e-9, w0=5.2e-6)
    fiber = FiberSpec(position=0.0, mode_field_diameter=10.4e-6)

    centered = analyze_fiber_coupling(beam, [], fiber)
    tilted = analyze_fiber_coupling(
        beam,
        [],
        fiber,
        laser=LaserAlignment(x_angle=30e-3),
    )

    assert centered.total_efficiency > tilted.total_efficiency
    assert tilted.angle_efficiency < 1.0


def test_default_double_lens_fiber_setup_is_high_coupling():
    beam = GaussianBeam(wavelength=1550e-9, w0=1.0e-3)
    lenses = [
        LensSpec(position=0.05, focal_length=0.05, aperture_radius=12.5e-3),
        LensSpec(position=0.25, focal_length=0.025, aperture_radius=6.25e-3),
    ]
    fiber = FiberSpec(position=0.28, mode_field_diameter=10.4e-6)

    report = analyze_fiber_coupling(beam, lenses, fiber)

    assert report.total_efficiency > 0.99
