"""Simple Gaussian waist-size matching estimate for fiber coupling."""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from beam import GaussianBeam, waist_from_q
from elements import FreeSpace, ThinLens
from plotting import plot_beam_envelope
from system import abcd_propagate, sample_system


def waist_size_coupling_efficiency(w_beam: float, w_fiber: float) -> float:
    """Return aligned Gaussian mode overlap from waist-size mismatch only."""

    if w_beam <= 0 or w_fiber <= 0:
        raise ValueError("mode radii must be positive")
    return (2.0 * w_beam * w_fiber / (w_beam**2 + w_fiber**2)) ** 2


def main() -> None:
    wavelength = 1064e-9
    input_waist = 1.0e-3
    lens_position = 0.050
    focal_length = 0.015
    fiber_mode_radius = 5.2e-6

    beam = GaussianBeam(wavelength=wavelength, w0=input_waist)
    elements = [
        FreeSpace(lens_position),
        ThinLens(focal_length),
        FreeSpace(0.040),
    ]
    sample = sample_system(beam, elements, z_samples_per_space=500)

    q_before_lens = beam.q(lens_position)
    q_after_lens = abcd_propagate(q_before_lens, ThinLens(focal_length).abcd())
    d_waist, focused_waist, _z_rayleigh = waist_from_q(q_after_lens, wavelength)
    waist_position = lens_position + d_waist
    efficiency = waist_size_coupling_efficiency(focused_waist, fiber_mode_radius)

    _, ax = plot_beam_envelope(
        sample.z,
        sample.w,
        elements=sample.element_positions,
        z_unit="m",
        w_unit="um",
    )
    ax.axvline(waist_position, color="tab:red", linestyle=":", linewidth=1.2)
    ax.axhline(fiber_mode_radius * 1e6, color="tab:green", linestyle=":", linewidth=1.0)
    ax.axhline(-fiber_mode_radius * 1e6, color="tab:green", linestyle=":", linewidth=1.0)
    ax.set_title("Fiber mode waist-size matching")

    print(f"Focused waist position: {waist_position:.6g} m")
    print(f"Focused waist radius: {focused_waist * 1e6:.6g} um")
    print(f"Fiber mode radius: {fiber_mode_radius * 1e6:.6g} um")
    print(f"Waist-size-only coupling efficiency: {efficiency:.4f}")

    plt.show()


if __name__ == "__main__":
    main()
