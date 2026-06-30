"""Tests for GaussianBeam formulas and q-parameter conversions."""

import numpy as np

from beam import GaussianBeam, q_to_R, q_to_w


def test_gaussian_beam_at_waist():
    beam = GaussianBeam(wavelength=1064e-9, w0=1.0e-3, z0=0.25)

    assert np.isclose(beam.w(beam.z0), beam.w0)
    assert np.isinf(beam.R(beam.z0))
    assert np.isinf(q_to_R(beam.q(beam.z0)))
    assert np.isclose(q_to_w(beam.q(beam.z0), beam.wavelength), beam.w0)


def test_gaussian_beam_at_rayleigh_range():
    beam = GaussianBeam(wavelength=1064e-9, w0=1.0e-3, z0=-0.10)
    z_rayleigh = beam.rayleigh_range()

    assert np.isclose(beam.w(beam.z0 + z_rayleigh), np.sqrt(2.0) * beam.w0)


def test_intensity_radius_is_one_over_e_squared_at_w():
    beam = GaussianBeam(wavelength=1064e-9, w0=1.0e-3)

    assert np.isclose(
        beam.intensity_radius_profile(beam.w0, 0.0),
        np.exp(-2.0),
    )
