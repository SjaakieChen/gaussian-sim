"""Machine-facing blind power-J gradient statement."""

from __future__ import annotations

from .blind_power_pattern_step import BlindPowerPatternStep


class BlindPowerJGradientStep(BlindPowerPatternStep):
    """Gradient-labelled blind power search entry point for TestMaster."""

    algorithm_name = "blind_power_j_gradient"
