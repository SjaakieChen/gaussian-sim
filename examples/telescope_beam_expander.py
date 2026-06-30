"""Two-lens Gaussian beam expander examples."""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt

if __package__ in {None, ""}:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from beam import GaussianBeam
from elements import FreeSpace, ThinLens
from plotting import plot_beam_envelope
from system import sample_system


def make_keplerian():
    f1 = 0.050
    f2 = 0.150
    return [
        FreeSpace(0.050),
        ThinLens(f1),
        FreeSpace(f1 + f2),
        ThinLens(f2),
        FreeSpace(0.300),
    ], f2 / f1


def make_galilean():
    f1 = -0.050
    f2 = 0.150
    return [
        FreeSpace(0.050),
        ThinLens(f1),
        FreeSpace(f2 - abs(f1)),
        ThinLens(f2),
        FreeSpace(0.300),
    ], f2 / abs(f1)


def main() -> None:
    wavelength = 1064e-9
    input_waist = 0.8e-3
    beam = GaussianBeam(wavelength=wavelength, w0=input_waist)

    systems = [
        ("Keplerian telescope", *make_keplerian()),
        ("Galilean telescope", *make_galilean()),
    ]

    fig, axes = plt.subplots(len(systems), 1, sharex=False, figsize=(8, 6))

    for ax, (title, elements, magnification) in zip(axes, systems, strict=True):
        sample = sample_system(beam, elements, z_samples_per_space=400)
        plot_beam_envelope(
            sample.z,
            sample.w,
            elements=sample.element_positions,
            z_unit="m",
            w_unit="mm",
            ax=ax,
        )
        ax.set_title(f"{title}, approximate M = {magnification:.3g}")

    print("Keplerian telescope approximate magnification: 3")
    print("Galilean telescope approximate magnification: 3")

    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
