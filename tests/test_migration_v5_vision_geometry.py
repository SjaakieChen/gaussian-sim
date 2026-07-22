import copy
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import migrations.migration_v5.python_vision_geometry.sequence_memory_workflow as sequence_memory_workflow_module
from migrations.migration_v5.python_vision_geometry.position_bias_planner import plan_biased_close_positions
from migrations.migration_v5.python_vision_geometry.macro_alignment_simulator import simulate_macro_alignment
from migrations.migration_v5.python_vision_geometry.sequence_geometry_memory import solve_sequence_geometry
from migrations.migration_v5.python_vision_geometry.sequence_memory_workflow import (
    build_macro_payload_from_sequence_memory,
    initialize_sequence_memory,
    main as sequence_memory_workflow_main,
    next_motion_or_capture_step,
    next_sequence_action_from_sequence_memory,
    record_sequence_capture,
    review_and_record_next_capture,
    run_sequence_memory_workflow,
    solve_macro_alignment_from_sequence_memory,
    solve_sequence_geometry_from_sequence_memory,
)
from migrations.migration_v5.python_vision_geometry.standard_capture_evidence import build_standard_capture_evidence
from migrations.migration_v5.python_vision_geometry.vision_geometry_solver import solve_common_geometry


ROOT = Path(__file__).resolve().parents[1]
V5 = ROOT / "migrations" / "migration_v5"
PLAN = V5 / "measurement_plan.json"
EXAMPLE_INPUT = V5 / "python_vision_geometry" / "examples" / "vision_geometry_input.json"
BIAS_EXAMPLE_INPUT = V5 / "python_vision_geometry" / "examples" / "position_bias_input.json"
BIAS_AUTO_EXAMPLE_INPUT = V5 / "python_vision_geometry" / "examples" / "position_bias_auto_input.json"
SEQUENCE_EXAMPLE_INPUT = V5 / "python_vision_geometry" / "examples" / "sequence_geometry_memory_input.json"
MACRO_AUTO_EXAMPLE_INPUT = V5 / "python_vision_geometry" / "examples" / "macro_alignment_auto_input.json"
V4_STANDARD_POSITIONS = ROOT / "Standard position images" / "v4" / "standard_positions.json"
V5_YASE_SOLVE_SEQUENCE = V5 / "SUB_vision_geometry" / "SUB_V5MacroAlignmentSolve_ReadOnly.xseq"
V5_YASE_INIT_SEQUENCE = V5 / "SUB_vision_geometry" / "SUB_V5SequenceMemoryInit_ReadOnly.xseq"
V5_YASE_NEXT_SEQUENCE = V5 / "SUB_vision_geometry" / "SUB_V5SequenceMemoryNextAction_ReadOnly.xseq"
V5_YASE_CAPTURE_REVIEW_SEQUENCE = V5 / "SUB_vision_geometry" / "SUB_V5CaptureReviewRecord_ReadOnly.xseq"
V5_YASE_FINAL_WORKFLOW_SEQUENCE = V5 / "SUB_vision_geometry" / "SUB_V5MacroAlignmentFinalWorkflow_Guarded.xseq"
V5_XSEQ_FILES = sorted((V5 / "SUB_vision_geometry").glob("*.xseq"))


def _session(edge_x, edge_y):
    return {
        "relative_measurement": {
            "edge_midpoint_relative_um": {
                "x": edge_x,
                "y": edge_y,
                "distance": math.hypot(edge_x, edge_y),
            },
            "circles": [
                {
                    "selection_index": 1,
                    "x_um": 0.0,
                    "y_um": 0.0,
                    "distance_um": 0.0,
                }
            ],
        }
    }


def _payload():
    return {
        "schema_version": 1,
        "max_axis_disagreement_um": 10.0,
        "captures": [
            {
                "capture_id": "2.5.1",
                "target": "ball_1",
                "view": "top_xz",
                "session": _session(30.0, -120.0),
            },
            {
                "capture_id": "2.6.1",
                "target": "ball_1",
                "view": "mirror_side_xy",
                "session": _session(-121.0, -12.0),
            },
            {
                "capture_id": "4.5.1",
                "target": "ball_2",
                "view": "top_xz",
                "session": _session(-42.0, -820.0),
            },
            {
                "capture_id": "4.6.2",
                "target": "ball_2",
                "view": "mirror_side_xy",
                "session": _session(-818.0, 15.0),
            },
        ],
    }


def _selected_circle_session(x, y):
    return {
        "selected_recognition": {
            "roi_1": [
                {
                    "shape_kind": "circle",
                    "shape": {
                        "x": x,
                        "y": y,
                        "radius": 40.0,
                    },
                }
            ]
        }
    }


def _selected_rectangle_session():
    return {
        "selected_recognition": {
            "roi_1": [
                {
                    "shape_kind": "rectangle",
                    "shape": {
                        "corners": [
                            {"x": 0.0, "y": 0.0},
                            {"x": 200.0, "y": 0.0},
                            {"x": 200.0, "y": 100.0},
                            {"x": 0.0, "y": 100.0},
                        ]
                    },
                }
            ]
        }
    }


def _live_machine_positions():
    return {
        "camera": {"x": -38997.0, "y": -45395.0, "z": -93995.0},
        "tower_1": {"x": 5331.0, "y": 12291.0, "z": 15198.0},
        "tower_2": {"x": 1674.0, "y": 12441.0, "z": 13205.0},
    }


def _xseq_root(path):
    return ET.parse(path).getroot()


def _xseq_statements(path):
    return _xseq_root(path).findall("Statement")


def _params_by_name(statement):
    return {parameter.attrib["Name"]: parameter.attrib for parameter in statement.findall("Parameter")}


def _statement_names(path):
    return [statement.attrib["Name"] for statement in _xseq_statements(path)]


def _string_values(path):
    return [
        parameter.attrib.get("StringValue", "")
        for parameter in _xseq_root(path).iter("Parameter")
        if "StringValue" in parameter.attrib
    ]


def _goto_targets(path):
    targets = []
    for statement in _xseq_statements(path):
        if statement.attrib["Name"] != "Goto":
            continue
        params = _params_by_name(statement)
        targets.append(params["Label"]["StringValue"])
    return targets


def _v5_json_from_xseq(path):
    setstring = next(
        statement
        for statement in _xseq_statements(path)
        if statement.attrib.get("Label") == "L_BuildPythonInput"
    )
    return json.loads(_params_by_name(setstring)["String 1"]["StringValue"])


def _v5_solver_json_from_xseq():
    return _v5_json_from_xseq(V5_YASE_SOLVE_SEQUENCE)


def _standard_positions_for_sequence(*, top_ball_camera_x=-38997, top_ball_camera_z=-93995):
    return {
        "positions": [
            {
                "id": "2.4",
                "captured_images": ["newhead/2.4.1.PNG"],
                "machine_positions_um": {
                    "camera": {"x": -38997, "y": -45395, "z": -93995},
                },
            },
            {
                "id": "2.5",
                "captured_images": ["newhead/2.5.1.PNG"],
                "machine_positions_um": {
                    "camera": {"x": top_ball_camera_x, "y": -44996, "z": top_ball_camera_z},
                },
            },
        ]
    }


def _sequence_payload():
    return {
        "schema_version": 1,
        "standard_positions": _standard_positions_for_sequence(),
        "targets": ["ball_1"],
        "sessions": {
            "2.4.1": _selected_rectangle_session(),
            "2.5.1": _selected_circle_session(112.0, 44.0),
        },
    }


def _bias_payload():
    return {
        "schema_version": 1,
        "standard_positions_path": str(V4_STANDARD_POSITIONS),
        "default_close_position_bias": {
            "max_bias_um": 350.0,
            "fail_if_gross_offset_exceeds_um": 900.0,
        },
        "gross_observations": [
            {
                "target": "ball_1",
                "gross_capture_id": "2.1.1",
                "um_per_pixel": 2.0,
                "baseline_session": _selected_circle_session(100.0, 200.0),
                "candidate_session": _selected_circle_session(120.0, 190.0),
            }
        ],
    }


