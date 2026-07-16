"""ABCD optical elements."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


class OpticalElement:
    """Base class for paraxial optical elements."""

    def abcd(self) -> np.ndarray:
        """Return the 2x2 ABCD ray-transfer matrix for this element."""

        raise NotImplementedError


@dataclass(frozen=True)
class FreeSpace(OpticalElement):
    """Free-space propagation over a fixed axial distance."""

    length: float

    def __post_init__(self) -> None:
        if not np.isfinite(self.length):
            raise ValueError("length must be finite")
        if self.length < 0:
            raise ValueError("length must be non-negative")

    def abcd(self) -> np.ndarray:
        return np.array([[1.0, self.length], [0.0, 1.0]], dtype=float)


@dataclass(frozen=True)
class ThinLens(OpticalElement):
    """Thin lens with focal length f (negative for diverging)."""

    focal_length: float

    def __post_init__(self) -> None:
        if not np.isfinite(self.focal_length):
            raise ValueError("focal_length must be finite")
        if self.focal_length == 0:
            raise ValueError("focal_length must be non-zero")

    def abcd(self) -> np.ndarray:
        return np.array([[1.0, 0.0], [-1.0 / self.focal_length, 1.0]], dtype=float)
