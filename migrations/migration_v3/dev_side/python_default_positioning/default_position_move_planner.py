"""Default-position movement planner for TMPython/YASE migration v3.

This module turns the standard-position JSON into a YASE-friendly action plan.
It does not move hardware. YASE must still validate each returned action,
display an operator confirmation popup, execute the single stage/IO operation,
wait for motion completion where applicable, and check machine errors.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - developer machines do not have TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback so the planner can be tested outside TestMaster."""


SCHEMA_VERSION = 3
DEFAULT_POSITIONS_FILENAME = "default_positions.json"
DEFAULT_TOLERANCE_UM = 0.05
DEFAULT_MAX_SINGLE_MOVE_UM = 200000.0
DEFAULT_MAX_EXPOSURE = 500000.0
EXPOSURE_ANALOG_LINE = "cam_12_ExpTime"

DEVICE_STAGE_MAP = {
    "tower_1": {"x": "Align_X1", "y": "Align_Y1", "z": "Align_Z1"},
    "tower_2": {"x": "Align_X2", "y": "Align_Y2", "z": "Align_Z2"},
    "camera": {"x": "Camera_X", "y": "Camera_Y", "z": "Camera_Z"},
}

ACTION_ORDER = (
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
    EXPOSURE_ANALOG_LINE,
)

ALLOWED_MOVE_STAGES = {
    "Align_X1",
    "Align_Y1",
    "Align_Z1",
    "Align_X2",
    "Align_Y2",
    "Align_Z2",
    "Camera_X",
    "Camera_Y",
    "Camera_Z",
    "Zoom",
}

JsonDict = dict[str, Any]
LEGACY_DEFAULT_POSITION_ID_RE = re.compile(r"^\d{3}$")


