"""Test path setup for the relocated lab source trees."""

from __future__ import annotations

import sys
from pathlib import Path


ALIGNMENT_LAB_ROOT = Path(__file__).resolve().parent / "alignment lab"
if ALIGNMENT_LAB_ROOT.exists():
    sys.path.insert(0, str(ALIGNMENT_LAB_ROOT))

VISION_RECOGNITION_LAB_ROOT = Path(__file__).resolve().parent / "vision recognition lab"
if VISION_RECOGNITION_LAB_ROOT.exists():
    sys.path.insert(0, str(VISION_RECOGNITION_LAB_ROOT))
