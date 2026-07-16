"""Free-space Gaussian beam example."""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from beam import GaussianBeam
from plotting import plot_beam_envelope


def main() -> None:
    wavelength = 1064e-9
    w0 = 0.5e-3
    beam = GaussianBeam(wavelength=wavelength, w0=w0)
    z_rayleigh = beam.rayleigh_range()

    z = np.linspace(-2.0 * z_rayleigh, 2.0 * z_rayleigh, 600)
    w = beam.w(z)

    _, ax = plot_beam_envelope(z, w, z_unit="m", w_unit="mm")
    ax.set_title("Free-space Gaussian beam")

    print(f"Rayleigh range: {z_rayleigh:.6g} m")
    print(f"w(z0): {beam.w(0.0):.6g} m")
    print(f"R(z0): {beam.R(0.0)}")
    print(f"Gouy phase at -2 z_R: {beam.gouy_phase(-2.0 * z_rayleigh):.6g} rad")
    print(f"Gouy phase at +2 z_R: {beam.gouy_phase(2.0 * z_rayleigh):.6g} rad")

    plt.show()


if __name__ == "__main__":
    main()
