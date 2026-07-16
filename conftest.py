"""Test path setup for the relocated alignment lab source tree."""

from __future__ import annotations

import sys
from pathlib import Path


ALIGNMENT_LAB_ROOT = Path(__file__).resolve().parent / "alignment lab"
if ALIGNMENT_LAB_ROOT.exists():
    sys.path.insert(0, str(ALIGNMENT_LAB_ROOT))
