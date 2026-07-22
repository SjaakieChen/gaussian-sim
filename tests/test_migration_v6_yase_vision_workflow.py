import copy
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from migrations.migration_v6.python_vision_geometry.v6_offset_workflow import (
    initialize_v6_memory,
    mirror_ball_reference_delta_px,
    run_v6_vision_workflow,
)


ROOT = Path(__file__).resolve().parents[1]
V4_STANDARD_POSITIONS = ROOT / "Standard position images" / "v4" / "standard_positions.json"
V6 = ROOT / "migrations" / "migration_v6"
V6_STANDARD_POSITIONS = V6 / "standard_positions.json"
V6_STANDARD_POSITIONS_COPY = V6 / "standard_positions_v4" / "standard_positions.json"
V6_STANDARD_POSITION_DIR = V6 / "SUB_v6_standard_positions"
V6_WORKFLOW_DIR = V6 / "SUB_v6_vision_workflow"
V6_XSEQ_FILES = sorted(V6.glob("SUB_*/*.xseq"))

CAPTURE_IDS = ["2.1.1", "2.4.1", "2.5.1", "2.6.1", "4.1.1", "4.4.1", "4.5.1", "4.6.2"]
OFFSET_CAPTURE_IDS = ["2.1.1", "2.5.1", "2.6.1", "4.1.1", "4.5.1", "4.6.2"]
TRANSITIONS = ["2.1_to_2.4", "2.4_to_2.5", "2.5_to_2.6", "4.1_to_4.4", "4.4_to_4.5", "4.5_to_4.6.2"]

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


def _slug(value):
    clean = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip())
    return re.sub(r"_+", "_", clean).strip("_") or "position"


def _position_sequence_path(position):
    return V6_STANDARD_POSITION_DIR / f"SUB_V6MoveToPosition_{position['id']}_{_slug(position['label'])}.xseq"


def _capture_sequence_path(capture_id):
    return V6_WORKFLOW_DIR / f"SUB_V6CaptureReviewRecord_{capture_id}_ReadOnly.xseq"


def _offset_sequence_path(capture_id):
    return V6_WORKFLOW_DIR / f"SUB_V6OffsetCorrection_{capture_id}_Guarded.xseq"


def _transition_sequence_path(transition_id):
    return V6_WORKFLOW_DIR / f"SUB_V6TransitionMove_{transition_id}_Guarded.xseq"


def _xseq_root(path):
    return ET.parse(path).getroot()


def _statements(path):
    return _xseq_root(path).findall("Statement")


def _params_by_name(statement):
    return {parameter.attrib["Name"]: parameter.attrib for parameter in statement.findall("Parameter")}


def _statement_names(path):
    return [statement.attrib["Name"] for statement in _statements(path)]


def _string_values(path):
    return [
        parameter.attrib.get("StringValue", "")
        for parameter in _xseq_root(path).iter("Parameter")
        if "StringValue" in parameter.attrib
    ]


def _float_string(value):
    value = float(value)
    if value.is_integer():
        return f"{int(value)}.0"
    return f"{value:.12g}"


def _setting_value(position, name):
    raw = (position.get("camera_settings") or {}).get(name)
    if isinstance(raw, dict):
        raw = raw.get("value")
    return raw


