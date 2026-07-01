from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import importlib
import json
import math
import re
import sys
import time


MACHINE_AXIS_TO_SIM_AXIS = {
    "X": "x",
    "Z": "y",
    "Y": "z",
}


STAGE_RE = re.compile(r"^(?P<body>Align|Camera)_(?P<axis>X|Y|Z)(?P<index>\d*)$")


def parse_number(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().strip('"')
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def strip_quotes(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().strip('"')


def merge_ini_values(store: "IniStore", values: dict[str, Any] | None) -> None:
    for section, section_values in (values or {}).items():
        for name, value in dict(section_values).items():
            store.set_value(str(section), str(name), value)


def split_stage(stage: str) -> tuple[str | None, str | None, str | None]:
    match = STAGE_RE.match(stage)
    if not match:
        return None, None, None
    body = match.group("body")
    index = match.group("index")
    if index:
        body = f"{body}{index}"
    return body, match.group("axis"), MACHINE_AXIS_TO_SIM_AXIS[match.group("axis")]


@dataclass
class IniStore:
    sections: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path) -> "IniStore":
        parser = ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        parser.read(path, encoding="ISO-8859-1")
        sections = {
            section: {key: value for key, value in parser.items(section)}
            for section in parser.sections()
        }
        return cls(sections=sections)

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "IniStore":
        sections: dict[str, dict[str, str]] = {}
        for section, values in (mapping or {}).items():
            sections[str(section)] = {
                str(key): str(value)
                for key, value in dict(values).items()
            }
        return cls(sections=sections)

    def has_key(self, section: str, name: str) -> bool:
        return section in self.sections and name in self.sections[section]

    def get_string(self, section: str, name: str) -> str | None:
        if not self.has_key(section, name):
            return None
        return strip_quotes(self.sections[section][name])

    def get_number(self, section: str, name: str) -> float | None:
        value = self.get_string(section, name)
        if value is None:
            return None
        return parse_number(value)

    def set_value(self, section: str, name: str, value: Any) -> None:
        self.sections.setdefault(section, {})[name] = str(value)


@dataclass
class MoveEvent:
    stage: str
    mode: str
    distance: float
    velocity: float
    sync: str
    before: float
    after: float
    sim_body: str | None
    sim_axis: str | None


@dataclass
class PowerReadEvent:
    meter: str
    mw: float
    dbm: float
    ma: float
    samples: int


@dataclass
class SimulationMachine:
    root: Path
    process_store: IniStore = field(default_factory=IniStore)
    system_store: IniStore = field(default_factory=IniStore)
    path_stores: dict[str, IniStore] = field(default_factory=dict)
    fiducialed: bool = True
    stage_positions: dict[str, float] = field(default_factory=dict)
    actors: dict[str, dict[str, float]] = field(default_factory=dict)
    digital_outputs: dict[str, str] = field(default_factory=dict)
    digital_inputs: dict[str, float] = field(default_factory=dict)
    analog_outputs: dict[str, float] = field(default_factory=dict)
    analog_inputs: dict[str, float] = field(default_factory=dict)
    grippers: dict[str, str] = field(default_factory=dict)
    vacuums: dict[str, str] = field(default_factory=dict)
    holder_map: dict[str, Any] = field(default_factory=dict)
    power_config: dict[str, Any] = field(default_factory=dict)
    dialog_policy: str = "button2"
    dropdown_selection: str = "Cancel"
    dropdown_selections: dict[str, str] = field(default_factory=dict)
    default_missing_number: float = 0.0
    default_missing_string: str = ""
    missing_information: list[str] = field(default_factory=list)
    move_log: list[MoveEvent] = field(default_factory=list)
    power_log: list[PowerReadEvent] = field(default_factory=list)
    io_log: list[str] = field(default_factory=list)

    @classmethod
    def from_config(cls, root: str | Path, config_path: str | Path | None = None) -> "SimulationMachine":
        root_path = Path(root)
        config: dict[str, Any] = {}
        if config_path:
            config = json.loads(Path(config_path).read_text(encoding="utf-8"))

        process_ini = config.get("process_ini", "Processvar.ini")
        process_path = Path(process_ini)
        if not process_path.is_absolute():
            process_path = root_path / process_path

        process_store = IniStore.from_file(process_path) if process_path.exists() else IniStore()
        merge_ini_values(process_store, config.get("process_variables"))
        system_store = IniStore.from_mapping(config.get("system_variables"))
        machine = cls(
            root=root_path,
            process_store=process_store,
            system_store=system_store,
            fiducialed=parse_bool(config.get("fiducialed", True)),
            dialog_policy=str(config.get("dialog_policy", "button2")).lower(),
            dropdown_selection=str(config.get("dropdown_selection", "Cancel")),
            dropdown_selections={
                str(key): str(value)
                for key, value in dict(config.get("dropdown_selections", {})).items()
            },
            default_missing_number=parse_number(config.get("default_missing_number", 0.0)),
            default_missing_string=str(config.get("default_missing_string", "")),
            holder_map=dict(config.get("holder_map", {})),
            power_config=dict(config.get("power", {})),
        )
        for stage, value in dict(config.get("initial_stage_positions", {})).items():
            machine.set_stage_position(stage, parse_number(value))
        machine.digital_outputs.update({str(k): str(v) for k, v in dict(config.get("digital_outputs", {})).items()})
        machine.digital_inputs.update({str(k): parse_number(v) for k, v in dict(config.get("digital_inputs", {})).items()})
        machine.analog_outputs.update({str(k): parse_number(v) for k, v in dict(config.get("analog_outputs", {})).items()})
        machine.analog_inputs.update({str(k): parse_number(v) for k, v in dict(config.get("analog_inputs", {})).items()})
        return machine

    def dropdown_selection_for(self, title: str) -> str:
        if title and title in self.dropdown_selections:
            return self.dropdown_selections[title]
        return self.dropdown_selection

    def set_stage_position(self, stage: str, position: float) -> None:
        self.stage_positions[stage] = float(position)
        body, _, sim_axis = split_stage(stage)
        if body and sim_axis:
            self.actors.setdefault(body, {"x": 0.0, "y": 0.0, "z": 0.0})[sim_axis] = float(position)

    def move_stage(self, stage: str, distance: float, velocity: float, sync: str, mode: str) -> MoveEvent:
        before = self.stage_positions.get(stage, 0.0)
        if mode.lower().startswith("abs"):
            after = float(distance)
        else:
            after = before + float(distance)
        self.set_stage_position(stage, after)
        body, _, sim_axis = split_stage(stage)
        event = MoveEvent(
            stage=stage,
            mode=mode,
            distance=float(distance),
            velocity=float(velocity),
            sync=sync,
            before=before,
            after=after,
            sim_body=body,
            sim_axis=sim_axis,
        )
        self.move_log.append(event)
        return event

    def query_stage(self, stage: str) -> float:
        return self.stage_positions.get(stage, 0.0)

    def get_store(self, source: str, path: str = "") -> IniStore:
        source_key = source.strip().lower()
        if source_key == "process":
            return self.process_store
        if source_key == "system":
            return self.system_store
        if source_key == "path":
            resolved = Path(path)
            if not resolved.is_absolute():
                resolved = self.root / resolved
            key = str(resolved)
            if key not in self.path_stores:
                self.path_stores[key] = IniStore.from_file(resolved) if resolved.exists() else IniStore()
            return self.path_stores[key]
        self.note_missing(f"Unknown INI source {source!r}; using process store.")
        return self.process_store

    def read_num_var(self, source: str, path: str, section: str, name: str) -> float:
        value = self.get_store(source, path).get_number(section, name)
        if value is None:
            self.note_missing(f"Missing numeric {source}/{section}/{name}; using {self.default_missing_number}.")
            return self.default_missing_number
        return value

    def read_string_var(self, source: str, path: str, section: str, name: str) -> str:
        value = self.get_store(source, path).get_string(section, name)
        if value is None:
            self.note_missing(f"Missing string {source}/{section}/{name}; using {self.default_missing_string!r}.")
            return self.default_missing_string
        return value

    def write_num_var(self, source: str, path: str, section: str, name: str, value: float) -> None:
        self.get_store(source, path).set_value(section, name, float(value))

    def write_string_var(self, source: str, path: str, section: str, name: str, value: str) -> None:
        self.get_store(source, path).set_value(section, name, value)

    def key_available(self, source: str, path: str, section: str, name: str) -> bool:
        return self.get_store(source, path).has_key(section, name)

    def read_power(self, meter: str) -> tuple[float, float, float]:
        meter = meter or "default"
        config = self.power_config.get(meter) or self.power_config.get("default") or {}
        model = str(config.get("model", "static")).lower()
        if model == "gaussian":
            mw = self._gaussian_power(config)
        elif model in {"gaussian_sim", "gaussian_ball_lens", "external_gaussian_ball_lens"}:
            mw = self._external_gaussian_ball_lens_power(config)
        else:
            mw = parse_number(config.get("mw", self.power_config.get(meter, 0.0)))
        dbm = 10.0 * math.log10(mw) if mw > 0 else float("-inf")
        ma = mw * parse_number(config.get("ma_per_mw", 1.0), 1.0)
        return mw, dbm, ma

    def read_average_power(self, meter: str, samples: int) -> tuple[float, float, float]:
        mw, dbm, ma = self.read_power(meter)
        self.power_log.append(PowerReadEvent(meter=meter, mw=mw, dbm=dbm, ma=ma, samples=max(samples, 1)))
        return mw, dbm, ma

    def _gaussian_power(self, config: dict[str, Any]) -> float:
        floor = parse_number(config.get("floor_mw", 0.0))
        peak = parse_number(config.get("peak_mw", 1.0))
        total_exponent = 0.0
        actor_configs = config.get("actors")
        if actor_configs:
            for actor, actor_config in dict(actor_configs).items():
                total_exponent += self._actor_gaussian_exponent(str(actor), dict(actor_config))
        else:
            actor = str(config.get("actor", "Align1"))
            total_exponent += self._actor_gaussian_exponent(actor, config)
        return floor + peak * math.exp(-0.5 * total_exponent)

    def _actor_gaussian_exponent(self, actor: str, config: dict[str, Any]) -> float:
        position = self.actors.get(actor, {"x": 0.0, "y": 0.0, "z": 0.0})
        target = dict(config.get("target", {}))
        sigma = config.get("sigma_um", 10.0)
        exponent = 0.0
        for axis in ("x", "y", "z"):
            axis_sigma = parse_number(dict(sigma).get(axis, 10.0)) if isinstance(sigma, dict) else parse_number(sigma, 10.0)
            if axis_sigma <= 0:
                continue
            delta = position.get(axis, 0.0) - parse_number(target.get(axis, 0.0))
            exponent += (delta / axis_sigma) ** 2
        return exponent

    def _external_gaussian_ball_lens_power(self, config: dict[str, Any]) -> float:
        sim_path = config.get("path") or self.power_config.get("gaussian_sim_path")
        if not sim_path:
            self.note_missing("Gaussian sim power model needs power.gaussian_sim_path or per-meter path.")
            return 0.0
        resolved = Path(str(sim_path))
        if not resolved.is_absolute():
            resolved = self.root / resolved
        if not resolved.exists():
            self.note_missing(f"Gaussian sim path does not exist: {resolved}")
            return 0.0

        path_text = str(resolved)
        if path_text not in sys.path:
            sys.path.insert(0, path_text)
        try:
            interactive_setup = importlib.import_module("interactive_setup")
        except Exception as exc:  # pragma: no cover - only reached when external sim is unavailable.
            self.note_missing(f"Could not import Gaussian sim interactive_setup from {resolved}: {exc}")
            return 0.0

        source = interactive_setup.LaserSource()
        self._apply_offset_config(source, config.get("source_offset_um"), ("x_offset", "y_offset"))
        self._apply_angle_config(source, config.get("source_angle_mrad"), ("x_angle", "y_angle"))
        if "source_power_mw" in config:
            source.power = parse_number(config["source_power_mw"]) * 1e-3

        balls, tapers, final_z = interactive_setup.default_ball_lens_layout()
        lens_actor_map = list(config.get("lens_actor_map", ["Align1", "Align2"]))
        stage_um_to_m = parse_number(config.get("stage_um_to_m", 1e-6), 1e-6)
        for index, actor_name in enumerate(lens_actor_map):
            if index >= len(balls):
                break
            actor = self.actors.get(str(actor_name), {})
            balls[index].x_offset += parse_number(actor.get("x", 0.0)) * stage_um_to_m
            balls[index].y_offset += parse_number(actor.get("y", 0.0)) * stage_um_to_m
            balls[index].position += parse_number(actor.get("z", 0.0)) * stage_um_to_m

        if tapers:
            self._apply_offset_config(tapers[0], config.get("taper_offset_um"), ("x_offset", "y_offset"))

        try:
            results = interactive_setup.simulate_layout(
                [source],
                [],
                [],
                final_z,
                balls=balls,
                tapers=tapers,
            )
        except Exception as exc:  # pragma: no cover - model errors depend on external setup values.
            self.note_missing(f"Gaussian sim evaluation failed: {exc}")
            return 0.0

        if not results or not results[0].taper_results:
            self.note_missing("Gaussian sim produced no taper power result.")
            return 0.0
        return parse_number(results[0].taper_results[0].received_power) * 1e3

    def _apply_offset_config(self, target: Any, offsets_um: Any, attrs: tuple[str, str]) -> None:
        if not offsets_um:
            return
        offsets = dict(offsets_um)
        setattr(target, attrs[0], parse_number(offsets.get("x", 0.0)) * 1e-6)
        setattr(target, attrs[1], parse_number(offsets.get("y", 0.0)) * 1e-6)

    def _apply_angle_config(self, target: Any, angles_mrad: Any, attrs: tuple[str, str]) -> None:
        if not angles_mrad:
            return
        angles = dict(angles_mrad)
        setattr(target, attrs[0], parse_number(angles.get("x", 0.0)) * 1e-3)
        setattr(target, attrs[1], parse_number(angles.get("y", 0.0)) * 1e-3)

    def set_digital_output(self, line: str, state: str) -> float:
        self.digital_outputs[line] = state
        self.io_log.append(f"SetDigOut {line}={state}")
        return time.monotonic()

    def get_digital_output(self, line: str) -> tuple[float, float]:
        state = self.digital_outputs.get(line, "Off")
        numeric = 1.0 if str(state).lower() == "on" else parse_number(state)
        return numeric, time.monotonic()

    def get_digital_input(self, line: str) -> tuple[float, float]:
        if line not in self.digital_inputs:
            self.note_missing(f"Missing digital input {line}; using 0.")
        return self.digital_inputs.get(line, 0.0), time.monotonic()

    def set_analog_output(self, line: str, value: float) -> float:
        self.analog_outputs[line] = float(value)
        self.io_log.append(f"SetAnalogOut {line}={value}")
        return time.monotonic()

    def get_analog_input(self, line: str) -> tuple[float, float]:
        if line not in self.analog_inputs:
            self.note_missing(f"Missing analog input {line}; using 0.")
        return self.analog_inputs.get(line, 0.0), time.monotonic()

    def note_missing(self, message: str) -> None:
        if message not in self.missing_information:
            self.missing_information.append(message)

    def snapshot(self) -> dict[str, Any]:
        return {
            "fiducialed": self.fiducialed,
            "stage_positions": dict(sorted(self.stage_positions.items())),
            "simulation_actors": {
                actor: dict(sorted(coords.items()))
                for actor, coords in sorted(self.actors.items())
            },
            "digital_outputs": dict(sorted(self.digital_outputs.items())),
            "digital_inputs": dict(sorted(self.digital_inputs.items())),
            "analog_outputs": dict(sorted(self.analog_outputs.items())),
            "analog_inputs": dict(sorted(self.analog_inputs.items())),
            "holder_map": self.holder_map,
            "missing_information": list(self.missing_information),
        }
