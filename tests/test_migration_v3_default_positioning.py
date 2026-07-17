import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from migrations.migration_v3.dev_side.python_default_positioning.default_position_move_planner import (
    plan_default_position_move,
)


ROOT = Path(__file__).resolve().parents[1]
STANDARD_POSITIONS = ROOT / "Standard position images" / "v2" / "standard_positions.json"
V3_DEFAULT_POSITIONS = (
    ROOT
    / "migrations"
    / "migration_v3"
    / "dev_side"
    / "python_default_positioning"
    / "default_positions.json"
)
YASE_APPLY_MOVE_SEQUENCE = (
    ROOT
    / "migrations"
    / "migration_v3"
    / "SUB_default_positioning"
    / "SUB_ApplyDefaultPositionMove.xseq"
)
YASE_APPLY_EXPOSURE_SEQUENCE = (
    ROOT
    / "migrations"
    / "migration_v3"
    / "SUB_default_positioning"
    / "SUB_ApplyDefaultPositionExposure.xseq"
)
YASE_DEFAULT_POSITION_DIR = ROOT / "migrations" / "migration_v3" / "SUB_default_positioning"
V3_XSEQ_FILES = sorted(YASE_DEFAULT_POSITION_DIR.glob("*.xseq"))


def _payload(target_id="3.0.0", **extra):
    payload = {
        "schema_version": 3,
        "target_id": target_id,
        "default_positions_path": str(V3_DEFAULT_POSITIONS),
        "limits": {"max_single_move_um": 200000, "max_exposure": 500000},
        "algorithm": {"name": "default_position_move_planner", "tolerance_um": 0.05},
    }
    payload.update(extra)
    return payload


def _statement_names(path):
    return [statement.attrib["Name"] for statement in ET.parse(path).getroot().findall("Statement")]


def _statements(path):
    return ET.parse(path).getroot().findall("Statement")


def _string_values(path):
    return [
        parameter.attrib.get("StringValue", "")
        for parameter in ET.parse(path).getroot().iter("Parameter")
        if "StringValue" in parameter.attrib
    ]


def _parameter_names(path):
    return [parameter.attrib["Name"] for parameter in ET.parse(path).getroot().iter("Parameter")]


def _params_by_name(statement):
    return {parameter.attrib["Name"]: parameter.attrib for parameter in statement.findall("Parameter")}


def _first_statement(path, name):
    for statement in _statements(path):
        if statement.attrib["Name"] == name:
            return statement
    raise AssertionError(f"{name} not found in {path}")


def _slug(value):
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return re.sub(r"_+", "_", clean).strip("_") or "position"


def _default_position_path(position):
    return YASE_DEFAULT_POSITION_DIR / (
        f"SUB_DefaultPosition_{position['id']}_{_slug(position['label'])}.xseq"
    )


def _float_string(value):
    value = float(value)
    if value.is_integer():
        return f"{int(value)}.0"
    return f"{value:.12g}"


def test_v3_deployable_default_positions_matches_standard_position_source():
    assert json.loads(V3_DEFAULT_POSITIONS.read_text(encoding="utf-8")) == json.loads(
        STANDARD_POSITIONS.read_text(encoding="utf-8")
    )


def test_v3_standard_position_ids_use_semantic_versions():
    defaults = json.loads(V3_DEFAULT_POSITIONS.read_text(encoding="utf-8"))

    assert [position["id"] for position in defaults["positions"]] == [
        "1.0.0",
        "2.0.0",
        "3.0.0",
        "4.0.0",
        "5.0.0",
        "6.0.0",
    ]


def test_v3_planner_maps_standard_position_3_0_0_to_stage_and_exposure_actions():
    result = plan_default_position_move(_payload("3.0.0"))

    assert result["schema_version"] == 3
    assert result["action"] == "move_stage"
    assert result["stage1"] == "Camera_X"
    assert result["target1_um"] == -38997.0
    assert result["move_mode1"] == "Absolute"
    assert result["confirm_text1"].startswith("Default position 3.0.0")

    actions = result["planned_actions"]
    assert [action["action_type"] for action in actions] == [
        "move_stage",
        "move_stage",
        "move_stage",
        "move_stage",
        "move_stage",
        "move_stage",
        "move_stage",
        "set_analog",
    ]
    assert [action.get("stage") or action.get("analog_line") for action in actions] == [
        "Camera_X",
        "Camera_Z",
        "Zoom",
        "Camera_Y",
        "Align_X1",
        "Align_Z1",
        "Align_Y1",
        "cam_12_ExpTime",
    ]
    assert actions[-1]["value"] == 10000.0
    assert all(action["confirm_required"] for action in actions)


