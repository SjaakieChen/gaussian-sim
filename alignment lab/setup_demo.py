"""Default two-ball-lens SiN taper coupling setup.

Run:

    python setup_demo.py

All internal values are SI units. Printed and plotted geometry is shown in um.
"""

from __future__ import annotations

import math

import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle

from interactive_setup import (
    DEFAULT_BALL1_FRONT_GAP,
    DEFAULT_BALL2_TAPER_GAP,
    DEFAULT_BALL_DIAMETER,
    DEFAULT_BALL_GAP,
    DEFAULT_MODE_RADIUS_X,
    DEFAULT_MODE_RADIUS_Y,
    DEFAULT_SOURCE_POWER,
    DEFAULT_WAVELENGTH,
    LaserSource,
    TaperDetectorElement,
    default_ball_lens_layout,
    format_simulation_report,
    fresnel_power_transmission,
    propagate_astigmatic_through_balls,
    simulate_layout,
)


# USER CONFIGURATION ---------------------------------------------------------

# Source is an elliptical Gaussian waist at Surface 0. Astigmatic waist offset
# is ignored: x and y share the same waist plane.
SOURCE = LaserSource(
    name="Elliptical Gaussian laser waist",
    position=0.0,
    wavelength=DEFAULT_WAVELENGTH,
    waist_radius=DEFAULT_MODE_RADIUS_X,
    waist_radius_y=DEFAULT_MODE_RADIUS_Y,
    waist_position=0.0,
    power=DEFAULT_SOURCE_POWER,
    x_offset=0.0,
    y_offset=0.0,
    x_angle=0.0,
    y_angle=0.0,
)

# Default geometry:
#   laser waist -> ball 1 front = 39 um
#   ball 1 thickness = 500 um
#   ball 1 back -> ball 2 front = 200 um
#   ball 2 thickness = 500 um
#   ball 2 back -> taper plane = 39 um
BALL_LENSES, TAPER_DETECTORS, FINAL_Z = default_ball_lens_layout()
TAPER_DETECTOR: TaperDetectorElement = TAPER_DETECTORS[0]

Z_SAMPLES_PER_SPACE = 160


def _fmt_um(value: float) -> str:
    return f"{value * 1e6:.6g} um"


def _print_source_of_truth() -> None:
    ball_1, ball_2 = BALL_LENSES
    taper = TAPER_DETECTOR
    print("Default two-ball-lens SiN taper model")
    print(f"  wavelength:             {_fmt_um(SOURCE.wavelength)}")
    print(f"  source power:           {SOURCE.power * 1e3:.6g} mW")
    print(f"  source waist x/y:       ({_fmt_um(SOURCE.waist_radius)}, {_fmt_um(SOURCE.waist_radius_y)})")
    print(f"  source MFD x/y:         ({_fmt_um(2.0 * SOURCE.waist_radius)}, {_fmt_um(2.0 * SOURCE.waist_radius_y)})")
    print(f"  Rayleigh x/y:           ({_fmt_um(SOURCE.rayleigh_range)}, {_fmt_um(SOURCE.rayleigh_range_y)})")
    print()
    print("Ball lenses")
    print(f"  material index:         {ball_1.refractive_index:.6g}")
    print(f"  diameter:               {_fmt_um(ball_1.diameter)}")
    print(f"  clear semi-diameter:    {_fmt_um(ball_1.radius)}")
    print(f"  ball center spacing:    {_fmt_um(ball_2.position - ball_1.position)}")
    print()
    print("Surface-to-surface geometry")
    print(f"  waist -> ball 1 front:  {_fmt_um(DEFAULT_BALL1_FRONT_GAP)}")
    print(f"  ball 1 thickness:       {_fmt_um(DEFAULT_BALL_DIAMETER)}")
    print(f"  ball 1 back -> ball 2:  {_fmt_um(DEFAULT_BALL_GAP)}")
    print(f"  ball 2 thickness:       {_fmt_um(DEFAULT_BALL_DIAMETER)}")
    print(f"  ball 2 back -> taper:   {_fmt_um(DEFAULT_BALL2_TAPER_GAP)}")
    print(f"  total length:           {_fmt_um(FINAL_Z)}")
    print()
    print("SiN inverse taper")
    print(f"  physical width x:       {_fmt_um(taper.width)}")
    print(f"  physical thickness y:   {_fmt_um(taper.height)}")
    print(f"  Gaussian mode radii x/y:({_fmt_um(taper.mode_radius_x)}, {_fmt_um(taper.mode_radius_y)})")
    print(f"  Gaussian MFD x/y:       ({_fmt_um(2.0 * taper.mode_radius_x)}, {_fmt_um(2.0 * taper.mode_radius_y)})")
    print(f"  facet index:            {taper.facet_refractive_index:.6g}")
    print(f"  facet transmission:     {fresnel_power_transmission(1.0, taper.facet_refractive_index):.6g}")
    print(f"  extra transmission:     {taper.extra_transmission:.6g}")
    print(f"  extra loss:             {10.0 * math.log10(taper.extra_transmission):.3f} dB")
    print()


