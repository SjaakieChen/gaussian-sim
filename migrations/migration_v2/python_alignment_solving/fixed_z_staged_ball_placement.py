"""Fixed-Z staged two-ball placement planner for TMPython/YASE.

The planner is self-contained for the migration v2 bundle.  It accepts measured
laser and detector coordinates, solves the fixed-Z two-ball transverse target,
then returns a YASE-friendly staged motion plan:

1. move the selected clearance axis to a safe "above" coordinate;
2. move the other machine axes to the solved target coordinates;
3. lower the clearance axis to the solved target coordinate.

Coordinate convention, all in micrometres:

* machine X / Align_X maps to simulation z, the optical propagation axis;
* machine Z / Align_Z maps to simulation x;
* machine Y / Align_Y maps to simulation y, the vertical/transverse no-go axis.

Python only plans and validates.  YASE/TestMaster must still prompt the
operator, check fiducials/interlocks, validate the returned fields, move axes,
wait for completion, and check axis errors.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - used on developer machines without TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback so the planner can be tested outside TestMaster."""


SCHEMA_VERSION = 2
DEFAULT_BALL_DIAMETER_UM = 500.0
DEFAULT_BALL_REFRACTIVE_INDEX = 1.760
DEFAULT_RESPONSE_STEP_UM = 1.0
DEFAULT_TOLERANCE_UM = 0.05
DEFAULT_NO_GO_CLEARANCE_UM = 5.0
DEFAULT_MAX_SINGLE_MOVE_UM = 5000.0
DEFAULT_CLEARANCE_AXIS = "Y"
DEFAULT_LASER_NO_GO_Z_MAX_UM = 250.0
DEFAULT_TAPER_NO_GO_Z_MAX_UM = 250.0
DEFAULT_TRENCH_FLOOR_Z_MAX_UM = -500.0
DEFAULT_WORKSPACE_Y_MARGIN_UM = 2000.0
EPS = 1.0e-9

MACHINE_AXIS_TO_POSE_INDEX = {"Z": 0, "Y": 1, "X": 2}
MACHINE_AXIS_LABELS = {
    "X": "machine X / simulation z / optical propagation",
    "Z": "machine Z / simulation x",
    "Y": "machine Y / simulation y",
}
STAGE_ORDER = ("Align_X1", "Align_Z1", "Align_Y1", "Align_X2", "Align_Z2", "Align_Y2")


JsonDict = dict[str, Any]
Pose = tuple[float, float, float]  # simulation x, simulation y, simulation z (machine Z, Y, X)


@dataclass(frozen=True)
class SourceSpec:
    x_um: float
    z_um: float
    y_um: float
    x_angle: float
    z_angle: float


@dataclass(frozen=True)
class DetectorSpec:
    x_um: float
    z_um: float
    y_um: float


@dataclass(frozen=True)
class BallSpec:
    name: str
    x_um: float
    z_um: float
    y_um: float
    diameter_um: float
    refractive_index: float
    stage_index: int

    @property
    def radius_um(self) -> float:
        return 0.5 * self.diameter_um

    @property
    def entry_y_um(self) -> float:
        return self.y_um - self.radius_um

    @property
    def exit_y_um(self) -> float:
        return self.y_um + self.radius_um

    @property
    def pose(self) -> Pose:
        return (self.x_um, self.z_um, self.y_um)


@dataclass(frozen=True)
class NoGoZone:
    name: str
    y_min_um: float
    y_max_um: float
    x_min_um: float | None = None
    x_max_um: float | None = None
    z_min_um: float | None = None
    z_max_um: float | None = None
    label: str = ""

    @property
    def y_low_um(self) -> float:
        return min(self.y_min_um, self.y_max_um)

    @property
    def y_high_um(self) -> float:
        return max(self.y_min_um, self.y_max_um)

    def intersects_ball_pose(self, pose: Pose, radius_um: float) -> bool:
        x_um, z_um, y_um = pose
        entry_y = y_um - radius_um
        exit_y = y_um + radius_um
        if not (exit_y > self.y_low_um and entry_y < self.y_high_um):
            return False

        ball_x_min = x_um - radius_um
        ball_x_max = x_um + radius_um
        zone_x_min = -math.inf if self.x_min_um is None else self.x_min_um
        zone_x_max = math.inf if self.x_max_um is None else self.x_max_um
        if not (ball_x_max > zone_x_min and ball_x_min < zone_x_max):
            return False

        ball_z_min = z_um - radius_um
        ball_z_max = z_um + radius_um
        zone_z_min = -math.inf if self.z_min_um is None else self.z_min_um
        zone_z_max = math.inf if self.z_max_um is None else self.z_max_um
        return ball_z_max > zone_z_min and ball_z_min < zone_z_max