def test_v3_planner_reports_special_positions_without_turning_them_into_moves():
    result = plan_default_position_move(_payload("1.0.0"))

    assert result["action"] == "move_stage"
    assert result["state"]["special_position_fields"]["tower_1"]["z_near_vacuum"] == -5000
    assert {
        (action.get("stage"), action.get("target_um"))
        for action in result["planned_actions"]
        if action["action_type"] == "move_stage"
    } >= {
        ("Align_X1", -13800.0),
        ("Align_Y1", 800.0),
        ("Align_Z1", 34400.0),
        ("Camera_X", -74997.0),
        ("Camera_Y", -45997.0),
        ("Camera_Z", -60395.0),
    }
    assert ("Align_Z1", -5000.0) not in {
        (action.get("stage"), action.get("target_um")) for action in result["planned_actions"]
    }


def test_v3_planner_fails_closed_for_unknown_position_2_0_0():
    result = plan_default_position_move(_payload("2.0.0"))

    assert result["action"] == "abort"
    assert "no known stage targets" in result["message"]


def test_v3_planner_skips_stage_targets_already_at_current_position():
    current_positions = {
        "Camera_X": -38997,
        "Camera_Z": -97694,
        "Zoom": 0,
        "Camera_Y": -45997,
        "Align_X1": 19000,
        "Align_Z1": 499,
        "Align_Y1": 5800,
    }
    result = plan_default_position_move(_payload("3.0.0", current_positions_um=current_positions))

    assert result["action"] == "set_analog"
    assert result["analog_line1"] == "cam_12_ExpTime"
    assert result["analog_value1"] == 10000.0
    assert result["planned_action_count"] == 1


def test_v3_xseq_paths_match_python_automation_machine_config():
    all_text = "\n".join(path.read_text(encoding="ISO-8859-1") for path in V3_XSEQ_FILES)

    assert "SUB_DefaultPositionPlanner_ReadOnly" not in all_text
    assert "TMPython_ExecuteScript" not in all_text
    assert "WriteToFile" not in all_text
    assert "default_positions_path" not in all_text
    assert "default_position_move_input" not in all_text
    assert "default_position_move_result" not in all_text
    assert "Python_310_PYTHON_AUTOMATION_INTERPRETER" not in all_text
    assert "Python_310_ALIGNMENT_TEST" not in all_text
    assert "Python_37_PYTHON_AUTOMATION_INTERPRETER" not in all_text
    assert "#SM_PROCESS#" not in all_text
    assert "C:\\Users\\" not in all_text
    assert "OneDrive" not in all_text

    for path in YASE_DEFAULT_POSITION_DIR.glob("SUB_DefaultPosition_*.xseq"):
        for statement in _statements(path):
            if statement.attrib["Name"].startswith("SEQ::SUB_ApplyDefaultPosition"):
                assert statement.attrib["Library"] == r"process\SUB_default_positioning"


def test_v3_apply_move_sequence_has_popup_before_absolute_movestage():
    names = _statement_names(YASE_APPLY_MOVE_SEQUENCE)
    string_values = _string_values(YASE_APPLY_MOVE_SEQUENCE)
    dialog_params = _params_by_name(_first_statement(YASE_APPLY_MOVE_SEQUENCE, "DisplayExtdSelectionDialog"))

    assert "DeclareStrParam" in names
    assert "DeclareNumParam" in names
    assert "StageCheckAllFiducialed" in names
    assert "QueryStage" in names
    assert "Math_Absolute" in names
    assert "InRange" in names
    assert "DisplayExtdSelectionDialog" in names
    assert names.count("MoveStage") == 1
    assert "SetStrNum" in names
    assert "SEQ::SUB_SYS_AxisWaitFinishList" in names
    assert "SEQ::SUB_SysCheckAxisMove" in names
    assert {
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
    }.issubset(set(string_values))
    assert {
        "VelocityAlignXSlow",
        "VelocityCameraXSlow",
        "VelocityCameraSlow",
        "VelocityZoom",
    }.issubset(set(string_values))
    assert "Abort" in string_values
    assert "Move" in string_values
    assert "Absolute" in string_values
    assert " | Stage to move: " in string_values
    assert " | Absolute target [um]: " in string_values
    assert " | Current [um]: " in string_values
    assert " | Delta [um]: " in string_values
    assert dialog_params["Dialog text"]["VariableName"] == "s_MovePopupText"
    assert names.index("DisplayExtdSelectionDialog") < names.index("MoveStage")


