"""Interactive optical layout editor for the Gaussian beam simulator.

Run this file directly:

    python interactive_setup.py

The default setup is an 808 nm elliptical Gaussian waist coupled through two
500 um sapphire ball lenses into a SiN inverse taper mode. The Simulate button
runs first-order paraxial propagation and reports taper mode overlap plus the
separate taper transmission factor.
"""

from __future__ import annotations

import math
import random
import tkinter as tk
from copy import deepcopy
from dataclasses import dataclass, field
from itertools import count
from tkinter import messagebox, ttk
from typing import Any

import numpy as np

from beam import GaussianBeam, q_to_R, q_to_w
from layout import (
    ApertureReport,
    FiberCouplingReport,
    FiberSpec,
    LaserAlignment,
    LensSpec,
    analyze_fiber_coupling,
    analyze_lens_apertures,
    build_elements_from_lenses,
    sample_beam_centroid,
)
from system import abcd_propagate, sample_system


_UID_COUNTER = count(1)
DEFAULT_REFRACTIVE_INDEX = 1.0
DEFAULT_CLIPPING_RADIUS_FACTOR = 1.5
DEFAULT_WAVELENGTH = 0.808e-6
DEFAULT_SOURCE_POWER = 300e-3
DEFAULT_MODE_RADIUS_X = 1.24e-6
DEFAULT_MODE_RADIUS_Y = 2.899e-6
SAPPHIRE_REFRACTIVE_INDEX = 1.760
DEFAULT_BALL_DIAMETER = 500e-6
DEFAULT_TAPER_PHYSICAL_WIDTH = 0.2e-6
DEFAULT_TAPER_PHYSICAL_THICKNESS = 0.2e-6
DEFAULT_TAPER_EXTRA_TRANSMISSION = 0.40
DEFAULT_TAPER_FACET_REFRACTIVE_INDEX = 2.0
DEFAULT_BALL1_FRONT_GAP = 39e-6
DEFAULT_BALL_GAP = 200e-6
DEFAULT_BALL2_TAPER_GAP = 39e-6
# Maximum random offsets applied relative to the stored nominal (perfectly aligned) pose.
AXIAL_TOLERANCE = 5e-6
TRANSVERSE_TOLERANCE = 50e-6


def _new_uid(prefix: str) -> str:
    return f"{prefix}-{next(_UID_COUNTER)}"


def _rayleigh_range_from_waist(wavelength: float, waist_radius: float) -> float:
    return math.pi * waist_radius**2 / wavelength


def _waist_radius_from_rayleigh(wavelength: float, rayleigh_range: float) -> float:
    return math.sqrt(wavelength * rayleigh_range / math.pi)


def fresnel_reflection_loss(n1: float, n2: float) -> float:
    if n1 <= 0 or n2 <= 0:
        raise ValueError("refractive indices must be positive")
    return float(((n1 - n2) / (n1 + n2)) ** 2)


def fresnel_power_transmission(n1: float, n2: float) -> float:
    return 1.0 - fresnel_reflection_loss(n1, n2)


@dataclass
class LaserSource:
    uid: str = field(default_factory=lambda: _new_uid("laser"))
    name: str = "Elliptical Gaussian laser waist"
    position: float = 0.0
    wavelength: float = DEFAULT_WAVELENGTH
    waist_radius: float = DEFAULT_MODE_RADIUS_X
    rayleigh_range: float | None = None
    waist_radius_y: float | None = DEFAULT_MODE_RADIUS_Y
    rayleigh_range_y: float | None = None
    waist_position: float = 0.0
    power: float = DEFAULT_SOURCE_POWER
    x_offset: float = 0.0
    y_offset: float = 0.0
    x_angle: float = 0.0
    y_angle: float = 0.0

    def __post_init__(self) -> None:
        if self.wavelength <= 0:
            raise ValueError("wavelength must be positive")
        if self.waist_radius_y is None:
            self.waist_radius_y = self.waist_radius
        if self.rayleigh_range is None:
            if self.waist_radius <= 0:
                raise ValueError("waist radius must be positive")
            self.rayleigh_range = _rayleigh_range_from_waist(self.wavelength, self.waist_radius)
        else:
            if self.rayleigh_range <= 0:
                raise ValueError("Rayleigh length must be positive")
            self.waist_radius = _waist_radius_from_rayleigh(self.wavelength, self.rayleigh_range)
        if self.rayleigh_range_y is None:
            if self.waist_radius_y <= 0:
                raise ValueError("y waist radius must be positive")
            self.rayleigh_range_y = _rayleigh_range_from_waist(self.wavelength, self.waist_radius_y)
        else:
            if self.rayleigh_range_y <= 0:
                raise ValueError("y Rayleigh length must be positive")
            self.waist_radius_y = _waist_radius_from_rayleigh(self.wavelength, self.rayleigh_range_y)

    @property
    def kind(self) -> str:
        return "laser"


@dataclass
class LensElement:
    uid: str = field(default_factory=lambda: _new_uid("lens"))
    name: str = "Lens"
    position: float = 0.05
    focal_length: float = 0.05
    aperture_radius: float = 12.5e-3
    x_offset: float = 0.0
    y_offset: float = 0.0

    @property
    def kind(self) -> str:
        return "lens"


@dataclass
class BallLensElement:
    uid: str = field(default_factory=lambda: _new_uid("ball"))
    name: str = "Sapphire ball lens"
    position: float = 0.0
    diameter: float = DEFAULT_BALL_DIAMETER
    refractive_index: float = SAPPHIRE_REFRACTIVE_INDEX
    x_offset: float = 0.0
    y_offset: float = 0.0

    @property
    def kind(self) -> str:
        return "ball"

    @property
    def radius(self) -> float:
        return 0.5 * self.diameter

    @property
    def entry_z(self) -> float:
        return self.position - self.radius

    @property
    def exit_z(self) -> float:
        return self.position + self.radius

    @property
    def effective_focal_length(self) -> float:
        return self.refractive_index * self.diameter / (4.0 * (self.refractive_index - 1.0))


@dataclass
class FiberElement:
    uid: str = field(default_factory=lambda: _new_uid("fiber"))
    name: str = "Corning SMF-28-style @ 1550 nm"
    position: float = 0.28
    mode_field_diameter: float = 10.4e-6
    cladding_diameter: float = 125e-6
    x_offset: float = 0.0
    y_offset: float = 0.0
    received_power: float = 0.0

    @property
    def kind(self) -> str:
        return "fiber"

    @property
    def mode_radius(self) -> float:
        return 0.5 * self.mode_field_diameter

    @property
    def cladding_radius(self) -> float:
        return 0.5 * self.cladding_diameter


@dataclass
class TaperDetectorElement:
    uid: str = field(default_factory=lambda: _new_uid("taper"))
    name: str = "SiN inverse taper"
    position: float = 0.0
    width: float = DEFAULT_TAPER_PHYSICAL_WIDTH
    height: float = DEFAULT_TAPER_PHYSICAL_THICKNESS
    mode_radius_x: float = DEFAULT_MODE_RADIUS_X
    mode_radius_y: float = DEFAULT_MODE_RADIUS_Y
    extra_transmission: float = DEFAULT_TAPER_EXTRA_TRANSMISSION
    facet_refractive_index: float = DEFAULT_TAPER_FACET_REFRACTIVE_INDEX
    x_offset: float = 0.0
    y_offset: float = 0.0
    received_power: float = 0.0

    @property
    def kind(self) -> str:
        return "taper"


@dataclass(frozen=True)
class AstigmaticBeamState:
    z: float
    q_x: complex
    q_y: complex
    x: float
    y: float
    x_angle: float
    y_angle: float


@dataclass(frozen=True)
class BallLensReport:
    ball: BallLensElement
    status: str
    beam_x: float
    beam_y: float
    x_mismatch: float
    y_mismatch: float
    beam_radius_x: float
    beam_radius_y: float
    transmission: float
    aperture_transmission: float = 1.0
    reflection_transmission: float = 1.0
    entry_reflection_loss: float = 0.0
    exit_reflection_loss: float = 0.0

    @property
    def radial_mismatch(self) -> float:
        return math.hypot(self.x_mismatch, self.y_mismatch)

    @property
    def missed(self) -> bool:
        return self.status == "MISS"


@dataclass(frozen=True)
class TaperSimulationResult:
    source: LaserSource
    taper: TaperDetectorElement
    ball_reports: list[BallLensReport]
    beam_radius_x: float | None
    beam_radius_y: float | None
    beam_R_x: float | None
    beam_R_y: float | None
    beam_x: float | None
    beam_y: float | None
    beam_x_angle: float | None
    beam_y_angle: float | None
    x_mismatch: float | None
    y_mismatch: float | None
    mode_efficiency: float
    extra_transmission: float
    taper_reflection_transmission: float
    ball_reflection_transmission: float
    aperture_transmission: float
    received_power: float
    warnings: list[str]


@dataclass(frozen=True)
class FiberSimulationResult:
    source: LaserSource
    fiber: FiberElement
    lens_reports: list[ApertureReport]
    coupling_report: FiberCouplingReport | None
    aperture_transmission: float
    received_power: float
    warnings: list[str]


@dataclass(frozen=True)
class SourceSimulationResult:
    source: LaserSource
    lens_reports: list[ApertureReport]
    fiber_results: list[FiberSimulationResult]
    final_beam_radius: float | None
    final_beam_R: float | None
    final_beam_radius_y: float | None
    final_beam_R_y: float | None
    aperture_transmission: float
    warnings: list[str]
    ball_reports: list[BallLensReport] = field(default_factory=list)
    taper_results: list[TaperSimulationResult] = field(default_factory=list)


@dataclass(frozen=True)
class BeamPathDisplay:
    source_uid: str
    source_name: str
    z: list[float]
    x: list[float]
    w: list[float]
    waist_z: float
    waist_x: float
    waist_radius: float
    color: str


@dataclass(frozen=True)
class FieldSpec:
    attr: str
    label: str
    unit: str = ""
    scale: float = 1.0
    is_text: bool = False


_LASER_FIELDS = [
    FieldSpec("name", "Name", is_text=True),
    FieldSpec("position", "z position", "um", 1e6),
    FieldSpec("wavelength", "wavelength", "um", 1e6),
    FieldSpec("waist_radius", "x waist radius", "um", 1e6),
    FieldSpec("rayleigh_range", "x Rayleigh length", "um", 1e6),
    FieldSpec("waist_radius_y", "y waist radius", "um", 1e6),
    FieldSpec("rayleigh_range_y", "y Rayleigh length", "um", 1e6),
    FieldSpec("waist_position", "waist z position", "um", 1e6),
    FieldSpec("power", "source power", "mW", 1e3),
    FieldSpec("x_offset", "x offset", "um", 1e6),
    FieldSpec("y_offset", "y offset", "um", 1e6),
    FieldSpec("x_angle", "x angle", "mrad", 1e3),
    FieldSpec("y_angle", "y angle", "mrad", 1e3),
]

_LENS_FIELDS = [
    FieldSpec("name", "Name", is_text=True),
    FieldSpec("position", "z position", "um", 1e6),
    FieldSpec("focal_length", "focal length", "um", 1e6),
    FieldSpec("aperture_radius", "aperture radius", "um", 1e6),
    FieldSpec("x_offset", "x offset", "um", 1e6),
    FieldSpec("y_offset", "y offset", "um", 1e6),
]

_BALL_FIELDS = [
    FieldSpec("name", "Name", is_text=True),
    FieldSpec("position", "center z position", "um", 1e6),
    FieldSpec("diameter", "diameter", "um", 1e6),
    FieldSpec("refractive_index", "refractive index"),
    FieldSpec("x_offset", "x center offset", "um", 1e6),
    FieldSpec("y_offset", "y center offset", "um", 1e6),
]

_FIBER_FIELDS = [
    FieldSpec("name", "Name", is_text=True),
    FieldSpec("position", "z position", "um", 1e6),
    FieldSpec("mode_field_diameter", "mode-field diameter", "um", 1e6),
    FieldSpec("cladding_diameter", "cladding diameter", "um", 1e6),
    FieldSpec("x_offset", "x offset", "um", 1e6),
    FieldSpec("y_offset", "y offset", "um", 1e6),
    FieldSpec("received_power", "received power placeholder", "mW", 1e3),
]

_TAPER_FIELDS = [
    FieldSpec("name", "Name", is_text=True),
    FieldSpec("position", "z position", "um", 1e6),
    FieldSpec("width", "physical width", "um", 1e6),
    FieldSpec("height", "physical thickness", "um", 1e6),
    FieldSpec("mode_radius_x", "mode radius x", "um", 1e6),
    FieldSpec("mode_radius_y", "mode radius y", "um", 1e6),
    FieldSpec("extra_transmission", "extra taper transmission"),
    FieldSpec("facet_refractive_index", "facet refractive index"),
    FieldSpec("x_offset", "x offset", "um", 1e6),
    FieldSpec("y_offset", "y offset", "um", 1e6),
    FieldSpec("received_power", "received power", "mW", 1e3),
]


def source_to_beam(source: LaserSource) -> GaussianBeam:
    if source.rayleigh_range is None:
        source.rayleigh_range = _rayleigh_range_from_waist(source.wavelength, source.waist_radius)
    return GaussianBeam(
        wavelength=source.wavelength,
        w0=source.waist_radius,
        z0=source.waist_position,
    )


def source_to_alignment(source: LaserSource) -> LaserAlignment:
    return LaserAlignment(
        x_offset=source.x_offset,
        y_offset=source.y_offset,
        x_angle=source.x_angle,
        y_angle=source.y_angle,
    )


def source_q_x(source: LaserSource, z: float | None = None) -> complex:
    z_value = source.position if z is None else z
    return (z_value - source.waist_position) + 1j * source.rayleigh_range


def source_q_y(source: LaserSource, z: float | None = None) -> complex:
    z_value = source.position if z is None else z
    return (z_value - source.waist_position) + 1j * source.rayleigh_range_y


def _ball_lens_matrix_terms(ball: BallLensElement) -> tuple[float, float, float, float]:
    n = ball.refractive_index
    diameter = ball.diameter
    a = (2.0 - n) / n
    b = diameter / n
    c = -4.0 * (n - 1.0) / (diameter * n)
    return a, b, c, a


