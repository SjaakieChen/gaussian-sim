"""Noiseless model-based two-ball-lens position solver."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from interactive_setup import BallLensElement, LaserSource, TaperDetectorElement, simulate_source_to_taper

from .base import (
    DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
    DEFAULT_TARGET_MODE_EFFICIENCY,
    AlignmentAlgorithmResult,
    AlignmentDevice,
    AlignmentModelGeometry,
    BallLensGeometry,
    LensPose,
    PowerReading,
    SourceGeometry,
    TaperGeometry,
)
from .given_positions import move_to_starting_poses


AXIAL_SEARCH_WINDOW = 100e-6
POSITION_SOLVE_SEARCH_WINDOWS = (100e-6, 200e-6, 300e-6)
AXIAL_SEARCH_STEPS = (25e-6, 5e-6, 1e-6, 0.25e-6)
AXIAL_REFINEMENT_SPAN_STEPS = 5
TRANSVERSE_RESPONSE_STEP = 1e-6
SINGULAR_CONDITION_LIMIT = 1.0e12
POSITION_ABSOLUTE_TOLERANCE = 1.0e-15


@dataclass(frozen=True)
class PositionSolveCandidate:
    poses: tuple[LensPose, ...]
    reading: PowerReading


@dataclass(frozen=True)
class PositionSolveAlignmentStatus:
    candidate: PositionSolveCandidate | None
    final_reading: PowerReading
    attempts: int
    success: bool


def axial_surface_gaps(
    poses: tuple[LensPose, ...],
    balls: tuple[BallLensGeometry, ...],
    source_z: float,
    taper_z: float,
) -> tuple[float, ...]:
    """Return source-ball, ball-ball, and ball-taper axial air gaps."""

    if len(poses) != len(balls):
        raise ValueError("pose count must match ball count")
    if not balls:
        return (taper_z - source_z,)

    gaps = [poses[0][2] - balls[0].radius - source_z]
    for previous_pose, previous_ball, next_pose, next_ball in zip(
        poses,
        balls,
        poses[1:],
        balls[1:],
    ):
        previous_exit_z = previous_pose[2] + previous_ball.radius
        next_entry_z = next_pose[2] - next_ball.radius
        gaps.append(next_entry_z - previous_exit_z)
    gaps.append(taper_z - (poses[-1][2] + balls[-1].radius))
    return tuple(float(gap) for gap in gaps)


def has_strict_axial_clearance(
    poses: tuple[LensPose, ...],
    balls: tuple[BallLensGeometry, ...],
    source_z: float,
    taper_z: float,
) -> bool:
    """Return True only when every surface-to-surface axial gap is positive."""

    try:
        gaps = axial_surface_gaps(poses, balls, source_z, taper_z)
    except ValueError:
        return False
    return all(math.isfinite(gap) and gap > 0.0 for gap in gaps)


def solve_position_candidate(
    geometry: AlignmentModelGeometry,
    axial_search_window: float = AXIAL_SEARCH_WINDOW,
) -> PositionSolveCandidate | None:
    if len(geometry.balls) != 2:
        raise ValueError("position solve currently supports exactly two ball lenses")
    solver = _PositionSolver(axial_search_window=axial_search_window)
    return solver.find_best_candidate(geometry)


def solve_position_target_poses(geometry: AlignmentModelGeometry) -> tuple[LensPose, ...] | None:
    candidate = solve_position_candidate(geometry)
    return None if candidate is None else candidate.poses


def run_position_solve_until_good(
    device: AlignmentDevice,
    *,
    target_mode_efficiency: float = DEFAULT_TARGET_MODE_EFFICIENCY,
    max_attempts: int = DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
    search_windows: tuple[float, ...] = POSITION_SOLVE_SEARCH_WINDOWS,
) -> PositionSolveAlignmentStatus:
    attempts = max(1, max_attempts)
    search_windows = search_windows or (AXIAL_SEARCH_WINDOW,)
    final_reading = device.measure()
    last_candidate: PositionSolveCandidate | None = None

    for attempt_index in range(attempts):
        final_reading = move_to_starting_poses(device)
        geometry = device.model_geometry()
        search_window = search_windows[min(attempt_index, len(search_windows) - 1)]
        candidate = solve_position_candidate(geometry, axial_search_window=search_window)
        if candidate is None:
            final_reading = device.measure()
            continue

        last_candidate = candidate
        final_reading = move_to_target_poses(device, geometry, candidate.poses)
        if final_reading.mode_efficiency >= target_mode_efficiency:
            return PositionSolveAlignmentStatus(
                candidate=last_candidate,
                final_reading=final_reading,
                attempts=attempt_index + 1,
                success=True,
            )

    return PositionSolveAlignmentStatus(
        candidate=last_candidate,
        final_reading=final_reading,
        attempts=attempts,
        success=False,
    )


def run_position_solve_with_j_steps_until_good(
    device: AlignmentDevice,
    *,
    target_mode_efficiency: float = DEFAULT_TARGET_MODE_EFFICIENCY,
    max_attempts: int = DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
    search_windows: tuple[float, ...] = POSITION_SOLVE_SEARCH_WINDOWS,
) -> PositionSolveAlignmentStatus:
    attempts = max(1, max_attempts)
    search_windows = search_windows or (AXIAL_SEARCH_WINDOW,)
    final_reading = device.measure()
    last_candidate: PositionSolveCandidate | None = None

    for attempt_index in range(attempts):
        final_reading = move_to_starting_poses(device)
        geometry = device.model_geometry()
        search_window = search_windows[min(attempt_index, len(search_windows) - 1)]
        candidate = solve_position_candidate(geometry, axial_search_window=search_window)
        if candidate is None:
            final_reading = device.measure()
            continue

        last_candidate = candidate
        final_reading = _move_z_to_target_poses(device, geometry, candidate.poses)
        final_reading = _show_j_matrix_probe_moves(device)
        final_reading = move_to_target_poses(device, geometry, candidate.poses)
        if final_reading.mode_efficiency >= target_mode_efficiency:
            return PositionSolveAlignmentStatus(
                candidate=last_candidate,
                final_reading=final_reading,
                attempts=attempt_index + 1,
                success=True,
            )

    return PositionSolveAlignmentStatus(
        candidate=last_candidate,
        final_reading=final_reading,
        attempts=attempts,
        success=False,
    )


def _move_z_to_target_poses(
    device: AlignmentDevice,
    geometry: AlignmentModelGeometry,
    target_poses: tuple[LensPose, ...],
) -> PowerReading:
    reading: PowerReading | None = None
    remaining_z = set(range(len(target_poses)))

    while remaining_z:
        moved_index = None
        current_poses = device.current_poses()
        for index in sorted(remaining_z):
            current_pose = current_poses[index]
            target_z = target_poses[index][2]
            if math.isclose(current_pose[2], target_z, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE):
                moved_index = index
                break
            trial_poses = list(current_poses)
            trial_poses[index] = (current_pose[0], current_pose[1], target_z)
            if has_strict_axial_clearance(
                tuple(trial_poses),
                geometry.balls,
                geometry.source.position,
                geometry.taper.position,
            ):
                reading = device.move_lens(index, dz=target_z - current_pose[2])
                moved_index = index
                break

        if moved_index is None:
            raise RuntimeError("could not move to solved z pose without violating axial no-touch constraints")
        remaining_z.remove(moved_index)

    return reading if reading is not None else device.measure()


def _show_j_matrix_probe_moves(device: AlignmentDevice) -> PowerReading:
    reading: PowerReading | None = None
    for dimension in (0, 1):
        for lens_index in (0, 1):
            if dimension == 0:
                reading = device.move_lens(lens_index, dx=TRANSVERSE_RESPONSE_STEP)
                reading = device.move_lens(lens_index, dx=-TRANSVERSE_RESPONSE_STEP)
            else:
                reading = device.move_lens(lens_index, dy=TRANSVERSE_RESPONSE_STEP)
                reading = device.move_lens(lens_index, dy=-TRANSVERSE_RESPONSE_STEP)
    return reading if reading is not None else device.measure()


def move_to_target_poses(
    device: AlignmentDevice,
    geometry: AlignmentModelGeometry,
    target_poses: tuple[LensPose, ...],
) -> PowerReading:
    reading: PowerReading | None = None
    remaining_z = set(range(len(target_poses)))

    while remaining_z:
        moved_index = None
        current_poses = device.current_poses()
        for index in sorted(remaining_z):
            current_pose = current_poses[index]
            target_z = target_poses[index][2]
            if math.isclose(current_pose[2], target_z, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE):
                moved_index = index
                break
            trial_poses = list(current_poses)
            trial_poses[index] = (current_pose[0], current_pose[1], target_z)
            if has_strict_axial_clearance(
                tuple(trial_poses),
                geometry.balls,
                geometry.source.position,
                geometry.taper.position,
            ):
                reading = device.move_lens(index, dz=target_z - current_pose[2])
                moved_index = index
                break

        if moved_index is None:
            raise RuntimeError("could not move to solved pose without violating axial no-touch constraints")
        remaining_z.remove(moved_index)

    for index, target_pose in enumerate(target_poses):
        current_pose = device.current_poses()[index]
        dx = target_pose[0] - current_pose[0]
        dy = target_pose[1] - current_pose[1]
        if (
            math.isclose(dx, 0.0, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE)
            and math.isclose(dy, 0.0, rel_tol=0.0, abs_tol=POSITION_ABSOLUTE_TOLERANCE)
        ):
            continue
        reading = device.move_lens(index, dx=dx, dy=dy)

    return reading if reading is not None else device.measure()


def _source_from_geometry(source: SourceGeometry) -> LaserSource:
    return LaserSource(
        name=source.name,
        position=source.position,
        wavelength=source.wavelength,
        waist_radius=source.waist_radius,
        waist_radius_y=source.waist_radius_y,
        rayleigh_range=source.rayleigh_range,
        rayleigh_range_y=source.rayleigh_range_y,
        waist_position=source.waist_position,
        power=source.power,
        x_offset=source.x_offset,
        y_offset=source.y_offset,
        x_angle=source.x_angle,
        y_angle=source.y_angle,
    )


def _taper_from_geometry(taper: TaperGeometry) -> TaperDetectorElement:
    return TaperDetectorElement(
        name=taper.name,
        position=taper.position,
        width=taper.width,
        height=taper.height,
        mode_radius_x=taper.mode_radius_x,
        mode_radius_y=taper.mode_radius_y,
        extra_transmission=taper.extra_transmission,
        facet_refractive_index=taper.facet_refractive_index,
        x_offset=taper.x_offset,
        y_offset=taper.y_offset,
    )


def _balls_from_geometry(
    balls: tuple[BallLensGeometry, ...],
    poses: tuple[LensPose, ...],
) -> list[BallLensElement]:
    return [
        BallLensElement(
            name=ball.name,
            position=pose[2],
            diameter=ball.diameter,
            refractive_index=ball.refractive_index,
            x_offset=pose[0],
            y_offset=pose[1],
        )
        for ball, pose in zip(balls, poses)
    ]


def _is_miss(warnings: list[str]) -> bool:
    return any("MISS" in warning for warning in warnings)


def _finite_poses(poses: tuple[LensPose, ...]) -> bool:
    return all(math.isfinite(value) for pose in poses for value in pose)


class _PositionSolver:
    def __init__(self, *, axial_search_window: float) -> None:
        self.axial_search_window = axial_search_window

    def find_best_candidate(self, geometry: AlignmentModelGeometry) -> PositionSolveCandidate | None:
        best: PositionSolveCandidate | None = None
        seen: set[tuple[int, int]] = set()

        anchors = self._unique_z_anchors(geometry)
        for anchor in anchors:
            best = self._search_grid(
                geometry,
                center=anchor,
                step=AXIAL_SEARCH_STEPS[0],
                span=self.axial_search_window,
                best=best,
                seen=seen,
            )

        if best is None:
            return None

        center = tuple(pose[2] for pose in best.poses)
        for step in AXIAL_SEARCH_STEPS[1:]:
            span = step * AXIAL_REFINEMENT_SPAN_STEPS
            best = self._search_grid(geometry, center=center, step=step, span=span, best=best, seen=seen)
            if best is not None:
                center = tuple(pose[2] for pose in best.poses)
        return best

    def _unique_z_anchors(self, geometry: AlignmentModelGeometry) -> tuple[tuple[float, float], ...]:
        anchors: list[tuple[float, float]] = []
        for poses in (geometry.current_poses, geometry.starting_poses):
            if len(poses) != 2:
                continue
            anchor = (float(poses[0][2]), float(poses[1][2]))
            if anchor not in anchors:
                anchors.append(anchor)
        return tuple(anchors)

    def _search_grid(
        self,
        geometry: AlignmentModelGeometry,
        center: tuple[float, float],
        step: float,
        span: float,
        best: PositionSolveCandidate | None,
        seen: set[tuple[int, int]],
    ) -> PositionSolveCandidate | None:
        step_count = max(0, int(round(span / step)))
        offsets = [index * step for index in range(-step_count, step_count + 1)]
        for dz1 in offsets:
            for dz2 in offsets:
                z_positions = (center[0] + dz1, center[1] + dz2)
                key = tuple(round(z / 1e-12) for z in z_positions)
                if key in seen:
                    continue
                seen.add(key)
                candidate = self._candidate_from_z_positions(geometry, z_positions)
                if candidate is None:
                    continue
                if best is None or candidate.reading.received_power > best.reading.received_power:
                    best = candidate
        return best

    def _candidate_from_z_positions(
        self,
        geometry: AlignmentModelGeometry,
        z_positions: tuple[float, float],
    ) -> PositionSolveCandidate | None:
        zero_offset_poses = tuple((0.0, 0.0, z_position) for z_position in z_positions)
        if not has_strict_axial_clearance(
            zero_offset_poses,
            geometry.balls,
            geometry.source.position,
            geometry.taper.position,
        ):
            return None

        x_offsets = self._solve_axis_offsets(geometry, z_positions, "x")
        y_offsets = self._solve_axis_offsets(geometry, z_positions, "y")
        if x_offsets is None or y_offsets is None:
            return None

        poses = tuple(
            (float(x_offset), float(y_offset), float(z_position))
            for x_offset, y_offset, z_position in zip(x_offsets, y_offsets, z_positions)
        )
        return self._simulate_candidate(geometry, poses)

    def _solve_axis_offsets(
        self,
        geometry: AlignmentModelGeometry,
        z_positions: tuple[float, float],
        axis: str,
    ) -> tuple[float, float] | None:
        base = self._axis_state(geometry, z_positions, axis, offsets=(0.0, 0.0))
        if base is None:
            return None

        columns = []
        for index in range(2):
            offsets = [0.0, 0.0]
            offsets[index] = TRANSVERSE_RESPONSE_STEP
            probe = self._axis_state(geometry, z_positions, axis, offsets=tuple(offsets))
            if probe is None:
                return None
            columns.append((probe - base) / TRANSVERSE_RESPONSE_STEP)

        response = np.column_stack(columns)
        if not np.all(np.isfinite(response)) or np.linalg.cond(response) > SINGULAR_CONDITION_LIMIT:
            return None

        target_offset = geometry.taper.x_offset if axis == "x" else geometry.taper.y_offset
        target = np.array([target_offset, 0.0], dtype=float)
        try:
            solution = np.linalg.solve(response, target - base)
        except np.linalg.LinAlgError:
            return None
        if not np.all(np.isfinite(solution)):
            return None
        return (float(solution[0]), float(solution[1]))

    def _axis_state(
        self,
        geometry: AlignmentModelGeometry,
        z_positions: tuple[float, float],
        axis: str,
        offsets: tuple[float, float],
    ) -> np.ndarray | None:
        poses = tuple(
            (
                offsets[index] if axis == "x" else 0.0,
                offsets[index] if axis == "y" else 0.0,
                z_position,
            )
            for index, z_position in enumerate(z_positions)
        )
        if not has_strict_axial_clearance(poses, geometry.balls, geometry.source.position, geometry.taper.position):
            return None

        source = _source_from_geometry(geometry.source)
        taper = _taper_from_geometry(geometry.taper)
        balls = _balls_from_geometry(geometry.balls, poses)
        result = simulate_source_to_taper(
            source,
            balls,
            taper,
            clipping_radius_factor=geometry.clipping_radius_factor,
        )
        if _is_miss(result.warnings):
            return None
        if axis == "x":
            values = (result.beam_x, result.beam_x_angle)
        else:
            values = (result.beam_y, result.beam_y_angle)
        if any(value is None or not math.isfinite(value) for value in values):
            return None
        return np.array(values, dtype=float)

    def _simulate_candidate(
        self,
        geometry: AlignmentModelGeometry,
        poses: tuple[LensPose, ...],
    ) -> PositionSolveCandidate | None:
        if not _finite_poses(poses):
            return None
        if not has_strict_axial_clearance(poses, geometry.balls, geometry.source.position, geometry.taper.position):
            return None

        source = _source_from_geometry(geometry.source)
        taper = _taper_from_geometry(geometry.taper)
        balls = _balls_from_geometry(geometry.balls, poses)
        result = simulate_source_to_taper(
            source,
            balls,
            taper,
            clipping_radius_factor=geometry.clipping_radius_factor,
        )
        if _is_miss(result.warnings) or not math.isfinite(result.received_power):
            return None
        if result.beam_radius_x is None or result.beam_radius_y is None:
            return None

        total_efficiency = result.received_power / source.power if source.power > 0 else 0.0
        reading = PowerReading(
            received_power=result.received_power,
            total_efficiency=total_efficiency,
            mode_efficiency=result.mode_efficiency,
        )
        return PositionSolveCandidate(poses=poses, reading=reading)


class PositionSolveAlgorithm:
    name = "position_solve"
    display_name = "Position solve/noiseless model"

    def __init__(
        self,
        *,
        target_mode_efficiency: float = DEFAULT_TARGET_MODE_EFFICIENCY,
        max_attempts: int = DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
        search_windows: tuple[float, ...] = POSITION_SOLVE_SEARCH_WINDOWS,
    ) -> None:
        self.target_mode_efficiency = target_mode_efficiency
        self.max_attempts = max(1, max_attempts)
        self.search_windows = search_windows

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        status = run_position_solve_until_good(
            device,
            target_mode_efficiency=self.target_mode_efficiency,
            max_attempts=self.max_attempts,
            search_windows=self.search_windows,
        )
        candidate = status.candidate
        outcome = "reached" if status.success else "did not reach"
        model_message = (
            "no valid no-touch ball-lens model solution"
            if candidate is None
            else f"model score {candidate.reading.received_power * 1e3:.6g} mW"
        )
        return AlignmentAlgorithmResult(
            name=self.name,
            display_name=self.display_name,
            final_poses=device.current_poses(),
            final_reading=status.final_reading,
            move_history=device.move_history(),
            message=(
                "Reference-pose bootstrap, then noiseless geometry solve using "
                "source/taper/lens positions; "
                f"{outcome} {self.target_mode_efficiency * 100:.0f}% mode match "
                f"in {status.attempts} attempt(s); {model_message}."
            ),
        )


class PositionSolveWithJStepsAlgorithm:
    name = "position_solve_j_steps"
    display_name = "Position solve/show J steps"

    def __init__(
        self,
        *,
        target_mode_efficiency: float = DEFAULT_TARGET_MODE_EFFICIENCY,
        max_attempts: int = DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
        search_windows: tuple[float, ...] = POSITION_SOLVE_SEARCH_WINDOWS,
    ) -> None:
        self.target_mode_efficiency = target_mode_efficiency
        self.max_attempts = max(1, max_attempts)
        self.search_windows = search_windows

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        status = run_position_solve_with_j_steps_until_good(
            device,
            target_mode_efficiency=self.target_mode_efficiency,
            max_attempts=self.max_attempts,
            search_windows=self.search_windows,
        )
        candidate = status.candidate
        outcome = "reached" if status.success else "did not reach"
        model_message = (
            "no valid no-touch ball-lens model solution"
            if candidate is None
            else f"model score {candidate.reading.received_power * 1e3:.6g} mW"
        )
        return AlignmentAlgorithmResult(
            name=self.name,
            display_name=self.display_name,
            final_poses=device.current_poses(),
            final_reading=status.final_reading,
            move_history=device.move_history(),
            message=(
                "Reference-pose bootstrap, visible J-matrix probe moves, then "
                "noiseless geometry solve using source/taper/lens positions; "
                f"{outcome} {self.target_mode_efficiency * 100:.0f}% mode match "
                f"in {status.attempts} attempt(s); {model_message}."
            ),
        )
