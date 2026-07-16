"""User-facing layout helpers for positioned and off-axis optical systems."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np

from beam import GaussianBeam, q_to_R, q_to_w
from elements import FreeSpace, OpticalElement, ThinLens
from system import abcd_propagate


def _check_finite(name: str, value: float) -> None:
    if not np.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class LaserAlignment:
    """Beam center and angle at the simulation start plane."""

    x_offset: float = 0.0
    y_offset: float = 0.0
    x_angle: float = 0.0
    y_angle: float = 0.0

    def __post_init__(self) -> None:
        _check_finite("laser x_offset", self.x_offset)
        _check_finite("laser y_offset", self.y_offset)
        _check_finite("laser x_angle", self.x_angle)
        _check_finite("laser y_angle", self.y_angle)


@dataclass(frozen=True)
class LensSpec:
    """Thin lens placed at an absolute axial position.

    All values are SI units. ``aperture_radius`` is a radius, not a diameter.
    Negative ``focal_length`` values represent diverging lenses. ``x_offset``
    and ``y_offset`` place the lens aperture center relative to the global
    optical axis.
    """

    position: float
    focal_length: float
    aperture_radius: float
    x_offset: float = 0.0
    y_offset: float = 0.0

    def __post_init__(self) -> None:
        _check_finite("lens position", self.position)
        if self.position < 0:
            raise ValueError("lens position must be non-negative")
        _check_finite("lens focal_length", self.focal_length)
        if self.focal_length == 0:
            raise ValueError("lens focal_length must be non-zero")
        _check_finite("lens aperture_radius", self.aperture_radius)
        if self.aperture_radius <= 0:
            raise ValueError("lens aperture_radius must be positive")
        _check_finite("lens x_offset", self.x_offset)
        _check_finite("lens y_offset", self.y_offset)


@dataclass(frozen=True)
class FiberSpec:
    """Single-mode fiber facet and mode definition."""

    position: float
    mode_field_diameter: float
    x_offset: float = 0.0
    y_offset: float = 0.0
    name: str = "single-mode fiber"
    cladding_diameter: float = 125e-6

    def __post_init__(self) -> None:
        _check_finite("fiber position", self.position)
        if self.position < 0:
            raise ValueError("fiber position must be non-negative")
        _check_finite("fiber mode_field_diameter", self.mode_field_diameter)
        if self.mode_field_diameter <= 0:
            raise ValueError("fiber mode_field_diameter must be positive")
        _check_finite("fiber x_offset", self.x_offset)
        _check_finite("fiber y_offset", self.y_offset)
        _check_finite("fiber cladding_diameter", self.cladding_diameter)
        if self.cladding_diameter <= 0:
            raise ValueError("fiber cladding_diameter must be positive")

    @property
    def mode_radius(self) -> float:
        return 0.5 * self.mode_field_diameter

    @property
    def cladding_radius(self) -> float:
        return 0.5 * self.cladding_diameter


@dataclass(frozen=True)
class BeamCenterState:
    """Paraxial beam center and angular direction at one z plane."""

    z: float
    x: float
    y: float
    x_angle: float
    y_angle: float


@dataclass(frozen=True)
class CentroidSample:
    """Sampled chief-ray centerline for off-axis visualization."""

    z: np.ndarray
    x: np.ndarray
    y: np.ndarray
    x_angle: np.ndarray
    y_angle: np.ndarray


@dataclass(frozen=True)
class ApertureReport:
    """Beam size and clipping estimate at a lens plane."""

    lens: LensSpec
    beam_radius: float
    transmission: float
    clipping_threshold_radius: float
    beam_x: float = 0.0
    beam_y: float = 0.0
    x_mismatch: float = 0.0
    y_mismatch: float = 0.0

    @property
    def radial_mismatch(self) -> float:
        return math.hypot(self.x_mismatch, self.y_mismatch)

    @property
    def clips(self) -> bool:
        return self.radial_mismatch + self.clipping_threshold_radius > self.lens.aperture_radius


@dataclass(frozen=True)
class FiberCouplingReport:
    """Mode-matching estimate at a fiber facet."""

    fiber: FiberSpec
    beam_radius: float
    beam_R: float
    beam_x: float
    beam_y: float
    beam_x_angle: float
    beam_y_angle: float
    x_mismatch: float
    y_mismatch: float
    mode_efficiency: float
    offset_efficiency: float
    angle_efficiency: float = 1.0
    combined_efficiency: float | None = None

    @property
    def fiber_mode_radius(self) -> float:
        return self.fiber.mode_radius

    @property
    def radial_mismatch(self) -> float:
        return math.hypot(self.x_mismatch, self.y_mismatch)

    @property
    def angular_mismatch(self) -> float:
        return math.hypot(self.beam_x_angle, self.beam_y_angle)

    @property
    def total_efficiency(self) -> float:
        if self.combined_efficiency is not None:
            return self.combined_efficiency
        return self.mode_efficiency * self.offset_efficiency * self.angle_efficiency


def gaussian_aperture_transmission(beam_radius: float, aperture_radius: float) -> float:
    """Return centered power fraction passing through a circular aperture.

    This uses the TEM00 radial power integral with ``beam_radius`` as the
    1/e^2 intensity radius:

        P(r < a) = 1 - exp(-2 a^2 / w^2)
    """

    if beam_radius <= 0:
        raise ValueError("beam_radius must be positive")
    if aperture_radius <= 0:
        raise ValueError("aperture_radius must be positive")

    return float(1.0 - math.exp(-2.0 * aperture_radius**2 / beam_radius**2))


def mode_overlap_efficiency(
    beam_radius: float,
    fiber_mode_radius: float,
    wavelength: float,
    beam_R: float = np.inf,
    fiber_R: float = np.inf,
    refractive_index: float = 1.0,
) -> float:
    """Return Gaussian mode overlap from size and curvature mismatch."""

    if beam_radius <= 0:
        raise ValueError("beam_radius must be positive")
    if fiber_mode_radius <= 0:
        raise ValueError("fiber_mode_radius must be positive")
    if wavelength <= 0:
        raise ValueError("wavelength must be positive")
    if refractive_index <= 0:
        raise ValueError("refractive_index must be positive")

    inv_beam_R = 0.0 if np.isinf(beam_R) else 1.0 / beam_R
    inv_fiber_R = 0.0 if np.isinf(fiber_R) else 1.0 / fiber_R
    curvature_term = (
        np.pi
        * refractive_index
        * beam_radius
        * fiber_mode_radius
        / wavelength
        * (inv_beam_R - inv_fiber_R)
    )
    size_term = beam_radius / fiber_mode_radius + fiber_mode_radius / beam_radius
    return float(4.0 / (size_term**2 + curvature_term**2))


def gaussian_mode_overlap_efficiency(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    beam_radius: float,
    fiber_mode_radius: float,
    wavelength: float,
    beam_R: float = np.inf,
    fiber_R: float = np.inf,
    x_offset: float = 0.0,
    y_offset: float = 0.0,
    x_angle: float = 0.0,
    y_angle: float = 0.0,
    refractive_index: float = 1.0,
) -> float:
    """Return exact overlap for two circular paraxial Gaussian modes.

    The fiber mode is centered on the reference axis. The beam mode may have a
    transverse center offset, wavefront-curvature mismatch, and transverse
    phase tilt. This is still an ideal Gaussian-mode overlap: it does not model
    aperture diffraction, polarization mismatch, Fresnel reflection, or fiber
    aberrations.
    """

    if beam_radius <= 0:
        raise ValueError("beam_radius must be positive")
    if fiber_mode_radius <= 0:
        raise ValueError("fiber_mode_radius must be positive")
    if wavelength <= 0:
        raise ValueError("wavelength must be positive")
    if refractive_index <= 0:
        raise ValueError("refractive_index must be positive")

    inv_beam_R = 0.0 if np.isinf(beam_R) else 1.0 / beam_R
    inv_fiber_R = 0.0 if np.isinf(fiber_R) else 1.0 / fiber_R
    k = 2.0 * np.pi * refractive_index / wavelength
    beam_alpha = 1.0 / beam_radius**2 + 1j * np.pi * refractive_index * inv_beam_R / wavelength
    fiber_alpha_conj = (
        1.0 / fiber_mode_radius**2 - 1j * np.pi * refractive_index * inv_fiber_R / wavelength
    )
    alpha_sum = beam_alpha + fiber_alpha_conj

    linear_x = 2.0 * beam_alpha * x_offset + 1j * k * x_angle
    linear_y = 2.0 * beam_alpha * y_offset + 1j * k * y_angle
    offset_squared = x_offset**2 + y_offset**2
    exponent = (linear_x**2 + linear_y**2) / (4.0 * alpha_sum) - beam_alpha * offset_squared
    overlap = 2.0 / (beam_radius * fiber_mode_radius * alpha_sum) * np.exp(exponent)
    efficiency = float(abs(overlap) ** 2)
    return min(max(efficiency, 0.0), 1.0)


def transverse_offset_efficiency(
    beam_radius: float,
    fiber_mode_radius: float,
    radial_offset: float,
) -> float:
    """Return approximate Gaussian overlap penalty from transverse offset."""

    if beam_radius <= 0:
        raise ValueError("beam_radius must be positive")
    if fiber_mode_radius <= 0:
        raise ValueError("fiber_mode_radius must be positive")
    if radial_offset < 0:
        raise ValueError("radial_offset must be non-negative")

    return float(math.exp(-2.0 * radial_offset**2 / (beam_radius**2 + fiber_mode_radius**2)))


def sorted_lenses(lenses: Iterable[LensSpec], start_z: float = 0.0) -> list[LensSpec]:
    """Return lenses sorted by position and validate basic layout constraints."""

    _check_finite("start_z", start_z)

    ordered = sorted(lenses, key=lambda lens: lens.position)
    previous_position = start_z
    have_previous_lens = False

    for lens in ordered:
        if lens.position < start_z:
            raise ValueError("lens positions must be at or after start_z")
        if lens.position == previous_position and have_previous_lens:
            raise ValueError("two lenses cannot share the same position")
        if lens.position < previous_position:
            raise ValueError("lens positions must be increasing")
        previous_position = lens.position
        have_previous_lens = True

    return ordered


def build_elements_from_lenses(
    lenses: Iterable[LensSpec],
    final_z: float,
    start_z: float = 0.0,
) -> list[OpticalElement]:
    """Build FreeSpace/ThinLens elements from absolute positioned lenses."""

    _check_finite("final_z", final_z)
    if final_z < start_z:
        raise ValueError("final_z must be at or after start_z")

    ordered = sorted_lenses(lenses, start_z=start_z)
    if ordered and ordered[-1].position > final_z:
        raise ValueError("all lens positions must be at or before final_z")

    elements: list[OpticalElement] = []
    current_z = start_z

    for lens in ordered:
        length = lens.position - current_z
        if length > 0:
            elements.append(FreeSpace(length))
        elements.append(ThinLens(lens.focal_length))
        current_z = lens.position

    final_length = final_z - current_z
    if final_length > 0:
        elements.append(FreeSpace(final_length))

    return elements


def _propagate_center(
    state: BeamCenterState,
    target_z: float,
) -> BeamCenterState:
    distance = target_z - state.z
    if distance < 0:
        raise ValueError("cannot propagate beam center backwards")
    return BeamCenterState(
        z=target_z,
        x=state.x + distance * state.x_angle,
        y=state.y + distance * state.y_angle,
        x_angle=state.x_angle,
        y_angle=state.y_angle,
    )


def _apply_decentered_lens(
    state: BeamCenterState,
    lens: LensSpec,
) -> BeamCenterState:
    return BeamCenterState(
        z=state.z,
        x=state.x,
        y=state.y,
        x_angle=state.x_angle - (state.x - lens.x_offset) / lens.focal_length,
        y_angle=state.y_angle - (state.y - lens.y_offset) / lens.focal_length,
    )


def beam_center_at_z(
    lenses: Iterable[LensSpec],
    z: float,
    laser: LaserAlignment | None = None,
    start_z: float = 0.0,
) -> BeamCenterState:
    """Return beam center at z, applying all lenses at or before z."""

    _check_finite("z", z)
    if z < start_z:
        raise ValueError("z must be at or after start_z")

    alignment = LaserAlignment() if laser is None else laser
    state = BeamCenterState(
        z=start_z,
        x=alignment.x_offset,
        y=alignment.y_offset,
        x_angle=alignment.x_angle,
        y_angle=alignment.y_angle,
    )

    for lens in sorted_lenses(lenses, start_z=start_z):
        if lens.position > z:
            break
        state = _propagate_center(state, lens.position)
        state = _apply_decentered_lens(state, lens)

    return _propagate_center(state, z)


def sample_beam_centroid(
    lenses: Iterable[LensSpec],
    final_z: float,
    start_z: float = 0.0,
    laser: LaserAlignment | None = None,
    z_samples_per_space: int = 200,
) -> CentroidSample:
    """Sample the paraxial beam centerline through the positioned lenses."""

    samples_per_space = int(z_samples_per_space)
    if samples_per_space < 2:
        raise ValueError("z_samples_per_space must be at least 2")
    _check_finite("final_z", final_z)
    if final_z < start_z:
        raise ValueError("final_z must be at or after start_z")

    ordered = sorted_lenses(lenses, start_z=start_z)
    if ordered and ordered[-1].position > final_z:
        raise ValueError("all lens positions must be at or before final_z")

    alignment = LaserAlignment() if laser is None else laser
    state = BeamCenterState(
        z=start_z,
        x=alignment.x_offset,
        y=alignment.y_offset,
        x_angle=alignment.x_angle,
        y_angle=alignment.y_angle,
    )
    z_values = [state.z]
    x_values = [state.x]
    y_values = [state.y]
    x_angle_values = [state.x_angle]
    y_angle_values = [state.y_angle]

    def append_state(new_state: BeamCenterState) -> None:
        z_values.append(new_state.z)
        x_values.append(new_state.x)
        y_values.append(new_state.y)
        x_angle_values.append(new_state.x_angle)
        y_angle_values.append(new_state.y_angle)

    def sample_free_space(target_z: float) -> None:
        nonlocal state
        distance = target_z - state.z
        if distance <= 0:
            return
        offsets = np.linspace(0.0, distance, samples_per_space)
        for offset in offsets[1:]:
            append_state(
                BeamCenterState(
                    z=state.z + float(offset),
                    x=state.x + float(offset) * state.x_angle,
                    y=state.y + float(offset) * state.y_angle,
                    x_angle=state.x_angle,
                    y_angle=state.y_angle,
                )
            )
        state = _propagate_center(state, target_z)

    for lens in ordered:
        sample_free_space(lens.position)
        state = _apply_decentered_lens(state, lens)
        append_state(state)

    sample_free_space(final_z)

    return CentroidSample(
        z=np.asarray(z_values, dtype=float),
        x=np.asarray(x_values, dtype=float),
        y=np.asarray(y_values, dtype=float),
        x_angle=np.asarray(x_angle_values, dtype=float),
        y_angle=np.asarray(y_angle_values, dtype=float),
    )


def _q_at_position(
    beam: GaussianBeam,
    lenses: Iterable[LensSpec],
    position: float,
    start_z: float,
) -> complex:
    q_current = beam.q(start_z)
    current_z = start_z

    for lens in sorted_lenses(lenses, start_z=start_z):
        if lens.position > position:
            break
        q_before_lens = q_current + (lens.position - current_z)
        q_current = abcd_propagate(q_before_lens, ThinLens(lens.focal_length).abcd())
        current_z = lens.position

    return q_current + (position - current_z)


def analyze_lens_apertures(
    beam: GaussianBeam,
    lenses: Iterable[LensSpec],
    start_z: float = 0.0,
    clipping_radius_factor: float = 1.5,
    laser: LaserAlignment | None = None,
) -> list[ApertureReport]:
    """Return clipping estimates at each lens, including upstream lens effects."""

    if clipping_radius_factor <= 0:
        raise ValueError("clipping_radius_factor must be positive")

    ordered = sorted_lenses(lenses, start_z=start_z)
    reports: list[ApertureReport] = []
    q_current = beam.q(start_z)
    current_z = start_z
    alignment = LaserAlignment() if laser is None else laser
    center = BeamCenterState(
        z=start_z,
        x=alignment.x_offset,
        y=alignment.y_offset,
        x_angle=alignment.x_angle,
        y_angle=alignment.y_angle,
    )

    for lens in ordered:
        q_before_lens = q_current + (lens.position - current_z)
        center = _propagate_center(center, lens.position)
        beam_radius = float(q_to_w(q_before_lens, beam.wavelength))
        x_mismatch = center.x - lens.x_offset
        y_mismatch = center.y - lens.y_offset
        transmission = gaussian_aperture_transmission(beam_radius, lens.aperture_radius)
        reports.append(
            ApertureReport(
                lens=lens,
                beam_radius=beam_radius,
                transmission=transmission,
                clipping_threshold_radius=clipping_radius_factor * beam_radius,
                beam_x=center.x,
                beam_y=center.y,
                x_mismatch=x_mismatch,
                y_mismatch=y_mismatch,
            )
        )
        q_current = abcd_propagate(q_before_lens, ThinLens(lens.focal_length).abcd())
        center = _apply_decentered_lens(center, lens)
        current_z = lens.position

    return reports


def analyze_fiber_coupling(
    beam: GaussianBeam,
    lenses: Iterable[LensSpec],
    fiber: FiberSpec,
    start_z: float = 0.0,
    laser: LaserAlignment | None = None,
    refractive_index: float = 1.0,
) -> FiberCouplingReport:
    """Estimate Gaussian coupling into an aligned single-mode fiber."""

    if fiber.position < start_z:
        raise ValueError("fiber position must be at or after start_z")

    ordered = sorted_lenses(lenses, start_z=start_z)
    if ordered and ordered[-1].position > fiber.position:
        raise ValueError("fiber must be at or after the final lens")

    q_at_fiber = _q_at_position(beam, ordered, fiber.position, start_z)
    beam_radius = float(q_to_w(q_at_fiber, beam.wavelength))
    beam_R = float(q_to_R(q_at_fiber))
    center = beam_center_at_z(ordered, fiber.position, laser=laser, start_z=start_z)
    x_mismatch = center.x - fiber.x_offset
    y_mismatch = center.y - fiber.y_offset
    radial_mismatch = math.hypot(x_mismatch, y_mismatch)

    mode_efficiency = mode_overlap_efficiency(
        beam_radius=beam_radius,
        fiber_mode_radius=fiber.mode_radius,
        wavelength=beam.wavelength,
        beam_R=beam_R,
        fiber_R=np.inf,
        refractive_index=refractive_index,
    )
    offset_efficiency = transverse_offset_efficiency(
        beam_radius=beam_radius,
        fiber_mode_radius=fiber.mode_radius,
        radial_offset=radial_mismatch,
    )
    size_only_efficiency = mode_overlap_efficiency(
        beam_radius=beam_radius,
        fiber_mode_radius=fiber.mode_radius,
        wavelength=beam.wavelength,
        refractive_index=refractive_index,
    )
    angle_only_efficiency = gaussian_mode_overlap_efficiency(
        beam_radius=beam_radius,
        fiber_mode_radius=fiber.mode_radius,
        wavelength=beam.wavelength,
        x_angle=center.x_angle,
        y_angle=center.y_angle,
        refractive_index=refractive_index,
    )
    if size_only_efficiency == 0:
        angle_efficiency = 0.0
    else:
        angle_efficiency = angle_only_efficiency / size_only_efficiency
    total_efficiency = gaussian_mode_overlap_efficiency(
        beam_radius=beam_radius,
        fiber_mode_radius=fiber.mode_radius,
        wavelength=beam.wavelength,
        beam_R=beam_R,
        fiber_R=np.inf,
        x_offset=x_mismatch,
        y_offset=y_mismatch,
        x_angle=center.x_angle,
        y_angle=center.y_angle,
        refractive_index=refractive_index,
    )

    return FiberCouplingReport(
        fiber=fiber,
        beam_radius=beam_radius,
        beam_R=beam_R,
        beam_x=center.x,
        beam_y=center.y,
        beam_x_angle=center.x_angle,
        beam_y_angle=center.y_angle,
        x_mismatch=x_mismatch,
        y_mismatch=y_mismatch,
        mode_efficiency=mode_efficiency,
        offset_efficiency=offset_efficiency,
        angle_efficiency=angle_efficiency,
        combined_efficiency=total_efficiency,
    )


def q_after_positioned_lenses(
    beam: GaussianBeam,
    lenses: Iterable[LensSpec],
    start_z: float = 0.0,
) -> tuple[complex, float]:
    """Return q immediately after the last lens and that lens position.

    With no lenses, the returned q is at ``start_z``.
    """

    ordered = sorted_lenses(lenses, start_z=start_z)
    q_current = beam.q(start_z)
    current_z = start_z

    for lens in ordered:
        q_before_lens = q_current + (lens.position - current_z)
        q_current = abcd_propagate(q_before_lens, ThinLens(lens.focal_length).abcd())
        current_z = lens.position

    return q_current, current_z
