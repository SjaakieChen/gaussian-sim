"""Tests for walk-the-beam alignment and headless session."""

from __future__ import annotations

import subprocess
import sys

import numpy as np
import pytest

from alignment_algorithms import available_algorithms, get_algorithm
from alignment_algorithms.walk_beam import WalkBeamAlgorithm
from alignment_session import (
    HeadlessAlignmentDevice,
    apply_full_scramble,
    apply_lab_scramble,
    apply_laser_taper_scramble,
    default_alignment_layout,
    evaluate_alignment_layout,
    run_alignment_session,
)
from interactive_setup import propagate_astigmatic_through_balls


def _ball_mismatches(layout) -> list[float]:
    _state, reports, _missed, _samples = propagate_astigmatic_through_balls(
        layout.source,
        layout.balls,
        layout.tapers[0].position,
    )
    return [report.radial_mismatch for report in reports]


def test_laser_taper_scramble_keeps_balls_at_nominal():
    layout = default_alignment_layout()
    nominal_poses = layout.nominal_ball_poses
    apply_laser_taper_scramble(layout, seed=42)

    assert layout.current_poses() == nominal_poses
    assert layout.source.x_offset > 0.0 or layout.source.y_offset > 0.0
    assert layout.tapers[0].x_offset != 0.0 or layout.tapers[0].y_offset != 0.0


def test_lab_laser_scramble_keeps_balls_at_nominal():
    layout = default_alignment_layout()
    nominal_poses = layout.nominal_ball_poses
    apply_lab_scramble(layout, seed=42, scramble_balls=False)

    assert layout.current_poses() == nominal_poses


def test_walk_beam_improves_power_from_laser_taper_scramble():
    layout = default_alignment_layout()
    apply_laser_taper_scramble(layout, seed=42)
    initial = evaluate_alignment_layout(layout).received_power

    _algorithm, result, _device = run_alignment_session("walk_beam", layout)

    assert result.received_power >= initial


def test_alignment_algorithm_registry_includes_walk_beam():
    algorithms = available_algorithms()

    assert set(algorithms) == {"manual", "walk_beam"}
    assert get_algorithm("walk_beam").display_name == "Walk the beam"


def test_headless_device_measure_and_move_counts():
    layout = default_alignment_layout()
    device = HeadlessAlignmentDevice(layout)
    before = device.current_poses()

    first = device.measure()
    second = device.measure()
    reading = device.move_lens(0, dx=1.0e-6, dy=-0.5e-6, dz=0.25e-6)

    after = device.current_poses()
    assert first.move_count == 0
    assert first.measurement_count == 1
    assert second.measurement_count == 2
    assert reading.move_count == 1
    assert np.allclose(after[0], (before[0][0] + 1.0e-6, before[0][1] - 0.5e-6, before[0][2] + 0.25e-6))
    assert len(device.move_history()) == 1


def test_full_scramble_centroid_walk_reduces_ball_mismatch():
    layout = default_alignment_layout()
    apply_full_scramble(layout, seed=42)
    before = _ball_mismatches(layout)

    device = HeadlessAlignmentDevice(layout)
    WalkBeamAlgorithm()._centroid_walk(device, len(layout.balls))  # pylint: disable=protected-access
    after = _ball_mismatches(layout)

    assert len(before) == 2
    assert all(after_value < before_value for before_value, after_value in zip(before, after))
    assert all(after_value < 1e-9 for after_value in after)


def test_walk_beam_improves_power_from_lab_scramble():
    layout = default_alignment_layout()
    apply_lab_scramble(layout, seed=42)
    initial = evaluate_alignment_layout(layout).received_power

    _algorithm, result, _device = run_alignment_session("walk_beam", layout)
    final = result.received_power

    assert final > initial
    assert final > 1e-3


def test_walk_beam_improves_or_maintains_power_from_full_scramble():
    layout = default_alignment_layout()
    apply_full_scramble(layout, seed=7)
    initial = evaluate_alignment_layout(layout).received_power

    _algorithm, result, _device = run_alignment_session("walk_beam", layout)

    assert result.received_power >= initial
    assert result.move_count > 0


def test_cli_runs_without_tk():
    completed = subprocess.run(
        [sys.executable, "run_alignment.py", "--algorithm", "walk_beam", "--seed", "42"],
        cwd="/workspace",
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Final power:" in completed.stdout
    assert "Scramble: laser" in completed.stdout