def test_migration_v5_plan_sequences_gross_then_top_then_side_views():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))

    assert plan["schema_version"] == 1
    assert plan["calibration"]["reference_edge_length_um"] == pytest.approx(500.0)
    assert plan["steps"][0]["capture_id"] == "2.1.1"
    assert plan["steps"][0]["result_use"] == "coarse_position_bias_only"
    assert plan["steps"][0]["close_position_ids"] == ["2.2", "2.3", "2.4", "2.5", "2.6"]
    assert plan["steps"][4]["capture_id"] == "4.1.1"
    assert plan["steps"][4]["close_position_ids"] == ["4.2", "4.3", "4.4", "4.5", "4.6.1", "4.6.2"]
    assert [step["capture_id"] for step in plan["steps"]] == [
        "2.1.1",
        "2.4.1",
        "2.5.1",
        "2.6.1",
        "4.1.1",
        "4.4.1",
        "4.5.1",
        "4.6.2",
    ]
    assert plan["view_mappings"]["top_xz"]["image_y"]["axis"] == "machine_x_um"
    assert plan["view_mappings"]["mirror_side_xy"]["image_y"]["axis"] == "machine_y_um"
    assert plan["default_close_position_bias"]["default_gross_view_mapping"]["image_x"]["tower_axis"] == "z"
    assert plan["default_close_position_bias"]["requires_um_per_pixel"] is True
    assert plan["sequence_capture_memory"]["guarded_motion_yase_sequence"] == (
        "SUB_vision_geometry/SUB_V5MacroAlignmentFinalWorkflow_Guarded.xseq"
    )
    assert any("No motion" in item or "motion" in item for item in plan["fail_closed_requirements"])


def test_migration_v5_yase_sequences_parse_and_keep_motion_in_guarded_apply_layer():
    assert V5_XSEQ_FILES
    for path in V5_XSEQ_FILES:
        root = _xseq_root(path)
        assert root.tag == "Sequence"
        names = _statement_names(path)
        assert "MoveStage" not in names
        assert "SetAnalogOut" not in names
        if path == V5_YASE_CAPTURE_REVIEW_SEQUENCE:
            assert names.count("Grab") == 1
            assert names.count("IMAQWriteFile") == 1
        else:
            assert "Grab" not in names
            assert "IMAQWriteFile" not in names
        if path == V5_YASE_FINAL_WORKFLOW_SEQUENCE:
            assert names.count("SEQ::SUB_ApplyDefaultPositionMove") == 1
        else:
            assert "SEQ::SUB_ApplyDefaultPositionMove" not in names


def test_migration_v5_yase_goto_targets_match_statement_labels():
    for path in V5_XSEQ_FILES:
        labels = {statement.attrib.get("Label", "") for statement in _xseq_statements(path)}
        for target in _goto_targets(path):
            assert target in labels, f"{path.name} Goto target {target!r} does not match any label"


def test_migration_v5_yase_sequences_do_not_use_stale_machine_fields():
    all_text = "\n".join(path.read_text(encoding="ISO-8859-1") for path in V5_XSEQ_FILES)

    forbidden = [
        "Python_310_ALIGNMENT_TEST",
        "Python_37_PYTHON_AUTOMATION_INTERPRETER",
        "Input JSON",
        "Result JSON",
        "#SM_PROCESS#",
        "C:\\Users\\",
        "OneDrive",
    ]
    for value in forbidden:
        assert value not in all_text


def test_migration_v5_yase_macro_solve_uses_verified_tmpython_bridge():
    statements = _xseq_statements(V5_YASE_SOLVE_SEQUENCE)
    names = [statement.attrib["Name"] for statement in statements]
    tmpython = next(statement for statement in statements if statement.attrib["Name"] == "TMPython_ExecuteScript")
    params = _params_by_name(tmpython)

    assert names == [
        "****",
        "DisplayStatus",
        "SetString",
        "SetStringVar",
        "WriteToFile",
        "TMPython_ExecuteScript",
        "DisplayStatus",
        "SetStringVar",
        "WriteToFile",
        "EndSeq",
    ]
    assert params["Interpreter"]["StringValue"] == "Python_310_PYTHON_AUTOMATION_INTERPRETER"
    assert params["Module"]["StringValue"] == "python_vision_geometry.sequence_memory_workflow"
    assert params["Class"]["StringValue"] == "VisionSequenceMemoryWorkflowStep"
    assert params["ParamIn"]["VariableName"] == "s_PythonInputJson"
    assert params["ParamOut"]["VariableName"] == "s_PythonResultJson"

    string_values = _string_values(V5_YASE_SOLVE_SEQUENCE)
    assert any(value.endswith(r"python_env\log\v5_macro_alignment_input.json") for value in string_values)
    assert any(value.endswith(r"python_env\log\v5_macro_alignment_result.json") for value in string_values)


def test_migration_v5_yase_sequence_memory_init_and_next_use_verified_tmpython_bridge():
    expected_commands = {
        V5_YASE_INIT_SEQUENCE: "init",
        V5_YASE_NEXT_SEQUENCE: "next_action",
    }

    for path, expected_command in expected_commands.items():
        statements = _xseq_statements(path)
        tmpython = next(statement for statement in statements if statement.attrib["Name"] == "TMPython_ExecuteScript")
        params = _params_by_name(tmpython)
        payload = _v5_json_from_xseq(path)

        assert params["Interpreter"]["StringValue"] == "Python_310_PYTHON_AUTOMATION_INTERPRETER"
        assert params["Module"]["StringValue"] == "python_vision_geometry.sequence_memory_workflow"
        assert params["Class"]["StringValue"] == "VisionSequenceMemoryWorkflowStep"
        assert params["ParamIn"]["VariableName"] == "s_PythonInputJson"
        assert params["ParamOut"]["VariableName"] == "s_PythonResultJson"
        assert payload["schema_version"] == 1
        assert payload["command"] == expected_command

    init_payload = _v5_json_from_xseq(V5_YASE_INIT_SEQUENCE)
    assert init_payload["standard_positions_path"] == (
        "D:/TestMasterData/Process/Python_Automation/python_env/standard_positions_v4/standard_positions.json"
    )
    assert init_payload["output_path"] == "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_sequence_memory.json"
    assert "\\" not in init_payload["standard_positions_path"]
    assert "\\" not in init_payload["output_path"]
    assert init_payload["physical_constants_um"] == {
        "laser_rectangle_short_edge_um": 500.0,
        "ball_diameter_um": 500.0,
        "trench_depth_um": 300.0,
    }
    assert init_payload["apply_remembered_focus_planes"] is False

    next_payload = _v5_json_from_xseq(V5_YASE_NEXT_SEQUENCE)
    assert next_payload["memory_path"] == "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_sequence_memory.json"
    assert next_payload["output_path"] == "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_sequence_next_action.json"
    assert "\\" not in next_payload["memory_path"]
    assert "\\" not in next_payload["output_path"]


def test_migration_v5_yase_capture_review_sequence_is_short_live_ui_bridge():
    statements = _xseq_statements(V5_YASE_CAPTURE_REVIEW_SEQUENCE)
    names = [statement.attrib["Name"] for statement in statements]

    assert "StageCheckAllFiducialed" in names
    assert names.count("Grab") == 1
    assert names.count("IMAQWriteFile") == 1
    assert names.count("QueryStage") == 9
    assert names.count("SetStrNum") == 9
    assert "MoveStage" not in names
    assert "SetAnalogOut" not in names
    assert names.index("Grab") < names.index("IMAQWriteFile") < names.index("TMPython_ExecuteScript")

    tmpython = next(statement for statement in statements if statement.attrib["Name"] == "TMPython_ExecuteScript")
    params = _params_by_name(tmpython)
    assert params["Interpreter"]["StringValue"] == "Python_310_PYTHON_AUTOMATION_INTERPRETER"
    assert params["Module"]["StringValue"] == "python_vision_geometry.sequence_memory_workflow"
    assert params["Class"]["StringValue"] == "VisionSequenceReviewRecordStep"
    assert params["ParamIn"]["VariableName"] == "s_PythonInputJson"
    assert params["ParamOut"]["VariableName"] == "s_PythonResultJson"

    query_params = [
        _params_by_name(statement)
        for statement in statements
        if statement.attrib["Name"] == "QueryStage"
    ]
    assert [params["Stage"]["StringValue"] for params in query_params] == [
        "Camera_X",
        "Camera_Y",
        "Camera_Z",
        "Align_X1",
        "Align_Y1",
        "Align_Z1",
        "Align_X2",
        "Align_Y2",
        "Align_Z2",
    ]
    assert all(params["Query"]["StringValue"] == "Absolute" for params in query_params)

    string_values = _string_values(V5_YASE_CAPTURE_REVIEW_SEQUENCE)
    assert "review_and_record_next_capture" in "\n".join(string_values)
    assert "D:/TestMasterData/data/Python_Automation/python_vision_input.bmp" in "\n".join(string_values)
    assert "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_sequence_memory.json" in "\n".join(
        string_values
    )
    for field in (
        "camera_x_um",
        "camera_y_um",
        "camera_z_um",
        "tower_1_x_um",
        "tower_1_y_um",
        "tower_1_z_um",
        "tower_2_x_um",
        "tower_2_y_um",
        "tower_2_z_um",
    ):
        assert f'"{field}"' in "\n".join(string_values)