def _plot_default_layout() -> None:
    _state, _reports, _missed, path = propagate_astigmatic_through_balls(
        SOURCE,
        BALL_LENSES,
        FINAL_Z,
        samples_per_space=Z_SAMPLES_PER_SPACE,
    )
    z_values, x_values, _y_values, wx_values, _wy_values = path
    z_um = [z * 1e6 for z in z_values]
    x_um = [x * 1e6 for x in x_values]
    wx_um = [w * 1e6 for w in wx_values]
    upper = [center + radius for center, radius in zip(x_um, wx_um)]
    lower = [center - radius for center, radius in zip(x_um, wx_um)]

    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.fill_between(z_um, lower, upper, color="tab:red", alpha=0.22, label="x-plane 1/e^2 beam radius")
    ax.plot(z_um, x_um, color="tab:red", linewidth=1.6, label="beam center")
    ax.plot(z_um, upper, color="tab:red", linewidth=0.8, linestyle="--")
    ax.plot(z_um, lower, color="tab:red", linewidth=0.8, linestyle="--")

    for ball in BALL_LENSES:
        circle = Circle(
            (ball.position * 1e6, ball.x_offset * 1e6),
            ball.radius * 1e6,
            facecolor="#a9d6f5",
            edgecolor="#2c6f9f",
            alpha=0.45,
            linewidth=1.5,
            label="500 um sapphire ball" if ball is BALL_LENSES[0] else None,
        )
        ax.add_patch(circle)
        ax.axvline(ball.entry_z * 1e6, color="#2c6f9f", linewidth=0.6, alpha=0.35)
        ax.axvline(ball.exit_z * 1e6, color="#2c6f9f", linewidth=0.6, alpha=0.35)

    taper = TAPER_DETECTOR
    taper_rect = Rectangle(
        ((taper.position - 0.5 * taper.width) * 1e6, (taper.x_offset - 0.5 * taper.height) * 1e6),
        taper.width * 1e6,
        taper.height * 1e6,
        facecolor="#4f4f4f",
        edgecolor="#202020",
        linewidth=1.2,
        label="0.2 um x 0.2 um taper start",
    )
    ax.add_patch(taper_rect)
    ax.axvline(taper.position * 1e6, color="#4f4f4f", linewidth=1.0, linestyle=":", label="taper plane")

    ax.set_title("Default two-ball-lens SiN taper coupling layout")
    ax.set_xlabel("z position (um)")
    ax.set_ylabel("x position / beam radius (um)")
    ax.set_xlim(0.0, FINAL_Z * 1e6 * 1.03)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, color="0.9")
    ax.legend(loc="upper right")
    fig.tight_layout()


def main() -> None:
    _print_source_of_truth()
    results = simulate_layout(
        [SOURCE],
        [],
        [],
        FINAL_Z,
        balls=BALL_LENSES,
        tapers=TAPER_DETECTORS,
    )
    print(format_simulation_report(results))
    _plot_default_layout()
    if "agg" not in plt.get_backend().lower():
        plt.show()


if __name__ == "__main__":
    main()
