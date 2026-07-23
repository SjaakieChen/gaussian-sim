"""Replay the migration v6 vision workflow against the standard images.

This is an offline simulator. It does not touch hardware. It uses the saved
standard reviewed feature selections as the simulated recognition result, calls
the same v6 Python planning functions used by YASE, and applies the returned
moves to an in-memory machine-position model.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from migrations.migration_v6.python_vision_geometry.v6_offset_workflow import (  # noqa: E402
    AXIS_FOR_STAGE,
    CAPTURE_SPECS,
    OFFSET_SPECS,
    SCHEMA_VERSION,
    STAGE_FOR_AXIS,
    V6VisionReviewRecordStep,
    run_v6_vision_workflow,
    validate_reviewed_capture_session,
)


JsonDict = dict[str, Any]

MIGRATION = ROOT / "migrations" / "migration_v6"
STANDARD_POSITIONS_PATH = MIGRATION / "standard_positions.json"
STANDARD_BASELINE_DIR = MIGRATION / "standard_positions_v4" / "vision_baselines"
STANDARD_IMAGE_ROOT = ROOT / "Standard position images" / "v4"
DEFAULT_TRACE_NAME = "v6_standard_workflow_simulation_trace.json"

STAGE_ORDER = [
    "Camera_X",
    "Camera_Z",
    "Zoom",
    "Camera_Y",
    "Align_X1",
    "Align_Z1",
    "Align_Y1",
    "Align_X2",
    "Align_Z2",
    "Align_Y2",
]

FULL_MAIN_SEQUENCE: list[tuple[str, str]] = [
    ("init", ""),
    ("move", "1.0"),
    ("move", "1.1"),
    ("move", "2.1"),
    ("converge", "2.1.1"),
    ("transition", "2.1_to_2.4"),
    ("capture", "2.4.1"),
    ("transition", "2.4_to_2.5"),
    ("converge", "2.5.1"),
    ("transition", "2.5_to_2.6"),
    ("converge", "2.6.1"),
    ("move", "3.0"),
    ("move", "3.1"),
    ("move", "4.1"),
    ("converge", "4.1.1"),
    ("transition", "4.1_to_4.4"),
    ("capture", "4.4.1"),
    ("transition", "4.4_to_4.5"),
    ("converge", "4.5.1"),
    ("transition", "4.5_to_4.6.2"),
    ("converge", "4.6.2"),
    ("verify", ""),
]

BALL_1_IDS = {"1.0", "1.1", "2.1", "2.4", "2.5", "2.6"}
BALL_2_IDS = {"3.0", "3.1", "4.1", "4.4", "4.5", "4.6.2"}


@dataclass
class SimulatorConfig:
    workflow_target: str = "all"
    headless: bool = False
    output_path: Path | None = None
    memory_path: Path | None = None
    coarse_shift_x_px: float = 0.0
    coarse_shift_y_px: float = 0.0
    fine_shift_x_px: float = 0.0
    fine_shift_y_px: float = 0.0
    side_shift_y_px: float = 0.0
    auto_advance_ms: int = 0
    popup_scope: str = "vision"
    baseline_dir: Path = STANDARD_BASELINE_DIR
    baseline_replacements: dict[str, Path] = field(default_factory=dict)
    baseline_backup_root: Path = ROOT / "tmp" / "v6_baseline_backups"


@dataclass
class TargetPixelResidual:
    coarse_x_px: float = 0.0
    coarse_y_px: float = 0.0
    fine_x_px: float = 0.0
    fine_y_px: float = 0.0
    side_full_y_px: float = 0.0

    def snapshot(self) -> JsonDict:
        return {
            "coarse_x_px": self.coarse_x_px,
            "coarse_y_px": self.coarse_y_px,
            "fine_x_px": self.fine_x_px,
            "fine_y_px": self.fine_y_px,
            "side_full_y_px": self.side_full_y_px,
        }


class V6StandardWorkflowSimulator:
    def __init__(self, config: SimulatorConfig) -> None:
        self.config = config
        self.standard_payload = json.loads(STANDARD_POSITIONS_PATH.read_text(encoding="utf-8"))
        self.positions = {str(position["id"]): position for position in self.standard_payload["positions"]}
        self.clearance_y_by_tower = tower_clearance_y_by_tower(self.positions.values())
        self.trace: list[JsonDict] = []
        self.machine_positions_um: JsonDict = {
            "camera": {"machine_x_um": 0.0, "machine_y_um": 0.0, "machine_z_um": 0.0},
            "tower_1": {"machine_x_um": 0.0, "machine_y_um": 0.0, "machine_z_um": 0.0},
            "tower_2": {"machine_x_um": 0.0, "machine_y_um": 0.0, "machine_z_um": 0.0},
            "zoom": {"zoom_um": 0.0},
        }
        self.camera_settings: JsonDict = {
            "exposure": None,
            "Illu_Coax": 0.0,
            "Illu_1": 0.0,
            "Illu_2": 0.0,
            "source": "reapplied_standard_position_before_capture_confirmation",
        }
        self.residuals = {
            "ball_1": TargetPixelResidual(
                coarse_x_px=config.coarse_shift_x_px,
                coarse_y_px=config.coarse_shift_y_px,
                fine_x_px=config.fine_shift_x_px,
                fine_y_px=config.fine_shift_y_px,
                side_full_y_px=config.side_shift_y_px,
            ),
            "ball_2": TargetPixelResidual(
                coarse_x_px=config.coarse_shift_x_px,
                coarse_y_px=config.coarse_shift_y_px,
                fine_x_px=config.fine_shift_x_px,
                fine_y_px=config.fine_shift_y_px,
                side_full_y_px=config.side_shift_y_px,
            ),
        }
        self.viewer = (
            None
            if config.headless
            else WorkflowPopupViewer(config.auto_advance_ms, popup_scope=config.popup_scope)
        )

    def run(self) -> JsonDict:
        for capture_id, replacement_path in self.config.baseline_replacements.items():
            backup_path = replace_simulator_baseline(
                capture_id,
                replacement_path,
                baseline_dir=self.config.baseline_dir,
                backup_root=self.config.baseline_backup_root,
            )
            self.add_step(
                {
                    "kind": "baseline_replacement",
                    "capture_id": capture_id,
                    "replacement_path": str(replacement_path),
                    "backup_path": str(backup_path),
                    "explanation": "Explicit baseline replacement with the previous file backed up first.",
                }
            )
        for kind, value in filtered_sequence(self.config.workflow_target):
            if kind == "init":
                self.initialize_memory()
            elif kind == "move":
                self.move_to_standard_position(value)
            elif kind == "capture":
                self.capture_review_record(value)
            elif kind == "offset":
                self.offset_correction(value)
            elif kind == "converge":
                self.convergence_loop(value)
            elif kind == "transition":
                self.transition_move_loop(value)
            elif kind == "verify":
                self.final_verification()
            else:
                raise ValueError(f"unsupported simulator sequence step {kind!r}")

        payload = {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "action": "v6_standard_workflow_simulation",
            "workflow_target": self.config.workflow_target,
            "standard_positions_path": str(STANDARD_POSITIONS_PATH),
            "standard_baseline_dir": str(self.config.baseline_dir),
            "memory_path": str(self.config.memory_path),
            "final_machine_positions_um": deepcopy_json(self.machine_positions_um),
            "final_pixel_residuals": self.pixel_residuals_snapshot(),
            "trace": self.trace,
        }
        if self.config.output_path:
            self.config.output_path.parent.mkdir(parents=True, exist_ok=True)
            self.config.output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        if self.viewer is not None:
            self.viewer.close()
        return payload

    def initialize_memory(self) -> None:
        result = run_v6_vision_workflow(
            {
                "schema_version": SCHEMA_VERSION,
                "command": "init",
                "memory_path": str(self.config.memory_path),
                "standard_positions_path": str(STANDARD_POSITIONS_PATH),
                "standard_baseline_dir": str(self.config.baseline_dir),
                "output_path": str(self.config.memory_path),
            }
        )
        self.add_step(
            {
                "kind": "memory_init",
                "subsequence": "SUB_V6SequenceMemoryInit_ReadOnly",
                "result": result,
                "explanation": "Memory starts empty and points at the standard positions and baseline reviewed features.",
            }
        )

    def move_to_standard_position(self, position_id: str) -> None:
        position = self.positions[position_id]
        moves = standard_position_moves(position, self.clearance_y_by_tower)
        applied: list[JsonDict] = []
        preview_positions = deepcopy_json(self.machine_positions_um)
        for move in moves:
            current = stage_value(preview_positions, move["stage"])
            applied_move = {
                **move,
                "delta_um": move["target_um"] - current,
                "move_mode": "Absolute",
                "velocity_class": "medium_approach",
            }
            set_stage_target(preview_positions, move["stage"], move["target_um"])
            applied.append(applied_move)

        if self.viewer is not None:
            self.viewer.confirm_standard_position(position, applied)

        for move in applied:
            self.apply_stage_target(move["stage"], move["target_um"])
        self.apply_camera_settings(position)
        self.add_step(
            {
                "kind": "standard_position_move",
                "subsequence": standard_position_subsequence_name(position),
                "position_id": position_id,
                "label": position.get("label"),
                "planned_moves": applied,
                "camera_settings": deepcopy_json(self.camera_settings),
                "machine_positions_after_um": deepcopy_json(self.machine_positions_um),
                "explanation": "Hardcoded standard-position approach: absolute camera/tower/zoom targets plus exposure and light settings.",
            }
        )

    def capture_review_record(self, capture_id: str) -> None:
        baseline = load_baseline(capture_id, self.config.baseline_dir)
        target = str(CAPTURE_SPECS[capture_id]["target"])
        live_session = shifted_live_session(capture_id, baseline, self.residuals[target])
        image_path = resolve_standard_image_path(baseline)
        position = self.positions[str(CAPTURE_SPECS[capture_id]["position_id"])]
        self.apply_camera_settings(position)
        if self.viewer is not None:
            self.viewer.confirm_capture_gate(
                capture_id,
                image_path,
                camera_settings=self.camera_settings,
                machine_positions_um=self.machine_positions_um,
            )
        capture_params = {
            "schema_version": SCHEMA_VERSION,
            "command": "record_capture",
            "capture_id": capture_id,
            "image_path": str(image_path),
            "memory_path": str(self.config.memory_path),
            "memory_output_path": str(self.config.memory_path),
            "standard_positions_path": str(STANDARD_POSITIONS_PATH),
            "standard_baseline_dir": str(self.config.baseline_dir),
            "machine_positions_before_grab_um": deepcopy_json(self.machine_positions_um),
            "machine_positions_after_grab_um": deepcopy_json(self.machine_positions_um),
            "camera_settings": deepcopy_json(self.camera_settings),
        }
        if self.config.headless:
            capture_params["review_session"] = live_session
            result = run_v6_vision_workflow(capture_params)
        else:
            capture_params["initial_review_session"] = live_session
            result = V6VisionReviewRecordStep().run(capture_params)
        if result.get("ok") is not True:
            raise RuntimeError(str(result.get("status") or f"capture {capture_id} failed"))
        recorded_session = as_dict(
            as_dict(as_dict(self.load_memory()).get("capture_records")).get(capture_id)
        ).get("session")
        if isinstance(recorded_session, dict):
            live_session = recorded_session

        self.add_step(
            {
                "kind": "capture_review_record",
                "subsequence": f"SUB_V6CaptureReviewRecord_{capture_id}_ReadOnly",
                "capture_id": capture_id,
                "position_id": result["position_id"],
                "target": target,
                "view": result["view"],
                "image_path": str(image_path),
                "standard_feature_summary": summarize_session_features(baseline),
                "simulated_live_feature_summary": summarize_session_features(live_session),
                "pixel_residuals_before_capture": self.residuals[target].snapshot(),
                "machine_positions_um": deepcopy_json(self.machine_positions_um),
                "record_result": result,
                "standard_session": baseline,
                "live_session": live_session,
                "explanation": "Fresh reviewed capture recorded with the exact stable in-memory machine pose.",
            }
        )

    def offset_correction(self, capture_id: str) -> None:
        target = str(CAPTURE_SPECS[capture_id]["target"])
        before = self.residuals[target].snapshot()
        result = run_v6_vision_workflow(
            {
                "schema_version": SCHEMA_VERSION,
                "command": "next_offset_correction",
                "capture_id": capture_id,
                "memory_path": str(self.config.memory_path),
                "standard_positions_path": str(STANDARD_POSITIONS_PATH),
                "standard_baseline_dir": str(self.config.baseline_dir),
                "machine_positions_um": deepcopy_json(self.machine_positions_um),
            }
        )
        applied = []
        if result.get("ok") is True and result.get("action") == "offset_correction_move":
            for move in result.get("planned_moves", []):
                if self.viewer is not None:
                    self.viewer.confirm_guarded_move(
                        f"SUB_V6OffsetCorrection_{capture_id}_Guarded",
                        move,
                        result,
                        velocity_class="slow_offset_correction",
                    )
                applied.append(self.apply_planned_move(move, velocity_class="slow_offset_correction"))
            self.update_pixel_residuals_from_offset(capture_id, result)
        self.add_step(
            {
                "kind": "offset_correction",
                "subsequence": f"SUB_V6OffsetCorrection_{capture_id}_Guarded",
                "capture_id": capture_id,
                "target": target,
                "result": result,
                "applied_moves": applied,
                "pixel_residuals_before": before,
                "pixel_residuals_after": self.residuals[target].snapshot(),
                "machine_positions_after_um": deepcopy_json(self.machine_positions_um),
                "explanation": "Python proposes bounded moves; the simulator applies them to the in-memory tower model.",
            }
        )

    def convergence_loop(self, capture_id: str) -> None:
        for attempt in range(1, 10):
            self.capture_review_record(capture_id)
            self.offset_correction(capture_id)
            result = as_dict(self.trace[-1].get("result"))
            if result.get("action") == "no_offset_correction_required":
                return
            if result.get("ok") is not True:
                raise RuntimeError(str(result.get("status") or f"{capture_id} convergence failed"))
        raise RuntimeError(
            f"{capture_id} did not converge after eight correction attempts "
            "and a final fresh verification capture"
        )

    def final_verification(self) -> None:
        result = run_v6_vision_workflow(
            {
                "schema_version": SCHEMA_VERSION,
                "command": "verify_final_geometry",
                "memory_path": str(self.config.memory_path),
                "standard_positions_path": str(STANDARD_POSITIONS_PATH),
                "standard_baseline_dir": str(self.config.baseline_dir),
            }
        )
        self.add_step(
            {
                "kind": "final_verification",
                "subsequence": "SUB_V6FinalVerification_ReadOnly",
                "result": result,
                "machine_positions_um": deepcopy_json(self.machine_positions_um),
                "explanation": "Read-only proof of 289 um, 989 um, and 700 um center spacing.",
            }
        )
        if result.get("action") != "final_geometry_verified":
            raise RuntimeError(str(result.get("status") or "final geometry verification failed"))

    def transition_move_loop(self, transition_id: str) -> None:
        for call_index in range(1, 40):
            result = run_v6_vision_workflow(
                {
                    "schema_version": SCHEMA_VERSION,
                    "command": "next_transition_move",
                    "transition_id": transition_id,
                    "memory_path": str(self.config.memory_path),
                    "standard_positions_path": str(STANDARD_POSITIONS_PATH),
                    "standard_baseline_dir": str(self.config.baseline_dir),
                    "machine_positions_um": deepcopy_json(self.machine_positions_um),
                }
            )
            applied = []
            if result.get("ok") is True and result.get("action") == "transition_move":
                for move in result.get("planned_moves", []):
                    if self.viewer is not None:
                        self.viewer.confirm_guarded_move(
                            f"SUB_V6TransitionMove_{transition_id}_Guarded",
                            move,
                            result,
                            velocity_class="medium_transition",
                        )
                    applied.append(self.apply_planned_move(move, velocity_class="medium_transition"))
            self.add_step(
                {
                    "kind": "transition_move",
                    "subsequence": f"SUB_V6TransitionMove_{transition_id}_Guarded",
                    "transition_id": transition_id,
                    "call_index": call_index,
                    "result": result,
                    "applied_moves": applied,
                    "machine_positions_after_um": deepcopy_json(self.machine_positions_um),
                    "explanation": "One transition call returns at most one guarded move, then YASE loops until complete.",
                }
            )
            if result.get("action") == "transition_complete":
                return
            if result.get("ok") is not True:
                raise RuntimeError(
                    str(result.get("status") or f"transition {transition_id} failed")
                )
        raise RuntimeError(f"transition {transition_id} did not complete inside the simulator loop limit")

    def apply_planned_move(self, move: JsonDict, *, velocity_class: str) -> JsonDict:
        stage = str(move["stage"])
        target_um = float(move["target_um"])
        applied = {
            "stage": stage,
            "target_um": target_um,
            "delta_um": float(move["delta_um"]),
            "move_mode": str(move.get("move_mode") or "Absolute"),
            "phase": str(move.get("phase") or ""),
            "confirm_text": str(move.get("confirm_text") or ""),
            "velocity_class": velocity_class,
        }
        self.apply_stage_target(stage, target_um)
        return applied

    def update_pixel_residuals_from_offset(self, capture_id: str, result: JsonDict) -> None:
        target = str(CAPTURE_SPECS[capture_id]["target"])
        residual = self.residuals[target]
        diagnostics = as_dict(result.get("diagnostics"))
        correction_kind = str(diagnostics.get("correction_kind") or "")
        correction = as_dict(diagnostics.get("correction"))
        um_per_pixel = float(as_dict(correction.get("scale_context")).get("um_per_pixel") or 0.0)
        if um_per_pixel <= 0.0:
            return
        for move in result.get("planned_moves", []):
            stage = str(move.get("stage") or "")
            delta_um = float(move.get("delta_um") or 0.0)
            stage_name, axis = AXIS_FOR_STAGE[stage]
            if correction_kind == "coarse_top":
                if axis == "machine_x_um":
                    residual.coarse_x_px += delta_um / um_per_pixel
                elif axis == "machine_z_um":
                    residual.coarse_y_px -= delta_um / um_per_pixel
            elif correction_kind == "top_fine":
                if axis == "machine_x_um":
                    residual.fine_x_px += delta_um / um_per_pixel
                elif axis == "machine_z_um":
                    residual.fine_y_px -= delta_um / um_per_pixel
            elif (
                correction_kind == "side_mirror_y"
                and axis == "machine_y_um"
                and stage_name.startswith("tower_")
            ):
                residual.side_full_y_px += delta_um / um_per_pixel

    def apply_stage_target(self, stage: str, target_um: float) -> None:
        set_stage_target(self.machine_positions_um, stage, target_um)

    def apply_camera_settings(self, position: JsonDict) -> None:
        settings = as_dict(position.get("camera_settings"))
        exposure = setting_value(settings, "exposure")
        if exposure is not None:
            self.camera_settings["exposure"] = exposure
        self.camera_settings["Illu_Coax"] = 0.9
        self.camera_settings["Illu_1"] = 0.9
        self.camera_settings["Illu_2"] = 0.9
        self.camera_settings["source"] = (
            "reapplied_standard_position_before_capture_confirmation"
        )

    def add_step(self, step: JsonDict) -> None:
        step["step_index"] = len(self.trace) + 1
        self.trace.append(strip_large_sessions_for_trace(step))
        if self.viewer is not None:
            self.viewer.show_step(step)

    def load_memory(self) -> JsonDict:
        return json.loads(Path(self.config.memory_path).read_text(encoding="utf-8"))

    def write_memory(self, memory: JsonDict) -> None:
        Path(self.config.memory_path).write_text(json.dumps(memory, indent=2, sort_keys=True), encoding="utf-8")

    def pixel_residuals_snapshot(self) -> JsonDict:
        return {target: residual.snapshot() for target, residual in self.residuals.items()}


class WorkflowPopupViewer:
    def __init__(self, auto_advance_ms: int = 0, *, popup_scope: str = "vision") -> None:
        self.auto_advance_ms = auto_advance_ms
        self.popup_scope = popup_scope
        self.tk = None
        self.root = None
        try:
            import tkinter as tk

            self.tk = tk
            self.root = tk.Tk()
            self.root.withdraw()
        except Exception as exc:
            print(f"Tkinter viewer could not start; continuing headless: {exc}", file=sys.stderr)
            self.tk = None
            self.root = None

    def should_show_yase_gates(self) -> bool:
        return self.popup_scope in {"yase", "all"}

    def confirm_standard_position(self, position: JsonDict, moves: list[JsonDict]) -> None:
        if not self.should_show_yase_gates():
            return
        move_lines = [
            f"{move['stage']} -> {float(move['target_um']):.6g} um "
            f"(delta {float(move['delta_um']):.6g} um)"
            for move in moves
        ]
        self._show_operator_dialog(
            title=standard_position_subsequence_name(position),
            message=standard_position_gate_text(position),
            proceed_text="Move",
            details=[
                "YASE sequence: " + standard_position_subsequence_name(position),
                "Simulated statements after confirmation:",
                *move_lines,
                "Set cam_12_ExpTime plus Illu_Coax, Illu_1, and Illu_2.",
            ],
        )

    def confirm_capture_gate(
        self,
        capture_id: str,
        image_path: Path,
        *,
        camera_settings: JsonDict,
        machine_positions_um: JsonDict,
    ) -> None:
        if not self.should_show_yase_gates():
            return
        self._show_operator_dialog(
            title=f"SUB_V6CaptureReviewRecord_{capture_id}_ReadOnly",
            message=capture_gate_text(capture_id),
            proceed_text="Capture current",
            details=[
                "The real YASE dialog is modal and does not permit manual positioning.",
                "Cancel it if adjustment is needed, then rerun only the capture/convergence step.",
                "The machine sequence reapplies standard exposure/lights before confirmation.",
                "The simulator uses this standard image as the captured CAM_12 frame:",
                str(image_path),
                "Camera settings: " + json.dumps(camera_settings, sort_keys=True),
                "Simulated queried pose: " + json.dumps(machine_positions_um, sort_keys=True),
            ],
            image_path=image_path,
        )

    def confirm_guarded_move(
        self,
        subsequence: str,
        move: JsonDict,
        result: JsonDict,
        *,
        velocity_class: str,
    ) -> None:
        if not self.should_show_yase_gates():
            return
        confirm_text = str(move.get("confirm_text") or result.get("confirm_text1") or "Confirm v6 move.")
        apply_sequence = (
            "SUB_V6ApplyApproachMove_Guarded"
            if velocity_class == "medium_transition"
            else "SUB_V6ApplyOffsetCorrectionMove_Guarded"
        )
        self._show_operator_dialog(
            title=apply_sequence,
            message=confirm_text,
            proceed_text="Move",
            details=[
                "Calling sequence: " + subsequence,
                f"Stage: {move.get('stage')}",
                f"Absolute target: {float(move.get('target_um', 0.0)):.6g} um",
                f"Delta from current simulated pose: {float(move.get('delta_um', 0.0)):.6g} um",
                "Velocity class in simulator: " + velocity_class,
            ],
        )

    def review_capture(self, capture_id: str, image_path: Path, initial_session: JsonDict) -> JsonDict | None:
        from migrations.migration_v6.vision_recognition_lab import run_vision_recognition_lab_session

        result = run_vision_recognition_lab_session(
            image_path,
            initial_session=initial_session,
            capture_id=capture_id,
        )
        if result.get("ok") is not True:
            raise RuntimeError(str(result.get("status") or f"review cancelled for {capture_id}"))
        return result

    def show_step(self, step: JsonDict) -> None:
        if self.popup_scope != "all":
            return
        if step.get("kind") in {
            "capture_review_record",
            "offset_correction",
            "standard_position_move",
            "transition_move",
        }:
            return
        if self.tk is None or self.root is None:
            return
        tk = self.tk
        top = tk.Toplevel(self.root)
        top.title(window_title(step))
        top.geometry("+80+80")

        text = step_text(step)
        label = tk.Label(top, text=text, justify="left", anchor="w", padx=12, pady=10)
        label.pack(fill="x")

        if step.get("kind") == "capture_review_record":
            self.add_image_canvas(top, step)

        button_text = "Apply simulated move" if step.get("kind") in {"offset_correction", "transition_move"} else "Next"
        button = tk.Button(top, text=button_text, command=top.destroy)
        button.pack(pady=10)
        if self.auto_advance_ms > 0:
            top.after(self.auto_advance_ms, top.destroy)
        top.wait_window()

    def _show_operator_dialog(
        self,
        *,
        title: str,
        message: str,
        proceed_text: str,
        details: list[str],
        image_path: Path | None = None,
    ) -> None:
        if self.tk is None or self.root is None:
            return
        tk = self.tk
        top = tk.Toplevel(self.root)
        top.title(title)
        top.geometry("+80+80")
        top.columnconfigure(0, weight=1)
        accepted = {"value": False}

        label = tk.Label(
            top,
            text=message,
            justify="left",
            anchor="w",
            wraplength=900,
            padx=12,
            pady=10,
        )
        label.grid(row=0, column=0, sticky="ew")
        if details:
            detail = tk.Text(top, width=120, height=min(max(len(details), 5), 12), wrap="word")
            detail.insert("1.0", "\n".join(details))
            detail.configure(state="disabled")
            detail.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 8))
        if image_path is not None:
            self.add_image_preview(top, image_path, row=2)

        buttons = tk.Frame(top)
        buttons.grid(row=3, column=0, sticky="e", padx=12, pady=(0, 12))

        def abort() -> None:
            top.destroy()

        def proceed() -> None:
            accepted["value"] = True
            top.destroy()

        tk.Button(buttons, text="Abort", command=abort).pack(side="left", padx=(0, 8))
        tk.Button(buttons, text=proceed_text, command=proceed).pack(side="left")
        top.protocol("WM_DELETE_WINDOW", abort)
        if self.auto_advance_ms > 0:
            top.after(self.auto_advance_ms, proceed)
        top.wait_window()
        if not accepted["value"]:
            raise RuntimeError(f"operator aborted simulator preview at {title}")

    def add_image_preview(self, top: Any, image_path: Path, *, row: int) -> None:
        tk = self.tk
        if tk is None:
            return
        if not image_path.is_file():
            tk.Label(top, text=f"Image not found: {image_path}", padx=12, pady=8).grid(
                row=row,
                column=0,
                sticky="ew",
            )
            return
        try:
            image = tk.PhotoImage(master=top, file=str(image_path))
        except Exception as exc:
            tk.Label(top, text=f"Could not load image: {exc}", padx=12, pady=8).grid(
                row=row,
                column=0,
                sticky="ew",
            )
            return
        subsample = max(1, math.ceil(image.width() / 900), math.ceil(image.height() / 520))
        shown = image.subsample(subsample, subsample) if subsample > 1 else image
        canvas = tk.Canvas(top, width=shown.width(), height=shown.height(), background="#111111")
        canvas.grid(row=row, column=0, padx=12, pady=(0, 8), sticky="n")
        canvas.create_image(0, 0, image=shown, anchor="nw")
        top._sim_preview_images = (image, shown)

    def add_image_canvas(self, top: Any, step: JsonDict) -> None:
        tk = self.tk
        if tk is None:
            return
        image_path = Path(str(step.get("image_path") or ""))
        if not image_path.is_file():
            tk.Label(top, text=f"Image not found: {image_path}", padx=12, pady=8).pack(fill="x")
            return
        try:
            image = tk.PhotoImage(master=top, file=str(image_path))
        except Exception as exc:
            tk.Label(top, text=f"Could not load image: {exc}", padx=12, pady=8).pack(fill="x")
            return
        subsample = max(1, math.ceil(image.width() / 1180), math.ceil(image.height() / 720))
        shown = image.subsample(subsample, subsample) if subsample > 1 else image
        scale = 1.0 / subsample
        canvas = tk.Canvas(top, width=shown.width(), height=shown.height(), background="#111111")
        canvas.pack(padx=12, pady=8)
        canvas.create_image(0, 0, image=shown, anchor="nw")
        draw_session_overlays(canvas, step.get("standard_session") or {}, scale, color="#00b7ff", tag_prefix="std")
        draw_session_overlays(canvas, step.get("live_session") or {}, scale, color="#ff4040", tag_prefix="live")
        canvas.create_text(
            12,
            12,
            text="cyan = standard reviewed feature, red = simulated live detection",
            anchor="nw",
            fill="#ffffff",
            font=("Segoe UI", 10, "bold"),
        )
        top._sim_images = (image, shown)

    def close(self) -> None:
        if self.root is not None:
            self.root.destroy()


def run_standard_workflow_simulation(config: SimulatorConfig) -> JsonDict:
    if config.memory_path is not None:
        config.memory_path.parent.mkdir(parents=True, exist_ok=True)
        return V6StandardWorkflowSimulator(config).run()
    with tempfile.TemporaryDirectory(prefix="v6_standard_workflow_sim_") as tmp:
        config.memory_path = Path(tmp) / "v6_vision_memory_sim.json"
        return V6StandardWorkflowSimulator(config).run()


def replace_simulator_baseline(
    capture_id: str,
    replacement_path: Path,
    *,
    baseline_dir: Path = STANDARD_BASELINE_DIR,
    backup_root: Path = ROOT / "tmp" / "v6_baseline_backups",
) -> Path:
    """Explicitly replace one baseline after backing up the previous JSON."""

    if capture_id not in CAPTURE_SPECS:
        raise ValueError(f"unsupported V6 capture id for baseline replacement: {capture_id}")
    replacement_path = Path(replacement_path)
    if not replacement_path.is_file():
        raise FileNotFoundError(f"replacement baseline does not exist: {replacement_path}")
    replacement = json.loads(replacement_path.read_text(encoding="utf-8"))
    if replacement.get("ok") is not True or not as_dict(replacement.get("selected_recognition")):
        raise ValueError("replacement baseline must be a saved reviewed session with selected_recognition")
    replacement_capture_id = str(replacement.get("capture_id") or "").strip()
    if replacement_capture_id and replacement_capture_id != capture_id:
        raise ValueError(
            f"replacement baseline capture_id {replacement_capture_id!r} does not match {capture_id!r}"
        )
    validate_reviewed_capture_session(capture_id, copy.deepcopy(replacement), {})
    destination = Path(baseline_dir) / f"{capture_id}.json"
    if not destination.is_file():
        raise FileNotFoundError(f"existing baseline does not exist and will not be silently created: {destination}")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = Path(backup_root) / timestamp
    backup_dir.mkdir(parents=True, exist_ok=False)
    backup_path = backup_dir / destination.name
    shutil.copy2(destination, backup_path)
    temporary = destination.with_name(f".{destination.name}.replacement.tmp")
    temporary.write_text(json.dumps(replacement, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, destination)
    return backup_path


def filtered_sequence(workflow_target: str) -> list[tuple[str, str]]:
    if workflow_target == "all":
        return FULL_MAIN_SEQUENCE
    if workflow_target not in {"ball_1", "ball_2"}:
        raise ValueError("workflow_target must be all, ball_1, or ball_2")
    allowed_position_ids = BALL_1_IDS if workflow_target == "ball_1" else BALL_2_IDS
    filtered: list[tuple[str, str]] = [("init", "")]
    for kind, value in FULL_MAIN_SEQUENCE:
        if kind == "init":
            continue
        if kind == "move" and value in allowed_position_ids:
            filtered.append((kind, value))
        elif kind == "capture" and CAPTURE_SPECS[value]["target"] == workflow_target:
            filtered.append((kind, value))
        elif kind == "offset" and OFFSET_SPECS[value]["target"] == workflow_target:
            filtered.append((kind, value))
        elif kind == "converge" and OFFSET_SPECS[value]["target"] == workflow_target:
            filtered.append((kind, value))
        elif kind == "transition" and transition_target(value) == workflow_target:
            filtered.append((kind, value))
        elif kind == "verify" and workflow_target == "all":
            filtered.append((kind, value))
    return filtered


def transition_target(transition_id: str) -> str:
    if transition_id.startswith("2."):
        return "ball_1"
    return "ball_2"


def standard_position_moves(position: JsonDict, clearance_y_by_tower: JsonDict) -> list[JsonDict]:
    raises: list[JsonDict] = []
    camera_moves: list[JsonDict] = []
    lateral_moves: list[JsonDict] = []
    lowers: list[JsonDict] = []
    machine_positions = as_dict(position.get("machine_positions_um"))

    tower_targets = []
    for tower, stage_map in (
        ("tower_1", {"x": "Align_X1", "y": "Align_Y1", "z": "Align_Z1"}),
        ("tower_2", {"x": "Align_X2", "y": "Align_Y2", "z": "Align_Z2"}),
    ):
        values = as_dict(machine_positions.get(tower))
        target_x = values.get("x")
        target_y = values.get("y")
        target_z = values.get("z")
        has_lateral_target = target_x is not None or target_z is not None
        if has_lateral_target and target_y is None:
            raise ValueError(
                f"position {position.get('id')} has a lateral target for {tower} "
                "without a machine Y clearance target"
            )
        clearance = None
        if has_lateral_target:
            clearance = max(float(clearance_y_by_tower[tower]), float(target_y))
            raises.append(
                {
                    "stage": stage_map["y"],
                    "target_um": clearance,
                    "phase": "v6_raise_tower_y_clearance",
                }
            )
        tower_targets.append(
            (stage_map, target_x, target_y, target_z, has_lateral_target, clearance)
        )

    camera = as_dict(machine_positions.get("camera"))
    for axis, stage in (("x", "Camera_X"), ("z", "Camera_Z"), ("y", "Camera_Y")):
        if camera.get(axis) is not None:
            camera_moves.append(
                {
                    "stage": stage,
                    "target_um": float(camera[axis]),
                    "phase": "v6_standard_position_approach",
                }
            )

    zoom = setting_value(as_dict(position.get("camera_settings")), "zoom")
    if zoom is not None:
        camera_moves.append(
            {
                "stage": "Zoom",
                "target_um": float(zoom),
                "phase": "v6_standard_position_approach",
            }
        )
    camera_moves.sort(key=lambda item: STAGE_ORDER.index(item["stage"]))

    for stage_map, target_x, target_y, target_z, has_lateral_target, clearance in tower_targets:
        if target_z is not None:
            lateral_moves.append(
                {
                    "stage": stage_map["z"],
                    "target_um": float(target_z),
                    "phase": "v6_standard_position_approach",
                }
            )
        if target_x is not None:
            lateral_moves.append(
                {
                    "stage": stage_map["x"],
                    "target_um": float(target_x),
                    "phase": "v6_standard_position_approach",
                }
            )
        if target_y is not None and (not has_lateral_target or float(target_y) != clearance):
            lowers.append(
                {
                    "stage": stage_map["y"],
                    "target_um": float(target_y),
                    "phase": "v6_lower_tower_y_to_standard",
                }
            )
    return raises + camera_moves + lateral_moves + lowers


def tower_clearance_y_by_tower(positions: Iterable[JsonDict]) -> JsonDict:
    result: JsonDict = {}
    for tower in ("tower_1", "tower_2"):
        values = []
        for position in positions:
            value = as_dict(as_dict(position.get("machine_positions_um")).get(tower)).get("y")
            if value is not None:
                values.append(float(value))
        result[tower] = max(values)
    return result


def shifted_live_session(capture_id: str, baseline: JsonDict, residual: TargetPixelResidual) -> JsonDict:
    session = deepcopy_json(baseline)
    if capture_id in {"2.1.1", "4.1.1"}:
        shift_selected_circles(session, dx=residual.coarse_x_px, dy=residual.coarse_y_px)
    elif capture_id in {"2.5.1", "4.5.1"}:
        shift_selected_circles(session, dx=residual.fine_x_px, dy=residual.fine_y_px)
    elif capture_id in {"2.6.1", "4.6.2"}:
        shift_selected_circles(session, dx=0.0, dy=residual.side_full_y_px)
    return session


def shift_selected_circles(session: JsonDict, *, dx: float, dy: float) -> None:
    if dx == 0.0 and dy == 0.0:
        return
    for item in selected_items(session):
        if str(item.get("shape_kind") or "").strip() != "circle":
            continue
        shape = as_dict(item.get("shape"))
        if "x" in shape:
            shape["x"] = float(shape["x"]) + dx
        if "y" in shape:
            shape["y"] = float(shape["y"]) + dy
        item["shape"] = shape


def load_baseline(capture_id: str, baseline_dir: Path = STANDARD_BASELINE_DIR) -> JsonDict:
    return json.loads((baseline_dir / f"{capture_id}.json").read_text(encoding="utf-8"))


def resolve_standard_image_path(baseline: JsonDict) -> Path:
    raw = (
        baseline.get("image_path")
        or baseline.get("standard_image_rel_path")
        or as_dict(baseline.get("official_baseline")).get("image_rel_path")
    )
    if not raw:
        return STANDARD_IMAGE_ROOT
    raw_path = Path(str(raw))
    if raw_path.is_absolute():
        return raw_path

    candidates = [
        MIGRATION / raw_path,
        ROOT / raw_path,
        STANDARD_IMAGE_ROOT / raw_path,
    ]
    parts = raw_path.parts
    if parts and parts[0] == "standard_positions_v4":
        candidates.insert(0, STANDARD_IMAGE_ROOT / Path(*parts[1:]))
    if parts and parts[0] == "newhead":
        candidates.insert(0, STANDARD_IMAGE_ROOT / raw_path)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def summarize_session_features(session: JsonDict) -> list[JsonDict]:
    features: list[JsonDict] = []
    for item in selected_items(session):
        shape_kind = str(item.get("shape_kind") or "")
        shape = as_dict(item.get("shape"))
        feature: JsonDict = {
            "shape_kind": shape_kind,
            "source": item.get("source"),
            "role": item.get("feature_role") or item.get("role") or item.get("semantic_role"),
        }
        if shape_kind == "circle":
            feature.update({"x_px": shape.get("x"), "y_px": shape.get("y"), "radius_px": shape.get("radius")})
        elif shape_kind == "rectangle":
            feature.update(rectangle_summary(shape))
        elif shape_kind == "line":
            feature.update({"x1_px": shape.get("x1"), "y1_px": shape.get("y1"), "x2_px": shape.get("x2"), "y2_px": shape.get("y2")})
        features.append(feature)
    side_reference = session.get("side_reference_line")
    if isinstance(side_reference, dict):
        features.append(
            {
                "shape_kind": "side_reference_line",
                "source": side_reference.get("source"),
                "y_px": side_reference.get("y_px"),
                "x1_px": side_reference.get("x1_px"),
                "x2_px": side_reference.get("x2_px"),
            }
        )
    return features


def rectangle_summary(shape: JsonDict) -> JsonDict:
    corners = shape.get("corners")
    if isinstance(corners, list) and len(corners) == 4:
        xs = [float(as_dict(corner)["x"]) for corner in corners]
        ys = [float(as_dict(corner)["y"]) for corner in corners]
        return {
            "center_x_px": sum(xs) / 4.0,
            "center_y_px": sum(ys) / 4.0,
            "x1_px": min(xs),
            "x2_px": max(xs),
            "y1_px": min(ys),
            "y2_px": max(ys),
        }
    x1 = float(shape.get("x1", 0.0))
    x2 = float(shape.get("x2", 0.0))
    y1 = float(shape.get("y1", 0.0))
    y2 = float(shape.get("y2", 0.0))
    return {"center_x_px": 0.5 * (x1 + x2), "center_y_px": 0.5 * (y1 + y2), "x1_px": x1, "x2_px": x2, "y1_px": y1, "y2_px": y2}


def selected_items(session: JsonDict) -> Iterable[JsonDict]:
    selected = as_dict(session.get("selected_recognition"))
    for key in sorted(selected):
        values = selected.get(key)
        if isinstance(values, list):
            for value in values:
                yield as_dict(value)


def draw_session_overlays(canvas: Any, session: JsonDict, scale: float, *, color: str, tag_prefix: str) -> None:
    for item in selected_items(session):
        roi = as_dict(item.get("roi"))
        if roi:
            canvas.create_rectangle(
                float(roi["x1"]) * scale,
                float(roi["y1"]) * scale,
                float(roi["x2"]) * scale,
                float(roi["y2"]) * scale,
                outline=color,
                dash=(4, 4),
                width=1,
                tags=tag_prefix,
            )
        shape_kind = str(item.get("shape_kind") or "")
        shape = as_dict(item.get("shape"))
        if shape_kind == "circle":
            x = float(shape["x"]) * scale
            y = float(shape["y"]) * scale
            radius = float(shape.get("radius") or 20.0) * scale
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, outline=color, width=3, tags=tag_prefix)
            canvas.create_line(x - 9, y, x + 9, y, fill=color, width=2, tags=tag_prefix)
            canvas.create_line(x, y - 9, x, y + 9, fill=color, width=2, tags=tag_prefix)
        elif shape_kind == "rectangle":
            corners = shape.get("corners")
            if isinstance(corners, list) and len(corners) == 4:
                coords: list[float] = []
                for corner in corners:
                    coords.extend([float(as_dict(corner)["x"]) * scale, float(as_dict(corner)["y"]) * scale])
                canvas.create_polygon(*coords, outline=color, fill="", width=3, tags=tag_prefix)
            else:
                canvas.create_rectangle(
                    float(shape["x1"]) * scale,
                    float(shape["y1"]) * scale,
                    float(shape["x2"]) * scale,
                    float(shape["y2"]) * scale,
                    outline=color,
                    width=3,
                    tags=tag_prefix,
                )
        elif shape_kind == "line":
            canvas.create_line(
                float(shape["x1"]) * scale,
                float(shape["y1"]) * scale,
                float(shape["x2"]) * scale,
                float(shape["y2"]) * scale,
                fill=color,
                width=3,
                tags=tag_prefix,
            )
    side_reference = session.get("side_reference_line")
    if isinstance(side_reference, dict):
        y = float(side_reference["y_px"]) * scale
        x1 = float(side_reference.get("x1_px") or 0.0) * scale
        x2 = float(side_reference.get("x2_px") or 2592.0) * scale
        canvas.create_line(x1, y, x2, y, fill=color, width=3, tags=tag_prefix)


def window_title(step: JsonDict) -> str:
    kind = str(step.get("kind") or "")
    if kind == "capture_review_record":
        return f"V6 simulated detection {step.get('capture_id')}"
    if kind == "offset_correction":
        return f"V6 offset correction {step.get('capture_id')}"
    if kind == "transition_move":
        return f"V6 transition {step.get('transition_id')}"
    if kind == "standard_position_move":
        return f"V6 move {step.get('position_id')}"
    return "V6 workflow simulator"


def step_text(step: JsonDict) -> str:
    kind = str(step.get("kind") or "")
    lines = [f"Step {step.get('step_index', '?')}: {kind}"]
    if step.get("subsequence"):
        lines.append(f"Subsequence: {step['subsequence']}")
    if step.get("explanation"):
        lines.append(str(step["explanation"]))
    if kind == "capture_review_record":
        lines.append(f"Capture: {step['capture_id']}  target: {step['target']}  view: {step['view']}")
        lines.append("Features: " + json.dumps(step.get("simulated_live_feature_summary", []), sort_keys=True))
    elif kind in {"offset_correction", "transition_move"}:
        result = as_dict(step.get("result"))
        lines.append(f"Action: {result.get('action')}  ok: {result.get('ok')}  move_count: {result.get('move_count')}")
        for move in result.get("planned_moves", []):
            lines.append(
                f"Move {move.get('index')}: {move.get('stage')} target {float(move.get('target_um', 0.0)):.6g} um "
                f"delta {float(move.get('delta_um', 0.0)):.6g} um"
            )
        if result.get("diagnostics"):
            lines.append("Diagnostics: " + json.dumps(result["diagnostics"], sort_keys=True)[:1200])
    elif kind == "standard_position_move":
        lines.append(f"Position: {step.get('position_id')} ({step.get('label')})")
        lines.append(f"Moves: {len(step.get('planned_moves') or [])}, settings: {step.get('camera_settings')}")
    return "\n".join(lines)


def strip_large_sessions_for_trace(step: JsonDict) -> JsonDict:
    compact = deepcopy_json(step)
    compact.pop("standard_session", None)
    compact.pop("live_session", None)
    return compact


def standard_position_subsequence_name(position: JsonDict) -> str:
    return f"SUB_V6MoveToPosition_{position['id']}_{slug(str(position['label']))}"


def standard_position_gate_text(position: JsonDict) -> str:
    return (
        f"V6 standard position {position['id']} ({position['label']}). "
        "Move to hardcoded machine targets and set exposure/lights. "
        "All moving towers raise before camera or tower X/Z motion; "
        "tower Z precedes X, then towers lower to final Y. "
        "Uses medium speeds for approach. Confirm chip, trench, both balls, tower, "
        "and camera clearance."
    )


def capture_gate_text(capture_id: str) -> str:
    return (
        f"V6 capture {capture_id}: this YASE dialog is modal, so manual positioning is "
        "not available while it is open. Standard exposure and lights are already "
        "applied; do not change them or zoom. Cancel if adjustment is needed, then "
        "position manually after the dialog closes and rerun only this capture or "
        "convergence step with the same V6 memory. Capture current only when the image "
        "is already ready and all motion has stopped."
    )


def slug(value: str) -> str:
    return "".join(character if character.isalnum() or character == "_" else "_" for character in value).strip("_")


def stage_value(machine_positions: JsonDict, stage: str) -> float:
    stage_name, axis = AXIS_FOR_STAGE[stage]
    return float(as_dict(machine_positions.get(stage_name)).get(axis, 0.0))


def set_stage_target(machine_positions: JsonDict, stage: str, target_um: float) -> None:
    stage_name, axis = AXIS_FOR_STAGE[stage]
    axes = as_dict(machine_positions.setdefault(stage_name, {}))
    axes[axis] = target_um
    machine_positions[stage_name] = axes


def setting_value(settings: JsonDict, name: str) -> float | None:
    raw = settings.get(name)
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw is None:
        return None
    return float(raw)


def as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def deepcopy_json(value: Any) -> Any:
    return copy.deepcopy(value)


def parse_args(argv: list[str] | None = None) -> SimulatorConfig:
    parser = argparse.ArgumentParser(description="Replay V6 against standard images with simulated reviewed detections.")
    parser.add_argument("--target", choices=["all", "ball_1", "ball_2"], default="all", help="Workflow slice to replay.")
    parser.add_argument("--headless", action="store_true", help="Do not open Tkinter review/move popups.")
    parser.add_argument("--output", type=Path, default=None, help="Optional trace JSON path.")
    parser.add_argument("--memory-path", type=Path, default=None, help="Optional simulator memory JSON path.")
    parser.add_argument("--coarse-shift-x-px", type=float, default=0.0, help="Injected gross ball image-X shift.")
    parser.add_argument("--coarse-shift-y-px", type=float, default=0.0, help="Injected gross ball image-Y shift.")
    parser.add_argument("--fine-shift-x-px", type=float, default=0.0, help="Injected fine top ball image-X residual.")
    parser.add_argument("--fine-shift-y-px", type=float, default=0.0, help="Injected fine top ball image-Y residual.")
    parser.add_argument("--side-shift-y-px", type=float, default=0.0, help="Injected side full-image ball Y shift before mirror flip.")
    parser.add_argument("--auto-advance-ms", type=int, default=0, help="Auto-close each popup after this many milliseconds.")
    parser.add_argument(
        "--popup-scope",
        choices=["vision", "yase", "all"],
        default="vision",
        help=(
            "Default opens editable vision review only; yase adds YASE-style operator gates; "
            "all also shows non-operational diagnostics."
        ),
    )
    parser.add_argument(
        "--replace-baseline",
        action="append",
        default=[],
        metavar="CAPTURE_ID=SESSION_JSON",
        help="Explicitly replace a simulator baseline after backing up the previous JSON.",
    )
    args = parser.parse_args(argv)

    output_path = args.output
    memory_path = args.memory_path
    if output_path is None and args.headless:
        output_path = Path(tempfile.gettempdir()) / DEFAULT_TRACE_NAME
    if memory_path is None and output_path is not None:
        memory_path = output_path.with_name("v6_vision_memory_sim.json")
    baseline_replacements: dict[str, Path] = {}
    for raw_replacement in args.replace_baseline:
        capture_id, separator, raw_path = str(raw_replacement).partition("=")
        if not separator or not capture_id.strip() or not raw_path.strip():
            parser.error("--replace-baseline must be CAPTURE_ID=SESSION_JSON")
        if capture_id.strip() in baseline_replacements:
            parser.error(f"duplicate --replace-baseline for {capture_id.strip()}")
        baseline_replacements[capture_id.strip()] = Path(raw_path.strip())
    return SimulatorConfig(
        workflow_target=args.target,
        headless=args.headless,
        output_path=output_path,
        memory_path=memory_path,
        coarse_shift_x_px=args.coarse_shift_x_px,
        coarse_shift_y_px=args.coarse_shift_y_px,
        fine_shift_x_px=args.fine_shift_x_px,
        fine_shift_y_px=args.fine_shift_y_px,
        side_shift_y_px=args.side_shift_y_px,
        auto_advance_ms=args.auto_advance_ms,
        popup_scope=args.popup_scope,
        baseline_replacements=baseline_replacements,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    result = run_standard_workflow_simulation(config)
    offset_steps = [step for step in result["trace"] if step["kind"] == "offset_correction"]
    move_steps = [step for step in offset_steps if as_dict(step.get("result")).get("action") == "offset_correction_move"]
    print(f"V6 standard workflow simulator completed for {result['workflow_target']}.")
    print(f"Trace steps: {len(result['trace'])}; offset move steps: {len(move_steps)}.")
    if config.output_path:
        print(f"Trace written to: {config.output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
