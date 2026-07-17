"""Interactive alignment sandbox with seeded source/detector height errors.

Run:

    python "alignment lab\alignment_lab.py"

This app reuses the existing ball-lens/taper simulator but starts with a
deterministic random height error on the laser source and taper detector. It is
intended as a manual and future automated optical-alignment test bench.
"""

from __future__ import annotations

import math
import random
import sys
import tkinter as tk
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk

from alignment_algorithms import (
    AlignmentAlgorithm,
    AlignmentAlgorithmResult,
    AlignmentModelGeometry,
    AlignmentMove,
    BallLensNoGoZone,
    BallLensGeometry,
    LensPose,
    PowerReading,
    SourceGeometry,
    TaperGeometry,
    available_algorithms,
    get_algorithm,
)
from alignment_algorithms.blind_power_j import (
    BLIND_POWER_J_ATTEMPTS,
    BLIND_POWER_J_MAX_CORRECTION,
    BLIND_POWER_J_SAMPLES_PER_POINT,
    BLIND_POWER_J_STEPS,
    BlindPowerJAlgorithm,
    DIRECTION_METHOD_BEST_OF_9,
    DIRECTION_METHOD_GRADIENT,
    DIRECTION_METHOD_NEWTON,
)
from alignment_algorithms.blind_power_j_best_of_9 import BlindPowerJBestOf9Algorithm
from alignment_algorithms.blind_power_j_gradient import BlindPowerJGradientAlgorithm
from alignment_algorithms.blind_power_j_newton import BlindPowerJNewtonAlgorithm
from interactive_setup import (
    AXIAL_TOLERANCE,
    DEFAULT_CLIPPING_RADIUS_FACTOR,
    DEFAULT_REFRACTIVE_INDEX,
    LASER_FIBRE_TRANSVERSE_TOLERANCE,
    OpticalLayoutEditor,
    TRANSVERSE_TOLERANCE,
    format_simulation_report,
    simulate_layout,
)

VISION_RECOGNITION_LAB_ROOT = Path(__file__).resolve().parents[1] / "vision recognition lab"
if VISION_RECOGNITION_LAB_ROOT.exists():
    sys.path.insert(0, str(VISION_RECOGNITION_LAB_ROOT))

from vision_recognition_lab import VisionRecognitionLab


DEFAULT_ALIGNMENT_SEED = 42
DEFAULT_INITIAL_BALL_X_OFFSET = 300e-6
DEFAULT_INITIAL_BALL_Y_OFFSET = 200e-6
# Max |offset| from nominal perfect alignment for source and taper x/y (metres).
DEFAULT_SOURCE_DETECTOR_TOLERANCE = 5.0e-6
# Max |offset| from nominal perfect alignment per lens x/y/z axis (metres).
DEFAULT_LENS_POSE_TOLERANCE = 2.0e-6
DEFAULT_HEIGHT_TOLERANCE = DEFAULT_SOURCE_DETECTOR_TOLERANCE
DEFAULT_ALGORITHM_SHOW_DELAY_MS = 180
DEFAULT_POWER_NOISE_PERCENT = 0.0
POWER_NOISE_DELTA_TOLERANCE = 1.0e-18
POWER_NOISE_DELTA_ADDED_COLOR = "#2e7d32"
POWER_NOISE_DELTA_SUBTRACTED_COLOR = "#c62828"
BLIND_DIRECTION_NEWTON_COLOR = "#0b3d91"
BLIND_DIRECTION_GRADIENT_COLOR = "#e65100"
BLIND_DIRECTION_BEST_OF_9_COLOR = "#6a1b9a"
BLIND_DIRECTION_IDLE_COLOR = "#666666"
VISION_SCRIPT_FINE_SCRAMBLE = 1.0e-6
VISION_SCRIPT_COARSE_SCRAMBLE = 5.0e-6
DEFAULT_LASER_NO_GO_Y_MAX = 250e-6
DEFAULT_TAPER_NO_GO_Y_MAX = 250e-6
DEFAULT_TAPER_TRENCH_Y_MAX = -500e-6
DEFAULT_ALIGNMENT_VIEW_Z_MARGIN = 250e-6
DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN = 2_000e-6
DEFAULT_ALIGNMENT_WORKSPACE_TRANSVERSE_LIMIT = 2_000e-6
DEFAULT_STARTING_POSITION_LASER_CLEARANCE = 200e-6
DEFAULT_STARTING_POSITION_BALL_GAP = 200e-6
LAB_ALGORITHM_NAMES = frozenset(
    (
        "blind_power_j",
        "blind_power_j_newton",
        "blind_power_j_gradient",
        "blind_power_j_best_of_9",
        "fixed_z_j_matrix",
        "position_solve",
        "position_solve_j_steps",
    )
)
BLIND_POWER_J_ALGORITHM_CLASSES = {
    "blind_power_j": BlindPowerJAlgorithm,
    "blind_power_j_newton": BlindPowerJNewtonAlgorithm,
    "blind_power_j_gradient": BlindPowerJGradientAlgorithm,
    "blind_power_j_best_of_9": BlindPowerJBestOf9Algorithm,
}
AxisTolerances = tuple[float, float, float]


def _is_blind_power_j_algorithm(name: str) -> bool:
    return name in BLIND_POWER_J_ALGORITHM_CLASSES


@dataclass(frozen=True)
class AlignmentEvaluation:
    source_x_offset: float
    source_y_offset: float
    taper_x_offset: float
    taper_y_offset: float
    ball_poses: tuple[LensPose, ...]
    ball_pose_offsets: tuple[LensPose, ...]
    received_power: float
    total_efficiency: float
    mode_efficiency: float
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class AlignmentScramble:
    source_x_offset: float
    source_y_offset: float
    source_z_offset: float
    taper_x_offset: float
    taper_y_offset: float
    taper_z_offset: float
    ball_pose_offsets: tuple[LensPose, ...]


@dataclass(frozen=True)
class AlignmentAlgorithmRun:
    algorithm: AlignmentAlgorithm
    result: AlignmentAlgorithmResult
    moves: tuple[AlignmentMove, ...]
    initial_poses: tuple[LensPose, ...]
    initial_snapshot: dict[str, object]


@dataclass(frozen=True)
class BallLensNoGoViolation:
    lens_index: int
    lens_name: str
    zone: BallLensNoGoZone

    @property
    def message(self) -> str:
        label = self.zone.label or self.zone.name
        return f"B{self.lens_index + 1} {self.lens_name} intersects {label}"


def alignment_no_go_zones_for_layout(
    source_z: float,
    taper_z: float,
    final_z: float,
    ball_poses: Sequence[LensPose],
    ball_radii: Sequence[float],
) -> tuple[BallLensNoGoZone, ...]:
    """Return stored no-go regions for ball-lens path planning and UI display."""

    if len(ball_poses) != len(ball_radii):
        raise ValueError("ball pose count must match ball radius count")
    if not ball_poses:
        return ()

    laser_z_min = min(source_z - DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN, source_z)
    taper_z_max = max(final_z, taper_z + DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN)
    zones = [
        BallLensNoGoZone(
            name="laser_side_no_go",
            z_min=laser_z_min,
            z_max=source_z,
            y_max=DEFAULT_LASER_NO_GO_Y_MAX,
            applies_to_all_x=True,
            label="Laser no-go below +250 um",
        ),
        BallLensNoGoZone(
            name="trench_floor",
            z_min=source_z,
            z_max=taper_z,
            y_max=DEFAULT_TAPER_TRENCH_Y_MAX,
            applies_to_all_x=True,
            label="Trench floor below -500 um",
        ),
        BallLensNoGoZone(
            name="taper_side_no_go",
            z_min=taper_z,
            z_max=taper_z_max,
            y_max=DEFAULT_TAPER_NO_GO_Y_MAX,
            applies_to_all_x=True,
            label="Taper no-go below +250 um",
        ),
    ]

    for index, (pose, radius) in enumerate(zip(ball_poses, ball_radii), start=1):
        x_offset, y_offset, z_position = pose
        y_min = y_offset + radius
        zones.append(
            BallLensNoGoZone(
                name=f"vacuum_tweezer_{index}",
                z_min=z_position - radius,
                z_max=z_position + radius,
                x_min=x_offset - radius,
                x_max=x_offset + radius,
                y_min=y_min,
                y_max=None,
                applies_to_all_x=True,
                label=f"Vacuum tweezer B{index}",
            )
        )

    return tuple(zones)


def ball_lens_no_go_violations(
    poses: Sequence[LensPose],
    radii: Sequence[float],
    zones: Sequence[BallLensNoGoZone],
    names: Sequence[str] | None = None,
) -> tuple[BallLensNoGoViolation, ...]:
    if len(poses) != len(radii):
        raise ValueError("pose count must match ball radius count")

    violations: list[BallLensNoGoViolation] = []
    for lens_index, (pose, radius) in enumerate(zip(poses, radii)):
        lens_name = names[lens_index] if names is not None and lens_index < len(names) else ""
        for zone in zones:
            if zone.intersects_ball_pose(pose, radius):
                violations.append(
                    BallLensNoGoViolation(
                        lens_index=lens_index,
                        lens_name=lens_name,
                        zone=zone,
                    )
                )
    return tuple(violations)


def outside_trench_starting_poses(
    current_poses: Sequence[LensPose],
    radii: Sequence[float],
    source_z: float,
) -> tuple[LensPose, ...]:
    if len(current_poses) != len(radii):
        raise ValueError("pose count must match ball radius count")
    if not current_poses:
        return ()

    staged_reversed: list[LensPose] = []
    next_exit_z = source_z - DEFAULT_STARTING_POSITION_LASER_CLEARANCE
    for _pose, radius in reversed(tuple(zip(current_poses, radii))):
        center_z = next_exit_z - radius
        staged_reversed.append(
            (DEFAULT_INITIAL_BALL_X_OFFSET, DEFAULT_INITIAL_BALL_Y_OFFSET, center_z)
        )
        next_exit_z = center_z - radius - DEFAULT_STARTING_POSITION_BALL_GAP
    return tuple(reversed(staged_reversed))


def format_no_go_warning(violations: Sequence[BallLensNoGoViolation]) -> str:
    if not violations:
        return ""
    messages = "; ".join(violation.message for violation in violations)
    return f"No-go warning: {messages}"


