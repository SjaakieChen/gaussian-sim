"""Headless alignment session for running algorithms without Tk."""

from __future__ import annotations

import copy
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from alignment_algorithms import AlignmentMove, LensPose, PowerReading, get_algorithm
from alignment_algorithms.base import AlignmentAlgorithmResult
from interactive_setup import (
    DEFAULT_CLIPPING_RADIUS_FACTOR,
    DEFAULT_REFRACTIVE_INDEX,
    BallLensElement,
    LaserSource,
    NominalElementState,
    TaperDetectorElement,
    capture_element_nominal,
    default_ball_lens_layout,
    propagate_astigmatic_through_balls,
    scramble_element_positive,
    simulate_layout,
)

if TYPE_CHECKING:
    from alignment_algorithms.base import AlignmentAlgorithm


DEFAULT_SOURCE_DETECTOR_TOLERANCE = 5.0e-6
DEFAULT_LENS_POSE_TOLERANCE = 2.0e-6


@dataclass(frozen=True)
class AlignmentMetrics:
    received_power: float
    total_efficiency: float
    mode_efficiency: float
    warnings: tuple[str, ...] = ()


@dataclass
class AlignmentLayout:
    source: LaserSource
    balls: list[BallLensElement]
    tapers: list[TaperDetectorElement]
    final_z: float
    nominal_ball_poses: tuple[LensPose, ...] = field(default_factory=tuple)
    element_nominals: dict[str, NominalElementState] = field(default_factory=dict)

    def copy(self) -> AlignmentLayout:
        return AlignmentLayout(
            source=copy.copy(self.source),
            balls=[copy.copy(ball) for ball in self.balls],
            tapers=[copy.copy(taper) for taper in self.tapers],
            final_z=self.final_z,
            nominal_ball_poses=self.nominal_ball_poses,
            element_nominals=dict(self.element_nominals),
        )

    def current_poses(self) -> tuple[LensPose, ...]:
        return tuple((ball.x_offset, ball.y_offset, ball.position) for ball in self.balls)

    def apply_poses(self, poses: tuple[LensPose, ...]) -> None:
        if len(poses) != len(self.balls):
            raise ValueError("lens pose count does not match the current ball count")
        for ball, pose in zip(self.balls, poses):
            x_offset, y_offset, position = pose
            ball.x_offset = float(x_offset)
            ball.y_offset = float(y_offset)
            ball.position = float(position)


def default_alignment_layout() -> AlignmentLayout:
    source = LaserSource()
    balls, tapers, final_z = default_ball_lens_layout()
    layout = AlignmentLayout(
        source=source,
        balls=balls,
        tapers=tapers,
        final_z=final_z,
    )
    capture_layout_nominals(layout)
    return layout


def capture_layout_nominals(layout: AlignmentLayout) -> None:
    elements = [layout.source, *layout.balls, *layout.tapers]
    layout.element_nominals = {element.uid: capture_element_nominal(element) for element in elements}
    layout.nominal_ball_poses = layout.current_poses()


def evaluate_alignment_layout(layout: AlignmentLayout) -> AlignmentMetrics:
    results = simulate_layout(
        [layout.source],
        [],
        [],
        layout.final_z,
        balls=layout.balls,
        tapers=layout.tapers,
        refractive_index=DEFAULT_REFRACTIVE_INDEX,
        clipping_radius_factor=DEFAULT_CLIPPING_RADIUS_FACTOR,
    )
    received_power = 0.0
    total_efficiency = 0.0
    mode_efficiency = 0.0
    warnings: list[str] = []

    if results and results[0].taper_results:
        taper_result = results[0].taper_results[0]
        received_power = taper_result.received_power
        mode_efficiency = taper_result.mode_efficiency
        total_efficiency = received_power / layout.source.power if layout.source.power > 0 else 0.0
        warnings.extend(results[0].warnings)
        warnings.extend(taper_result.warnings)

    return AlignmentMetrics(
        received_power=received_power,
        total_efficiency=total_efficiency,
        mode_efficiency=mode_efficiency,
        warnings=tuple(warnings),
    )


def beam_centroid_at_ball_entry(layout: AlignmentLayout, lens_index: int) -> tuple[float, float]:
    if lens_index < 0 or lens_index >= len(layout.balls):
        raise IndexError("lens_index is out of range")

    target_ball = layout.balls[lens_index]
    ordered_indices = sorted(range(len(layout.balls)), key=lambda index: layout.balls[index].position)
    target_order_index = ordered_indices.index(lens_index)
    upstream = [layout.balls[index] for index in ordered_indices[:target_order_index]]
    state, _reports, _missed, _samples = propagate_astigmatic_through_balls(
        layout.source,
        upstream,
        target_ball.entry_z,
    )
    return state.x, state.y


def restore_nominal_ball_poses(layout: AlignmentLayout) -> None:
    if not layout.nominal_ball_poses:
        capture_layout_nominals(layout)
    layout.apply_poses(layout.nominal_ball_poses)


