"""Public Python interface for the Migration V6 reviewed-vision workflow."""

from __future__ import annotations

from typing import Any

__all__ = [
    "initialize_v6_memory",
    "run_v6_vision_workflow",
]


def initialize_v6_memory(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .v6_offset_workflow import initialize_v6_memory as _initialize_v6_memory

    return _initialize_v6_memory(*args, **kwargs)


def run_v6_vision_workflow(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .v6_offset_workflow import run_v6_vision_workflow as _run_v6_vision_workflow

    return _run_v6_vision_workflow(*args, **kwargs)
