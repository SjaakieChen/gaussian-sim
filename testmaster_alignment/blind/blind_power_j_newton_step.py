"""Machine-facing blind power-J Newton statement."""

from __future__ import annotations

from .blind_power_pattern_step import BlindPowerPatternStep


class BlindPowerJNewtonStep(BlindPowerPatternStep):
    """Newton-labelled blind power search entry point for TestMaster."""

    algorithm_name = "blind_power_j_newton"
