"""Compatibility module matching the original README example names."""

from __future__ import annotations

try:
    from testmaster_alignment.blind.blind_power_j_step import BlindPowerJStep
except ImportError:
    from blind.blind_power_j_step import BlindPowerJStep  # type: ignore[no-redef]


class BlindAlignStep(BlindPowerJStep):
    """Default blind alignment class used by the README examples."""

    pass
