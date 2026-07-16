"""YASE subprocesses exposed as step-based alignment algorithms."""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

from yase_sim import SimulationMachine, YaseInterpreter
from yase_sim.machine import MoveEvent, PowerReadEvent, parse_number

from .base import (
    DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
    DEFAULT_TARGET_MODE_EFFICIENCY,
    AlignmentAlgorithmResult,
    AlignmentDevice,
    PowerReading,
)
from .position_solve import run_position_solve_until_good


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YASE_ROOT = PROJECT_ROOT / "yase_example_processes"
DEFAULT_YASE_CONFIG = DEFAULT_YASE_ROOT / "examples" / "yase_sim_config.json"
DEFAULT_LENS_ACTOR_MAP = ("Align1", "Align2")
DEFAULT_STAGE_UM_TO_M = 1e-6
YASE_ALGORITHM_PREFIX = "yase:"
SIM_AXIS_TO_MACHINE_AXIS = {
    "x": "Z",
    "y": "Y",
    "z": "X",
}


def _power_dbm(mw: float) -> float:
    return 10.0 * math.log10(mw) if mw > 0 else float("-inf")


def _display_sequence_name(sequence_relpath: str) -> str:
    return Path(sequence_relpath).with_suffix("").as_posix()


def _power_model_lens_mapping(power_config: dict[str, Any]) -> tuple[tuple[str, ...], float]:
    for model in power_config.values():
        if not isinstance(model, dict):
            continue
        if "lens_actor_map" not in model:
            continue
        lens_actor_map = tuple(str(actor) for actor in model.get("lens_actor_map", DEFAULT_LENS_ACTOR_MAP))
        stage_um_to_m = parse_number(model.get("stage_um_to_m", DEFAULT_STAGE_UM_TO_M), DEFAULT_STAGE_UM_TO_M)
        return lens_actor_map, stage_um_to_m
    return DEFAULT_LENS_ACTOR_MAP, DEFAULT_STAGE_UM_TO_M


def _stage_name_for_actor_axis(actor: str, sim_axis: str) -> str | None:
    if not actor.startswith("Align"):
        return None
    suffix = actor.removeprefix("Align")
    machine_axis = SIM_AXIS_TO_MACHINE_AXIS[sim_axis]
    return f"Align_{machine_axis}{suffix}"


@dataclass
class DeviceBackedYaseMachine(SimulationMachine):
    """YASE machine state whose alignment moves are applied to an AlignmentDevice."""

    device: AlignmentDevice | None = None
    lens_actor_map: tuple[str, ...] = DEFAULT_LENS_ACTOR_MAP
    stage_um_to_m: float = DEFAULT_STAGE_UM_TO_M
    last_reading: PowerReading | None = None

    @classmethod
    def from_config_and_device(
        cls,
        root: str | Path,
        config_path: str | Path | None,
        device: AlignmentDevice,
    ) -> "DeviceBackedYaseMachine":
        base = SimulationMachine.from_config(root, config_path)
        base_kwargs = {field.name: getattr(base, field.name) for field in fields(SimulationMachine)}
        lens_actor_map, stage_um_to_m = _power_model_lens_mapping(base.power_config)
        machine = cls(
            **base_kwargs,
            device=device,
            lens_actor_map=lens_actor_map,
            stage_um_to_m=stage_um_to_m,
        )
        machine._sync_stage_positions_from_device_reference()
        return machine

    def move_stage(self, stage: str, distance: float, velocity: float, sync: str, mode: str) -> MoveEvent:
        event = super().move_stage(stage, distance, velocity, sync, mode)
        self._apply_alignment_move(event)
        return event

    def read_power(self, meter: str) -> tuple[float, float, float]:
        if self.device is None:
            return super().read_power(meter)
        self.last_reading = self.device.measure()
        mw = self.last_reading.received_power * 1e3
        return mw, _power_dbm(mw), mw

    def read_average_power(self, meter: str, samples: int) -> tuple[float, float, float]:
        mw, dbm, ma = self.read_power(meter)
        self.power_log.append(PowerReadEvent(meter=meter, mw=mw, dbm=dbm, ma=ma, samples=max(samples, 1)))
        return mw, dbm, ma

    def final_reading(self) -> PowerReading:
        if self.last_reading is not None:
            return self.last_reading
        if self.device is None:
            return PowerReading(received_power=0.0, total_efficiency=0.0, mode_efficiency=0.0)
        self.last_reading = self.device.measure()
        return self.last_reading

    def _sync_stage_positions_from_device_reference(self) -> None:
        if self.device is None or self.stage_um_to_m == 0:
            return
        current_poses = self.device.current_poses()
        starting_poses = self.device.starting_poses()
        for lens_index, actor in enumerate(self.lens_actor_map):
            if lens_index >= len(current_poses) or lens_index >= len(starting_poses):
                break
            current_pose = current_poses[lens_index]
            starting_pose = starting_poses[lens_index]
            offsets_um = {
                "x": (current_pose[0] - starting_pose[0]) / self.stage_um_to_m,
                "y": (current_pose[1] - starting_pose[1]) / self.stage_um_to_m,
                "z": (current_pose[2] - starting_pose[2]) / self.stage_um_to_m,
            }
            for sim_axis, value in offsets_um.items():
                stage = _stage_name_for_actor_axis(actor, sim_axis)
                if stage is not None:
                    self.set_stage_position(stage, value)

    def solve_position_alignment(
        self,
        *,
        target_mode_efficiency: float = DEFAULT_TARGET_MODE_EFFICIENCY,
        max_attempts: int = DEFAULT_MAX_ALIGNMENT_ATTEMPTS,
    ) -> dict[str, float]:
        if self.device is None:
            return {
                "model_power_mw": 0.0,
                "final_power_mw": 0.0,
                "final_mode_efficiency": 0.0,
                "attempts": 0.0,
                "success": 0.0,
            }
        status = run_position_solve_until_good(
            self.device,
            target_mode_efficiency=target_mode_efficiency,
            max_attempts=max_attempts,
        )
        if status.candidate is None:
            raise RuntimeError("no valid noiseless position-solve candidate was found")
        self.last_reading = status.final_reading
        self._sync_stage_positions_from_device_reference()
        return {
            "model_power_mw": status.candidate.reading.received_power * 1e3,
            "final_power_mw": status.final_reading.received_power * 1e3,
            "final_mode_efficiency": status.final_reading.mode_efficiency,
            "attempts": float(status.attempts),
            "success": 1.0 if status.success else 0.0,
        }

    def _apply_alignment_move(self, event: MoveEvent) -> None:
        if self.device is None or event.sim_body is None or event.sim_axis is None:
            return
        try:
            lens_index = self.lens_actor_map.index(event.sim_body)
        except ValueError:
            return

        delta_m = (event.after - event.before) * self.stage_um_to_m
        axis_args = {"dx": 0.0, "dy": 0.0, "dz": 0.0}
        if event.sim_axis == "x":
            axis_args["dx"] = delta_m
        elif event.sim_axis == "y":
            axis_args["dy"] = delta_m
        elif event.sim_axis == "z":
            axis_args["dz"] = delta_m
        else:
            return
        self.last_reading = self.device.move_lens(lens_index, **axis_args)