def test_v3_apply_exposure_sequence_has_popup_before_setanalogout():
    names = _statement_names(YASE_APPLY_EXPOSURE_SEQUENCE)
    string_values = _string_values(YASE_APPLY_EXPOSURE_SEQUENCE)
    dialog_params = _params_by_name(_first_statement(YASE_APPLY_EXPOSURE_SEQUENCE, "DisplayExtdSelectionDialog"))

    assert "DeclareStrParam" in names
    assert "DeclareNumParam" in names
    assert "InRange" in names
    assert "DisplayExtdSelectionDialog" in names
    assert names.count("SetAnalogOut") == 1
    assert "SetStrNum" in names
    assert "MoveStage" not in names
    assert "cam_12_ExpTime" in string_values
    assert "Abort" in string_values
    assert "Set" in string_values
    assert " | Analog line: " in string_values
    assert " | Target value: " in string_values
    assert dialog_params["Dialog text"]["VariableName"] == "s_ExposurePopupText"
    assert names.index("DisplayExtdSelectionDialog") < names.index("SetAnalogOut")


def test_v3_has_one_full_wrapper_sequence_for_each_default_position():
    default_positions = json.loads(V3_DEFAULT_POSITIONS.read_text(encoding="utf-8"))["positions"]
    wrapper_paths = sorted(YASE_DEFAULT_POSITION_DIR.glob("SUB_DefaultPosition_*.xseq"))

    assert wrapper_paths == sorted(_default_position_path(position) for position in default_positions)


def test_v3_full_wrapper_sequences_match_planned_actions_and_do_not_touch_hardware_directly():
    default_positions = json.loads(V3_DEFAULT_POSITIONS.read_text(encoding="utf-8"))["positions"]

    for position in default_positions:
        result = plan_default_position_move(_payload(position["id"]))
        path = _default_position_path(position)
        statements = _statements(path)
        names = [statement.attrib["Name"] for statement in statements]
        string_values = _string_values(path)
        shrink_collapsed = [
            shrink.attrib.get("Collapsed") for shrink in ET.parse(path).getroot().findall("Shrink")
        ]

        assert "MoveStage" not in names
        assert "SetAnalogOut" not in names
        assert "DisplayStatus" in names
        assert "ReturnNumParam" in names
        assert "ReturnStrParam" in names
        assert "true" in shrink_collapsed

        calls = [
            statement
            for statement in statements
            if statement.attrib["Name"].startswith("SEQ::SUB_ApplyDefaultPosition")
        ]
        if result["action"] == "abort":
            assert not calls
            assert "52.0" in string_values
            assert any("aborted before hardware" in value for value in string_values)
            continue

        actions = result["planned_actions"]
        assert len(calls) == len(actions)
        for call, action in zip(calls, actions):
            params = _params_by_name(call)
            assert call.attrib["Library"] == r"process\SUB_default_positioning"
            assert params["ErrorType"]["Direction"] == "Output"
            assert params["ErrorType"]["VariableName"] == "d_ErrorType"
            assert params["ErrorMessage"]["Direction"] == "Output"
            assert params["ErrorMessage"]["VariableName"] == "s_ErrorMessage"
            assert params["ConfirmText"]["StringValue"] == action["confirm_text"]

            if action["action_type"] == "move_stage":
                assert call.attrib["Name"] == "SEQ::SUB_ApplyDefaultPositionMove"
                assert params["Stage"]["StringValue"] == action["stage"]
                assert params["TargetUm"]["StringValue"] == _float_string(action["target_um"])
                assert params["MaxSingleMoveUm"]["StringValue"] == _float_string(
                    action["max_single_move_um"]
                )
                assert "Move one stage only" in action["confirm_text"]
                assert "Absolute target" in action["confirm_text"]
                assert action["stage"] in action["confirm_text"]
            else:
                assert call.attrib["Name"] == "SEQ::SUB_ApplyDefaultPositionExposure"
                assert params["AnalogLine"]["StringValue"] == action["analog_line"]
                assert params["Value"]["StringValue"] == _float_string(action["value"])
                assert params["MaxExposure"]["StringValue"] == _float_string(action["max_exposure"])
                assert "Set one camera value only" in action["confirm_text"]
                assert "Target value" in action["confirm_text"]
                assert action["analog_line"] in action["confirm_text"]

            call_index = statements.index(call)
            assert names[call_index + 1] == "ifnum"
            assert names[call_index + 2] == "Goto"


def test_v3_full_wrapper_sequences_include_zoom_and_exposure_when_known():
    sequence_3_0_0_values = _string_values(
        _default_position_path({"id": "3.0.0", "label": "cam_view_1_wide"})
    )
    sequence_6_0_0_values = _string_values(
        _default_position_path({"id": "6.0.0", "label": "full_above_trench"})
    )

    assert "Zoom" in sequence_3_0_0_values
    assert "0.0" in sequence_3_0_0_values
    assert "cam_12_ExpTime" in sequence_3_0_0_values
    assert "10000.0" in sequence_3_0_0_values
    assert "Zoom" in sequence_6_0_0_values
    assert "2500.0" in sequence_6_0_0_values
    assert "cam_12_ExpTime" not in sequence_6_0_0_values