def ball_lens_matrix(ball: BallLensElement) -> tuple[tuple[float, float], tuple[float, float]]:
    a, b, c, d = _ball_lens_matrix_terms(ball)
    return ((a, b), (c, d))


def ball_lens_effective_focal_length(diameter: float, refractive_index: float) -> float:
    return refractive_index * diameter / (4.0 * (refractive_index - 1.0))


def _propagate_astigmatic_free_space(state: AstigmaticBeamState, target_z: float) -> AstigmaticBeamState:
    distance = target_z - state.z
    if distance < -1e-15:
        raise ValueError("cannot propagate astigmatic beam backwards")
    distance = max(distance, 0.0)
    return AstigmaticBeamState(
        z=target_z,
        q_x=state.q_x + distance,
        q_y=state.q_y + distance,
        x=state.x + distance * state.x_angle,
        y=state.y + distance * state.y_angle,
        x_angle=state.x_angle,
        y_angle=state.y_angle,
    )


def _apply_ball_matrix_to_state(state: AstigmaticBeamState, ball: BallLensElement) -> AstigmaticBeamState:
    matrix = ball_lens_matrix(ball)
    a, b, c, d = _ball_lens_matrix_terms(ball)
    x_rel = state.x - ball.x_offset
    y_rel = state.y - ball.y_offset
    return AstigmaticBeamState(
        z=ball.exit_z,
        q_x=abcd_propagate(state.q_x, matrix),
        q_y=abcd_propagate(state.q_y, matrix),
        x=ball.x_offset + a * x_rel + b * state.x_angle,
        y=ball.y_offset + a * y_rel + b * state.y_angle,
        x_angle=c * x_rel + d * state.x_angle,
        y_angle=c * y_rel + d * state.y_angle,
    )


def _initial_astigmatic_state(source: LaserSource) -> AstigmaticBeamState:
    return AstigmaticBeamState(
        z=source.position,
        q_x=source_q_x(source),
        q_y=source_q_y(source),
        x=source.x_offset,
        y=source.y_offset,
        x_angle=source.x_angle,
        y_angle=source.y_angle,
    )


def _append_astigmatic_free_space_samples(
    z_values: list[float],
    x_values: list[float],
    y_values: list[float],
    wx_values: list[float],
    wy_values: list[float],
    state: AstigmaticBeamState,
    target_z: float,
    wavelength: float,
    sample_count: int,
) -> None:
    distance = target_z - state.z
    if distance <= 0:
        return
    steps = max(sample_count, 2)
    for index in range(1, steps + 1):
        offset = distance * index / steps
        q_x = state.q_x + offset
        q_y = state.q_y + offset
        z_values.append(state.z + offset)
        x_values.append(state.x + offset * state.x_angle)
        y_values.append(state.y + offset * state.y_angle)
        wx_values.append(float(q_to_w(q_x, wavelength)))
        wy_values.append(float(q_to_w(q_y, wavelength)))


def propagate_astigmatic_through_balls(
    source: LaserSource,
    balls: list[BallLensElement],
    target_z: float,
    clipping_radius_factor: float = DEFAULT_CLIPPING_RADIUS_FACTOR,
    samples_per_space: int = 0,
) -> tuple[AstigmaticBeamState, list[BallLensReport], bool, tuple[list[float], list[float], list[float], list[float], list[float]]]:
    if target_z < source.position:
        raise ValueError("target z is before this source")
    if clipping_radius_factor <= 0:
        raise ValueError("clipping_radius_factor must be positive")

    state = _initial_astigmatic_state(source)
    reports: list[BallLensReport] = []
    missed_ball = False
    z_values = [state.z]
    x_values = [state.x]
    y_values = [state.y]
    wx_values = [float(q_to_w(state.q_x, source.wavelength))]
    wy_values = [float(q_to_w(state.q_y, source.wavelength))]

    for ball in sorted(balls, key=lambda item: item.position):
        if ball.exit_z > target_z:
            break
        if ball.entry_z < state.z - 1e-15:
            raise ValueError(f"{ball.name} overlaps a previous optic or starts before the source")

        if samples_per_space:
            _append_astigmatic_free_space_samples(
                z_values,
                x_values,
                y_values,
                wx_values,
                wy_values,
                state,
                ball.entry_z,
                source.wavelength,
                samples_per_space,
            )
        state = _propagate_astigmatic_free_space(state, ball.entry_z)

        beam_radius_x = float(q_to_w(state.q_x, source.wavelength))
        beam_radius_y = float(q_to_w(state.q_y, source.wavelength))
        x_mismatch = state.x - ball.x_offset
        y_mismatch = state.y - ball.y_offset
        radial_mismatch = math.hypot(x_mismatch, y_mismatch)
        max_beam_radius = max(beam_radius_x, beam_radius_y)

        if radial_mismatch > ball.radius:
            status = "MISS"
            transmission = 1.0
            missed_ball = True
            reports.append(
                BallLensReport(
                    ball=ball,
                    status=status,
                    beam_x=state.x,
                    beam_y=state.y,
                    x_mismatch=x_mismatch,
                    y_mismatch=y_mismatch,
                    beam_radius_x=beam_radius_x,
                    beam_radius_y=beam_radius_y,
                    transmission=transmission,
                    aperture_transmission=1.0,
                    reflection_transmission=1.0,
                    entry_reflection_loss=0.0,
                    exit_reflection_loss=0.0,
                )
            )
            if samples_per_space:
                _append_astigmatic_free_space_samples(
                    z_values,
                    x_values,
                    y_values,
                    wx_values,
                    wy_values,
                    state,
                    ball.exit_z,
                    source.wavelength,
                    samples_per_space,
                )
            state = _propagate_astigmatic_free_space(state, ball.exit_z)
            continue

        status = "CLIPPING" if radial_mismatch + clipping_radius_factor * max_beam_radius > ball.radius else "OK"
        clearance_radius = max(ball.radius - radial_mismatch, 1e-15)
        aperture_transmission = 1.0 if status == "OK" else gaussian_aperture_estimate(max_beam_radius, clearance_radius)
        entry_reflection_loss = fresnel_reflection_loss(DEFAULT_REFRACTIVE_INDEX, ball.refractive_index)
        exit_reflection_loss = fresnel_reflection_loss(ball.refractive_index, DEFAULT_REFRACTIVE_INDEX)
        reflection_transmission = (1.0 - entry_reflection_loss) * (1.0 - exit_reflection_loss)
        transmission = aperture_transmission * reflection_transmission
        reports.append(
            BallLensReport(
                ball=ball,
                status=status,
                beam_x=state.x,
                beam_y=state.y,
                x_mismatch=x_mismatch,
                y_mismatch=y_mismatch,
                beam_radius_x=beam_radius_x,
                beam_radius_y=beam_radius_y,
                transmission=transmission,
                aperture_transmission=aperture_transmission,
                reflection_transmission=reflection_transmission,
                entry_reflection_loss=entry_reflection_loss,
                exit_reflection_loss=exit_reflection_loss,
            )
        )
        state = _apply_ball_matrix_to_state(state, ball)
        if samples_per_space:
            z_values.append(state.z)
            x_values.append(state.x)
            y_values.append(state.y)
            wx_values.append(float(q_to_w(state.q_x, source.wavelength)))
            wy_values.append(float(q_to_w(state.q_y, source.wavelength)))

    if samples_per_space:
        _append_astigmatic_free_space_samples(
            z_values,
            x_values,
            y_values,
            wx_values,
            wy_values,
            state,
            target_z,
            source.wavelength,
            samples_per_space,
        )
    state = _propagate_astigmatic_free_space(state, target_z)
    if not samples_per_space or z_values[-1] != target_z:
        z_values.append(state.z)
        x_values.append(state.x)
        y_values.append(state.y)
        wx_values.append(float(q_to_w(state.q_x, source.wavelength)))
        wy_values.append(float(q_to_w(state.q_y, source.wavelength)))

    return state, reports, missed_ball, (z_values, x_values, y_values, wx_values, wy_values)


def gaussian_aperture_estimate(beam_radius: float, aperture_radius: float) -> float:
    if beam_radius <= 0:
        raise ValueError("beam_radius must be positive")
    if aperture_radius <= 0:
        raise ValueError("aperture_radius must be positive")
    return float(1.0 - math.exp(-2.0 * aperture_radius**2 / beam_radius**2))


def elliptical_gaussian_mode_overlap_efficiency(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    beam_radius_x: float,
    beam_radius_y: float,
    mode_radius_x: float,
    mode_radius_y: float,
    wavelength: float,
    beam_R_x: float = math.inf,
    beam_R_y: float = math.inf,
    mode_R_x: float = math.inf,
    mode_R_y: float = math.inf,
    x_offset: float = 0.0,
    y_offset: float = 0.0,
    x_angle: float = 0.0,
    y_angle: float = 0.0,
    refractive_index: float = DEFAULT_REFRACTIVE_INDEX,
) -> float:
    if beam_radius_x <= 0 or beam_radius_y <= 0:
        raise ValueError("beam radii must be positive")
    if mode_radius_x <= 0 or mode_radius_y <= 0:
        raise ValueError("mode radii must be positive")
    if wavelength <= 0:
        raise ValueError("wavelength must be positive")
    if refractive_index <= 0:
        raise ValueError("refractive_index must be positive")

    k = 2.0 * math.pi * refractive_index / wavelength

    def axis_amplitude(
        beam_radius: float,
        mode_radius: float,
        beam_R: float,
        mode_R: float,
        offset: float,
        angle: float,
    ) -> complex:
        inv_beam_R = 0.0 if math.isinf(beam_R) else 1.0 / beam_R
        inv_mode_R = 0.0 if math.isinf(mode_R) else 1.0 / mode_R
        beam_alpha = 1.0 / beam_radius**2 + 1j * math.pi * refractive_index * inv_beam_R / wavelength
        mode_alpha_conj = 1.0 / mode_radius**2 - 1j * math.pi * refractive_index * inv_mode_R / wavelength
        alpha_sum = beam_alpha + mode_alpha_conj
        linear = 2.0 * beam_alpha * offset + 1j * k * angle
        exponent = linear**2 / (4.0 * alpha_sum) - beam_alpha * offset**2
        return complex(math.sqrt(2.0 / (beam_radius * mode_radius))) / np.sqrt(alpha_sum) * np.exp(exponent)

    amplitude = axis_amplitude(
        beam_radius_x,
        mode_radius_x,
        beam_R_x,
        mode_R_x,
        x_offset,
        x_angle,
    ) * axis_amplitude(
        beam_radius_y,
        mode_radius_y,
        beam_R_y,
        mode_R_y,
        y_offset,
        y_angle,
    )
    return min(max(float(abs(amplitude) ** 2), 0.0), 1.0)


def lens_to_spec(lens: LensElement) -> LensSpec:
    return LensSpec(
        position=lens.position,
        focal_length=lens.focal_length,
        aperture_radius=lens.aperture_radius,
        x_offset=lens.x_offset,
        y_offset=lens.y_offset,
    )


def fiber_to_spec(fiber: FiberElement) -> FiberSpec:
    return FiberSpec(
        position=fiber.position,
        mode_field_diameter=fiber.mode_field_diameter,
        x_offset=fiber.x_offset,
        y_offset=fiber.y_offset,
        name=fiber.name,
        cladding_diameter=fiber.cladding_diameter,
    )


def lens_specs_between(
    lenses: list[LensElement],
    start_z: float,
    end_z: float,
) -> list[LensSpec]:
    return [
        lens_to_spec(lens)
        for lens in sorted(lenses, key=lambda item: item.position)
        if start_z <= lens.position <= end_z
    ]


def aperture_transmission_product(reports: list[ApertureReport]) -> float:
    transmission = 1.0
    for report in reports:
        transmission *= report.transmission
    return transmission


def simulate_source_to_fiber(
    source: LaserSource,
    lenses: list[LensElement],
    fiber: FiberElement,
    refractive_index: float = DEFAULT_REFRACTIVE_INDEX,
    clipping_radius_factor: float = DEFAULT_CLIPPING_RADIUS_FACTOR,
) -> FiberSimulationResult:
    if fiber.position < source.position:
        return FiberSimulationResult(
            source=source,
            fiber=fiber,
            lens_reports=[],
            coupling_report=None,
            aperture_transmission=0.0,
            received_power=0.0,
            warnings=["fiber is before this source"],
        )

    beam = source_to_beam(source)
    alignment = source_to_alignment(source)
    fiber_spec = fiber_to_spec(fiber)
    usable_lenses = lens_specs_between(lenses, source.position, fiber.position)

    try:
        lens_reports = analyze_lens_apertures(
            beam,
            usable_lenses,
            start_z=source.position,
            clipping_radius_factor=clipping_radius_factor,
            laser=alignment,
        )
        coupling_report = analyze_fiber_coupling(
            beam,
            usable_lenses,
            fiber_spec,
            start_z=source.position,
            laser=alignment,
            refractive_index=refractive_index,
        )
    except ValueError as exc:
        return FiberSimulationResult(
            source=source,
            fiber=fiber,
            lens_reports=[],
            coupling_report=None,
            aperture_transmission=0.0,
            received_power=0.0,
            warnings=[str(exc)],
        )

    aperture_transmission = aperture_transmission_product(lens_reports)
    received_power = source.power * aperture_transmission * coupling_report.total_efficiency
    warnings = [f"clipping risk at {report.lens.position * 1e6:.3g} um" for report in lens_reports if report.clips]

    return FiberSimulationResult(
        source=source,
        fiber=fiber,
        lens_reports=lens_reports,
        coupling_report=coupling_report,
        aperture_transmission=aperture_transmission,
        received_power=received_power,
        warnings=warnings,
    )