def _validate_tolerance(name: str, tolerance: float) -> None:
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError(f"{name} tolerance must be non-negative and finite")


def _default_blind_power_j_steps_um_text() -> str:
    return ", ".join(f"{step * 1e6:g}" for step in BLIND_POWER_J_STEPS)


def _default_um_text(value: float) -> str:
    return f"{value * 1e6:g}"


def _coerce_axis_tolerances(name: str, tolerances: float | Sequence[float]) -> AxisTolerances:
    if isinstance(tolerances, (int, float)):
        axis_tolerances = (float(tolerances), float(tolerances), float(tolerances))
    else:
        axis_tolerances = tuple(float(value) for value in tolerances)
    if len(axis_tolerances) != 3:
        raise ValueError(f"{name} tolerance must provide x, y, and z values")
    for axis, tolerance in zip(("x", "y", "z"), axis_tolerances):
        _validate_tolerance(f"{name} {axis}", tolerance)
    return axis_tolerances  # type: ignore[return-value]


def seeded_alignment_errors(
    seed: int,
    source_detector_tolerance: float | Sequence[float] = DEFAULT_SOURCE_DETECTOR_TOLERANCE,
    lens_pose_tolerance: float | Sequence[float] = DEFAULT_LENS_POSE_TOLERANCE,
    lens_count: int = 2,
    *,
    taper_tolerance: float | Sequence[float] | None = None,
    ball_pose_tolerances: Sequence[float | Sequence[float]] | None = None,
) -> AlignmentScramble:
    """Return reproducible misalignments relative to the nominal perfect layout."""

    source_tolerances = _coerce_axis_tolerances("source", source_detector_tolerance)
    taper_tolerances = _coerce_axis_tolerances(
        "taper",
        source_detector_tolerance if taper_tolerance is None else taper_tolerance,
    )
    default_lens_tolerances = _coerce_axis_tolerances("lens pose", lens_pose_tolerance)
    if lens_count < 0:
        raise ValueError("lens_count must be non-negative")
    if ball_pose_tolerances is None:
        ball_tolerances = (default_lens_tolerances,) * lens_count
    else:
        parsed_ball_tolerances = tuple(
            _coerce_axis_tolerances(f"ball {index + 1}", tolerances)
            for index, tolerances in enumerate(ball_pose_tolerances)
        )
        if len(parsed_ball_tolerances) < lens_count:
            parsed_ball_tolerances = parsed_ball_tolerances + (default_lens_tolerances,) * (
                lens_count - len(parsed_ball_tolerances)
            )
        ball_tolerances = parsed_ball_tolerances[:lens_count]

    rng = random.Random(int(seed))
    ball_pose_offsets = tuple(
        (
            rng.uniform(-x_tolerance, x_tolerance),
            rng.uniform(-y_tolerance, y_tolerance),
            rng.uniform(-z_tolerance, z_tolerance),
        )
        for x_tolerance, y_tolerance, z_tolerance in ball_tolerances
    )
    return AlignmentScramble(
        source_x_offset=rng.uniform(-source_tolerances[0], source_tolerances[0]),
        source_y_offset=rng.uniform(-source_tolerances[1], source_tolerances[1]),
        source_z_offset=rng.uniform(-source_tolerances[2], source_tolerances[2]),
        taper_x_offset=rng.uniform(-taper_tolerances[0], taper_tolerances[0]),
        taper_y_offset=rng.uniform(-taper_tolerances[1], taper_tolerances[1]),
        taper_z_offset=rng.uniform(-taper_tolerances[2], taper_tolerances[2]),
        ball_pose_offsets=ball_pose_offsets,
    )


def seeded_height_errors(seed: int, tolerance: float = DEFAULT_HEIGHT_TOLERANCE) -> tuple[float, float]:
    _validate_tolerance("height", tolerance)
    rng = random.Random(int(seed))
    return (
        rng.uniform(-tolerance, tolerance),
        rng.uniform(-tolerance, tolerance),
    )


class AlignmentLabDevice:
    """Step-based lens move/measure interface exposed to algorithms."""

    def __init__(self, app: "AlignmentLabEditor", update_display: bool = False) -> None:
        self._app = app
        self._update_display = update_display
        self._move_count = 0
        self._measurement_count = 0
        self._move_history: list[AlignmentMove] = []
        self._pending_direction_method: str | None = None
        self._power_noise_enabled = app.power_noise_enabled()
        self._power_noise_fraction = app.power_noise_fraction() if self._power_noise_enabled else 0.0

    def current_poses(self) -> tuple[LensPose, ...]:
        return self._app.current_poses()

    def starting_poses(self) -> tuple[LensPose, ...]:
        return self._app.starting_poses()

    def model_geometry(self) -> AlignmentModelGeometry:
        return self._app.model_geometry()

    def set_next_move_direction_method(self, direction_method: str | None) -> None:
        self._pending_direction_method = direction_method

    def measure(self) -> PowerReading:
        self._measurement_count += 1
        evaluation = (
            self._app._run_alignment_simulation(update_report=False)
            if self._update_display
            else self._app.evaluate_current_alignment()
        )
        return self._reading_from_evaluation(evaluation)

    def move_lens(
        self,
        lens_index: int,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
    ) -> PowerReading:
        if lens_index < 0 or lens_index >= len(self._app.balls):
            raise IndexError("lens_index is out of range")
        poses = list(self.current_poses())
        x_offset, y_offset, position = poses[lens_index]
        poses[lens_index] = (
            x_offset + float(dx),
            y_offset + float(dy),
            position + float(dz),
        )
        self._app._apply_lens_poses(tuple(poses))
        self._move_count += 1
        self._measurement_count += 1
        evaluation = (
            self._app._run_alignment_simulation(update_report=False)
            if self._update_display
            else self._app.evaluate_current_alignment()
        )
        reading = self._reading_from_evaluation(evaluation)
        committed_method = self._pending_direction_method
        self._move_history.append(
            AlignmentMove(
                lens_index=lens_index,
                dx=float(dx),
                dy=float(dy),
                dz=float(dz),
                poses=tuple(poses),
                reading=reading,
                direction_method=committed_method,
            )
        )
        if committed_method is not None:
            self._app._notify_blind_direction_method(committed_method)
        return reading

    def move_history(self) -> tuple[AlignmentMove, ...]:
        return tuple(self._move_history)

    def _reading_from_evaluation(self, evaluation: AlignmentEvaluation) -> PowerReading:
        received_power = evaluation.received_power
        total_efficiency = evaluation.total_efficiency
        noise_delta = 0.0
        if self._power_noise_enabled and self._power_noise_fraction > 0.0:
            max_coupled_power = self._app.max_coupled_power()
            noise_amplitude = self._power_noise_fraction * max_coupled_power
            noise_delta = self._app.next_power_noise(noise_amplitude)
            noisy_power = received_power + noise_delta
            received_power = min(max(noisy_power, 0.0), max_coupled_power)
            total_efficiency = received_power / max_coupled_power if max_coupled_power > 0.0 else 0.0
        return PowerReading(
            received_power=received_power,
            total_efficiency=total_efficiency,
            mode_efficiency=evaluation.mode_efficiency,
            move_count=self._move_count,
            measurement_count=self._measurement_count,
            noise_delta=noise_delta,
        )