class DefaultPositionMovePlannerStep(TMPythonStatementJ):
    """TMPython statement class returning a default-position action plan."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return plan_default_position_move(params_in)
        except Exception as exc:  # fail closed for the machine call
            return abort_response(f"DefaultPositionMovePlannerStep failed: {exc}")


def plan_default_position_move(params_in: JsonDict) -> JsonDict:
    """Return a full plan and the first action for the requested default position."""

    require_schema(params_in)
    target_key = normalize_default_position_id(
        params_in.get("target_id") or params_in.get("target_label") or ""
    )
    if not target_key:
        raise ValueError("target_id or target_label is required")

    defaults = load_default_positions(params_in)
    target = find_target(defaults, target_key)
    actions, skipped = actions_for_target(target, params_in)
    if not actions:
        return abort_response(
            f"default position {target_key!r} has no known stage targets or camera settings",
            state=state_for(target, actions, skipped),
        )

    index = action_index(params_in)
    if index >= len(actions):
        return done_response(
            f"default position {target['id']} ({target['label']}) plan is complete",
            state=state_for(target, actions, skipped),
        )

    action = actions[index]
    return action_response(action, actions, target, skipped)


def load_default_positions(params: JsonDict) -> JsonDict:
    inline = params.get("default_positions")
    if isinstance(inline, dict):
        return inline

    path_value = params.get("default_positions_path")
    if path_value:
        path = Path(str(path_value))
        if not path.is_file():
            raise ValueError(f"default_positions_path does not exist: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    local_path = Path(__file__).with_name(DEFAULT_POSITIONS_FILENAME)
    if local_path.is_file():
        return json.loads(local_path.read_text(encoding="utf-8"))

    raise ValueError(
        "default positions were not supplied; pass default_positions, "
        "default_positions_path, or deploy default_positions.json next to this module"
    )


def find_target(defaults: JsonDict, target_key: str) -> JsonDict:
    positions = defaults.get("positions")
    if not isinstance(positions, list):
        raise ValueError("default positions JSON must contain a positions list")
    for raw in positions:
        position = as_dict(raw)
        if normalize_default_position_id(position.get("id", "")) == target_key:
            return position
        if str(position.get("label", "")).strip() == target_key:
            return position
    available = [normalize_default_position_id(as_dict(item).get("id", "")) for item in positions]
    raise ValueError(f"unknown default position {target_key!r}; available ids: {', '.join(available)}")


def normalize_default_position_id(value: Any) -> str:
    text = str(value).strip()
    if LEGACY_DEFAULT_POSITION_ID_RE.fullmatch(text):
        return f"{int(text)}.0.0"
    return text


def actions_for_target(target: JsonDict, params: JsonDict) -> tuple[list[JsonDict], list[JsonDict]]:
    machine_positions = as_dict(target.get("machine_positions_um"))
    current_positions = current_positions_um(params)
    tolerance = tolerance_um(params)
    max_move = max_single_move_um(params)

    actions: list[JsonDict] = []
    skipped: list[JsonDict] = []
    for device, axis_map in DEVICE_STAGE_MAP.items():
        device_positions = as_dict(machine_positions.get(device))
        for axis, stage in axis_map.items():
            value = device_positions.get(axis)
            if value is None:
                skipped.append({"device": device, "axis": axis, "stage": stage, "reason": "unknown"})
                continue
            target_um = finite_float(value, f"{target.get('id')}.{device}.{axis}")
            current_um = current_positions.get(stage)
            delta_um: float | None = None
            if current_um is not None:
                delta_um = target_um - current_um
                if abs(delta_um) <= tolerance:
                    skipped.append({"device": device, "axis": axis, "stage": stage, "reason": "already_at_target"})
                    continue
                if abs(delta_um) > max_move:
                    raise ValueError(
                        f"{stage} delta {delta_um:.6g} um exceeds max_single_move_um {max_move:.6g}"
                    )
            actions.append(
                {
                    "action_type": "move_stage",
                    "stage": stage,
                    "target_um": target_um,
                    "distance_um": target_um,
                    "delta_um": delta_um,
                    "mode": "absolute",
                    "sync": "No sync",
                    "device": device,
                    "axis": axis,
                    "confirm_required": True,
                    "confirm_text": confirm_move_text(target, stage, current_um, target_um, delta_um),
                    "max_single_move_um": max_move,
                }
            )

    zoom_value = nested_setting_value(target, "zoom")
    if zoom_value is not None:
        zoom_target = finite_float(zoom_value, f"{target.get('id')}.camera_settings.zoom")
        current_um = current_positions.get("Zoom")
        delta_um = None if current_um is None else zoom_target - current_um
        if delta_um is None or abs(delta_um) > tolerance:
            if delta_um is not None and abs(delta_um) > max_move:
                raise ValueError(f"Zoom delta {delta_um:.6g} exceeds max_single_move_um {max_move:.6g}")
            actions.append(
                {
                    "action_type": "move_stage",
                    "stage": "Zoom",
                    "target_um": zoom_target,
                    "distance_um": zoom_target,
                    "delta_um": delta_um,
                    "mode": "absolute",
                    "sync": "No sync",
                    "device": "camera",
                    "axis": "zoom",
                    "confirm_required": True,
                    "confirm_text": confirm_move_text(target, "Zoom", current_um, zoom_target, delta_um),
                    "max_single_move_um": max_move,
                }
            )
        else:
            skipped.append({"device": "camera", "axis": "zoom", "stage": "Zoom", "reason": "already_at_target"})

    exposure_value = nested_setting_value(target, "exposure")
    if exposure_value is not None:
        exposure = finite_float(exposure_value, f"{target.get('id')}.camera_settings.exposure")
        max_exposure = max_exposure_value(params)
        if exposure < 0.0 or exposure > max_exposure:
            raise ValueError(f"exposure {exposure:.6g} is outside [0, {max_exposure:.6g}]")
        actions.append(
            {
                "action_type": "set_analog",
                "analog_line": EXPOSURE_ANALOG_LINE,
                "value": exposure,
                "device": "camera",
                "setting": "exposure",
                "confirm_required": True,
                "confirm_text": confirm_exposure_text(target, exposure),
                "max_exposure": max_exposure,
            }
        )

    actions.sort(key=action_sort_key)
    for index, action in enumerate(actions, start=1):
        action["id"] = index
    return actions, skipped


def nested_setting_value(target: JsonDict, name: str) -> Any:
    setting = as_dict(as_dict(target.get("camera_settings")).get(name))
    if "value" in setting:
        return setting["value"]
    raw = as_dict(target.get("camera_settings")).get(name)
    if raw is None or isinstance(raw, dict):
        return None
    return raw


def action_sort_key(action: JsonDict) -> tuple[int, int]:
    key = str(action.get("stage") or action.get("analog_line") or "")
    try:
        return (ACTION_ORDER.index(key), int(action.get("id") or 0))
    except ValueError:
        return (len(ACTION_ORDER), int(action.get("id") or 0))


def action_response(action: JsonDict, actions: list[JsonDict], target: JsonDict, skipped: list[JsonDict]) -> JsonDict:
    state = state_for(target, actions, skipped)
    action_type = str(action["action_type"])
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": action_type,
        "action_type1": action_type,
        "action_index1": int(action["id"]) - 1,
        "target_id": target.get("id"),
        "target_label": target.get("label"),
        "planned_action_count": len(actions),
        "planned_actions": actions,
        "skipped_targets": skipped,
        "confirm_text1": action["confirm_text"],
        "message": f"default position {target.get('id')} returning action {action['id']} of {len(actions)}",
        "state": state,
    }
    if action_type == "move_stage":
        result.update(
            {
                "move_count": 1,
                "stage1": action["stage"],
                "distance1_um": action["distance_um"],
                "target1_um": action["target_um"],
                "delta1_um": action["delta_um"],
                "delta1_known": action["delta_um"] is not None,
                "move_mode1": "Absolute",
                "max_single_move_um": action["max_single_move_um"],
            }
        )
    elif action_type == "set_analog":
        result.update(
            {
                "move_count": 0,
                "stage1": "",
                "distance1_um": 0.0,
                "target1_um": 0.0,
                "delta1_um": None,
                "delta1_known": False,
                "move_mode1": "",
                "analog_line1": action["analog_line"],
                "analog_value1": action["value"],
                "max_exposure1": action["max_exposure"],
            }
        )
    else:  # pragma: no cover - internal invariant
        raise ValueError(f"unsupported action type {action_type!r}")
    return result


def state_for(target: JsonDict, actions: list[JsonDict], skipped: list[JsonDict]) -> JsonDict:
    return {
        "algorithm": "default_position_move_planner",
        "target_id": target.get("id"),
        "target_label": target.get("label"),
        "planned_actions": actions,
        "planned_action_count": len(actions),
        "skipped_targets": skipped,
        "stage_mapping": DEVICE_STAGE_MAP,
        "allowed_move_stages": sorted(ALLOWED_MOVE_STAGES),
        "exposure_analog_line": EXPOSURE_ANALOG_LINE,
        "special_position_fields": special_position_fields(target),
    }


def special_position_fields(target: JsonDict) -> JsonDict:
    result: JsonDict = {}
    for device, values in as_dict(target.get("machine_positions_um")).items():
        extras = {
            key: value
            for key, value in as_dict(values).items()
            if key not in {"x", "y", "z"} and value is not None
        }
        if extras:
            result[str(device)] = extras
    return result


def done_response(message: str, state: JsonDict | None = None) -> JsonDict:
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "done",
        "action_type1": "done",
        "move_count": 0,
        "stage1": "",
        "distance1_um": 0.0,
        "target1_um": 0.0,
        "delta1_um": 0.0,
        "delta1_known": True,
        "move_mode1": "",
        "planned_actions": [],
        "planned_action_count": 0,
        "message": message,
    }
    if state is not None:
        result["state"] = state
    return result


def abort_response(message: str, state: JsonDict | None = None) -> JsonDict:
    result: JsonDict = {
        "schema_version": SCHEMA_VERSION,
        "action": "abort",
        "action_type1": "abort",
        "move_count": 0,
        "stage1": "",
        "distance1_um": 0.0,
        "target1_um": 0.0,
        "delta1_um": 0.0,
        "delta1_known": True,
        "move_mode1": "",
        "planned_actions": [],
        "planned_action_count": 0,
        "message": message,
    }
    if state is not None:
        result["state"] = state
    return result


def confirm_move_text(
    target: JsonDict,
    stage: str,
    current_um: float | None,
    target_um: float,
    delta_um: float | None,
) -> str:
    prefix = (
        f"Default position {target.get('id')} ({target.get('label')}). "
        f"Move one stage only. Stage: {stage}. Absolute target: {target_um:.6g} um"
    )
    if current_um is None or delta_um is None:
        return f"{prefix}."
    return f"{prefix}. Current: {current_um:.6g} um. Delta: {delta_um:.6g} um."


def confirm_exposure_text(target: JsonDict, exposure: float) -> str:
    return (
        f"Default position {target.get('id')} ({target.get('label')}). "
        f"Set one camera value only. Analog line: {EXPOSURE_ANALOG_LINE}. "
        f"Target value: {exposure:.6g}."
    )


def require_schema(params: JsonDict) -> None:
    version = params.get("schema_version")
    if int(version or 0) != SCHEMA_VERSION:
        raise ValueError(f"unsupported schema_version {version!r}; expected {SCHEMA_VERSION}")


def action_index(params: JsonDict) -> int:
    raw = as_dict(params.get("state")).get("action_index", params.get("action_index", 0))
    try:
        index = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("action_index must be an integer") from exc
    if index < 0:
        raise ValueError("action_index must be non-negative")
    return index


def current_positions_um(params: JsonDict) -> dict[str, float]:
    raw = as_dict(params.get("current_positions_um") or as_dict(params.get("machine")).get("positions_um"))
    positions: dict[str, float] = {}
    for stage, value in raw.items():
        stage_name = str(stage)
        if stage_name in ALLOWED_MOVE_STAGES:
            positions[stage_name] = finite_float(value, f"current_positions_um.{stage_name}")
    return positions


def tolerance_um(params: JsonDict) -> float:
    value = as_dict(params.get("algorithm")).get("tolerance_um", DEFAULT_TOLERANCE_UM)
    result = finite_float(value, "algorithm.tolerance_um")
    if result < 0.0:
        raise ValueError("algorithm.tolerance_um must be non-negative")
    return result


def max_single_move_um(params: JsonDict) -> float:
    limits = as_dict(params.get("limits"))
    value = limits.get("max_single_move_um", DEFAULT_MAX_SINGLE_MOVE_UM)
    result = finite_float(value, "limits.max_single_move_um")
    if result <= 0.0:
        raise ValueError("limits.max_single_move_um must be positive")
    return result


def max_exposure_value(params: JsonDict) -> float:
    limits = as_dict(params.get("limits"))
    value = limits.get("max_exposure", DEFAULT_MAX_EXPOSURE)
    result = finite_float(value, "limits.max_exposure")
    if result <= 0.0:
        raise ValueError("limits.max_exposure must be positive")
    return result


def as_dict(value: Any) -> JsonDict:
    return value if isinstance(value, dict) else {}


def finite_float(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


if __name__ == "__main__":  # pragma: no cover - local manual smoke helper
    import sys

    payload = json.load(sys.stdin)
    print(json.dumps(plan_default_position_move(payload), indent=2, sort_keys=True))
