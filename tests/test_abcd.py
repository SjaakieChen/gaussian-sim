"""Tests for ABCD propagation and optical systems."""

import numpy as np

from beam import GaussianBeam, q_to_R, q_to_w
from elements import FreeSpace, ThinLens
from system import OpticalSystem, abcd_propagate


def test_free_space_abcd_propagation():
    q_in = 0.10 + 2.0j
    distance = 0.30

    q_out = abcd_propagate(q_in, FreeSpace(distance).abcd())

    assert np.isclose(q_out, q_in + distance)


def test_thin_lens_abcd_propagation():
    q_in = 0.10 + 2.0j
    focal_length = 0.25

    q_out = abcd_propagate(q_in, ThinLens(focal_length).abcd())

    assert np.isclose(q_out, q_in / (1.0 - q_in / focal_length))


def test_optical_system_records_q_after_each_element():
    q_in = 1.0j
    elements = [FreeSpace(0.2), ThinLens(0.5), FreeSpace(0.1)]
    system = OpticalSystem(elements)

    q_values = system.propagate_q(q_in)

    assert len(q_values) == 4
    assert np.isclose(q_values[1], q_in + 0.2)


def test_thin_lens_keeps_beam_radius_and_changes_curvature():
    beam = GaussianBeam(wavelength=1064e-9, w0=1.0e-3)
    lens_position = 0.20
    focal_length = 0.10

    q_before = beam.q(lens_position)
    q_after = abcd_propagate(q_before, ThinLens(focal_length).abcd())

    assert np.isclose(q_to_w(q_before, beam.wavelength), q_to_w(q_after, beam.wavelength))
    assert not np.isclose(q_to_R(q_before), q_to_R(q_after))
