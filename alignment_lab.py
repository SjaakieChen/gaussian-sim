"""Interactive alignment sandbox with seeded source/detector height errors.

Run:

    python alignment_lab.py

This app reuses the existing ball-lens/taper simulator but starts with a
deterministic random height error on the laser source and taper detector. It is
intended as a manual and future automated optical-alignment test bench.
"""

from __future__ import annotations

import math
import random
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk

from alignment_algorithms import AlignmentMove, LensPose, PowerReading, available_algorithms, get_algorithm
from interactive_setup import (
    DEFAULT_CLIPPING_RADIUS_FACTOR,
    DEFAULT_REFRACTIVE_INDEX,
    OpticalLayoutEditor,
    format_simulation_report,
    simulate_layout,
)


DEFAULT_ALIGNMENT_SEED = 42
DEFAULT_INITIAL_BALL_X_OFFSET = 300e-6
# Max |offset| from nominal perfect alignment for source and taper x/y (metres).
DEFAULT_SOURCE_DETECTOR_TOLERANCE = 5.0e-6
# Max |offset| from nominal perfect alignment per lens x/y/z axis (metres).
DEFAULT_LENS_POSE_TOLERANCE = 2.0e-6
DEFAULT_HEIGHT_TOLERANCE = DEFAULT_SOURCE_DETECTOR_TOLERANCE
DEFAULT_ALGORITHM_SHOW_DELAY_MS = 180


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
    taper_x_offset: float
    taper_y_offset: float
    ball_pose_offsets: tuple[LensPose, ...]


def _validate_tolerance(name: str, tolerance: float) -> None:
    if tolerance < 0 or not math.isfinite(tolerance):
        raise ValueError(f"{name} tolerance must be non-negative and finite")


