from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
import math
import time

from .machine import SimulationMachine, parse_number
from .xseq import YaseParameter, YaseSequence, YaseStatement, load_xseq, normalize_label


NUMERIC_TYPES = {"DBL", "I32", "U32", "SGL", "Boolean"}

DEFAULT_FRAME_VARIABLES: dict[str, Any] = {
    "Error": 0.0,
    "ErrorType": 0.0,
    "d_ErrorType": 0.0,
    "s_ErrorMessage": "",
    "S_ErrorMessage": "",
    "s_ErrorSequenceName": "",
    "s_SequenceName": "",
}

VISION_ROUTINE_NAMES = {
    "FixingPos1_12032026",
}


@dataclass
class Frame:
    sequence: YaseSequence
    variables: dict[str, Any] = field(default_factory=dict)
    input_parameters: dict[str, Any] = field(default_factory=dict)
    return_parameters: dict[str, Any] = field(default_factory=dict)
    pc: int = 0
    halted: bool = False


@dataclass
class RunResult:
    sequence: Path
    steps: int
    halted: bool
    variables: dict[str, Any]
    return_parameters: dict[str, Any]
    machine_snapshot: dict[str, Any]
    trace: list[str]
    warnings: list[str]


class YaseInterpreter:
    def __init__(
        self,
        machine: SimulationMachine,
        *,
        strict_unknown: bool = False,
        trace_enabled: bool = False,
    ) -> None:
        self.machine = machine
        self.strict_unknown = strict_unknown
        self.trace_enabled = trace_enabled
        self.trace: list[str] = []
        self.warnings: list[str] = []
        self._cache: dict[Path, YaseSequence] = {}
        self._step_budget = 0
        self._handlers: dict[str, Callable[[Frame, YaseStatement], None]] = {
            "****": self._noop,
            "BEGIN": self._noop,
            "END": self._noop,
            "SetString": self._set_string,
            "DisplayStatus": self._display_status,
            "DisplayDialog": self._display_status,
            "DisplayExtdSelectionDialog": self._display_extended_selection_dialog,
            "DisplayExtdDialog": self._display_extended_dialog,
            "DropDownDialog": self._dropdown_dialog,
            "UserDialog": self._display_extended_dialog,
            "DisplayStatusNum": self._display_status,
            "Positioning_OpenManuelMovePanel": self._noop,
            "GetDateTimeString": self._get_datetime_string,
            "TimeFormat": self._time_format,
            "AppendStrings": self._append_strings,
            "GetStringLength": self._get_string_length,
            "SetStrNum": self._set_str_num,
            "ResolvePath": self._resolve_path,
            "ReadDataFileString": self._read_data_file_string,
            "ReadDataFileStringList": self._read_data_file_string_list,
            "ReadDataFileStringListwithPath": self._read_data_file_string_list,
            "WriteDataFileStringListwithPath": self._noop,
            "TIAAutorange": self._tia_setup_noop,
            "TIAOffset": self._tia_setup_noop,
            "TIAWavelength": self._tia_setup_noop,
            "TIAPolarity": self._tia_setup_noop,
            "WaitDigIn": self._wait_dig_in,
            "OnError": self._noop,
            "AbortAllSequences": self._abort_all_sequences,
            "GrabAndSave": self._unmodeled_hardware_error,
            "GetTimer": self._get_timer,
            "set": self._set_number,
            "calc": self._calc,
            "NumToString": self._num_to_string,
            "StringToNum": self._string_to_num,
            "InRange": self._in_range,
            "StageCheckAllFiducialed": self._stage_check_all_fiducialed,
            "MoveStage": self._move_stage,
            "QueryStage": self._query_stage,
            "GetNumVar": self._get_num_var,
            "SetNumVar": self._set_num_var,
            "GetStringVar": self._get_string_var,
            "SetStringVar": self._set_string_var,
            "KeyAvailable": self._key_available,
            "SetDigOut": self._set_dig_out,
            "GetDigOut": self._get_dig_out,
            "GetDigIn": self._get_dig_in,
            "SetAnalogOut": self._set_analog_out,
            "GetAnalogIn": self._get_analog_in,
            "GetPower": self._get_power,
            "GaussianSim_PositionSolve": self._gaussian_sim_position_solve,
            "TIARange": self._tia_range,
            "DeclareNumParam": self._declare_num_param,
            "DeclareStrParam": self._declare_str_param,
            "ReturnNumParam": self._return_num_param,
            "ReturnStrParam": self._return_str_param,
            "Delay": self._delay,
            "OpenPanel": self._noop,
            "MiniReportClear": self._noop,
            "MiniReportWriteString": self._noop,
            "MiniReportWriteNumber": self._noop,
            "ProgressBar_Start": self._noop,
            "ProgressBar_Stop": self._noop,
            "MetrologyScanDisplay": self._unmodeled_hardware_error,
            "MetrologyLineScan": self._unmodeled_hardware_error,
            "AdvAlign_SpiralScan": self._unmodeled_hardware_error,
            "Grab": self._unmodeled_hardware_error,
            "VA_TM_GetValue": self._unmodeled_hardware_error,
            "VA_TM_FreeAllDocs": self._noop,
            "IMAQWind_ShowImage": self._noop,
            "IMAQWind_SetZoomFactor": self._noop,
        }

    def load_sequence(self, path: str | Path) -> YaseSequence:
        sequence_path = Path(path)
        if not sequence_path.is_absolute():
            sequence_path = self.machine.root / sequence_path
        sequence_path = sequence_path.resolve()
        if sequence_path not in self._cache:
            self._cache[sequence_path] = load_xseq(sequence_path)
        return self._cache[sequence_path]

    def run(
        self,
        sequence_path: str | Path,
        *,
        parameters: dict[str, Any] | None = None,
        max_steps: int = 10000,
    ) -> RunResult:
        sequence = self.load_sequence(sequence_path)
        frame = Frame(sequence=sequence, input_parameters=dict(parameters or {}))
        self._init_frame_variables(frame)
        self._step_budget = max_steps
        steps = self._run_frame(frame)
        return RunResult(
            sequence=sequence.path,
            steps=steps,
            halted=frame.halted,
            variables=dict(frame.variables),
            return_parameters=dict(frame.return_parameters),
            machine_snapshot=self.machine.snapshot(),
            trace=list(self.trace),
            warnings=list(self.warnings),
        )

    def _init_frame_variables(self, frame: Frame) -> None:
        for name, value in DEFAULT_FRAME_VARIABLES.items():
            frame.variables.setdefault(name, value)

    def _run_frame(self, frame: Frame) -> int:
        steps = 0
        statements = frame.sequence.statements
        while not frame.halted and frame.pc < len(statements):
            if self._step_budget <= 0:
                raise RuntimeError(f"maximum step count reached in {frame.sequence.path}")
            statement = statements[frame.pc]
            self._trace(frame, statement)
            frame.pc += 1
            steps += 1
            self._step_budget -= 1
            if self._is_disabled(statement):
                continue

            if statement.name == "ifnum":
                self._ifnum(frame, statement)
                continue
            if statement.name == "ifstring":
                self._ifstring(frame, statement)
                continue
            if statement.name == "ELSE":
                self._else(frame)
                continue
            if statement.name == "Goto":
                self._goto(frame, statement)
                continue
            if statement.name == "EndSeq":
                frame.halted = True
                continue

            handler = self._handlers.get(statement.name)
            if handler:
                handler(frame, statement)
            elif statement.name.startswith("SEQ::"):
                self._call_sequence_or_system_helper(frame, statement)
            elif self._is_vision_routine(statement.name):
                self._unmodeled_hardware_error(frame, statement)
            else:
                self._unknown_statement(statement)
        return steps

    def _is_vision_routine(self, name: str) -> bool:
        return name.startswith("VIS_") or name in VISION_ROUTINE_NAMES

    def _trace(self, frame: Frame, statement: YaseStatement) -> None:
        if self.trace_enabled:
            self.trace.append(f"{frame.sequence.path.name}:{statement.index}:{statement.name}")

    def _is_disabled(self, statement: YaseStatement) -> bool:
        label = statement.label.strip()
        return label == "*" or label.startswith("//")

    def _value(self, frame: Frame, parameter: YaseParameter) -> Any:
        if parameter.value_type == "Variable":
            if not parameter.variable_name:
                return 0.0 if parameter.type_name in NUMERIC_TYPES else ""
            if parameter.variable_name not in frame.variables:
                if parameter.variable_name not in DEFAULT_FRAME_VARIABLES:
                    self.warn(f"Variable {parameter.variable_name!r} read before assignment; using default.")
            value = frame.variables.get(
                parameter.variable_name,
                0.0 if parameter.type_name in NUMERIC_TYPES else "",
            )
            return value
        if parameter.type_name in NUMERIC_TYPES:
            if parameter.string_value not in (None, ""):
                return parse_number(parameter.string_value)
            return parse_number(parameter.numeric_value)
        return parameter.string_value if parameter.string_value is not None else ""

    def _num(self, frame: Frame, parameter: YaseParameter) -> float:
        return parse_number(self._value(frame, parameter))

    def _str(self, frame: Frame, parameter: YaseParameter) -> str:
        value = self._value(frame, parameter)
        if isinstance(value, float) and value.is_integer():
            return str(int(value))
        return str(value)

    def _param_by_name(self, statement: YaseStatement, name: str) -> YaseParameter:
        return statement.param(name)

    def _param_at(self, statement: YaseStatement, index: int) -> YaseParameter:
        return statement.parameters[index]

    def _write_output(self, frame: Frame, parameter: YaseParameter, value: Any) -> None:
        if parameter.value_type == "Variable" and parameter.variable_name:
            frame.variables[parameter.variable_name] = value

    def _write_named_output(self, frame: Frame, statement: YaseStatement, name: str, value: Any) -> None:
        self._write_output(frame, statement.param(name), value)

    def _noop(self, frame: Frame, statement: YaseStatement) -> None:
        return None

    def _display_status(self, frame: Frame, statement: YaseStatement) -> None:
        if statement.parameters:
            text = self._str(frame, statement.parameters[0])
            if self.trace_enabled:
                self.trace.append(f"STATUS {text}")

    def _display_extended_selection_dialog(self, frame: Frame, statement: YaseStatement) -> None:
        policy = self.machine.dialog_policy
        if policy in {"button2", "skip", "move", "continue"}:
            frame.pc += 1
        elif policy in {"button1", "ok", "abort"}:
            return
        else:
            self.warn(f"Unknown dialog policy {policy!r}; using button2/skip behavior.")
            frame.pc += 1

    def _display_extended_dialog(self, frame: Frame, statement: YaseStatement) -> None:
        return None

    def _dropdown_dialog(self, frame: Frame, statement: YaseStatement) -> None:
        title = ""
        for parameter in statement.parameters:
            if parameter.name == "Title":
                title = self._str(frame, parameter)
                break
        selection = self.machine.dropdown_selection_for(title)
        for parameter in statement.parameters:
            if parameter.direction == "Output" and parameter.type_name == "String":
                self._write_output(frame, parameter, selection)
                return

    def _get_datetime_string(self, frame: Frame, statement: YaseStatement) -> None:
        for parameter in statement.parameters:
            if parameter.direction == "Output" and parameter.type_name == "String":
                self._write_output(frame, parameter, "2026-01-01 00:00:00")
                return

    def _time_format(self, frame: Frame, statement: YaseStatement) -> None:
        for parameter in statement.parameters:
            if parameter.direction == "Output":
                self._write_output(frame, parameter, 0.0 if parameter.type_name in NUMERIC_TYPES else "")

    def _get_string_length(self, frame: Frame, statement: YaseStatement) -> None:
        source = self._str(frame, statement.parameters[0])
        for parameter in statement.parameters:
            if parameter.direction == "Output" and parameter.type_name in NUMERIC_TYPES:
                self._write_output(frame, parameter, float(len(source)))
                return

    def _set_str_num(self, frame: Frame, statement: YaseStatement) -> None:
        prefix = self._str(frame, statement.param("String"))
        number = self._num(frame, statement.param("Number"))
        precision = int(self._num(frame, statement.param("Precision")))
        if precision > 0:
            formatted = f"{number:.{precision}f}"
        else:
            formatted = str(int(number)) if number.is_integer() else str(number)
        self._write_named_output(frame, statement, "String out", prefix + formatted)

    def _append_strings(self, frame: Frame, statement: YaseStatement) -> None:
        first = self._str(frame, statement.param("First String"))
        second = self._str(frame, statement.param("SecondString"))
        output_names = [parameter.name for parameter in statement.parameters if parameter.direction == "Output"]
        output_name = "Path out" if "Path out" in output_names else "String out"
        self._write_named_output(frame, statement, output_name, first + second)

    def _resolve_path(self, frame: Frame, statement: YaseStatement) -> None:
        for parameter in statement.parameters:
            if parameter.direction == "Output" and parameter.type_name == "String":
                self._write_output(frame, parameter, str(self.machine.root))
                return

    def _read_data_file_string(self, frame: Frame, statement: YaseStatement) -> None:
        for parameter in statement.parameters:
            if parameter.direction == "Output" and parameter.type_name == "String":
                self._write_output(frame, parameter, "")
                return

    def _read_data_file_string_list(self, frame: Frame, statement: YaseStatement) -> None:
        for parameter in statement.parameters:
            if parameter.direction == "Output" and parameter.type_name == "String":
                self._write_output(frame, parameter, "")
                return

    def _tia_setup_noop(self, frame: Frame, statement: YaseStatement) -> None:
        for parameter in statement.parameters:
            if parameter.direction == "Output":
                self._write_output(frame, parameter, 0.0 if parameter.type_name in NUMERIC_TYPES else "")

    def _wait_dig_in(self, frame: Frame, statement: YaseStatement) -> None:
        line = self._str(frame, statement.param("Digital Line"))
        desired = self._str(frame, statement.param("State")).lower()
        state, _ = self.machine.get_digital_input(line)
        actual = "on" if state >= 0.5 else "off"
        matched = actual == desired
        # Output "Timeout": 1 = run next line, 0 = skip next line (YASE naming is inverted).
        continue_next = 0.0 if matched else 1.0
        self._write_named_output(frame, statement, "Timeout", continue_next)
        if continue_next == 0.0 and frame.pc < len(frame.sequence.statements):
            next_statement = frame.sequence.statements[frame.pc]
            if next_statement.name == "Goto":
                frame.pc += 1

    def _abort_all_sequences(self, frame: Frame, statement: YaseStatement) -> None:
        frame.halted = True

    def _get_timer(self, frame: Frame, statement: YaseStatement) -> None:
        self._write_named_output(frame, statement, "Timer val", time.monotonic())

    def _set_string(self, frame: Frame, statement: YaseStatement) -> None:
        value = self._str(frame, statement.param("String 1")) + self._str(frame, statement.param("String 2"))
        self._write_named_output(frame, statement, "String out", value)

    def _set_number(self, frame: Frame, statement: YaseStatement) -> None:
        self._write_named_output(frame, statement, "Number out", self._num(frame, statement.param("Value")))

    def _calc(self, frame: Frame, statement: YaseStatement) -> None:
        left = self._num(frame, statement.param("Number 1 in"))
        right = self._num(frame, statement.param("Number 2 in"))
        operation = self._str(frame, statement.param("Operation")).strip()
        if operation == "+":
            result = left + right
        elif operation in {"--", "-"}:
            result = left - right
        elif operation == "*":
            result = left * right
        elif operation == "/":
            if right == 0:
                result = math.inf
            else:
                result = left / right
        else:
            self.warn(f"Unsupported calc operation {operation!r}; using left operand.")
            result = left
        self._write_named_output(frame, statement, "Number out", result)

    def _num_to_string(self, frame: Frame, statement: YaseStatement) -> None:
        fmt = self._str(frame, statement.parameters[0])
        value = self._num(frame, statement.parameters[1])
        try:
            result = fmt % value
        except Exception:
            result = str(value)
            if fmt:
                self.warn(f"Unsupported NumToString format {fmt!r}; using str(value).")
        self._write_output(frame, statement.parameters[2], result)

    def _string_to_num(self, frame: Frame, statement: YaseStatement) -> None:
        self._write_output(frame, statement.parameters[-1], parse_number(self._str(frame, statement.parameters[0])))

    def _in_range(self, frame: Frame, statement: YaseStatement) -> None:
        value = self._num(frame, statement.param("Value"))
        max_value = self._num(frame, statement.param("Max"))
        min_value = self._num(frame, statement.param("Min"))
        self._write_named_output(frame, statement, "InRange", 1.0 if min_value <= value <= max_value else 0.0)

    def _stage_check_all_fiducialed(self, frame: Frame, statement: YaseStatement) -> None:
        self._write_named_output(frame, statement, "Fiducialed?", 1.0 if self.machine.fiducialed else 0.0)

    def _move_stage(self, frame: Frame, statement: YaseStatement) -> None:
        stage = self._str(frame, statement.param("Stage"))
        velocity = self._num(frame, statement.param("Velocity [um/s]"))
        distance = self._num(frame, statement.param("Distance [um]"))
        sync = self._str(frame, statement.param("Sync"))
        mode = self._str(frame, statement.param("rel/abs"))
        self.machine.move_stage(stage, distance, velocity, sync, mode)

    def _query_stage(self, frame: Frame, statement: YaseStatement) -> None:
        stage = self._str(frame, statement.param("Stage"))
        self._write_named_output(frame, statement, "Position [um]", self.machine.query_stage(stage))
        if any(parameter.name == "Message" for parameter in statement.parameters):
            self._write_named_output(frame, statement, "Message", "")

    def _get_num_var(self, frame: Frame, statement: YaseStatement) -> None:
        source, path, section, name = self._variable_io_args(frame, statement)
        value = self.machine.read_num_var(source, path, section, name)
        self._write_named_output(frame, statement, "VarValueOut", value)

    def _set_num_var(self, frame: Frame, statement: YaseStatement) -> None:
        source, path, section, name = self._variable_io_args(frame, statement)
        value = self._num(frame, statement.parameters[-1])
        self.machine.write_num_var(source, path, section, name, value)

    def _get_string_var(self, frame: Frame, statement: YaseStatement) -> None:
        source, path, section, name = self._variable_io_args(frame, statement)
        value = self.machine.read_string_var(source, path, section, name)
        self._write_named_output(frame, statement, "VarStringOut", value)

    def _set_string_var(self, frame: Frame, statement: YaseStatement) -> None:
        source, path, section, name = self._variable_io_args(frame, statement)
        value = self._str(frame, statement.parameters[-1])
        self.machine.write_string_var(source, path, section, name, value)

    def _key_available(self, frame: Frame, statement: YaseStatement) -> None:
        source, path, section, name = self._variable_io_args(frame, statement)
        self._write_output(frame, statement.parameters[-1], 1.0 if self.machine.key_available(source, path, section, name) else 0.0)

    def _variable_io_args(self, frame: Frame, statement: YaseStatement) -> tuple[str, str, str, str]:
        return (
            self._str(frame, statement.parameters[0]),
            self._str(frame, statement.parameters[1]),
            self._str(frame, statement.parameters[2]),
            self._str(frame, statement.parameters[3]),
        )

    def _set_dig_out(self, frame: Frame, statement: YaseStatement) -> None:
        line = self._str(frame, statement.param("Digital Line"))
        state = self._str(frame, statement.param("State"))
        last_change = self.machine.set_digital_output(line, state)
        if len(statement.parameters) >= 3:
            self._write_output(frame, statement.parameters[2], last_change)

    def _get_dig_out(self, frame: Frame, statement: YaseStatement) -> None:
        state, changed = self.machine.get_digital_output(self._str(frame, statement.param("Digital Line")))
        self._write_named_output(frame, statement, "State", state)
        if any(parameter.name == "LastChangeTime" for parameter in statement.parameters):
            self._write_named_output(frame, statement, "LastChangeTime", changed)

    def _get_dig_in(self, frame: Frame, statement: YaseStatement) -> None:
        state, read_time = self.machine.get_digital_input(self._str(frame, statement.param("Digital Line")))
        self._write_named_output(frame, statement, "State", state)
        if any(parameter.name == "ReadOutTime" for parameter in statement.parameters):
            self._write_named_output(frame, statement, "ReadOutTime", read_time)

    def _set_analog_out(self, frame: Frame, statement: YaseStatement) -> None:
        line = self._str(frame, statement.param("Analog Line"))
        value = self._num(frame, statement.param("Value"))
        last_change = self.machine.set_analog_output(line, value)
        if len(statement.parameters) >= 3:
            self._write_output(frame, statement.parameters[2], last_change)

    def _get_analog_in(self, frame: Frame, statement: YaseStatement) -> None:
        value, read_time = self.machine.get_analog_input(self._str(frame, statement.param("Analog Line")))
        self._write_named_output(frame, statement, "Value", value)
        if any(parameter.name == "ReadOutTime" for parameter in statement.parameters):
            self._write_named_output(frame, statement, "ReadOutTime", read_time)

    def _get_power(self, frame: Frame, statement: YaseStatement) -> None:
        meter = self._str(frame, statement.param("PowerMeter"))
        mw, _, _ = self.machine.read_average_power(meter, 1)
        self._write_named_output(frame, statement, "Power", mw)

    def _gaussian_sim_position_solve(self, frame: Frame, statement: YaseStatement) -> None:
        solve = getattr(self.machine, "solve_position_alignment", None)
        if solve is None:
            message = "Machine does not support GaussianSim_PositionSolve."
            self.warn(message)
            self._write_optional_named_output(frame, statement, "ErrorType", 1.0)
            self._write_optional_named_output(frame, statement, "ErrorMessage", message)
            return
        try:
            kwargs = {}
            if self._has_named_parameter(statement, "TargetMode"):
                kwargs["target_mode_efficiency"] = self._num(frame, statement.param("TargetMode"))
            if self._has_named_parameter(statement, "MaxAttempts"):
                kwargs["max_attempts"] = int(max(1, self._num(frame, statement.param("MaxAttempts"))))
            summary = solve(**kwargs)
        except Exception as exc:  # pragma: no cover - exercised by machine-specific failures.
            message = str(exc)
            self.warn(message)
            self._write_optional_named_output(frame, statement, "ErrorType", 1.0)
            self._write_optional_named_output(frame, statement, "ErrorMessage", message)
            return
        if isinstance(summary, dict):
            model_power_mw = float(summary.get("model_power_mw", 0.0))
            final_power_mw = float(summary.get("final_power_mw", model_power_mw))
            final_mode = float(summary.get("final_mode_efficiency", 0.0))
            attempts = float(summary.get("attempts", 0.0))
            success = float(summary.get("success", 0.0))
        else:
            model_power_mw = float(summary)
            final_power_mw = model_power_mw
            final_mode = 0.0
            attempts = 1.0
            success = 1.0
        self._write_optional_named_output(frame, statement, "ModelPower", model_power_mw)
        self._write_optional_named_output(frame, statement, "FinalPower", final_power_mw)
        self._write_optional_named_output(frame, statement, "FinalMode", final_mode)
        self._write_optional_named_output(frame, statement, "Attempts", attempts)
        self._write_optional_named_output(frame, statement, "Success", success)
        self._write_optional_named_output(frame, statement, "ErrorType", 0.0)
        self._write_optional_named_output(frame, statement, "ErrorMessage", "")

    def _has_named_parameter(self, statement: YaseStatement, name: str) -> bool:
        return any(parameter.name == name for parameter in statement.parameters)

    def _write_optional_named_output(self, frame: Frame, statement: YaseStatement, name: str, value: Any) -> None:
        if self._has_named_parameter(statement, name):
            self._write_named_output(frame, statement, name, value)

    def _tia_range(self, frame: Frame, statement: YaseStatement) -> None:
        meter = self._str(frame, statement.param("Meter"))
        function = self._str(frame, statement.param("Function")).lower()
        gain_in = self._num(frame, statement.param("GainIn"))
        key = f"{meter}_Range"
        if function == "set":
            self.machine.analog_outputs[key] = gain_in
        gain_out = self.machine.analog_outputs.get(key, gain_in)
        self._write_named_output(frame, statement, "GainOut", gain_out)

    def _declare_num_param(self, frame: Frame, statement: YaseStatement) -> None:
        name = self._str(frame, statement.param("Name"))
        default = self._num(frame, statement.param("Default value"))
        self._write_named_output(frame, statement, "Value", parse_number(frame.input_parameters.get(name, default)))

    def _declare_str_param(self, frame: Frame, statement: YaseStatement) -> None:
        name = self._str(frame, statement.param("Name"))
        default = self._str(frame, statement.param("Default value"))
        self._write_named_output(frame, statement, "Value", str(frame.input_parameters.get(name, default)))

    def _return_num_param(self, frame: Frame, statement: YaseStatement) -> None:
        frame.return_parameters[self._str(frame, statement.param("Name"))] = self._num(frame, statement.param("Value"))

    def _return_str_param(self, frame: Frame, statement: YaseStatement) -> None:
        frame.return_parameters[self._str(frame, statement.param("Name"))] = self._str(frame, statement.param("Value"))

    def _delay(self, frame: Frame, statement: YaseStatement) -> None:
        return None

    def _ifnum(self, frame: Frame, statement: YaseStatement) -> None:
        left = self._num(frame, statement.param("Num1"))
        comp = self._str(frame, statement.param("Comp"))
        right = self._num(frame, statement.param("Num2"))
        self._conditional_jump(frame, self._compare(left, comp, right))

    def _ifstring(self, frame: Frame, statement: YaseStatement) -> None:
        left = self._str(frame, statement.param("String1"))
        comp = self._str(frame, statement.param("Comp"))
        right = self._str(frame, statement.param("String2"))
        self._conditional_jump(frame, self._compare(left, comp, right))

    def _compare(self, left: Any, comp: str, right: Any) -> bool:
        if comp == "=":
            return left == right
        if comp == "<>":
            return left != right
        if comp == "<":
            return left < right
        if comp == "<=":
            return left <= right
        if comp == ">":
            return left > right
        if comp == ">=":
            return left >= right
        self.warn(f"Unsupported comparison {comp!r}; treating as false.")
        return False

    def _conditional_jump(self, frame: Frame, condition: bool) -> None:
        statements = frame.sequence.statements
        if frame.pc >= len(statements):
            return
        next_statement = statements[frame.pc]
        if next_statement.name == "BEGIN":
            else_index, end_index = self._find_block_bounds(frame, frame.pc)
            if not condition:
                frame.pc = (else_index + 1) if else_index is not None else (end_index + 1)
            return
        if not condition:
            if frame.pc + 1 < len(statements) and statements[frame.pc + 1].name == "ELSE":
                frame.pc += 2
            else:
                frame.pc += 1

    def _else(self, frame: Frame) -> None:
        else_index = frame.pc - 1
        _, end_index = self._find_block_bounds_from_else(frame, else_index)
        frame.pc = end_index + 1

    def _find_block_bounds(self, frame: Frame, begin_index: int) -> tuple[int | None, int]:
        depth = 0
        else_index: int | None = None
        for index in range(begin_index, len(frame.sequence.statements)):
            name = frame.sequence.statements[index].name
            if name == "BEGIN":
                depth += 1
            elif name == "END":
                depth -= 1
                if depth == 0:
                    return else_index, index
            elif name == "ELSE" and depth == 1:
                else_index = index
        if else_index is not None and depth == 1:
            return else_index, else_index
        raise RuntimeError(f"BEGIN at {begin_index} has no matching END in {frame.sequence.path}")

    def _find_block_bounds_from_else(self, frame: Frame, else_index: int) -> tuple[int | None, int]:
        depth = 1
        statements = frame.sequence.statements
        for index in range(else_index + 1, len(statements)):
            name = statements[index].name
            if name == "BEGIN":
                depth += 1
            elif name == "END":
                depth -= 1
                if depth == 0:
                    return else_index, index
            elif depth == 1 and name == "Goto":
                return else_index, index
        return else_index, len(statements) - 1

    def _goto(self, frame: Frame, statement: YaseStatement) -> None:
        target = normalize_label(self._str(frame, statement.param("Label")))
        if target not in frame.sequence.labels:
            raise RuntimeError(f"Goto target {target!r} not found in {frame.sequence.path}")
        frame.pc = frame.sequence.labels[target]

    def _call_sequence_or_system_helper(self, frame: Frame, statement: YaseStatement) -> None:
        name = statement.name
        if name == "SEQ::SUB_SYS_AxisWaitFinishList":
            return
        if name == "SEQ::SUB_SysCheckAxisMove":
            self._write_named_output(frame, statement, "Error", 0.0)
            self._write_named_output(frame, statement, "S_ErrorMessage", "")
            return
        if name == "SEQ::SUB_SysReadAveragePower":
            meter = self._str(frame, statement.param("PowerMeter"))
            samples = int(max(1, self._num(frame, statement.param("Number of measurements"))))
            mw, dbm, ma = self.machine.read_average_power(meter, samples)
            self._write_named_output(frame, statement, "Average power [mW]", mw)
            self._write_named_output(frame, statement, "Average power [dBm]", dbm)
            self._write_named_output(frame, statement, "Average power [mA]", ma)
            return
        if name == "SEQ::SUB_SYS_Gripper_OpenClose":
            gripper = self._str(frame, statement.parameters[0])
            command = self._str(frame, statement.parameters[1])
            self.machine.grippers[gripper] = command
            self._set_common_sequence_outputs(frame, statement, 0.0, "", name.removeprefix("SEQ::"))
            return
        if name == "SEQ::SUB_SYS_Vacuum_OnOff":
            channel = self._str(frame, statement.parameters[0])
            command = self._str(frame, statement.parameters[1]) if len(statement.parameters) > 1 else "On"
            self.machine.vacuums[channel] = command
            self._set_common_sequence_outputs(frame, statement, 0.0, "", name.removeprefix("SEQ::"))
            return
        if name == "SEQ::SUB_SysTimeHandler":
            if len(statement.parameters) >= 8:
                self._write_output(frame, statement.parameters[6], 0.0)
                self._write_output(frame, statement.parameters[7], "0.0 s")
            return
        sub_name = name.removeprefix("SEQ::")
        sub_path = self._resolve_process_sequence(statement.library, sub_name)
        if sub_path is not None:
            self._call_process_sequence(frame, statement, sub_path=sub_path)
            return
        self._unmodeled_sequence_error(frame, statement)

    def _set_common_sequence_outputs(
        self,
        frame: Frame,
        statement: YaseStatement,
        error: float,
        message: str,
        sequence_name: str,
    ) -> None:
        for parameter in statement.parameters:
            if parameter.direction != "Output":
                continue
            if parameter.name in {"Error", "ErrorType"}:
                self._write_output(frame, parameter, error)
            elif parameter.name in {"S_ErrorMessage", "ErrorMessage"}:
                self._write_output(frame, parameter, message)
            elif parameter.name == "SequenceName":
                self._write_output(frame, parameter, sequence_name)

    def _call_process_sequence(
        self,
        frame: Frame,
        statement: YaseStatement,
        *,
        sub_path: Path | None = None,
    ) -> None:
        sub_name = statement.name.removeprefix("SEQ::")
        if sub_path is None:
            sub_path = self._resolve_process_sequence(statement.library, sub_name)
        if sub_path is None:
            self._unmodeled_sequence_error(frame, statement)
            return
        child_inputs = {
            parameter.name: self._value(frame, parameter)
            for parameter in statement.parameters
            if parameter.direction == "Input"
        }
        child = Frame(sequence=self.load_sequence(sub_path), input_parameters=child_inputs)
        self._init_frame_variables(child)
        self._run_frame(child)
        aliases = {
            "Error": "ErrorType",
            "S_ErrorMessage": "ErrorMessage",
        }
        for parameter in statement.parameters:
            if parameter.direction != "Output":
                continue
            value = child.return_parameters.get(parameter.name)
            if value is None and parameter.name in aliases:
                value = child.return_parameters.get(aliases[parameter.name])
            if value is None and parameter.name == "SequenceName":
                value = sub_name
            if value is None:
                value = 0.0 if parameter.type_name in NUMERIC_TYPES else ""
            self._write_output(frame, parameter, value)

    def _resolve_process_sequence(self, library: str, sub_name: str) -> Path | None:
        library_lower = library.replace("/", "\\").lower()
        parts = [
            part
            for part in library.replace("/", "\\").split("\\")
            if part and part.lower() not in {"process", "system", "system..", "helper", "positioning"}
        ]
        if parts:
            candidate = self.machine.root.joinpath(*parts, f"{sub_name}.xseq")
            if candidate.exists():
                return candidate
        if "positioning" in library_lower:
            candidate = self.machine.root / "SUB_Positioning" / f"{sub_name}.xseq"
            if candidate.exists():
                return candidate
        matches = sorted(self.machine.root.rglob(f"{sub_name}.xseq"))
        return matches[0] if matches else None

    def _unmodeled_hardware_error(self, frame: Frame, statement: YaseStatement) -> None:
        message = f"Unmodeled hardware/product module: {statement.name!r} from {statement.library!r}"
        self.machine.note_missing(message)
        if self.strict_unknown:
            raise NotImplementedError(message)
        self.warn(message)
        if any(
            parameter.direction == "Output" and parameter.name in {"Error", "ErrorType"}
            for parameter in statement.parameters
        ):
            self._set_common_sequence_outputs(frame, statement, 1.0, message, "")

    def _unmodeled_sequence_error(self, frame: Frame, statement: YaseStatement) -> None:
        sub_name = statement.name.removeprefix("SEQ::")
        message = f"Unmodeled sequence call: {statement.name!r} from {statement.library!r}"
        self.machine.note_missing(message)
        if self.strict_unknown:
            raise NotImplementedError(message)
        self.warn(message)
        self._set_common_sequence_outputs(frame, statement, 1.0, message, sub_name)

    def _unknown_statement(self, statement: YaseStatement) -> None:
        message = f"Unsupported statement {statement.name!r} from {statement.library!r}"
        if self.strict_unknown:
            raise NotImplementedError(message)
        self.warn(message)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)
