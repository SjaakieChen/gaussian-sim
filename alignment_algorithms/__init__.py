"""Registry for optical alignment algorithms."""

from __future__ import annotations

from .base import (
    AlignmentAlgorithm,
    AlignmentAlgorithmResult,
    AlignmentDevice,
    AlignmentModelGeometry,
    AlignmentMove,
    BallLensGeometry,
    DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
    DEFAULT_TARGET_MODE_EFFICIENCY,
    LensPose,
    PowerReading,
    SourceGeometry,
    TaperGeometry,
)
from .coordinate_scan import CoordinateScanAlgorithm
from .given_positions import GivenPositionsAlgorithm
from .manual import ManualAlignmentAlgorithm
from .position_solve import (
    BeamErrorJMatrixAlgorithm,
    FixedZJMatrixAlgorithm,
    PositionSolveAlgorithm,
    PositionSolveWithJStepsAlgorithm,
)
from .yase import YaseAlignmentAlgorithm, discover_yase_algorithms


_ALGORITHMS: dict[str, AlignmentAlgorithm] = {
    algorithm.name: algorithm
    for algorithm in (
        CoordinateScanAlgorithm(),
        GivenPositionsAlgorithm(),
        ManualAlignmentAlgorithm(),
        BeamErrorJMatrixAlgorithm(),
        FixedZJMatrixAlgorithm(),
        PositionSolveAlgorithm(),
        PositionSolveWithJStepsAlgorithm(),
    )
}
_ALGORITHMS.update(discover_yase_algorithms())


def available_algorithms() -> dict[str, AlignmentAlgorithm]:
    return dict(_ALGORITHMS)


def get_algorithm(name: str) -> AlignmentAlgorithm:
    try:
        return _ALGORITHMS[name]
    except KeyError as exc:
        choices = ", ".join(sorted(_ALGORITHMS))
        raise ValueError(f"unknown alignment algorithm {name!r}; choose one of: {choices}") from exc


__all__ = [
    "AlignmentAlgorithm",
    "AlignmentAlgorithmResult",
    "AlignmentDevice",
    "AlignmentModelGeometry",
    "AlignmentMove",
    "BallLensGeometry",
    "BeamErrorJMatrixAlgorithm",
    "CoordinateScanAlgorithm",
    "DEFAULT_MAX_ALIGNMENT_ATTEMPTS",
    "DEFAULT_TARGET_MODE_EFFICIENCY",
    "FixedZJMatrixAlgorithm",
    "GivenPositionsAlgorithm",
    "LensPose",
    "ManualAlignmentAlgorithm",
    "PositionSolveAlgorithm",
    "PositionSolveWithJStepsAlgorithm",
    "PowerReading",
    "SourceGeometry",
    "TaperGeometry",
    "YaseAlignmentAlgorithm",
    "available_algorithms",
    "get_algorithm",
]
