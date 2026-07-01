"""Walk-the-beam alignment: centroid capture then power hill-climb."""

from __future__ import annotations

from typing import Protocol

from .base import AlignmentAlgorithmResult, AlignmentDevice, PowerReading

AXIAL_STEP = 0.5e-6
TRANSVERSE_STEP = 0.25e-6
MIN_STEP = 0.05e-6
MAX_MOVES = 200
JOINT_PASSES = 2


class WalkBeamDevice(AlignmentDevice, Protocol):
    def beam_centroid_at_ball_entry(self, lens_index: int) -> tuple[float, float]:
        """Return predicted beam centroid (x, y) at the entry plane of ball lens_index."""


class WalkBeamAlgorithm:
    name = "walk_beam"
    display_name = "Walk the beam"

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        if not hasattr(device, "beam_centroid_at_ball_entry"):
            raise TypeError("walk_beam requires a device with beam_centroid_at_ball_entry()")

        walk_device: WalkBeamDevice = device  # type: ignore[assignment]
        initial = device.measure()
        lens_count = len(device.current_poses())

        self._centroid_walk(walk_device, lens_count)
        self._power_optimization(device, lens_count)

        final = device.measure()
        initial_mw = initial.received_power * 1e3
        final_mw = final.received_power * 1e3
        message = (
            f"Walk-the-beam: {initial_mw:.6g} -> {final_mw:.6g} mW "
            f"({device.move_history().__len__()} moves, {final.measurement_count} reads)."
        )
        return AlignmentAlgorithmResult(
            name=self.name,
            display_name=self.display_name,
            final_poses=device.current_poses(),
            final_reading=final,
            move_history=device.move_history(),
            message=message,
        )

    def _centroid_walk(self, device: WalkBeamDevice, lens_count: int) -> None:
        poses = device.current_poses()
        ordered_indices = sorted(range(lens_count), key=lambda index: poses[index][2])
        for lens_index in ordered_indices:
            beam_x, beam_y = device.beam_centroid_at_ball_entry(lens_index)
            ball_x, ball_y, _position = device.current_poses()[lens_index]
            dx = beam_x - ball_x
            dy = beam_y - ball_y
            if abs(dx) > 1e-18 or abs(dy) > 1e-18:
                device.move_lens(lens_index, dx=dx, dy=dy)

    def _power_optimization(self, device: AlignmentDevice, lens_count: int) -> None:
        for _pass in range(JOINT_PASSES):
            improved = False
            ordered_indices = sorted(
                range(lens_count),
                key=lambda index: device.current_poses()[index][2],
            )
            for lens_index in ordered_indices:
                if self._coordinate_pass(device, lens_index, "dz", AXIAL_STEP):
                    improved = True
            for lens_index in ordered_indices:
                if self._coordinate_pass(device, lens_index, "dx", TRANSVERSE_STEP):
                    improved = True
                if self._coordinate_pass(device, lens_index, "dy", TRANSVERSE_STEP):
                    improved = True
            if not improved:
                break

    def _coordinate_pass(
        self,
        device: AlignmentDevice,
        lens_index: int,
        axis: str,
        step: float,
    ) -> bool:
        improved = False
        current_step = step
        while current_step >= MIN_STEP:
            if len(device.move_history()) >= MAX_MOVES:
                return improved
            baseline = device.measure()
            best_delta = 0.0
            best_reading: PowerReading | None = None

            for sign in (1.0, -1.0):
                if len(device.move_history()) >= MAX_MOVES:
                    break
                delta = sign * current_step
                reading = self._try_lens_move(device, lens_index, axis, delta)
                if reading.received_power > baseline.received_power + 1e-18:
                    if best_reading is None or reading.received_power > best_reading.received_power:
                        best_delta = delta
                        best_reading = reading
                self._try_lens_move(device, lens_index, axis, -delta)

            if best_reading is None:
                current_step *= 0.5
                continue

            self._try_lens_move(device, lens_index, axis, best_delta)
            improved = True
            current_step *= 0.5

        return improved

    def _try_lens_move(
        self,
        device: AlignmentDevice,
        lens_index: int,
        axis: str,
        delta: float,
    ) -> PowerReading:
        if axis == "dx":
            return device.move_lens(lens_index, dx=delta)
        if axis == "dy":
            return device.move_lens(lens_index, dy=delta)
        if axis == "dz":
            return device.move_lens(lens_index, dz=delta)
        raise ValueError(f"unsupported axis {axis!r}")