class AlignmentLabEditor(OpticalLayoutEditor):
    """Alignment-oriented variant of the interactive layout editor."""

    def __init__(self, *, window_geometry: str = "1180x760") -> None:
        self.alignment_seed = DEFAULT_ALIGNMENT_SEED
        self.source_detector_tolerance: AxisTolerances = (
            DEFAULT_SOURCE_DETECTOR_TOLERANCE,
            DEFAULT_SOURCE_DETECTOR_TOLERANCE,
            0.0,
        )
        self.taper_seed_tolerance: AxisTolerances = self.source_detector_tolerance
        self.lens_pose_tolerance: AxisTolerances = (
            DEFAULT_LENS_POSE_TOLERANCE,
            DEFAULT_LENS_POSE_TOLERANCE,
            DEFAULT_LENS_POSE_TOLERANCE,
        )
        self.ball_seed_tolerances: tuple[AxisTolerances, ...] = ()
        self._nominal_ball_poses: tuple[LensPose, ...] = ()
        self._alignment_algorithms = available_algorithms()
        self._algorithm_label_to_name: dict[str, str] = {}
        self._alignment_initializing = True
        self._alignment_ui_ready = False
        self._algorithm_animation_after_id: str | None = None
        self._last_evaluation: AlignmentEvaluation | None = None
        self._best_evaluation: AlignmentEvaluation | None = None
        self._last_algorithm_run: AlignmentAlgorithmRun | None = None
        self._alignment_no_go_zones: tuple[BallLensNoGoZone, ...] = ()
        self._power_noise_rng = random.Random(DEFAULT_ALIGNMENT_SEED)
        self._blind_algorithm_running = False
        self._vision_recognition_lab: VisionRecognitionLab | None = None
        super().__init__(window_geometry=window_geometry)
        self.title("Optical Alignment Lab")
        self._capture_nominal_ball_poses()
        self._alignment_initializing = False
        self._apply_initial_ball_offsets()
        self._clear_simulation_overlay()
        self._fit_view_bounds_to_layout()
        self._refresh_tree()
        self.redraw()
        self._run_alignment_simulation(update_report=True)

    def _build_ui(self) -> None:
        super()._build_ui()
        self._hide_layout_editor_toolbar_buttons()
        self.tree.configure(height=4)
        self._build_scramble_tolerances_section()
        self._build_algorithm_parameters_section()
        self._set_parameters_panel_open(False)
        self._set_output_panel_open(False)
        self.rowconfigure(2, weight=0)

        panel = ttk.Frame(self, padding=(8, 0, 8, 8))
        panel.grid(row=2, column=0, sticky="ew")
        panel.columnconfigure(14, weight=1)

        ttk.Label(panel, text="Seed").grid(row=0, column=0, padx=(0, 4), sticky="w")
        self.seed_var = tk.StringVar(value=str(DEFAULT_ALIGNMENT_SEED))
        ttk.Entry(panel, textvariable=self.seed_var, width=8).grid(row=0, column=1, padx=(0, 10), sticky="w")

        self.return_to_start_button = ttk.Button(
            panel,
            text="Return to start",
            command=self._return_to_simulation_start,
        )
        self.return_to_start_button.grid(row=0, column=2, padx=(0, 14))
        self.vision_script_scramble_button = ttk.Button(
            self._toolbar,
            text="Vision script scramble",
            command=self._vision_script_scramble,
        )
        self.vision_script_scramble_button.grid(row=1, column=3, padx=(0, 6), pady=(6, 0))
        self.parameters_toggle_button.grid(row=1, column=4, padx=(8, 6), pady=(6, 0))
        self.output_toggle_button.grid(row=1, column=5, padx=(0, 6), pady=(6, 0))
        self.vision_recognition_lab_button = ttk.Button(
            self._toolbar,
            text="Vision recognition lab",
            command=self._open_vision_recognition_lab,
        )
        self.vision_recognition_lab_button.grid(row=1, column=6, padx=(0, 6), pady=(6, 0))

        self._algorithm_label_to_name = {
            algorithm.display_name: name
            for name, algorithm in self._alignment_algorithms.items()
            if name in LAB_ALGORITHM_NAMES
        }
        algorithm_labels = list(self._algorithm_label_to_name)
        default_algorithm = self._alignment_algorithms.get("blind_power_j") or next(
            iter(self._alignment_algorithms.values())
        )
        self.algorithm_var = tk.StringVar(value=default_algorithm.display_name)
        self.algorithm_status_var = tk.StringVar(value="Algorithm: idle")
        self.algorithm_direction_method_var = tk.StringVar(value="")

        ttk.Label(panel, text="Algorithm").grid(row=1, column=0, padx=(0, 4), sticky="w")
        algorithm_combobox = ttk.Combobox(
            panel,
            textvariable=self.algorithm_var,
            values=algorithm_labels,
            state="readonly",
            width=44,
        )
        algorithm_combobox.grid(row=1, column=1, columnspan=2, padx=(0, 8), sticky="w")
        algorithm_combobox.bind("<<ComboboxSelected>>", self._on_algorithm_selection_changed)
        ttk.Button(panel, text="Run algorithm", command=self._run_selected_algorithm).grid(
            row=1, column=3, padx=(0, 8), sticky="w"
        )
        ttk.Button(panel, text="Show", command=self._show_selected_algorithm).grid(
            row=1, column=4, padx=(0, 8), sticky="w"
        )
        self.power_noise_enabled_var = tk.BooleanVar(value=False)
        self.power_noise_percent_var = tk.StringVar(value=f"{DEFAULT_POWER_NOISE_PERCENT:.3g}")
        self.power_noise_enabled_check = ttk.Checkbutton(
            panel,
            text="Noise",
            variable=self.power_noise_enabled_var,
            command=self._update_noise_status,
        )
        self.power_noise_enabled_check.grid(row=1, column=5, padx=(0, 6), sticky="w")
        self.power_noise_percent_entry = ttk.Entry(panel, textvariable=self.power_noise_percent_var, width=7)
        self.power_noise_percent_entry.grid(row=1, column=6, padx=(0, 4), sticky="w")
        ttk.Label(panel, text="%").grid(row=1, column=7, padx=(0, 8), sticky="w")
        self.power_noise_status_var = tk.StringVar(value="OFF")
        ttk.Label(panel, textvariable=self.power_noise_status_var, font=("Segoe UI", 9, "bold")).grid(
            row=1, column=8, padx=(0, 12), sticky="w"
        )
        self.algorithm_direction_method_label = tk.Label(
            panel,
            textvariable=self.algorithm_direction_method_var,
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        )
        self.algorithm_direction_method_label.grid(row=1, column=9, columnspan=5, sticky="w", padx=(0, 8))

        self.received_power_var = tk.StringVar(value="Received power: n/a")
        self.total_efficiency_var = tk.StringVar(value="Coupling total: n/a")
        self.mode_efficiency_var = tk.StringVar(value="Mode match: n/a")
        self.power_percent_var = tk.StringVar(value="MODE MATCH: n/a")
        self.source_height_var = tk.StringVar(value="Source height error: n/a")
        self.detector_height_var = tk.StringVar(value="Detector height error: n/a")
        self.best_power_var = tk.StringVar(value="Best so far: n/a")
        self.best_offsets_var = tk.StringVar(value="Best ball offsets: n/a")
        self.no_go_warning_var = tk.StringVar(value="")

        self.lens_offsets_var = tk.StringVar(value="Ball pose errors: n/a")

        power_headline_frame = ttk.Frame(panel)
        power_headline_frame.grid(row=0, column=3, columnspan=3, padx=(0, 16), sticky="w")
        ttk.Label(
            power_headline_frame,
            textvariable=self.power_percent_var,
            font=("Segoe UI", 14, "bold"),
        ).grid(row=0, column=0, sticky="w")
        self.power_noise_delta_label = tk.Label(
            power_headline_frame,
            text="",
            font=("Segoe UI", 11, "bold"),
        )
        self.power_noise_delta_label.grid(row=0, column=1, padx=(6, 0), sticky="w")
        ttk.Label(panel, textvariable=self.source_height_var).grid(row=0, column=6, padx=(0, 12), sticky="w")
        ttk.Label(panel, textvariable=self.detector_height_var).grid(row=0, column=7, padx=(0, 12), sticky="w")

        metrics = ttk.Frame(panel)
        metrics.grid(row=2, column=0, columnspan=14, sticky="ew", pady=(4, 0))
        for column in range(3):
            metrics.columnconfigure(column, weight=1, uniform="alignment_metrics")
        metric_font = ("Segoe UI", 10, "bold")
        ttk.Label(metrics, textvariable=self.mode_efficiency_var, font=metric_font).grid(
            row=0, column=0, padx=(0, 12), sticky="w"
        )
        ttk.Label(metrics, textvariable=self.received_power_var, font=metric_font).grid(
            row=0, column=1, padx=(0, 12), sticky="w"
        )
        ttk.Label(metrics, textvariable=self.total_efficiency_var, font=metric_font).grid(
            row=0, column=2, sticky="w"
        )

        ttk.Label(panel, textvariable=self.lens_offsets_var).grid(row=3, column=0, columnspan=6, sticky="w")
        ttk.Label(panel, textvariable=self.best_power_var, font=("Segoe UI", 10, "bold")).grid(row=3, column=6, padx=(0, 12), sticky="w")
        ttk.Label(panel, textvariable=self.best_offsets_var).grid(row=3, column=7, columnspan=8, sticky="w")
        self.algorithm_status_label = ttk.Label(
            panel,
            textvariable=self.algorithm_status_var,
            anchor="w",
            wraplength=1400,
        )
        self.algorithm_status_label.grid(row=4, column=0, columnspan=15, sticky="ew", pady=(4, 0))
        self.no_go_warning_label = ttk.Label(
            panel,
            textvariable=self.no_go_warning_var,
            anchor="w",
            foreground="#c62828",
            wraplength=1400,
        )
        self.no_go_warning_label.grid(row=5, column=0, columnspan=15, sticky="ew", pady=(2, 0))

        self._alignment_ui_ready = True
        self._on_algorithm_selection_changed()

    def _hide_layout_editor_toolbar_buttons(self) -> None:
        for button_name in (
            "add_source",
            "add_ball",
            "add_taper",
            "edit_selected",
            "delete_selected",
            "zoom_out",
            "zoom_in",
        ):
            self._toolbar_buttons[button_name].grid_remove()

    def _open_vision_recognition_lab(self) -> None:
        if self._vision_recognition_lab is not None and self._vision_recognition_lab.winfo_exists():
            self._vision_recognition_lab.lift()
            self._vision_recognition_lab.focus_force()
            return
        self._vision_recognition_lab = VisionRecognitionLab(self)

    def _build_scramble_tolerances_section(self) -> None:
        self.scramble_tolerances_frame = ttk.LabelFrame(
            self.parameters_frame,
            text="Scramble tolerances",
            padding=(6, 4),
        )
        self.scramble_tolerances_frame.grid(row=1, column=0, sticky="ew", pady=(6, 0))

        source_default = DEFAULT_SOURCE_DETECTOR_TOLERANCE * 1e6
        lens_default = DEFAULT_LENS_POSE_TOLERANCE * 1e6
        transverse_default = TRANSVERSE_TOLERANCE * 1e6
        laser_fibre_default = LASER_FIBRE_TRANSVERSE_TOLERANCE * 1e6
        axial_default = AXIAL_TOLERANCE * 1e6
        fine_default = VISION_SCRIPT_FINE_SCRAMBLE * 1e6
        coarse_default = VISION_SCRIPT_COARSE_SCRAMBLE * 1e6

        self.seed_laser_tolerance_vars = self._axis_tolerance_vars(source_default, source_default, 0.0)
        self.seed_receiver_tolerance_vars = self._axis_tolerance_vars(source_default, source_default, 0.0)
        self.seed_ball_tolerance_vars = [
            self._axis_tolerance_vars(lens_default, lens_default, lens_default)
            for _ball in self.balls
        ]
        self.vision_laser_tolerance_vars = self._axis_tolerance_vars(fine_default, fine_default, coarse_default)
        self.vision_receiver_tolerance_vars = self._axis_tolerance_vars(fine_default, coarse_default, fine_default)
        self.vision_ball_tolerance_vars = [
            self._axis_tolerance_vars(0.0, 0.0, 0.0)
            for _ball in self.balls
        ]
        self.laser_fibre_laser_tolerance_vars = self._axis_tolerance_vars(
            laser_fibre_default,
            laser_fibre_default,
            0.0,
        )
        self.laser_fibre_receiver_tolerance_vars = self._axis_tolerance_vars(
            laser_fibre_default,
            laser_fibre_default,
            0.0,
        )
        self.laser_fibre_ball_tolerance_vars = [
            self._axis_tolerance_vars(0.0, 0.0, 0.0)
            for _ball in self.balls
        ]
        self.full_scramble_laser_tolerance_vars = self._axis_tolerance_vars(
            transverse_default,
            transverse_default,
            0.0,
        )
        self.full_scramble_receiver_tolerance_vars = self._axis_tolerance_vars(
            transverse_default,
            transverse_default,
            0.0,
        )
        self.full_scramble_ball_tolerance_vars = [
            self._axis_tolerance_vars(transverse_default, transverse_default, axial_default)
            for _ball in self.balls
        ]

        self.source_detector_tolerance_var = self.seed_laser_tolerance_vars[0]
        self.lens_tolerance_var = self.seed_ball_tolerance_vars[0][0] if self.seed_ball_tolerance_vars else tk.StringVar(value=f"{lens_default:.3g}")
        self.vision_laser_xy_tolerance_var = self.vision_laser_tolerance_vars[0]
        self.vision_laser_z_tolerance_var = self.vision_laser_tolerance_vars[2]
        self.vision_receiver_xz_tolerance_var = self.vision_receiver_tolerance_vars[0]
        self.vision_receiver_y_tolerance_var = self.vision_receiver_tolerance_vars[1]
        self.laser_fibre_scramble_xy_tolerance_var = self.laser_fibre_laser_tolerance_vars[0]
        self.full_scramble_lens_xy_tolerance_var = self.full_scramble_ball_tolerance_vars[0][0] if self.full_scramble_ball_tolerance_vars else tk.StringVar(value=f"{transverse_default:.3g}")
        self.full_scramble_lens_z_tolerance_var = self.full_scramble_ball_tolerance_vars[0][2] if self.full_scramble_ball_tolerance_vars else tk.StringVar(value=f"{axial_default:.3g}")
        self.full_scramble_source_fibre_taper_xy_tolerance_var = self.full_scramble_laser_tolerance_vars[0]

        self.scramble_tolerance_sections = {}
        self._build_scramble_tolerance_table(
            "Seed scramble",
            (
                ("Laser", self.seed_laser_tolerance_vars),
                ("Receiver", self.seed_receiver_tolerance_vars),
                *self._ball_tolerance_rows(self.seed_ball_tolerance_vars),
            ),
            row=0,
        )
        self._build_scramble_tolerance_table(
            "Vision script scramble",
            (
                ("Laser", self.vision_laser_tolerance_vars),
                ("Receiver", self.vision_receiver_tolerance_vars),
                *self._ball_tolerance_rows(self.vision_ball_tolerance_vars),
            ),
            row=1,
        )
        self._build_scramble_tolerance_table(
            "Scramble laser/fibre",
            (
                ("Laser", self.laser_fibre_laser_tolerance_vars),
                ("Receiver", self.laser_fibre_receiver_tolerance_vars),
                *self._ball_tolerance_rows(self.laser_fibre_ball_tolerance_vars),
            ),
            row=2,
        )
        self._build_scramble_tolerance_table(
            "Full scramble",
            (
                ("Laser", self.full_scramble_laser_tolerance_vars),
                ("Receiver", self.full_scramble_receiver_tolerance_vars),
                *self._ball_tolerance_rows(self.full_scramble_ball_tolerance_vars),
            ),
            row=3,
        )

    def _axis_tolerance_vars(self, x_default_um: float, y_default_um: float, z_default_um: float) -> tuple[tk.StringVar, tk.StringVar, tk.StringVar]:
        return (
            tk.StringVar(value=f"{x_default_um:.3g}"),
            tk.StringVar(value=f"{y_default_um:.3g}"),
            tk.StringVar(value=f"{z_default_um:.3g}"),
        )

    def _ball_tolerance_rows(
        self,
        tolerance_vars: Sequence[tuple[tk.StringVar, tk.StringVar, tk.StringVar]],
    ) -> tuple[tuple[str, tuple[tk.StringVar, tk.StringVar, tk.StringVar]], ...]:
        return tuple(
            (f"Ball {index + 1}", axis_vars)
            for index, axis_vars in enumerate(tolerance_vars)
        )

    def _build_scramble_tolerance_table(
        self,
        title: str,
        rows: Sequence[tuple[str, tuple[tk.StringVar, tk.StringVar, tk.StringVar]]],
        *,
        row: int,
    ) -> None:
        section = ttk.LabelFrame(self.scramble_tolerances_frame, text=title, padding=(6, 4))
        grid_row = row // 2
        grid_column = row % 2
        section.grid(
            row=grid_row,
            column=grid_column,
            sticky="new",
            padx=(0, 6) if grid_column == 0 else (0, 0),
            pady=(0, 6),
        )
        self.scramble_tolerance_sections[title] = section
        section.columnconfigure(0, weight=1)
        for column, label in enumerate(("X +/- um", "Y +/- um", "Z +/- um"), start=1):
            ttk.Label(section, text=label).grid(row=0, column=column, padx=(0, 4), sticky="w")
        for row_index, (label, axis_vars) in enumerate(rows, start=1):
            ttk.Label(section, text=label).grid(row=row_index, column=0, padx=(0, 8), pady=1, sticky="w")
            for axis_index, variable in enumerate(axis_vars, start=1):
                ttk.Entry(section, textvariable=variable, width=7).grid(
                    row=row_index,
                    column=axis_index,
                    padx=(0, 4),
                    pady=1,
                    sticky="w",
                )
        self.scramble_tolerances_frame.columnconfigure(0, weight=1)
        self.scramble_tolerances_frame.columnconfigure(1, weight=1)

    def _build_algorithm_parameters_section(self) -> None:
        self.algorithm_parameters_frame = ttk.LabelFrame(
            self.parameters_frame,
            text="Algorithm parameters",
            padding=(6, 4),
        )
        self.algorithm_parameters_frame.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        self.algorithm_parameters_frame.columnconfigure(0, weight=1)

        self.blind_algorithm_parameters_frame = ttk.LabelFrame(
            self.algorithm_parameters_frame,
            text="Blind power J algorithms",
            padding=(6, 4),
        )
        self.blind_algorithm_parameters_frame.grid(row=0, column=0, sticky="ew")
        self.blind_algorithm_parameters_frame.columnconfigure(1, weight=1)

        for column, header in enumerate(("Algorithm", "Steps um", "Attempts", "Max corr um", "Samples")):
            ttk.Label(self.blind_algorithm_parameters_frame, text=header).grid(
                row=0,
                column=column,
                padx=(0, 6),
                sticky="w",
            )

        self.blind_power_j_step_vars: dict[str, tk.StringVar] = {}
        self.blind_power_j_attempt_vars: dict[str, tk.StringVar] = {}
        self.blind_power_j_max_correction_vars: dict[str, tk.StringVar] = {}
        self.blind_power_j_sample_vars: dict[str, tk.StringVar] = {}
        labels = {
            "blind_power_j": "Auto fallback",
            "blind_power_j_newton": "Newton",
            "blind_power_j_gradient": "Gradient",
            "blind_power_j_best_of_9": "Best-of-9",
        }
        for row, algorithm_name in enumerate(BLIND_POWER_J_ALGORITHM_CLASSES, start=1):
            self.blind_power_j_step_vars[algorithm_name] = tk.StringVar(value=_default_blind_power_j_steps_um_text())
            self.blind_power_j_attempt_vars[algorithm_name] = tk.StringVar(value=str(BLIND_POWER_J_ATTEMPTS))
            self.blind_power_j_max_correction_vars[algorithm_name] = tk.StringVar(
                value=_default_um_text(BLIND_POWER_J_MAX_CORRECTION)
            )
            self.blind_power_j_sample_vars[algorithm_name] = tk.StringVar(value=str(BLIND_POWER_J_SAMPLES_PER_POINT))

            ttk.Label(self.blind_algorithm_parameters_frame, text=labels[algorithm_name]).grid(
                row=row,
                column=0,
                padx=(0, 6),
                pady=1,
                sticky="w",
            )
            ttk.Entry(
                self.blind_algorithm_parameters_frame,
                textvariable=self.blind_power_j_step_vars[algorithm_name],
                width=24,
            ).grid(row=row, column=1, padx=(0, 6), pady=1, sticky="ew")
            ttk.Entry(
                self.blind_algorithm_parameters_frame,
                textvariable=self.blind_power_j_attempt_vars[algorithm_name],
                width=7,
            ).grid(row=row, column=2, padx=(0, 6), pady=1, sticky="w")
            ttk.Entry(
                self.blind_algorithm_parameters_frame,
                textvariable=self.blind_power_j_max_correction_vars[algorithm_name],
                width=9,
            ).grid(row=row, column=3, padx=(0, 6), pady=1, sticky="w")
            ttk.Entry(
                self.blind_algorithm_parameters_frame,
                textvariable=self.blind_power_j_sample_vars[algorithm_name],
                width=7,
            ).grid(row=row, column=4, padx=(0, 6), pady=1, sticky="w")

        self.blind_power_j_steps_var = self.blind_power_j_step_vars["blind_power_j"]
        self.blind_power_j_attempts_var = self.blind_power_j_attempt_vars["blind_power_j"]
        self.blind_power_j_max_correction_var = self.blind_power_j_max_correction_vars["blind_power_j"]
        self.blind_power_j_samples_var = self.blind_power_j_sample_vars["blind_power_j"]

    def _parse_positive_int_var(self, variable: tk.StringVar, name: str) -> int:
        try:
            value = int(variable.get().strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive integer.") from exc
        if value <= 0:
            raise ValueError(f"{name} must be a positive integer.")
        return value

    def _parse_positive_float_um_var(self, variable: tk.StringVar, name: str) -> float:
        try:
            value_um = float(variable.get().strip())
        except ValueError as exc:
            raise ValueError(f"{name} must be a positive finite number.") from exc
        if value_um <= 0.0 or not math.isfinite(value_um):
            raise ValueError(f"{name} must be a positive finite number.")
        return value_um / 1e6

    def _parse_blind_power_j_parameters(self, algorithm_name: str) -> dict[str, object]:
        return {
            "steps": self._parse_blind_power_j_steps(algorithm_name),
            "max_attempts": self._parse_positive_int_var(
                self.blind_power_j_attempt_vars[algorithm_name],
                f"{algorithm_name} attempts",
            ),
            "max_correction": self._parse_positive_float_um_var(
                self.blind_power_j_max_correction_vars[algorithm_name],
                f"{algorithm_name} max correction",
            ),
            "samples_per_point": self._parse_positive_int_var(
                self.blind_power_j_sample_vars[algorithm_name],
                f"{algorithm_name} samples",
            ),
        }

    def _parse_tolerance_um(self, variable: tk.StringVar, name: str) -> float:
        try:
            tolerance_um = float(variable.get().strip())
        except ValueError as exc:
            raise ValueError(f"{name} tolerance must be a non-negative finite number.") from exc
        tolerance = tolerance_um / 1e6
        _validate_tolerance(name, tolerance)
        return tolerance

    def _parse_blind_power_j_steps(self, algorithm_name: str = "blind_power_j") -> tuple[float, ...]:
        raw = self.blind_power_j_step_vars[algorithm_name].get().strip()
        if not raw:
            raise ValueError(
                "Blind power J step sizes must include at least one positive finite value."
            )
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        if not parts:
            raise ValueError(
                "Blind power J step sizes must include at least one positive finite value."
            )
        steps: list[float] = []
        for index, part in enumerate(parts, start=1):
            try:
                step_um = float(part)
            except ValueError as exc:
                raise ValueError(
                    f"Blind power J step {index} must be a positive finite number."
                ) from exc
            if step_um <= 0 or not math.isfinite(step_um):
                raise ValueError(
                    f"Blind power J step {index} must be a positive finite number."
                )
            steps.append(step_um / 1e6)
        return tuple(steps)

    def _create_algorithm_instance(self, algorithm_name: str) -> AlignmentAlgorithm:
        if algorithm_name in BLIND_POWER_J_ALGORITHM_CLASSES:
            return BLIND_POWER_J_ALGORITHM_CLASSES[algorithm_name](
                **self._parse_blind_power_j_parameters(algorithm_name)
            )
        return get_algorithm(algorithm_name)

    def _parse_axis_tolerances(
        self,
        variables: tuple[tk.StringVar, tk.StringVar, tk.StringVar],
        name: str,
    ) -> AxisTolerances:
        return (
            self._parse_tolerance_um(variables[0], f"{name} x"),
            self._parse_tolerance_um(variables[1], f"{name} y"),
            self._parse_tolerance_um(variables[2], f"{name} z"),
        )

    def _parse_ball_axis_tolerances(
        self,
        tolerance_vars: Sequence[tuple[tk.StringVar, tk.StringVar, tk.StringVar]],
        name: str,
    ) -> tuple[AxisTolerances, ...]:
        return tuple(
            self._parse_axis_tolerances(axis_vars, f"{name} ball {index + 1}")
            for index, axis_vars in enumerate(tolerance_vars)
        )

    def _parse_alignment_controls(self) -> tuple[int, AxisTolerances, AxisTolerances, tuple[AxisTolerances, ...]]:
        seed = int(self.seed_var.get().strip())
        return (
            seed,
            self._parse_axis_tolerances(self.seed_laser_tolerance_vars, "seed laser"),
            self._parse_axis_tolerances(self.seed_receiver_tolerance_vars, "seed receiver"),
            self._parse_ball_axis_tolerances(self.seed_ball_tolerance_vars, "seed"),
        )

    def power_noise_enabled(self) -> bool:
        variable = getattr(self, "power_noise_enabled_var", None)
        return bool(variable.get()) if variable is not None else False

    def _update_noise_status(self) -> None:
        if hasattr(self, "power_noise_status_var"):
            self.power_noise_status_var.set("ON" if self.power_noise_enabled() else "OFF")

    def power_noise_fraction(self) -> float:
        variable = getattr(self, "power_noise_percent_var", None)
        percent_text = variable.get().strip() if variable is not None else "0"
        percent = float(percent_text)
        if not math.isfinite(percent) or percent < 0.0:
            raise ValueError("Power noise must be a non-negative percentage.")
        return percent / 100.0

    def max_coupled_power(self) -> float:
        if not self.sources:
            return 0.0
        return max(float(self.sources[0].power), 0.0)

    def next_power_noise(self, amplitude: float) -> float:
        if amplitude <= 0.0:
            return 0.0
        return self._power_noise_rng.uniform(-amplitude, amplitude)

    def next_vision_script_scramble_delta(self, amplitude: float) -> float:
        return random.uniform(-amplitude, amplitude)

    def _parse_vision_script_scramble_tolerances(
        self,
    ) -> tuple[AxisTolerances, AxisTolerances, tuple[AxisTolerances, ...]]:
        return (
            self._parse_axis_tolerances(self.vision_laser_tolerance_vars, "vision laser"),
            self._parse_axis_tolerances(self.vision_receiver_tolerance_vars, "vision receiver"),
            self._parse_ball_axis_tolerances(self.vision_ball_tolerance_vars, "vision"),
        )

    def _ball_axis_tolerance_for_element(
        self,
        element,
        tolerance_vars: Sequence[tuple[tk.StringVar, tk.StringVar, tk.StringVar]],
        name: str,
    ) -> AxisTolerances:
        try:
            index = self.balls.index(element)
        except ValueError:
            index = 0
        if index >= len(tolerance_vars):
            index = len(tolerance_vars) - 1
        return self._parse_axis_tolerances(tolerance_vars[index], f"{name} ball {index + 1}")

    def _scramble_laser_fibre_axis_tolerances(self, element) -> AxisTolerances:
        if element in self.balls and self.laser_fibre_ball_tolerance_vars:
            return self._ball_axis_tolerance_for_element(
                element,
                self.laser_fibre_ball_tolerance_vars,
                "laser/fibre scramble",
            )
        if element in self.tapers:
            return self._parse_axis_tolerances(self.laser_fibre_receiver_tolerance_vars, "laser/fibre receiver")
        return self._parse_axis_tolerances(self.laser_fibre_laser_tolerance_vars, "laser/fibre laser")

    def _scramble_laser_fibre_elements(self):
        elements = [*self.sources, *self.fibers, *self.tapers]
        ball_tolerances = self._parse_ball_axis_tolerances(
            self.laser_fibre_ball_tolerance_vars,
            "laser/fibre scramble",
        )
        for ball, tolerances in zip(self.balls, ball_tolerances):
            if any(tolerance > 0.0 for tolerance in tolerances):
                elements.append(ball)
        return elements

    def _scramble_full_lens_axis_tolerances(self, element) -> AxisTolerances:
        if element in self.balls and self.full_scramble_ball_tolerance_vars:
            return self._ball_axis_tolerance_for_element(
                element,
                self.full_scramble_ball_tolerance_vars,
                "full scramble",
            )
        if self.full_scramble_ball_tolerance_vars:
            return self._parse_axis_tolerances(self.full_scramble_ball_tolerance_vars[0], "full scramble lens")
        return TRANSVERSE_TOLERANCE, TRANSVERSE_TOLERANCE, AXIAL_TOLERANCE

    def _scramble_full_source_axis_tolerances(self, element) -> AxisTolerances:
        if element in self.tapers:
            return self._parse_axis_tolerances(self.full_scramble_receiver_tolerance_vars, "full scramble receiver")
        return self._parse_axis_tolerances(self.full_scramble_laser_tolerance_vars, "full scramble laser")

    def _capture_nominal_ball_poses(self) -> None:
        self._nominal_ball_poses = self.current_poses()

    def _ensure_nominal_ball_poses(self) -> None:
        if len(self._nominal_ball_poses) != len(self.balls):
            self._capture_nominal_ball_poses()

    def current_poses(self) -> tuple[LensPose, ...]:
        return tuple((ball.x_offset, ball.y_offset, ball.position) for ball in self.balls)

    def _apply_initial_ball_offsets(self) -> None:
        for ball in self.balls:
            ball.x_offset = DEFAULT_INITIAL_BALL_X_OFFSET
            ball.y_offset = DEFAULT_INITIAL_BALL_Y_OFFSET

    def starting_poses(self) -> tuple[LensPose, ...]:
        self._ensure_nominal_ball_poses()
        return self._nominal_ball_poses

    def _refresh_alignment_no_go_zones(self) -> tuple[BallLensNoGoZone, ...]:
        self._alignment_no_go_zones = self._no_go_zones_for_poses(self.current_poses())
        return self._alignment_no_go_zones

    def _no_go_zones_for_poses(self, poses: Sequence[LensPose]) -> tuple[BallLensNoGoZone, ...]:
        source_z = self.sources[0].position if self.sources else 0.0
        taper_z = self.tapers[0].position if self.tapers else self.final_z
        return alignment_no_go_zones_for_layout(
            source_z=source_z,
            taper_z=taper_z,
            final_z=self.final_z,
            ball_poses=tuple(poses),
            ball_radii=tuple(ball.radius for ball in self.balls),
        )

    def no_go_zones(self) -> tuple[BallLensNoGoZone, ...]:
        return self._refresh_alignment_no_go_zones()

    def ball_no_go_violations(
        self,
        poses: Sequence[LensPose] | None = None,
    ) -> tuple[BallLensNoGoViolation, ...]:
        target_poses = tuple(poses) if poses is not None else self.current_poses()
        return ball_lens_no_go_violations(
            target_poses,
            tuple(ball.radius for ball in self.balls),
            self._no_go_zones_for_poses(target_poses),
            tuple(ball.name for ball in self.balls),
        )

    def _draw_background_overlays(self) -> None:
        super()._draw_background_overlays()
        for zone in self.no_go_zones():
            if self._active_plane == "y":
                self._draw_y_no_go_zone(zone)
            else:
                self._draw_x_no_go_zone(zone)

    def _no_go_zone_style(self, zone: BallLensNoGoZone) -> tuple[str, str]:
        styles = {
            "laser_side_no_go": ("#f8c8c8", "#a33a3a"),
            "trench_floor": ("#f6df9d", "#9a6a00"),
            "taper_side_no_go": ("#f8c8c8", "#a33a3a"),
        }
        if zone.name.startswith("vacuum_tweezer_"):
            return "#cfe8f5", "#2d6684"
        return styles.get(zone.name, ("#e0e0e0", "#666666"))

    def _visible_no_go_z_pixels(self, zone: BallLensNoGoZone) -> tuple[float, float, float, float] | None:
        left, right, top, bottom = self._plot_pixel_bounds()
        z_start = max(zone.z_low, self._z_min)
        z_end = min(zone.z_high, self._z_max)
        if z_end <= z_start:
            return None
        z_left = max(left, min(right, self._z_to_px(z_start)))
        z_right = max(left, min(right, self._z_to_px(z_end)))
        if z_right <= z_left:
            return None
        return z_left, z_right, top, bottom

    def _draw_y_no_go_zone(self, zone: BallLensNoGoZone) -> None:
        z_pixels = self._visible_no_go_z_pixels(zone)
        if z_pixels is None:
            return
        z_left, z_right, top, bottom = z_pixels
        fill, outline = self._no_go_zone_style(zone)

        zone_y_min = self._x_min if zone.y_min is None else zone.y_min
        zone_y_max = self._x_max if zone.y_max is None else zone.y_max
        if zone_y_max <= zone_y_min:
            return

        rect_top = max(top, self._x_to_px(zone_y_max))
        rect_bottom = min(bottom, self._x_to_px(zone_y_min))
        if rect_bottom <= rect_top:
            return

        self.canvas.create_rectangle(
            z_left,
            rect_top,
            z_right,
            rect_bottom,
            fill=fill,
            outline="",
            stipple="gray50",
            tags=("no_go_zone", zone.name, f"plane_{self._active_plane}"),
        )
        if zone.y_max is not None:
            boundary_px = self._x_to_px(zone.y_max)
            if top <= boundary_px <= bottom:
                self.canvas.create_line(
                    z_left,
                    boundary_px,
                    z_right,
                    boundary_px,
                    fill=outline,
                    dash=(5, 3),
                    width=1.5,
                )
        if zone.y_min is not None:
            boundary_px = self._x_to_px(zone.y_min)
            if top <= boundary_px <= bottom:
                self.canvas.create_line(
                    z_left,
                    boundary_px,
                    z_right,
                    boundary_px,
                    fill=outline,
                    dash=(5, 3),
                    width=1.5,
                )
        label_y = min(rect_bottom - 14, max(rect_top + 14, 0.5 * (rect_top + rect_bottom)))
        self.canvas.create_text(
            0.5 * (z_left + z_right),
            label_y,
            text=zone.label or zone.name,
            fill=outline,
            font=("Segoe UI", 9, "bold"),
            width=max(90, int(z_right - z_left) - 8),
            tags=("no_go_zone", zone.name, f"plane_{self._active_plane}"),
        )

    def _draw_x_no_go_zone(self, zone: BallLensNoGoZone) -> None:
        z_pixels = self._visible_no_go_z_pixels(zone)
        if z_pixels is None:
            return
        z_left, z_right, top, bottom = z_pixels
        fill, outline = self._no_go_zone_style(zone)

        if zone.x_min is not None and zone.x_max is not None:
            rect_top = max(top, self._x_to_px(zone.x_max))
            rect_bottom = min(bottom, self._x_to_px(zone.x_min))
            if rect_bottom <= rect_top:
                return
            label_y = min(rect_bottom - 10, max(rect_top + 10, 0.5 * (rect_top + rect_bottom)))
            text = zone.label or zone.name
        else:
            rect_top = top
            rect_bottom = bottom
            label_y = 0.5 * (rect_top + rect_bottom)
            text = zone.label or zone.name

        self.canvas.create_rectangle(
            z_left,
            rect_top,
            z_right,
            rect_bottom,
            fill=fill,
            outline="",
            stipple="gray75",
            tags=("no_go_zone", zone.name, f"plane_{self._active_plane}"),
        )
        self.canvas.create_text(
            0.5 * (z_left + z_right),
            label_y,
            text=text,
            fill=outline,
            font=("Segoe UI", 8, "bold"),
            width=max(90, int(z_right - z_left) - 8),
            tags=("no_go_zone", zone.name, f"plane_{self._active_plane}"),
        )

    def _draw_ball(self, ball) -> None:
        super()._draw_ball(ball)
        lens_index = next((index for index, candidate in enumerate(self.balls) if candidate is ball), None)
        if lens_index is None:
            return
        if not any(violation.lens_index == lens_index for violation in self.ball_no_go_violations()):
            return

        z_px = self._z_to_px(ball.position)
        plane_offset = self._plane_offset(ball)
        offset_px = self._x_to_px(plane_offset)
        z_radius_px = max(10.0, abs(self._z_to_px(ball.position + ball.radius) - z_px))
        offset_radius_px = max(10.0, abs(self._x_to_px(plane_offset + ball.radius) - offset_px))
        self.canvas.create_oval(
            z_px - z_radius_px - 5,
            offset_px - offset_radius_px - 5,
            z_px + z_radius_px + 5,
            offset_px + offset_radius_px + 5,
            outline="#c62828",
            dash=(5, 3),
            width=3,
        )

    def _layout_view_bounds(self) -> tuple[float, float, float, float]:
        base_z_min, base_z_max, base_x_min, base_x_max = super()._layout_view_bounds()
        source_z = self.sources[0].position if self.sources else 0.0
        taper_z = self.tapers[0].position if self.tapers else self.final_z
        base_z_min = min(base_z_min, source_z - DEFAULT_ALIGNMENT_VIEW_Z_MARGIN)
        base_z_max = max(base_z_max, taper_z + DEFAULT_ALIGNMENT_VIEW_Z_MARGIN)
        zones = self.no_go_zones()
        if not zones:
            return base_z_min, base_z_max, base_x_min, base_x_max

        zone_y_values: list[float] = []
        for zone in zones:
            if zone.y_min is not None:
                zone_y_values.append(zone.y_min)
            if zone.y_max is not None:
                zone_y_values.append(zone.y_max)
        if zone_y_values:
            y_margin = 75e-6
            base_x_min = min(base_x_min, min(zone_y_values) - y_margin)
            base_x_max = max(base_x_max, max(zone_y_values) + y_margin)

        return base_z_min, base_z_max, base_x_min, base_x_max

    def _view_limit_bounds(self) -> tuple[float, float, float, float]:
        base_z_min, base_z_max, base_x_min, base_x_max = self._layout_view_bounds()
        source_z = self.sources[0].position if self.sources else 0.0
        taper_z = self.tapers[0].position if self.tapers else self.final_z
        return (
            min(base_z_min, source_z - DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN),
            max(base_z_max, taper_z + DEFAULT_ALIGNMENT_WORKSPACE_Z_MARGIN),
            min(base_x_min, -DEFAULT_ALIGNMENT_WORKSPACE_TRANSVERSE_LIMIT),
            max(base_x_max, DEFAULT_ALIGNMENT_WORKSPACE_TRANSVERSE_LIMIT),
        )

    def _minimum_element_position(self, element) -> float:
        if getattr(element, "kind", None) == "ball":
            return self._z_min
        return super()._minimum_element_position(element)

    def _minimum_ball_entry_z(self, _ball) -> float:
        return -math.inf

    def model_geometry(self) -> AlignmentModelGeometry:
        if not self.sources:
            raise ValueError("No laser source is available for model-based alignment.")
        if not self.tapers:
            raise ValueError("No taper detector is available for model-based alignment.")
        self._ensure_nominal_ball_poses()

        source = self.sources[0]
        taper = self.tapers[0]
        if source.rayleigh_range is None or source.rayleigh_range_y is None or source.waist_radius_y is None:
            raise ValueError("The laser source is missing Gaussian beam parameters.")

        return AlignmentModelGeometry(
            source=SourceGeometry(
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
            ),
            taper=TaperGeometry(
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
            ),
            balls=tuple(
                BallLensGeometry(
                    name=ball.name,
                    position=ball.position,
                    diameter=ball.diameter,
                    refractive_index=ball.refractive_index,
                    x_offset=ball.x_offset,
                    y_offset=ball.y_offset,
                )
                for ball in self.balls
            ),
            current_poses=self.current_poses(),
            starting_poses=self.starting_poses(),
            clipping_radius_factor=DEFAULT_CLIPPING_RADIUS_FACTOR,
            no_go_zones=self.no_go_zones(),
        )

    def _apply_lens_poses(self, poses: tuple[LensPose, ...]) -> None:
        if len(poses) != len(self.balls):
            raise ValueError("lens pose count does not match the current ball count")
        for ball, pose in zip(self.balls, poses):
            x_offset, y_offset, position = pose
            ball.x_offset = float(x_offset)
            ball.y_offset = float(y_offset)
            ball.position = float(position)

    def _ball_pose_offsets_from_nominal(self) -> tuple[LensPose, ...]:
        self._ensure_nominal_ball_poses()
        return tuple(
            (
                ball.x_offset - nominal_x,
                ball.y_offset - nominal_y,
                ball.position - nominal_z,
            )
            for ball, (nominal_x, nominal_y, nominal_z) in zip(self.balls, self._nominal_ball_poses)
        )

    def _format_lens_pose_offsets(self, offsets: tuple[LensPose, ...]) -> str:
        if not offsets:
            return "none"
        return "; ".join(
            f"B{index + 1} (x {x_offset * 1e6:.4g}, y {y_offset * 1e6:.4g}, z {z_offset * 1e6:.4g}) um"
            for index, (x_offset, y_offset, z_offset) in enumerate(offsets)
        )

    def _apply_seeded_alignment_errors(self, push_undo: bool) -> None:
        if not self.sources or not self.tapers:
            return
        self._ensure_nominal_ball_poses()
        scramble = seeded_alignment_errors(
            self.alignment_seed,
            self.source_detector_tolerance,
            self.lens_pose_tolerance,
            lens_count=len(self.balls),
            taper_tolerance=self.taper_seed_tolerance,
            ball_pose_tolerances=self.ball_seed_tolerances,
        )
        if push_undo:
            self._push_undo()
        source = self.sources[0]
        taper = self.tapers[0]
        source_nominal = self._nominal_for(source)
        taper_nominal = self._nominal_for(taper)
        source.x_offset = source_nominal.x_offset + scramble.source_x_offset
        source.y_offset = source_nominal.y_offset + scramble.source_y_offset
        source.position = max(
            self._minimum_element_position(source),
            source_nominal.position + scramble.source_z_offset,
        )
        taper.x_offset = taper_nominal.x_offset + scramble.taper_x_offset
        taper.y_offset = taper_nominal.y_offset + scramble.taper_y_offset
        taper.position = max(
            self._minimum_element_position(taper),
            taper_nominal.position + scramble.taper_z_offset,
        )
        poses = tuple(
            (
                nominal_x + offset_x,
                nominal_y + offset_y,
                nominal_z + offset_z,
            )
            for (nominal_x, nominal_y, nominal_z), (offset_x, offset_y, offset_z) in zip(
                self._nominal_ball_poses,
                scramble.ball_pose_offsets,
            )
        )
        self._apply_lens_poses(poses)
        self._clear_simulation_overlay()
        self._fit_view_bounds_to_layout()
        self._refresh_tree()
        self.redraw()

    def _apply_seeded_height_errors(self, push_undo: bool) -> None:
        self._apply_seeded_alignment_errors(push_undo=push_undo)

    def _vision_script_scramble(self) -> None:
        if not self.sources or not self.tapers:
            self.status_var.set("Vision script scramble unavailable.")
            return
        try:
            (
                laser_tolerances,
                receiver_tolerances,
                ball_tolerances,
            ) = self._parse_vision_script_scramble_tolerances()
        except ValueError as exc:
            messagebox.showerror("Invalid vision script scramble", str(exc))
            return
        self._cancel_algorithm_animation()
        self._push_undo()

        source = self.sources[0]
        receiver = self.tapers[0]
        source.x_offset += self.next_vision_script_scramble_delta(laser_tolerances[0])
        source.y_offset += self.next_vision_script_scramble_delta(laser_tolerances[1])
        source.position = max(
            self._minimum_element_position(source),
            source.position + self.next_vision_script_scramble_delta(laser_tolerances[2]),
        )
        receiver.x_offset += self.next_vision_script_scramble_delta(receiver_tolerances[0])
        receiver.y_offset += self.next_vision_script_scramble_delta(receiver_tolerances[1])
        receiver.position += self.next_vision_script_scramble_delta(receiver_tolerances[2])
        for ball, tolerances in zip(self.balls, ball_tolerances):
            if not any(tolerance > 0.0 for tolerance in tolerances):
                continue
            ball.x_offset += self.next_vision_script_scramble_delta(tolerances[0])
            ball.y_offset += self.next_vision_script_scramble_delta(tolerances[1])
            ball.position += self.next_vision_script_scramble_delta(tolerances[2])

        self._validate_element(source)
        self._validate_element(receiver)
        for ball in self.balls:
            self._validate_element(ball)
        self._clear_simulation_overlay()
        self._fit_view_bounds_to_layout()
        self._best_evaluation = None
        evaluation = self._run_alignment_simulation(update_report=True)
        self.status_var.set(f"Vision script scramble: {evaluation.received_power * 1e3:.6g} mW received.")

    def _move_balls_to_starting_position(self) -> None:
        if not self.balls:
            return
        self._cancel_algorithm_animation()
        self._push_undo()
        source_z = self.sources[0].position if self.sources else 0.0
        poses = outside_trench_starting_poses(
            self.current_poses(),
            tuple(ball.radius for ball in self.balls),
            source_z,
        )
        self._apply_lens_poses(poses)
        self._best_evaluation = None
        evaluation = self._run_alignment_simulation(update_report=True)
        if self._alignment_ui_ready:
            self.algorithm_status_var.set("Algorithm: idle")
            self._clear_blind_direction_method_label()
        self.status_var.set(
            f"Moved balls to starting position outside the trench: {evaluation.received_power * 1e3:.6g} mW received."
        )

    def _return_to_simulation_start(self) -> None:
        self._cancel_algorithm_animation()
        if self._last_algorithm_run is None:
            self.status_var.set("No previous run.")
            return
        self._push_undo()
        self._restore_layout_snapshot(self._last_algorithm_run.initial_snapshot)
        self._best_evaluation = None
        self._run_alignment_simulation(update_report=True)
        if self._alignment_ui_ready:
            self.algorithm_status_var.set("Algorithm: idle")
            self._clear_blind_direction_method_label()
        self.status_var.set("Returned to run start.")

    def _rescramble_alignment_errors(self) -> None:
        try:
            (
                self.alignment_seed,
                self.source_detector_tolerance,
                self.taper_seed_tolerance,
                self.ball_seed_tolerances,
            ) = self._parse_alignment_controls()
            self.lens_pose_tolerance = self.ball_seed_tolerances[0] if self.ball_seed_tolerances else (
                DEFAULT_LENS_POSE_TOLERANCE,
                DEFAULT_LENS_POSE_TOLERANCE,
                DEFAULT_LENS_POSE_TOLERANCE,
            )
        except ValueError as exc:
            messagebox.showerror("Invalid alignment scramble", str(exc))
            return
        self._apply_seeded_alignment_errors(push_undo=True)
        self._best_evaluation = None
        self._run_alignment_simulation(update_report=True)

    def _rescramble_height_errors(self) -> None:
        self._rescramble_alignment_errors()

    def evaluate_current_alignment(self) -> AlignmentEvaluation:
        results = simulate_layout(
            self.sources,
            self.lenses,
            self.fibers,
            self.final_z,
            balls=self.balls,
            tapers=self.tapers,
            refractive_index=DEFAULT_REFRACTIVE_INDEX,
            clipping_radius_factor=DEFAULT_CLIPPING_RADIUS_FACTOR,
        )
        return self._evaluation_from_results(results)

    def _evaluation_from_results(self, results) -> AlignmentEvaluation:
        source = self.sources[0]
        taper = self.tapers[0]
        received_power = 0.0
        total_efficiency = 0.0
        mode_efficiency = 0.0
        warnings: list[str] = []
        no_go_violations = self.ball_no_go_violations()

        if results and results[0].taper_results:
            taper_result = results[0].taper_results[0]
            received_power = taper_result.received_power
            mode_efficiency = taper_result.mode_efficiency
            total_efficiency = received_power / source.power if source.power > 0 else 0.0
            warnings.extend(results[0].warnings)
            warnings.extend(taper_result.warnings)
        warnings.extend(violation.message for violation in no_go_violations)

        return AlignmentEvaluation(
            source_x_offset=source.x_offset,
            source_y_offset=source.y_offset,
            taper_x_offset=taper.x_offset,
            taper_y_offset=taper.y_offset,
            ball_poses=self.current_poses(),
            ball_pose_offsets=self._ball_pose_offsets_from_nominal(),
            received_power=received_power,
            total_efficiency=total_efficiency,
            mode_efficiency=mode_efficiency,
            warnings=tuple(warnings),
        )

    def _run_alignment_simulation(self, update_report: bool) -> AlignmentEvaluation:
        results = simulate_layout(
            self.sources,
            self.lenses,
            self.fibers,
            self.final_z,
            balls=self.balls,
            tapers=self.tapers,
            refractive_index=DEFAULT_REFRACTIVE_INDEX,
            clipping_radius_factor=DEFAULT_CLIPPING_RADIUS_FACTOR,
        )
        for fiber in self.fibers:
            fiber.received_power = 0.0
        for taper in self.tapers:
            taper.received_power = 0.0
        for result in results:
            for fiber_result in result.fiber_results:
                fiber_result.fiber.received_power += fiber_result.received_power
            for taper_result in result.taper_results:
                taper_result.taper.received_power += taper_result.received_power

        self._beam_paths = self._build_beam_paths()
        evaluation = self._evaluation_from_results(results)
        self._last_evaluation = evaluation
        self._update_alignment_readout(evaluation)
        if update_report:
            self._write_output_report(format_simulation_report(results))
        self._refresh_tree()
        self.redraw()
        return evaluation

    def _clear_power_noise_delta_label(self) -> None:
        if not self._alignment_ui_ready:
            return
        self.power_noise_delta_label.configure(text="", fg=POWER_NOISE_DELTA_ADDED_COLOR)

    def _set_power_noise_delta_label(self, delta_watts: float) -> None:
        if not self._alignment_ui_ready:
            return
        if abs(delta_watts) <= POWER_NOISE_DELTA_TOLERANCE:
            self._clear_power_noise_delta_label()
            return
        delta_mw = delta_watts * 1e3
        if delta_watts > 0.0:
            self.power_noise_delta_label.configure(
                text=f"+{delta_mw:.6g} mW",
                fg=POWER_NOISE_DELTA_ADDED_COLOR,
            )
        else:
            self.power_noise_delta_label.configure(
                text=f"−{abs(delta_mw):.6g} mW",
                fg=POWER_NOISE_DELTA_SUBTRACTED_COLOR,
            )

    def _update_alignment_readout(
        self,
        evaluation: AlignmentEvaluation,
        *,
        device_reading: PowerReading | None = None,
    ) -> None:
        if not self._alignment_ui_ready:
            return
        received_mw = evaluation.received_power * 1e3
        received_percent = evaluation.total_efficiency * 100
        mode_percent = evaluation.mode_efficiency * 100
        self.power_percent_var.set(
            f"MODE MATCH: {mode_percent:.6g}%  |  RECEIVED: {received_percent:.6g}%  |  {received_mw:.6g} mW"
        )
        self.received_power_var.set(f"Received power: {evaluation.received_power * 1e3:.6g} mW")
        self.total_efficiency_var.set(f"Coupling total: {evaluation.total_efficiency * 100:.6g}%")
        self.mode_efficiency_var.set(f"Mode match: {evaluation.mode_efficiency * 100:.6g}%")
        self.source_height_var.set(
            f"Source error: x {evaluation.source_x_offset * 1e6:.4g}, "
            f"y {evaluation.source_y_offset * 1e6:.4g} um"
        )
        self.detector_height_var.set(
            f"Detector error: x {evaluation.taper_x_offset * 1e6:.4g}, "
            f"y {evaluation.taper_y_offset * 1e6:.4g} um"
        )
        self.lens_offsets_var.set(
            f"Ball pose errors: {self._format_lens_pose_offsets(evaluation.ball_pose_offsets)}"
        )

        if device_reading is not None and abs(device_reading.noise_delta) > POWER_NOISE_DELTA_TOLERANCE:
            self._set_power_noise_delta_label(device_reading.noise_delta)
        else:
            self._clear_power_noise_delta_label()

        if self._best_evaluation is None or evaluation.received_power > self._best_evaluation.received_power:
            self._best_evaluation = evaluation
        best = self._best_evaluation
        self.best_power_var.set(f"Best so far: {best.received_power * 1e3:.6g} mW")
        self.best_offsets_var.set(
            f"Best ball pose errors: {self._format_lens_pose_offsets(best.ball_pose_offsets)}"
        )
        self.no_go_warning_var.set(format_no_go_warning(self.ball_no_go_violations()))

    def _maybe_run_live_alignment(self) -> None:
        if self._alignment_initializing or not self._alignment_ui_ready:
            return
        self._run_alignment_simulation(update_report=False)

    def _simulate(self) -> None:
        evaluation = self._run_alignment_simulation(update_report=True)
        self.status_var.set(f"Alignment simulation complete: {evaluation.received_power * 1e3:.6g} mW received.")

    def _on_canvas_drag(self, event: tk.Event) -> None:
        drag_mode = self._drag.get("mode") if self._drag else None
        super()._on_canvas_drag(event)
        if drag_mode != "pan":
            self._maybe_run_live_alignment()

    def _save_editor(self, dialog, element, fields, variables) -> None:
        super()._save_editor(dialog, element, fields, variables)
        self._maybe_run_live_alignment()

    def _apply_final_z(self) -> None:
        super()._apply_final_z()
        self._maybe_run_live_alignment()

    def _reset_defaults(self) -> None:
        super()._reset_defaults()
        self._capture_nominal_ball_poses()
        self._best_evaluation = None
        self._apply_initial_ball_offsets()
        self._clear_simulation_overlay()
        self._fit_view_bounds_to_layout()
        self._refresh_tree()
        self.redraw()
        self._maybe_run_live_alignment()

    def _add_source(self) -> None:
        super()._add_source()
        self._maybe_run_live_alignment()

    def _add_ball(self) -> None:
        super()._add_ball()
        self._ensure_nominal_ball_poses()
        self._maybe_run_live_alignment()

    def _add_taper(self) -> None:
        super()._add_taper()
        self._maybe_run_live_alignment()

    def _add_fiber(self) -> None:
        super()._add_fiber()
        self._maybe_run_live_alignment()

    def _delete_element(self, element, parent=None) -> bool:
        deleted = super()._delete_element(element, parent=parent)
        if deleted:
            self._ensure_nominal_ball_poses()
            self._maybe_run_live_alignment()
        return deleted

    def _selected_algorithm_name(self) -> str:
        selected = self.algorithm_var.get().strip()
        return self._algorithm_label_to_name.get(selected, selected)

    def _set_blind_direction_method_label(self, method: str | None) -> None:
        if not self._alignment_ui_ready:
            return
        if not method:
            self._set_blind_direction_method_idle()
            return
        text, color = self._blind_direction_method_display(method)
        self.algorithm_direction_method_var.set(text)
        self.algorithm_direction_method_label.configure(fg=color)

    def _set_blind_direction_method_idle(self) -> None:
        if not self._alignment_ui_ready:
            return
        self.algorithm_direction_method_var.set("Blind direction: —")
        self.algorithm_direction_method_label.configure(fg=BLIND_DIRECTION_IDLE_COLOR)

    def _clear_blind_direction_method_label(self) -> None:
        if not self._alignment_ui_ready:
            return
        self.algorithm_direction_method_var.set("")
        self.algorithm_direction_method_label.configure(fg=BLIND_DIRECTION_IDLE_COLOR)

    def _blind_direction_method_display(self, method: str) -> tuple[str, str]:
        short_names = {
            DIRECTION_METHOD_NEWTON: "Newton",
            DIRECTION_METHOD_GRADIENT: "Gradient",
            DIRECTION_METHOD_BEST_OF_9: "Best-of-9",
        }
        colors = {
            DIRECTION_METHOD_NEWTON: BLIND_DIRECTION_NEWTON_COLOR,
            DIRECTION_METHOD_GRADIENT: BLIND_DIRECTION_GRADIENT_COLOR,
            DIRECTION_METHOD_BEST_OF_9: BLIND_DIRECTION_BEST_OF_9_COLOR,
        }
        short_name = short_names.get(method, method)
        return f"Blind direction: {short_name}", colors.get(method, BLIND_DIRECTION_IDLE_COLOR)

    def _on_algorithm_selection_changed(self, *_args) -> None:
        if not self._alignment_ui_ready:
            return
        if _is_blind_power_j_algorithm(self._selected_algorithm_name()):
            if self._blind_algorithm_running:
                return
            current = self.algorithm_direction_method_var.get()
            if not current or current == "Blind direction: —":
                self._set_blind_direction_method_idle()
        else:
            self._clear_blind_direction_method_label()

    def _notify_blind_direction_method(self, method: str) -> None:
        if not self._blind_algorithm_running or not self._alignment_ui_ready:
            return
        self._set_blind_direction_method_label(method)
        self.update_idletasks()

    def _last_blind_direction_method(self, moves: Sequence[AlignmentMove]) -> str | None:
        for move in reversed(moves):
            if move.direction_method:
                return move.direction_method
        return None

    def _run_selected_algorithm(self) -> None:
        try:
            self.run_alignment_algorithm()
        except (RuntimeError, ValueError) as exc:
            messagebox.showerror("Alignment algorithm failed", str(exc))

    def _show_selected_algorithm(self) -> None:
        try:
            self.show_alignment_algorithm()
        except (RuntimeError, ValueError) as exc:
            messagebox.showerror("Alignment algorithm failed", str(exc))

    def _cancel_algorithm_animation(self) -> None:
        if self._algorithm_animation_after_id is None:
            return
        try:
            self.after_cancel(self._algorithm_animation_after_id)
        except tk.TclError:
            pass
        self._algorithm_animation_after_id = None
        self._clear_power_noise_delta_label()

    def _push_undo_snapshot(self, snapshot) -> None:
        self._undo_stack.append(snapshot)
        max_undo = 80
        if len(self._undo_stack) > max_undo:
            del self._undo_stack[0 : len(self._undo_stack) - max_undo]

    def create_alignment_device(self, update_display: bool = False) -> AlignmentLabDevice:
        return AlignmentLabDevice(self, update_display=update_display)

    def _solve_alignment_algorithm(
        self,
        name: str | None,
        update_display: bool,
    ):
        if not self.balls:
            raise ValueError("No ball lenses are available to align.")

        algorithm_name = name or self._selected_algorithm_name()
        algorithm = self._create_algorithm_instance(algorithm_name)
        initial_poses = self.current_poses()
        initial_snapshot = self._layout_snapshot()
        device = self.create_alignment_device(update_display=update_display)
        result = algorithm.run(device)
        if len(result.final_poses) != len(self.balls):
            raise ValueError("algorithm result has the wrong number of lens poses")
        return AlignmentAlgorithmRun(
            algorithm=algorithm,
            result=result,
            moves=device.move_history(),
            initial_poses=initial_poses,
            initial_snapshot=initial_snapshot,
        )

    def _set_algorithm_complete_status(self, algorithm, result, evaluation: AlignmentEvaluation) -> None:
        detail = f"{algorithm.display_name}: {result.move_count} moves, {result.evaluations} reads, {evaluation.received_power * 1e3:.6g} mW"
        if self._alignment_ui_ready:
            self.algorithm_status_var.set(detail)
            if _is_blind_power_j_algorithm(algorithm.name):
                self._set_blind_direction_method_label(self._last_blind_direction_method(result.move_history))
            else:
                self._clear_blind_direction_method_label()
        self.status_var.set(
            f"{algorithm.display_name} complete: {evaluation.received_power * 1e3:.6g} mW received."
        )

    def run_alignment_algorithm(self, name: str | None = None) -> AlignmentEvaluation:
        self._cancel_algorithm_animation()
        algorithm_name = name or self._selected_algorithm_name()
        if not _is_blind_power_j_algorithm(algorithm_name) and self._alignment_ui_ready:
            self._clear_blind_direction_method_label()
        elif _is_blind_power_j_algorithm(algorithm_name) and self._alignment_ui_ready:
            self.algorithm_direction_method_var.set("Blind direction: running…")
            self.algorithm_direction_method_label.configure(fg=BLIND_DIRECTION_IDLE_COLOR)
            self.update_idletasks()
        undo_snapshot = self._layout_snapshot()
        self._blind_algorithm_running = _is_blind_power_j_algorithm(algorithm_name)
        try:
            algorithm_run = self._solve_alignment_algorithm(name, update_display=False)
            self._last_algorithm_run = algorithm_run
            result = algorithm_run.result
            if result.final_poses != algorithm_run.initial_poses:
                self._push_undo_snapshot(undo_snapshot)
            self._apply_lens_poses(result.final_poses)

            evaluation = self._run_alignment_simulation(update_report=True)
            self._set_algorithm_complete_status(algorithm_run.algorithm, result, evaluation)
            return evaluation
        finally:
            self._blind_algorithm_running = False

    def show_alignment_algorithm(
        self,
        name: str | None = None,
        delay_ms: int = DEFAULT_ALGORITHM_SHOW_DELAY_MS,
    ) -> None:
        self._cancel_algorithm_animation()
        undo_snapshot = self._layout_snapshot()
        if name is None:
            if self._last_algorithm_run is None:
                raise RuntimeError("Run an algorithm before using Show.")
            algorithm_run = self._last_algorithm_run
        else:
            algorithm_run = self._solve_alignment_algorithm(name, update_display=False)
            self._last_algorithm_run = algorithm_run

        algorithm = algorithm_run.algorithm
        result = algorithm_run.result
        moves = algorithm_run.moves
        initial_poses = algorithm_run.initial_poses
        self._apply_lens_poses(initial_poses)

        if result.final_poses != initial_poses:
            self._push_undo_snapshot(undo_snapshot)

        move_list = list(moves)
        if not move_list:
            evaluation = self._run_alignment_simulation(update_report=True)
            self._set_algorithm_complete_status(algorithm, result, evaluation)
            return

        if self._alignment_ui_ready:
            self.algorithm_status_var.set(
                f"Showing {algorithm.display_name}: 0/{len(move_list)} device moves"
            )

        def play_step(index: int) -> None:
            move = move_list[index]
            self._apply_lens_poses(move.poses)
            evaluation = self._run_alignment_simulation(update_report=False)
            self._update_alignment_readout(evaluation, device_reading=move.reading)
            if self._alignment_ui_ready:
                self.algorithm_status_var.set(
                    f"Showing {algorithm.display_name}: {index + 1}/{len(move_list)}, "
                    f"{move.reading.received_power * 1e3:.6g} mW"
                )
                if _is_blind_power_j_algorithm(algorithm.name) and move.direction_method:
                    self._set_blind_direction_method_label(move.direction_method)

            if index + 1 < len(move_list):
                self._algorithm_animation_after_id = self.after(
                    max(0, int(delay_ms)),
                    lambda: play_step(index + 1),
                )
                return

            self._algorithm_animation_after_id = None
            self._apply_lens_poses(result.final_poses)
            final_evaluation = self._run_alignment_simulation(update_report=True)
            self._set_algorithm_complete_status(algorithm, result, final_evaluation)

        play_step(0)


def main() -> None:
    app = AlignmentLabEditor()
    app.mainloop()


if __name__ == "__main__":
    main()
