"""Machine-facing blind power-J best-of-9 statement."""

from __future__ import annotations

from .blind_power_pattern_step import BlindPowerPatternStep


class BlindPowerJBestOf9Step(BlindPowerPatternStep):
    """Best-of-9-labelled blind power search entry point for TestMaster."""

    algorithm_name = "blind_power_j_best_of_9"