def test_migration_v5_yase_final_workflow_is_short_guarded_python_loop():
    statements = _xseq_statements(V5_YASE_FINAL_WORKFLOW_SEQUENCE)
    names = [statement.attrib["Name"] for statement in statements]

    assert "StageCheckAllFiducialed" in names
    assert names.count("QueryStage") == 9
    assert names.count("SetStrNum") == 9
    assert names.count("TMPython_ExecuteScript") == 1
    assert names.count("JSON_GetFieldValueBoolean") == 1
    assert names.count("JSON_GetFieldValueNumeric") == 2
    assert names.count("JSON_GetFieldValueString") == 3
    assert names.count("SEQ::SUB_ApplyDefaultPositionMove") == 1
    assert names.count("SEQ::SUB_V5CaptureReviewRecord_ReadOnly") == 1
    assert names.count("SEQ::SUB_V5MacroAlignmentSolve_ReadOnly") == 1
    assert "MoveStage" not in names
    assert "SetAnalogOut" not in names
    assert "Grab" not in names
    assert "IMAQWriteFile" not in names

    tmpython = next(statement for statement in statements if statement.attrib["Name"] == "TMPython_ExecuteScript")
    params = _params_by_name(tmpython)
    assert params["Interpreter"]["StringValue"] == "Python_310_PYTHON_AUTOMATION_INTERPRETER"
    assert params["Module"]["StringValue"] == "python_vision_geometry.sequence_memory_workflow"
    assert params["Class"]["StringValue"] == "VisionSequenceMemoryWorkflowStep"
    assert params["ParamIn"]["VariableName"] == "s_PythonInputJson"
    assert params["ParamOut"]["VariableName"] == "s_PythonResultJson"

    query_params = [
        _params_by_name(statement)
        for statement in statements
        if statement.attrib["Name"] == "QueryStage"
    ]
    assert [params["Stage"]["StringValue"] for params in query_params] == [
        "Camera_X",
        "Camera_Y",
        "Camera_Z",
        "Align_X1",
        "Align_Y1",
        "Align_Z1",
        "Align_X2",
        "Align_Y2",
        "Align_Z2",
    ]
    assert all(params["Query"]["StringValue"] == "Absolute" for params in query_params)

    json_paths = [
        _params_by_name(statement)["Path"]["StringValue"]
        for statement in statements
        if statement.attrib["Name"].startswith("JSON_GetFieldValue")
    ]
    assert json_paths == ["ok", "schema_version", "action", "stage1", "target1_um", "confirm_text1"]

    apply_move = next(statement for statement in statements if statement.attrib["Name"] == "SEQ::SUB_ApplyDefaultPositionMove")
    assert apply_move.attrib["Library"] == r"process\SUB_default_positioning"
    apply_params = _params_by_name(apply_move)
    assert apply_params["Stage"]["VariableName"] == "s_MoveStage"
    assert apply_params["TargetUm"]["VariableName"] == "d_TargetUm"
    assert apply_params["MaxSingleMoveUm"]["NumericValue"] == "200000.0"
    assert apply_params["ConfirmText"]["VariableName"] == "s_ConfirmText"
    assert apply_params["ErrorType"]["VariableName"] == "d_ErrorType"
    assert apply_params["ErrorMessage"]["VariableName"] == "s_ErrorMessage"

    string_values = "\n".join(_string_values(V5_YASE_FINAL_WORKFLOW_SEQUENCE))
    assert "next_motion_or_capture" in string_values
    assert "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_sequence_memory.json" in string_values
    assert "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_final_workflow_result.json" in string_values
    assert "move_to_next_capture" in string_values
    assert "capture_review_record_required" in string_values
    assert "solve_ready" in string_values
    assert r"D:\TestMasterData\Process\Python_Automation\python_env\log" in string_values


def test_migration_v5_yase_macro_solve_embedded_json_matches_machine_contract():
    payload = _v5_solver_json_from_xseq()

    assert payload["schema_version"] == 1
    assert payload["command"] == "solve_macro"
    memory = payload["memory"]
    assert memory["action"] == "v5_sequence_capture_memory"
    assert memory["standard_positions_path"] == (
        "D:/TestMasterData/Process/Python_Automation/python_env/standard_positions_v4/standard_positions.json"
    )
    assert "\\" not in memory["standard_positions_path"]
    assert memory["physical_constants_um"] == {
        "laser_rectangle_short_edge_um": 500.0,
        "ball_diameter_um": 500.0,
        "trench_depth_um": 300.0,
    }
    assert memory["machine_y_source"] == "trench_model"
    assert memory["auto_detect_gross_sessions"] is True
    assert memory["auto_detect_missing_sessions"] is True
    assert memory["auto_detect_side_references"] is True


def test_migration_v5_yase_result_paramout_is_only_parsed_by_guarded_final_workflow():
    for path in V5_XSEQ_FILES:
        result_json_uses = []
        for statement in _xseq_statements(path):
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

        display_and_log_uses = [
            ("TMPython_ExecuteScript", "ParamOut", "String", "Output"),
            ("DisplayStatus", "Status text", "String", "Input"),
            ("SetStringVar", "VarStringIn", "String", "Input"),
            ("WriteToFile", "Data", "String", "Input"),
        ]
        if path != V5_YASE_FINAL_WORKFLOW_SEQUENCE:
            assert result_json_uses == display_and_log_uses
            continue

        assert result_json_uses[:4] == display_and_log_uses
        assert result_json_uses[4:] == [
            ("JSON_GetFieldValueBoolean", "JSONString in", "String", "Input"),
            ("JSON_GetFieldValueNumeric", "JSONString in", "String", "Input"),
            ("JSON_GetFieldValueString", "JSONString in", "String", "Input"),
            ("JSON_GetFieldValueString", "JSONString in", "String", "Input"),
            ("JSON_GetFieldValueNumeric", "JSONString in", "String", "Input"),
            ("JSON_GetFieldValueString", "JSONString in", "String", "Input"),
        ]
        assert "SEQ::SUB_ApplyDefaultPositionMove" in _statement_names(path)


def test_migration_v5_bias_planner_adjusts_close_positions_from_gross_ball_shift():
    result = plan_biased_close_positions(_bias_payload())

    assert result["ok"] is True
    assert result["action"] == "biased_close_positions_planned"
    plan = result["plans"][0]
    assert plan["target"] == "ball_1"
    assert plan["pixel_shift"] == {"x": 20.0, "y": -10.0}
    assert plan["raw_bias_um"] == {"z": 40.0, "x": -20.0}
    assert plan["applied_bias_um"] == {"z": 40.0, "x": -20.0}
    assert plan["bias_mapping"] == {
        "image_x": {"tower_axis": "z", "sign": 1.0},
        "image_y": {"tower_axis": "x", "sign": 1.0},
    }
    assert plan["bias_mapping_evidence"]["calibration_status"] == "not_motion_calibrated"
    assert plan["bias_mapping_evidence"]["use_for_motion"] is False
    assert plan["bias_mapping_evidence"]["operator_review_required"] is True
    assert plan["close_position_ids"] == ["2.2", "2.3", "2.4", "2.5", "2.6"]

    planned_2_3 = next(position for position in plan["planned_positions"] if position["id"] == "2.3")
    tower_1 = planned_2_3["machine_positions_um"]["tower_1"]
    camera = planned_2_3["machine_positions_um"]["camera"]
    assert tower_1["x"] == pytest.approx(5311.0)
    assert tower_1["y"] == pytest.approx(12291.0)
    assert tower_1["z"] == pytest.approx(15238.0)
    assert camera == {"x": -38997, "y": -45996, "z": -97694}
    assert planned_2_3["bias_plan"]["camera_positions_unchanged"] is True
    assert planned_2_3["bias_plan"]["use_for_motion"] is False
    assert planned_2_3["bias_plan"]["operator_review_required"] is True


