"""Optical system propagation using q-parameter ABCD matrices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

import numpy as np

from beam import GaussianBeam, q_to_R, q_to_w
from elements import FreeSpace, OpticalElement


def abcd_propagate(q: complex, matrix: np.ndarray) -> complex:
    """Apply an ABCD matrix to q using q_out = (A q + B) / (C q + D)."""

    abcd = np.asarray(matrix, dtype=float)
    if abcd.shape != (2, 2):
        raise ValueError("ABCD matrix must have shape (2, 2)")

    a, b = abcd[0, 0], abcd[0, 1]
    c, d = abcd[1, 0], abcd[1, 1]
    return (a * q + b) / (c * q + d)


@dataclass(frozen=True)
class OpticalSystem:
    """Sequence of optical elements for q-parameter propagation."""

    elements: Sequence[OpticalElement]

    def propagate_q(self, q_initial: complex) -> list[complex]:
        """Return q before and after each element in order."""

        q_values = [q_initial]
        q_current = q_initial

        for element in self.elements:
            q_current = abcd_propagate(q_current, element.abcd())
            q_values.append(q_current)

        return q_values


@dataclass(frozen=True)
class SystemSample:
    """Sampled beam properties along the global optical axis."""

    z: np.ndarray
    w: np.ndarray
    R: np.ndarray
    q: np.ndarray
    element_positions: list[tuple[float, OpticalElement]]

    def __iter__(self) -> Iterator[np.ndarray]:
        yield self.z
        yield self.w
        yield self.R
        yield self.q


def sample_system(
    beam: GaussianBeam,
    elements: Iterable[OpticalElement],
    z_samples_per_space: int = 200,
    start_z: float = 0.0,
) -> SystemSample:
    """Sample beam properties through an optical system.

    Global z starts at the input plane, z=start_z. Free-space sections are sampled
    continuously. Thin or other zero-length elements are sampled as
    discontinuities by adding a second point at the same z after the element.
    """

    samples_per_space = int(z_samples_per_space)
    if samples_per_space < 2:
        raise ValueError("z_samples_per_space must be at least 2")
    if not np.isfinite(start_z):
        raise ValueError("start_z must be finite")

    q_current = beam.q(start_z)
    z_current = start_z

    z_values: list[float] = [z_current]
    q_values: list[complex] = [q_current]
    element_positions: list[tuple[float, OpticalElement]] = []

    for element in elements:
        if isinstance(element, FreeSpace):
            if element.length == 0:
                continue

            distances = np.linspace(0.0, element.length, samples_per_space)
            for distance in distances[1:]:
                z_values.append(z_current + float(distance))
                q_values.append(q_current + complex(distance))

            q_current = q_current + element.length
            z_current += element.length
            continue

        element_positions.append((z_current, element))
        q_current = abcd_propagate(q_current, element.abcd())
        z_values.append(z_current)
        q_values.append(q_current)

    z_array = np.asarray(z_values, dtype=float)
    q_array = np.asarray(q_values, dtype=complex)
    return SystemSample(
        z=z_array,
        w=np.asarray(q_to_w(q_array, beam.wavelength), dtype=float),
        R=np.asarray(q_to_R(q_array), dtype=float),
        q=q_array,
        element_positions=element_positions,
    )
