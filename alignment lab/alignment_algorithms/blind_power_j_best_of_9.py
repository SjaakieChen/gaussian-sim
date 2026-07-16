"""Best-of-9 blind power-J alignment variant."""

from __future__ import annotations

from .blind_power_j import BlindPowerJAlgorithm, DIRECTION_METHOD_BEST_OF_9


class BlindPowerJBestOf9Algorithm(BlindPowerJAlgorithm):
    name = "blind_power_j_best_of_9"
    display_name = "Blind power J: Best-of-9"
    direction_methods = (DIRECTION_METHOD_BEST_OF_9,)