def test_migration_v5_bias_planner_clips_bias_before_close_position_output():
    payload = _bias_payload()
    payload["gross_observations"][0]["candidate_session"] = _selected_circle_session(400.0, 200.0)

    result = plan_biased_close_positions(payload)

    assert result["ok"] is True
    plan = result["plans"][0]
    assert plan["raw_bias_um"]["z"] == pytest.approx(600.0)
    assert plan["applied_bias_um"]["z"] == pytest.approx(350.0)
    assert plan["bias_clipped"] is True
    assert plan["clipped_axes"] == ["z"]


def test_migration_v5_bias_planner_fails_closed_when_gross_offset_is_too_large():
    payload = _bias_payload()
    payload["gross_observations"][0]["candidate_session"] = _selected_circle_session(600.0, 200.0)

    result = plan_biased_close_positions(payload)

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "exceeds fail_if_gross_offset_exceeds_um" in result["status"]


def test_migration_v5_bias_planner_requires_scale_for_gross_pixel_shift():
    payload = _bias_payload()
    del payload["gross_observations"][0]["um_per_pixel"]

    result = plan_biased_close_positions(payload)

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "must include um_per_pixel" in result["status"]
    assert "estimate_um_per_pixel_from_ball_diameter" in result["status"]


def test_migration_v5_bias_planner_rebases_close_positions_from_live_gross_coordinates():
    payload = _bias_payload()
    payload["gross_observations"][0]["candidate_machine_positions_um"] = {
        "camera": {"x": -38000.0, "y": -45000.0, "z": -93000.0},
        "tower_1": {"x": 6000.0, "y": 13000.0, "z": 16000.0},
    }

    result = plan_biased_close_positions(payload)

    assert result["ok"] is True
    plan = result["plans"][0]
    planned_2_4 = next(position for position in plan["planned_positions"] if position["id"] == "2.4")
    assert planned_2_4["machine_positions_um"]["camera"] == pytest.approx(
        {"x": -38000.0, "y": -44399.0, "z": -89301.0}
    )
    assert planned_2_4["machine_positions_um"]["tower_1"] == pytest.approx(
        {"x": 5980.0, "y": 12001.0, "z": 18240.0}
    )
    rebase = planned_2_4["bias_plan"]["gross_rebase"]
    assert rebase["source"] == "candidate_gross_machine_positions_plus_standard_gross_to_close_delta"
    assert rebase["standard_gross_to_close_delta_um"]["camera"] == {"x": 0.0, "y": 601.0, "z": 3699.0}
    assert planned_2_4["bias_plan"]["camera_positions_unchanged"] is False


def test_migration_v5_memory_next_action_uses_recorded_gross_machine_coordinates_for_fine_view():
    memory = initialize_sequence_memory({"schema_version": 1, "targets": ["ball_1"]})
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.1.1",
            "session": _selected_circle_session(100.0, 200.0),
            "machine_positions_um": {
                "camera": {"x": -38000.0, "y": -45000.0, "z": -93000.0},
                "tower_1": {"x": 6000.0, "y": 13000.0, "z": 16000.0},
            },
            "official_baseline": True,
        }
    )

    result = next_sequence_action_from_sequence_memory(memory)

    assert result["ok"] is True
    assert result["action"] == "capture_required"
    capture = result["next_capture"]
    assert capture["capture_id"] == "2.4.1"
    assert capture["planned_from_gross_bias"] is True
    assert capture["machine_positions_um"]["camera"] == pytest.approx(
        {"x": -38000.0, "y": -44399.0, "z": -89301.0}
    )
    assert capture["bias_plan"]["gross_rebase"]["standard_close_position_id"] == "2.4"


def test_migration_v5_solver_fuses_top_and_mirror_side_axes():
    result = solve_common_geometry(_payload())

    assert result["ok"] is True
    assert result["action"] == "geometry_solved"
    coordinates = result["machine_coordinates_um"]
    assert coordinates["machine_reference"] == {
        "machine_x_um": 0.0,
        "machine_y_um": 0.0,
        "machine_z_um": 0.0,
    }
    assert coordinates["ball_1"]["machine_x_um"] == pytest.approx(120.5)
    assert coordinates["ball_1"]["machine_y_um"] == pytest.approx(12.0)
    assert coordinates["ball_1"]["machine_z_um"] == pytest.approx(-30.0)
    assert coordinates["ball_2"]["machine_x_um"] == pytest.approx(819.0)
    assert coordinates["ball_2"]["machine_y_um"] == pytest.approx(-15.0)
    assert coordinates["ball_2"]["machine_z_um"] == pytest.approx(42.0)
    assert result["max_axis_disagreement_um"] == pytest.approx(2.0)


def test_migration_v5_solver_fails_closed_on_missing_relative_measurement():
    payload = _payload()
    del payload["captures"][0]["session"]["relative_measurement"]

    result = solve_common_geometry(payload)

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "relative_measurement is missing" in result["status"]


def test_migration_v5_solver_fails_closed_when_top_side_disagree():
    payload = _payload()
    payload["captures"][1]["session"] = _session(-190.0, -12.0)

    result = solve_common_geometry(payload)

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "disagreement" in result["status"]


def test_migration_v5_solver_can_emit_fixed_z_geometry_when_detector_is_supplied():
    payload = _payload()
    payload["detector_um"] = {
        "machine_x_um": 1278.0,
        "machine_y_um": 0.0,
        "machine_z_um": 0.0,
    }

    result = solve_common_geometry(payload)

    assert result["ok"] is True
    assert result["machine_geometry_um"]["machine_reference"]["machine_x_um"] == pytest.approx(0.0)
    assert result["machine_geometry_um"]["detector"]["machine_x_um"] == pytest.approx(1278.0)
    assert [ball["name"] for ball in result["machine_geometry_um"]["balls"]] == ["ball_1", "ball_2"]
    assert result["machine_geometry_um"]["balls"][0]["diameter_um"] == pytest.approx(500.0)


def test_migration_v5_example_input_matches_solver_contract():
    payload = json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))
    result = solve_common_geometry(copy.deepcopy(payload))

    assert result["ok"] is True
    assert result["machine_coordinates_um"]["ball_1"]["machine_y_um"] == pytest.approx(12.0)
    assert result["machine_coordinates_um"]["ball_2"]["machine_z_um"] == pytest.approx(42.0)


def test_migration_v5_position_bias_example_matches_planner_contract():
    payload = json.loads(BIAS_EXAMPLE_INPUT.read_text(encoding="utf-8"))
    result = plan_biased_close_positions(payload)

    assert result["ok"] is True
    assert [plan["target"] for plan in result["plans"]] == ["ball_1", "ball_2"]
    assert result["plans"][0]["planned_positions"][0]["id"] == "2.2"
    assert result["plans"][1]["planned_positions"][-1]["id"] == "4.6.2"


def test_migration_v5_position_bias_auto_detects_gross_standard_ball_centers():
    payload = json.loads(BIAS_AUTO_EXAMPLE_INPUT.read_text(encoding="utf-8"))

    result = plan_biased_close_positions(payload)

    assert result["ok"] is True
    ball_1_plan, ball_2_plan = result["plans"]
    assert ball_1_plan["baseline_session_auto_detected"] is True
    assert ball_1_plan["candidate_session_auto_detected"] is True
    assert ball_1_plan["baseline_center_px"] == pytest.approx({"x": 1275.5, "y": 1713.5})
    assert ball_1_plan["baseline_radius_px"] == pytest.approx(54.400001525878906)
    assert ball_1_plan["candidate_radius_px"] == pytest.approx(54.400001525878906)
    assert ball_1_plan["pixel_shift"] == pytest.approx({"x": 0.0, "y": 0.0})
    assert ball_1_plan["applied_bias_um"] == pytest.approx({"z": 0.0, "x": 0.0})
    assert ball_2_plan["baseline_center_px"] == pytest.approx({"x": 1997.5, "y": 879.5})
    assert ball_2_plan["baseline_radius_px"] == pytest.approx(169.39999389648438)
    assert ball_2_plan["close_position_ids"] == ["4.2", "4.3", "4.4", "4.5", "4.6.1", "4.6.2"]