def simulate_source_to_taper(
    source: LaserSource,
    balls: list[BallLensElement],
    taper: TaperDetectorElement,
    clipping_radius_factor: float = DEFAULT_CLIPPING_RADIUS_FACTOR,
) -> TaperSimulationResult:
    if taper.position < source.position:
        return TaperSimulationResult(
            source=source,
            taper=taper,
            ball_reports=[],
            beam_radius_x=None,
            beam_radius_y=None,
            beam_R_x=None,
            beam_R_y=None,
            beam_x=None,
            beam_y=None,
            beam_x_angle=None,
            beam_y_angle=None,
            x_mismatch=None,
            y_mismatch=None,
            mode_efficiency=0.0,
            extra_transmission=taper.extra_transmission,
            taper_reflection_transmission=0.0,
            ball_reflection_transmission=0.0,
            aperture_transmission=0.0,
            received_power=0.0,
            warnings=["taper detector is before this source"],
        )

    usable_balls = [
        ball
        for ball in sorted(balls, key=lambda item: item.position)
        if ball.exit_z <= taper.position and ball.entry_z >= source.position
    ]

    try:
        state, ball_reports, missed_ball, _path = propagate_astigmatic_through_balls(
            source,
            usable_balls,
            taper.position,
            clipping_radius_factor=clipping_radius_factor,
        )
        beam_radius_x = float(q_to_w(state.q_x, source.wavelength))
        beam_radius_y = float(q_to_w(state.q_y, source.wavelength))
        beam_R_x = float(q_to_R(state.q_x))
        beam_R_y = float(q_to_R(state.q_y))
        mode_efficiency = elliptical_gaussian_mode_overlap_efficiency(
            beam_radius_x=beam_radius_x,
            beam_radius_y=beam_radius_y,
            mode_radius_x=taper.mode_radius_x,
            mode_radius_y=taper.mode_radius_y,
            wavelength=source.wavelength,
            beam_R_x=beam_R_x,
            beam_R_y=beam_R_y,
            x_offset=state.x - taper.x_offset,
            y_offset=state.y - taper.y_offset,
            x_angle=state.x_angle,
            y_angle=state.y_angle,
        )
    except ValueError as exc:
        return TaperSimulationResult(
            source=source,
            taper=taper,
            ball_reports=[],
            beam_radius_x=None,
            beam_radius_y=None,
            beam_R_x=None,
            beam_R_y=None,
            beam_x=None,
            beam_y=None,
            beam_x_angle=None,
            beam_y_angle=None,
            x_mismatch=None,
            y_mismatch=None,
            mode_efficiency=0.0,
            extra_transmission=taper.extra_transmission,
            taper_reflection_transmission=0.0,
            ball_reflection_transmission=0.0,
            aperture_transmission=0.0,
            received_power=0.0,
            warnings=[str(exc)],
        )

    aperture_transmission = 1.0
    ball_reflection_transmission = 1.0
    warnings: list[str] = []
    for report in ball_reports:
        aperture_transmission *= report.aperture_transmission
        ball_reflection_transmission *= report.reflection_transmission
        if report.status == "MISS":
            warnings.append(f"{report.ball.name} MISS")
        elif report.status == "CLIPPING":
            warnings.append(f"{report.ball.name} CLIPPING")

    taper_reflection_transmission = fresnel_power_transmission(
        DEFAULT_REFRACTIVE_INDEX,
        taper.facet_refractive_index,
    )
    received_power = (
        source.power
        * aperture_transmission
        * ball_reflection_transmission
        * taper_reflection_transmission
        * mode_efficiency
        * taper.extra_transmission
    )
    return TaperSimulationResult(
        source=source,
        taper=taper,
        ball_reports=ball_reports,
        beam_radius_x=beam_radius_x,
        beam_radius_y=beam_radius_y,
        beam_R_x=beam_R_x,
        beam_R_y=beam_R_y,
        beam_x=state.x,
        beam_y=state.y,
        beam_x_angle=state.x_angle,
        beam_y_angle=state.y_angle,
        x_mismatch=state.x - taper.x_offset,
        y_mismatch=state.y - taper.y_offset,
        mode_efficiency=mode_efficiency,
        extra_transmission=taper.extra_transmission,
        taper_reflection_transmission=taper_reflection_transmission,
        ball_reflection_transmission=ball_reflection_transmission,
        aperture_transmission=aperture_transmission,
        received_power=received_power,
        warnings=warnings,
    )


def simulate_source(
    source: LaserSource,
    lenses: list[LensElement],
    fibers: list[FiberElement],
    final_z: float,
    balls: list[BallLensElement] | None = None,
    tapers: list[TaperDetectorElement] | None = None,
    refractive_index: float = DEFAULT_REFRACTIVE_INDEX,
    clipping_radius_factor: float = DEFAULT_CLIPPING_RADIUS_FACTOR,
) -> SourceSimulationResult:
    beam = source_to_beam(source)
    alignment = source_to_alignment(source)
    warnings: list[str] = []
    lens_reports: list[ApertureReport] = []
    final_beam_radius: float | None = None
    final_beam_R: float | None = None
    final_beam_radius_y: float | None = None
    final_beam_R_y: float | None = None

    if final_z < source.position:
        warnings.append("final z is before this source")
    else:
        try:
            if balls:
                final_state, _ball_reports, _missed_ball, _path = propagate_astigmatic_through_balls(
                    source,
                    balls,
                    final_z,
                    clipping_radius_factor=clipping_radius_factor,
                )
                final_beam_radius = float(q_to_w(final_state.q_x, source.wavelength))
                final_beam_R = float(q_to_R(final_state.q_x))
                final_beam_radius_y = float(q_to_w(final_state.q_y, source.wavelength))
                final_beam_R_y = float(q_to_R(final_state.q_y))
            else:
                final_lenses = lens_specs_between(lenses, source.position, final_z)
                lens_reports = analyze_lens_apertures(
                    beam,
                    final_lenses,
                    start_z=source.position,
                    clipping_radius_factor=clipping_radius_factor,
                    laser=alignment,
                )
                elements = build_elements_from_lenses(final_lenses, final_z=final_z, start_z=source.position)
                sample = sample_system(beam, elements, z_samples_per_space=2, start_z=source.position)
                final_q = sample.q[-1]
                final_beam_radius = float(q_to_w(final_q, source.wavelength))
                final_beam_R = float(q_to_R(final_q))
                final_beam_radius_y = final_beam_radius
                final_beam_R_y = final_beam_R
        except ValueError as exc:
            warnings.append(str(exc))

    fiber_results = [
        simulate_source_to_fiber(
            source,
            lenses,
            fiber,
            refractive_index=refractive_index,
            clipping_radius_factor=clipping_radius_factor,
        )
        for fiber in sorted(fibers, key=lambda item: item.position)
    ]
    taper_results = [
        simulate_source_to_taper(
            source,
            balls or [],
            taper,
            clipping_radius_factor=clipping_radius_factor,
        )
        for taper in sorted(tapers or [], key=lambda item: item.position)
    ]
    ball_reports = taper_results[0].ball_reports if taper_results else []

    warnings.extend(f"clipping risk at {report.lens.position * 1e6:.3g} um" for report in lens_reports if report.clips)
    return SourceSimulationResult(
        source=source,
        lens_reports=lens_reports,
        fiber_results=fiber_results,
        final_beam_radius=final_beam_radius,
        final_beam_R=final_beam_R,
        final_beam_radius_y=final_beam_radius_y,
        final_beam_R_y=final_beam_R_y,
        aperture_transmission=aperture_transmission_product(lens_reports),
        warnings=warnings,
        ball_reports=ball_reports,
        taper_results=taper_results,
    )


def simulate_layout(
    sources: list[LaserSource],
    lenses: list[LensElement],
    fibers: list[FiberElement],
    final_z: float,
    balls: list[BallLensElement] | None = None,
    tapers: list[TaperDetectorElement] | None = None,
    refractive_index: float = DEFAULT_REFRACTIVE_INDEX,
    clipping_radius_factor: float = DEFAULT_CLIPPING_RADIUS_FACTOR,
) -> list[SourceSimulationResult]:
    return [
        simulate_source(
            source,
            lenses,
            fibers,
            final_z,
            balls=balls,
            tapers=tapers,
            refractive_index=refractive_index,
            clipping_radius_factor=clipping_radius_factor,
        )
        for source in sorted(sources, key=lambda item: item.position)
    ]


def _format_curvature(radius: float | None) -> str:
    if radius is None:
        return "n/a"
    if math.isinf(radius):
        return "inf"
    return f"{radius * 1e3:.4g} mm"


def _all_fiber_results(results: list[SourceSimulationResult]) -> list[FiberSimulationResult]:
    return [fiber_result for result in results for fiber_result in result.fiber_results]


def _all_taper_results(results: list[SourceSimulationResult]) -> list[TaperSimulationResult]:
    return [taper_result for result in results for taper_result in result.taper_results]


def format_simulation_report(results: list[SourceSimulationResult]) -> str:
    fiber_results = _all_fiber_results(results)
    taper_results = _all_taper_results(results)
    valid_results = [fiber_result for fiber_result in fiber_results if fiber_result.coupling_report is not None]
    valid_tapers = [taper_result for taper_result in taper_results if taper_result.beam_radius_x is not None]
    lines = [
        "Elliptical Gaussian propagation and SiN taper mode matching",
        "Model: independent x/y q propagation with shared waist plane + affine centroid propagation + spherical ball lenses + Gaussian mode overlap",
        f"Coupling refractive index: {DEFAULT_REFRACTIVE_INDEX:.6g}",
        "",
        "COUPLING SUMMARY",
    ]
    if valid_tapers:
        best_taper = max(valid_tapers, key=lambda taper_result: taper_result.received_power)
        best_total = (
            best_taper.mode_efficiency
            * best_taper.aperture_transmission
            * best_taper.ball_reflection_transmission
            * best_taper.taper_reflection_transmission
            * best_taper.extra_transmission
        )
        lines.append(
            "BEST DETECTOR: "
            f"{best_taper.source.name} -> {best_taper.taper.name} | "
            f"MODE = {best_taper.mode_efficiency * 100:.5g}% | "
            f"TOTAL = {best_total * 100:.5g}% | "
            f"RECEIVED = {best_taper.received_power * 1e3:.5g} mW"
        )
        for taper_result in valid_tapers:
            offset = math.hypot(taper_result.x_mismatch or 0.0, taper_result.y_mismatch or 0.0)
            status = "; ".join(taper_result.warnings) if taper_result.warnings else "ok"
            total_efficiency = (
                taper_result.mode_efficiency
                * taper_result.aperture_transmission
                * taper_result.ball_reflection_transmission
                * taper_result.taper_reflection_transmission
                * taper_result.extra_transmission
            )
            lines.append(
                "  "
                f"{taper_result.source.name} -> {taper_result.taper.name}: "
                f"MODE = {taper_result.mode_efficiency * 100:.5g}%"
                f", total = {total_efficiency * 100:.5g}%"
                f", received = {taper_result.received_power * 1e3:.5g} mW"
                f", offset = {offset * 1e6:.4g} um"
                f", status = {status}"
            )
    elif valid_results:
        best = max(valid_results, key=lambda fiber_result: fiber_result.coupling_report.total_efficiency)
        best_report = best.coupling_report
        lines.append(
            "BEST: "
            f"{best.source.name} -> {best.fiber.name} | "
            f"COUPLING = {best_report.total_efficiency * 100:.5g}% | "
            f"RECEIVED = {best.received_power * 1e3:.5g} mW"
        )
        for fiber_result in valid_results:
            report = fiber_result.coupling_report
            lines.append(
                "  "
                f"{fiber_result.source.name} -> {fiber_result.fiber.name}: "
                f"COUPLING = {report.total_efficiency * 100:.5g}%"
                f", received = {fiber_result.received_power * 1e3:.5g} mW"
                f", offset = {report.radial_mismatch * 1e6:.4g} um"
                f", angle = {report.angular_mismatch * 1e3:.4g} mrad"
            )
    else:
        lines.append("  no valid detector or source-fiber coupling paths")
    invalid_tapers = [taper_result for taper_result in taper_results if taper_result.beam_radius_x is None]
    for taper_result in invalid_tapers:
        status = "; ".join(taper_result.warnings) if taper_result.warnings else "not simulated"
        lines.append(f"  {taper_result.source.name} -> {taper_result.taper.name}: unavailable ({status})")
    invalid_results = [fiber_result for fiber_result in fiber_results if fiber_result.coupling_report is None]
    for fiber_result in invalid_results:
        status = "; ".join(fiber_result.warnings) if fiber_result.warnings else "not simulated"
        lines.append(f"  {fiber_result.source.name} -> {fiber_result.fiber.name}: unavailable ({status})")
    lines.append("")

    for source_index, result in enumerate(results, start=1):
        source = result.source
        lines.extend(
            [
                f"Source {source_index}: {source.name}",
                f"  z: {source.position * 1e6:.4g} um",
                f"  wavelength: {source.wavelength * 1e6:.4g} um",
                f"  x/y waist radius: ({source.waist_radius * 1e6:.4g}, {source.waist_radius_y * 1e6:.4g}) um",
                f"  x/y Rayleigh length: ({source.rayleigh_range * 1e6:.4g}, {source.rayleigh_range_y * 1e6:.4g}) um",
                f"  waist z: {source.waist_position * 1e6:.4g} um",
                f"  power: {source.power * 1e3:.4g} mW",
                f"  x/y offset: ({source.x_offset * 1e6:.4g}, {source.y_offset * 1e6:.4g}) um",
                f"  x/y angle: ({source.x_angle * 1e3:.4g}, {source.y_angle * 1e3:.4g}) mrad",
            ]
        )
        if result.final_beam_radius is not None:
            lines.append(
                "  final beam at FINAL_Z: "
                f"wx/wy=({result.final_beam_radius * 1e6:.4g}, {result.final_beam_radius_y * 1e6:.4g}) um, "
                f"Rx/Ry=({_format_curvature(result.final_beam_R)}, {_format_curvature(result.final_beam_R_y)})"
            )
        for warning in result.warnings:
            lines.append(f"  WARNING: {warning}")

        if result.taper_results:
            lines.append("  Ball lens checks:")
            ball_reports = result.taper_results[0].ball_reports
            if ball_reports:
                lines.append("    z(um)   D(um)   wx(um)   wy(um)   offset(um)   aper(%)   refl(%)   total(%)   status")
                for report in ball_reports:
                    lines.append(
                        "    "
                        f"{report.ball.position * 1e6:6.4g}"
                        f" {report.ball.diameter * 1e6:7.4g}"
                        f" {report.beam_radius_x * 1e6:8.4g}"
                        f" {report.beam_radius_y * 1e6:8.4g}"
                        f" {report.radial_mismatch * 1e6:11.4g}"
                        f" {report.aperture_transmission * 100:9.4g}"
                        f" {report.reflection_transmission * 100:9.4g}"
                        f" {report.transmission * 100:9.4g}"
                        f"   {report.status}"
                    )
            else:
                lines.append("    none before detector")

            lines.append("  Taper Gaussian mode matching:")
            lines.append(
                "    taper                 mode(%)   refl(%)   extra   total(%)   Prx(mW)   aper(%)   ball refl(%)   wx(um)   wy(um)   offset(um)   status"
            )
            for taper_result in result.taper_results:
                status = "; ".join(taper_result.warnings) if taper_result.warnings else "ok"
                if taper_result.beam_radius_x is None or taper_result.beam_radius_y is None:
                    lines.append(
                        f"    {taper_result.taper.name[:20]:20s}"
                        "    n/a      n/a      n/a      0        0        0        0       n/a      n/a       n/a       "
                        f"{status}"
                    )
                    continue
                offset = math.hypot(taper_result.x_mismatch or 0.0, taper_result.y_mismatch or 0.0)
                total_efficiency = (
                    taper_result.mode_efficiency
                    * taper_result.aperture_transmission
                    * taper_result.ball_reflection_transmission
                    * taper_result.taper_reflection_transmission
                    * taper_result.extra_transmission
                )
                lines.append(
                    f"    {taper_result.taper.name[:20]:20s}"
                    f" {taper_result.mode_efficiency * 100:8.5g}"
                    f" {taper_result.taper_reflection_transmission * 100:9.4g}"
                    f" {taper_result.extra_transmission:7.4g}"
                    f" {total_efficiency * 100:9.5g}"
                    f" {taper_result.received_power * 1e3:9.4g}"
                    f" {taper_result.aperture_transmission * 100:8.4g}"
                    f" {taper_result.ball_reflection_transmission * 100:12.4g}"
                    f" {taper_result.beam_radius_x * 1e6:8.4g}"
                    f" {taper_result.beam_radius_y * 1e6:8.4g}"
                    f" {offset * 1e6:10.4g}"
                    f"   {status}"
                )
            lines.append("")

        lines.append("  Lens aperture checks:")
        if result.lens_reports:
            lines.append("    z(um)   w(um)   mismatch(um)   aperture(um)   trans(%)   status")
            for report in result.lens_reports:
                status = "CLIPPING" if report.clips else "ok"
                lines.append(
                    "    "
                    f"{report.lens.position * 1e6:6.3g}"
                    f" {report.beam_radius * 1e6:7.4g}"
                    f" {report.radial_mismatch * 1e6:12.4g}"
                    f" {report.lens.aperture_radius * 1e6:12.4g}"
                    f" {report.transmission * 100:9.4g}"
                    f"   {status}"
                )
        else:
            lines.append("    none before FINAL_Z")
        lines.append(f"    aperture transmission product: {result.aperture_transmission * 100:.5g}%")

        lines.append("  Fiber coupling:")
        if result.fiber_results:
            lines.append(
                "    fiber                  eta(%)   Prx(mW)   aper(%)   "
                "w(um)   R        offset(um)   angle(mrad)   status"
            )
            for fiber_result in result.fiber_results:
                report = fiber_result.coupling_report
                status_parts = fiber_result.warnings if fiber_result.warnings else ["ok"]
                status = "; ".join(status_parts)
                if report is None:
                    lines.append(
                        f"    {fiber_result.fiber.name[:20]:20s}"
                        "    n/a      0        0       n/a    n/a      n/a          n/a        "
                        f"{status}"
                    )
                    continue
                lines.append(
                    f"    {fiber_result.fiber.name[:20]:20s}"
                    f" {report.total_efficiency * 100:8.4g}"
                    f" {fiber_result.received_power * 1e3:9.4g}"
                    f" {fiber_result.aperture_transmission * 100:8.4g}"
                    f" {report.beam_radius * 1e6:7.4g}"
                    f" {_format_curvature(report.beam_R):8s}"
                    f" {report.radial_mismatch * 1e6:10.4g}"
                    f" {report.angular_mismatch * 1e3:11.4g}"
                    f"   {status}"
                )
        else:
            lines.append("    no fibers")
        lines.append("")

    return "\n".join(lines).rstrip()