@dataclass(frozen=True)
class YaseAlignmentAlgorithm:
    """Run one YASE .xseq subprocess against the alignment lab device."""

    sequence_relpath: str
    root: Path = DEFAULT_YASE_ROOT
    config_path: Path = DEFAULT_YASE_CONFIG
    parameters: dict[str, Any] | None = None
    max_steps: int = 10000

    @property
    def name(self) -> str:
        return f"{YASE_ALGORITHM_PREFIX}{self.sequence_relpath}"

    @property
    def display_name(self) -> str:
        return f"YASE: {_display_sequence_name(self.sequence_relpath)}"

    def run(self, device: AlignmentDevice) -> AlignmentAlgorithmResult:
        if not self.root.exists():
            raise RuntimeError(f"YASE process root is missing: {self.root}")
        config_path = self.config_path if self.config_path.exists() else None
        machine = DeviceBackedYaseMachine.from_config_and_device(self.root, config_path, device)
        interpreter = YaseInterpreter(machine)
        result = interpreter.run(
            self.sequence_relpath,
            parameters=dict(self.parameters or {}),
            max_steps=self.max_steps,
        )
        warnings = tuple(result.warnings) + tuple(machine.missing_information)
        message = f"{result.steps} YASE statements, {len(machine.move_log)} stage moves"
        if warnings:
            message += f", {len(warnings)} warnings"
        return AlignmentAlgorithmResult(
            name=self.name,
            display_name=self.display_name,
            final_poses=device.current_poses(),
            final_reading=machine.final_reading(),
            move_history=device.move_history(),
            message=message,
        )


def discover_yase_algorithms(
    root: Path = DEFAULT_YASE_ROOT,
    config_path: Path = DEFAULT_YASE_CONFIG,
) -> dict[str, YaseAlignmentAlgorithm]:
    if not root.exists():
        return {}

    algorithms: dict[str, YaseAlignmentAlgorithm] = {}
    for sequence_path in sorted(root.rglob("*.xseq")):
        relpath = sequence_path.relative_to(root).as_posix()
        if not (relpath == "MAIN_PROCESS.xseq" or relpath.startswith("SUB_")):
            continue
        algorithm = YaseAlignmentAlgorithm(
            sequence_relpath=relpath,
            root=root,
            config_path=config_path,
        )
        algorithms[algorithm.name] = algorithm
    return algorithms
