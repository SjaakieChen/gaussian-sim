"""Single thin-lens focusing example."""

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


def main() -> None:
    wavelength = 1064e-9
    w0 = 1.0e-3
    lens_position = 0.20
    focal_length = 0.10
    propagation_after_lens = 0.25

    beam = GaussianBeam(wavelength=wavelength, w0=w0)
    elements = [
        FreeSpace(lens_position),
        ThinLens(focal_length),
        FreeSpace(propagation_after_lens),
    ]

    sample = sample_system(beam, elements, z_samples_per_space=500)

    q_before_lens = beam.q(lens_position)
    q_after_lens = abcd_propagate(q_before_lens, ThinLens(focal_length).abcd())
    d_waist, w0_new, z_rayleigh_new = waist_from_q(q_after_lens, wavelength)
    waist_position = lens_position + d_waist

    _, ax = plot_beam_envelope(
        sample.z,
        sample.w,
        elements=sample.element_positions,
        z_unit="m",
        w_unit="mm",
    )
    ax.axvline(waist_position, color="tab:red", linestyle=":", linewidth=1.2)
    ax.set_title("Single thin-lens Gaussian focus")

    print(f"New waist position: {waist_position:.6g} m")
    print(f"Distance from lens to waist: {d_waist:.6g} m")
    print(f"New waist size: {w0_new * 1e6:.6g} um")
    print(f"Rayleigh range after focus: {z_rayleigh_new * 1e3:.6g} mm")

    plt.show()


if __name__ == "__main__":
    main()