def _expected_moves(position):
    moves = []
    machine_positions = position.get("machine_positions_um") or {}
    camera = machine_positions.get("camera") or {}
    for axis, stage in (("x", "Camera_X"), ("z", "Camera_Z"), ("y", "Camera_Y")):
        value = camera.get(axis)
        if value is not None:
            moves.append((stage, _float_string(value)))

    zoom = _setting_value(position, "zoom")
    if zoom is not None:
        moves.append(("Zoom", _float_string(zoom)))
    moves = sorted(moves, key=lambda item: STAGE_ORDER.index(item[0]))

    clearances = _tower_clearance_y_by_tower()
    for tower, stage_map in (
        ("tower_1", {"x": "Align_X1", "y": "Align_Y1", "z": "Align_Z1"}),
        ("tower_2", {"x": "Align_X2", "y": "Align_Y2", "z": "Align_Z2"}),
    ):
        values = machine_positions.get(tower) or {}
        target_x = values.get("x")
        target_y = values.get("y")
        target_z = values.get("z")
        has_lateral_target = target_x is not None or target_z is not None
        if has_lateral_target and target_y is not None:
            clearance = max(clearances[tower], float(target_y))
            moves.append((stage_map["y"], _float_string(clearance)))
            if target_z is not None:
                moves.append((stage_map["z"], _float_string(target_z)))
            if target_x is not None:
                moves.append((stage_map["x"], _float_string(target_x)))
            if float(target_y) != clearance:
                moves.append((stage_map["y"], _float_string(target_y)))
            continue
        if target_z is not None:
            moves.append((stage_map["z"], _float_string(target_z)))
        if target_x is not None:
            moves.append((stage_map["x"], _float_string(target_x)))
        if target_y is not None:
            moves.append((stage_map["y"], _float_string(target_y)))

    return moves


def _tower_clearance_y_by_tower():
    result = {}
    for tower in ("tower_1", "tower_2"):
        values = []
        for position in _standard_positions_payload()["positions"]:
            value = ((position.get("machine_positions_um") or {}).get(tower) or {}).get("y")
            if value is not None:
                values.append(float(value))
        result[tower] = max(values)
    return result


def _move_targets(path):
    targets = []
    for statement in _statements(path):
        if statement.attrib["Name"] != "MoveStage":
            continue
        params = _params_by_name(statement)
        targets.append((params["Stage"]["StringValue"], params["Distance [um]"]["StringValue"]))
    return targets


def _analog_targets(path):
    targets = {}
    for statement in _statements(path):
        if statement.attrib["Name"] != "SetAnalogOut":
            continue
        params = _params_by_name(statement)
        targets[params["Analog Line"]["StringValue"]] = params["Value"]["StringValue"]
    return targets


def _tmpython_params(path):
    statement = next(statement for statement in _statements(path) if statement.attrib["Name"] == "TMPython_ExecuteScript")
    return _params_by_name(statement)


def _standard_positions_payload():
    return json.loads(V4_STANDARD_POSITIONS.read_text(encoding="utf-8"))


def _position_by_id(position_id):
    return next(position for position in _standard_positions_payload()["positions"] if position["id"] == position_id)


def _circle_session(x, y, *, radius=50.0, role="ball", target=""):
    item = {
        "shape_kind": "circle",
        "feature_role": role,
        "selection_index": 1,
        "shape": {"x": x, "y": y, "radius": radius},
    }
    if target:
        item["target"] = target
    return {"selected_recognition": {"roi_1": [item]}}


def _rectangle_session(center_x=100.0, center_y=50.0, *, short_edge=100.0):
    half = short_edge / 2.0
    return {
        "selected_recognition": {
            "roi_1": [
                {
                    "shape_kind": "rectangle",
                    "feature_role": "laser_reference",
                    "selection_index": 1,
                    "shape": {
                        "x1": center_x - half,
                        "y1": center_y - half,
                        "x2": center_x + half,
                        "y2": center_y + half,
                    },
                }
            ]
        }
    }


def _side_session(ball_y, reference_y, *, ball_x=400.0, radius=50.0):
    session = _circle_session(ball_x, ball_y, radius=radius, role="side_ball", target="ball_1")
    session["side_reference_line"] = {
        "y_px": reference_y,
        "x1_px": 100.0,
        "x2_px": 900.0,
        "source": "reviewed_side_reference",
        "score": 1.0,
    }
    return session


def _ambiguous_side_session():
    return {
        "selected_recognition": {
            "roi_1": [
                {
                    "shape_kind": "circle",
                    "feature_role": "side_ball",
                    "selection_index": 1,
                    "shape": {"x": 400.0, "y": 710.0, "radius": 50.0},
                },
                {
                    "shape_kind": "line",
                    "feature_role": "side_reference",
                    "selection_index": 2,
                    "shape": {"x1": 100.0, "y1": 900.0, "x2": 900.0, "y2": 900.0},
                },
                {
                    "shape_kind": "line",
                    "feature_role": "side_reference",
                    "selection_index": 3,
                    "shape": {"x1": 100.0, "y1": 880.0, "x2": 900.0, "y2": 880.0},
                },
            ]
        }
    }


