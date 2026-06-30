"""Plotting helpers for sampled Gaussian beam systems."""

from __future__ import annotations

from collections.abc import Iterable

import matplotlib.pyplot as plt
import numpy as np

from beam import imag_part, real_part
from elements import FreeSpace, OpticalElement


_UNIT_FACTORS = {
    "m": 1.0,
    "cm": 1e2,
    "mm": 1e3,
    "um": 1e6,
    "nm": 1e9,
}


def _factor(unit: str) -> float:
    try:
        return _UNIT_FACTORS[unit]
    except KeyError as exc:
        valid = ", ".join(sorted(_UNIT_FACTORS))
        raise ValueError(f"unsupported unit {unit!r}; use one of: {valid}") from exc


def _element_positions(elements: Iterable[object] | None) -> list[float]:
    if elements is None:
        return []

    items = list(elements)
    if not items:
        return []

    positions: list[float] = []
    if all(isinstance(item, tuple) and len(item) == 2 for item in items):
        for position, _element in items:
            positions.append(float(position))
        return positions

    z_position = 0.0
    for element in items:
        if isinstance(element, FreeSpace):
            z_position += element.length
        elif isinstance(element, OpticalElement):
            positions.append(z_position)

    return positions


def _draw_elements(ax, elements, z_factor: float) -> None:
    for index, position in enumerate(_element_positions(elements)):
        label = "lens" if index == 0 else "_nolegend_"
        ax.axvline(position * z_factor, color="0.45", linestyle="--", linewidth=1.0, label=label)


def plot_beam_radius(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    z,
    w,
    elements=None,
    z_unit: str = "m",
    w_unit: str = "mm",
    ax=None,
):
    """Plot beam radius w(z)."""

    z_factor = _factor(z_unit)
    w_factor = _factor(w_unit)

    if ax is None:
        fig, ax = plt.subplots()
    else:
        fig = ax.figure

    ax.plot(np.asarray(z) * z_factor, np.asarray(w) * w_factor)
    _draw_elements(ax, elements, z_factor)
    ax.set_xlabel(f"z ({z_unit})")
    ax.set_ylabel(f"beam radius w ({w_unit})")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig, ax


def plot_beam_envelope(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    z,
    w,
    elements=None,
    z_unit: str = "m",
    w_unit: str = "mm",
    ax=None,
):
    """Plot +w(z) and -w(z) as a Gaussian beam envelope."""

    z_factor = _factor(z_unit)
    w_factor = _factor(w_unit)
    z_display = np.asarray(z) * z_factor
    w_display = np.asarray(w) * w_factor

    if ax is None:
        fig, ax = plt.subplots()
    else:
        fig = ax.figure

    ax.plot(z_display, w_display, color="tab:blue")
    ax.plot(z_display, -w_display, color="tab:blue")
    ax.axhline(0.0, color="0.2", linewidth=0.8, alpha=0.5)
    _draw_elements(ax, elements, z_factor)
    ax.set_xlabel(f"z ({z_unit})")
    ax.set_ylabel(f"beam radius ({w_unit})")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    return fig, ax


def plot_q_evolution(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    z,
    q,
    elements=None,
    z_unit: str = "m",
    q_unit: str = "m",
    ax=None,
):
    """Plot the real and imaginary parts of q along the system."""

    z_factor = _factor(z_unit)
    q_factor = _factor(q_unit)
    z_display = np.asarray(z) * z_factor
    q_array = np.asarray(q, dtype=complex)

    if ax is None:
        fig, ax = plt.subplots()
    else:
        fig = ax.figure

    ax.plot(z_display, real_part(q_array) * q_factor, label="Re(q)")
    ax.plot(z_display, imag_part(q_array) * q_factor, label="Im(q)")
    _draw_elements(ax, elements, z_factor)
    ax.set_xlabel(f"z ({z_unit})")
    ax.set_ylabel(f"q ({q_unit})")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    return fig, ax


