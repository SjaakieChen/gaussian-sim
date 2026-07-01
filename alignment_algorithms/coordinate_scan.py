"""Reserved for future coordinate-scan alignment algorithms.

The alignment lab now uses a step-based device API. The first real optimizer
will live here once the move/measure foundation is stable.
"""

from __future__ import annotations

from .base import AlignmentAlgorithmResult, AlignmentDevice


class CoordinateScanAlgorithm:
    name = "coordinate_scan"
    display_name = "Coordinate scan"

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        raise NotImplementedError("coordinate scan has not been adapted to the step-based API yet")
