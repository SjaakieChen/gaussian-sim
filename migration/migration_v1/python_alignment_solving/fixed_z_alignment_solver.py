"""Fixed-Z theoretical two-ball alignment solver for TMPython.

This file is intentionally self-contained for the migration v1 bundle.  It
mirrors the simulation's fixed-Z transverse solve: with the ball optical-axis
positions held fixed, solve the two ball-lens transverse offsets that put the
beam on the fiber/taper center with zero output angle.

Legacy coordinate and unit convention:

* all input and output distances are micrometres;
* this v1 solver predates the corrected universal machine mapping;
* do not use v1 as the coordinate reference for machine motion;
* current machine motion should use migration_v2, where Align_X maps to
  simulation z, Align_Z maps to simulation x, and Align_Y maps to simulation y.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - used on developer machines without TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback so the solver can be tested outside TestMaster."""


SCHEMA_VERSION = 1
DEFAULT_BALL_DIAMETER_UM = 500.0
DEFAULT_BALL_REFRACTIVE_INDEX = 1.760
DEFAULT_RESPONSE_STEP_UM = 1.0
DEFAULT_MAX_STEP_UM = 2.0
DEFAULT_TOLERANCE_UM = 0.05
DEFAULT_NO_GO_CLEARANCE_UM = 5.0
DEFAULT_AXIS_ORDER = ("Align_X1", "Align_Z1", "Align_X2", "Align_Z2")
DEFAULT_ALLOWED_STAGES = DEFAULT_AXIS_ORDER
DEFAULT_LASER_NO_GO_Z_MAX_UM = 250.0
DEFAULT_TAPER_NO_GO_Z_MAX_UM = 250.0
DEFAULT_TRENCH_FLOOR_Z_MAX_UM = -500.0
DEFAULT_WORKSPACE_Y_MARGIN_UM = 2000.0
EPS = 1.0e-9


JsonDict = dict[str, Any]
Pose = tuple[float, float, float]  # legacy v1 order: machine X, machine Z, machine Y
Move = tuple[str, float]


@dataclass(frozen=True)
class SourceSpec:
    x_um: float
    z_um: float
    optical_y_um: float
    x_angle: float
    z_angle: float


@dataclass(frozen=True)
class FiberSpec:
    x_um: float
    z_um: float
    optical_y_um: float


@dataclass(frozen=True)
class BallSpec:
    name: str
    x_um: float
    z_um: float
    optical_y_um: float
    diameter_um: float
    refractive_index: float
    stage_index: int

    @property
    def radius_um(self) -> float:
        return 0.5 * self.diameter_um

    @property
    def entry_y_um(self) -> float:
        return self.optical_y_um - self.radius_um

    @property
    def exit_y_um(self) -> float:
        return self.optical_y_um + self.radius_um

    @property
    def pose(self) -> Pose:
        return (self.x_um, self.z_um, self.optical_y_um)


@dataclass(frozen=True)
class NoGoZone:
    name: str
    optical_y_min_um: float
    optical_y_max_um: float
    x_min_um: float | None = None
    x_max_um: float | None = None
    z_min_um: float | None = None
    z_max_um: float | None = None
    label: str = ""

    @property
    def y_low_um(self) -> float:
        return min(self.optical_y_min_um, self.optical_y_max_um)

    @property
    def y_high_um(self) -> float:
        return max(self.optical_y_min_um, self.optical_y_max_um)

    def intersects_ball_pose(self, pose: Pose, radius_um: float) -> bool:
        x_um, z_um, optical_y_um = pose
        entry_y = optical_y_um - radius_um
        exit_y = optical_y_um + radius_um
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
    fiber: FiberSpec
    balls: tuple[BallSpec, ...]


