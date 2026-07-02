"""Power-only two-ball-lens coordinate scan."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .base import (
    DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
    DEFAULT_TARGET_MODE_EFFICIENCY,
    AlignmentAlgorithmResult,
    AlignmentDevice,
    LensPose,
    PowerReading,
)
from .given_positions import move_to_starting_poses


TRANSVERSE_SCAN_SCHEDULE: tuple[tuple[float, int], ...] = (
    (5.0e-6, 8),
    (2.0e-6, 4),
    (1.0e-6, 3),
    (0.5e-6, 3),
    (0.25e-6, 3),
)
WIDE_TRANSVERSE_SCAN_SCHEDULE: tuple[tuple[float, int], ...] = (
    (10.0e-6, 8),
    (5.0e-6, 8),
    (2.0e-6, 4),
    (1.0e-6, 3),
    (0.5e-6, 3),
    (0.25e-6, 3),
)
EXTRA_WIDE_TRANSVERSE_SCAN_SCHEDULE: tuple[tuple[float, int], ...] = (
    (15.0e-6, 8),
    (7.5e-6, 8),
    (3.0e-6, 5),
    (1.0e-6, 4),
    (0.5e-6, 3),
    (0.25e-6, 3),
)
TRANSVERSE_SCAN_ATTEMPTS: tuple[tuple[tuple[float, int], ...], ...] = (
    TRANSVERSE_SCAN_SCHEDULE,
    WIDE_TRANSVERSE_SCAN_SCHEDULE,
    EXTRA_WIDE_TRANSVERSE_SCAN_SCHEDULE,
)
POWER_RELATIVE_TOLERANCE = 1.0e-12
POWER_ABSOLUTE_TOLERANCE = 1.0e-18
POSITION_ABSOLUTE_TOLERANCE = 1.0e-15


@dataclass(frozen=True)
class _ScanAxis:
    """A virtual two-lens coordinate: common shift or differential tilt control."""

    name: str
    dimension: int
    differential: bool = False


COMMON_X = _ScanAxis("common x", 0)
DIFFERENTIAL_X = _ScanAxis("differential x", 0, differential=True)
COMMON_Y = _ScanAxis("common y", 1)
DIFFERENTIAL_Y = _ScanAxis("differential y", 1, differential=True)
TRANSVERSE_AXES = (COMMON_X, DIFFERENTIAL_X, COMMON_Y, DIFFERENTIAL_Y)


def _is_better(candidate: PowerReading, best: PowerReading) -> bool:
    improvement = candidate.received_power - best.received_power
    threshold = max(POWER_ABSOLUTE_TOLERANCE, abs(best.received_power) * POWER_RELATIVE_TOLERANCE)
    return improvement > threshold


def _axis_coordinate(poses: tuple[LensPose, ...], axis: _ScanAxis) -> float:
    first = poses[0][axis.dimension]
    second = poses[1][axis.dimension]
    if axis.differential:
        return 0.5 * (first - second)
    return 0.5 * (first + second)


class CoordinateScanAlgorithm:
    name = "coordinate_scan"
    display_name = "Power-only coordinate scan"

    def __init__(
        self,
        *,
        target_mode_efficiency: float = DEFAULT_TARGET_MODE_EFFICIENCY,
        max_attempts: int = DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
        scan_attempts: tuple[tuple[tuple[float, int], ...], ...] = TRANSVERSE_SCAN_ATTEMPTS,
    ) -> None:
        self.target_mode_efficiency = target_mode_efficiency
        self.max_attempts = max(1, max_attempts)
        self.scan_attempts = scan_attempts or (TRANSVERSE_SCAN_SCHEDULE,)

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        starting_poses = device.starting_poses()
        current_poses = device.current_poses()
        if len(starting_poses) != len(current_poses):
            raise ValueError("starting pose count does not match the current lens count")
        if len(current_poses) != 2:
            raise ValueError("power-only coordinate scan currently supports exactly two lenses")

        best = device.measure()
        best_poses = device.current_poses()
        final_reading = best
        success = False
        attempts_used = 0

        while not success and attempts_used < self.max_attempts:
            schedule_index = min(attempts_used, len(self.scan_attempts) - 1)
            schedule = self.scan_attempts[schedule_index]
            attempts_used += 1

            reference_reading = move_to_starting_poses(device, starting_poses)
            if _is_better(reference_reading, best):
                best = reference_reading
                best_poses = device.current_poses()

            for step, radius_steps in schedule:
                for axis in TRANSVERSE_AXES:
                    reading = self._scan_axis(device, axis, step, radius_steps)
                    if _is_better(reading, best):
                        best = reading
                        best_poses = device.current_poses()

            final_reading = device.measure()
            if _is_better(final_reading, best):
                best = final_reading
                best_poses = device.current_poses()
            success = final_reading.mode_efficiency >= self.target_mode_efficiency

        if not success and device.current_poses() != best_poses:
            final_reading = move_to_starting_poses(device, best_poses)
            success = final_reading.mode_efficiency >= self.target_mode_efficiency

        outcome = "reached" if success else "did not reach"
        return AlignmentAlgorithmResult(
            name=self.name,
            display_name=self.display_name,
            final_poses=device.current_poses(),
            final_reading=final_reading,
            move_history=device.move_history(),
            message=(
                "Moved to starting aligned lens poses, then ran bounded power-only "
                f"common/differential scans; {outcome} "
                f"{self.target_mode_efficiency * 100:.0f}% mode match in {attempts_used} "
                f"attempt(s); best power {best.received_power * 1e3:.6g} mW."
            ),
        )

    def _scan_axis(
        self,
        device: AlignmentDevice,
        axis: _ScanAxis,
        step: float,
        radius_steps: int,
    ) -> PowerReading:
        center = _axis_coordinate(device.current_poses(), axis)
        best_value = center
        best_reading = device.measure()

        for offset_index in range(-radius_steps, radius_steps + 1):
            target_value = center + offset_index * step
            reading = self._set_axis_coordinate(device, axis, target_value)
            if _is_better(reading, best_reading):
                best_reading = reading
                best_value = target_value

        final_reading = self._set_axis_coordinate(device, axis, best_value)
        return final_reading if final_reading.received_power >= best_reading.received_power else best_reading

    def _set_axis_coordinate(
        self,
        device: AlignmentDevice,
        axis: _ScanAxis,
        target_value: float,
    ) -> PowerReading:
        current_value = _axis_coordinate(device.current_poses(), axis)
        delta = target_value - current_value
        if math.isclose(delta, 0.0, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE):
            return device.measure()

        if axis.differential:
            first = self._move_lens_dimension(device, 0, axis.dimension, delta)
            second = self._move_lens_dimension(device, 1, axis.dimension, -delta)
            return second if second.measurement_count >= first.measurement_count else first

        first = self._move_lens_dimension(device, 0, axis.dimension, delta)
        second = self._move_lens_dimension(device, 1, axis.dimension, delta)
        return second if second.measurement_count >= first.measurement_count else first

    def _move_lens_dimension(
        self,
        device: AlignmentDevice,
        lens_index: int,
        dimension: int,
        delta: float,
    ) -> PowerReading:
        if dimension == 0:
            return device.move_lens(lens_index, dx=delta)
        if dimension == 1:
            return device.move_lens(lens_index, dy=delta)
        if dimension == 2:
            return device.move_lens(lens_index, dz=delta)
        raise ValueError(f"unsupported pose dimension: {dimension}")