@dataclass(frozen=True)
class Geometry:
    source: SourceSpec
    detector: DetectorSpec
    balls: tuple[BallSpec, ...]


class FixedZStagedBallPlacementStep(TMPythonStatementJ):
    """TMPython statement class returning the next staged ball-placement move."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return solve_fixed_z_staged_ball_placement(params_in)
        except Exception as exc:  # fail closed for the machine call
            return abort_response(f"FixedZStagedBallPlacementStep failed: {exc}")


def solve_fixed_z_staged_ball_placement(params_in: JsonDict) -> JsonDict:
    """Return a collision-checked staged plan and the next absolute move."""

    require_schema(params_in)
    geometry = parse_geometry(params_in)
    if len(geometry.balls) != 2:
        raise ValueError("fixed-Z staged placement currently supports exactly two ball lenses")

    current_positions = positions_um(params_in)
    current_poses = current_poses_for_balls(geometry.balls, current_positions)
    target_poses = solve_fixed_z_target_poses(geometry)
    check_strict_final_clearance(geometry.source, geometry.detector, geometry.balls, target_poses)

    zones = no_go_zones_for(params_in, geometry)
    target_violations = no_go_violations(target_poses, geometry.balls, zones)
    if target_violations:
        return abort_response(
            "solved final ball pose intersects a no-go zone; refusing to plan motion",
            state={
                "violations": target_violations,
                "target_positions_um": stages_from_poses(target_poses),
                "axis_mapping": axis_mapping_json(),
            },
        )

    target_positions = stages_from_poses(target_poses)
    state_in = as_dict(params_in.get("state"))
    state_plan = state_in.get("planned_moves")
    if isinstance(state_plan, list):
        plan = remaining_planned_moves(state_plan, current_positions, tolerance_um(params_in))
        safe_clearance_um = finite_float(state_in.get("safe_clearance_um", 0.0), "state.safe_clearance_um")
        clearance_axis = clearance_axis_from_state(state_in)
    else:
        try:
            plan, safe_clearance_um, clearance_axis = build_staged_plan(
                params_in,
                geometry,
                current_poses,
                target_poses,
                zones,
            )
        except ValueError as exc:
            return abort_response(
                str(exc),
                state={
                    "target_positions_um": target_positions,
                    "axis_mapping": axis_mapping_json(),
                    "no_go_zones_um": [zone_to_json(zone) for zone in zones],
                },
            )
    state = output_state(
        target_positions=target_positions,
        planned_moves=plan,
        zones=zones,
        clearance_axis=clearance_axis,
        safe_clearance_um=safe_clearance_um,
    )

    if not plan:
        return done_response("fixed_z_staged_ball_placement path is complete", state=state)

    return move_response(
        plan[0],
        plan,
        "fixed_z_staged_ball_placement returning next operator-confirmed absolute move",
        state,
    )


def solve_fixed_z_target_poses(geometry: Geometry) -> tuple[Pose, ...]:
    ordered_balls = sorted(geometry.balls, key=lambda ball: ball.y_um)
    x_offsets = solve_axis_offsets(
        geometry.source.x_um,
        geometry.source.x_angle,
        geometry.detector.x_um,
        geometry.source.y_um,
        geometry.detector.y_um,
        ordered_balls,
        axis="x",
    )
    z_offsets = solve_axis_offsets(
        geometry.source.z_um,
        geometry.source.z_angle,
        geometry.detector.z_um,
        geometry.source.y_um,
        geometry.detector.y_um,
        ordered_balls,
        axis="z",
    )

    ordered_targets: dict[int, Pose] = {}
    for ball, x_um, z_um in zip(ordered_balls, x_offsets, z_offsets):
        ordered_targets[ball.stage_index] = (x_um, z_um, ball.y_um)
    return tuple(ordered_targets[index] for index in sorted(ordered_targets))


def solve_axis_offsets(
    source_offset_um: float,
    source_angle: float,
    detector_offset_um: float,
    source_y_um: float,
    detector_y_um: float,
    ordered_balls: list[BallSpec],
    *,
    axis: str,
) -> tuple[float, float]:
    base = axis_state(
        source_offset_um,
        source_angle,
        source_y_um,
        detector_y_um,
        ordered_balls,
        axis=axis,
        offsets_um=(0.0, 0.0),
    )
    columns: list[tuple[float, float]] = []
    for index in range(2):
        offsets = [0.0, 0.0]
        offsets[index] = DEFAULT_RESPONSE_STEP_UM
        probe = axis_state(
            source_offset_um,
            source_angle,
            source_y_um,
            detector_y_um,
            ordered_balls,
            axis=axis,
            offsets_um=tuple(offsets),
        )
        columns.append(
            (
                (probe[0] - base[0]) / DEFAULT_RESPONSE_STEP_UM,
                (probe[1] - base[1]) / DEFAULT_RESPONSE_STEP_UM,
            )
        )

    target = (detector_offset_um - base[0], 0.0 - base[1])
    return solve_2x2_columns(columns[0], columns[1], target)


def axis_state(
    source_offset_um: float,
    source_angle: float,
    source_y_um: float,
    detector_y_um: float,
    ordered_balls: list[BallSpec],
    *,
    axis: str,
    offsets_um: tuple[float, float],
) -> tuple[float, float]:
    y_um = source_y_um
    offset_um = source_offset_um
    angle = source_angle

    for ball, lens_offset_um in zip(ordered_balls, offsets_um):
        if ball.entry_y_um < y_um - EPS:
            raise ValueError(f"{ball.name} overlaps a previous optic or starts before the laser")
        distance = max(ball.entry_y_um - y_um, 0.0)
        offset_um += distance * angle
        y_um = ball.entry_y_um

        a, b, c, d = ball_matrix_terms(ball)
        relative_offset = offset_um - lens_offset_um
        offset_um = lens_offset_um + a * relative_offset + b * angle
        angle = c * relative_offset + d * angle
        y_um = ball.exit_y_um

    if detector_y_um < y_um - EPS:
        raise ValueError("detector/fiber is before the final ball lens")
    offset_um += max(detector_y_um - y_um, 0.0) * angle
    if not math.isfinite(offset_um) or not math.isfinite(angle):
        raise ValueError(f"non-finite {axis}-axis ray state")
    return (offset_um, angle)


def ball_matrix_terms(ball: BallSpec) -> tuple[float, float, float, float]:
    n = ball.refractive_index
    diameter = ball.diameter_um
    if n <= 1.0:
        raise ValueError(f"{ball.name} refractive_index must be greater than 1")
    if diameter <= 0.0:
        raise ValueError(f"{ball.name} diameter_um must be positive")
    a = (2.0 - n) / n
    b = diameter / n
    c = -4.0 * (n - 1.0) / (diameter * n)
    return (a, b, c, a)


def solve_2x2_columns(
    col0: tuple[float, float],
    col1: tuple[float, float],
    target: tuple[float, float],
) -> tuple[float, float]:
    a, c = col0
    b, d = col1
    det = a * d - b * c
    if abs(det) < 1.0e-12:
        raise ValueError("fixed-Z transverse response matrix is singular or nearly singular")
    y0, y1 = target
    x0 = (d * y0 - b * y1) / det
    x1 = (-c * y0 + a * y1) / det
    if not math.isfinite(x0) or not math.isfinite(x1):
        raise ValueError("fixed-Z transverse solve returned non-finite target offsets")
    return (x0, x1)


def build_staged_plan(
    params: JsonDict,
    geometry: Geometry,
    current_poses: tuple[Pose, ...],
    target_poses: tuple[Pose, ...],
    zones: tuple[NoGoZone, ...],
) -> tuple[list[JsonDict], float, str]:
    clearance_axis = clearance_axis_for(params)
    safe_clearance = safe_clearance_um(params, geometry, current_poses, target_poses, zones, clearance_axis)
    max_move = max_single_move_um(params)
    tolerance = tolerance_um(params)

    poses = list(current_poses)
    planned_moves: list[JsonDict] = []

    def add_absolute_move(lens_index: int, axis: str, target_um: float, phase: str) -> None:
        stage = stage_name(axis, lens_index + 1)
        start_pose = poses[lens_index]
        start_um = pose_stage_value(start_pose, axis)
        delta_um = target_um - start_um
        if abs(delta_um) <= tolerance:
            return
        if abs(delta_um) > max_move + EPS:
            raise ValueError(
                f"{stage} planned delta {delta_um:.6g} um exceeds max_single_move_um {max_move:.6g}"
            )
        target_pose = pose_with_stage_value(start_pose, axis, target_um)
        if not segment_clear_no_go(params, geometry, tuple(poses), lens_index, target_pose, zones):
            raise ValueError(f"{stage} {phase} segment intersects a no-go zone")
        poses[lens_index] = target_pose
        planned_moves.append(
            {
                "id": len(planned_moves) + 1,
                "phase": phase,
                "lens": lens_index + 1,
                "stage": stage,
                "target_um": target_um,
                "distance_um": target_um,
                "delta_um": delta_um,
                "mode": "absolute",
                "sync": "No sync",
                "confirm_required": True,
                "confirm_text": confirm_text(stage, start_um, target_um, delta_um, phase),
            }
        )

    for lens_index, _pose in enumerate(poses):
        add_absolute_move(lens_index, clearance_axis, safe_clearance, "raise_clearance")

    for lens_index, target_pose in enumerate(target_poses):
        for axis in ("X", "Z", "Y"):
            if axis == clearance_axis:
                continue
            add_absolute_move(lens_index, axis, pose_stage_value(target_pose, axis), "move_to_solved_coordinates")

    for lens_index, target_pose in enumerate(target_poses):
        add_absolute_move(lens_index, clearance_axis, pose_stage_value(target_pose, clearance_axis), "lower_to_solved_coordinate")

    final_poses = tuple(poses)
    if no_go_violations(final_poses, geometry.balls, zones):
        raise ValueError("planned final poses intersect a no-go zone")
    check_strict_final_clearance(geometry.source, geometry.detector, geometry.balls, final_poses)
    return planned_moves, safe_clearance, clearance_axis


def segment_clear_no_go(
    params: JsonDict,
    geometry: Geometry,
    poses: tuple[Pose, ...],
    lens_index: int,
    target_pose: Pose,
    zones: tuple[NoGoZone, ...],
) -> bool:
    start_pose = poses[lens_index]
    samples = int(as_dict(params.get("algorithm")).get("path_samples", 25) or 25)
    samples = max(2, min(samples, 100))
    for step in range(1, samples + 1):
        t = step / samples
        pose = (
            start_pose[0] + (target_pose[0] - start_pose[0]) * t,
            start_pose[1] + (target_pose[1] - start_pose[1]) * t,
            start_pose[2] + (target_pose[2] - start_pose[2]) * t,
        )
        trial_poses = list(poses)
        trial_poses[lens_index] = pose
        if no_go_violations(tuple(trial_poses), geometry.balls, zones):
            return False
        if not within_axis_limits(params, tuple(trial_poses)):
            return False
    return True


def check_strict_final_clearance(
    source: SourceSpec,
    detector: DetectorSpec,
    balls: tuple[BallSpec, ...],
    poses: tuple[Pose, ...],
) -> None:
    ordered = sorted(zip(balls, poses), key=lambda item: item[1][2])
    previous_exit = source.y_um
    for ball, pose in ordered:
        radius = ball.radius_um
        entry = pose[2] - radius
        exit_y = pose[2] + radius
        if entry <= previous_exit + EPS:
            raise ValueError("ball lenses/source/detector do not have strict positive optical-axis clearance")
        previous_exit = exit_y
    if detector.y_um <= previous_exit + EPS:
        raise ValueError("detector/fiber does not have strict positive optical-axis clearance after final ball")


def no_go_zones_for(params: JsonDict, geometry: Geometry) -> tuple[NoGoZone, ...]:
    limits = as_dict(params.get("limits"))
    algorithm = as_dict(params.get("algorithm"))
    zones: list[NoGoZone] = []
    if bool(algorithm.get("default_no_go_zones", True)):
        zones.extend(default_no_go_zones(geometry))
    raw_zones = limits.get("no_go_zones_um") or algorithm.get("no_go_zones_um") or []
    if isinstance(raw_zones, list):
        zones.extend(parse_no_go_zone(as_dict(zone), index) for index, zone in enumerate(raw_zones, start=1))
    return tuple(zones)


def default_no_go_zones(geometry: Geometry) -> list[NoGoZone]:
    source_y = geometry.source.y_um
    detector_y = geometry.detector.y_um
    return [
        NoGoZone(
            name="laser_side_no_go",
            y_min_um=min(source_y - DEFAULT_WORKSPACE_Y_MARGIN_UM, source_y),
            y_max_um=source_y,
            z_max_um=DEFAULT_LASER_NO_GO_Z_MAX_UM,
            label="Laser no-go below +250 um machine Y",
        ),
        NoGoZone(
            name="trench_floor",
            y_min_um=source_y,
            y_max_um=detector_y,
            z_max_um=DEFAULT_TRENCH_FLOOR_Z_MAX_UM,
            label="Trench floor below -500 um machine Y",
        ),
        NoGoZone(
            name="detector_side_no_go",
            y_min_um=detector_y,
            y_max_um=detector_y + DEFAULT_WORKSPACE_Y_MARGIN_UM,
            z_max_um=DEFAULT_TAPER_NO_GO_Z_MAX_UM,
            label="Detector/fiber no-go below +250 um machine Y",
        ),
    ]


def dynamic_vacuum_zones(balls: tuple[BallSpec, ...], poses: tuple[Pose, ...]) -> list[NoGoZone]:
    zones: list[NoGoZone] = []
    for ball, pose in zip(balls, poses):
        radius = ball.radius_um
        zones.append(
            NoGoZone(
                name=f"vacuum_tweezer_{ball.stage_index}",
                y_min_um=pose[2] - radius,
                y_max_um=pose[2] + radius,
                x_min_um=pose[0] - radius,
                x_max_um=pose[0] + radius,
                z_min_um=pose[1] + radius,
                z_max_um=None,
                label=f"Vacuum tweezer B{ball.stage_index}",
            )
        )
    return zones


def parse_no_go_zone(block: JsonDict, index: int) -> NoGoZone:
    return NoGoZone(
        name=str(block.get("name") or f"no_go_zone_{index}"),
        y_min_um=finite_float(
            first_present(block, "machine_x_min_um", "optical_y_min_um", "sim_z_min_um", default=0.0),
            "no_go_zone.machine_x_min_um",
        ),
        y_max_um=finite_float(
            first_present(block, "machine_x_max_um", "optical_y_max_um", "sim_z_max_um", default=0.0),
            "no_go_zone.machine_x_max_um",
        ),
        x_min_um=optional_float(first_present(block, "machine_z_min_um", "sim_x_min_um", "x_min_um", default=None), "no_go_zone.x_min_um"),
        x_max_um=optional_float(first_present(block, "machine_z_max_um", "sim_x_max_um", "x_max_um", default=None), "no_go_zone.x_max_um"),
        z_min_um=optional_float(first_present(block, "machine_y_min_um", "sim_y_min_um", "z_min_um", default=None), "no_go_zone.z_min_um"),
        z_max_um=optional_float(first_present(block, "machine_y_max_um", "sim_y_max_um", "z_max_um", default=None), "no_go_zone.z_max_um"),
        label=str(block.get("label") or block.get("name") or ""),
    )


def no_go_violations(
    poses: tuple[Pose, ...],
    balls: tuple[BallSpec, ...],
    zones: tuple[NoGoZone, ...],
) -> list[JsonDict]:
    violations: list[JsonDict] = []
    all_zones = tuple(zones) + tuple(dynamic_vacuum_zones(balls, poses))
    for ball, pose in zip(balls, poses):
        for zone in all_zones:
            if zone.intersects_ball_pose(pose, ball.radius_um):
                violations.append(
                    {
                        "ball": ball.name,
                        "stage_index": ball.stage_index,
                        "zone": zone.name,
                        "label": zone.label or zone.name,
                    }
                )
    return violations


def safe_clearance_um(
    params: JsonDict,
    geometry: Geometry,
    current_poses: tuple[Pose, ...],
    target_poses: tuple[Pose, ...],
    zones: tuple[NoGoZone, ...],
    axis: str,
) -> float:
    staging = as_dict(params.get("staging"))
    explicit = first_present(
        staging,
        f"safe_machine_{axis.lower()}_um",
        f"above_machine_{axis.lower()}_um",
        "safe_clearance_um",
        "clearance_target_um",
        default=None,
    )
    if explicit is not None:
        return finite_float(explicit, f"staging.safe_machine_{axis.lower()}_um")

    radius = max(ball.radius_um for ball in geometry.balls)
    clearance = no_go_clearance_um(params)
    candidates = [pose_stage_value(pose, axis) for pose in tuple(current_poses) + tuple(target_poses)]
    if axis == "Y":
        for zone in zones:
            if zone.z_max_um is not None:
                candidates.append(zone.z_max_um + radius + clearance)
    elif axis == "X":
        for zone in zones:
            candidates.append(zone.y_high_um + radius + clearance)
    elif axis == "Z":
        for pose in tuple(current_poses) + tuple(target_poses):
            candidates.append(pose[0] + radius + clearance)
    else:  # pragma: no cover - axis is normalized before this point
        raise ValueError(f"unsupported clearance axis {axis!r}")
    return max(candidates)


def within_axis_limits(params: JsonDict, poses: tuple[Pose, ...]) -> bool:
    raw_limits = as_dict(as_dict(params.get("limits")).get("max_abs_um"))
    if not raw_limits:
        return True
    positions = stages_from_poses(poses)
    for stage, value in positions.items():
        if stage not in raw_limits:
            continue
        limit = positive_float(raw_limits[stage], f"limits.max_abs_um.{stage}")
        if abs(value) > limit + EPS:
            return False
    return True


def parse_geometry(params: JsonDict) -> Geometry:
    geometry = as_dict(params.get("geometry_um") or params.get("geometry"))
    positions = positions_um(params, required=False)
    source_block = as_dict(geometry.get("laser") or geometry.get("source"))
    detector_block = as_dict(
        geometry.get("detector") or geometry.get("fiber") or geometry.get("taper") or geometry.get("waveguide")
    )
    raw_balls = geometry.get("balls")

    source = SourceSpec(
        x_um=finite_float(first_present(source_block, "machine_z_um", "x_um", "sim_x_um", default=0.0), "laser.sim_x_um"),
        z_um=finite_float(
            first_present(source_block, "machine_y_um", "z_um", "sim_y_um", "transverse_z_um", default=0.0),
            "laser.sim_y_um",
        ),
        y_um=finite_float(
            first_present(source_block, "machine_x_um", "optical_y_um", "position_um", "sim_z_um", default=0.0),
            "laser.sim_z_um",
        ),
        x_angle=angle_value(source_block, "x"),
        z_angle=angle_value(source_block, "z"),
    )

    detector = DetectorSpec(
        x_um=finite_float(
            first_present(detector_block, "machine_z_um", "x_um", "sim_x_um", default=0.0),
            "detector.sim_x_um",
        ),
        z_um=finite_float(
            first_present(detector_block, "machine_y_um", "z_um", "sim_y_um", "transverse_z_um", default=0.0),
            "detector.sim_y_um",
        ),
        y_um=finite_float(
            first_present(detector_block, "machine_x_um", "optical_y_um", "position_um", "sim_z_um", default=1278.0),
            "detector.sim_z_um",
        ),
    )

    balls: list[BallSpec] = []
    if isinstance(raw_balls, list) and raw_balls:
        for index, raw_ball in enumerate(raw_balls, start=1):
            balls.append(parse_ball(as_dict(raw_ball), index, positions))
    else:
        balls = [parse_ball({}, 1, positions), parse_ball({}, 2, positions)]
    return Geometry(source=source, detector=detector, balls=tuple(balls))


def parse_ball(block: JsonDict, index: int, positions: dict[str, float]) -> BallSpec:
    default_y = 289.0 if index == 1 else 989.0
    return BallSpec(
        name=str(block.get("name") or f"ball_{index}"),
        x_um=finite_float(
            first_present(block, "machine_z_um", "x_um", "sim_x_um", default=positions.get(f"Align_Z{index}", 0.0)),
            f"balls[{index}].sim_x_um",
        ),
        z_um=finite_float(
            first_present(
                block,
                "machine_y_um",
                "z_um",
                "sim_y_um",
                "transverse_z_um",
                default=positions.get(f"Align_Y{index}", 0.0),
            ),
            f"balls[{index}].sim_y_um",
        ),
        y_um=finite_float(
            first_present(
                block,
                "machine_x_um",
                "optical_y_um",
                "position_um",
                "sim_z_um",
                default=positions.get(f"Align_X{index}", default_y),
            ),
            f"balls[{index}].sim_z_um",
        ),
        diameter_um=positive_float(
            first_present(block, "diameter_um", "diameter", default=DEFAULT_BALL_DIAMETER_UM),
            f"balls[{index}].diameter_um",
        ),
        refractive_index=positive_float(
            first_present(block, "refractive_index", "n", default=DEFAULT_BALL_REFRACTIVE_INDEX),
            f"balls[{index}].refractive_index",
        ),
        stage_index=index,
    )


def positions_um(params: JsonDict, *, required: bool = True) -> dict[str, float]:
    machine = as_dict(params.get("machine"))
    raw_positions = as_dict(params.get("positions_um") or machine.get("positions_um"))
    positions: dict[str, float] = {}
    for stage, value in raw_positions.items():
        positions[str(stage)] = finite_float(value, f"positions_um.{stage}")
    if required and not positions:
        raise ValueError("machine.positions_um must contain current stage coordinates")
    return positions


def current_poses_for_balls(balls: tuple[BallSpec, ...], positions: dict[str, float]) -> tuple[Pose, ...]:
    poses: list[Pose] = []
    for ball in balls:
        index = ball.stage_index
        poses.append(
            (
                positions.get(f"Align_Z{index}", ball.x_um),
                positions.get(f"Align_Y{index}", ball.z_um),
                positions.get(f"Align_X{index}", ball.y_um),
            )
        )
    return tuple(poses)


def stages_from_poses(poses: tuple[Pose, ...]) -> dict[str, float]:
    result: dict[str, float] = {}
    for index, pose in enumerate(poses, start=1):
        result[f"Align_Z{index}"] = pose[0]
        result[f"Align_Y{index}"] = pose[1]
        result[f"Align_X{index}"] = pose[2]
    return result


def pose_stage_value(pose: Pose, axis: str) -> float:
    return pose[MACHINE_AXIS_TO_POSE_INDEX[axis]]


def pose_with_stage_value(pose: Pose, axis: str, value: float) -> Pose:
    values = list(pose)
    values[MACHINE_AXIS_TO_POSE_INDEX[axis]] = value
    return (values[0], values[1], values[2])


def stage_name(axis: str, lens_index: int) -> str:
    return f"Align_{axis}{lens_index}"


def clearance_axis_for(params: JsonDict) -> str:
    staging = as_dict(params.get("staging"))
    raw = first_present(staging, "clearance_stage_axis", "clearance_axis", default=DEFAULT_CLEARANCE_AXIS)
    axis = str(raw).strip()
    if axis.startswith("Align_"):
        axis = axis.removeprefix("Align_")[:1]
    if axis.lower().startswith("machine_"):
        axis = axis.split("_", maxsplit=1)[1][:1]
    axis = axis.upper()
    if axis not in MACHINE_AXIS_TO_POSE_INDEX:
        raise ValueError("staging.clearance_stage_axis must be one of X, Y, Z, Align_X, Align_Y, Align_Z")
    return axis


def clearance_axis_from_state(state: JsonDict) -> str:
    raw = str(state.get("clearance_stage_axis") or DEFAULT_CLEARANCE_AXIS)
    if raw.startswith("Align_"):
        raw = raw.removeprefix("Align_")[:1]
    raw = raw.replace("*", "").upper()
    if raw not in MACHINE_AXIS_TO_POSE_INDEX:
        return DEFAULT_CLEARANCE_AXIS
    return raw


def remaining_planned_moves(planned_moves: list[Any], current_positions: dict[str, float], tolerance: float) -> list[JsonDict]:
    remaining: list[JsonDict] = []
    for raw_move in planned_moves:
        move = as_dict(raw_move)
        stage = str(move.get("stage") or "")
        if not stage:
            continue
        target_um = finite_float(move.get("target_um", move.get("distance_um", 0.0)), f"state.planned_moves.{stage}.target_um")
        current_um = current_positions.get(stage)
        if current_um is not None and abs(current_um - target_um) <= tolerance:
            continue
        updated = dict(move)
        updated["target_um"] = target_um
        updated["distance_um"] = target_um
        if current_um is not None:
            delta_um = target_um - current_um
            updated["delta_um"] = delta_um
            updated["confirm_text"] = confirm_text(
                stage,
                current_um,
                target_um,
                delta_um,
                str(move.get("phase") or "staged_move"),
            )
        remaining.append(updated)
    return remaining


def angle_value(block: JsonDict, axis: str) -> float:
    mrad_key = f"{axis}_angle_mrad"
    machine_mrad_key = f"machine_{axis}_angle_mrad"
    rad_key = f"{axis}_angle_rad"
    loose_key = f"{axis}_angle"
    if machine_mrad_key in block:
        return finite_float(block[machine_mrad_key], machine_mrad_key) * 1.0e-3
    if mrad_key in block:
        return finite_float(block[mrad_key], mrad_key) * 1.0e-3
    if rad_key in block:
        return finite_float(block[rad_key], rad_key)
    return finite_float(block.get(loose_key, 0.0), loose_key)


def output_state(
    target_positions: dict[str, float],
    planned_moves: list[JsonDict],
    zones: tuple[NoGoZone, ...],
    clearance_axis: str,
    safe_clearance_um: float,
) -> JsonDict:
    return {
        "algorithm": "fixed_z_staged_ball_placement",
        "target_positions_um": target_positions,
        "planned_moves": planned_moves,
        "planned_move_count": len(planned_moves),
        "remaining_move_count": len(planned_moves),
        "clearance_stage_axis": f"Align_{clearance_axis}*",
        "safe_clearance_um": safe_clearance_um,
        "axis_mapping": axis_mapping_json(),
        "no_go_zones_um": [zone_to_json(zone) for zone in zones],
    }


def move_response(move: JsonDict, planned_moves: list[JsonDict], message: str, state: JsonDict) -> JsonDict:
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "move",
        "move_count": 1,
        "stage1": move["stage"],
        "distance1_um": move["distance_um"],
        "target1_um": move["target_um"],
        "delta1_um": move["delta_um"],
        "move_mode1": "Absolute",
        "phase1": move["phase"],
        "confirm_text1": move["confirm_text"],
        "moves": [move],
        "planned_moves": planned_moves,
        "message": message,
        "state": state,
    }
    add_flat_target_fields(result, state)
    return result


def done_response(message: str, state: JsonDict | None = None) -> JsonDict:
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "done",
        "move_count": 0,
        "stage1": "",
        "distance1_um": 0.0,
        "target1_um": 0.0,
        "delta1_um": 0.0,
        "move_mode1": "Absolute",
        "moves": [],
        "planned_moves": [],
        "message": message,
    }
    if state is not None:
        result["state"] = state
        add_flat_target_fields(result, state)
    return result


def abort_response(message: str, state: JsonDict | None = None) -> JsonDict:
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "abort",
        "move_count": 0,
        "stage1": "",
        "distance1_um": 0.0,
        "target1_um": 0.0,
        "delta1_um": 0.0,
        "move_mode1": "Absolute",
        "moves": [],
        "planned_moves": [],
        "message": message,
    }
    if state is not None:
        result["state"] = state
        add_flat_target_fields(result, state)
    return result


def add_flat_target_fields(result: JsonDict, state: JsonDict) -> None:
    targets = as_dict(state.get("target_positions_um"))
    for stage in STAGE_ORDER:
        if stage in targets:
            result[f"target_{stage}_um"] = targets[stage]


def zone_to_json(zone: NoGoZone) -> JsonDict:
    return {
        "name": zone.name,
        "machine_x_min_um": zone.y_min_um,
        "machine_x_max_um": zone.y_max_um,
        "machine_z_min_um": zone.x_min_um,
        "machine_z_max_um": zone.x_max_um,
        "machine_y_min_um": zone.z_min_um,
        "machine_y_max_um": zone.z_max_um,
        "label": zone.label,
    }


def axis_mapping_json() -> JsonDict:
    return {
        "machine_x": "simulation_z_optical_axis",
        "machine_z": "simulation_x",
        "machine_y": "simulation_y",
        "stage_axes": MACHINE_AXIS_LABELS,
    }


def confirm_text(stage: str, start_um: float, target_um: float, delta_um: float, phase: str) -> str:
    return (
        f"{phase}: confirm {stage} absolute move from {start_um:.6g} um "
        f"to {target_um:.6g} um (delta {delta_um:.6g} um)."
    )


def require_schema(params: JsonDict) -> None:
    version = params.get("schema_version")
    if int(version or 0) != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}")


def as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def first_present(block: JsonDict, *names: str, default: Any = None) -> Any:
    for name in names:
        if name in block:
            return block[name]
    return default


def finite_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def optional_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    return finite_float(value, name)


def positive_float(value: Any, name: str) -> float:
    result = finite_float(value, name)
    if result <= 0.0:
        raise ValueError(f"{name} must be positive")
    return result


def tolerance_um(params: JsonDict) -> float:
    value = as_dict(params.get("algorithm")).get("tolerance_um", DEFAULT_TOLERANCE_UM)
    return positive_float(value, "tolerance_um")


def no_go_clearance_um(params: JsonDict) -> float:
    value = as_dict(params.get("algorithm")).get("no_go_clearance_um", DEFAULT_NO_GO_CLEARANCE_UM)
    return positive_float(value, "no_go_clearance_um")


def max_single_move_um(params: JsonDict) -> float:
    limits = as_dict(params.get("limits"))
    algorithm = as_dict(params.get("algorithm"))
    value = first_present(algorithm, "max_single_move_um", default=limits.get("max_single_move_um", DEFAULT_MAX_SINGLE_MOVE_UM))
    return positive_float(value, "max_single_move_um")


if __name__ == "__main__":  # pragma: no cover - local manual smoke helper
    import json
    import sys

    payload = json.load(sys.stdin)
    print(json.dumps(solve_fixed_z_staged_ball_placement(payload), indent=2, sort_keys=True))
