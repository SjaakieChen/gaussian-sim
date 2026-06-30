"""Configurable off-axis double-lens fiber mode-matching setup.

Edit the values in the USER CONFIGURATION section, then run:

    python setup_demo.py

All internal units are SI: metres, radians, and seconds.
"""

from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt

from beam import GaussianBeam, waist_from_q
from layout import (
    FiberSpec,
    LaserAlignment,
    LensSpec,
    analyze_fiber_coupling,
    analyze_lens_apertures,
    build_elements_from_lenses,
    q_after_positioned_lenses,
    sample_beam_centroid,
)
from plotting import plot_beam_3d, plot_beam_envelope
from system import sample_system


# USER CONFIGURATION ---------------------------------------------------------

# Beam waist parameters.
WAVELENGTH = 1550e-9
INPUT_WAIST_RADIUS = 1.0e-3
WAIST_POSITION = 0.0

# Beam center and angle at START_Z.
LASER = LaserAlignment(
    x_offset=0.0,
    y_offset=0.0,
    x_angle=0.0,
    y_angle=0.0,
)

# Simulation window along the optical z-axis.
START_Z = 0.0
FINAL_Z = 0.32

# Two thin lenses. Aperture values are radii, not diameters.
LENSES = [
    LensSpec(
        position=0.05,
        focal_length=0.05,
        aperture_radius=12.5e-3,
        x_offset=0.0,
        y_offset=0.0,
    ),
    LensSpec(
        position=0.25,
        focal_length=0.025,
        aperture_radius=6.25e-3,
        x_offset=0.0,
        y_offset=0.0,
    ),
]

# Corning SMF-28-style telecom single-mode fiber default at 1550 nm.
FIBER = FiberSpec(
    position=0.28,
    mode_field_diameter=10.4e-6,
    x_offset=0.0,
    y_offset=0.0,
    name="Corning SMF-28-style @ 1550 nm",
)

# Sampling and reporting.
Z_SAMPLES_PER_SPACE = 500
CLIPPING_RADIUS_FACTOR = 1.5


def _fmt_curvature(radius: float) -> str:
    if np.isinf(radius):
        return "inf"
    return f"{radius * 1e3:.3f} mm"


def _print_setup() -> None:
    print("Beam setup")
    print(f"  wavelength:       {WAVELENGTH * 1e9:.0f} nm")
    print(f"  input waist:      {INPUT_WAIST_RADIUS * 1e3:.3f} mm")
    print(f"  waist position:   {WAIST_POSITION * 1e2:.3f} cm")
    print(f"  simulation start: {START_Z * 1e2:.3f} cm")
    print(f"  simulation end:   {FINAL_Z * 1e2:.3f} cm")
    print()
    print("Laser alignment at simulation start")
    print(f"  x offset:         {LASER.x_offset * 1e3:.4f} mm")
    print(f"  y offset:         {LASER.y_offset * 1e3:.4f} mm")
    print(f"  x angle:          {LASER.x_angle * 1e3:.4f} mrad")
    print(f"  y angle:          {LASER.y_angle * 1e3:.4f} mrad")
    print()


def _print_lens_reports(reports) -> None:
    print("Lenses and aperture checks")
    header = (
        "  #  z(cm)   f(cm)  aperture(mm)  lens x/y(mm)    "
        "beam x/y(mm)    mismatch(mm)  beam w(mm)  trans.      status"
    )
    print(header)
    for index, report in enumerate(reports, start=1):
        lens = report.lens
        status = "CLIPPING WARNING" if report.clips else "ok"
        print(
            f"  {index:<1d}"
            f" {lens.position * 1e2:6.2f}"
            f" {lens.focal_length * 1e2:7.2f}"
            f" {lens.aperture_radius * 1e3:12.3f}"
            f"  ({lens.x_offset * 1e3:7.3f}, {lens.y_offset * 1e3:7.3f})"
            f"  ({report.beam_x * 1e3:7.3f}, {report.beam_y * 1e3:7.3f})"
            f" {report.radial_mismatch * 1e3:12.4f}"
            f" {report.beam_radius * 1e3:10.4f}"
            f" {report.transmission * 100:9.4f}%"
            f"  {status}"
        )
    print()


def _print_focus_report(waist_position: float, d_waist: float, focused_waist: float, z_rayleigh: float) -> None:
    print("Focus after final lens")
    print(f"  waist position:       {waist_position * 1e2:.4f} cm")
    print(f"  from final lens:      {d_waist * 1e2:.4f} cm")
    print(f"  focused waist radius: {focused_waist * 1e6:.4f} um")
    print(f"  Rayleigh range:       {z_rayleigh * 1e3:.4f} mm")
    print()


