from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from alignment_algorithms.base import AlignmentMove, LensPose, PowerReading
from alignment_algorithms.yase import DeviceBackedYaseMachine, YaseAlignmentAlgorithm


ROOT = Path(__file__).resolve().parents[1]
YASE_ROOT = ROOT / "yase_process"
YASE_CONFIG = YASE_ROOT / "examples" / "yase_sim_config.json"


class FakeAlignmentDevice:
    def __init__(self) -> None:
        self._poses: list[LensPose] = [(0.0, 0.0, 0.0), (10e-6, 0.0, 1e-3)]
        self._moves: list[AlignmentMove] = []
        self._move_count = 0
        self._measurement_count = 0

    def current_poses(self) -> tuple[LensPose, ...]:
        return tuple(self._poses)

    def starting_poses(self) -> tuple[LensPose, ...]:
        return ((0.0, 0.0, 0.0), (10e-6, 0.0, 1e-3))

    def move_lens(
        self,
        lens_index: int,
        dx: float = 0.0,
        dy: float = 0.0,
        dz: float = 0.0,
    ) -> PowerReading:
        x_offset, y_offset, z_position = self._poses[lens_index]
        self._poses[lens_index] = (x_offset + dx, y_offset + dy, z_position + dz)
        self._move_count += 1
        self._measurement_count += 1
        reading = self._reading()
        self._moves.append(
            AlignmentMove(
                lens_index=lens_index,
                dx=dx,
                dy=dy,
                dz=dz,
                poses=self.current_poses(),
                reading=reading,
            )
        )
        return reading

    def measure(self) -> PowerReading:
        self._measurement_count += 1
        return self._reading()

    def move_history(self) -> tuple[AlignmentMove, ...]:
        return tuple(self._moves)

    def _reading(self) -> PowerReading:
        offset = sum(abs(value) for pose in self._poses for value in pose)
        received_power = max(0.0, 1e-3 - offset * 1e-3)
        return PowerReading(
            received_power=received_power,
            total_efficiency=received_power,
            mode_efficiency=received_power,
            move_count=self._move_count,
            measurement_count=self._measurement_count,
        )


def test_device_backed_yase_machine_maps_align_axes_to_lens_pose_axes():
    device = FakeAlignmentDevice()
    machine = DeviceBackedYaseMachine.from_config_and_device(YASE_ROOT, YASE_CONFIG, device)

    machine.move_stage("Align_X1", 5.0, 50.0, "Sync", "Relative")
    machine.move_stage("Align_Z1", -2.0, 50.0, "Sync", "Relative")
    machine.move_stage("Align_Y2", 3.0, 50.0, "Sync", "Relative")
    machine.move_stage("Camera_X", 99.0, 50.0, "Sync", "Relative")

    poses = device.current_poses()
    assert np.allclose(poses[0], (5e-6, -2e-6, 0.0))
    assert np.allclose(poses[1], (10e-6, 0.0, 1e-3 + 3e-6))
    assert len(device.move_history()) == 3
    assert machine.actors["Align1"]["x"] == 5.0
    assert machine.actors["Align1"]["y"] == -2.0
    assert machine.actors["Align2"]["z"] == 3.0


def test_device_backed_yase_machine_uses_alignment_device_for_power_reads():
    device = FakeAlignmentDevice()
    machine = DeviceBackedYaseMachine.from_config_and_device(YASE_ROOT, YASE_CONFIG, device)

    mw, dbm, ma = machine.read_average_power("TIA1", 3)

    assert math.isclose(mw, machine.last_reading.received_power * 1e3)
    assert math.isfinite(dbm)
    assert ma == mw
    assert len(machine.power_log) == 1
    assert machine.power_log[0].samples == 3


def test_yase_subprocess_runs_as_alignment_algorithm():
    device = FakeAlignmentDevice()
    algorithm = YaseAlignmentAlgorithm(
        "SUB_Positioning/SUB_Test_DrawCircle_AlignX1Z1.xseq",
        root=YASE_ROOT,
        config_path=YASE_CONFIG,
    )

    result = algorithm.run(device)

    assert result.name == "yase:SUB_Positioning/SUB_Test_DrawCircle_AlignX1Z1.xseq"
    assert "YASE:" in result.display_name
    assert len(result.move_history) == 20
    assert np.allclose(result.final_poses, ((0.0, 0.0, 0.0), (10e-6, 0.0, 1e-3)))
    assert result.move_count == 20
    assert result.evaluations == 20
