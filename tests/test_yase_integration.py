from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from alignment_algorithms.base import AlignmentMove, LensPose, PowerReading
from alignment_algorithms.yase import DeviceBackedYaseMachine, YaseAlignmentAlgorithm
from yase_sim import SimulationMachine, YaseInterpreter


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


def _statement(name: str, parameters: str = "") -> str:
    return f'<Statement Label="" Editable="FALSE" Name="{name}" Library="Standard">{parameters}</Statement>'


def _set_string(text: str, variable: str) -> str:
    return _statement(
        "SetString",
        f'<Parameter Name="String 1" Type="String" Direction="Input" ValueType="Constant" StringValue="{text}" />'
        '<Parameter Name="String 2" Type="String" Direction="Input" ValueType="Constant" StringValue="" />'
        f'<Parameter Name="String out" Type="String" Direction="Output" ValueType="Variable" VariableName="{variable}" />',
    )


def _ifnum(left: float, comp: str, right: float) -> str:
    return _statement(
        "ifnum",
        f'<Parameter Name="Num1" Type="DBL" Direction="Input" ValueType="Constant" StringValue="{left}" />'
        f'<Parameter Name="Comp" Type="Enum Word" Direction="Input" ValueType="Constant" StringValue="{comp}" />'
        f'<Parameter Name="Num2" Type="DBL" Direction="Input" ValueType="Constant" StringValue="{right}" />',
    )


def _ifstring(left: str, comp: str, right: str) -> str:
    return _statement(
        "ifstring",
        f'<Parameter Name="String1" Type="String" Direction="Input" ValueType="Constant" StringValue="{left}" />'
        f'<Parameter Name="Comp" Type="Enum Word" Direction="Input" ValueType="Constant" StringValue="{comp}" />'
        f'<Parameter Name="String2" Type="String" Direction="Input" ValueType="Constant" StringValue="{right}" />',
    )


def _selection_dialog() -> str:
    return _statement(
        "DisplayExtdSelectionDialog",
        '<Parameter Name="Message" Type="String" Direction="Input" ValueType="Constant" StringValue="choose" />',
    )


def _run_statements(tmp_path: Path, statements: list[str], dialog_policy: str = "button2"):
    body = "\n".join(statements)
    sequence_path = tmp_path / "test_sequence.xseq"
    sequence_path.write_text(
        f'<?xml version="1.0" encoding="ISO-8859-1"?>\n<Sequence>\n{body}\n</Sequence>\n',
        encoding="ISO-8859-1",
    )
    machine = SimulationMachine(root=tmp_path, dialog_policy=dialog_policy)
    interpreter = YaseInterpreter(machine)
    return interpreter.run(sequence_path)


def test_inline_ifnum_else_true_branch_skips_only_else_statement(tmp_path):
    result = _run_statements(
        tmp_path,
        [
            _ifnum(1, "=", 1),
            _set_string("TRUE", "s_Branch"),
            _statement("ELSE"),
            _set_string("FALSE", "s_Branch"),
            _set_string("AFTER", "s_After"),
        ],
    )
    assert result.variables["s_Branch"] == "TRUE"
    assert result.variables["s_After"] == "AFTER"


def test_inline_ifnum_else_false_branch_runs_else_statement(tmp_path):
    result = _run_statements(
        tmp_path,
        [
            _ifnum(0, "=", 1),
            _set_string("TRUE", "s_Branch"),
            _statement("ELSE"),
            _set_string("FALSE", "s_Branch"),
            _set_string("AFTER", "s_After"),
        ],
    )
    assert result.variables["s_Branch"] == "FALSE"
    assert result.variables["s_After"] == "AFTER"


def test_inline_ifstring_else_true_branch_skips_only_else_statement(tmp_path):
    result = _run_statements(
        tmp_path,
        [
            _ifstring("A", "=", "A"),
            _set_string("TRUE", "s_Branch"),
            _statement("ELSE"),
            _set_string("FALSE", "s_Branch"),
            _set_string("AFTER", "s_After"),
        ],
    )
    assert result.variables["s_Branch"] == "TRUE"
    assert result.variables["s_After"] == "AFTER"


def test_block_ifnum_else_true_branch_skips_to_end(tmp_path):
    result = _run_statements(
        tmp_path,
        [
            _ifnum(1, "=", 1),
            _statement("BEGIN"),
            _set_string("TRUE", "s_Branch"),
            _statement("ELSE"),
            _set_string("FALSE", "s_Branch"),
            _statement("END"),
            _set_string("AFTER", "s_After"),
        ],
    )
    assert result.variables["s_Branch"] == "TRUE"
    assert result.variables["s_After"] == "AFTER"


def test_block_ifnum_else_false_branch_runs_else_section(tmp_path):
    result = _run_statements(
        tmp_path,
        [
            _ifnum(0, "=", 1),
            _statement("BEGIN"),
            _set_string("TRUE", "s_Branch"),
            _statement("ELSE"),
            _set_string("FALSE", "s_Branch"),
            _statement("END"),
            _set_string("AFTER", "s_After"),
        ],
    )
    assert result.variables["s_Branch"] == "FALSE"
    assert result.variables["s_After"] == "AFTER"


def test_selection_dialog_button2_runs_else_branch(tmp_path):
    result = _run_statements(
        tmp_path,
        [
            _selection_dialog(),
            _set_string("BUTTON1", "s_Branch"),
            _statement("ELSE"),
            _set_string("BUTTON2", "s_Branch"),
            _set_string("AFTER", "s_After"),
        ],
        dialog_policy="button2",
    )
    assert result.variables["s_Branch"] == "BUTTON2"
    assert result.variables["s_After"] == "AFTER"


def test_selection_dialog_button1_runs_first_branch(tmp_path):
    result = _run_statements(
        tmp_path,
        [
            _selection_dialog(),
            _set_string("BUTTON1", "s_Branch"),
            _statement("ELSE"),
            _set_string("BUTTON2", "s_Branch"),
            _set_string("AFTER", "s_After"),
        ],
        dialog_policy="button1",
    )
    assert result.variables["s_Branch"] == "BUTTON1"
    assert result.variables["s_After"] == "AFTER"


def test_selection_dialog_button2_skips_whole_begin_block(tmp_path):
    result = _run_statements(
        tmp_path,
        [
            _selection_dialog(),
            _statement("BEGIN"),
            _set_string("INBLOCK", "s_Branch"),
            _statement("END"),
            _set_string("AFTER", "s_After"),
        ],
        dialog_policy="button2",
    )
    assert "s_Branch" not in result.variables
    assert result.variables["s_After"] == "AFTER"