def apply_laser_taper_scramble(layout: AlignmentLayout, seed: int | None = None) -> None:
    """Keep ball lenses at nominal alignment; scramble only the source and taper x/y."""

    if not layout.element_nominals:
        capture_layout_nominals(layout)

    restore_nominal_ball_poses(layout)
    rng = random.Random(seed)
    for element in [layout.source, *layout.tapers]:
        nominal = layout.element_nominals[element.uid]
        scramble_element_positive(element, nominal, rng=rng)


def apply_full_scramble(layout: AlignmentLayout, seed: int | None = None) -> None:
    if not layout.element_nominals:
        capture_layout_nominals(layout)

    rng = random.Random(seed)
    for ball in layout.balls:
        nominal = layout.element_nominals[ball.uid]
        scramble_element_positive(ball, nominal, rng=rng)
    for element in [layout.source, *layout.tapers]:
        nominal = layout.element_nominals[element.uid]
        scramble_element_positive(element, nominal, rng=rng)


def apply_lab_scramble(
    layout: AlignmentLayout,
    seed: int,
    source_detector_tolerance: float = DEFAULT_SOURCE_DETECTOR_TOLERANCE,
    lens_pose_tolerance: float = DEFAULT_LENS_POSE_TOLERANCE,
    *,
    scramble_balls: bool = True,
) -> None:
    from alignment_lab import seeded_alignment_errors

    if not layout.nominal_ball_poses:
        capture_layout_nominals(layout)

    scramble = seeded_alignment_errors(
        seed,
        source_detector_tolerance=source_detector_tolerance,
        lens_pose_tolerance=lens_pose_tolerance,
        lens_count=len(layout.balls),
    )
    layout.source.x_offset = scramble.source_x_offset
    layout.source.y_offset = scramble.source_y_offset
    layout.tapers[0].x_offset = scramble.taper_x_offset
    layout.tapers[0].y_offset = scramble.taper_y_offset
    if scramble_balls:
        poses = tuple(
            (
                nominal_x + offset_x,
                nominal_y + offset_y,
                nominal_z + offset_z,
            )
            for (nominal_x, nominal_y, nominal_z), (offset_x, offset_y, offset_z) in zip(
                layout.nominal_ball_poses,
                scramble.ball_pose_offsets,
            )
        )
        layout.apply_poses(poses)
    else:
        restore_nominal_ball_poses(layout)


class HeadlessAlignmentDevice:
    """Step-based lens move/measure interface for headless alignment runs."""

    def __init__(self, layout: AlignmentLayout) -> None:
        self._layout = layout
        self._move_count = 0
        self._measurement_count = 0
        self._move_history: list[AlignmentMove] = []

    @property
    def layout(self) -> AlignmentLayout:
        return self._layout

    def current_poses(self) -> tuple[LensPose, ...]:
        return self._layout.current_poses()

    def measure(self) -> PowerReading:
        self._measurement_count += 1
        return self._reading_from_metrics(evaluate_alignment_layout(self._layout))

    def move_lens(
        self,
        lens_index: int,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
    ) -> PowerReading:
        if lens_index < 0 or lens_index >= len(self._layout.balls):
            raise IndexError("lens_index is out of range")
        poses = list(self.current_poses())
        x_offset, y_offset, position = poses[lens_index]
        poses[lens_index] = (
            x_offset + float(dx),
            y_offset + float(dy),
            position + float(dz),
        )
        self._layout.apply_poses(tuple(poses))
        self._move_count += 1
        self._measurement_count += 1
        reading = self._reading_from_metrics(evaluate_alignment_layout(self._layout))
        self._move_history.append(
            AlignmentMove(
                lens_index=lens_index,
                dx=float(dx),
                dy=float(dy),
                dz=float(dz),
                poses=tuple(poses),
                reading=reading,
            )
        )
        return reading

    def move_history(self) -> tuple[AlignmentMove, ...]:
        return tuple(self._move_history)

    def beam_centroid_at_ball_entry(self, lens_index: int) -> tuple[float, float]:
        return beam_centroid_at_ball_entry(self._layout, lens_index)

    def _reading_from_metrics(self, metrics: AlignmentMetrics) -> PowerReading:
        return PowerReading(
            received_power=metrics.received_power,
            total_efficiency=metrics.total_efficiency,
            mode_efficiency=metrics.mode_efficiency,
            move_count=self._move_count,
            measurement_count=self._measurement_count,
        )


def run_alignment_session(
    algorithm_name: str,
    layout: AlignmentLayout,
) -> tuple[AlignmentAlgorithm, AlignmentAlgorithmResult, HeadlessAlignmentDevice]:
    if not layout.balls:
        raise ValueError("No ball lenses are available to align.")

    algorithm = get_algorithm(algorithm_name)
    device = HeadlessAlignmentDevice(layout)
    result = algorithm.run(device)
    if len(result.final_poses) != len(layout.balls):
        raise ValueError("algorithm result has the wrong number of lens poses")
    layout.apply_poses(result.final_poses)
    return algorithm, result, device
