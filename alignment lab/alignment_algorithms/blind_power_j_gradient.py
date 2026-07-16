"""Gradient-only blind power-J alignment variant."""

from __future__ import annotations

from .blind_power_j import BlindPowerJAlgorithm, DIRECTION_METHOD_GRADIENT


class BlindPowerJGradientAlgorithm(BlindPowerJAlgorithm):
    name = "blind_power_j_gradient"
    display_name = "Blind power J: Gradient"
    direction_methods = (DIRECTION_METHOD_GRADIENT,)
