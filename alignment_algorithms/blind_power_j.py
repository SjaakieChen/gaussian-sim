"""Blind power-only J-like local alignment."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .base import (
    AlignmentAlgorithmResult,
    AlignmentDevice,
    LensPose,
    PowerReading,
)


BLIND_POWER_J_STEPS = (5.0e-6, 2.0e-6, 1.0e-6, 0.5e-6, 0.25e-6)
BLIND_POWER_J_ATTEMPTS = 1
BLIND_POWER_J_MAX_CORRECTION = 25.0e-6
BLIND_POWER_J_SAMPLES_PER_POINT = 1
DIRECTION_METHOD_NEWTON = "Newton (quadratic peak)"
DIRECTION_METHOD_GRADIENT = "Gradient fallback"
DIRECTION_METHOD_BEST_OF_9 = "Best-of-9 probe fallback"
POWER_ABSOLUTE_TOLERANCE = 1.0e-18
POWER_RELATIVE_TOLERANCE = 1.0e-12
POSITION_ABSOLUTE_TOLERANCE = 1.0e-15
SINGULAR_CONDITION_LIMIT = 1.0e12


@dataclass(frozen=True)
class _PowerSample:
    offsets: tuple[float, float]
    reading: PowerReading


def _is_better(candidate: PowerReading, best: PowerReading) -> bool:
    improvement = candidate.received_power - best.received_power
    threshold = max(POWER_ABSOLUTE_TOLERANCE, abs(best.received_power) * POWER_RELATIVE_TOLERANCE)
    return improvement > threshold


def _move_transverse_to_poses(
    device: AlignmentDevice,
    target_poses: tuple[LensPose, ...],
    *,
    direction_method: str | None = None,
) -> PowerReading:
    _set_move_direction_method(device, direction_method)
    try:
        reading: PowerReading | None = None
        for lens_index, target_pose in enumerate(target_poses):
            current_pose = device.current_poses()[lens_index]
            dx = target_pose[0] - current_pose[0]
            dy = target_pose[1] - current_pose[1]
            if (
                math.isclose(dx, 0.0, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE)
                and math.isclose(dy, 0.0, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE)
            ):
                continue
            reading = device.move_lens(lens_index, dx=dx, dy=dy)
        return reading if reading is not None else device.measure()
    finally:
        _set_move_direction_method(device, None)


def _set_move_direction_method(device: AlignmentDevice, direction_method: str | None) -> None:
    setter = getattr(device, "set_next_move_direction_method", None)
    if setter is not None:
        setter(direction_method)


def _target_poses_for_axis_offsets(
    center_poses: tuple[LensPose, ...],
    axis: str,
    offsets: tuple[float, float],
) -> tuple[LensPose, ...]:
    poses = []
    for pose, offset in zip(center_poses, offsets):
        if axis == "x":
            poses.append((pose[0] + offset, pose[1], pose[2]))
        elif axis == "y":
            poses.append((pose[0], pose[1] + offset, pose[2]))
        else:
            raise ValueError(f"unsupported blind power-J axis: {axis}")
    return tuple(poses)


def _average_reading(first: PowerReading, others: list[PowerReading]) -> PowerReading:
    readings = [first, *others]
    count = len(readings)
    last = readings[-1]
    return PowerReading(
        received_power=sum(reading.received_power for reading in readings) / count,
        total_efficiency=sum(reading.total_efficiency for reading in readings) / count,
        mode_efficiency=sum(reading.mode_efficiency for reading in readings) / count,
        move_count=last.move_count,
        measurement_count=last.measurement_count,
    )


def _measure_average(device: AlignmentDevice, samples_per_point: int) -> PowerReading:
    first = device.measure()
    others = [device.measure() for _index in range(max(0, samples_per_point - 1))]
    return _average_reading(first, others)


class BlindPowerJAlgorithm:
    name = "blind_power_j"
    display_name = "Blind power J"
    direction_methods = (
        DIRECTION_METHOD_NEWTON,
        DIRECTION_METHOD_GRADIENT,
        DIRECTION_METHOD_BEST_OF_9,
    )

    def __init__(
        self,
        *,
        max_attempts: int = BLIND_POWER_J_ATTEMPTS,
        steps: tuple[float, ...] = BLIND_POWER_J_STEPS,
        max_correction: float = BLIND_POWER_J_MAX_CORRECTION,
        samples_per_point: int = BLIND_POWER_J_SAMPLES_PER_POINT,
        direction_methods: tuple[str, ...] | None = None,
    ) -> None:
        self.max_attempts = max(1, max_attempts)
        self.steps = steps or BLIND_POWER_J_STEPS
        self.max_correction = max_correction
        self.samples_per_point = max(1, samples_per_point)
        self.direction_methods = direction_methods or self.direction_methods

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        current_poses = device.current_poses()
        if len(current_poses) != 2:
            raise ValueError("blind power-J currently supports exactly two lenses")

        final_reading = device.measure()
        best_reading = final_reading
        best_poses = device.current_poses()

        for _attempt in range(self.max_attempts):
            for step in self.steps:
                for axis in ("x", "y"):
                    final_reading = self._optimize_axis_pair(device, axis, step)
                    if _is_better(final_reading, best_reading):
                        best_reading = final_reading
                        best_poses = device.current_poses()

        final_reading = _measure_average(device, self.samples_per_point)
        if _is_better(final_reading, best_reading):
            best_reading = final_reading
            best_poses = device.current_poses()
        elif device.current_poses() != best_poses:
            final_reading = _move_transverse_to_poses(device, best_poses)
            best_reading = final_reading

        return AlignmentAlgorithmResult(
            name=self.name,
            display_name=self.display_name,
            final_poses=device.current_poses(),
            final_reading=best_reading,
            move_history=device.move_history(),
            message="Blind power-only local quadratic/J step using lens x/y coordinates and power.",
        )

    def _optimize_axis_pair(self, device: AlignmentDevice, axis: str, step: float) -> PowerReading:
        center_poses = device.current_poses()
        samples = self._sample_local_quadratic(device, axis, step, center_poses)
        base = samples[(0.0, 0.0)].reading

        if DIRECTION_METHOD_NEWTON in self.direction_methods:
            correction = self._quadratic_peak_offset(samples, step)
            if correction is not None:
                candidate_average = self._try_axis_correction(
                    device,
                    axis,
                    center_poses,
                    correction,
                    DIRECTION_METHOD_NEWTON,
                )
                if _is_better(candidate_average, base):
                    return candidate_average
                _move_transverse_to_poses(device, center_poses)

        if DIRECTION_METHOD_GRADIENT in self.direction_methods:
            correction = self._gradient_fallback_offset(samples, step)
            candidate_average = self._try_axis_correction(
                device,
                axis,
                center_poses,
                correction,
                DIRECTION_METHOD_GRADIENT,
            )
            if _is_better(candidate_average, base):
                return candidate_average
            _move_transverse_to_poses(device, center_poses)

        if DIRECTION_METHOD_BEST_OF_9 in self.direction_methods:
            best_sample = max(samples.values(), key=lambda sample: sample.reading.received_power)
            if _is_better(best_sample.reading, base):
                best_poses = _target_poses_for_axis_offsets(center_poses, axis, best_sample.offsets)
                return _move_transverse_to_poses(
                    device,
                    best_poses,
                    direction_method=DIRECTION_METHOD_BEST_OF_9,
                )
        return _measure_average(device, self.samples_per_point)

    def _try_axis_correction(
        self,
        device: AlignmentDevice,
        axis: str,
        center_poses: tuple[LensPose, ...],
        correction: tuple[float, float],
        direction_method: str,
    ) -> PowerReading:
        candidate_poses = _target_poses_for_axis_offsets(center_poses, axis, correction)
        candidate_reading = _move_transverse_to_poses(
            device,
            candidate_poses,
            direction_method=direction_method,
        )
        return _average_reading(
            candidate_reading,
            [device.measure() for _index in range(max(0, self.samples_per_point - 1))],
        )

    def _sample_local_quadratic(
        self,
        device: AlignmentDevice,
        axis: str,
        step: float,
        center_poses: tuple[LensPose, ...],
    ) -> dict[tuple[float, float], _PowerSample]:
        offsets_to_sample = (
            (0.0, 0.0),
            (step, 0.0),
            (-step, 0.0),
            (0.0, step),
            (0.0, -step),
            (step, step),
            (step, -step),
            (-step, step),
            (-step, -step),
        )
        samples: dict[tuple[float, float], _PowerSample] = {}
        for offsets in offsets_to_sample:
            target_poses = _target_poses_for_axis_offsets(center_poses, axis, offsets)
            first = _move_transverse_to_poses(device, target_poses)
            averaged = _average_reading(
                first,
                [device.measure() for _index in range(max(0, self.samples_per_point - 1))],
            )
            samples[offsets] = _PowerSample(offsets=offsets, reading=averaged)
            _move_transverse_to_poses(device, center_poses)
        return samples

    def _quadratic_peak_offset(
        self,
        samples: dict[tuple[float, float], _PowerSample],
        step: float,
    ) -> tuple[float, float] | None:
        center = samples[(0.0, 0.0)].reading.received_power
        p10 = samples[(step, 0.0)].reading.received_power
        m10 = samples[(-step, 0.0)].reading.received_power
        p01 = samples[(0.0, step)].reading.received_power
        m01 = samples[(0.0, -step)].reading.received_power
        pp = samples[(step, step)].reading.received_power
        pm = samples[(step, -step)].reading.received_power
        mp = samples[(-step, step)].reading.received_power
        mm = samples[(-step, -step)].reading.received_power

        gradient = np.array([(p10 - m10) / (2.0 * step), (p01 - m01) / (2.0 * step)], dtype=float)
        hessian = np.array(
            [
                [(p10 - 2.0 * center + m10) / step**2, (pp - pm - mp + mm) / (4.0 * step**2)],
                [(pp - pm - mp + mm) / (4.0 * step**2), (p01 - 2.0 * center + m01) / step**2],
            ],
            dtype=float,
        )
        if not np.all(np.isfinite(gradient)) or not np.all(np.isfinite(hessian)):
            return None
        if np.linalg.cond(hessian) > SINGULAR_CONDITION_LIMIT:
            return None

        try:
            correction = -np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            return None
        if not np.all(np.isfinite(correction)) or float(np.dot(gradient, correction)) < 0.0:
            return None
        return self._clamp_offset(correction)

    def _gradient_fallback_offset(
        self,
        samples: dict[tuple[float, float], _PowerSample],
        step: float,
    ) -> tuple[float, float]:
        center = samples[(0.0, 0.0)].reading.received_power
        p10 = samples[(step, 0.0)].reading.received_power
        m10 = samples[(-step, 0.0)].reading.received_power
        p01 = samples[(0.0, step)].reading.received_power
        m01 = samples[(0.0, -step)].reading.received_power
        gradient = np.array([(p10 - m10) / (2.0 * step), (p01 - m01) / (2.0 * step)], dtype=float)
        if np.all(np.isfinite(gradient)) and np.max(np.abs(gradient)) > 0.0:
            return self._clamp_offset(step * gradient / np.max(np.abs(gradient)))

        best = max(samples.values(), key=lambda sample: sample.reading.received_power)
        if best.reading.received_power > center:
            return best.offsets
        return (0.0, 0.0)

    def _clamp_offset(self, offset: np.ndarray) -> tuple[float, float]:
        largest = float(np.max(np.abs(offset)))
        if largest > self.max_correction:
            offset = offset * (self.max_correction / largest)
        return (float(offset[0]), float(offset[1]))


class BlindPowerJNewtonAlgorithm(BlindPowerJAlgorithm):
    name = "blind_power_j_newton"
    display_name = "Blind power J: Newton"
    direction_methods = (DIRECTION_METHOD_NEWTON,)


class BlindPowerJGradientAlgorithm(BlindPowerJAlgorithm):
    name = "blind_power_j_gradient"
    display_name = "Blind power J: Gradient"
    direction_methods = (DIRECTION_METHOD_GRADIENT,)


class BlindPowerJBestOf9Algorithm(BlindPowerJAlgorithm):
    name = "blind_power_j_best_of_9"
    display_name = "Blind power J: Best-of-9"
    direction_methods = (DIRECTION_METHOD_BEST_OF_9,)