def plot_beam_3d(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    z,
    w,
    lenses=None,
    center_x=None,
    center_y=None,
    fiber=None,
    waist_position: float | None = None,
    waist_radius: float | None = None,
    z_unit: str = "m",
    transverse_unit: str = "mm",
    angular_samples: int = 64,
    fiber_color: str = "tab:purple",
    ax=None,
):
    """Render a Gaussian beam envelope, lens apertures, and optional fiber in 3D."""

    z_factor = _factor(z_unit)
    transverse_factor = _factor(transverse_unit)
    z_array = np.asarray(z, dtype=float)
    z_display = z_array * z_factor
    w_display = np.asarray(w, dtype=float) * transverse_factor

    if z_display.shape != w_display.shape:
        raise ValueError("z and w must have matching shapes")
    if z_display.size == 0:
        raise ValueError("z and w must not be empty")

    if center_x is None:
        center_x_display = np.zeros_like(z_display)
    else:
        center_x_display = np.asarray(center_x, dtype=float) * transverse_factor
    if center_y is None:
        center_y_display = np.zeros_like(z_display)
    else:
        center_y_display = np.asarray(center_y, dtype=float) * transverse_factor
    if center_x_display.shape != z_display.shape or center_y_display.shape != z_display.shape:
        raise ValueError("center_x and center_y must match z shape")

    angular_count = int(angular_samples)
    if angular_count < 8:
        raise ValueError("angular_samples must be at least 8")

    if ax is None:
        fig = plt.figure()
        ax = fig.add_subplot(111, projection="3d")
    else:
        fig = ax.figure

    theta = np.linspace(0.0, 2.0 * np.pi, angular_count)
    theta_grid, optical_z_grid = np.meshgrid(theta, z_display)
    radius_grid = w_display[:, np.newaxis]
    beam_x_grid = center_x_display[:, np.newaxis] + radius_grid * np.cos(theta_grid)
    beam_y_grid = center_y_display[:, np.newaxis] + radius_grid * np.sin(theta_grid)

    ax.plot_surface(
        optical_z_grid,
        beam_x_grid,
        beam_y_grid,
        color="tab:blue",
        alpha=0.28,
        linewidth=0,
        antialiased=True,
        shade=True,
    )
    ax.plot(z_display, center_x_display, center_y_display, color="0.25", linewidth=0.9)
    ax.plot(z_display, np.zeros_like(z_display), np.zeros_like(z_display), color="0.65", linewidth=0.7)

    max_transverse = float(
        np.max(
            np.hypot(center_x_display, center_y_display)
            + w_display
        )
    )
    for lens in [] if lenses is None else lenses:
        position = lens.position * z_factor
        aperture = lens.aperture_radius * transverse_factor
        lens_x = lens.x_offset * transverse_factor
        lens_y = lens.y_offset * transverse_factor
        max_transverse = max(max_transverse, _hypot(lens_x, lens_y) + aperture)

        disk_r = np.linspace(0.0, aperture, 3)
        disk_theta, disk_radius = np.meshgrid(theta, disk_r)
        disk_z = np.full_like(disk_radius, position)
        disk_x = lens_x + disk_radius * np.cos(disk_theta)
        disk_y = lens_y + disk_radius * np.sin(disk_theta)

        ax.plot_surface(
            disk_z,
            disk_x,
            disk_y,
            color="tab:orange",
            alpha=0.22,
            linewidth=0,
            shade=False,
        )
        ax.plot(
            np.full_like(theta, position),
            lens_x + aperture * np.cos(theta),
            lens_y + aperture * np.sin(theta),
            color="tab:orange",
            linewidth=1.4,
        )

    if fiber is not None:
        fiber_z = fiber.position * z_factor
        fiber_x = fiber.x_offset * transverse_factor
        fiber_y = fiber.y_offset * transverse_factor
        mode_radius = fiber.mode_radius * transverse_factor
        cladding_radius = fiber.cladding_radius * transverse_factor
        max_transverse = max(max_transverse, _hypot(fiber_x, fiber_y) + cladding_radius)

        disk_r = np.linspace(0.0, cladding_radius, 3)
        disk_theta, disk_radius = np.meshgrid(theta, disk_r)
        disk_z = np.full_like(disk_radius, fiber_z)
        disk_x = fiber_x + disk_radius * np.cos(disk_theta)
        disk_y = fiber_y + disk_radius * np.sin(disk_theta)

        ax.plot_surface(
            disk_z,
            disk_x,
            disk_y,
            color=fiber_color,
            alpha=0.28,
            linewidth=0,
            shade=False,
        )
        ax.plot(
            np.full_like(theta, fiber_z),
            fiber_x + cladding_radius * np.cos(theta),
            fiber_y + cladding_radius * np.sin(theta),
            color=fiber_color,
            linewidth=2.0,
        )
        ax.plot(
            np.full_like(theta, fiber_z),
            fiber_x + mode_radius * np.cos(theta),
            fiber_y + mode_radius * np.sin(theta),
            color=fiber_color,
            linestyle=":",
            linewidth=1.2,
        )

    if waist_position is not None:
        waist_z = waist_position * z_factor
        waist_x = _interp_unique(z_array, center_x_display, waist_position)
        waist_y = _interp_unique(z_array, center_y_display, waist_position)
        ax.scatter([waist_z], [waist_x], [waist_y], color="tab:red", s=28, depthshade=False)
        if waist_radius is not None and waist_radius > 0:
            waist_r = waist_radius * transverse_factor
            max_transverse = max(max_transverse, _hypot(waist_x, waist_y) + waist_r)
            ax.plot(
                np.full_like(theta, waist_z),
                waist_x + waist_r * np.cos(theta),
                waist_y + waist_r * np.sin(theta),
                color="tab:red",
                linestyle=":",
                linewidth=1.4,
            )

    transverse_limit = max_transverse * 1.15
    if transverse_limit <= 0:
        transverse_limit = 1.0
    z_min = float(np.min(z_display))
    z_max = float(np.max(z_display))
    if z_min == z_max:
        z_min -= 0.5
        z_max += 0.5

    ax.set_xlim(z_min, z_max)
    ax.set_ylim(-transverse_limit, transverse_limit)
    ax.set_zlim(-transverse_limit, transverse_limit)
    ax.set_xlabel(f"z ({z_unit})")
    ax.set_ylabel(f"x ({transverse_unit})")
    ax.set_zlabel(f"y ({transverse_unit})")
    ax.view_init(elev=12, azim=-90)
    ax.set_box_aspect((2.5, 1.0, 1.0))
    fig.tight_layout()
    return fig, ax


def _hypot(x_value: float, y_value: float) -> float:
    return float(np.hypot(x_value, y_value))


def _interp_unique(z_values: np.ndarray, values: np.ndarray, target_z: float) -> float:
    unique_z, unique_indices = np.unique(z_values, return_index=True)
    unique_values = values[unique_indices]
    if target_z <= unique_z[0]:
        return float(unique_values[0])
    if target_z >= unique_z[-1]:
        return float(unique_values[-1])
    return float(np.interp(target_z, unique_z, unique_values))
