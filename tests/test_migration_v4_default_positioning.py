import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STANDARD_POSITIONS = ROOT / "Standard position images" / "v2" / "standard_positions.json"
V4 = ROOT / "migrations" / "migration_v4"
V4_DEFAULT_POSITIONS = V4 / "default_positions.json"
V4_DEFAULT_POSITION_DIR = V4 / "SUB_default_positioning"
V4_VISION_SEQUENCE = V4 / "SUB_vision_recognition" / "SUB_OpenVisionRecognitionLab_ReadOnly.xseq"
V4_XSEQ_FILES = sorted(V4.glob("SUB_*/*.xseq"))
SOURCE_VISION_RUNTIME = ROOT / "vision recognition lab" / "vision_recognition_lab.py"


def _slug(value):
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return re.sub(r"_+", "_", clean).strip("_") or "position"


def _default_position_path(position):
    return V4_DEFAULT_POSITION_DIR / (
        f"SUB_DefaultPosition_{position['id']}_{_slug(position['label'])}.xseq"
    )


def _statements(path):
    return ET.parse(path).getroot().findall("Statement")


def _params_by_name(statement):
    return {parameter.attrib["Name"]: parameter.attrib for parameter in statement.findall("Parameter")}


def _statement_names(path):
    return [statement.attrib["Name"] for statement in _statements(path)]


def _string_values(path):
    return [
        parameter.attrib.get("StringValue", "")
        for parameter in ET.parse(path).getroot().iter("Parameter")
        if "StringValue" in parameter.attrib
    ]


def _float_string(value):
    value = float(value)
    if value.is_integer():
        return f"{int(value)}.0"
    return f"{value:.12g}"