def test_migration_v5_position_bias_can_use_auto_baseline_with_shifted_candidate():
    payload = {
        "schema_version": 1,
        "standard_positions_path": str(V4_STANDARD_POSITIONS),
        "auto_detect_gross_sessions": True,
        "gross_observations": [
            {
                "target": "ball_1",
                "gross_capture_id": "2.1.1",
                "um_per_pixel": 0.5,
                "candidate_session": _selected_circle_session(1295.5, 1703.5),
            }
        ],
    }

    result = plan_biased_close_positions(payload)

    assert result["ok"] is True
    plan = result["plans"][0]
    assert plan["baseline_session_auto_detected"] is True
    assert plan["candidate_session_auto_detected"] is False
    assert plan["pixel_shift"] == pytest.approx({"x": 20.0, "y": -10.0})
    assert plan["raw_bias_um"] == pytest.approx({"z": 10.0, "x": -5.0})
    planned_2_5 = next(position for position in plan["planned_positions"] if position["id"] == "2.5")
    assert planned_2_5["machine_positions_um"]["tower_1"]["z"] == pytest.approx(15208.0)


def test_migration_v5_position_bias_can_estimate_gross_scale_from_ball_diameter():
    payload = {
        "schema_version": 1,
        "standard_positions_path": str(V4_STANDARD_POSITIONS),
        "auto_detect_gross_sessions": True,
        "gross_observations": [
            {
                "target": "ball_1",
                "gross_capture_id": "2.1.1",
                "estimate_um_per_pixel_from_ball_diameter": True,
                "ball_diameter_um": 500.0,
                "candidate_session": _selected_circle_session(1285.5, 1703.5),
            }
        ],
    }

    result = plan_biased_close_positions(payload)

    assert result["ok"] is True
    plan = result["plans"][0]
    assert plan["um_per_pixel"] == pytest.approx(500.0 / (2.0 * 54.400001525878906))
    assert plan["raw_bias_um"]["z"] == pytest.approx(45.956, abs=0.01)


def test_migration_v5_sequence_geometry_solves_from_separate_focus_captures():
    result = solve_sequence_geometry(_sequence_payload())

    assert result["ok"] is True
    coordinates = result["machine_coordinates_um"]["ball_1"]
    assert coordinates["machine_x_um"] == pytest.approx(-30.0)
    assert coordinates["machine_y_um"] == pytest.approx(-50.0)
    assert coordinates["machine_z_um"] == pytest.approx(60.0)
    memory = result["feature_memory"]["ball_1"]
    assert memory["reference_rectangle"]["um_per_pixel"] == pytest.approx(5.0)
    assert memory["top_delta_rectangle_center_to_ball"]["px"] == {"x": 12.0, "y": -6.0}
    focus_memory = result["focus_memory"]["ball_1"]
    assert focus_memory["camera_focus_delta_um"] == pytest.approx(399.0)
    assert focus_memory["same_top_view_lateral_camera"] is True
    assert focus_memory["top_camera_lateral_delta_um"] == {"x": 0.0, "z": 0.0}
    assert focus_memory["remembered_focus_planes_um"]["laser_rectangle_reference"]["camera_y_um"] == pytest.approx(
        -45395.0
    )
    assert focus_memory["remembered_focus_planes_um"]["ball_top_focus"]["camera_y_um"] == pytest.approx(-44996.0)
    assert focus_memory["physical_height_model_um"]["ball_center_y_um"] == pytest.approx(-50.0)
    assert focus_memory["physical_height_model_um"]["ball_top_y_um"] == pytest.approx(200.0)
    assert result["physical_constants_um"]["trench_depth_um"] == pytest.approx(300.0)


def test_migration_v5_sequence_geometry_prefers_semantic_roles_over_shape_order():
    payload = {
        "schema_version": 1,
        "standard_positions": _standard_positions_for_sequence(),
        "targets": ["ball_1"],
        "sessions": {
            "2.4.1": {
                "selected_recognition": {
                    "roi_1": [
                        {
                            "shape_kind": "rectangle",
                            "feature_role": "fiducial_candidate",
                            "selection_index": 1,
                            "shape": {
                                "corners": [
                                    {"x": 900.0, "y": 900.0},
                                    {"x": 940.0, "y": 900.0},
                                    {"x": 940.0, "y": 940.0},
                                    {"x": 900.0, "y": 940.0},
                                ]
                            },
                        }
                    ],
                    "roi_2": [
                        {
                            "shape_kind": "rectangle",
                            "feature_role": "laser_reference",
                            "selection_index": 2,
                            "shape": {
                                "corners": [
                                    {"x": 0.0, "y": 0.0},
                                    {"x": 200.0, "y": 0.0},
                                    {"x": 200.0, "y": 100.0},
                                    {"x": 0.0, "y": 100.0},
                                ]
                            },
                        }
                    ],
                }
            },
            "2.5.1": {
                "selected_recognition": {
                    "roi_1": [
                        {
                            "shape_kind": "circle",
                            "feature_role": "fiducial_candidate",
                            "selection_index": 1,
                            "source": "circle",
                            "shape": {"x": 999.0, "y": 999.0, "radius": 40.0},
                        }
                    ],
                    "roi_2": [
                        {
                            "shape_kind": "circle",
                            "feature_role": "ball_1_top_ball",
                            "selection_index": 2,
                            "source": "circle",
                            "shape": {"x": 112.0, "y": 44.0, "radius": 40.0},
                        }
                    ],
                }
            },
        },
    }

    result = solve_sequence_geometry(payload)

    assert result["ok"] is True
    coordinates = result["machine_coordinates_um"]["ball_1"]
    assert coordinates["machine_x_um"] == pytest.approx(-30.0)
    assert coordinates["machine_z_um"] == pytest.approx(60.0)
    memory = result["feature_memory"]["ball_1"]
    assert memory["reference_rectangle"]["center_px"] == {"x": 100.0, "y": 50.0}
    assert memory["top_ball_circle"]["center_px"] == {"x": 112.0, "y": 44.0}


def test_migration_v5_sequence_geometry_fails_closed_when_ball_focus_is_missing():
    payload = _sequence_payload()
    del payload["sessions"]["2.5.1"]

    result = solve_sequence_geometry(payload)

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "2.5.1" in result["status"]


def test_migration_v5_sequence_geometry_fails_closed_when_top_camera_lateral_changed():
    payload = _sequence_payload()
    payload["standard_positions"] = _standard_positions_for_sequence(top_ball_camera_x=-38990)

    result = solve_sequence_geometry(payload)

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "camera x/z changed" in result["status"]


def test_migration_v5_sequence_geometry_example_input_matches_contract():
    payload = json.loads(SEQUENCE_EXAMPLE_INPUT.read_text(encoding="utf-8"))
    result = solve_sequence_geometry(payload)

    assert result["ok"] is True
    assert result["machine_coordinates_um"]["ball_1"]["machine_y_um"] == pytest.approx(-50.0)
    assert result["feature_memory"]["ball_1"]["reference_capture_id"] == "2.4.1"


def test_migration_v5_sequence_geometry_auto_detects_missing_ball_1_standard_sessions(tmp_path):
    payload = {
        "schema_version": 1,
        "targets": ["ball_1"],
        "baseline_dir": str(tmp_path),
        "standard_positions_path": str(V4_STANDARD_POSITIONS),
        "auto_detect_missing_sessions": True,
    }

    result = solve_sequence_geometry(payload)

    assert result["ok"] is True
    assert result["machine_coordinates_um"]["ball_1"]["machine_x_um"] == pytest.approx(-583.343, abs=0.01)
    assert result["machine_coordinates_um"]["ball_1"]["machine_y_um"] == pytest.approx(-50.0)
    assert result["machine_coordinates_um"]["ball_1"]["machine_z_um"] == pytest.approx(156.037, abs=0.01)
    assert result["feature_memory"]["ball_1"]["reference_rectangle"]["um_per_pixel"] == pytest.approx(
        0.77362,
        abs=0.0001,
    )
    assert result["feature_memory"]["ball_1"]["top_ball_circle"]["source"] == "auto_ball_circle"
    assert result["feature_memory"]["ball_1"]["top_ball_circle"]["radius_px"] == pytest.approx(316.32, abs=0.1)
    side_height = result["feature_memory"]["ball_1"]["side_height_candidate"]
    assert side_height["side_reference_y_px"] == pytest.approx(532.0)
    assert side_height["measured_machine_y_um_candidate"] == pytest.approx(-164.500, abs=0.01)
    assert side_height["used_for_coordinate"] is False
    assert side_height["review_required"] is True


