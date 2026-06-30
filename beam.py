"""Paraxial TEM00 Gaussian beam formulas."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike


def _maybe_scalar(value: np.ndarray, original: object):
    if np.ndim(original) == 0 and np.ndim(value) == 0:
        return value.item()
    return value


def real_part(z: ArrayLike) -> np.ndarray:
    """Return the real part of complex values without using ``.real``."""

    z_array = np.asarray(z, dtype=complex)
    result = (z_array + np.conj(z_array)) * 0.5
    return np.real_if_close(result).astype(float)


def imag_part(z: ArrayLike) -> np.ndarray:
    """Return the imaginary part of complex values without using ``.imag``."""

    z_array = np.asarray(z, dtype=complex)
    result = (z_array - np.conj(z_array)) / (2.0j)
    return np.real_if_close(result).astype(float)


def q_to_w(q: ArrayLike, wavelength: float):
    """Return beam radius w from the complex beam parameter q.

    The convention used throughout the package is

        1 / q = 1 / R - i * wavelength / (pi * w**2)

    so a physical beam has -imag(1 / q) > 0.
    """

    if wavelength <= 0:
        raise ValueError("wavelength must be positive")

    q_array = np.asarray(q, dtype=complex)
    inv_q = 1.0 / q_array
    neg_imag_inv = 0.0 - imag_part(inv_q)

    if np.any(neg_imag_inv <= 0):
        raise ValueError("q is not physical: expected -imag(1 / q) > 0")

    w = np.sqrt(wavelength / (np.pi * neg_imag_inv))
    return _maybe_scalar(w, q)


def q_to_R(q: ArrayLike, atol: float = 1e-12):
    """Return wavefront radius of curvature R from q."""

    q_array = np.asarray(q, dtype=complex)
    inv_q = 1.0 / q_array
    inv_real = real_part(inv_q)
    with np.errstate(divide="ignore", invalid="ignore"):
        R = np.where(np.abs(inv_real) < atol, np.inf, 1.0 / inv_real)

    return _maybe_scalar(R, q)


def waist_from_q(q: complex, wavelength: float) -> tuple[float, float, float]:
    """Return waist distance, waist radius, and Rayleigh range from q.

    The returned distance is measured from the plane where ``q`` is defined.
    Under free-space propagation q(d) = q + d, so the waist occurs at
    the negative of the real part of q. The Rayleigh range at that waist is imag(q).
    """

    if wavelength <= 0:
        raise ValueError("wavelength must be positive")

    q_value = np.complex128(q)
    z_rayleigh = float(imag_part(q_value))
    if z_rayleigh <= 0:
        raise ValueError("q is not physical: expected imag(q) > 0")

    q_real = float(real_part(q_value))
    distance_to_waist = 0.0 - q_real
    waist_radius = math.sqrt(wavelength * z_rayleigh / math.pi)
    return distance_to_waist, waist_radius, z_rayleigh


@dataclass(frozen=True)
class GaussianBeam:
    """Ideal paraxial TEM00 Gaussian beam.

    Parameters are SI units. ``w0`` is the 1/e^2 intensity radius at the waist,
    and ``z0`` is the waist position along the global optical axis.
    """

    wavelength: float
    w0: float
    z0: float = 0.0

    def __post_init__(self) -> None:
        if self.wavelength <= 0:
            raise ValueError("wavelength must be positive")
        if self.w0 <= 0:
            raise ValueError("w0 must be positive")

    def rayleigh_range(self) -> float:
        """Return the Rayleigh range z_R at the beam waist."""

        return np.pi * self.w0**2 / self.wavelength

    def q(self, z: ArrayLike):
        """Return the complex beam parameter q at global position z."""

        z_array = np.asarray(z, dtype=float)
        q_value = (z_array - self.z0) + 1j * self.rayleigh_range()
        return _maybe_scalar(q_value, z)

    def w(self, z: ArrayLike):
        """Return the 1/e^2 beam radius at global position z."""

        z_array = np.asarray(z, dtype=float)
        z_rayleigh = self.rayleigh_range()
        radius = self.w0 * np.sqrt(1.0 + ((z_array - self.z0) / z_rayleigh) ** 2)
        return _maybe_scalar(radius, z)

    def R(self, z: ArrayLike):
        """Return the wavefront radius of curvature at global position z."""

        z_array = np.asarray(z, dtype=float)
        relative_z = z_array - self.z0
        z_rayleigh = self.rayleigh_range()

        with np.errstate(divide="ignore", invalid="ignore"):
            radius = relative_z * (1.0 + (z_rayleigh / relative_z) ** 2)

        radius = np.where(relative_z == 0.0, np.inf, radius)
        return _maybe_scalar(radius, z)

    def gouy_phase(self, z: ArrayLike):
        """Return the Gouy phase at global position z."""

        z_array = np.asarray(z, dtype=float)
        phase = np.arctan((z_array - self.z0) / self.rayleigh_range())
        return _maybe_scalar(phase, z)

    def intensity_radius_profile(self, r: ArrayLike, z: ArrayLike):
        """Return relative intensity I(r, z), normalized to waist on-axis I=1."""

        r_array = np.asarray(r, dtype=float)
        w_z = self.w(z)
        intensity = (self.w0 / w_z) ** 2 * np.exp(-2.0 * (r_array / w_z) ** 2)
        return _maybe_scalar(np.asarray(intensity), r)

    def field_amplitude(self, r: ArrayLike, z: ArrayLike):
        """Return the scalar complex field amplitude, up to a global constant."""

        r_array = np.asarray(r, dtype=float)
        z_array = np.asarray(z, dtype=float)
        relative_z = z_array - self.z0
        k = 2.0 * np.pi / self.wavelength
        w_z = self.w(z)
        R_z = self.R(z)
        gouy = self.gouy_phase(z)

        with np.errstate(divide="ignore", invalid="ignore"):
            curvature_phase = np.where(np.isfinite(R_z), k * r_array**2 / (2.0 * R_z), 0.0)

        amplitude = (self.w0 / w_z) * np.exp(-(r_array / w_z) ** 2)
        phase = k * relative_z + curvature_phase - gouy
        field = amplitude * np.exp(-1j * phase)
        return _maybe_scalar(np.asarray(field), r)