def _current_machine_positions():
    return {
        "camera": {"x": -38000.0, "y": -45000.0, "z": -93000.0},
        "tower_1": {"x": 1000.0, "y": 1000.0, "z": 2000.0},
        "tower_2": {"x": 3000.0, "y": 4000.0, "z": 5000.0},
        "zoom": {"value": 0.0},
    }


def _memory_with_records(*, standard_baselines, capture_records):
    memory = initialize_v6_memory({"schema_version": 1})
    memory["standard_baselines"] = copy.deepcopy(standard_baselines)
    memory["capture_records"] = {
        capture_id: {"capture_id": capture_id, "session": copy.deepcopy(session)}
        for capture_id, session in capture_records.items()
    }
    return memory


def test_v6_standard_positions_are_copied_from_v4_source():
    source = json.loads(V4_STANDARD_POSITIONS.read_text(encoding="utf-8"))

    assert json.loads(V6_STANDARD_POSITIONS.read_text(encoding="utf-8")) == source
    assert json.loads(V6_STANDARD_POSITIONS_COPY.read_text(encoding="utf-8")) == source


def test_v6_xseq_files_parse_and_avoid_stale_machine_fields():
    assert len(V6_XSEQ_FILES) == 41
    for path in V6_XSEQ_FILES:
        statements = _statements(path)
        labels = {statement.attrib.get("Label", "") for statement in statements}
        labels.discard("")
        assert all(not label.startswith("@") for label in labels), path

        for statement in statements:
            if statement.attrib["Name"] != "Goto":
                continue
            target = _params_by_name(statement)["Label"]["StringValue"]
            assert target in labels, f"{path}: Goto target {target!r} not found"

    all_v6_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in V6.rglob("*")
        if path.is_file() and path.suffix.lower() in {".xseq", ".py", ".json", ".md", ".txt"}
    )
    for forbidden in [
        "Python_310_ALIGNMENT_TEST",
        "Python_37_PYTHON_AUTOMATION_INTERPRETER",
        "Input JSON",
        "Result JSON",
        "#SM_PROCESS#",
        "C:\\Users\\",
        "OneDrive",
    ]:
        assert forbidden not in all_v6_text


def test_v6_hardcoded_position_sequences_match_standard_targets_and_settings():
    positions = _standard_positions_payload()["positions"]
    assert [position["id"] for position in positions] == [
        "1.0",
        "1.1",
        "2.1",
        "2.2",
        "2.3",
        "2.4",
        "2.5",
        "2.6",
        "3.0",
        "3.1",
        "4.1",
        "4.2",
        "4.3",
        "4.4",
        "4.5",
        "4.6.1",
        "4.6.2",
    ]

    for position in positions:
        path = _position_sequence_path(position)
        assert path.is_file()
        assert _move_targets(path) == _expected_moves(position)

        analogs = _analog_targets(path)
        assert analogs["cam_12_ExpTime"] == _float_string(_setting_value(position, "exposure"))
        assert analogs["Illu_Coax"] == "0.9"
        assert analogs["Illu_1"] == "0.9"
        assert analogs["Illu_2"] == "0.9"


def test_v6_standard_position_tower_motion_raises_y_before_lateral_then_lowers():
    clearances = _tower_clearance_y_by_tower()

    for position in _standard_positions_payload()["positions"]:
        path = _position_sequence_path(position)
        moves = _move_targets(path)
        for tower, stages in {
            "tower_1": ("Align_Y1", "Align_Z1", "Align_X1"),
            "tower_2": ("Align_Y2", "Align_Z2", "Align_X2"),
        }.items():
            values = ((position.get("machine_positions_um") or {}).get(tower) or {})
            if values.get("x") is None and values.get("z") is None:
                continue
            target_y = values.get("y")
            if target_y is None:
                continue

            stage_y, stage_z, stage_x = stages
            first_y_index = next(i for i, move in enumerate(moves) if move[0] == stage_y)
            lateral_indices = [i for i, move in enumerate(moves) if move[0] in {stage_z, stage_x}]
            assert lateral_indices
            assert first_y_index < min(lateral_indices)
            assert moves[first_y_index] == (stage_y, _float_string(max(clearances[tower], float(target_y))))

            if float(target_y) < clearances[tower]:
                assert moves[max(lateral_indices) + 1] == (stage_y, _float_string(target_y))


