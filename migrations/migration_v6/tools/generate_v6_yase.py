"""Generate migration v6 YASE sequence files.

The generated files intentionally keep the same plain XML style as the
checked-in v4/v5 sequences so they can be statically reviewed and copied into
the Python_Automation process folders.
"""

from __future__ import annotations

import json
import re
from html import escape
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
MIGRATION = ROOT / "migrations" / "migration_v6"
STANDARD_POSITIONS_JSON = MIGRATION / "standard_positions.json"
STANDARD_POSITION_DIR = MIGRATION / "SUB_v6_standard_positions"
WORKFLOW_DIR = MIGRATION / "SUB_v6_vision_workflow"

PYTHON_ENV = "D:/TestMasterData/Process/Python_Automation/python_env"
LOG_DIR = f"{PYTHON_ENV}/log"
DATA_DIR = "D:/TestMasterData/data/Python_Automation"
MEMORY_PATH = f"{LOG_DIR}/v6_vision_memory.json"
STANDARD_POSITIONS_MACHINE_PATH = f"{PYTHON_ENV}/standard_positions_v4/standard_positions.json"
STANDARD_BASELINE_MACHINE_DIR = f"{PYTHON_ENV}/standard_positions_v4/vision_baselines"
IMAGE_PATH = f"{DATA_DIR}/python_vision_input.bmp"
INTERPRETER = "Python_310_PYTHON_AUTOMATION_INTERPRETER"
MODULE = "python_vision_geometry.v6_offset_workflow"

