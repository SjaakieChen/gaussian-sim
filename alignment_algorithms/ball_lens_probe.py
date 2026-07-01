"""Ball-lens probe alignment algorithm.

The routine models a lab workflow where one ball lens is used to find a
measurable focus and the other ball lens is swept through the beam as an
occluding probe. Three or more probe positions with reduced detector power are
fit to a circle; the fitted circle centre is used as the inferred beam centre.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .base import AlignmentAlgorithmResult, AlignmentDevice, LensPose, PowerReading


SAFE_X_OFFSET = 300e-6
FOCUS_SCAN_STEP = 3e-6
BLOCK_SCAN_RADIUS = 18e-6
BLOCK_THRESHOLD_FRACTION = 0.70
MIN_USEFUL_POWER_FRACTION = 1.0e-4


@dataclass(frozen=True)
class _Point:
    x: float
    y: float


class BallLensProbeAlignmentAlgorithm:
    name = "ball_lens_probe"
    display_name = "Ball-lens probe"

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        initial = device.current_poses()
        if len(initial) < 2:
            reading = device.measure()
            return AlignmentAlgorithmResult(
                name=self.name,
                display_name=self.display_name,
                final_poses=device.current_poses(),
                final_reading=reading,
                move_history=device.move_history(),
                message="Ball-lens probe requires at least two ball lenses.",
            )

        reference = device.coordinate_reference_point()
        safe_poses = self._safe_poses(initial, reference)
        for lens_index, pose in enumerate(safe_poses):
            device.move_lens_to(lens_index, *pose)

        messages: list[str] = [
            "Moved both ball lenses to safe default positions.",
            (
                f"Using coordinate reference x={reference[0] * 1e6:.4g} um, "
                f"y={reference[1] * 1e6:.4g} um, z={reference[2] * 1e6:.4g} um."
            ),
        ]

        final_reading: PowerReading = device.measure()
        for active_lens, probe_lens in ((0, 1), (1, 0)):
            focus_pose, focus_reading = self._find_useful_focus(device, active_lens, safe_poses[active_lens])
            final_reading = focus_reading
            messages.append(
                f"Lens {active_lens + 1} focus scan found {focus_reading.received_power * 1e3:.6g} mW."
            )
            centre = self._find_beam_centre_with_probe(device, probe_lens, focus_pose, focus_reading)
            if centre is None:
                messages.append(f"Lens {probe_lens + 1} probe did not collect enough blocked points.")
                device.move_lens_to(probe_lens, *safe_poses[probe_lens])
                continue
            final_reading = device.move_lens_to(active_lens, centre.x, centre.y, focus_pose[2])
            device.move_lens_to(probe_lens, *safe_poses[probe_lens])
            messages.append(
                f"Probe lens {probe_lens + 1} inferred beam centre x={centre.x * 1e6:.4g} um, y={centre.y * 1e6:.4g} um."
            )

        final_reading = device.measure()
        return AlignmentAlgorithmResult(
            name=self.name,
            display_name=self.display_name,
            final_poses=device.current_poses(),
            final_reading=final_reading,
            move_history=device.move_history(),
            message=" ".join(messages),
        )

    def _safe_poses(self, poses: tuple[LensPose, ...], reference: LensPose) -> tuple[LensPose, ...]:
        safe: list[LensPose] = []
        for _x, y, z in poses:
            safe.append((SAFE_X_OFFSET, reference[1] if math.isfinite(reference[1]) else y, z))
        return tuple(safe)

    def _find_useful_focus(
        self,
        device: AlignmentDevice,
        lens_index: int,
        start_pose: LensPose,
    ) -> tuple[LensPose, PowerReading]:
        best_pose = start_pose
        best_reading = device.move_lens_to(lens_index, *start_pose)
        offsets = [i * FOCUS_SCAN_STEP for i in range(-4, 5)]
        for dx in offsets:
            for dy in offsets:
                reading = device.move_lens_to(lens_index, dx, dy, start_pose[2])
                if reading.received_power > best_reading.received_power:
                    best_reading = reading
                    best_pose = device.current_poses()[lens_index]
        if best_reading.received_power <= 0.0:
            return best_pose, best_reading
        return best_pose, device.move_lens_to(lens_index, *best_pose)

    def _find_beam_centre_with_probe(
        self,
        device: AlignmentDevice,
        probe_lens: int,
        around_pose: LensPose,
        open_reading: PowerReading,
    ) -> _Point | None:
        if open_reading.received_power <= 0.0:
            return None
        threshold = max(
            open_reading.received_power * BLOCK_THRESHOLD_FRACTION,
            open_reading.received_power
            - open_reading.received_power * MIN_USEFUL_POWER_FRACTION,
        )
        blocked: list[_Point] = []
        for angle_index in range(12):
            angle = 2.0 * math.pi * angle_index / 12.0
            for radius_fraction in (0.0, 0.33, 0.67, 1.0):
                radius = BLOCK_SCAN_RADIUS * radius_fraction
                x = around_pose[0] + math.cos(angle) * radius
                y = around_pose[1] + math.sin(angle) * radius
                reading = device.move_lens_to(probe_lens, x, y, around_pose[2])
                if reading.received_power < threshold:
                    blocked.append(_Point(x, y))
                    break
        if len(blocked) < 3:
            return None
        return self._circle_centre(blocked) or _Point(
            sum(point.x for point in blocked) / len(blocked),
            sum(point.y for point in blocked) / len(blocked),
        )

    def _circle_centre(self, points: list[_Point]) -> _Point | None:
        # Least-squares fit for x^2 + y^2 + ax + by + c = 0.
        n = len(points)
        sx = sum(p.x for p in points)
        sy = sum(p.y for p in points)
        sxx = sum(p.x * p.x for p in points)
        syy = sum(p.y * p.y for p in points)
        sxy = sum(p.x * p.y for p in points)
        bx = -sum(p.x * (p.x * p.x + p.y * p.y) for p in points)
        by = -sum(p.y * (p.x * p.x + p.y * p.y) for p in points)
        bc = -sum(p.x * p.x + p.y * p.y for p in points)
        det = (
            sxx * (syy * n - sy * sy)
            - sxy * (sxy * n - sx * sy)
            + sx * (sxy * sy - syy * sx)
        )
        if abs(det) < 1e-30:
            return None
        da = (
            bx * (syy * n - sy * sy)
            - sxy * (by * n - sy * bc)
            + sx * (by * sy - syy * bc)
        )
        db = (
            sxx * (by * n - sy * bc)
            - bx * (sxy * n - sx * sy)
            + sx * (sxy * bc - by * sx)
        )
        return _Point(-0.5 * da / det, -0.5 * db / det)