def test_v6_close_to_chip_moves_do_not_use_fast_velocity():
    close_to_chip_text = "\n".join(path.read_text(encoding="ISO-8859-1") for path in V6_XSEQ_FILES)
    for forbidden in ["VelocityCameraFast", "VelocityCameraXFast", "VelocityAlignFast"]:
        assert forbidden not in close_to_chip_text

    expected_standard_velocity = {
        "Camera_X": "d_Vel_Camera_Medium",
        "Camera_Z": "d_Vel_Camera_Medium",
        "Camera_Y": "d_Vel_Camera_Medium",
        "Zoom": "d_Vel_Zoom",
        "Align_X1": "d_Vel_Align_Medium",
        "Align_Z1": "d_Vel_Align_Medium",
        "Align_Y1": "d_Vel_Align_Medium",
        "Align_X2": "d_Vel_Align_Medium",
        "Align_Z2": "d_Vel_Align_Medium",
        "Align_Y2": "d_Vel_Align_Medium",
    }
    for path in V6_STANDARD_POSITION_DIR.glob("SUB_V6MoveToPosition_*.xseq"):
        for statement in _statements(path):
            if statement.attrib["Name"] != "MoveStage":
                continue
            params = _params_by_name(statement)
            stage = params["Stage"]["StringValue"]
            assert params["Velocity [um/s]"]["VariableName"] == expected_standard_velocity[stage]

    offset_text = (V6_WORKFLOW_DIR / "SUB_V6ApplyOffsetCorrectionMove_Guarded.xseq").read_text(
        encoding="ISO-8859-1"
    )
    assert "VelocityAlignXSlow" in offset_text
    assert "VelocityAlignSlow" in offset_text
    assert "VelocityAlignMedium" not in offset_text
    assert "VelocityCameraMedium" not in offset_text
    assert "VelocityZoom" not in offset_text
    assert "Camera_" not in offset_text
    assert "Zoom" not in offset_text


def test_v6_capture_review_sequences_are_read_only_ui_recognition_steps():
    for capture_id in CAPTURE_IDS:
        path = _capture_sequence_path(capture_id)
        statements = _statements(path)
        names = [statement.attrib["Name"] for statement in statements]
        strings = "\n".join(_string_values(path))

        assert "Grab" in names
        assert "IMAQWriteFile" in names
        assert names.count("QueryStage") == 10
        assert "MoveStage" not in names
        assert "SetAnalogOut" not in names
        assert names.index("Grab") < names.index("IMAQWriteFile") < names.index("TMPython_ExecuteScript")

        tmpython = _tmpython_params(path)
        assert tmpython["Interpreter"]["StringValue"] == "Python_310_PYTHON_AUTOMATION_INTERPRETER"
        assert tmpython["Module"]["StringValue"] == "python_vision_geometry.v6_offset_workflow"
        assert tmpython["Class"]["StringValue"] == "V6VisionReviewRecordStep"
        assert tmpython["ParamIn"]["VariableName"] == "s_PythonInputJson"
        assert tmpython["ParamOut"]["VariableName"] == "s_PythonResultJson"
        assert '"command":"record_capture"' in strings
        assert f'"capture_id":"{capture_id}"' in strings