def _expected_moves(position):
    stage_map = {
        "tower_1": {"x": "Align_X1", "y": "Align_Y1", "z": "Align_Z1"},
        "tower_2": {"x": "Align_X2", "y": "Align_Y2", "z": "Align_Z2"},
        "camera": {"x": "Camera_X", "y": "Camera_Y", "z": "Camera_Z"},
    }
    order = [
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
    moves = []
    machine_positions = position.get("machine_positions_um") or {}
    for device, axis_map in stage_map.items():
        values = machine_positions.get(device) or {}
        for axis, stage in axis_map.items():
            value = values.get(axis)
            if value is not None:
                moves.append((stage, _float_string(value)))

    settings = position.get("camera_settings") or {}
    zoom = settings.get("zoom")
    if isinstance(zoom, dict):
        zoom = zoom.get("value")
    if zoom is not None:
        moves.append(("Zoom", _float_string(zoom)))

    return sorted(moves, key=lambda item: order.index(item[0]))


def _expected_exposure(position):
    exposure = (position.get("camera_settings") or {}).get("exposure")
    if isinstance(exposure, dict):
        exposure = exposure.get("value")
    return None if exposure is None else _float_string(exposure)


def test_v4_default_positions_match_standard_position_source():
    assert json.loads(V4_DEFAULT_POSITIONS.read_text(encoding="utf-8")) == json.loads(
        STANDARD_POSITIONS.read_text(encoding="utf-8")
    )


def test_v4_copy_layout_has_loose_runtime_python_files():
    assert (V4 / "vision_recognition_lab.py").is_file()
    assert (V4 / "requirements.txt").is_file()
    assert (V4 / "default_positions.json").is_file()
    assert not (V4 / "dev_side").exists()
    assert (V4 / "vision_recognition_lab.py").read_text(encoding="utf-8") == (
        SOURCE_VISION_RUNTIME.read_text(encoding="utf-8")
    )


def test_v4_xseq_files_parse_and_all_goto_targets_exist():
    assert V4_XSEQ_FILES
    for path in V4_XSEQ_FILES:
        statements = _statements(path)
        labels = {statement.attrib.get("Label", "") for statement in statements}
        labels.discard("")
        assert all(not label.startswith("@") for label in labels), path

        for statement in statements:
            if statement.attrib["Name"] != "Goto":
                continue
            target = _params_by_name(statement)["Label"]["StringValue"]
            assert target in labels, f"{path}: Goto target {target!r} not found"


def test_v4_xseq_files_do_not_use_stale_machine_fields():
    all_text = "\n".join(path.read_text(encoding="ISO-8859-1") for path in V4_XSEQ_FILES)

    forbidden = [
        "Python_310_ALIGNMENT_TEST",
        "Python_37_PYTHON_AUTOMATION_INTERPRETER",
        "Input JSON",
        "Result JSON",
        "#SM_PROCESS#",
        "C:\\Users\\",
        "OneDrive",
        "VelocityCameraXSlow",
        "VelocityCameraSlow",
    ]
    for value in forbidden:
        assert value not in all_text


def test_v4_default_sequences_are_direct_and_do_not_read_process_position_targets():
    for path in V4_DEFAULT_POSITION_DIR.glob("SUB_DefaultPosition_*.xseq"):
        names = _statement_names(path)
        strings = _string_values(path)

        assert "SEQ::SUB_ApplyDefaultPositionMove" not in names
        assert "SEQ::SUB_ApplyDefaultPositionExposure" not in names
        assert "TMPython_ExecuteScript" not in names
        assert "DeclareStrParam" not in names
        assert "DeclareNumParam" not in names
        assert "WriteToFile" not in names
        assert "Process" not in strings

        for statement in _statements(path):
            if statement.attrib["Name"] != "GetNumVar":
                continue
            params = _params_by_name(statement)
            assert params["from"]["StringValue"] == "System"
            assert params["Section"]["StringValue"] == "MainVelocity"


def test_v4_default_sequences_match_known_default_position_targets():
    defaults = json.loads(V4_DEFAULT_POSITIONS.read_text(encoding="utf-8"))["positions"]

    for position in defaults:
        path = _default_position_path(position)
        assert path.is_file()
        statements = _statements(path)
        move_targets = []
        exposures = []

        for statement in statements:
            params = _params_by_name(statement)
            if statement.attrib["Name"] == "MoveStage":
                move_targets.append(
                    (
                        params["Stage"]["StringValue"],
                        params["Distance [um]"]["StringValue"],
                    )
                )
            if statement.attrib["Name"] == "SetAnalogOut":
                assert params["Analog Line"]["StringValue"] == "cam_12_ExpTime"
                exposures.append(params["Value"]["StringValue"])

        assert move_targets == _expected_moves(position)

        expected_exposure = _expected_exposure(position)
        if expected_exposure is None:
            assert exposures == []
        else:
            assert exposures == [expected_exposure]

        if not move_targets and expected_exposure is None:
            assert "52.0" in _string_values(path)


def test_v4_default_sequences_use_fast_camera_velocity_and_wait_pattern():
    velocity_by_stage = {
        "Camera_X": "d_Vel_Camera_XFast",
        "Camera_Z": "d_Vel_Camera_XFast",
        "Camera_Y": "d_Vel_Camera_Fast",
        "Zoom": "d_Vel_Zoom",
        "Align_X1": "d_Vel_Align_Fast",
        "Align_Z1": "d_Vel_Align_Fast",
        "Align_Y1": "d_Vel_Align_Fast",
        "Align_X2": "d_Vel_Align_Fast",
        "Align_Z2": "d_Vel_Align_Fast",
        "Align_Y2": "d_Vel_Align_Fast",
    }

    for path in V4_DEFAULT_POSITION_DIR.glob("SUB_DefaultPosition_*.xseq"):
        statements = _statements(path)
        names = [statement.attrib["Name"] for statement in statements]
        move_indices = [i for i, name in enumerate(names) if name == "MoveStage"]
        if not move_indices:
            assert "DisplayExtdSelectionDialog" not in names
            continue

        assert "StageCheckAllFiducialed" in names
        assert "DisplayExtdSelectionDialog" in names
        assert names.index("StageCheckAllFiducialed") < move_indices[0]
        assert names.index("DisplayExtdSelectionDialog") < move_indices[0]
        assert "SEQ::SUB_SYS_AxisWaitFinishList" in names

        for index in move_indices:
            params = _params_by_name(statements[index])
            stage = params["Stage"]["StringValue"]
            assert params["Velocity [um/s]"]["VariableName"] == velocity_by_stage[stage]
            assert params["Sync"]["StringValue"] == "No sync"
            assert params["rel/abs"]["StringValue"] == "Absolute"


def test_v4_vision_launcher_uses_loose_module_and_verified_tmpython_fields():
    statements = _statements(V4_VISION_SEQUENCE)
    tmpython = next(statement for statement in statements if statement.attrib["Name"] == "TMPython_ExecuteScript")
    params = _params_by_name(tmpython)

    assert params["Interpreter"]["StringValue"] == "Python_310_PYTHON_AUTOMATION_INTERPRETER"
    assert params["Module"]["StringValue"] == "vision_recognition_lab"
    assert params["Class"]["StringValue"] == "VisionRecognitionLabStep"
    assert params["ParamIn"]["VariableName"] == "s_PythonInputJson"
    assert params["ParamOut"]["VariableName"] == "s_PythonResultJson"

    display_after_tmpython = statements[statements.index(tmpython) + 1]
    display_params = _params_by_name(display_after_tmpython)
    assert display_after_tmpython.attrib["Name"] == "DisplayStatus"
    assert display_params["Status text"]["VariableName"] == "s_PythonResultJson"
    assert "relative measurement summary" in display_params["Status text"]["Description"]

    comments = " ".join(comment.text or "" for comment in ET.parse(V4_VISION_SEQUENCE).getroot().findall("Comment"))
    assert "relative_measurement" in comments
    assert "yase_display" in comments


def test_v4_vision_result_paramout_is_only_used_as_string_status_and_log_data():
    result_json_uses = []
    for statement in _statements(V4_VISION_SEQUENCE):
        for parameter in statement.findall("Parameter"):
            if parameter.attrib.get("VariableName") == "s_PythonResultJson":
                result_json_uses.append(
                    (
                        statement.attrib["Name"],
                        parameter.attrib["Name"],
                        parameter.attrib["Type"],
                        parameter.attrib["Direction"],
                    )
                )

    assert result_json_uses == [
        ("TMPython_ExecuteScript", "ParamOut", "String", "Output"),
        ("DisplayStatus", "Status text", "String", "Input"),
        ("WriteToFile", "Data", "String", "Input"),
    ]
    assert "MoveStage" not in _statement_names(V4_VISION_SEQUENCE)
    assert "SetAnalogOut" not in _statement_names(V4_VISION_SEQUENCE)
