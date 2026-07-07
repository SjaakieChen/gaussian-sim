"""Newton-only blind power-J alignment variant."""

from __future__ import annotations

from .blind_power_j import BlindPowerJAlgorithm, DIRECTION_METHOD_NEWTON


class BlindPowerJNewtonAlgorithm(BlindPowerJAlgorithm):
    name = "blind_power_j_newton"
    display_name = "Blind power J: Newton"
    direction_methods = (DIRECTION_METHOD_NEWTON,)
