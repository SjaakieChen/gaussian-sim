"""Read-only v5 vision geometry helpers."""

from __future__ import annotations

from typing import Any

__all__ = [
    "build_macro_payload_from_sequence_memory",
    "build_standard_capture_evidence",
    "build_sequence_geometry_payload_from_sequence_memory",
    "initialize_sequence_memory",
    "next_sequence_action_from_sequence_memory",
    "next_motion_or_capture_step",
    "plan_biased_close_positions",
    "record_sequence_capture",
    "review_and_record_next_capture",
    "simulate_macro_alignment",
    "solve_common_geometry",
    "solve_macro_alignment_from_sequence_memory",
    "solve_sequence_geometry",
    "solve_sequence_geometry_from_sequence_memory",
]


def plan_biased_close_positions(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .position_bias_planner import plan_biased_close_positions as _plan_biased_close_positions

    return _plan_biased_close_positions(*args, **kwargs)


def solve_common_geometry(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .vision_geometry_solver import solve_common_geometry as _solve_common_geometry

    return _solve_common_geometry(*args, **kwargs)


def simulate_macro_alignment(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .macro_alignment_simulator import simulate_macro_alignment as _simulate_macro_alignment

    return _simulate_macro_alignment(*args, **kwargs)


def initialize_sequence_memory(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_memory_workflow import initialize_sequence_memory as _initialize_sequence_memory

    return _initialize_sequence_memory(*args, **kwargs)


def record_sequence_capture(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_memory_workflow import record_sequence_capture as _record_sequence_capture

    return _record_sequence_capture(*args, **kwargs)


def review_and_record_next_capture(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_memory_workflow import review_and_record_next_capture as _review_and_record_next_capture

    return _review_and_record_next_capture(*args, **kwargs)


def next_sequence_action_from_sequence_memory(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_memory_workflow import next_sequence_action_from_sequence_memory as _next_sequence_action

    return _next_sequence_action(*args, **kwargs)


def next_motion_or_capture_step(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_memory_workflow import next_motion_or_capture_step as _next_motion_or_capture_step

    return _next_motion_or_capture_step(*args, **kwargs)


def build_sequence_geometry_payload_from_sequence_memory(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_memory_workflow import (
        build_sequence_geometry_payload_from_sequence_memory as _build_sequence_geometry_payload_from_sequence_memory,
    )

    return _build_sequence_geometry_payload_from_sequence_memory(*args, **kwargs)


def build_macro_payload_from_sequence_memory(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_memory_workflow import build_macro_payload_from_sequence_memory as _build_macro_payload_from_sequence_memory

    return _build_macro_payload_from_sequence_memory(*args, **kwargs)


def build_standard_capture_evidence(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .standard_capture_evidence import build_standard_capture_evidence as _build_standard_capture_evidence

    return _build_standard_capture_evidence(*args, **kwargs)


def solve_sequence_geometry_from_sequence_memory(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_memory_workflow import solve_sequence_geometry_from_sequence_memory as _solve_sequence_geometry_from_sequence_memory

    return _solve_sequence_geometry_from_sequence_memory(*args, **kwargs)


def solve_macro_alignment_from_sequence_memory(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_memory_workflow import solve_macro_alignment_from_sequence_memory as _solve_macro_alignment_from_sequence_memory

    return _solve_macro_alignment_from_sequence_memory(*args, **kwargs)


def solve_sequence_geometry(*args: Any, **kwargs: Any) -> dict[str, Any]:
    from .sequence_geometry_memory import solve_sequence_geometry as _solve_sequence_geometry

    return _solve_sequence_geometry(*args, **kwargs)
