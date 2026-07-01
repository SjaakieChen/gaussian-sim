"""Shared interfaces for step-based alignment algorithms."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, TypeAlias


LensPose: TypeAlias = tuple[float, float, float]


@dataclass(frozen=True)
class PowerReading:
    received_power: float
    total_efficiency: float
    mode_efficiency: float
    move_count: int = 0
    measurement_count: int = 0


@dataclass(frozen=True)
class AlignmentMove:
    lens_index: int
    dx: float
    dy: float
    dz: float
    poses: tuple[LensPose, ...]
    reading: PowerReading


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
    def current_poses(self) -> tuple[LensPose, ...]:
        """Return current lens poses as (x_offset, y_offset, z_position)."""

    def move_lens(
        self,
        lens_index: int,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
    ) -> PowerReading:
        """Apply one discrete lens move and return the measured power."""

    def move_lens_to(
        self,
        lens_index: int,
        x_offset: float,
        y_offset: float,
        z_position: float,
    ) -> PowerReading:
        """Move one lens to an absolute coordinate and return the measured power."""

    def coordinate_reference_point(self) -> LensPose:
        """Return one lab-provided reference point for coordinate-based moves."""

    def measure(self) -> PowerReading:
        """Measure power without moving any lens."""

    def move_history(self) -> tuple[AlignmentMove, ...]:
        """Return the sequence of discrete moves made by the algorithm."""


class AlignmentAlgorithm(Protocol):
    name: str
    display_name: str

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        """Run against a step-based alignment device."""
