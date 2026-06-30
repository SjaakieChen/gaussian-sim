"""Smoke tests for plotting helpers."""

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from beam import GaussianBeam
from layout import FiberSpec, LaserAlignment, LensSpec, sample_beam_centroid
from elements import FreeSpace, ThinLens
from plotting import plot_beam_3d, plot_beam_envelope
from system import sample_system


def test_plot_beam_3d_creates_figure():
    beam = GaussianBeam(wavelength=1064e-9, w0=1.0e-3)
    sample = sample_system(beam, [], z_samples_per_space=10)
    lens = LensSpec(position=0.0, focal_length=0.10, aperture_radius=5e-3)

    fig, ax = plot_beam_3d(sample.z, sample.w, lenses=[lens])

    assert fig is ax.figure
    assert ax.get_xlabel() == "z (m)"
    plt.close(fig)


def test_plot_beam_envelope_labels_lens_for_legend():
    beam = GaussianBeam(wavelength=1064e-9, w0=1.0e-3)
    elements = [FreeSpace(0.1), ThinLens(0.2), FreeSpace(0.1)]
    sample = sample_system(beam, elements, z_samples_per_space=10)

    fig, ax = plot_beam_envelope(sample.z, sample.w, elements=sample.element_positions)
    _handles, labels = ax.get_legend_handles_labels()

    assert "lens" in labels
    plt.close(fig)


def test_plot_beam_3d_accepts_offsets_and_fiber():
    beam = GaussianBeam(wavelength=1550e-9, w0=1.0e-3)
    lenses = [
        LensSpec(position=0.05, focal_length=0.05, aperture_radius=12.5e-3, x_offset=1e-3),
        LensSpec(position=0.25, focal_length=0.025, aperture_radius=6.25e-3, y_offset=-1e-3),
    ]
    fiber = FiberSpec(position=0.28, mode_field_diameter=10.4e-6, x_offset=2e-6)
    laser = LaserAlignment(x_offset=0.5e-3, x_angle=1e-3)
    sample = sample_system(
        beam,
        [],
        z_samples_per_space=10,
    )
    center = sample_beam_centroid(lenses=[], final_z=sample.z[-1], laser=laser, z_samples_per_space=10)

    fig, ax = plot_beam_3d(
        sample.z,
        sample.w,
        lenses=lenses,
        center_x=center.x,
        center_y=center.y,
        fiber=fiber,
    )

    assert fig is ax.figure
    assert fiber.cladding_radius == 62.5e-6
    plt.close(fig)
