"""Move lenses to the known aligned reference positions."""

from __future__ import annotations

import math

from .base import AlignmentAlgorithmResult, AlignmentDevice, LensPose, PowerReading


POSITION_ABSOLUTE_TOLERANCE = 1.0e-15


def move_to_starting_poses(
    device: AlignmentDevice,
    starting_poses: tuple[LensPose, ...] | None = None,
) -> PowerReading:
    """Move every lens to the known aligned/reference pose and return the last reading."""

    target_poses = device.starting_poses() if starting_poses is None else starting_poses
    current_poses = device.current_poses()
    if len(target_poses) != len(current_poses):
        raise ValueError("starting pose count does not match the current lens count")

    reading: PowerReading | None = None
    for lens_index, target_pose in enumerate(target_poses):
        current_pose = device.current_poses()[lens_index]
        dx = target_pose[0] - current_pose[0]
        dy = target_pose[1] - current_pose[1]
        dz = target_pose[2] - current_pose[2]
        if (
            math.isclose(dx, 0.0, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE)
            and math.isclose(dy, 0.0, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE)
            and math.isclose(dz, 0.0, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE)
        ):
            continue
        reading = device.move_lens(lens_index, dx=dx, dy=dy, dz=dz)
    return reading if reading is not None else device.measure()


class GivenPositionsAlgorithm:
    name = "given_positions"
    display_name = "Reference pose only"

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        initial_reading = device.measure()
        final_reading = move_to_starting_poses(device)
        message = (
            "Moved lenses to the known starting aligned poses; "
            f"initial power {initial_reading.received_power * 1e3:.6g} mW."
        )
        return AlignmentAlgorithmResult(
            name=self.name,
            display_name=self.display_name,
            final_poses=device.current_poses(),
            final_reading=final_reading,
            move_history=device.move_history(),
            message=message,
        )