@dataclass(frozen=True)
class NominalElementState:
    position: float
    x_offset: float = 0.0
    y_offset: float = 0.0
    x_angle: float = 0.0
    y_angle: float = 0.0


LayoutElement = LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement


def capture_element_nominal(element: LayoutElement) -> NominalElementState:
    state = NominalElementState(
        position=element.position,
        x_offset=element.x_offset,
        y_offset=element.y_offset,
    )
    if isinstance(element, LaserSource):
        return NominalElementState(
            position=element.position,
            x_offset=element.x_offset,
            y_offset=element.y_offset,
            x_angle=element.x_angle,
            y_angle=element.y_angle,
        )
    return state


def apply_nominal_state(element: LayoutElement, nominal: NominalElementState) -> None:
    element.position = nominal.position
    element.x_offset = nominal.x_offset
    element.y_offset = nominal.y_offset
    if isinstance(element, LaserSource):
        element.x_angle = nominal.x_angle
        element.y_angle = nominal.y_angle


def random_positive(max_value: float, rng: random.Random | None = None) -> float:
    generator = rng if rng is not None else random
    return generator.uniform(0.0, max_value)


def scramble_element_positive(
    element: LayoutElement,
    nominal: NominalElementState,
    *,
    axial_tolerance: float = AXIAL_TOLERANCE,
    transverse_tolerance: float = TRANSVERSE_TOLERANCE,
    scramble_axial: bool | None = None,
    rng: random.Random | None = None,
) -> None:
    """Apply a random misalignment relative to the nominal perfectly aligned pose.

    Lenses and ball lenses: positive z offset plus positive x/y offsets from nominal.
    Laser, fibre, and taper elements: x/y offsets only (z stays at nominal).
    """

    if scramble_axial is None:
        scramble_axial = isinstance(element, (LensElement, BallLensElement))

    if scramble_axial:
        element.position = nominal.position + random_positive(axial_tolerance, rng)
    else:
        element.position = nominal.position
    element.x_offset = random_positive(transverse_tolerance, rng)
    element.y_offset = random_positive(transverse_tolerance, rng)


def default_ball_lens_layout() -> tuple[list[BallLensElement], list[TaperDetectorElement], float]:
    radius = 0.5 * DEFAULT_BALL_DIAMETER
    ball1_center = DEFAULT_BALL1_FRONT_GAP + radius
    ball2_center = ball1_center + DEFAULT_BALL_DIAMETER + DEFAULT_BALL_GAP
    taper_z = ball2_center + radius + DEFAULT_BALL2_TAPER_GAP
    balls = [
        BallLensElement(name="Sapphire ball 1", position=ball1_center),
        BallLensElement(name="Sapphire ball 2", position=ball2_center),
    ]
    taper = TaperDetectorElement(position=taper_z)
    final_z = taper_z
    return balls, [taper], final_z