def test_migration_v5_sequence_geometry_can_opt_into_side_reference_y(tmp_path):
    payload = {
        "schema_version": 1,
        "targets": ["ball_1"],
        "baseline_dir": str(tmp_path),
        "standard_positions_path": str(V4_STANDARD_POSITIONS),
        "auto_detect_missing_sessions": True,
        "machine_y_source": "side_reference",
    }

    result = solve_sequence_geometry(payload)

    assert result["ok"] is True
    assert result["machine_y_source"] == "side_reference"
    assert result["machine_coordinates_um"]["ball_1"]["machine_y_um"] == pytest.approx(-164.500, abs=0.01)
    assert result["feature_memory"]["ball_1"]["side_height_candidate"]["used_for_coordinate"] is True


def test_migration_v5_macro_alignment_simulator_runs_full_auto_standard_sequence():
    payload = json.loads(MACRO_AUTO_EXAMPLE_INPUT.read_text(encoding="utf-8"))

    result = simulate_macro_alignment(payload)

    assert result["ok"] is True
    assert result["action"] == "macro_alignment_simulated"
    assert result["machine_coordinates_um"]["ball_1"]["machine_x_um"] == pytest.approx(-583.305, abs=0.01)
    assert result["machine_coordinates_um"]["ball_1"]["machine_y_um"] == pytest.approx(-50.0)
    assert result["machine_coordinates_um"]["ball_2"]["machine_z_um"] == pytest.approx(360.937, abs=0.01)
    assert result["close_position_summary"]["ball_1"]["applied_bias_um"] == pytest.approx({"x": 0.0, "z": 0.0})
    assert result["gross_bias"]["plans"][0]["um_per_pixel"] == pytest.approx(500.0 / (2.0 * 54.400001525878906))
    assert result["feature_memory"]["ball_1"]["top_ball_circle"]["source"] == "auto_ball_circle"


def test_migration_v5_standard_capture_evidence_summarizes_image_and_machine_relationships():
    result = build_standard_capture_evidence(
        {
            "schema_version": 1,
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
        }
    )

    assert result["ok"] is True
    assert result["action"] == "standard_capture_evidence_built"
    assert result["physical_constants_um"]["laser_rectangle_short_edge_um"] == pytest.approx(500.0)
    assert result["physical_constants_um"]["ball_diameter_um"] == pytest.approx(500.0)
    assert result["physical_constants_um"]["trench_depth_um"] == pytest.approx(300.0)
    assert result["physical_constants_um"]["assumed_ball_center_y_um"] == pytest.approx(-50.0)

    ball_1 = result["target_evidence"]["ball_1"]
    assert ball_1["gross_capture_id"] == "2.1.1"
    assert ball_1["gross_ball_center_px"] == pytest.approx({"x": 1275.5, "y": 1713.5})
    assert ball_1["gross_ball_scale"]["radius_px"] == pytest.approx(54.400001525878906)
    assert ball_1["gross_ball_scale"]["um_per_pixel_from_radius"] == pytest.approx(
        500.0 / (2.0 * 54.400001525878906)
    )
    assert ball_1["reference_rectangle"]["short_edge_length_um"] == pytest.approx(500.0)
    assert ball_1["focus_memory"]["top_camera_lateral_delta_um"] == {"x": 0.0, "z": 0.0}
    assert ball_1["focus_memory"]["camera_focus_delta_um"] == pytest.approx(399.0)
    assert ball_1["gross_to_close_machine_deltas_um"]["2.4"]["tower_delta_um"] == {
        "x": 0.0,
        "y": -999.0,
        "z": 2200.0,
    }
    assert ball_1["gross_to_close_machine_deltas_um"]["2.4"]["camera_delta_um"] == {
        "x": 0.0,
        "y": 601.0,
        "z": 3699.0,
    }
    ball_1_motion = ball_1["same_camera_motion_evidence"]
    assert ball_1_motion["missing_feature_capture_ids"] == []
    assert [sample["capture_id"] for sample in ball_1_motion["motion_samples"]] == ["2.2.1", "2.3.1"]
    assert ball_1_motion["motion_samples"][0]["pixel_shift_from_gross_px"] == {"x": 240.5, "y": -290.5}
    assert ball_1_motion["motion_samples"][0]["tower_delta_from_gross_um"] == {
        "x": 1000.0,
        "y": -1199.0,
        "z": 0.0,
    }
    assert ball_1_motion["motion_samples"][1]["pixel_shift_from_gross_px"] == {"x": -0.5, "y": -1633.5}
    assert ball_1_motion["tower_to_pixel_fit"]["rank"] == 2
    assert ball_1_motion["tower_to_pixel_fit"]["status"] == "underconstrained_full_3_axis_calibration"
    assert ball_1_motion["tower_to_pixel_fit"]["use_for_motion"] is False
    assert ball_1["machine_coordinates_um"]["machine_y_um"] == pytest.approx(-50.0)

    ball_2 = result["target_evidence"]["ball_2"]
    assert ball_2["gross_capture_id"] == "4.1.1"
    assert ball_2["focus_memory"]["camera_focus_delta_um"] == pytest.approx(502.0)
    assert ball_2["gross_to_close_machine_deltas_um"]["4.4"]["tower_delta_um"] == {
        "x": -2000.0,
        "y": -1100.0,
        "z": 1140.0,
    }
    assert ball_2["same_camera_motion_evidence"]["missing_feature_capture_ids"] == ["4.2.1", "4.3.1"]
    assert ball_2["same_camera_motion_evidence"]["tower_to_pixel_fit"]["status"] == "missing_samples"
    assert ball_2["machine_coordinates_um"]["machine_z_um"] == pytest.approx(360.937, abs=0.01)


def test_migration_v5_macro_alignment_simulator_fails_closed_when_gross_stage_fails():
    result = simulate_macro_alignment(
        {
            "schema_version": 1,
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
            "gross_observations": [
                {
                    "target": "ball_1",
                    "gross_capture_id": "2.1.1",
                }
            ],
            "auto_detect_gross_sessions": False,
        }
    )

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "gross bias planning failed" in result["status"]


def test_migration_v5_sequence_memory_initializes_required_capture_records():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
        }
    )

    assert memory["ok"] is True
    assert memory["action"] == "v5_sequence_capture_memory"
    assert list(memory["capture_records"]) == [
        "2.1.1",
        "2.4.1",
        "2.5.1",
        "2.6.1",
        "4.1.1",
        "4.4.1",
        "4.5.1",
        "4.6.2",
    ]
    assert memory["capture_records"]["2.4.1"]["result_use"] == "reference_focus_registration"
    assert memory["physical_constants_um"]["laser_rectangle_short_edge_um"] == pytest.approx(500.0)
    assert memory["auto_detect_missing_sessions"] is False


def test_migration_v5_sequence_memory_next_action_starts_with_gross_capture():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "targets": ["ball_1"],
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
        }
    )

    result = next_sequence_action_from_sequence_memory(memory)

    assert result["ok"] is True
    assert result["action"] == "capture_required"
    assert result["next_capture"]["capture_id"] == "2.1.1"
    assert result["next_capture"]["position_id"] == "2.1"
    assert "machine_positions_um" in result["next_capture"]
    assert result["machine_coordinate_output"] == "machine_coordinates_um"


def test_migration_v5_sequence_memory_next_action_requires_official_gross_baseline():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "targets": ["ball_1"],
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
        }
    )
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.1.1",
            "session": _selected_circle_session(120.0, 190.0),
        }
    )

    result = run_sequence_memory_workflow(
        {
            "schema_version": 1,
            "command": "next_action",
            "memory": memory,
        }
    )

    assert result["ok"] is False
    assert result["action"] == "official_baseline_required"
    assert result["next_capture"]["capture_id"] == "2.1.1"
    assert result["next_capture"]["official_baseline"] is True
    assert result["machine_coordinate_output"] == "machine_coordinates_um"


