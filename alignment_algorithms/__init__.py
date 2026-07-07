"""Registry for optical alignment algorithms."""

from __future__ import annotations

from .base import (
    AlignmentAlgorithm,
    AlignmentAlgorithmResult,
    AlignmentDevice,
    AlignmentModelGeometry,
    AlignmentMove,
    BallLensGeometry,
    BallLensNoGoZone,
    DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
    DEFAULT_TARGET_MODE_EFFICIENCY,
    LensPose,
    PowerReading,
    SourceGeometry,
    TaperGeometry,
)
from .blind_power_j import BlindPowerJAlgorithm
from .blind_power_j_best_of_9 import BlindPowerJBestOf9Algorithm
from .blind_power_j_gradient import BlindPowerJGradientAlgorithm
from .blind_power_j_newton import BlindPowerJNewtonAlgorithm
from .position_solve import (
    FixedZJMatrixAlgorithm,
    PositionSolveAlgorithm,
    PositionSolveWithJStepsAlgorithm,
)
from .yase import YaseAlignmentAlgorithm


_ALGORITHMS: dict[str, AlignmentAlgorithm] = {
    algorithm.name: algorithm
    for algorithm in (
        BlindPowerJAlgorithm(),
        BlindPowerJNewtonAlgorithm(),
        BlindPowerJGradientAlgorithm(),
        BlindPowerJBestOf9Algorithm(),
        FixedZJMatrixAlgorithm(),
        PositionSolveAlgorithm(),
        PositionSolveWithJStepsAlgorithm(),
    )
}


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
    "BallLensNoGoZone",
    "BlindPowerJAlgorithm",
    "BlindPowerJBestOf9Algorithm",
    "BlindPowerJGradientAlgorithm",
    "BlindPowerJNewtonAlgorithm",
    "DEFAULT_MAX_ALIGNMENT_ATTEMPTS",
    "DEFAULT_TARGET_MODE_EFFICIENCY",
    "FixedZJMatrixAlgorithm",
    "LensPose",
    "PositionSolveAlgorithm",
    "PositionSolveWithJStepsAlgorithm",
    "PowerReading",
    "SourceGeometry",
    "TaperGeometry",
    "YaseAlignmentAlgorithm",
    "available_algorithms",
    "get_algorithm",
]