def test_v6_offset_and_transition_sequences_use_python_then_guarded_apply_sequences():
    for capture_id in OFFSET_CAPTURE_IDS:
        path = _offset_sequence_path(capture_id)
        names = _statement_names(path)
        strings = "\n".join(_string_values(path))

        assert "Grab" not in names
        assert "IMAQWriteFile" not in names
        assert names.count("QueryStage") == 10
        assert "TMPython_ExecuteScript" in names
        assert _tmpython_params(path)["Class"]["StringValue"] == "V6VisionWorkflowStep"
        assert '"command":"next_offset_correction"' in strings
        assert f'"capture_id":"{capture_id}"' in strings
        assert "move_count" in strings
        assert "stage1" in strings
        assert "target1_um" in strings
        assert names.count("SEQ::SUB_V6ApplyOffsetCorrectionMove_Guarded") == 3

    for transition_id in TRANSITIONS:
        path = _transition_sequence_path(transition_id)
        names = _statement_names(path)
        strings = "\n".join(_string_values(path))

        assert "Grab" not in names
        assert "IMAQWriteFile" not in names
        assert names.count("QueryStage") == 10
        assert _tmpython_params(path)["Class"]["StringValue"] == "V6VisionWorkflowStep"
        assert '"command":"next_transition_move"' in strings
        assert f'"transition_id":"{transition_id}"' in strings
        assert names.count("SEQ::SUB_V6ApplyApproachMove_Guarded") == 1
        assert "transition_complete" in strings
        assert "L_Start" in [
            _params_by_name(statement)["Label"]["StringValue"]
            for statement in _statements(path)
            if statement.attrib["Name"] == "Goto"
        ]


def test_v6_main_workflow_chains_capture_correction_and_second_review_passes():
    names = _statement_names(V6_WORKFLOW_DIR / "SUB_V6MainWorkflow_Guarded.xseq")

    assert names[0] == "****"
    assert "SEQ::SUB_V6SequenceMemoryInit_ReadOnly" in names
    for capture_id in OFFSET_CAPTURE_IDS:
        assert names.count(f"SEQ::SUB_V6CaptureReviewRecord_{capture_id}_ReadOnly") == 2
        assert names.count(f"SEQ::SUB_V6OffsetCorrection_{capture_id}_Guarded") == 2
    for capture_id in ["2.4.1", "4.4.1"]:
        assert names.count(f"SEQ::SUB_V6CaptureReviewRecord_{capture_id}_ReadOnly") == 1
    for transition_id in TRANSITIONS:
        assert names.count(f"SEQ::SUB_V6TransitionMove_{transition_id}_Guarded") == 1


def test_v6_coarse_top_offset_maps_image_x_to_z_and_image_y_to_x():
    standard = _circle_session(100.0, 200.0, radius=50.0, role="gross_ball", target="ball_1")
    live = _circle_session(110.0, 190.0, radius=50.0, role="gross_ball", target="ball_1")
    memory = _memory_with_records(standard_baselines={"2.1.1": standard}, capture_records={"2.1.1": live})

    result = run_v6_vision_workflow(
        {
            "schema_version": 1,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory": memory,
            "machine_positions_um": _current_machine_positions(),
        }
    )

    assert result["ok"] is True
    assert result["action"] == "offset_correction_move"
    assert result["move_count"] == 2
    assert result["stage1"] == "Align_X1"
    assert result["delta1_um"] == pytest.approx(-50.0)
    assert result["target1_um"] == pytest.approx(950.0)
    assert result["stage2"] == "Align_Z1"
    assert result["delta2_um"] == pytest.approx(50.0)
    assert result["target2_um"] == pytest.approx(2050.0)
    mapping = result["diagnostics"]["correction"]["view_mapping"]
    assert mapping["image_x"]["tower_axis"] == "z"
    assert mapping["image_y"]["tower_axis"] == "x"