def seeded_alignment_errors(
    seed: int,
    source_detector_tolerance: float = DEFAULT_SOURCE_DETECTOR_TOLERANCE,
    lens_pose_tolerance: float = DEFAULT_LENS_POSE_TOLERANCE,
    lens_count: int = 2,
) -> AlignmentScramble:
    """Return reproducible misalignments relative to the nominal perfect layout."""

    _validate_tolerance("source/detector", source_detector_tolerance)
    _validate_tolerance("lens pose", lens_pose_tolerance)
    if lens_count < 0:
        raise ValueError("lens_count must be non-negative")

    rng = random.Random(int(seed))
    ball_pose_offsets = tuple(
        (
            rng.uniform(-lens_pose_tolerance, lens_pose_tolerance),
            rng.uniform(-lens_pose_tolerance, lens_pose_tolerance),
            rng.uniform(-lens_pose_tolerance, lens_pose_tolerance),
        )
        for _index in range(lens_count)
    )
    return AlignmentScramble(
        source_x_offset=rng.uniform(-source_detector_tolerance, source_detector_tolerance),
        source_y_offset=rng.uniform(-source_detector_tolerance, source_detector_tolerance),
        taper_x_offset=rng.uniform(-source_detector_tolerance, source_detector_tolerance),
        taper_y_offset=rng.uniform(-source_detector_tolerance, source_detector_tolerance),
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

    def current_poses(self) -> tuple[LensPose, ...]:
        return self._app.current_poses()

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

    def _reading_from_evaluation(self, evaluation: AlignmentEvaluation) -> PowerReading:
        return PowerReading(
            received_power=evaluation.received_power,
            total_efficiency=evaluation.total_efficiency,
            mode_efficiency=evaluation.mode_efficiency,
            move_count=self._move_count,
            measurement_count=self._measurement_count,
        )


class AlignmentLabEditor(OpticalLayoutEditor):
    """Alignment-oriented variant of the interactive layout editor."""

    def __init__(self) -> None:
        self.alignment_seed = DEFAULT_ALIGNMENT_SEED
        self.source_detector_tolerance = DEFAULT_SOURCE_DETECTOR_TOLERANCE
        self.lens_pose_tolerance = DEFAULT_LENS_POSE_TOLERANCE
        self._nominal_ball_poses: tuple[LensPose, ...] = ()
        self._alignment_algorithms = available_algorithms()
        self._algorithm_label_to_name: dict[str, str] = {}
        self._alignment_initializing = True
        self._alignment_ui_ready = False
        self._algorithm_animation_after_id: str | None = None
        self._last_evaluation: AlignmentEvaluation | None = None
        self._best_evaluation: AlignmentEvaluation | None = None
        super().__init__()
        self.title("Optical Alignment Lab")
        self._capture_nominal_ball_poses()
        self._alignment_initializing = False
        for ball in self.balls:
            ball.x_offset = DEFAULT_INITIAL_BALL_X_OFFSET
        self._clear_simulation_overlay()
        self._fit_view_bounds_to_layout()
        self._refresh_tree()
        self.redraw()
        self._run_alignment_simulation(update_report=True)

    def _build_ui(self) -> None:
        super()._build_ui()
        self.rowconfigure(2, weight=0)

        panel = ttk.Frame(self, padding=(8, 0, 8, 8))
        panel.grid(row=2, column=0, sticky="ew")
        panel.columnconfigure(14, weight=1)

        ttk.Label(panel, text="Seed").grid(row=0, column=0, padx=(0, 4), sticky="w")
        self.seed_var = tk.StringVar(value=str(DEFAULT_ALIGNMENT_SEED))
        ttk.Entry(panel, textvariable=self.seed_var, width=8).grid(row=0, column=1, padx=(0, 10), sticky="w")

        ttk.Label(panel, text="Src/det ±").grid(row=0, column=2, padx=(0, 4), sticky="w")
        self.source_detector_tolerance_var = tk.StringVar(
            value=f"{DEFAULT_SOURCE_DETECTOR_TOLERANCE * 1e6:.3g}"
        )
        ttk.Entry(panel, textvariable=self.source_detector_tolerance_var, width=8).grid(
            row=0, column=3, padx=(0, 4), sticky="w"
        )
        ttk.Label(panel, text="um").grid(row=0, column=4, padx=(0, 10), sticky="w")

        ttk.Label(panel, text="Lens ±").grid(row=0, column=5, padx=(0, 4), sticky="w")
        self.lens_tolerance_var = tk.StringVar(value=f"{DEFAULT_LENS_POSE_TOLERANCE * 1e6:.3g}")
        ttk.Entry(panel, textvariable=self.lens_tolerance_var, width=8).grid(
            row=0, column=6, padx=(0, 4), sticky="w"
        )
        ttk.Label(panel, text="um").grid(row=0, column=7, padx=(0, 10), sticky="w")

        ttk.Button(panel, text="Rescramble", command=self._rescramble_alignment_errors).grid(row=0, column=8, padx=(0, 14))

        self._algorithm_label_to_name = {
            algorithm.display_name: name for name, algorithm in self._alignment_algorithms.items()
        }
        algorithm_labels = list(self._algorithm_label_to_name)
        default_algorithm = self._alignment_algorithms.get("coordinate_scan") or next(
            iter(self._alignment_algorithms.values())
        )
        self.algorithm_var = tk.StringVar(value=default_algorithm.display_name)
        self.algorithm_status_var = tk.StringVar(value="Algorithm: idle")

        ttk.Label(panel, text="Algorithm").grid(row=1, column=0, padx=(0, 4), sticky="w")
        ttk.Combobox(
            panel,
            textvariable=self.algorithm_var,
            values=algorithm_labels,
            state="readonly",
            width=44,
        ).grid(row=1, column=1, columnspan=2, padx=(0, 8), sticky="w")
        ttk.Button(panel, text="Run algorithm", command=self._run_selected_algorithm).grid(
            row=1, column=3, padx=(0, 8), sticky="w"
        )
        ttk.Button(panel, text="Show", command=self._show_selected_algorithm).grid(
            row=1, column=4, padx=(0, 8), sticky="w"
        )
        ttk.Label(panel, textvariable=self.algorithm_status_var).grid(
            row=1, column=5, padx=(0, 12), sticky="w"
        )

        self.received_power_var = tk.StringVar(value="Received power: n/a")
        self.total_efficiency_var = tk.StringVar(value="Coupling total: n/a")
        self.mode_efficiency_var = tk.StringVar(value="Mode match: n/a")
        self.power_percent_var = tk.StringVar(value="RECEIVED: n/a")
        self.source_height_var = tk.StringVar(value="Source height error: n/a")
        self.detector_height_var = tk.StringVar(value="Detector height error: n/a")
        self.best_power_var = tk.StringVar(value="Best so far: n/a")
        self.best_offsets_var = tk.StringVar(value="Best ball offsets: n/a")

        self.lens_offsets_var = tk.StringVar(value="Ball pose errors: n/a")

        ttk.Label(panel, textvariable=self.power_percent_var, font=("Segoe UI", 14, "bold")).grid(
            row=0, column=9, columnspan=2, padx=(0, 16), sticky="w"
        )
        ttk.Label(panel, textvariable=self.source_height_var).grid(row=0, column=11, padx=(0, 12), sticky="w")
        ttk.Label(panel, textvariable=self.detector_height_var).grid(row=0, column=12, padx=(0, 12), sticky="w")
        ttk.Label(panel, textvariable=self.received_power_var, font=("Segoe UI", 10, "bold")).grid(row=1, column=6, padx=(0, 12), sticky="w")
        ttk.Label(panel, textvariable=self.total_efficiency_var, font=("Segoe UI", 10, "bold")).grid(row=1, column=7, padx=(0, 12), sticky="w")
        ttk.Label(panel, textvariable=self.mode_efficiency_var, font=("Segoe UI", 10, "bold")).grid(row=1, column=8, padx=(0, 12), sticky="w")
        ttk.Label(panel, textvariable=self.lens_offsets_var).grid(row=2, column=0, columnspan=6, sticky="w")
        ttk.Label(panel, textvariable=self.best_power_var, font=("Segoe UI", 10, "bold")).grid(row=2, column=6, padx=(0, 12), sticky="w")
        ttk.Label(panel, textvariable=self.best_offsets_var).grid(row=2, column=7, columnspan=8, sticky="w")

        self._alignment_ui_ready = True

    def _parse_alignment_controls(self) -> tuple[int, float, float]:
        seed = int(self.seed_var.get().strip())
        source_detector_tolerance_um = float(self.source_detector_tolerance_var.get().strip())
        lens_pose_tolerance_um = float(self.lens_tolerance_var.get().strip())
        source_detector_tolerance = source_detector_tolerance_um / 1e6
        lens_pose_tolerance = lens_pose_tolerance_um / 1e6
        _validate_tolerance("source/detector", source_detector_tolerance)
        _validate_tolerance("lens pose", lens_pose_tolerance)
        return seed, source_detector_tolerance, lens_pose_tolerance

    def _capture_nominal_ball_poses(self) -> None:
        self._nominal_ball_poses = self.current_poses()

    def _ensure_nominal_ball_poses(self) -> None:
        if len(self._nominal_ball_poses) != len(self.balls):
            self._capture_nominal_ball_poses()

    def current_poses(self) -> tuple[LensPose, ...]:
        return tuple((ball.x_offset, ball.y_offset, ball.position) for ball in self.balls)

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
        )
        if push_undo:
            self._push_undo()
        self.sources[0].x_offset = scramble.source_x_offset
        self.sources[0].y_offset = scramble.source_y_offset
        self.tapers[0].x_offset = scramble.taper_x_offset
        self.tapers[0].y_offset = scramble.taper_y_offset
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

    def _rescramble_alignment_errors(self) -> None:
        try:
            (
                self.alignment_seed,
                self.source_detector_tolerance,
                self.lens_pose_tolerance,
            ) = self._parse_alignment_controls()
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

        if results and results[0].taper_results:
            taper_result = results[0].taper_results[0]
            received_power = taper_result.received_power
            mode_efficiency = taper_result.mode_efficiency
            total_efficiency = received_power / source.power if source.power > 0 else 0.0
            warnings.extend(results[0].warnings)
            warnings.extend(taper_result.warnings)

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

    def _update_alignment_readout(self, evaluation: AlignmentEvaluation) -> None:
        if not self._alignment_ui_ready:
            return
        received_mw = evaluation.received_power * 1e3
        received_percent = evaluation.total_efficiency * 100
        mode_percent = evaluation.mode_efficiency * 100
        self.power_percent_var.set(
            f"RECEIVED: {received_percent:.6g}%  |  MODE: {mode_percent:.6g}%  |  {received_mw:.6g} mW"
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

        if self._best_evaluation is None or evaluation.received_power > self._best_evaluation.received_power:
            self._best_evaluation = evaluation
        best = self._best_evaluation
        self.best_power_var.set(f"Best so far: {best.received_power * 1e3:.6g} mW")
        self.best_offsets_var.set(
            f"Best ball pose errors: {self._format_lens_pose_offsets(best.ball_pose_offsets)}"
        )

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
        for ball in self.balls:
            ball.x_offset = DEFAULT_INITIAL_BALL_X_OFFSET
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
        algorithm = get_algorithm(algorithm_name)
        initial_poses = self.current_poses()
        device = self.create_alignment_device(update_display=update_display)
        result = algorithm.run(device)
        if len(result.final_poses) != len(self.balls):
            raise ValueError("algorithm result has the wrong number of lens poses")
        return algorithm, result, device.move_history(), initial_poses

    def _set_algorithm_complete_status(self, algorithm, result, evaluation: AlignmentEvaluation) -> None:
        detail = (
            f"{algorithm.display_name}: {result.move_count} moves, {result.evaluations} reads, "
            f"best {evaluation.received_power * 1e3:.6g} mW"
        )
        if result.message:
            detail = f"{detail} | {result.message}"
        if self._alignment_ui_ready:
            self.algorithm_status_var.set(detail)
        self.status_var.set(
            f"{algorithm.display_name} complete: {evaluation.received_power * 1e3:.6g} mW received."
        )

    def run_alignment_algorithm(self, name: str | None = None) -> AlignmentEvaluation:
        self._cancel_algorithm_animation()
        undo_snapshot = self._layout_snapshot()
        algorithm, result, _moves, initial_poses = self._solve_alignment_algorithm(name, update_display=False)
        if result.final_poses != initial_poses:
            self._push_undo_snapshot(undo_snapshot)
        self._apply_lens_poses(result.final_poses)

        evaluation = self._run_alignment_simulation(update_report=True)
        self._set_algorithm_complete_status(algorithm, result, evaluation)
        return evaluation

    def show_alignment_algorithm(
        self,
        name: str | None = None,
        delay_ms: int = DEFAULT_ALGORITHM_SHOW_DELAY_MS,
    ) -> None:
        self._cancel_algorithm_animation()
        undo_snapshot = self._layout_snapshot()
        algorithm, result, moves, initial_poses = self._solve_alignment_algorithm(name, update_display=False)
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
            if self._alignment_ui_ready:
                self.algorithm_status_var.set(
                    f"Showing {algorithm.display_name}: {index + 1}/{len(move_list)}, "
                    f"{evaluation.received_power * 1e3:.6g} mW"
                )

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