def test_migration_v5_sequence_memory_next_action_returns_biased_focus_capture_after_gross_baseline():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "targets": ["ball_1"],
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
        }
    )
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.1.1",
            "session": _selected_circle_session(100.0, 200.0),
            "official_baseline": True,
        }
    )

    result = next_sequence_action_from_sequence_memory(memory)

    assert result["ok"] is True
    assert result["action"] == "capture_required"
    assert result["next_capture"]["capture_id"] == "2.4.1"
    assert result["next_capture"]["position_id"] == "2.4"
    assert result["next_capture"]["planned_from_gross_bias"] is True
    assert result["next_capture"]["bias_plan"]["target"] == "ball_1"
    assert result["next_capture"]["bias_plan"]["use_for_motion"] is False
    assert result["next_capture"]["bias_plan"]["bias_mapping_evidence"]["operator_review_required"] is True
    assert result["gross_bias"]["applied_bias_um"] == pytest.approx({"x": 0.0, "z": 0.0})
    assert result["gross_bias"]["bias_mapping_evidence"]["use_for_motion"] is False


def test_migration_v5_sequence_memory_remembers_focus_plane_for_next_same_height_capture():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
            "auto_detect_gross_sessions": True,
        }
    )
    for capture_id, session, camera_y in (
        ("2.4.1", _selected_rectangle_session(), -45395.0),
        ("2.5.1", _selected_circle_session(112.0, 44.0), -44996.0),
        ("2.6.1", _selected_circle_session(88.0, 132.0), -47996.0),
    ):
        memory = record_sequence_capture(
            {
                "schema_version": 1,
                "memory": memory,
                "capture_id": capture_id,
                "session": session,
                "machine_positions_um": {
                    "camera": {"y": camera_y},
                },
            }
        )

    result = next_sequence_action_from_sequence_memory(memory)

    assert result["ok"] is True
    assert result["next_capture"]["capture_id"] == "4.4.1"
    assert result["next_capture"]["focus_plane_key"] == "top_laser_reference"
    remembered = result["next_capture"]["remembered_focus_plane"]
    assert remembered["source_capture_id"] == "2.4.1"
    assert remembered["suggested_camera_y_um"] == pytest.approx(-45395.0)
    assert remembered["planned_camera_y_um"] == pytest.approx(-45345.0)
    assert remembered["delta_from_planned_camera_y_um"] == pytest.approx(-50.0)
    assert remembered["applied_to_machine_positions"] is False
    assert result["next_capture"]["machine_positions_um"]["camera"]["y"] == pytest.approx(-45345.0)
    assert result["sequence_memory_summary"]["focus_plane_keys"] == [
        "side_ball_focus",
        "top_ball_focus",
        "top_laser_reference",
    ]


def test_migration_v5_sequence_memory_can_apply_remembered_focus_plane_when_explicitly_enabled():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
            "auto_detect_gross_sessions": True,
            "apply_remembered_focus_planes": True,
        }
    )
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.4.1",
            "session": _selected_rectangle_session(),
            "machine_positions_um": {
                "camera": {"y": -45395.0},
            },
        }
    )
    for capture_id, session in (
        ("2.5.1", _selected_circle_session(112.0, 44.0)),
        ("2.6.1", _selected_circle_session(88.0, 132.0)),
    ):
        memory = record_sequence_capture(
            {
                "schema_version": 1,
                "memory": memory,
                "capture_id": capture_id,
                "session": session,
            }
        )

    result = next_sequence_action_from_sequence_memory(memory)

    assert result["ok"] is True
    assert result["next_capture"]["capture_id"] == "4.4.1"
    assert result["next_capture"]["machine_positions_um"]["camera"]["y"] == pytest.approx(-45395.0)
    assert result["next_capture"]["remembered_focus_plane"]["applied_to_machine_positions"] is True
    assert result["next_capture"]["remembered_focus_plane"]["operator_review_required"] is True


def test_migration_v5_sequence_memory_cli_record_accepts_machine_positions(tmp_path):
    memory_path = tmp_path / "memory.json"
    session_path = tmp_path / "2.4.1.session.json"
    session_path.write_text(json.dumps(_selected_rectangle_session()), encoding="utf-8")

    sequence_memory_workflow_main(
        [
            "init",
            "--standard-positions",
            str(V4_STANDARD_POSITIONS),
            "--allow-standard-auto",
            "--output",
            str(memory_path),
        ]
    )
    result = sequence_memory_workflow_main(
        [
            "record",
            str(memory_path),
            "--capture-id",
            "2.4.1",
            "--session",
            str(session_path),
            "--camera-x",
            "-38997",
            "--camera-y",
            "-45395",
            "--camera-z",
            "-93995",
            "--tower-1-x",
            "5331",
            "--tower-1-y",
            "12291",
            "--tower-1-z",
            "15198",
        ]
    )

    assert result["ok"] is True
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    record = memory["capture_records"]["2.4.1"]
    assert record["machine_positions_um"]["camera"] == {"x": -38997.0, "y": -45395.0, "z": -93995.0}
    assert record["machine_positions_um"]["tower_1"] == {"x": 5331.0, "y": 12291.0, "z": 15198.0}
    assert memory["focus_plane_memory"]["top_laser_reference"]["latest_camera_y_um"] == pytest.approx(-45395.0)


def test_migration_v5_review_record_opens_ui_and_records_next_capture(monkeypatch, tmp_path):
    memory_path = tmp_path / "memory.json"
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "targets": ["ball_1"],
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
        }
    )
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    calls = []

    def fake_review_ui(image_path, *, roi_output_path=None, result_output_path=None):
        calls.append(
            {
                "image_path": image_path,
                "roi_output_path": roi_output_path,
                "result_output_path": result_output_path,
            }
        )
        return _selected_circle_session(100.0, 200.0)

    monkeypatch.setattr(sequence_memory_workflow_module, "open_vision_review_ui", fake_review_ui)
    result = review_and_record_next_capture(
        {
            "schema_version": 1,
            "memory_path": str(memory_path),
            "image_path": "D:/TestMasterData/data/Python_Automation/python_vision_input.bmp",
            "roi_output_path": "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_rois.json",
            "review_session_output_path": (
                "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_review_session.json"
            ),
            "result_output_path": str(tmp_path / "result.json"),
            "machine_positions_um": _live_machine_positions(),
        }
    )

    assert result["ok"] is True
    assert result["action"] == "reviewed_capture_recorded"
    assert result["capture_id"] == "2.1.1"
    assert result["official_baseline"] is False
    assert calls == [
        {
            "image_path": "D:/TestMasterData/data/Python_Automation/python_vision_input.bmp",
            "roi_output_path": "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_rois.json",
            "result_output_path": "D:/TestMasterData/Process/Python_Automation/python_env/log/v5_review_session.json",
        }
    ]
    updated = json.loads(memory_path.read_text(encoding="utf-8"))
    record = updated["capture_records"]["2.1.1"]
    assert record["review_status"] == "reviewed"
    assert record["machine_positions_um"] == _live_machine_positions()
    assert record["session"]["selected_recognition"]
    assert result["next_action_after_record"]["action"] == "official_baseline_required"


def test_migration_v5_review_record_fails_closed_without_selected_shape(monkeypatch, tmp_path):
    memory_path = tmp_path / "memory.json"
    memory = initialize_sequence_memory({"schema_version": 1, "targets": ["ball_1"]})
    memory_path.write_text(json.dumps(memory), encoding="utf-8")

    monkeypatch.setattr(
        sequence_memory_workflow_module,
        "open_vision_review_ui",
        lambda image_path, **kwargs: {"selected_recognition": {}},
    )
    result = review_and_record_next_capture(
        {
            "schema_version": 1,
            "memory_path": str(memory_path),
            "image_path": "D:/TestMasterData/data/Python_Automation/python_vision_input.bmp",
            "machine_positions_um": _live_machine_positions(),
        }
    )

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "saved no selected shapes" in result["status"]
    updated = json.loads(memory_path.read_text(encoding="utf-8"))
    assert not updated["capture_records"]["2.1.1"].get("session")