def test_v6_fine_top_offset_uses_remaining_ball_to_reference_residual():
    standard_reference = _rectangle_session(100.0, 50.0, short_edge=100.0)
    live_reference = _rectangle_session(100.0, 50.0, short_edge=100.0)
    standard_ball = _circle_session(110.0, 40.0, radius=50.0, role="top_ball", target="ball_1")
    live_ball = _circle_session(112.0, 44.0, radius=50.0, role="top_ball", target="ball_1")
    memory = _memory_with_records(
        standard_baselines={"2.4.1": standard_reference, "2.5.1": standard_ball},
        capture_records={"2.4.1": live_reference, "2.5.1": live_ball},
    )

    result = run_v6_vision_workflow(
        {
            "schema_version": 1,
            "command": "next_offset_correction",
            "capture_id": "2.5.1",
            "memory": memory,
            "machine_positions_um": _current_machine_positions(),
        }
    )

    assert result["ok"] is True
    assert result["move_count"] == 2
    assert result["stage1"] == "Align_X1"
    assert result["delta1_um"] == pytest.approx(20.0)
    assert result["target1_um"] == pytest.approx(1020.0)
    assert result["stage2"] == "Align_Z1"
    assert result["delta2_um"] == pytest.approx(10.0)
    assert result["target2_um"] == pytest.approx(2010.0)
    correction = result["diagnostics"]["correction"]
    assert correction["standard_delta_px"] == {"x": 10.0, "y": -10.0}
    assert correction["live_delta_px"] == {"x": 12.0, "y": -6.0}
    assert correction["residual_px"] == {"x": 2.0, "y": 4.0}


def test_v6_side_mirror_offset_flips_mirror_y_before_machine_y_correction():
    standard_side = _side_session(700.0, 900.0)
    live_side = _side_session(710.0, 900.0)
    memory = _memory_with_records(standard_baselines={"2.6.1": standard_side}, capture_records={"2.6.1": live_side})

    result = run_v6_vision_workflow(
        {
            "schema_version": 1,
            "command": "next_offset_correction",
            "capture_id": "2.6.1",
            "memory": memory,
            "machine_positions_um": _current_machine_positions(),
            "mirror_roi": {"x1": 0.0, "y1": 600.0, "x2": 1000.0, "y2": 1000.0},
        }
    )

    assert result["ok"] is True
    assert result["move_count"] == 1
    assert result["stage1"] == "Align_Y1"
    assert result["delta1_um"] == pytest.approx(-50.0)
    assert result["target1_um"] == pytest.approx(950.0)

    correction = result["diagnostics"]["correction"]
    assert correction["mirror_view"] is True
    assert correction["mirror_flip_y"] is True
    assert correction["standard_mirror_transform"]["mirror_flipped"]["ball_y_px"] == pytest.approx(300.0)
    assert correction["standard_mirror_transform"]["mirror_flipped"]["reference_y_px"] == pytest.approx(100.0)
    assert correction["live_mirror_transform"]["mirror_flipped"]["ball_y_px"] == pytest.approx(290.0)
    assert correction["residual_flipped_y_px"] == pytest.approx(-10.0)
    assert correction["view_mapping"]["mirror_image_y_after_flip"]["tower_axis"] == "y"


def test_v6_side_mirror_transform_maps_top_of_mirror_after_vertical_flip():
    transform = mirror_ball_reference_delta_px(
        ball_y_px=600.0,
        reference_y_px=900.0,
        mirror_roi={"x1": 0.0, "y1": 600.0, "x2": 1000.0, "y2": 1000.0},
    )

    assert transform["full_image"]["raw_delta_y_px"] == pytest.approx(-300.0)
    assert transform["mirror_flipped"]["ball_y_px"] == pytest.approx(400.0)
    assert transform["mirror_flipped"]["reference_y_px"] == pytest.approx(100.0)
    assert transform["flipped_delta_y_px"] == pytest.approx(300.0)


def test_v6_offset_bounds_fail_closed_without_motion():
    standard = _circle_session(100.0, 200.0, radius=50.0, role="gross_ball", target="ball_1")
    live = _circle_session(200.0, 200.0, radius=50.0, role="gross_ball", target="ball_1")
    memory = _memory_with_records(standard_baselines={"2.1.1": standard}, capture_records={"2.1.1": live})

    result = run_v6_vision_workflow(
        {
            "schema_version": 1,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory": memory,
            "machine_positions_um": _current_machine_positions(),
            "max_correction_um": 100.0,
        }
    )

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert result["move_count"] == 0
    assert result["stage1"] == ""
    assert "exceeds max_correction_um" in result["status"]


