"""Machine-facing blind power-J auto-fallback statement."""

from __future__ import annotations

from .blind_power_pattern_step import BlindPowerPatternStep


class BlindPowerJStep(BlindPowerPatternStep):
    """Default blind power search using positive/negative probes per stage."""

    algorithm_name = "blind_power_j"
