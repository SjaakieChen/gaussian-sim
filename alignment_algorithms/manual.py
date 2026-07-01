"""Manual alignment placeholder algorithm."""

from __future__ import annotations

from .base import AlignmentAlgorithmResult, AlignmentDevice


class ManualAlignmentAlgorithm:
    name = "manual"
    display_name = "Manual/no search"

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        reading = device.measure()
        return AlignmentAlgorithmResult(
            name=self.name,
            display_name=self.display_name,
            final_poses=device.current_poses(),
            final_reading=reading,
            move_history=device.move_history(),
            message="Manual mode measured the current layout without moving lenses.",
        )