@pytest.mark.parametrize(
    ("standard_session", "live_session", "expected_status"),
    [
        (_side_session(700.0, 900.0), _circle_session(400.0, 710.0, radius=50.0, role="side_ball"), "side_reference"),
        (_side_session(700.0, 900.0), _ambiguous_side_session(), "ambiguous side_reference"),
    ],
)
def test_v6_missing_or_ambiguous_side_mirror_reference_fails_closed(
    standard_session, live_session, expected_status
):
    memory = _memory_with_records(
        standard_baselines={"2.6.1": standard_session},
        capture_records={"2.6.1": live_session},
    )

    result = run_v6_vision_workflow(
        {
            "schema_version": 1,
            "command": "next_offset_correction",
            "capture_id": "2.6.1",
            "memory": memory,
            "machine_positions_um": _current_machine_positions(),
            "mirror_roi": {"x1": 0.0, "y1": 600.0, "x2": 1000.0, "y2": 1000.0},
        }
    )

    assert result["ok"] is False
    assert result["action"] == "abort"
    assert result["move_count"] == 0
    assert result["stage1"] == ""
    assert expected_status in result["status"]


def test_v6_transition_move_rebases_standard_delta_from_current_live_position():
    current = _current_machine_positions()
    current["tower_1"] = {"x": 5000.0, "y": 12000.0, "z": 15000.0}
    payload = _standard_positions_payload()

    result = run_v6_vision_workflow(
        {
            "schema_version": 1,
            "command": "next_transition_move",
            "transition_id": "2.1_to_2.4",
            "standard_positions": payload,
            "machine_positions_um": current,
            "move_tolerance_um": 0.0,
        }
    )

    assert result["ok"] is True
    assert result["action"] == "transition_move"
    targets = result["target_positions_um"]
    assert targets["camera"]["x"] == pytest.approx(-38000.0)
    assert targets["camera"]["y"] == pytest.approx(-44399.0)
    assert targets["camera"]["z"] == pytest.approx(-89301.0)
    assert targets["tower_1"]["x"] == pytest.approx(5000.0)
    assert targets["tower_1"]["y"] == pytest.approx(11001.0)
    assert targets["tower_1"]["z"] == pytest.approx(17200.0)
    assert result["stage1"] == "Align_Y1"
    assert result["target1_um"] == pytest.approx(17000.0)
    assert result["delta1_um"] == pytest.approx(5000.0)


def test_v6_transition_plan_is_anchored_once_across_yase_loop_iterations(tmp_path):
    memory_path = tmp_path / "v6_memory.json"
    init_result = run_v6_vision_workflow(
        {
            "schema_version": 1,
            "command": "init",
            "memory_path": str(memory_path),
            "standard_positions_path": str(V6_STANDARD_POSITIONS),
        }
    )
    assert init_result["ok"] is True
    assert memory_path.is_file()

    current = _current_machine_positions()
    current["tower_1"] = {"x": 5000.0, "y": 17000.0, "z": 15000.0}
    payload = _standard_positions_payload()
    request = {
        "schema_version": 1,
        "command": "next_transition_move",
        "transition_id": "2.1_to_2.4",
        "standard_positions": payload,
        "memory_path": str(memory_path),
        "machine_positions_um": current,
        "move_tolerance_um": 0.0,
    }

    first = run_v6_vision_workflow(request)
    assert first["ok"] is True
    assert first["stage1"] == "Camera_Z"
    assert first["target1_um"] == pytest.approx(-89301.0)

    after_first_move = copy.deepcopy(current)
    after_first_move["camera"]["z"] = first["target1_um"]
    second = run_v6_vision_workflow({**request, "machine_positions_um": after_first_move})

    assert second["ok"] is True
    assert second["target_positions_um"]["camera"]["z"] == pytest.approx(first["target_positions_um"]["camera"]["z"])
    assert second["transition_anchor_positions_um"]["camera"]["z"] == pytest.approx(current["camera"]["z"])
    assert second["stage1"] != "Camera_Z"
    assert second["stage1"] == "Zoom"