def test_migration_v5_review_record_marks_official_gross_baseline(monkeypatch, tmp_path):
    memory = initialize_sequence_memory({"schema_version": 1, "targets": ["ball_1"]})
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.1.1",
            "session": _selected_circle_session(110.0, 210.0),
            "machine_positions_um": _live_machine_positions(),
        }
    )
    memory_path = tmp_path / "memory.json"
    memory_path.write_text(json.dumps(memory), encoding="utf-8")

    monkeypatch.setattr(
        sequence_memory_workflow_module,
        "open_vision_review_ui",
        lambda image_path, **kwargs: _selected_circle_session(100.0, 200.0),
    )
    result = review_and_record_next_capture(
        {
            "schema_version": 1,
            "memory_path": str(memory_path),
            "image_path": "D:/TestMasterData/data/Python_Automation/python_vision_input.bmp",
            "camera_x_um": -38997.0,
            "camera_y_um": -45395.0,
            "camera_z_um": -93995.0,
            "tower_1_x_um": 5331.0,
            "tower_1_y_um": 12291.0,
            "tower_1_z_um": 15198.0,
        }
    )

    assert result["ok"] is True
    assert result["capture_id"] == "2.1.1"
    assert result["official_baseline"] is True
    updated = json.loads(memory_path.read_text(encoding="utf-8"))
    assert updated["capture_records"]["2.1.1"]["review_status"] == "recorded"
    assert updated["capture_records"]["2.1.1"]["session"]["selected_recognition"]["roi_1"][0]["shape"]["x"] == 110.0
    assert updated["official_baselines"]["2.1.1"]["session"]["selected_recognition"]
    assert updated["official_baselines"]["2.1.1"]["session"]["selected_recognition"]["roi_1"][0]["shape"]["x"] == 100.0
    assert result["next_action_after_record"]["action"] == "capture_required"
    assert result["next_action_after_record"]["next_capture"]["capture_id"] == "2.4.1"


def test_migration_v5_next_motion_or_capture_returns_flat_yase_move():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "targets": ["ball_1"],
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
        }
    )

    result = next_motion_or_capture_step(
        memory,
        {
            "schema_version": 1,
            "machine_positions_um": {
                "camera": {"x": 0.0, "y": -45996.0, "z": -97694.0},
                "tower_1": {"x": 5331.0, "y": 13290.0, "z": 12998.0},
            },
        },
    )

    assert result["ok"] is True
    assert result["action"] == "move_to_next_capture"
    assert result["stage1"] == "Camera_X"
    assert result["target1_um"] == pytest.approx(-38997.0)
    assert result["distance1_um"] == result["target1_um"]
    assert result["delta1_um"] == pytest.approx(-38997.0)
    assert result["move_mode1"] == "Absolute"
    assert "Camera_X" in result["confirm_text1"]
    assert result["next_sequence_after_move"] == "rerun next_motion_or_capture"


def test_migration_v5_next_motion_or_capture_returns_ui_step_when_at_target():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "targets": ["ball_1"],
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
        }
    )

    result = next_motion_or_capture_step(
        memory,
        {
            "schema_version": 1,
            "machine_positions_um": {
                "camera": {"x": -38997.0, "y": -45996.0, "z": -97694.0},
                "tower_1": {"x": 5331.0, "y": 13290.0, "z": 12998.0},
            },
        },
    )

    assert result["ok"] is True
    assert result["action"] == "capture_review_record_required"
    assert result["capture_id"] == "2.1.1"
    assert result["next_sequence"] == "SUB_V5CaptureReviewRecord_ReadOnly.xseq"
    assert result["stage1"] == ""
    assert result["target1_um"] == 0.0


def test_migration_v5_sequence_memory_next_action_solve_ready_after_manual_records():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "targets": ["ball_1"],
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
        }
    )
    for capture_id, session in (
        ("2.1.1", _selected_circle_session(100.0, 200.0)),
        ("2.4.1", _selected_rectangle_session()),
        ("2.5.1", _selected_circle_session(112.0, 44.0)),
        ("2.6.1", _selected_circle_session(88.0, 132.0)),
    ):
        memory = record_sequence_capture(
            {
                "schema_version": 1,
                "memory": memory,
                "capture_id": capture_id,
                "session": session,
                "official_baseline": capture_id == "2.1.1",
            }
        )

    result = next_sequence_action_from_sequence_memory(memory)

    assert result["ok"] is True
    assert result["action"] == "solve_ready"
    assert result["next_command"] == "solve_macro"
    assert result["machine_coordinate_output"] == "machine_coordinates_um"


def test_migration_v5_sequence_memory_records_focused_captures_and_solves():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "targets": ["ball_1"],
            "standard_positions": _standard_positions_for_sequence(),
        }
    )
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.4.1",
            "session": _selected_rectangle_session(),
            "machine_positions_um": {
                "camera": {"x": -38997, "y": -45395, "z": -93995},
            },
        }
    )
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.5.1",
            "session": _selected_circle_session(112.0, 44.0),
            "machine_positions_um": {
                "camera": {"x": -38997, "y": -44996, "z": -93995},
            },
        }
    )

    result = solve_sequence_geometry_from_sequence_memory(memory)

    assert result["ok"] is True
    assert result["machine_coordinates_um"]["ball_1"]["machine_x_um"] == pytest.approx(-30.0)
    assert result["machine_coordinates_um"]["ball_1"]["machine_y_um"] == pytest.approx(-50.0)
    assert result["machine_coordinates_um"]["ball_1"]["machine_z_um"] == pytest.approx(60.0)
    assert result["sequence_memory_summary"]["recorded_capture_ids"] == ["2.4.1", "2.5.1"]
    assert result["focus_memory"]["ball_1"]["camera_focus_delta_um"] == pytest.approx(399.0)


def test_migration_v5_sequence_memory_fails_closed_when_recorded_focus_is_missing():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "targets": ["ball_1"],
            "standard_positions": _standard_positions_for_sequence(),
            "auto_detect_missing_sessions": False,
        }
    )
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.4.1",
            "session": _selected_rectangle_session(),
        }
    )

    result = solve_sequence_geometry_from_sequence_memory(memory)

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "2.5.1" in result["status"]


def test_migration_v5_sequence_memory_macro_payload_preserves_live_standard_positions():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
            "auto_detect_gross_sessions": True,
            "auto_detect_missing_sessions": True,
            "auto_detect_side_references": True,
        }
    )
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.5.1",
            "machine_positions_um": {
                "camera": {"x": -38997, "y": -44996, "z": -93995},
                "tower_1": {"x": 1.0, "y": 2.0, "z": 3.0},
            },
        }
    )

    payload = build_macro_payload_from_sequence_memory(memory)

    assert payload["standard_positions"]["positions"]
    position_2_5 = next(position for position in payload["standard_positions"]["positions"] if position["id"] == "2.5")
    assert position_2_5["machine_positions_um"]["tower_1"] == {"x": 1.0, "y": 2.0, "z": 3.0}


def test_migration_v5_sequence_memory_macro_solver_uses_live_camera_position_override():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
            "auto_detect_gross_sessions": True,
            "auto_detect_missing_sessions": True,
            "auto_detect_side_references": True,
        }
    )
    memory = record_sequence_capture(
        {
            "schema_version": 1,
            "memory": memory,
            "capture_id": "2.5.1",
            "machine_positions_um": {
                "camera": {"x": -38990},
            },
        }
    )

    result = solve_macro_alignment_from_sequence_memory(memory)

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert "camera x/z changed" in result["status"]


def test_migration_v5_sequence_memory_solves_full_auto_standard_macro():
    memory = initialize_sequence_memory(
        {
            "schema_version": 1,
            "standard_positions_path": str(V4_STANDARD_POSITIONS),
            "auto_detect_gross_sessions": True,
            "auto_detect_missing_sessions": True,
            "auto_detect_side_references": True,
        }
    )

    result = solve_macro_alignment_from_sequence_memory(memory)

    assert result["ok"] is True
    assert result["action"] == "macro_alignment_simulated"
    assert result["machine_coordinates_um"]["ball_1"]["machine_x_um"] == pytest.approx(-583.305, abs=0.01)
    assert result["machine_coordinates_um"]["ball_2"]["machine_z_um"] == pytest.approx(360.937, abs=0.01)
    assert result["sequence_memory_summary"]["capture_count"] == 8