STAGE_ORDER = [
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
QUERY_STAGES = [
    ("Camera_X", "d_CameraXUm", "camera_x_um"),
    ("Camera_Y", "d_CameraYUm", "camera_y_um"),
    ("Camera_Z", "d_CameraZUm", "camera_z_um"),
    ("Align_X1", "d_Tower1XUm", "tower_1_x_um"),
    ("Align_Y1", "d_Tower1YUm", "tower_1_y_um"),
    ("Align_Z1", "d_Tower1ZUm", "tower_1_z_um"),
    ("Align_X2", "d_Tower2XUm", "tower_2_x_um"),
    ("Align_Y2", "d_Tower2YUm", "tower_2_y_um"),
    ("Align_Z2", "d_Tower2ZUm", "tower_2_z_um"),
    ("Zoom", "d_ZoomUm", "zoom_um"),
]
CAPTURE_IDS = ["2.1.1", "2.4.1", "2.5.1", "2.6.1", "4.1.1", "4.4.1", "4.5.1", "4.6.2"]
OFFSET_CAPTURE_IDS = ["2.1.1", "2.5.1", "2.6.1", "4.1.1", "4.5.1", "4.6.2"]
TRANSITIONS = ["2.1_to_2.4", "2.4_to_2.5", "2.5_to_2.6", "4.1_to_4.4", "4.4_to_4.5", "4.5_to_4.6.2"]


def main() -> None:
    STANDARD_POSITION_DIR.mkdir(parents=True, exist_ok=True)
    WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(STANDARD_POSITIONS_JSON.read_text(encoding="utf-8"))
    positions = payload["positions"]
    clearance_y_by_tower = tower_clearance_y_by_tower(positions)
    for position in positions:
        write_text(standard_position_path(position), generate_standard_position_sequence(position, clearance_y_by_tower))
    write_text(WORKFLOW_DIR / "SUB_V6SequenceMemoryInit_ReadOnly.xseq", generate_init_sequence())
    write_text(WORKFLOW_DIR / "SUB_V6ApplyApproachMove_Guarded.xseq", generate_apply_move_sequence("approach"))
    write_text(WORKFLOW_DIR / "SUB_V6ApplyOffsetCorrectionMove_Guarded.xseq", generate_apply_move_sequence("offset"))
    for capture_id in CAPTURE_IDS:
        write_text(capture_sequence_path(capture_id), generate_capture_sequence(capture_id))
    for capture_id in OFFSET_CAPTURE_IDS:
        write_text(offset_sequence_path(capture_id), generate_offset_sequence(capture_id))
    for transition_id in TRANSITIONS:
        write_text(transition_sequence_path(transition_id), generate_transition_sequence(transition_id, positions))
    write_text(WORKFLOW_DIR / "SUB_V6MainWorkflow_Guarded.xseq", generate_main_workflow(positions))


def generate_standard_position_sequence(position: dict[str, Any], clearance_y_by_tower: dict[str, float]) -> str:
    sequence_name = standard_position_path(position).stem
    moves = position_moves(position, clearance_y_by_tower)
    analogs = position_analogs(position)
    confirm = (
        f"V6 standard position {position['id']} ({position['label']}). "
        f"Move to hardcoded machine targets and set exposure/lights. "
        f"Towers raise to clearance before X/Z motion, then lower to final Y. "
        f"Uses medium speeds for approach. Confirm chip/tower/camera clearance."
    )
    statements = common_sequence_start(sequence_name)
    if not moves and not analogs:
        statements.extend(error_and_end_statements("52.0", "No known v6 targets or settings for this position."))
        return sequence_document(sequence_name, statements, "V6 standard position with no hardware action.")
    statements.extend(stage_check_statements("L_Error_Fiducial"))
    statements.extend(get_standard_position_velocities())
    statements.append(dialog_statement("L_UserConfirm", confirm, ok="Abort", skip="Move"))
    statements.append(goto_statement("L_Error_UserAbort"))
    for stage, target in moves:
        statements.extend(move_stage_and_wait(stage, target, velocity_var_for_standard_stage(stage)))
    for line, value in analogs:
        statements.append(set_analog_statement(line, value))
    statements.extend(success_and_error_end(sequence_name, success_text=f"{sequence_name} complete."))
    return sequence_document(sequence_name, statements, f"V6 hardcoded standard position {position['id']}.")


def generate_apply_move_sequence(kind: str) -> str:
    if kind == "approach":
        sequence_name = "SUB_V6ApplyApproachMove_Guarded"
        allowed = STAGE_ORDER
        velocity_statements = get_apply_approach_velocities()
        select_velocity = select_approach_velocity_statements()
        description = "Apply one operator-confirmed medium-speed v6 transition move."
    else:
        sequence_name = "SUB_V6ApplyOffsetCorrectionMove_Guarded"
        allowed = ["Align_X1", "Align_Y1", "Align_Z1", "Align_X2", "Align_Y2", "Align_Z2"]
        velocity_statements = get_apply_offset_velocities()
        select_velocity = select_offset_velocity_statements()
        description = "Apply one operator-confirmed slow v6 offset-correction move."
    statements = common_sequence_start(sequence_name)
    statements.extend(declare_move_params(default_max="200000.0" if kind == "approach" else "350.0"))
    statements.extend(stage_check_statements("L_Error_Fiducial"))
    statements.extend(validate_stage_statements(allowed))
    statements.extend(validate_delta_statements())
    statements.extend(velocity_statements)
    statements.extend(select_velocity)
    statements.extend(move_popup_statements())
    statements.append(dialog_statement("L_UserConfirmMove", variable="s_MovePopupText", ok="Abort", skip="Move"))
    statements.append(goto_statement("L_Error_UserAbort"))
    statements.append(
        statement(
            "MoveStage",
            "Stage",
            label="L_MoveAbsolute",
            params=[
                param("Stage", "String", "Input", variable="s_MoveStage"),
                param("Velocity [um/s]", "DBL", "Input", variable="d_MoveVelocity"),
                param("Distance [um]", "DBL", "Input", variable="d_TargetUm"),
                param("Sync", "Enum Word", "Input", string="No sync"),
                param("rel/abs", "Enum Word", "Input", string="Absolute"),
            ],
        )
    )
    statements.append(
        statement(
            "SEQ::SUB_SYS_AxisWaitFinishList",
            "system..",
            params=[param("AxisList [CSV Format]", "String", "Input", variable="s_MoveStage")],
        )
    )
    statements.append(
        statement(
            "SEQ::SUB_SysCheckAxisMove",
            "system..",
            params=[
                param("Axis1", "String", "Input", variable="s_MoveStage"),
                param("Axis2", "String", "Input", string=""),
                param("Axis3", "String", "Input", string=""),
                param("Axis4", "String", "Input", string=""),
                param("Axis5", "String", "Input", string=""),
                param("Axis6", "String", "Input", string=""),
                param("Error", "DBL", "Output", variable="d_ErrorType"),
                param("S_ErrorMessage", "String", "Output", variable="s_ErrorMessage"),
            ],
        )
    )
    statements.append(ifnum_statement("d_ErrorType", "<>", "0.0"))
    statements.append(goto_statement("L_End"))
    statements.extend(success_and_error_end(sequence_name, success_text=f"{sequence_name} complete."))
    return sequence_document(sequence_name, statements, description)


def generate_init_sequence() -> str:
    sequence_name = "SUB_V6SequenceMemoryInit_ReadOnly"
    payload = {
        "schema_version": 1,
        "command": "init",
        "memory_path": MEMORY_PATH,
        "standard_positions_path": STANDARD_POSITIONS_MACHINE_PATH,
        "standard_baseline_dir": STANDARD_BASELINE_MACHINE_DIR,
        "output_path": MEMORY_PATH,
    }
    statements = common_sequence_start(sequence_name)
    statements.extend(json_builder_statements(payload, [], "s_PythonInputJson"))
    statements.append(write_file_statement(r"D:\TestMasterData\Process\Python_Automation\python_env\log\v6_init_input.json", "s_PythonInputJson", "b_V6InitInputWritten"))
    statements.append(tmpython_statement("V6VisionWorkflowStep"))
    statements.append(display_status_variable("s_PythonResultJson"))
    statements.append(write_file_statement(r"D:\TestMasterData\Process\Python_Automation\python_env\log\v6_init_result_from_yase.json", "s_PythonResultJson", "b_V6InitResultWritten"))
    statements.extend(return_success_end(sequence_name))
    statements.extend(final_return_end())
    return sequence_document(sequence_name, statements, "Initialize v6 vision workflow memory.")


def generate_capture_sequence(capture_id: str) -> str:
    sequence_name = capture_sequence_path(capture_id).stem
    payload = {
        "schema_version": 1,
        "command": "record_capture",
        "capture_id": capture_id,
        "memory_path": MEMORY_PATH,
        "memory_output_path": MEMORY_PATH,
        "standard_positions_path": STANDARD_POSITIONS_MACHINE_PATH,
        "standard_baseline_dir": STANDARD_BASELINE_MACHINE_DIR,
        "image_path": IMAGE_PATH,
        "roi_output_path": f"{LOG_DIR}/v6_{capture_id}_reviewed_rois.json",
        "review_session_output_path": f"{LOG_DIR}/v6_{capture_id}_reviewed_session.json",
        "result_output_path": f"{LOG_DIR}/v6_{capture_id}_capture_record_result.json",
    }
    numeric_fields = [(field_name, variable_name) for _stage, variable_name, field_name in QUERY_STAGES]
    statements = common_sequence_start(sequence_name)
    statements.extend(stage_check_statements("L_Error_Fiducial"))
    statements.append(grab_statement())
    statements.append(imaq_write_statement())
    statements.extend(query_stage_statements())
    statements.extend(json_builder_statements(payload, numeric_fields, "s_PythonInputJson"))
    safe_capture_id = safe_id(capture_id)
    statements.append(write_file_statement(rf"D:\TestMasterData\Process\Python_Automation\python_env\log\v6_{safe_capture_id}_capture_input.json", "s_PythonInputJson", f"b_V6Capture{safe_capture_id}InputWritten"))
    statements.append(tmpython_statement("V6VisionReviewRecordStep"))
    statements.append(display_status_variable("s_PythonResultJson"))
    statements.append(write_file_statement(rf"D:\TestMasterData\Process\Python_Automation\python_env\log\v6_{safe_capture_id}_capture_result_from_yase.json", "s_PythonResultJson", f"b_V6Capture{safe_capture_id}ResultWritten"))
    statements.extend(parse_python_ok_or_error("L_Error_Python", parse_schema=True))
    statements.extend(return_success_end(sequence_name))
    statements.extend(simple_error_label("L_Error_Fiducial", "31.0", "Stages are not fiducialed. Aborted before v6 capture review."))
    statements.extend(simple_error_label("L_Error_Python", "41.0", "Python capture/review returned ok=false or unsupported schema."))
    statements.extend(final_return_end())
    return sequence_document(sequence_name, statements, f"Capture CAM_12 and record reviewed v6 capture {capture_id}.")


def generate_offset_sequence(capture_id: str) -> str:
    sequence_name = offset_sequence_path(capture_id).stem
    payload = {
        "schema_version": 1,
        "command": "next_offset_correction",
        "capture_id": capture_id,
        "memory_path": MEMORY_PATH,
        "standard_positions_path": STANDARD_POSITIONS_MACHINE_PATH,
        "standard_baseline_dir": STANDARD_BASELINE_MACHINE_DIR,
        "output_path": f"{LOG_DIR}/v6_{capture_id}_offset_result.json",
    }
    numeric_fields = [(field_name, variable_name) for _stage, variable_name, field_name in QUERY_STAGES]
    statements = common_sequence_start(sequence_name)
    statements.extend(stage_check_statements("L_Error_Fiducial"))
    statements.extend(query_stage_statements())
    statements.extend(json_builder_statements(payload, numeric_fields, "s_PythonInputJson"))
    safe_capture_id = safe_id(capture_id)
    statements.append(write_file_statement(rf"D:\TestMasterData\Process\Python_Automation\python_env\log\v6_{safe_capture_id}_offset_input.json", "s_PythonInputJson", f"b_V6Offset{safe_capture_id}InputWritten"))
    statements.append(tmpython_statement("V6VisionWorkflowStep"))
    statements.append(display_status_variable("s_PythonResultJson"))
    statements.append(write_file_statement(rf"D:\TestMasterData\Process\Python_Automation\python_env\log\v6_{safe_capture_id}_offset_result_from_yase.json", "s_PythonResultJson", f"b_V6Offset{safe_capture_id}ResultWritten"))
    statements.extend(parse_python_ok_or_error("L_Error_Python", parse_schema=True))
    statements.extend(parse_action_and_move_count())
    statements.append(ifstring_statement("s_NextAction", "=", "offset_correction_move"))
    statements.append(goto_statement("L_MaybeMove1"))
    statements.append(ifstring_statement("s_NextAction", "=", "no_offset_correction_required"))
    statements.append(goto_statement("L_End"))
    statements.append(goto_statement("L_Error_Action"))
    for index in (1, 2, 3):
        statements.extend(offset_apply_block(index))
    statements.extend(return_success_end(sequence_name))
    statements.extend(simple_error_label("L_Error_Fiducial", "31.0", "Stages are not fiducialed. Aborted before v6 offset correction."))
    statements.extend(simple_error_label("L_Error_Python", "41.0", "Python offset correction returned ok=false or unsupported schema. No move was applied."))
    statements.extend(simple_error_label("L_Error_Action", "42.0", "Python returned an unsupported v6 offset action. No move was applied."))
    statements.extend(final_return_end())
    return sequence_document(sequence_name, statements, f"Calculate and apply v6 offset correction for {capture_id}.")


def generate_transition_sequence(transition_id: str, positions: list[dict[str, Any]]) -> str:
    sequence_name = transition_sequence_path(transition_id).stem
    transition = {
        "schema_version": 1,
        "command": "next_transition_move",
        "transition_id": transition_id,
        "memory_path": MEMORY_PATH,
        "standard_positions_path": STANDARD_POSITIONS_MACHINE_PATH,
        "standard_baseline_dir": STANDARD_BASELINE_MACHINE_DIR,
        "output_path": f"{LOG_DIR}/v6_{transition_id}_transition_result.json",
        "max_single_move_um": 200000.0,
        "move_tolerance_um": 1.0,
    }
    to_position_id = transition_id.split("_to_")[-1]
    to_position = next(position for position in positions if position["id"] == to_position_id)
    numeric_fields = [(field_name, variable_name) for _stage, variable_name, field_name in QUERY_STAGES]
    statements = common_sequence_start(sequence_name)
    statements.extend(stage_check_statements("L_Error_Fiducial"))
    statements.extend(query_stage_statements())
    statements.extend(json_builder_statements(transition, numeric_fields, "s_PythonInputJson"))
    safe_transition_id = safe_id(transition_id)
    statements.append(write_file_statement(rf"D:\TestMasterData\Process\Python_Automation\python_env\log\v6_{safe_transition_id}_transition_input.json", "s_PythonInputJson", f"b_V6Transition{safe_transition_id}InputWritten"))
    statements.append(tmpython_statement("V6VisionWorkflowStep"))
    statements.append(display_status_variable("s_PythonResultJson"))
    statements.append(write_file_statement(rf"D:\TestMasterData\Process\Python_Automation\python_env\log\v6_{safe_transition_id}_transition_result_from_yase.json", "s_PythonResultJson", f"b_V6Transition{safe_transition_id}ResultWritten"))
    statements.extend(parse_python_ok_or_error("L_Error_Python", parse_schema=True))
    statements.append(json_get("String", "action", "s_NextAction"))
    statements.append(ifstring_statement("s_NextAction", "=", "transition_move"))
    statements.append(goto_statement("L_ParseMove"))
    statements.append(ifstring_statement("s_NextAction", "=", "transition_complete"))
    statements.append(goto_statement("L_SetSettings"))
    statements.append(goto_statement("L_Error_Action"))
    statements.extend(
        parse_one_move_block(
            "L_ParseMove",
            1,
            "SUB_V6ApplyApproachMove_Guarded",
            "process\\SUB_v6_vision_workflow",
            max_single_move_um="200000.0",
        )
    )
    statements.append(goto_statement("L_Start"))
    statements.extend(setting_statements(to_position, label="L_SetSettings"))
    statements.extend(return_success_end(sequence_name))
    statements.extend(simple_error_label("L_Error_Fiducial", "31.0", "Stages are not fiducialed. Aborted before v6 transition."))
    statements.extend(simple_error_label("L_Error_Python", "41.0", "Python transition planner returned ok=false or unsupported schema."))
    statements.extend(simple_error_label("L_Error_Action", "42.0", "Python returned an unsupported v6 transition action."))
    statements.extend(final_return_end())
    return sequence_document(sequence_name, statements, f"Rebased v6 transition {transition_id}.")


def generate_main_workflow(positions: list[dict[str, Any]]) -> str:
    sequence_name = "SUB_V6MainWorkflow_Guarded"
    calls = [
        ("SUB_V6SequenceMemoryInit_ReadOnly", "process\\SUB_v6_vision_workflow"),
        (standard_position_path(position_by_id(positions, "1.0")).stem, "process\\SUB_v6_standard_positions"),
        (standard_position_path(position_by_id(positions, "1.1")).stem, "process\\SUB_v6_standard_positions"),
        (standard_position_path(position_by_id(positions, "2.1")).stem, "process\\SUB_v6_standard_positions"),
        (capture_sequence_path("2.1.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("2.1.1").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("2.1.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("2.1.1").stem, "process\\SUB_v6_vision_workflow"),
        (transition_sequence_path("2.1_to_2.4").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("2.4.1").stem, "process\\SUB_v6_vision_workflow"),
        (transition_sequence_path("2.4_to_2.5").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("2.5.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("2.5.1").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("2.5.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("2.5.1").stem, "process\\SUB_v6_vision_workflow"),
        (transition_sequence_path("2.5_to_2.6").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("2.6.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("2.6.1").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("2.6.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("2.6.1").stem, "process\\SUB_v6_vision_workflow"),
        (standard_position_path(position_by_id(positions, "3.0")).stem, "process\\SUB_v6_standard_positions"),
        (standard_position_path(position_by_id(positions, "3.1")).stem, "process\\SUB_v6_standard_positions"),
        (standard_position_path(position_by_id(positions, "4.1")).stem, "process\\SUB_v6_standard_positions"),
        (capture_sequence_path("4.1.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("4.1.1").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("4.1.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("4.1.1").stem, "process\\SUB_v6_vision_workflow"),
        (transition_sequence_path("4.1_to_4.4").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("4.4.1").stem, "process\\SUB_v6_vision_workflow"),
        (transition_sequence_path("4.4_to_4.5").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("4.5.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("4.5.1").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("4.5.1").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("4.5.1").stem, "process\\SUB_v6_vision_workflow"),
        (transition_sequence_path("4.5_to_4.6.2").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("4.6.2").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("4.6.2").stem, "process\\SUB_v6_vision_workflow"),
        (capture_sequence_path("4.6.2").stem, "process\\SUB_v6_vision_workflow"),
        (offset_sequence_path("4.6.2").stem, "process\\SUB_v6_vision_workflow"),
    ]
    statements = common_sequence_start(sequence_name)
    for call_name, library in calls:
        statements.append(subsequence_call(call_name, library))
        statements.append(ifnum_statement("d_ErrorType", "<>", "0.0"))
        statements.append(goto_statement("L_End"))
    statements.extend(success_and_error_end(sequence_name, success_text="SUB_V6MainWorkflow_Guarded complete."))
    return sequence_document(sequence_name, statements, "Full v6 guarded macro workflow.")


def common_sequence_start(sequence_name: str) -> list[str]:
    return [
        statement("****", "XSEQFlowControl", label="L_Start", params=[param("Comment", "String", "Input", string="")]),
        statement("SetString", "Standard", params=[
            param("String 1", "String", "Input", string=sequence_name),
            param("String 2", "String", "Input", string=""),
            param("String out", "String", "Output", variable="s_SequenceName"),
        ]),
        display_status_variable("s_SequenceName"),
        statement("set", "Standard", params=[
            param("Value", "DBL", "Input", string="0.0", numeric="0.0"),
            param("Number out", "DBL", "Output", variable="d_ErrorType"),
        ]),
        statement("SetString", "Standard", params=[
            param("String 1", "String", "Input", string=""),
            param("String 2", "String", "Input", string=""),
            param("String out", "String", "Output", variable="s_ErrorMessage"),
        ]),
    ]


def stage_check_statements(error_label: str) -> list[str]:
    return [
        statement("StageCheckAllFiducialed", "Stage", params=[param("Fiducialed?", "Boolean", "Output", variable="b_StagesFiducialed")]),
        ifnum_statement("b_StagesFiducialed", "=", "0.0"),
        goto_statement(error_label),
    ]


def get_standard_position_velocities() -> list[str]:
    return [
        get_num_var("VelocityCameraMedium", "d_Vel_Camera_Medium"),
        velocity_positive_check("d_Vel_Camera_Medium"),
        get_num_var("VelocityAlignMedium", "d_Vel_Align_Medium"),
        velocity_positive_check("d_Vel_Align_Medium"),
        get_num_var("VelocityZoom", "d_Vel_Zoom"),
        velocity_positive_check("d_Vel_Zoom"),
    ]


def get_apply_approach_velocities() -> list[str]:
    return get_standard_position_velocities()


def get_apply_offset_velocities() -> list[str]:
    return [
        get_num_var("VelocityAlignXSlow", "d_Vel_Align_XSlow"),
        velocity_positive_check("d_Vel_Align_XSlow"),
        get_num_var("VelocityAlignSlow", "d_Vel_Align_Slow"),
        velocity_positive_check("d_Vel_Align_Slow"),
    ]


def get_num_var(name: str, variable: str) -> str:
    return statement(
        "GetNumVar",
        "VariableIO",
        params=[
            param("from", "Enum Word", "Input", string="System"),
            param("Path", "String", "Input", string=""),
            param("Section", "String", "Input", string="MainVelocity"),
            param("Name", "String", "Input", string=name),
            param("VarValueOut", "DBL", "Output", variable=variable),
        ],
    )


def velocity_positive_check(variable: str) -> str:
    return "\n".join([ifnum_statement(variable, "<=", "0.0"), goto_statement("L_Error_Velocity")])


def select_approach_velocity_statements() -> list[str]:
    statements = [set_num("d_MoveVelocity", "0.0")]
    for stage in ("Camera_X", "Camera_Y", "Camera_Z"):
        statements.extend(select_velocity_for_stage(stage, "d_Vel_Camera_Medium"))
    for stage in ("Align_X1", "Align_Y1", "Align_Z1", "Align_X2", "Align_Y2", "Align_Z2"):
        statements.extend(select_velocity_for_stage(stage, "d_Vel_Align_Medium"))
    statements.extend(select_velocity_for_stage("Zoom", "d_Vel_Zoom"))
    statements.append(ifnum_statement("d_MoveVelocity", "<=", "0.0"))
    statements.append(goto_statement("L_Error_Velocity"))
    return statements


def select_offset_velocity_statements() -> list[str]:
    statements = [set_num("d_MoveVelocity", "0.0")]
    for stage in ("Align_X1", "Align_X2"):
        statements.extend(select_velocity_for_stage(stage, "d_Vel_Align_XSlow"))
    for stage in ("Align_Y1", "Align_Z1", "Align_Y2", "Align_Z2"):
        statements.extend(select_velocity_for_stage(stage, "d_Vel_Align_Slow"))
    statements.append(ifnum_statement("d_MoveVelocity", "<=", "0.0"))
    statements.append(goto_statement("L_Error_Velocity"))
    return statements


def select_velocity_for_stage(stage: str, velocity_var: str) -> list[str]:
    return [ifstring_statement("s_MoveStage", "=", stage), set_num("d_MoveVelocity", variable=velocity_var)]


def declare_move_params(default_max: str) -> list[str]:
    return [
        declare_str("Stage", "s_MoveStage"),
        declare_num("TargetUm", "d_TargetUm", "0.0"),
        declare_num("MaxSingleMoveUm", "d_MaxSingleMoveUm", default_max),
        declare_str("ConfirmText", "s_ConfirmText", "Confirm v6 move."),
    ]


def validate_stage_statements(allowed: list[str]) -> list[str]:
    statements = [set_num("b_StageAllowed", "0.0")]
    for stage in allowed:
        statements.append(ifstring_statement("s_MoveStage", "=", stage))
        statements.append(set_num("b_StageAllowed", "1.0"))
    statements.append(ifnum_statement("b_StageAllowed", "=", "0.0"))
    statements.append(goto_statement("L_Error_Stage"))
    return statements


def validate_delta_statements() -> list[str]:
    return [
        statement("QueryStage", "Stage", label="L_ValidateDelta", params=[
            param("Stage", "String", "Input", variable="s_MoveStage"),
            param("Query", "Enum Word", "Input", string="Absolute"),
            param("Position [um]", "DBL", "Output", variable="d_CurrentUm"),
            param("Message", "String", "Output", variable="s_QueryStageMessage"),
        ]),
        statement("calc", "Standard", params=[
            param("Number 1 in", "DBL", "Input", variable="d_TargetUm"),
            param("Operation", "Enum Word", "Input", string="--"),
            param("Number 2 in", "DBL", "Input", variable="d_CurrentUm"),
            param("Number out", "DBL", "Output", variable="d_DeltaUm"),
        ]),
        statement("Math_Absolute", "product_modules\\Functions\\Math\\", params=[
            param("InputValue", "DBL", "Input", variable="d_DeltaUm"),
            param("AbsoluteValue", "DBL", "Output", variable="d_DeltaAbsUm"),
        ]),
        statement("InRange", "Standard", params=[
            param("Value", "DBL", "Input", variable="d_DeltaAbsUm"),
            param("Min", "DBL", "Input", string="0.0", numeric="0.0"),
            param("Max", "DBL", "Input", variable="d_MaxSingleMoveUm"),
            param("In range?", "Boolean", "Output", variable="b_DeltaInRange"),
        ]),
        ifnum_statement("b_DeltaInRange", "=", "0.0"),
        goto_statement("L_Error_Delta"),
    ]


def move_popup_statements() -> list[str]:
    parts = [
        ("s_ConfirmText", " | Stage to move: ", "s_MovePopupText1"),
        ("s_MovePopupText1", None, "s_MovePopupText2", "s_MoveStage"),
        ("s_MovePopupText2", " | Absolute target [um]: ", "s_MovePopupText3"),
    ]
    statements = [
        set_string("s_ConfirmText", " | Stage to move: ", "s_MovePopupText1"),
        set_string("s_MovePopupText1", variable_2="s_MoveStage", out="s_MovePopupText2"),
        set_string("s_MovePopupText2", " | Absolute target [um]: ", "s_MovePopupText3"),
        set_str_num("s_MovePopupText3", "d_TargetUm", "s_MovePopupText4", precision="3"),
        set_string("s_MovePopupText4", " | Current [um]: ", "s_MovePopupText5"),
        set_str_num("s_MovePopupText5", "d_CurrentUm", "s_MovePopupText6", precision="3"),
        set_string("s_MovePopupText6", " | Delta [um]: ", "s_MovePopupText7"),
        set_str_num("s_MovePopupText7", "d_DeltaUm", "s_MovePopupText", precision="3"),
    ]
    return statements


def query_stage_statements() -> list[str]:
    statements = []
    for stage, variable, _field in QUERY_STAGES:
        statements.append(
            statement(
                "QueryStage",
                "Stage",
                params=[
                    param("Stage", "String", "Input", string=stage),
                    param("Query", "Enum Word", "Input", string="Absolute"),
                    param("Position [um]", "DBL", "Output", variable=variable),
                    param("Message", "String", "Output", variable="s_QueryStageMessage"),
                ],
            )
        )
    return statements


def json_builder_statements(payload: dict[str, Any], numeric_fields: list[tuple[str, str]], output_var: str) -> list[str]:
    statements = []
    if not numeric_fields:
        statements.append(
            statement(
                "SetString",
                "Standard",
                params=[
                    param("String 1", "String", "Input", string=json.dumps(payload, separators=(",", ":"))),
                    param("String 2", "String", "Input", string=""),
                    param("String out", "String", "Output", variable=output_var),
                ],
            )
        )
        return statements
    prefix = json.dumps(payload, separators=(",", ":"))[:-1] + f',"{numeric_fields[0][0]}":'
    statements.append(
        statement(
            "SetString",
            "Standard",
            label="L_BuildPythonInput",
            params=[
                param("String 1", "String", "Input", string=prefix),
                param("String 2", "String", "Input", string=""),
                param("String out", "String", "Output", variable="s_Json00"),
            ],
        )
    )
    previous = "s_Json00"
    counter = 1
    for index, (field_name, variable_name) in enumerate(numeric_fields, start=1):
        after_number = f"s_Json{counter:02d}"
        statements.append(set_str_num(previous, variable_name, after_number, precision="6"))
        previous = after_number
        counter += 1
        if index < len(numeric_fields):
            after_key = f"s_Json{counter:02d}"
            next_field = numeric_fields[index][0]
            statements.append(set_string(previous, f',"{next_field}":', after_key))
            previous = after_key
            counter += 1
    statements.append(set_string(previous, "}", output_var))
    return statements


def parse_python_ok_or_error(error_label: str, *, parse_schema: bool) -> list[str]:
    statements = [
        json_get("Boolean", "ok", "b_PythonOk"),
        ifnum_statement("b_PythonOk", "=", "0.0"),
        goto_statement(error_label),
    ]
    if parse_schema:
        statements.extend([
            json_get("Numeric", "schema_version", "d_SchemaVersion"),
            ifnum_statement("d_SchemaVersion", "<>", "1.0"),
            goto_statement(error_label),
        ])
    return statements


def parse_action_and_move_count() -> list[str]:
    return [json_get("String", "action", "s_NextAction"), json_get("Numeric", "move_count", "d_MoveCount")]


def offset_apply_block(index: int) -> list[str]:
    label = f"L_MaybeMove{index}"
    next_label = f"L_MaybeMove{index + 1}" if index < 3 else "L_End"
    statements = [
        ifnum_statement("d_MoveCount", ">=", f"{index}.0", label=label),
        goto_statement(f"L_ParseMove{index}"),
        goto_statement("L_End"),
    ]
    statements.extend(
        parse_one_move_block(
            f"L_ParseMove{index}",
            index,
            "SUB_V6ApplyOffsetCorrectionMove_Guarded",
            "process\\SUB_v6_vision_workflow",
            max_single_move_um="350.0",
        )
    )
    if index < 3:
        statements.append(goto_statement(next_label))
    return statements


def parse_one_move_block(
    label: str,
    index: int,
    sequence_name: str,
    library: str,
    *,
    max_single_move_um: str,
) -> list[str]:
    suffix = str(index)
    return [
        json_get("String", f"stage{suffix}", f"s_MoveStage{suffix}", label=label),
        json_get("Numeric", f"target{suffix}_um", f"d_Target{suffix}Um"),
        json_get("String", f"confirm_text{suffix}", f"s_ConfirmText{suffix}"),
        statement(
            f"SEQ::{sequence_name}",
            library,
            params=[
                param("Stage", "String", "Input", variable=f"s_MoveStage{suffix}"),
                param("TargetUm", "DBL", "Input", variable=f"d_Target{suffix}Um"),
                param("MaxSingleMoveUm", "DBL", "Input", string=max_single_move_um, numeric=max_single_move_um),
                param("ConfirmText", "String", "Input", variable=f"s_ConfirmText{suffix}"),
                param("ErrorType", "DBL", "Output", variable="d_ErrorType"),
                param("ErrorMessage", "String", "Output", variable="s_ErrorMessage"),
            ],
        ),
        ifnum_statement("d_ErrorType", "<>", "0.0"),
        goto_statement("L_End"),
    ]


def setting_statements(position: dict[str, Any], *, label: str | None = None) -> list[str]:
    statements = []
    for index, (line, value) in enumerate(position_analogs(position)):
        statements.append(set_analog_statement(line, value, label=label if index == 0 else ""))
    return statements


def success_and_error_end(sequence_name: str, *, success_text: str) -> list[str]:
    return return_success_end(sequence_name, success_text=success_text) + [
        *simple_error_label("L_Error_Fiducial", "31.0", "Stages are not fiducialed. Aborted before v6 motion."),
        *simple_error_label("L_Error_Stage", "32.0", "Parsed stage is not allowed for this v6 move sequence."),
        *simple_error_label("L_Error_Delta", "33.0", "Planned absolute move delta exceeds MaxSingleMoveUm."),
        *simple_error_label("L_Error_Velocity", "34.0", "A required MainVelocity value is missing or not greater than zero."),
        *simple_error_label("L_Error_UserAbort", "35.0", "Operator aborted before MoveStage or SetAnalogOut."),
        *simple_error_label("L_Error_Timeout", "36.0", "Axis wait timed out. Confirm physical motion has stopped before continuing."),
        *final_return_end(),
    ]


def return_success_end(sequence_name: str, *, success_text: str | None = None) -> list[str]:
    return [
        statement("SetString", "Standard", label="L_Success", params=[
            param("String 1", "String", "Input", string=success_text or f"{sequence_name} complete."),
            param("String 2", "String", "Input", string=""),
            param("String out", "String", "Output", variable="s_ErrorMessage"),
        ]),
        goto_statement("L_End"),
    ]


def final_return_end() -> list[str]:
    return [
        display_status_variable("s_ErrorMessage", label="L_End"),
        statement("ReturnNumParam", "XSEQDefinition", params=[
            param("Name", "String", "Input", string="ErrorType"),
            param("Value", "DBL", "Input", variable="d_ErrorType"),
        ]),
        statement("ReturnStrParam", "XSEQDefinition", params=[
            param("Name", "String", "Input", string="ErrorMessage"),
            param("Value", "String", "Input", variable="s_ErrorMessage"),
        ]),
        statement("EndSeq", "XSEQFlowControl"),
    ]


def error_and_end_statements(error_type: str, message: str) -> list[str]:
    return simple_error_label("L_Error_NoTargets", error_type, message) + final_return_end()


def simple_error_label(label: str, error_type: str, message: str) -> list[str]:
    return [
        set_num("d_ErrorType", error_type, label=label),
        statement("SetString", "Standard", params=[
            param("String 1", "String", "Input", string=message),
            param("String 2", "String", "Input", string=""),
            param("String out", "String", "Output", variable="s_ErrorMessage"),
        ]),
        goto_statement("L_End"),
    ]


def position_moves(position: dict[str, Any], clearance_y_by_tower: dict[str, float]) -> list[tuple[str, str]]:
    machine = position.get("machine_positions_um") or {}
    moves: list[tuple[str, str]] = []
    camera = machine.get("camera") or {}
    for axis, stage in (("x", "Camera_X"), ("z", "Camera_Z"), ("y", "Camera_Y")):
        if camera.get(axis) is not None:
            moves.append((stage, float_string(camera[axis])))
    zoom = setting_value(position, "zoom")
    if zoom is not None:
        moves.append(("Zoom", float_string(zoom)))
    moves = sorted(moves, key=lambda item: STAGE_ORDER.index(item[0]))

    for tower, stage_map in (
        ("tower_1", {"x": "Align_X1", "y": "Align_Y1", "z": "Align_Z1"}),
        ("tower_2", {"x": "Align_X2", "y": "Align_Y2", "z": "Align_Z2"}),
    ):
        values = machine.get(tower) or {}
        target_x = values.get("x")
        target_y = values.get("y")
        target_z = values.get("z")
        has_lateral_target = target_x is not None or target_z is not None
        if has_lateral_target and target_y is not None:
            clearance = max(float(clearance_y_by_tower[tower]), float(target_y))
            moves.append((stage_map["y"], float_string(clearance)))
            if target_z is not None:
                moves.append((stage_map["z"], float_string(target_z)))
            if target_x is not None:
                moves.append((stage_map["x"], float_string(target_x)))
            if float(target_y) != clearance:
                moves.append((stage_map["y"], float_string(target_y)))
            continue
        if target_z is not None:
            moves.append((stage_map["z"], float_string(target_z)))
        if target_x is not None:
            moves.append((stage_map["x"], float_string(target_x)))
        if target_y is not None:
            moves.append((stage_map["y"], float_string(target_y)))
    return moves


def tower_clearance_y_by_tower(positions: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for tower in ("tower_1", "tower_2"):
        values = []
        for position in positions:
            value = ((position.get("machine_positions_um") or {}).get(tower) or {}).get("y")
            if value is not None:
                values.append(float(value))
        if values:
            result[tower] = max(values)
    return result


def position_analogs(position: dict[str, Any]) -> list[tuple[str, str]]:
    analogs = []
    exposure = setting_value(position, "exposure")
    if exposure is not None:
        analogs.append(("cam_12_ExpTime", float_string(exposure)))
    for line in ("Illu_Coax", "Illu_1", "Illu_2"):
        analogs.append((line, "0.9"))
    return analogs


def setting_value(position: dict[str, Any], name: str) -> float | None:
    raw = (position.get("camera_settings") or {}).get(name)
    if isinstance(raw, dict):
        raw = raw.get("value")
    if raw is None:
        return None
    return float(raw)


def velocity_var_for_standard_stage(stage: str) -> str:
    if stage.startswith("Camera_"):
        return "d_Vel_Camera_Medium"
    if stage.startswith("Align_"):
        return "d_Vel_Align_Medium"
    if stage == "Zoom":
        return "d_Vel_Zoom"
    raise ValueError(stage)


def move_stage_and_wait(stage: str, target: str, velocity_var: str) -> list[str]:
    return [
        statement("MoveStage", "Stage", params=[
            param("Stage", "String", "Input", string=stage),
            param("Velocity [um/s]", "DBL", "Input", variable=velocity_var),
            param("Distance [um]", "DBL", "Input", string=target, numeric=target),
            param("Sync", "Enum Word", "Input", string="No sync"),
            param("rel/abs", "Enum Word", "Input", string="Absolute"),
        ]),
        statement("SEQ::SUB_SYS_AxisWaitFinishList", "system..", params=[param("AxisList [CSV Format]", "String", "Input", string=stage)]),
        ifnum_statement("Timeout", "=", "1.0"),
        goto_statement("L_Error_Timeout"),
    ]


def set_analog_statement(line: str, value: str, *, label: str = "") -> str:
    return statement("SetAnalogOut", "Analog", label=label, params=[
        param("Analog Line", "String", "Input", string=line),
        param("Value", "DBL", "Input", string=value, numeric=value),
    ])


def write_file_statement(path: str, data_var: str, success_var: str) -> str:
    return statement("WriteToFile", "FileIO", params=[
        param("File Path", "String", "Input", string=path),
        param("Mode", "Enum Word", "Input", string="New file"),
        param("Data", "String", "Input", variable=data_var),
        param("success", "Boolean", "Output", variable=success_var),
    ])


def tmpython_statement(class_name: str) -> str:
    return statement("TMPython_ExecuteScript", "", label="L_CallPython", params=[
        param("Interpreter", "String", "Input", string=INTERPRETER),
        param("Module", "String", "Input", string=MODULE),
        param("Class", "String", "Input", string=class_name),
        param("ParamIn", "String", "Input", variable="s_PythonInputJson"),
        param("ParamOut", "String", "Output", variable="s_PythonResultJson"),
    ])


def grab_statement() -> str:
    return statement("Grab", "AdvancedIMAQ", params=[
        param("Camera", "String", "Input", string="CAM_12"),
        param("Image Out", "String", "Output", variable="r_Image_Ref"),
    ])


def imaq_write_statement() -> str:
    return statement("IMAQWriteFile", "AdvancedIMAQ", params=[
        param("Image In", "String", "Input", variable="r_Image_Ref"),
        param("FileName", "String", "Input", string="python_vision_input.bmp"),
        param("FileType", "Enum Word", "Input", string="BMP"),
    ])


def subsequence_call(name: str, library: str) -> str:
    return statement(f"SEQ::{name}", library, params=[
        param("ErrorType", "DBL", "Output", variable="d_ErrorType"),
        param("ErrorMessage", "String", "Output", variable="s_ErrorMessage"),
    ])


def declare_str(name: str, variable: str, default: str = "") -> str:
    return statement("DeclareStrParam", "XSEQDefinition", params=[
        param("Name", "String", "Input", string=name),
        param("Default value", "String", "Input", string=default),
        param("Value", "String", "Output", variable=variable),
    ])


def declare_num(name: str, variable: str, default: str) -> str:
    return statement("DeclareNumParam", "XSEQDefinition", params=[
        param("Name", "String", "Input", string=name),
        param("Default value", "DBL", "Input", string=default, numeric=default),
        param("Value", "DBL", "Output", variable=variable),
    ])


def set_num(out: str, value: str | None = None, *, variable: str | None = None, label: str = "") -> str:
    value_param = param("Value", "DBL", "Input", string=value or "0.0", numeric=value or "0.0") if variable is None else param("Value", "DBL", "Input", variable=variable)
    return statement("set", "Standard", label=label, params=[value_param, param("Number out", "DBL", "Output", variable=out)])


def set_string(variable_1: str, string_2: str | None = None, out: str | None = None, *, variable_2: str | None = None) -> str:
    return statement("SetString", "Standard", params=[
        param("String 1", "String", "Input", variable=variable_1),
        param("String 2", "String", "Input", variable=variable_2) if variable_2 else param("String 2", "String", "Input", string=string_2 or ""),
        param("String out", "String", "Output", variable=out or "s_StringOut"),
    ])


def set_str_num(string_var: str, number_var: str, out: str, *, precision: str) -> str:
    return statement("SetStrNum", "Standard", params=[
        param("String", "String", "Input", variable=string_var),
        param("Number", "DBL", "Input", variable=number_var),
        param("Precision", "I32", "Input", string=precision, numeric=precision),
        param("String out", "String", "Output", variable=out),
    ])


def dialog_statement(label: str, text: str | None = None, *, variable: str | None = None, ok: str, skip: str) -> str:
    return statement("DisplayExtdSelectionDialog", "Standard", label=label, params=[
        param("Dialog text", "String", "Input", variable=variable) if variable else param("Dialog text", "String", "Input", string=text or ""),
        param("LeftPos", "DBL", "Input", string="", numeric="0.0"),
        param("TopPos", "DBL", "Input", string="", numeric="0.0"),
        param("Window Title", "String", "Input", variable="s_SequenceName"),
        param("Button 1 (OK) text", "String", "Input", string=ok),
        param("Button 2 (Skip) text", "String", "Input", string=skip),
    ])


def display_status_variable(variable: str, *, label: str = "") -> str:
    return statement("DisplayStatus", "Standard", label=label, params=[param("Status text", "String", "Input", variable=variable)])


def json_get(kind: str, path: str, out_var: str, *, label: str = "") -> str:
    type_name = {"Boolean": "Boolean", "Numeric": "DBL", "String": "String"}[kind]
    return statement(f"JSON_GetFieldValue{kind}", "JSON", label=label, params=[
        param("JSONString in", "String", "Input", variable="s_PythonResultJson"),
        param("Path", "String", "Input", string=path),
        param("Value", type_name, "Output", variable=out_var),
        param("JSONString out", "String", "Output", variable="s_PythonResultJsonParsed"),
    ])


def ifnum_statement(variable: str, comp: str, value: str, *, label: str = "") -> str:
    return statement("ifnum", "Standard", label=label, params=[
        param("Num1", "DBL", "Input", variable=variable),
        param("Comp", "Enum Word", "Input", string=comp),
        param("Num2", "DBL", "Input", string=value, numeric=value),
    ])


def ifstring_statement(variable: str, comp: str, value: str) -> str:
    return statement("ifstring", "Standard", params=[
        param("String1", "String", "Input", variable=variable),
        param("Comp", "Enum Word", "Input", string=comp),
        param("String2", "String", "Input", string=value),
    ])


def goto_statement(label: str) -> str:
    return statement("Goto", "XSEQFlowControl", params=[param("Label", "String", "Input", string=label)])


def statement(name: str, library: str, *, label: str = "", params: list[str] | None = None) -> str:
    params = params or []
    if params:
        inner = "\n".join(f"      {item}" for item in params)
        return f'   <Statement Label="{xml(label)}" Editable="FALSE" Name="{xml(name)}" Library="{xml(library)}">\n{inner}\n   </Statement>'
    return f'   <Statement Label="{xml(label)}" Editable="FALSE" Name="{xml(name)}" Library="{xml(library)}">\n   </Statement>'


def param(
    name: str,
    type_: str,
    direction: str,
    *,
    string: str | None = None,
    numeric: str | None = None,
    variable: str | None = None,
) -> str:
    attrs = [
        f'Name="{xml(name)}"',
        'Description=""',
        f'Type="{xml(type_)}"',
        f'Direction="{xml(direction)}"',
    ]
    if variable is not None:
        attrs.append('ValueType="Variable"')
        attrs.append(f'VariableName="{xml(variable)}"')
    else:
        attrs.append('ValueType="Constant"')
        attrs.append(f'NumericValue="{xml(numeric or "0.0")}"')
        attrs.append(f'StringValue="{xml(string or "")}"')
    return f"<Parameter {' '.join(attrs)} />"


def sequence_document(sequence_name: str, statements: list[str], description: str) -> str:
    comments = [
        f"   <Comment>{xml(sequence_name)}</Comment>",
        f"   <Comment>{xml(description)}</Comment>",
        "   <Comment>Review MACHINE_CONFIGURATION.md and COMMON_MISTAKES.md before machine use.</Comment>",
    ]
    history = f"""   <SequenceProperties>
   </SequenceProperties>
   <SequenceTags>
   </SequenceTags>
   <History>
      <Sequence-Description>{xml(description)}</Sequence-Description>
      <Entry>2026-07-22 Generated for migration v6 guarded vision workflow</Entry>
   </History>"""
    return "<?xml version=\"1.0\" encoding=\"ISO-8859-1\"?>\n<Sequence>\n" + "\n".join(comments + statements + [history]) + "\n</Sequence>\n"


def standard_position_path(position: dict[str, Any]) -> Path:
    return STANDARD_POSITION_DIR / f"SUB_V6MoveToPosition_{position['id']}_{slug(position['label'])}.xseq"


def capture_sequence_path(capture_id: str) -> Path:
    return WORKFLOW_DIR / f"SUB_V6CaptureReviewRecord_{capture_id}_ReadOnly.xseq"


def offset_sequence_path(capture_id: str) -> Path:
    return WORKFLOW_DIR / f"SUB_V6OffsetCorrection_{capture_id}_Guarded.xseq"


def transition_sequence_path(transition_id: str) -> Path:
    return WORKFLOW_DIR / f"SUB_V6TransitionMove_{transition_id}_Guarded.xseq"


def position_by_id(positions: list[dict[str, Any]], position_id: str) -> dict[str, Any]:
    return next(position for position in positions if position["id"] == position_id)


def slug(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return re.sub(r"_+", "_", clean).strip("_") or "position"


def safe_id(value: str) -> str:
    return slug(value.replace(".", "_"))


def float_string(value: Any) -> str:
    value = float(value)
    if value.is_integer():
        return f"{int(value)}.0"
    return f"{value:.12g}"


def xml(value: str) -> str:
    return escape(str(value), quote=True)


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="ISO-8859-1")


if __name__ == "__main__":
    main()