def _print_fiber_report(report) -> None:
    fiber = report.fiber
    print("Fiber coupling")
    print(f"  fiber:                {fiber.name}")
    print(f"  fiber position:       {fiber.position * 1e2:.4f} cm")
    print(f"  fiber x/y offset:     ({fiber.x_offset * 1e6:.3f}, {fiber.y_offset * 1e6:.3f}) um")
    print(f"  mode-field diameter:  {fiber.mode_field_diameter * 1e6:.3f} um")
    print(f"  fiber mode radius:    {fiber.mode_radius * 1e6:.3f} um")
    print(f"  beam radius at fiber: {report.beam_radius * 1e6:.3f} um")
    print(f"  beam R at fiber:      {_fmt_curvature(report.beam_R)}")
    print(f"  beam x/y at fiber:    ({report.beam_x * 1e6:.3f}, {report.beam_y * 1e6:.3f}) um")
    print(f"  beam angle x/y:       ({report.beam_x_angle * 1e3:.4f}, {report.beam_y_angle * 1e3:.4f}) mrad")
    print(f"  transverse mismatch:  {report.radial_mismatch * 1e6:.3f} um")
    print(f"  angular mismatch:     {report.angular_mismatch * 1e3:.4f} mrad")
    print(f"  mode efficiency:      {report.mode_efficiency * 100:.4f}%")
    print(f"  offset efficiency:    {report.offset_efficiency * 100:.4f}%")
    print(f"  angular efficiency:   {report.angle_efficiency * 100:.4f}%")
    print(f"  total efficiency:     {report.total_efficiency * 100:.4f}%")
    print()


def main() -> None:
    beam = GaussianBeam(
        wavelength=WAVELENGTH,
        w0=INPUT_WAIST_RADIUS,
        z0=WAIST_POSITION,
    )
    elements = build_elements_from_lenses(LENSES, final_z=FINAL_Z, start_z=START_Z)
    sample = sample_system(
        beam,
        elements,
        z_samples_per_space=Z_SAMPLES_PER_SPACE,
        start_z=START_Z,
    )
    center_sample = sample_beam_centroid(
        LENSES,
        final_z=FINAL_Z,
        start_z=START_Z,
        laser=LASER,
        z_samples_per_space=Z_SAMPLES_PER_SPACE,
    )
    aperture_reports = analyze_lens_apertures(
        beam,
        LENSES,
        start_z=START_Z,
        clipping_radius_factor=CLIPPING_RADIUS_FACTOR,
        laser=LASER,
    )
    fiber_report = analyze_fiber_coupling(
        beam,
        LENSES,
        FIBER,
        start_z=START_Z,
        laser=LASER,
    )

    q_after_last_lens, final_lens_position = q_after_positioned_lenses(
        beam,
        LENSES,
        start_z=START_Z,
    )
    d_waist, focused_waist, z_rayleigh_focus = waist_from_q(q_after_last_lens, WAVELENGTH)
    waist_position = final_lens_position + d_waist
    waist_fiber_error = FIBER.position - waist_position

    _print_setup()
    _print_lens_reports(aperture_reports)
    _print_focus_report(waist_position, d_waist, focused_waist, z_rayleigh_focus)
    print(f"Fiber axial offset from waist: {waist_fiber_error * 1e6:.3f} um")
    print()
    _print_fiber_report(fiber_report)

    _, ax_2d = plot_beam_envelope(
        sample.z,
        sample.w,
        elements=sample.element_positions,
        z_unit="m",
        w_unit="um",
    )
    fiber_color = "0.35"
    ax_2d.axvline(FIBER.position, color=fiber_color, linestyle="-.", linewidth=1.2, label="fiber")
    ax_2d.axhline(FIBER.mode_radius * 1e6, color=fiber_color, linestyle=":", linewidth=1.0)
    ax_2d.axhline(-FIBER.mode_radius * 1e6, color=fiber_color, linestyle=":", linewidth=1.0)
    ax_2d.scatter(
        [FIBER.position],
        [0.0],
        marker=".",
        s=90,
        color=fiber_color,
        label="fiber facet",
        zorder=5,
    )
    if START_Z <= waist_position <= FINAL_Z:
        ax_2d.axvline(
            waist_position,
            color="tab:red",
            linestyle=":",
            linewidth=1.2,
            label="waist",
        )
    ax_2d.legend()
    ax_2d.set_title("Double-lens fiber mode matching")

    fig_3d, _ax_3d = plot_beam_3d(
        sample.z,
        sample.w,
        lenses=LENSES,
        center_x=center_sample.x,
        center_y=center_sample.y,
        fiber=FIBER,
        waist_position=waist_position,
        waist_radius=focused_waist,
        z_unit="m",
        transverse_unit="mm",
        fiber_color=fiber_color,
    )
    fig_3d.suptitle("3D off-axis beam, lenses, and fiber")

    plt.show()


if __name__ == "__main__":
    main()
