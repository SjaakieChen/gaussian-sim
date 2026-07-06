"""Shared interfaces for step-based alignment algorithms."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeAlias


LensPose: TypeAlias = tuple[float, float, float]

DEFAULT_TARGET_MODE_EFFICIENCY = 0.50
DEFAULT_MAX_ALIGNMENT_ATTEMPTS = 3


@dataclass(frozen=True)
class SourceGeometry:
    name: str
    position: float
    wavelength: float
    waist_radius: float
    waist_radius_y: float
    rayleigh_range: float
    rayleigh_range_y: float
    waist_position: float
    power: float
    x_offset: float
    y_offset: float
    x_angle: float
    y_angle: float


@dataclass(frozen=True)
class BallLensGeometry:
    name: str
    position: float
    diameter: float
    refractive_index: float
    x_offset: float
    y_offset: float

    @property
    def radius(self) -> float:
        return 0.5 * self.diameter

    @property
    def entry_z(self) -> float:
        return self.position - self.radius

    @property
    def exit_z(self) -> float:
        return self.position + self.radius


@dataclass(frozen=True)
class BallLensNoGoZone:
    name: str
    z_min: float
    z_max: float
    x_min: float | None = None
    x_max: float | None = None
    y_min: float | None = None
    y_max: float | None = None
    applies_to_all_x: bool = True
    label: str = ""

    @property
    def z_low(self) -> float:
        return min(self.z_min, self.z_max)

    @property
    def z_high(self) -> float:
        return max(self.z_min, self.z_max)

    def overlaps_z_span(self, entry_z: float, exit_z: float) -> bool:
        return exit_z > self.z_low and entry_z < self.z_high

    def intersects_ball_pose(self, pose: LensPose, radius: float) -> bool:
        x_offset, y_offset, z_position = pose
        if not self.overlaps_z_span(z_position - radius, z_position + radius):
            return False
        ball_x_min = x_offset - radius
        ball_x_max = x_offset + radius
        zone_x_min = float("-inf") if self.x_min is None else self.x_min
        zone_x_max = float("inf") if self.x_max is None else self.x_max
        if not (ball_x_max > zone_x_min and ball_x_min < zone_x_max):
            return False
        ball_y_min = y_offset - radius
        ball_y_max = y_offset + radius
        zone_y_min = float("-inf") if self.y_min is None else self.y_min
        zone_y_max = float("inf") if self.y_max is None else self.y_max
        return ball_y_max > zone_y_min and ball_y_min < zone_y_max


@dataclass(frozen=True)
class TaperGeometry:
    name: str
    position: float
    width: float
    height: float
    mode_radius_x: float
    mode_radius_y: float
    extra_transmission: float
    facet_refractive_index: float
    x_offset: float
    y_offset: float


@dataclass(frozen=True)
class AlignmentModelGeometry:
    source: SourceGeometry
    taper: TaperGeometry
    balls: tuple[BallLensGeometry, ...]
    current_poses: tuple[LensPose, ...]
    starting_poses: tuple[LensPose, ...]
    clipping_radius_factor: float
    no_go_zones: tuple[BallLensNoGoZone, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PowerReading:
    received_power: float
    total_efficiency: float
    mode_efficiency: float
    move_count: int = 0
    measurement_count: int = 0
    noise_delta: float = 0.0
    noise_delta: float = 0.0


@dataclass(frozen=True)
class AlignmentMove:
    lens_index: int
    dx: float
    dy: float
    dz: float
    poses: tuple[LensPose, ...]
    reading: PowerReading
    direction_method: str | None = None


@dataclass(frozen=True)
class AlignmentAlgorithmResult:
    name: str
    display_name: str
    final_poses: tuple[LensPose, ...]
    final_reading: PowerReading
    move_history: tuple[AlignmentMove, ...] = field(default_factory=tuple)
    message: str = ""

    @property
    def received_power(self) -> float:
        return self.final_reading.received_power

    @property
    def total_efficiency(self) -> float:
        return self.final_reading.total_efficiency

    @property
    def mode_efficiency(self) -> float:
        return self.final_reading.mode_efficiency

    @property
    def move_count(self) -> int:
        return self.final_reading.move_count

    @property
    def evaluations(self) -> int:
        return self.final_reading.measurement_count


class AlignmentDevice(Protocol):
    def starting_poses(self) -> tuple[LensPose, ...]:
        """Return known aligned/reference lens poses as (x_offset, y_offset, z_position)."""

    def current_poses(self) -> tuple[LensPose, ...]:
        """Return current lens poses as (x_offset, y_offset, z_position)."""

    def model_geometry(self) -> AlignmentModelGeometry:
        """Return a read-only geometry snapshot for noiseless model-based algorithms."""

    def move_lens(
        self,
        lens_index: int,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
    ) -> PowerReading:
        """Apply one discrete lens move and return the measured power."""

    def measure(self) -> PowerReading:
        """Measure power without moving any lens."""

    def move_history(self) -> tuple[AlignmentMove, ...]:
        """Return the sequence of discrete moves made by the algorithm."""


class AlignmentAlgorithm(Protocol):
    name: str
    display_name: str

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        """Run against a step-based alignment device."""
