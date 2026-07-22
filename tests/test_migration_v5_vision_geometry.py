import copy
import json
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from migrations.migration_v5.python_vision_geometry.position_bias_planner import plan_biased_close_positions
from migrations.migration_v5.python_vision_geometry.macro_alignment_simulator import simulate_macro_alignment
from migrations.migration_v5.python_vision_geometry.sequence_geometry_memory import solve_sequence_geometry
from migrations.migration_v5.python_vision_geometry.sequence_memory_workflow import (
    build_macro_payload_from_sequence_memory,
    initialize_sequence_memory,
    record_sequence_capture,
    solve_macro_alignment_from_sequence_memory,
    solve_sequence_geometry_from_sequence_memory,
)
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


def _v5_solver_json_from_xseq():
    setstring = next(
        statement
        for statement in _xseq_statements(V5_YASE_SOLVE_SEQUENCE)
        if statement.attrib.get("Label") == "L_BuildPythonInput"
    )
    return json.loads(_params_by_name(setstring)["String 1"]["StringValue"])


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
    assert any("No motion" in item or "motion" in item for item in plan["fail_closed_requirements"])


def test_migration_v5_yase_sequences_parse_and_are_read_only():
    assert V5_XSEQ_FILES
    for path in V5_XSEQ_FILES:
        root = _xseq_root(path)
        assert root.tag == "Sequence"
        names = _statement_names(path)
        assert "MoveStage" not in names
        assert "SetAnalogOut" not in names
        assert "Grab" not in names
        assert "IMAQWriteFile" not in names


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


def test_migration_v5_yase_result_paramout_is_not_used_for_motion():
    result_json_uses = []
    for statement in _xseq_statements(V5_YASE_SOLVE_SEQUENCE):
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
        ("SetStringVar", "VarStringIn", "String", "Input"),
        ("WriteToFile", "Data", "String", "Input"),
    ]


def test_migration_v5_bias_planner_adjusts_close_positions_from_gross_ball_shift():
    result = plan_biased_close_positions(_bias_payload())

    assert result["ok"] is True
    assert result["action"] == "biased_close_positions_planned"
    plan = result["plans"][0]
    assert plan["target"] == "ball_1"
    assert plan["pixel_shift"] == {"x": 20.0, "y": -10.0}
    assert plan["raw_bias_um"] == {"z": 40.0, "x": -20.0}
    assert plan["applied_bias_um"] == {"z": 40.0, "x": -20.0}
    assert plan["close_position_ids"] == ["2.2", "2.3", "2.4", "2.5", "2.6"]

    planned_2_3 = next(position for position in plan["planned_positions"] if position["id"] == "2.3")
    tower_1 = planned_2_3["machine_positions_um"]["tower_1"]
    camera = planned_2_3["machine_positions_um"]["camera"]
    assert tower_1["x"] == pytest.approx(5311.0)
    assert tower_1["y"] == pytest.approx(12291.0)
    assert tower_1["z"] == pytest.approx(15238.0)
    assert camera == {"x": -38997, "y": -45996, "z": -97694}
    assert planned_2_3["bias_plan"]["camera_positions_unchanged"] is True


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
    assert ball_1_plan["pixel_shift"] == pytest.approx({"x": 0.0, "y": 0.0})
    assert ball_1_plan["applied_bias_um"] == pytest.approx({"z": 0.0, "x": 0.0})
    assert ball_2_plan["baseline_center_px"] == pytest.approx({"x": 1997.5, "y": 879.5})
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
    assert result["focus_memory"]["ball_1"]["camera_focus_delta_um"] == pytest.approx(399.0)
    assert result["physical_constants_um"]["trench_depth_um"] == pytest.approx(300.0)


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