class FixedZAlignmentSolveStep(TMPythonStatementJ):
    """TMPython statement class returning fixed-Z theoretical target moves."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return solve_fixed_z_alignment(params_in)
        except Exception as exc:  # fail closed for the machine call
            return abort_response(f"FixedZAlignmentSolveStep failed: {exc}")


def solve_fixed_z_alignment(params_in: JsonDict) -> JsonDict:
    """Return target coordinates, a no-go-checked path, and the next move."""

    require_schema(params_in)
    geometry = parse_geometry(params_in)
    if len(geometry.balls) != 2:
        raise ValueError("fixed-Z solver currently supports exactly two ball lenses")

    current_positions = positions_um(params_in)
    current_poses = current_poses_for_balls(geometry.balls, current_positions)
    check_strict_axial_clearance(geometry.source, geometry.fiber, geometry.balls, current_poses)

    target_poses = solve_fixed_z_target_poses(geometry)
    check_strict_axial_clearance(geometry.source, geometry.fiber, geometry.balls, target_poses)

    zones = no_go_zones_for(params_in, geometry, current_poses)
    current_violations = no_go_violations(current_poses, geometry.balls, zones)
    if current_violations:
        return abort_response(
            "current ball pose intersects a no-go zone; refusing to plan motion",
            state={"violations": current_violations},
        )

    target_violations = no_go_violations(target_poses, geometry.balls, zones)
    if target_violations:
        return abort_response(
            "solved target pose intersects a no-go zone; refusing to plan motion",
            state={"violations": target_violations, "target_positions_um": stages_from_poses(target_poses)},
        )

    path = plan_no_go_checked_path(params_in, geometry, current_poses, target_poses, zones)
    target_positions = stages_from_poses(target_poses)

    state = dict(params_in.get("state") if isinstance(params_in.get("state"), dict) else {})
    path_index = normalized_path_index(state, path, current_positions)
    if path_index >= len(path):
        return done_response(
            "fixed_z_alignment_solve path is complete",
            state=output_state(target_positions, path, path_index, zones),
        )

    waypoint = path[path_index]
    moves = next_moves_to_waypoint(params_in, current_positions, waypoint)
    if not moves:
        path_index += 1
        if path_index >= len(path):
            return done_response(
                "fixed_z_alignment_solve path is complete",
                state=output_state(target_positions, path, path_index, zones),
            )
        waypoint = path[path_index]
        moves = next_moves_to_waypoint(params_in, current_positions, waypoint)

    if not moves:
        return done_response(
            "fixed_z_alignment_solve current position is inside waypoint tolerance",
            state=output_state(target_positions, path, path_index, zones),
        )

    return move_response(
        moves,
        f"fixed_z_alignment_solve moving toward no-go-checked waypoint {path_index + 1} of {len(path)}",
        state=output_state(target_positions, path, path_index, zones, waypoint),
    )


def solve_fixed_z_target_poses(geometry: Geometry) -> tuple[Pose, ...]:
    ordered_balls = sorted(geometry.balls, key=lambda ball: ball.optical_y_um)
    x_offsets = solve_axis_offsets(
        geometry.source.x_um,
        geometry.source.x_angle,
        geometry.fiber.x_um,
        geometry.source.optical_y_um,
        geometry.fiber.optical_y_um,
        ordered_balls,
        axis="x",
    )
    z_offsets = solve_axis_offsets(
        geometry.source.z_um,
        geometry.source.z_angle,
        geometry.fiber.z_um,
        geometry.source.optical_y_um,
        geometry.fiber.optical_y_um,
        ordered_balls,
        axis="z",
    )

    ordered_targets: dict[int, Pose] = {}
    for ball, x_um, z_um in zip(ordered_balls, x_offsets, z_offsets):
        ordered_targets[ball.stage_index] = (x_um, z_um, ball.optical_y_um)
    return tuple(ordered_targets[index] for index in sorted(ordered_targets))


def solve_axis_offsets(
    source_offset_um: float,
    source_angle: float,
    fiber_offset_um: float,
    source_y_um: float,
    fiber_y_um: float,
    ordered_balls: list[BallSpec],
    *,
    axis: str,
) -> tuple[float, float]:
    base = axis_state(
        source_offset_um,
        source_angle,
        source_y_um,
        fiber_y_um,
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
            fiber_y_um,
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

    target = (fiber_offset_um - base[0], 0.0 - base[1])
    return solve_2x2_columns(columns[0], columns[1], target)


def axis_state(
    source_offset_um: float,
    source_angle: float,
    source_y_um: float,
    fiber_y_um: float,
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

    if fiber_y_um < y_um - EPS:
        raise ValueError("fiber/taper is before the final ball lens")
    offset_um += max(fiber_y_um - y_um, 0.0) * angle
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


def parse_geometry(params: JsonDict) -> Geometry:
    geometry = as_dict(params.get("geometry_um") or params.get("geometry"))
    positions = positions_um(params, required=False)

    source_block = as_dict(geometry.get("laser") or geometry.get("source"))
    fiber_block = as_dict(geometry.get("fiber") or geometry.get("taper") or geometry.get("waveguide"))
    raw_balls = geometry.get("balls")

    source = SourceSpec(
        x_um=finite_float(first_present(source_block, "x_um", "machine_x_um", "sim_x_um", default=0.0), "laser.x_um"),
        z_um=finite_float(
            first_present(source_block, "z_um", "machine_z_um", "sim_y_um", "transverse_z_um", default=0.0),
            "laser.z_um",
        ),
        optical_y_um=finite_float(
            first_present(source_block, "optical_y_um", "machine_y_um", "position_um", "sim_z_um", default=0.0),
            "laser.optical_y_um",
        ),
        x_angle=angle_value(source_block, "x"),
        z_angle=angle_value(source_block, "z"),
    )

    fiber = FiberSpec(
        x_um=finite_float(first_present(fiber_block, "x_um", "machine_x_um", "sim_x_um", default=0.0), "fiber.x_um"),
        z_um=finite_float(
            first_present(fiber_block, "z_um", "machine_z_um", "sim_y_um", "transverse_z_um", default=0.0),
            "fiber.z_um",
        ),
        optical_y_um=finite_float(
            first_present(
                fiber_block,
                "optical_y_um",
                "machine_y_um",
                "position_um",
                "sim_z_um",
                default=1278.0,
            ),
            "fiber.optical_y_um",
        ),
    )

    balls: list[BallSpec] = []
    if isinstance(raw_balls, list) and raw_balls:
        for index, raw_ball in enumerate(raw_balls, start=1):
            balls.append(parse_ball(as_dict(raw_ball), index, positions))
    else:
        balls = [
            parse_ball({}, 1, positions),
            parse_ball({}, 2, positions),
        ]

    return Geometry(source=source, fiber=fiber, balls=tuple(balls))


def parse_ball(block: JsonDict, index: int, positions: dict[str, float]) -> BallSpec:
    default_y = 289.0 if index == 1 else 989.0
    return BallSpec(
        name=str(block.get("name") or f"ball_{index}"),
        x_um=finite_float(
            first_present(block, "x_um", "machine_x_um", "sim_x_um", default=positions.get(f"Align_X{index}", 0.0)),
            f"balls[{index}].x_um",
        ),
        z_um=finite_float(
            first_present(
                block,
                "z_um",
                "machine_z_um",
                "sim_y_um",
                "transverse_z_um",
                default=positions.get(f"Align_Z{index}", 0.0),
            ),
            f"balls[{index}].z_um",
        ),
        optical_y_um=finite_float(
            first_present(
                block,
                "optical_y_um",
                "machine_y_um",
                "position_um",
                "sim_z_um",
                default=positions.get(f"Align_Y{index}", default_y),
            ),
            f"balls[{index}].optical_y_um",
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


def angle_value(block: JsonDict, axis: str) -> float:
    mrad_key = f"{axis}_angle_mrad"
    rad_key = f"{axis}_angle_rad"
    loose_key = f"{axis}_angle"
    if mrad_key in block:
        return finite_float(block[mrad_key], mrad_key) * 1.0e-3
    if rad_key in block:
        return finite_float(block[rad_key], rad_key)
    return finite_float(block.get(loose_key, 0.0), loose_key)


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
                positions.get(f"Align_X{index}", ball.x_um),
                positions.get(f"Align_Z{index}", ball.z_um),
                positions.get(f"Align_Y{index}", ball.optical_y_um),
            )
        )
    return tuple(poses)


def check_strict_axial_clearance(
    source: SourceSpec,
    fiber: FiberSpec,
    balls: tuple[BallSpec, ...],
    poses: tuple[Pose, ...],
) -> None:
    ordered = sorted(zip(balls, poses), key=lambda item: item[1][2])
    previous_exit = source.optical_y_um
    for ball, pose in ordered:
        radius = ball.radius_um
        entry = pose[2] - radius
        exit_y = pose[2] + radius
        if entry <= previous_exit + EPS:
            raise ValueError("ball lenses/source/fiber do not have strict positive axial clearance")
        previous_exit = exit_y
    if fiber.optical_y_um <= previous_exit + EPS:
        raise ValueError("fiber/taper does not have strict positive axial clearance after final ball")


def no_go_zones_for(params: JsonDict, geometry: Geometry, current_poses: tuple[Pose, ...]) -> tuple[NoGoZone, ...]:
    limits = as_dict(params.get("limits"))
    algorithm = as_dict(params.get("algorithm"))
    zones: list[NoGoZone] = []
    if bool(algorithm.get("default_no_go_zones", True)):
        zones.extend(default_no_go_zones(geometry, current_poses))
    raw_zones = limits.get("no_go_zones_um") or algorithm.get("no_go_zones_um") or []
    if isinstance(raw_zones, list):
        zones.extend(parse_no_go_zone(as_dict(zone), index) for index, zone in enumerate(raw_zones, start=1))
    return tuple(zones)


def default_no_go_zones(geometry: Geometry, current_poses: tuple[Pose, ...]) -> list[NoGoZone]:
    source_y = geometry.source.optical_y_um
    fiber_y = geometry.fiber.optical_y_um
    _ = current_poses
    return [
        NoGoZone(
            name="laser_side_no_go",
            optical_y_min_um=min(source_y - DEFAULT_WORKSPACE_Y_MARGIN_UM, source_y),
            optical_y_max_um=source_y,
            z_max_um=DEFAULT_LASER_NO_GO_Z_MAX_UM,
            label="Laser no-go below +250 um machine Z",
        ),
        NoGoZone(
            name="trench_floor",
            optical_y_min_um=source_y,
            optical_y_max_um=fiber_y,
            z_max_um=DEFAULT_TRENCH_FLOOR_Z_MAX_UM,
            label="Trench floor below -500 um machine Z",
        ),
        NoGoZone(
            name="taper_side_no_go",
            optical_y_min_um=fiber_y,
            optical_y_max_um=fiber_y + DEFAULT_WORKSPACE_Y_MARGIN_UM,
            z_max_um=DEFAULT_TAPER_NO_GO_Z_MAX_UM,
            label="Taper no-go below +250 um machine Z",
        ),
    ]


def dynamic_vacuum_zones(balls: tuple[BallSpec, ...], poses: tuple[Pose, ...]) -> list[NoGoZone]:
    zones: list[NoGoZone] = []
    for ball, pose in zip(balls, poses):
        radius = ball.radius_um
        zones.append(
            NoGoZone(
                name=f"vacuum_tweezer_{ball.stage_index}",
                optical_y_min_um=pose[2] - radius,
                optical_y_max_um=pose[2] + radius,
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
        optical_y_min_um=finite_float(
            first_present(block, "optical_y_min_um", "machine_y_min_um", "y_min_um", default=0.0),
            "no_go_zone.optical_y_min_um",
        ),
        optical_y_max_um=finite_float(
            first_present(block, "optical_y_max_um", "machine_y_max_um", "y_max_um", default=0.0),
            "no_go_zone.optical_y_max_um",
        ),
        x_min_um=optional_float(first_present(block, "x_min_um", "machine_x_min_um", default=None), "no_go_zone.x_min_um"),
        x_max_um=optional_float(first_present(block, "x_max_um", "machine_x_max_um", default=None), "no_go_zone.x_max_um"),
        z_min_um=optional_float(
            first_present(block, "z_min_um", "machine_z_min_um", "transverse_z_min_um", default=None),
            "no_go_zone.z_min_um",
        ),
        z_max_um=optional_float(
            first_present(block, "z_max_um", "machine_z_max_um", "transverse_z_max_um", default=None),
            "no_go_zone.z_max_um",
        ),
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


def plan_no_go_checked_path(
    params: JsonDict,
    geometry: Geometry,
    current_poses: tuple[Pose, ...],
    target_poses: tuple[Pose, ...],
    zones: tuple[NoGoZone, ...],
) -> list[dict[str, float]]:
    poses = list(current_poses)
    waypoints: list[dict[str, float]] = []
    for lens_index, target_pose in enumerate(target_poses):
        start_pose = poses[lens_index]
        if pose_close(start_pose, target_pose):
            continue
        if segment_is_safe(params, geometry, tuple(poses), lens_index, target_pose, zones):
            poses[lens_index] = target_pose
            append_waypoint(waypoints, tuple(poses))
            continue

        detour = find_transverse_z_detour(params, geometry, tuple(poses), lens_index, target_pose, zones)
        if detour is None:
            raise ValueError(f"no no-go-safe fixed-Z path found for ball {lens_index + 1}")
        for waypoint_pose in detour:
            poses[lens_index] = waypoint_pose
            append_waypoint(waypoints, tuple(poses))

    if not waypoints:
        waypoints.append(stages_from_poses(target_poses))
    return waypoints


def find_transverse_z_detour(
    params: JsonDict,
    geometry: Geometry,
    poses: tuple[Pose, ...],
    lens_index: int,
    target_pose: Pose,
    zones: tuple[NoGoZone, ...],
) -> list[Pose] | None:
    start_pose = poses[lens_index]
    radius = geometry.balls[lens_index].radius_um
    clearance = no_go_clearance_um(params)
    candidates = {start_pose[1], target_pose[1]}
    for zone in zones:
        if not zone_intersects_optical_span(zone, target_pose[2], radius):
            continue
        if zone.z_max_um is not None:
            candidates.add(zone.z_max_um + radius + clearance)
        if zone.z_min_um is not None:
            candidates.add(zone.z_min_um - radius - clearance)

    ordered_candidates = sorted(candidates, key=lambda value: abs(value - start_pose[1]) + abs(value - target_pose[1]))
    for safe_z in ordered_candidates:
        waypoint_1 = (start_pose[0], safe_z, start_pose[2])
        waypoint_2 = (target_pose[0], safe_z, target_pose[2])
        waypoint_3 = target_pose
        trial_poses = poses
        ok = True
        for waypoint in (waypoint_1, waypoint_2, waypoint_3):
            if not segment_is_safe(params, geometry, trial_poses, lens_index, waypoint, zones):
                ok = False
                break
            trial_list = list(trial_poses)
            trial_list[lens_index] = waypoint
            trial_poses = tuple(trial_list)
        if ok:
            return [waypoint_1, waypoint_2, waypoint_3]
    return None


def segment_is_safe(
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
        if not poses_are_safe(params, geometry, tuple(trial_poses), zones):
            return False
    return True


def poses_are_safe(
    params: JsonDict,
    geometry: Geometry,
    poses: tuple[Pose, ...],
    zones: tuple[NoGoZone, ...],
) -> bool:
    try:
        check_strict_axial_clearance(geometry.source, geometry.fiber, geometry.balls, poses)
    except ValueError:
        return False
    if no_go_violations(poses, geometry.balls, zones):
        return False
    return within_axis_limits(params, poses)


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


def append_waypoint(waypoints: list[dict[str, float]], poses: tuple[Pose, ...]) -> None:
    waypoint = stages_from_poses(poses)
    if not waypoints or any(abs(waypoints[-1][key] - value) > DEFAULT_TOLERANCE_UM for key, value in waypoint.items()):
        waypoints.append(waypoint)


def stages_from_poses(poses: tuple[Pose, ...]) -> dict[str, float]:
    result: dict[str, float] = {}
    for index, pose in enumerate(poses, start=1):
        result[f"Align_X{index}"] = pose[0]
        result[f"Align_Z{index}"] = pose[1]
        result[f"Align_Y{index}"] = pose[2]
    return result


def normalized_path_index(state: JsonDict, path: list[dict[str, float]], current_positions: dict[str, float]) -> int:
    raw = state.get("path_index", 0)
    try:
        path_index = int(raw)
    except (TypeError, ValueError):
        path_index = 0
    path_index = max(0, min(path_index, len(path)))
    while path_index < len(path) and target_reached(current_positions, path[path_index]):
        path_index += 1
    return path_index


def next_moves_to_waypoint(params: JsonDict, positions: dict[str, float], waypoint: dict[str, float]) -> list[Move]:
    tolerance = tolerance_um(params)
    max_step = max_step_um(params)
    allowed = allowed_stages(params)
    max_moves = max_moves_per_call(params)
    stage_order = stage_order_for(params)
    moves: list[Move] = []
    for stage in stage_order:
        if stage not in waypoint:
            continue
        if allowed and stage not in allowed:
            continue
        delta = waypoint[stage] - positions.get(stage, waypoint[stage])
        if abs(delta) <= tolerance:
            continue
        moves.append((stage, clip(delta, max_step)))
        if len(moves) >= max_moves:
            break
    return moves


def target_reached(positions: dict[str, float], target: dict[str, float]) -> bool:
    tolerance = DEFAULT_TOLERANCE_UM
    return all(abs(target[stage] - positions.get(stage, target[stage])) <= tolerance for stage in DEFAULT_AXIS_ORDER)


def output_state(
    target_positions: dict[str, float],
    path: list[dict[str, float]],
    path_index: int,
    zones: tuple[NoGoZone, ...],
    waypoint: dict[str, float] | None = None,
) -> JsonDict:
    state: JsonDict = {
        "algorithm": "fixed_z_alignment_solve",
        "path_index": path_index,
        "target_positions_um": target_positions,
        "path_um": path,
        "no_go_zones_um": [zone_to_json(zone) for zone in zones],
    }
    if waypoint is not None:
        state["waypoint_um"] = waypoint
    return state


def move_response(moves: list[Move], message: str, state: JsonDict) -> JsonDict:
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "move",
        "move_count": len(moves),
        "stage1": moves[0][0],
        "distance1_um": moves[0][1],
        "moves": [{"stage": stage, "distance_um": distance, "mode": "relative"} for stage, distance in moves],
        "message": message,
        "state": state,
    }
    if len(moves) > 1:
        result["stage2"] = moves[1][0]
        result["distance2_um"] = moves[1][1]
    add_flat_target_fields(result, state)
    return result


def done_response(message: str, state: JsonDict | None = None) -> JsonDict:
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "done",
        "move_count": 0,
        "stage1": "",
        "distance1_um": 0.0,
        "moves": [],
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
        "moves": [],
        "message": message,
    }
    if state is not None:
        result["state"] = state
        add_flat_target_fields(result, state)
    return result


def add_flat_target_fields(result: JsonDict, state: JsonDict) -> None:
    targets = as_dict(state.get("target_positions_um"))
    waypoint = as_dict(state.get("waypoint_um"))
    for stage in ("Align_X1", "Align_Z1", "Align_Y1", "Align_X2", "Align_Z2", "Align_Y2"):
        if stage in targets:
            result[f"target_{stage}_um"] = targets[stage]
        if stage in waypoint:
            result[f"waypoint_{stage}_um"] = waypoint[stage]


def zone_to_json(zone: NoGoZone) -> JsonDict:
    return {
        "name": zone.name,
        "optical_y_min_um": zone.optical_y_min_um,
        "optical_y_max_um": zone.optical_y_max_um,
        "x_min_um": zone.x_min_um,
        "x_max_um": zone.x_max_um,
        "z_min_um": zone.z_min_um,
        "z_max_um": zone.z_max_um,
        "label": zone.label,
    }


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


def max_step_um(params: JsonDict) -> float:
    algorithm = as_dict(params.get("algorithm"))
    limits = as_dict(params.get("limits"))
    value = algorithm.get("max_step_um", limits.get("max_step_um", DEFAULT_MAX_STEP_UM))
    return positive_float(value, "max_step_um")


def tolerance_um(params: JsonDict) -> float:
    value = as_dict(params.get("algorithm")).get("tolerance_um", DEFAULT_TOLERANCE_UM)
    return positive_float(value, "tolerance_um")


def no_go_clearance_um(params: JsonDict) -> float:
    value = as_dict(params.get("algorithm")).get("no_go_clearance_um", DEFAULT_NO_GO_CLEARANCE_UM)
    return positive_float(value, "no_go_clearance_um")


def max_moves_per_call(params: JsonDict) -> int:
    value = as_dict(params.get("algorithm")).get("max_moves_per_call", 1)
    try:
        count = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, min(2, count))


def allowed_stages(params: JsonDict) -> set[str]:
    raw = as_dict(params.get("limits")).get("allowed_stages")
    if raw is None:
        return set(DEFAULT_ALLOWED_STAGES)
    return {str(stage) for stage in raw}


def stage_order_for(params: JsonDict) -> tuple[str, ...]:
    raw = as_dict(params.get("algorithm")).get("stage_order")
    if raw is None:
        return DEFAULT_AXIS_ORDER
    stages = tuple(str(stage) for stage in raw if str(stage))
    return stages or DEFAULT_AXIS_ORDER


def clip(value: float, max_abs: float) -> float:
    if abs(value) <= max_abs:
        return value
    return math.copysign(max_abs, value)


def pose_close(a: Pose, b: Pose) -> bool:
    return all(abs(left - right) <= DEFAULT_TOLERANCE_UM for left, right in zip(a, b))


def zone_intersects_optical_span(zone: NoGoZone, optical_y_um: float, radius_um: float) -> bool:
    return optical_y_um + radius_um > zone.y_low_um and optical_y_um - radius_um < zone.y_high_um


if __name__ == "__main__":  # pragma: no cover - local manual smoke helper
    import json
    import sys

    payload = json.load(sys.stdin)
    print(json.dumps(solve_fixed_z_alignment(payload), indent=2, sort_keys=True))