class OpticalLayoutEditor(tk.Tk):
    """Small Tkinter editor for positioning optical components."""

    def __init__(self) -> None:
        super().__init__()
        self.title("Gaussian Beam Layout Editor")
        self.geometry("1180x760")
        self.minsize(960, 620)

        default_balls, default_tapers, default_final_z = default_ball_lens_layout()
        self.sources = [LaserSource()]
        self.lenses: list[LensElement] = []
        self.balls = default_balls
        self.fibers: list[FiberElement] = []
        self.tapers = default_tapers
        self.final_z = default_final_z
        self._nominal_states: dict[str, NominalElementState] = {}

        self.selected_uid: str | None = None
        self._drag: dict[str, Any] | None = None
        self._item_actions: dict[int, tuple[str, str]] = {}
        self._z_min = 0.0
        self._z_max = self.final_z
        self._x_limit = 0.015
        self._x_min = -self._x_limit
        self._x_max = self._x_limit
        self._base_z_min = 0.0
        self._base_z_max = self.final_z * 1.08
        self._base_x_min = -self._x_limit
        self._base_x_max = self._x_limit
        self._view_zoom = 1.0
        self._zoom_anchor_z: float | None = None
        self._zoom_anchor_x: float | None = None
        self._zoom_anchor_z_fraction = 0.5
        self._zoom_anchor_x_fraction = 0.5
        self._beam_paths: list[BeamPathDisplay] = []
        self._undo_stack: list[dict[str, Any]] = []
        self._plot_left = 76
        self._plot_right = 36
        self._plot_top = 42
        self._plot_bottom = 70

        self._fit_view_bounds_to_layout()
        self._capture_nominal_layout()
        self._build_ui()
        self._refresh_tree()
        self.redraw()

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, padding=(8, 8, 8, 4))
        toolbar.grid(row=0, column=0, sticky="ew")
        toolbar.columnconfigure(14, weight=1)

        ttk.Button(toolbar, text="Add source", command=self._add_source).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(toolbar, text="Add ball", command=self._add_ball).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(toolbar, text="Add taper", command=self._add_taper).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(toolbar, text="Edit selected", command=self._edit_selected).grid(row=0, column=3, padx=(0, 6))
        ttk.Button(toolbar, text="Delete selected", command=self._delete_selected).grid(row=0, column=4, padx=(0, 14))
        ttk.Label(toolbar, text="Final z").grid(row=0, column=5, padx=(0, 4))
        self.final_z_var = tk.StringVar(value=f"{self.final_z * 1e6:.2f}")
        final_entry = ttk.Entry(toolbar, textvariable=self.final_z_var, width=8)
        final_entry.grid(row=0, column=6, padx=(0, 4))
        final_entry.bind("<Return>", lambda _event: self._apply_final_z())
        ttk.Label(toolbar, text="um").grid(row=0, column=7, padx=(0, 6))
        ttk.Button(toolbar, text="Apply", command=self._apply_final_z).grid(row=0, column=8, padx=(0, 14))
        ttk.Button(toolbar, text="Simulate", command=self._simulate).grid(row=0, column=9, padx=(0, 6))
        ttk.Button(toolbar, text="Zoom -", command=self._zoom_out).grid(row=0, column=10, padx=(0, 6))
        ttk.Button(toolbar, text="Zoom +", command=self._zoom_in).grid(row=0, column=11, padx=(0, 6))
        ttk.Button(toolbar, text="Reset view", command=self._reset_view).grid(row=0, column=12, padx=(0, 14))
        ttk.Button(toolbar, text="Align all", command=self._align_all).grid(row=1, column=0, padx=(0, 6), pady=(6, 0))
        ttk.Button(toolbar, text="Scramble laser/fibre", command=self._scramble_laser_fibre).grid(
            row=1, column=1, padx=(0, 6), pady=(6, 0)
        )
        ttk.Button(toolbar, text="Full scramble", command=self._scramble_full).grid(
            row=1, column=2, padx=(0, 6), pady=(6, 0)
        )

        self.status_var = tk.StringVar(
            value=(
                "Drag empty canvas to pan. Drag elements to change z and x. "
                "Drag aperture handles to resize. Double-click an element for exact parameters."
            )
        )
        ttk.Label(toolbar, textvariable=self.status_var, anchor="e").grid(row=0, column=14, sticky="ew")

        main = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        canvas_frame = ttk.Frame(main)
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(canvas_frame, background="#fbfbfb", highlightthickness=1, highlightbackground="#c9c9c9")
        self.canvas.grid(row=0, column=0, sticky="nsew")
        main.add(canvas_frame, weight=3)

        side = ttk.Frame(main, padding=(8, 0, 0, 0))
        side.rowconfigure(1, weight=1)
        side.rowconfigure(3, weight=1)
        side.columnconfigure(0, weight=1)
        ttk.Label(side, text="Layout parameters").grid(row=0, column=0, sticky="w")

        columns = ("kind", "z", "x", "y", "mfd", "extra")
        self.tree = ttk.Treeview(side, columns=columns, show="tree headings", height=10, selectmode="browse")
        self.tree.heading("#0", text="Element")
        self.tree.heading("kind", text="Type")
        self.tree.heading("z", text="z")
        self.tree.heading("x", text="x")
        self.tree.heading("y", text="y")
        self.tree.heading("mfd", text="MFD")
        self.tree.heading("extra", text="extra")
        self.tree.column("#0", width=150, anchor="w")
        self.tree.column("kind", width=66, anchor="center")
        self.tree.column("z", width=80, anchor="e")
        self.tree.column("x", width=80, anchor="e")
        self.tree.column("y", width=80, anchor="e")
        self.tree.column("mfd", width=110, anchor="e")
        self.tree.column("extra", width=150, anchor="e")
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(4, 8))

        ttk.Label(side, text="Simulate output").grid(row=2, column=0, sticky="w")
        self.output = tk.Text(side, height=12, width=44, wrap="none", font=("Consolas", 9))
        self.output.grid(row=3, column=0, sticky="nsew", pady=(4, 0))
        main.add(side, weight=1)

        self.canvas.bind("<Configure>", lambda _event: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.canvas.bind("<Double-Button-1>", self._on_canvas_double_click)
        self.canvas.bind("<MouseWheel>", self._on_mouse_wheel)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-Button-1>", lambda _event: self._edit_selected())
        self.bind_all("<Control-z>", self._undo)
        self.bind_all("<Control-Z>", self._undo)

    def _all_elements(self) -> list[LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement]:
        elements: list[LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement] = [
            *self.sources,
            *self.lenses,
            *self.balls,
            *self.fibers,
            *self.tapers,
        ]
        order = {"laser": 0, "ball": 1, "lens": 2, "taper": 3, "fiber": 4}
        return sorted(elements, key=lambda element: (element.position, order[element.kind], element.name))

    def _element_by_uid(
        self,
        uid: str | None,
    ) -> LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement | None:
        if uid is None:
            return None
        for element in [*self.sources, *self.lenses, *self.balls, *self.fibers, *self.tapers]:
            if element.uid == uid:
                return element
        return None

    def _layout_snapshot(self) -> dict[str, Any]:
        return {
            "sources": deepcopy(self.sources),
            "lenses": deepcopy(self.lenses),
            "balls": deepcopy(self.balls),
            "fibers": deepcopy(self.fibers),
            "tapers": deepcopy(self.tapers),
            "final_z": self.final_z,
            "selected_uid": self.selected_uid,
        }

    def _push_undo(self) -> None:
        self._undo_stack.append(self._layout_snapshot())
        max_undo = 80
        if len(self._undo_stack) > max_undo:
            del self._undo_stack[0 : len(self._undo_stack) - max_undo]

    def _restore_layout_snapshot(self, snapshot: dict[str, Any]) -> None:
        self.sources = deepcopy(snapshot["sources"])
        self.lenses = deepcopy(snapshot["lenses"])
        self.balls = deepcopy(snapshot["balls"])
        self.fibers = deepcopy(snapshot["fibers"])
        self.tapers = deepcopy(snapshot["tapers"])
        self.final_z = float(snapshot["final_z"])
        self.selected_uid = snapshot["selected_uid"]
        self.final_z_var.set(f"{self.final_z * 1e6:.2f}")
        self._clear_simulation_overlay()
        self._fit_view_bounds_to_layout()
        self._refresh_tree()
        self.redraw()

    def _undo(self, _event: tk.Event | None = None) -> str:
        if not self._undo_stack:
            self.status_var.set("Nothing to undo.")
            return "break"
        self._restore_layout_snapshot(self._undo_stack.pop())
        self.status_var.set("Undid last layout change.")
        return "break"

    def redraw(self) -> None:
        self.canvas.delete("all")
        self._item_actions.clear()
        self._update_view_bounds()
        self._draw_grid()
        self._draw_beam_paths()
        for source in sorted(self.sources, key=lambda item: item.position):
            self._draw_laser(source)
        for lens in sorted(self.lenses, key=lambda item: item.position):
            self._draw_lens(lens)
        for ball in sorted(self.balls, key=lambda item: item.position):
            self._draw_ball(ball)
        for fiber in sorted(self.fibers, key=lambda item: item.position):
            self._draw_fiber(fiber)
        for taper in sorted(self.tapers, key=lambda item: item.position):
            self._draw_taper(taper)

    def _update_view_bounds(self) -> None:
        base_z_min = self._base_z_min
        base_z_max = max(self._base_z_max, base_z_min + 1e-6)
        base_x_min = self._base_x_min
        base_x_max = max(self._base_x_max, base_x_min + 1e-9)

        zoom = max(self._view_zoom, 1.0)
        base_z_range = max(base_z_max - base_z_min, 1e-6)
        zoomed_z_range = base_z_range / zoom
        z_anchor = self._zoom_anchor_z
        if z_anchor is None:
            z_anchor = 0.5 * (base_z_min + base_z_max)
        z_anchor = min(max(z_anchor, base_z_min), base_z_max)
        self._z_min = z_anchor - self._zoom_anchor_z_fraction * zoomed_z_range
        self._z_max = self._z_min + zoomed_z_range
        if self._z_min < base_z_min:
            self._z_min = base_z_min
            self._z_max = self._z_min + zoomed_z_range
        if self._z_max > base_z_max:
            self._z_max = base_z_max
            self._z_min = self._z_max - zoomed_z_range
        self._z_min = max(self._z_min, base_z_min)

        base_x_range = max(base_x_max - base_x_min, 1e-9)
        zoomed_x_range = base_x_range / zoom
        x_anchor = self._zoom_anchor_x
        if x_anchor is None:
            x_anchor = 0.5 * (base_x_min + base_x_max)
        x_anchor = min(max(x_anchor, base_x_min), base_x_max)
        self._x_min = x_anchor - (1.0 - self._zoom_anchor_x_fraction) * zoomed_x_range
        self._x_max = self._x_min + zoomed_x_range
        if self._x_min < base_x_min:
            self._x_min = base_x_min
            self._x_max = self._x_min + zoomed_x_range
        if self._x_max > base_x_max:
            self._x_max = base_x_max
            self._x_min = self._x_max - zoomed_x_range
        self._x_min = max(self._x_min, base_x_min)
        self._x_limit = max(0.5 * (self._x_max - self._x_min), 1e-9)

    def _fit_view_bounds_to_layout(self) -> None:
        self._base_z_min, self._base_z_max, self._base_x_min, self._base_x_max = self._layout_view_bounds()

    def _layout_view_bounds(self) -> tuple[float, float, float, float]:
        positions = [element.position for element in self._all_elements()]
        for path in self._beam_paths:
            positions.extend(path.z)
        max_z = max([self.final_z, *positions, 1e-3])
        base_z_min = 0.0
        base_z_max = max_z * 1.08

        x_limit = 50.0e-6
        for source in self.sources:
            x_limit = max(x_limit, abs(source.x_offset) + source.waist_radius)
        for lens in self.lenses:
            x_limit = max(x_limit, abs(lens.x_offset) + lens.aperture_radius)
        for ball in self.balls:
            x_limit = max(x_limit, abs(ball.x_offset) + ball.radius)
        for fiber in self.fibers:
            x_limit = max(
                x_limit,
                abs(fiber.x_offset) + fiber.cladding_radius,
                abs(fiber.x_offset) + fiber.mode_radius,
            )
        for taper in self.tapers:
            x_limit = max(x_limit, abs(taper.x_offset) + 0.5 * taper.height)
        for path in self._beam_paths:
            if path.x:
                x_limit = max(
                    x_limit,
                    max(
                        max(abs(center + radius), abs(center - radius))
                        for center, radius in zip(path.x, path.w)
                    ),
                )

        base_x_half_range = max(x_limit * 1.18, 1e-9)
        base_x_min = -base_x_half_range
        base_x_max = base_x_half_range
        return base_z_min, base_z_max, base_x_min, base_x_max

    def _canvas_size(self) -> tuple[int, int]:
        return max(self.canvas.winfo_width(), 800), max(self.canvas.winfo_height(), 500)

    def _plot_pixel_bounds(self) -> tuple[float, float, float, float]:
        width, height = self._canvas_size()
        return (
            float(self._plot_left),
            float(width - self._plot_right),
            float(self._plot_top),
            float(height - self._plot_bottom),
        )

    def _store_zoom_anchor_from_canvas(self, px: float, py: float) -> None:
        left, right, top, bottom = self._plot_pixel_bounds()
        usable_z = right - left
        usable_x = bottom - top
        if usable_z <= 0 or usable_x <= 0:
            return

        clamped_px = min(max(float(px), left), right)
        clamped_py = min(max(float(py), top), bottom)
        self._zoom_anchor_z_fraction = (clamped_px - left) / usable_z
        self._zoom_anchor_x_fraction = (clamped_py - top) / usable_x
        self._zoom_anchor_z = self._px_to_z(clamped_px)
        self._zoom_anchor_x = self._px_to_x(clamped_py)

    def _pan_view_by_pixels(self, dx: float, dy: float) -> None:
        left, right, top, bottom = self._plot_pixel_bounds()
        usable_z = right - left
        usable_x = bottom - top
        if usable_z <= 0 or usable_x <= 0:
            return

        z_range = self._z_max - self._z_min
        x_range = self._x_max - self._x_min
        if self._zoom_anchor_z is None:
            self._zoom_anchor_z = self._z_min + self._zoom_anchor_z_fraction * z_range
        if self._zoom_anchor_x is None:
            self._zoom_anchor_x = self._x_min + (1.0 - self._zoom_anchor_x_fraction) * x_range

        self._zoom_anchor_z -= float(dx) * z_range / usable_z
        self._zoom_anchor_x += float(dy) * x_range / usable_x

    def _z_to_px(self, z_value: float) -> float:
        width, _height = self._canvas_size()
        usable = width - self._plot_left - self._plot_right
        return self._plot_left + usable * (z_value - self._z_min) / (self._z_max - self._z_min)

    def _x_to_px(self, x_value: float) -> float:
        _width, height = self._canvas_size()
        usable = height - self._plot_top - self._plot_bottom
        return self._plot_top + usable * (self._x_max - x_value) / (self._x_max - self._x_min)

    def _px_to_z(self, px: float) -> float:
        width, _height = self._canvas_size()
        usable = width - self._plot_left - self._plot_right
        raw = self._z_min + (px - self._plot_left) * (self._z_max - self._z_min) / usable
        return max(0.0, min(raw, self._z_max))

    def _px_to_x(self, py: float) -> float:
        _width, height = self._canvas_size()
        usable = height - self._plot_top - self._plot_bottom
        raw = self._x_max - (py - self._plot_top) * (self._x_max - self._x_min) / usable
        return max(self._x_min, min(raw, self._x_max))

    def _draw_grid(self) -> None:
        width, height = self._canvas_size()
        left = self._plot_left
        right = width - self._plot_right
        top = self._plot_top
        bottom = height - self._plot_bottom
        axis_y = self._x_to_px(0.0)

        self.canvas.create_rectangle(left, top, right, bottom, outline="#dedede", fill="#ffffff")
        if top <= axis_y <= bottom:
            self.canvas.create_line(left, axis_y, right, axis_y, fill="#7a7a7a", width=1)

        for i in range(7):
            z_value = self._z_min + i * (self._z_max - self._z_min) / 6.0
            px = self._z_to_px(z_value)
            self.canvas.create_line(px, top, px, bottom, fill="#eeeeee")
            self.canvas.create_text(px, bottom + 18, text=f"{z_value * 1e6:.1f}", fill="#555555", font=("Segoe UI", 9))

        for i in range(5):
            x_value = self._x_min + i * (self._x_max - self._x_min) / 4.0
            py = self._x_to_px(x_value)
            self.canvas.create_line(left, py, right, py, fill="#f0f0f0")
            self.canvas.create_text(left - 8, py, text=f"{x_value * 1e6:.1f}", anchor="e", fill="#555555", font=("Segoe UI", 9))

        self.canvas.create_text((left + right) / 2.0, height - 20, text="z position (um)", fill="#333333")
        self.canvas.create_text(
            18,
            (top + bottom) / 2.0,
            text=f"x offset / aperture (um), zoom {self._view_zoom:.2g}x",
            angle=90,
            fill="#333333",
        )

    def _draw_beam_paths(self) -> None:
        for path in self._beam_paths:
            if len(path.z) < 2:
                continue
            upper_points: list[tuple[float, float]] = []
            lower_points: list[tuple[float, float]] = []
            coords: list[float] = []
            upper_coords: list[float] = []
            lower_coords: list[float] = []
            for z_value, x_value, radius in zip(path.z, path.x, path.w):
                z_px = self._z_to_px(z_value)
                center_px = self._x_to_px(x_value)
                upper_px = self._x_to_px(x_value + radius)
                lower_px = self._x_to_px(x_value - radius)
                upper_points.append((z_px, upper_px))
                lower_points.append((z_px, lower_px))
                coords.extend([z_px, center_px])
                upper_coords.extend([z_px, upper_px])
                lower_coords.extend([z_px, lower_px])
            polygon_coords: list[float] = []
            for z_px, x_px in upper_points:
                polygon_coords.extend([z_px, x_px])
            for z_px, x_px in reversed(lower_points):
                polygon_coords.extend([z_px, x_px])
            self.canvas.create_polygon(*polygon_coords, fill=path.color, outline="", stipple="gray75")
            self.canvas.create_line(*upper_coords, fill=path.color, width=1.2, dash=(3, 3), smooth=True)
            self.canvas.create_line(*lower_coords, fill=path.color, width=1.2, dash=(3, 3), smooth=True)
            self.canvas.create_line(*coords, fill=path.color, width=1.8, smooth=True)
            waist_z_px = self._z_to_px(path.waist_z)
            waist_x_px = self._x_to_px(path.waist_x)
            waist_upper_px = self._x_to_px(path.waist_x + path.waist_radius)
            waist_lower_px = self._x_to_px(path.waist_x - path.waist_radius)
            self.canvas.create_line(waist_z_px, waist_upper_px, waist_z_px, waist_lower_px, fill=path.color, width=2)
            self.canvas.create_oval(
                waist_z_px - 4,
                waist_x_px - 4,
                waist_z_px + 4,
                waist_x_px + 4,
                fill=path.color,
                outline="#ffffff",
                width=1,
            )
            self.canvas.create_text(
                waist_z_px + 8,
                min(waist_upper_px, waist_lower_px) - 10,
                text=f"waist {path.waist_radius * 1e6:.3g} um",
                fill=path.color,
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            )
            end_z = self._z_to_px(path.z[-1])
            end_x = self._x_to_px(path.x[-1])
            self.canvas.create_text(
                end_z,
                end_x - 12,
                text=f"{path.source_name} path",
                fill=path.color,
                font=("Segoe UI", 9, "bold"),
                anchor="e",
            )

    def _draw_laser(self, laser: LaserSource) -> None:
        z_px = self._z_to_px(laser.position)
        x_px = self._x_to_px(laser.x_offset)
        uid = laser.uid
        selected = uid == self.selected_uid
        color = "#d33f49"

        dz = min(0.035, max(self._z_max * 0.12, 0.012))
        end_z = min(self._z_max, laser.position + dz)
        end_x = laser.x_offset + laser.x_angle * (end_z - laser.position)
        end_z_px = self._z_to_px(end_z)
        end_x_px = self._x_to_px(end_x)

        line = self.canvas.create_line(z_px, x_px, end_z_px, end_x_px, fill=color, width=2, arrow=tk.LAST)
        self._register_canvas_item(line, uid, "move")
        oval = self.canvas.create_oval(z_px - 9, x_px - 9, z_px + 9, x_px + 9, fill=color, outline="#8f2630", width=2)
        self._register_canvas_item(oval, uid, "move")
        label_text = f"{laser.name}\n{laser.power * 1e3:.3g} mW"
        label = self.canvas.create_text(z_px, x_px - 24, text=label_text, fill="#333333", font=("Segoe UI", 9, "bold"))
        self._register_canvas_item(label, uid, "move")
        waist_px = self._x_to_px(laser.x_offset + laser.waist_radius)
        waist_radius_px = max(6.0, abs(waist_px - x_px))
        ring = self.canvas.create_oval(
            z_px - waist_radius_px,
            x_px - waist_radius_px,
            z_px + waist_radius_px,
            x_px + waist_radius_px,
            outline=color,
            dash=(3, 3),
        )
        self._register_canvas_item(ring, uid, "move")
        if selected:
            self.canvas.create_oval(z_px - 14, x_px - 14, z_px + 14, x_px + 14, outline="#1f6feb", width=2)

    def _draw_lens(self, lens: LensElement) -> None:
        z_px = self._z_to_px(lens.position)
        x_px = self._x_to_px(lens.x_offset)
        radius_px = abs(self._x_to_px(lens.x_offset + lens.aperture_radius) - x_px)
        display_radius_px = max(12.0, radius_px)
        uid = lens.uid
        selected = uid == self.selected_uid
        color = "#e6862f"

        body = self.canvas.create_line(z_px, x_px - display_radius_px, z_px, x_px + display_radius_px, fill=color, width=5)
        self._register_canvas_item(body, uid, "move")
        center = self.canvas.create_oval(z_px - 6, x_px - 6, z_px + 6, x_px + 6, fill=color, outline="#9b551d", width=1)
        self._register_canvas_item(center, uid, "move")
        top = self.canvas.create_rectangle(z_px - 6, x_px - display_radius_px - 6, z_px + 6, x_px - display_radius_px + 6, fill="#ffffff", outline=color, width=2)
        bottom = self.canvas.create_rectangle(z_px - 6, x_px + display_radius_px - 6, z_px + 6, x_px + display_radius_px + 6, fill="#ffffff", outline=color, width=2)
        self._register_canvas_item(top, uid, "radius")
        self._register_canvas_item(bottom, uid, "radius")
        label_text = f"{lens.name}\nf={lens.focal_length * 1e6:.3g} um, ap={lens.aperture_radius * 1e6:.3g} um"
        label = self.canvas.create_text(z_px + 8, x_px - display_radius_px - 16, text=label_text, anchor="w", fill="#333333", font=("Segoe UI", 9))
        self._register_canvas_item(label, uid, "move")
        if selected:
            self.canvas.create_rectangle(
                z_px - 12,
                x_px - display_radius_px - 12,
                z_px + 12,
                x_px + display_radius_px + 12,
                outline="#1f6feb",
                dash=(4, 3),
                width=2,
            )

    def _draw_ball(self, ball: BallLensElement) -> None:
        z_px = self._z_to_px(ball.position)
        x_px = self._x_to_px(ball.x_offset)
        z_radius_px = abs(self._z_to_px(ball.position + ball.radius) - z_px)
        x_radius_px = abs(self._x_to_px(ball.x_offset + ball.radius) - x_px)
        display_z_radius_px = max(10.0, z_radius_px)
        display_x_radius_px = max(10.0, x_radius_px)
        uid = ball.uid
        selected = uid == self.selected_uid
        fill = "#a9d6f5"
        outline = "#2c6f9f"

        body = self.canvas.create_oval(
            z_px - display_z_radius_px,
            x_px - display_x_radius_px,
            z_px + display_z_radius_px,
            x_px + display_x_radius_px,
            fill=fill,
            outline=outline,
            width=2,
            stipple="gray75",
        )
        self._register_canvas_item(body, uid, "move")
        center = self.canvas.create_oval(z_px - 4, x_px - 4, z_px + 4, x_px + 4, fill=outline, outline=outline)
        self._register_canvas_item(center, uid, "move")
        top = self.canvas.create_rectangle(
            z_px - 6,
            x_px - display_x_radius_px - 6,
            z_px + 6,
            x_px - display_x_radius_px + 6,
            fill="#ffffff",
            outline=outline,
            width=2,
        )
        bottom = self.canvas.create_rectangle(
            z_px - 6,
            x_px + display_x_radius_px - 6,
            z_px + 6,
            x_px + display_x_radius_px + 6,
            fill="#ffffff",
            outline=outline,
            width=2,
        )
        self._register_canvas_item(top, uid, "radius")
        self._register_canvas_item(bottom, uid, "radius")
        label_text = (
            f"{ball.name}\n"
            f"D={ball.diameter * 1e6:.3g} um, n={ball.refractive_index:.3g}\n"
            f"EFL={ball.effective_focal_length * 1e6:.3g} um"
        )
        label = self.canvas.create_text(
            z_px + display_z_radius_px + 8,
            x_px - display_x_radius_px - 10,
            text=label_text,
            anchor="w",
            fill="#333333",
            font=("Segoe UI", 9),
        )
        self._register_canvas_item(label, uid, "move")
        if selected:
            self.canvas.create_oval(
                z_px - display_z_radius_px - 6,
                x_px - display_x_radius_px - 6,
                z_px + display_z_radius_px + 6,
                x_px + display_x_radius_px + 6,
                outline="#1f6feb",
                dash=(4, 3),
                width=2,
            )

    def _draw_fiber(self, fiber: FiberElement) -> None:
        z_px = self._z_to_px(fiber.position)
        x_px = self._x_to_px(fiber.x_offset)
        radius_px = abs(self._x_to_px(fiber.x_offset + fiber.cladding_radius) - x_px)
        display_radius_px = max(12.0, radius_px)
        mode_px = max(3.0, abs(self._x_to_px(fiber.x_offset + fiber.mode_radius) - x_px))
        uid = fiber.uid
        selected = uid == self.selected_uid
        color = "#707070"

        body = self.canvas.create_line(z_px, x_px - display_radius_px, z_px, x_px + display_radius_px, fill=color, width=5)
        self._register_canvas_item(body, uid, "move")
        mode = self.canvas.create_line(z_px + 7, x_px - mode_px, z_px + 7, x_px + mode_px, fill="#4f7fbe", width=2)
        self._register_canvas_item(mode, uid, "move")
        center = self.canvas.create_oval(z_px - 6, x_px - 6, z_px + 6, x_px + 6, fill=color, outline="#444444", width=1)
        self._register_canvas_item(center, uid, "move")
        top = self.canvas.create_rectangle(z_px - 6, x_px - display_radius_px - 6, z_px + 6, x_px - display_radius_px + 6, fill="#ffffff", outline=color, width=2)
        bottom = self.canvas.create_rectangle(z_px - 6, x_px + display_radius_px - 6, z_px + 6, x_px + display_radius_px + 6, fill="#ffffff", outline=color, width=2)
        self._register_canvas_item(top, uid, "radius")
        self._register_canvas_item(bottom, uid, "radius")
        label_text = (
            f"{fiber.name}\n"
            f"MFD={fiber.mode_field_diameter * 1e6:.2g} um, clad={fiber.cladding_diameter * 1e6:.3g} um\n"
            f"Prx={fiber.received_power * 1e3:.3g} mW"
        )
        label = self.canvas.create_text(z_px + 8, x_px + display_radius_px + 16, text=label_text, anchor="w", fill="#333333", font=("Segoe UI", 9))
        self._register_canvas_item(label, uid, "move")
        if selected:
            self.canvas.create_rectangle(
                z_px - 12,
                x_px - display_radius_px - 12,
                z_px + 12,
                x_px + display_radius_px + 12,
                outline="#1f6feb",
                dash=(4, 3),
                width=2,
            )

    def _draw_taper(self, taper: TaperDetectorElement) -> None:
        z_px = self._z_to_px(taper.position)
        x_px = self._x_to_px(taper.x_offset)
        half_width_px = max(5.0, abs(self._z_to_px(taper.position + 0.5 * taper.width) - z_px))
        half_height_px = max(5.0, abs(self._x_to_px(taper.x_offset + 0.5 * taper.height) - x_px))
        mode_radius_x_px = max(7.0, abs(self._x_to_px(taper.x_offset + taper.mode_radius_x) - x_px))
        mode_half_width_px = max(8.0, half_width_px * 1.9)
        uid = taper.uid
        selected = uid == self.selected_uid
        color = "#5b5b5b"
        mode_color = "#2f73d9"

        mode = self.canvas.create_oval(
            z_px - mode_half_width_px,
            x_px - mode_radius_x_px,
            z_px + mode_half_width_px,
            x_px + mode_radius_x_px,
            outline=mode_color,
            width=2,
            dash=(5, 3),
        )
        self._register_canvas_item(mode, uid, "move")
        mode_center = self.canvas.create_line(
            z_px - mode_half_width_px,
            x_px,
            z_px + mode_half_width_px,
            x_px,
            fill=mode_color,
            width=1,
            dash=(2, 3),
        )
        self._register_canvas_item(mode_center, uid, "move")

        body = self.canvas.create_rectangle(
            z_px - half_width_px,
            x_px - half_height_px,
            z_px + half_width_px,
            x_px + half_height_px,
            fill=color,
            outline="#202020",
            width=2,
        )
        self._register_canvas_item(body, uid, "move")
        label_text = (
            f"{taper.name}\n"
            f"physical {taper.width * 1e6:.3g} x {taper.height * 1e6:.3g} um\n"
            f"optical mode {taper.mode_radius_x * 1e6:.3g} x {taper.mode_radius_y * 1e6:.3g} um, "
            f"n={taper.facet_refractive_index:.3g}, T={taper.extra_transmission:.3g}\n"
            f"Prx={taper.received_power * 1e3:.4g} mW"
        )
        label = self.canvas.create_text(
            z_px + half_width_px + 8,
            x_px + max(half_height_px, mode_radius_x_px) + 12,
            text=label_text,
            anchor="w",
            fill="#333333",
            font=("Segoe UI", 9),
        )
        self._register_canvas_item(label, uid, "move")
        if selected:
            self.canvas.create_rectangle(
                z_px - half_width_px - 6,
                x_px - half_height_px - 6,
                z_px + half_width_px + 6,
                x_px + half_height_px + 6,
                outline="#1f6feb",
                dash=(4, 3),
                width=2,
            )

    def _register_canvas_item(self, item_id: int, uid: str, action: str) -> None:
        self._item_actions[item_id] = (uid, action)

    def _clear_simulation_overlay(self) -> None:
        self._beam_paths = []

    def _source_color(self, index: int) -> str:
        colors = [
            "#d33f49",
            "#1f77b4",
            "#2ca02c",
            "#9467bd",
            "#8c564b",
            "#17becf",
        ]
        return colors[index % len(colors)]

    def _build_beam_paths(self) -> list[BeamPathDisplay]:
        paths: list[BeamPathDisplay] = []
        for index, source in enumerate(sorted(self.sources, key=lambda item: item.position)):
            if self.final_z < source.position:
                continue
            if self.balls:
                try:
                    _state, _reports, _missed, path_data = propagate_astigmatic_through_balls(
                        source,
                        self.balls,
                        self.final_z,
                        clipping_radius_factor=DEFAULT_CLIPPING_RADIUS_FACTOR,
                        samples_per_space=80,
                    )
                except ValueError:
                    continue
                z_values, x_values, _y_values, wx_values, _wy_values = path_data
                if len(z_values) < 2:
                    continue
                waist_index = min(range(len(wx_values)), key=lambda item: wx_values[item])
                paths.append(
                    BeamPathDisplay(
                        source_uid=source.uid,
                        source_name=source.name,
                        z=[float(value) for value in z_values],
                        x=[float(value) for value in x_values],
                        w=[float(value) for value in wx_values],
                        waist_z=float(z_values[waist_index]),
                        waist_x=float(x_values[waist_index]),
                        waist_radius=float(wx_values[waist_index]),
                        color=self._source_color(index),
                    )
                )
                continue

            usable_lenses = lens_specs_between(self.lenses, source.position, self.final_z)
            try:
                center_sample = sample_beam_centroid(
                    usable_lenses,
                    final_z=self.final_z,
                    start_z=source.position,
                    laser=source_to_alignment(source),
                    z_samples_per_space=80,
                )
                elements = build_elements_from_lenses(
                    usable_lenses,
                    final_z=self.final_z,
                    start_z=source.position,
                )
                beam_sample = sample_system(
                    source_to_beam(source),
                    elements,
                    z_samples_per_space=80,
                    start_z=source.position,
                )
            except ValueError:
                continue
            sample_count = min(len(center_sample.z), len(beam_sample.z))
            if sample_count < 2:
                continue
            z_values = [float(value) for value in beam_sample.z[:sample_count]]
            x_values = [float(value) for value in center_sample.x[:sample_count]]
            w_values = [float(value) for value in beam_sample.w[:sample_count]]
            waist_index = min(range(sample_count), key=lambda item: w_values[item])
            paths.append(
                BeamPathDisplay(
                    source_uid=source.uid,
                    source_name=source.name,
                    z=z_values,
                    x=x_values,
                    w=w_values,
                    waist_z=z_values[waist_index],
                    waist_x=x_values[waist_index],
                    waist_radius=w_values[waist_index],
                    color=self._source_color(index),
                )
            )
        return paths

    def _on_canvas_press(self, event: tk.Event) -> None:
        self._store_zoom_anchor_from_canvas(event.x, event.y)
        action = self._current_action()
        if action is None:
            self.selected_uid = None
            self._drag = {
                "mode": "pan",
                "last_x": event.x,
                "last_y": event.y,
            }
            self._refresh_tree()
            self.redraw()
            return

        uid, mode = action
        element = self._element_by_uid(uid)
        if element is None:
            return
        self.selected_uid = uid
        self._drag = {
            "uid": uid,
            "mode": mode,
            "start_x": event.x,
            "start_y": event.y,
            "position": element.position,
            "x_offset": getattr(element, "x_offset", 0.0),
            "undo_pushed": False,
        }
        self._refresh_tree()
        self.redraw()

    def _on_canvas_drag(self, event: tk.Event) -> None:
        if self._drag is None:
            return
        if self._drag["mode"] == "pan":
            dx = event.x - self._drag["last_x"]
            dy = event.y - self._drag["last_y"]
            self._pan_view_by_pixels(dx, dy)
            self._drag["last_x"] = event.x
            self._drag["last_y"] = event.y
            self.redraw()
            return

        element = self._element_by_uid(self._drag["uid"])
        if element is None:
            return
        if not self._drag.get("undo_pushed"):
            self._push_undo()
            self._drag["undo_pushed"] = True

        if self._drag["mode"] == "radius":
            self._resize_element_from_pointer(element, event.y)
        else:
            element.position = self._px_to_z(event.x)
            element.x_offset = self._px_to_x(event.y)

        self._clear_simulation_overlay()
        self._validate_element(element)
        self._refresh_tree()
        self.redraw()

    def _on_canvas_release(self, _event: tk.Event) -> None:
        self._drag = None

    def _on_canvas_double_click(self, _event: tk.Event) -> None:
        action = self._current_action()
        if action is None:
            return
        self.selected_uid = action[0]
        self._refresh_tree()
        self.redraw()
        self._edit_selected()

    def _on_mouse_wheel(self, event: tk.Event) -> None:
        self._store_zoom_anchor_from_canvas(event.x, event.y)
        if event.delta > 0:
            self._zoom_in()
        elif event.delta < 0:
            self._zoom_out()

    def _current_action(self) -> tuple[str, str] | None:
        current = self.canvas.find_withtag("current")
        if not current:
            return None
        return self._item_actions.get(current[0])

    def _resize_element_from_pointer(
        self,
        element: LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement,
        pointer_y: float,
    ) -> None:
        new_radius = abs(self._px_to_x(pointer_y) - element.x_offset)
        if isinstance(element, LensElement):
            element.aperture_radius = max(new_radius, 1e-6)
        elif isinstance(element, BallLensElement):
            element.diameter = max(2.0 * new_radius, 1e-9)
        elif isinstance(element, FiberElement):
            element.cladding_diameter = max(2.0 * new_radius, 1e-6)

    def _on_tree_select(self, _event: tk.Event) -> None:
        selection = self.tree.selection()
        if selection:
            self.selected_uid = selection[0]
            self.redraw()

    def _refresh_tree(self) -> None:
        existing = set(self.tree.get_children(""))
        current = set()
        for element in self._all_elements():
            current.add(element.uid)
            values = self._tree_values(element)
            if element.uid in existing:
                self.tree.item(element.uid, text=element.name, values=values)
            else:
                self.tree.insert("", "end", iid=element.uid, text=element.name, values=values)
        for item_id in existing - current:
            self.tree.delete(item_id)
        if self.selected_uid in current:
            self.tree.selection_set(self.selected_uid)
        else:
            self.tree.selection_remove(*self.tree.selection())

    def _tree_values(
        self,
        element: LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement,
    ) -> tuple[str, str, str, str, str, str]:
        if isinstance(element, LaserSource):
            mfd = f"{2.0 * element.waist_radius * 1e6:.3g}x{2.0 * element.waist_radius_y * 1e6:.3g} um"
            extra = f"w0 {element.waist_radius * 1e6:.3g}x{element.waist_radius_y * 1e6:.3g} um"
            x_text = f"{element.x_offset * 1e6:.3g} um"
            y_text = f"{element.y_offset * 1e6:.3g} um"
        elif isinstance(element, LensElement):
            mfd = "-"
            extra = f"f {element.focal_length * 1e6:.3g} um, ap {element.aperture_radius * 1e6:.3g} um"
            x_text = f"{element.x_offset * 1e6:.3g} um"
            y_text = f"{element.y_offset * 1e6:.3g} um"
        elif isinstance(element, BallLensElement):
            mfd = "-"
            extra = f"D {element.diameter * 1e6:.3g} um, n {element.refractive_index:.3g}"
            x_text = f"{element.x_offset * 1e6:.3g} um"
            y_text = f"{element.y_offset * 1e6:.3g} um"
        elif isinstance(element, TaperDetectorElement):
            mfd = f"{2.0 * element.mode_radius_x * 1e6:.3g}x{2.0 * element.mode_radius_y * 1e6:.3g} um"
            extra = f"phys {element.width * 1e6:.3g}x{element.height * 1e6:.3g} um, n {element.facet_refractive_index:.3g}"
            x_text = f"{element.x_offset * 1e6:.3g} um"
            y_text = f"{element.y_offset * 1e6:.3g} um"
        else:
            mfd = f"{element.mode_field_diameter * 1e6:.3g} um"
            extra = f"clad {element.cladding_diameter * 1e6:.3g} um"
            x_text = f"{element.x_offset * 1e6:.3g} um"
            y_text = f"{element.y_offset * 1e6:.3g} um"
        return (element.kind, f"{element.position * 1e6:.3g} um", x_text, y_text, mfd, extra)

    def _add_source(self) -> None:
        source_number = len(self.sources) + 1
        source = LaserSource(
            name=f"Laser source {source_number}",
            position=0.0,
            x_offset=(source_number - 1) * 1.0e-3,
        )
        self._push_undo()
        self.sources.append(source)
        self.selected_uid = source.uid
        self._register_nominal(source)
        self._clear_simulation_overlay()
        self._refresh_tree()
        self.redraw()

    def _add_lens(self) -> None:
        lens_number = len(self.lenses) + 1
        if self.lenses:
            position = min(self.final_z * 0.92, self.lenses[-1].position + 0.04)
        else:
            position = self.final_z * 0.4
        lens = LensElement(
            name=f"Lens {lens_number}",
            position=position,
            focal_length=0.025,
            aperture_radius=6.25e-3,
        )
        self._push_undo()
        self.lenses.append(lens)
        self.selected_uid = lens.uid
        self._register_nominal(lens)
        self._clear_simulation_overlay()
        self._refresh_tree()
        self.redraw()

    def _add_ball(self) -> None:
        ball_number = len(self.balls) + 1
        if self.balls:
            position = min(self.final_z * 0.92, self.balls[-1].position + DEFAULT_BALL_DIAMETER)
        else:
            position = self.final_z * 0.4
        ball = BallLensElement(
            name=f"Sapphire ball {ball_number}",
            position=position,
        )
        self._push_undo()
        self.balls.append(ball)
        self.selected_uid = ball.uid
        self._register_nominal(ball)
        self._clear_simulation_overlay()
        self._refresh_tree()
        self.redraw()

    def _add_taper(self) -> None:
        taper_number = len(self.tapers) + 1
        if self.tapers:
            position = min(self.final_z, self.tapers[-1].position + 100e-6)
        else:
            position = self.final_z * 0.88
        taper = TaperDetectorElement(
            name=f"Taper detector {taper_number}",
            position=position,
        )
        self._push_undo()
        self.tapers.append(taper)
        self.selected_uid = taper.uid
        self._register_nominal(taper)
        self._clear_simulation_overlay()
        self._refresh_tree()
        self.redraw()

    def _add_fiber(self) -> None:
        fiber_number = len(self.fibers) + 1
        if self.fibers:
            position = min(self.final_z, self.fibers[-1].position + 0.02)
        else:
            position = self.final_z * 0.88
        fiber = FiberElement(
            name=f"Fiber {fiber_number}",
            position=position,
            x_offset=(fiber_number - 1) * 50e-6,
        )
        self._push_undo()
        self.fibers.append(fiber)
        self.selected_uid = fiber.uid
        self._register_nominal(fiber)
        self._clear_simulation_overlay()
        self._refresh_tree()
        self.redraw()

    def _delete_selected(self) -> None:
        element = self._element_by_uid(self.selected_uid)
        if element is None:
            messagebox.showinfo("Delete selected", "Select an element first.")
            return
        self._delete_element(element)

    def _delete_from_editor(
        self,
        dialog: tk.Toplevel,
        element: LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement,
    ) -> None:
        if self._delete_element(element, parent=dialog):
            dialog.destroy()

    def _delete_element(
        self,
        element: LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement,
        parent: tk.Misc | None = None,
    ) -> bool:
        if isinstance(element, LaserSource):
            if len(self.sources) <= 1:
                messagebox.showinfo("Delete selected", "Keep at least one laser source.", parent=parent)
                return False
        elif isinstance(element, TaperDetectorElement) and len(self.tapers) <= 1:
            messagebox.showinfo("Delete selected", "Keep at least one taper detector.", parent=parent)
            return False

        confirmed = messagebox.askyesno("Delete element", f"Delete {element.name}?", parent=parent)
        if not confirmed:
            return False

        self._push_undo()
        if isinstance(element, LaserSource):
            self.sources = [source for source in self.sources if source.uid != element.uid]
        elif isinstance(element, LensElement):
            self.lenses = [lens for lens in self.lenses if lens.uid != element.uid]
        elif isinstance(element, BallLensElement):
            self.balls = [ball for ball in self.balls if ball.uid != element.uid]
        elif isinstance(element, FiberElement):
            self.fibers = [fiber for fiber in self.fibers if fiber.uid != element.uid]
        elif isinstance(element, TaperDetectorElement):
            self.tapers = [taper for taper in self.tapers if taper.uid != element.uid]
        self._nominal_states.pop(element.uid, None)
        self.selected_uid = None
        self._clear_simulation_overlay()
        self._refresh_tree()
        self.redraw()
        return True

    def _edit_selected(self) -> None:
        element = self._element_by_uid(self.selected_uid)
        if element is None:
            messagebox.showinfo("Edit selected", "Select an element first.")
            return
        self._open_editor(element)

    def _open_editor(self, element: LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement) -> None:
        fields = self._field_specs_for(element)
        dialog = tk.Toplevel(self)
        dialog.title(f"Edit {element.name}")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)

        variables: dict[str, tk.StringVar] = {}
        for row, spec in enumerate(fields):
            label = spec.label if not spec.unit else f"{spec.label} ({spec.unit})"
            ttk.Label(dialog, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=5)
            raw_value = getattr(element, spec.attr)
            if spec.is_text:
                display_value = str(raw_value)
            else:
                display_value = f"{raw_value * spec.scale:.9g}"
            var = tk.StringVar(value=display_value)
            variables[spec.attr] = var
            ttk.Entry(dialog, textvariable=var, width=28).grid(row=row, column=1, sticky="ew", padx=10, pady=5)

        buttons = ttk.Frame(dialog)
        buttons.grid(row=len(fields), column=0, columnspan=2, sticky="e", padx=10, pady=(8, 10))
        ttk.Button(buttons, text="Delete", command=lambda: self._delete_from_editor(dialog, element)).grid(row=0, column=0, padx=(0, 18))
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).grid(row=0, column=1, padx=(0, 6))
        ttk.Button(buttons, text="Save", command=lambda: self._save_editor(dialog, element, fields, variables)).grid(row=0, column=2)

        dialog.bind("<Return>", lambda _event: self._save_editor(dialog, element, fields, variables))
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_window()

    def _field_specs_for(
        self,
        element: LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement,
    ) -> list[FieldSpec]:
        if isinstance(element, LaserSource):
            return _LASER_FIELDS
        if isinstance(element, LensElement):
            return _LENS_FIELDS
        if isinstance(element, BallLensElement):
            return _BALL_FIELDS
        if isinstance(element, TaperDetectorElement):
            return _TAPER_FIELDS
        return _FIBER_FIELDS

    def _save_editor(
        self,
        dialog: tk.Toplevel,
        element: LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement,
        fields: list[FieldSpec],
        variables: dict[str, tk.StringVar],
    ) -> None:
        previous = {spec.attr: getattr(element, spec.attr) for spec in fields}
        snapshot = self._layout_snapshot()
        new_values: dict[str, str | float] = {}
        changed_attrs: set[str] = set()
        try:
            for spec in fields:
                raw_text = variables[spec.attr].get().strip()
                if spec.is_text:
                    value = raw_text or spec.label
                else:
                    value = float(raw_text) / spec.scale
                if value != previous[spec.attr]:
                    changed_attrs.add(spec.attr)
                new_values[spec.attr] = value
            for attr, value in new_values.items():
                setattr(element, attr, value)
            if isinstance(element, LaserSource):
                self._sync_source_gaussian_parameters(element, changed_attrs)
            self._validate_element(element)
        except ValueError as exc:
            for attr, value in previous.items():
                setattr(element, attr, value)
            messagebox.showerror("Invalid parameter", str(exc), parent=dialog)
            return

        if changed_attrs:
            self._undo_stack.append(snapshot)
            if len(self._undo_stack) > 80:
                del self._undo_stack[0 : len(self._undo_stack) - 80]
        self._clear_simulation_overlay()
        self._refresh_tree()
        self.redraw()
        dialog.destroy()

    def _sync_source_gaussian_parameters(self, source: LaserSource, changed_attrs: set[str]) -> None:
        if source.wavelength <= 0:
            return
        if "rayleigh_range" in changed_attrs:
            if source.rayleigh_range is None or source.rayleigh_range <= 0:
                return
            source.waist_radius = _waist_radius_from_rayleigh(source.wavelength, source.rayleigh_range)
        elif "waist_radius" in changed_attrs or "wavelength" in changed_attrs or source.rayleigh_range is None:
            if source.waist_radius <= 0:
                return
            source.rayleigh_range = _rayleigh_range_from_waist(source.wavelength, source.waist_radius)
        if "rayleigh_range_y" in changed_attrs:
            if source.rayleigh_range_y is None or source.rayleigh_range_y <= 0:
                return
            source.waist_radius_y = _waist_radius_from_rayleigh(source.wavelength, source.rayleigh_range_y)
        elif "waist_radius_y" in changed_attrs or "wavelength" in changed_attrs or source.rayleigh_range_y is None:
            if source.waist_radius_y is None or source.waist_radius_y <= 0:
                return
            source.rayleigh_range_y = _rayleigh_range_from_waist(source.wavelength, source.waist_radius_y)

    def _validate_element(self, element: LaserSource | LensElement | BallLensElement | FiberElement | TaperDetectorElement) -> None:
        for attr, value in element.__dict__.items():
            if attr in {"uid", "name"}:
                continue
            try:
                is_finite = math.isfinite(float(value))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{attr} must be finite") from exc
            if not is_finite:
                raise ValueError(f"{attr} must be finite")

        if element.position < 0:
            raise ValueError("z position must be non-negative")
        if isinstance(element, LaserSource):
            if element.wavelength <= 0:
                raise ValueError("wavelength must be positive")
            if element.waist_radius <= 0:
                raise ValueError("x waist radius must be positive")
            if element.rayleigh_range is None or element.rayleigh_range <= 0:
                raise ValueError("x Rayleigh length must be positive")
            if element.waist_radius_y is None or element.waist_radius_y <= 0:
                raise ValueError("y waist radius must be positive")
            if element.rayleigh_range_y is None or element.rayleigh_range_y <= 0:
                raise ValueError("y Rayleigh length must be positive")
            if element.power < 0:
                raise ValueError("source power must be non-negative")
        elif isinstance(element, LensElement):
            if element.focal_length == 0:
                raise ValueError("focal length must be non-zero")
            if element.aperture_radius <= 0:
                raise ValueError("aperture radius must be positive")
        elif isinstance(element, BallLensElement):
            if element.diameter <= 0:
                raise ValueError("ball diameter must be positive")
            if element.refractive_index <= 1.0:
                raise ValueError("ball refractive index must be greater than 1")
            if element.entry_z < 0:
                raise ValueError("ball entry plane must be non-negative")
        elif isinstance(element, FiberElement):
            if element.mode_field_diameter <= 0:
                raise ValueError("mode-field diameter must be positive")
            if element.cladding_diameter <= 0:
                raise ValueError("cladding diameter must be positive")
            if element.received_power < 0:
                raise ValueError("received power placeholder must be non-negative")
        elif isinstance(element, TaperDetectorElement):
            if element.width <= 0:
                raise ValueError("taper width must be positive")
            if element.height <= 0:
                raise ValueError("taper height must be positive")
            if element.mode_radius_x <= 0:
                raise ValueError("taper mode radius x must be positive")
            if element.mode_radius_y <= 0:
                raise ValueError("taper mode radius y must be positive")
            if not 0.0 <= element.extra_transmission <= 1.0:
                raise ValueError("extra taper transmission must be between 0 and 1")
            if element.facet_refractive_index <= 0:
                raise ValueError("taper facet refractive index must be positive")
            if element.received_power < 0:
                raise ValueError("received power must be non-negative")

    def _apply_final_z(self) -> None:
        try:
            value = float(self.final_z_var.get()) / 1e6
        except ValueError:
            messagebox.showerror("Invalid final z", "Final z must be a number in um.")
            return
        if value <= 0 or not math.isfinite(value):
            messagebox.showerror("Invalid final z", "Final z must be positive and finite.")
            return
        if math.isclose(value, self.final_z):
            self._fit_view_bounds_to_layout()
            self.redraw()
            return
        self._push_undo()
        self.final_z = value
        self._clear_simulation_overlay()
        self._fit_view_bounds_to_layout()
        self.redraw()

    def _zoom_in(self) -> None:
        self._zoom_by(1.35)

    def _zoom_out(self) -> None:
        self._zoom_by(1.0 / 1.35)

    def _zoom_by(self, factor: float) -> None:
        self._view_zoom = min(max(self._view_zoom * factor, 1.0), 80.0)
        self.redraw()

    def _reset_view(self) -> None:
        self._view_zoom = 1.0
        self._zoom_anchor_z = None
        self._zoom_anchor_x = None
        self._zoom_anchor_z_fraction = 0.5
        self._zoom_anchor_x_fraction = 0.5
        self.redraw()

    def _reset_defaults(self) -> None:
        default_balls, default_tapers, default_final_z = default_ball_lens_layout()
        self._push_undo()
        self.sources = [LaserSource()]
        self.lenses = []
        self.balls = default_balls
        self.fibers = []
        self.tapers = default_tapers
        self.final_z = default_final_z
        self.final_z_var.set(f"{self.final_z * 1e6:.2f}")
        self.selected_uid = None
        self._view_zoom = 1.0
        self._zoom_anchor_z = None
        self._zoom_anchor_x = None
        self._zoom_anchor_z_fraction = 0.5
        self._zoom_anchor_x_fraction = 0.5
        self._clear_simulation_overlay()
        self._fit_view_bounds_to_layout()
        self._capture_nominal_layout()
        self._refresh_tree()
        self.redraw()

    def _capture_nominal_layout(self) -> None:
        self._nominal_states = {
            element.uid: capture_element_nominal(element) for element in self._all_elements()
        }

    def _register_nominal(self, element: LayoutElement) -> None:
        self._nominal_states[element.uid] = capture_element_nominal(element)

    def _nominal_for(self, element: LayoutElement) -> NominalElementState:
        nominal = self._nominal_states.get(element.uid)
        if nominal is None:
            nominal = capture_element_nominal(element)
            self._nominal_states[element.uid] = nominal
        return nominal

    def _apply_layout_change(self, status: str) -> None:
        self._clear_simulation_overlay()
        self._fit_view_bounds_to_layout()
        self._refresh_tree()
        self.redraw()
        self.status_var.set(status)
        self._simulate()

    def _validate_layout(self) -> bool:
        try:
            for element in self._all_elements():
                self._validate_element(element)
        except ValueError as exc:
            messagebox.showerror("Invalid layout", str(exc))
            return False
        return True

    def _align_all(self) -> None:
        for element in self._all_elements():
            apply_nominal_state(element, self._nominal_for(element))
        self._apply_layout_change("All elements restored to nominal aligned positions.")

    def _scramble_transverse_message(self, scope: str) -> str:
        transverse_um = TRANSVERSE_TOLERANCE * 1e6
        return (
            f"{scope} scrambled relative to perfect alignment "
            f"(+0 to {transverse_um:g} µm x/y from nominal)."
        )

    def _scramble_lens_message(self, scope: str) -> str:
        axial_um = AXIAL_TOLERANCE * 1e6
        transverse_um = TRANSVERSE_TOLERANCE * 1e6
        return (
            f"{scope} scrambled relative to perfect alignment "
            f"(+0 to {axial_um:g} µm z, +0 to {transverse_um:g} µm x/y from nominal)."
        )

    def _scramble_laser_fibre(self) -> None:
        rng = random.Random()
        for element in [*self.sources, *self.fibers, *self.tapers]:
            scramble_element_positive(element, self._nominal_for(element), rng=rng)
        if not self._validate_layout():
            return
        self._apply_layout_change(self._scramble_transverse_message("Laser, fibre, and taper positions"))

    def _scramble_full(self) -> None:
        rng = random.Random()
        for element in [*self.lenses, *self.balls]:
            scramble_element_positive(element, self._nominal_for(element), rng=rng)
        for element in [*self.sources, *self.fibers, *self.tapers]:
            scramble_element_positive(element, self._nominal_for(element), rng=rng)
        if not self._validate_layout():
            return
        self._apply_layout_change(
            "All elements scrambled relative to perfect alignment "
            f"(lenses: +0 to {AXIAL_TOLERANCE * 1e6:g} µm z and +0 to {TRANSVERSE_TOLERANCE * 1e6:g} µm x/y from nominal; "
            f"laser/fibre/taper: +0 to {TRANSVERSE_TOLERANCE * 1e6:g} µm x/y from nominal only)."
        )

    def _simulate(self) -> None:
        results = simulate_layout(
            self.sources,
            self.lenses,
            self.fibers,
            self.final_z,
            balls=self.balls,
            tapers=self.tapers,
            refractive_index=DEFAULT_REFRACTIVE_INDEX,
            clipping_radius_factor=DEFAULT_CLIPPING_RADIUS_FACTOR,
        )
        for fiber in self.fibers:
            fiber.received_power = 0.0
        for taper in self.tapers:
            taper.received_power = 0.0
        for result in results:
            for fiber_result in result.fiber_results:
                fiber_result.fiber.received_power += fiber_result.received_power
            for taper_result in result.taper_results:
                taper_result.taper.received_power += taper_result.received_power

        self._beam_paths = self._build_beam_paths()
        report = format_simulation_report(results)
        self._write_output_report(report)
        print(report)
        self._refresh_tree()
        self.redraw()
        self.status_var.set("Simulation complete: detector and received powers updated.")

    def _write_output_report(self, report: str) -> None:
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, report)
        self.output.tag_configure("summary", foreground="#0b5c2a", font=("Consolas", 10, "bold"))
        self.output.tag_configure("best", foreground="#9b1c1c", font=("Consolas", 10, "bold"))
        self.output.tag_configure("coupling", foreground="#0b3d91", font=("Consolas", 9, "bold"))
        for line_number, line in enumerate(report.splitlines(), start=1):
            start = f"{line_number}.0"
            end = f"{line_number}.end"
            if line.startswith("COUPLING SUMMARY"):
                self.output.tag_add("summary", start, end)
            elif line.startswith("BEST:") or line.startswith("BEST DETECTOR:"):
                self.output.tag_add("best", start, end)
            elif "COUPLING =" in line or "MODE =" in line or "TOTAL =" in line:
                self.output.tag_add("coupling", start, end)

    def _configuration_snippet(self) -> str:
        sources = sorted(self.sources, key=lambda source: source.position)
        lenses = sorted(self.lenses, key=lambda lens: lens.position)
        balls = sorted(self.balls, key=lambda ball: ball.position)
        fibers = sorted(self.fibers, key=lambda fiber: fiber.position)
        tapers = sorted(self.tapers, key=lambda taper: taper.position)
        primary_source = sources[0]
        lines = [
            "# Captured from interactive_setup.py",
            "# Values are SI units.",
            "# Detector/fiber received_power values are updated by the interactive simulation.",
            f"WAVELENGTH = {primary_source.wavelength:.12g}",
            f"INPUT_WAIST_RADIUS = {primary_source.waist_radius:.12g}",
            f"INPUT_WAIST_RADIUS_Y = {primary_source.waist_radius_y:.12g}",
            f"INPUT_RAYLEIGH_RANGE = {primary_source.rayleigh_range:.12g}",
            f"INPUT_RAYLEIGH_RANGE_Y = {primary_source.rayleigh_range_y:.12g}",
            f"WAIST_POSITION = {primary_source.waist_position:.12g}",
            f"INPUT_POWER = {primary_source.power:.12g}",
            "",
            "LASER = LaserAlignment(",
            f"    x_offset={primary_source.x_offset:.12g},",
            f"    y_offset={primary_source.y_offset:.12g},",
            f"    x_angle={primary_source.x_angle:.12g},",
            f"    y_angle={primary_source.y_angle:.12g},",
            ")",
            "",
            f"START_Z = {primary_source.position:.12g}",
            f"FINAL_Z = {self.final_z:.12g}",
            "",
            "SOURCES = [",
        ]
        for source in sources:
            lines.extend(
                [
                    "    {",
                    f"        \"name\": {source.name!r},",
                    f"        \"position\": {source.position:.12g},",
                    f"        \"wavelength\": {source.wavelength:.12g},",
                    f"        \"waist_radius\": {source.waist_radius:.12g},",
                    f"        \"waist_radius_y\": {source.waist_radius_y:.12g},",
                    f"        \"rayleigh_range\": {source.rayleigh_range:.12g},",
                    f"        \"rayleigh_range_y\": {source.rayleigh_range_y:.12g},",
                    f"        \"waist_position\": {source.waist_position:.12g},",
                    f"        \"power\": {source.power:.12g},",
                    "        \"alignment\": LaserAlignment(",
                    f"            x_offset={source.x_offset:.12g},",
                    f"            y_offset={source.y_offset:.12g},",
                    f"            x_angle={source.x_angle:.12g},",
                    f"            y_angle={source.y_angle:.12g},",
                    "        ),",
                    "    },",
                ]
            )
        lines.extend(
            [
                "]",
                "",
                "PRIMARY_SOURCE = SOURCES[0]",
                "",
                "BALL_LENSES = [",
            ]
        )
        for ball in balls:
            lines.extend(
                [
                    "    {",
                    f"        \"name\": {ball.name!r},",
                    f"        \"position\": {ball.position:.12g},",
                    f"        \"diameter\": {ball.diameter:.12g},",
                    f"        \"refractive_index\": {ball.refractive_index:.12g},",
                    f"        \"x_offset\": {ball.x_offset:.12g},",
                    f"        \"y_offset\": {ball.y_offset:.12g},",
                    "    },",
                ]
            )
        lines.extend(
            [
                "]",
                "",
                "TAPER_DETECTORS = [",
            ]
        )
        for taper in tapers:
            lines.extend(
                [
                    "    {",
                    f"        \"name\": {taper.name!r},",
                    f"        \"position\": {taper.position:.12g},",
                    f"        \"width\": {taper.width:.12g},",
                    f"        \"height\": {taper.height:.12g},",
                    f"        \"mode_radius_x\": {taper.mode_radius_x:.12g},",
                    f"        \"mode_radius_y\": {taper.mode_radius_y:.12g},",
                    f"        \"extra_transmission\": {taper.extra_transmission:.12g},",
                    f"        \"facet_refractive_index\": {taper.facet_refractive_index:.12g},",
                    f"        \"x_offset\": {taper.x_offset:.12g},",
                    f"        \"y_offset\": {taper.y_offset:.12g},",
                    f"        \"received_power\": {taper.received_power:.12g},",
                    "    },",
                ]
            )
        lines.extend(
            [
                "]",
                "",
            "LENSES = [",
            ]
        )
        for lens in lenses:
            lines.extend(
                [
                    "    LensSpec(",
                    f"        position={lens.position:.12g},",
                    f"        focal_length={lens.focal_length:.12g},",
                    f"        aperture_radius={lens.aperture_radius:.12g},",
                    f"        x_offset={lens.x_offset:.12g},",
                    f"        y_offset={lens.y_offset:.12g},",
                    "    ),",
                ]
            )
        lines.extend(
            [
                "]",
                "",
                "FIBERS = [",
            ]
        )
        for fiber in fibers:
            lines.extend(
                [
                    "    {",
                    "        \"spec\": FiberSpec(",
                    f"            position={fiber.position:.12g},",
                    f"            mode_field_diameter={fiber.mode_field_diameter:.12g},",
                    f"            x_offset={fiber.x_offset:.12g},",
                    f"            y_offset={fiber.y_offset:.12g},",
                    f"            name={fiber.name!r},",
                    f"            cladding_diameter={fiber.cladding_diameter:.12g},",
                    "        ),",
                    f"        \"received_power\": {fiber.received_power:.12g},",
                    "    },",
                ]
            )
        lines.append("]")
        return "\n".join(lines)


def main() -> None:
    app = OpticalLayoutEditor()
    app.mainloop()


if __name__ == "__main__":
    main()
