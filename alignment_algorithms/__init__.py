"""Registry for optical alignment algorithms."""

from __future__ import annotations

from .base import (
    AlignmentAlgorithm,
    AlignmentAlgorithmResult,
    AlignmentDevice,
    AlignmentMove,
    LensPose,
    PowerReading,
)
from .ball_lens_probe import BallLensProbeAlignmentAlgorithm
from .manual import ManualAlignmentAlgorithm


_ALGORITHMS: dict[str, AlignmentAlgorithm] = {
    algorithm.name: algorithm
    for algorithm in (
        BallLensProbeAlignmentAlgorithm(),
        ManualAlignmentAlgorithm(),
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
    "AlignmentMove",
    "LensPose",
    "BallLensProbeAlignmentAlgorithm",
    "ManualAlignmentAlgorithm",
    "PowerReading",
    "available_algorithms",
    "get_algorithm",
]
