import copy
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from alignment_lab import (
    DEFAULT_TAPER_TRENCH_Y_MAX,
    alignment_no_go_zones_for_layout,
    ball_lens_no_go_violations,
)
from migration.migration_v2.python_alignment_solving.fixed_z_staged_ball_placement import (
    BallSpec,
    default_no_go_zones,
    no_go_violations,
    parse_geometry,
    solve_fixed_z_staged_ball_placement,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_INPUT = (
    ROOT
    / "migration"
    / "migration_v2"
    / "python_alignment_solving"
    / "examples"
    / "fixed_z_staged_ball_placement_input.json"
)
YASE_READ_ONLY_SEQUENCE = (
    ROOT
    / "migration"
    / "migration_v2"
    / "SUB_alignment_solving"
    / "SUB_FixedZStagedBallPlacement_ReadOnly.xseq"
)
YASE_APPLY_MOVE_SEQUENCE = (
    ROOT
    / "migration"
    / "migration_v2"
    / "SUB_alignment_solving"
    / "SUB_ApplyFixedZStagedBallMove.xseq"
)


def _example_payload():
    return json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))


def test_migration_v2_plans_above_then_solved_then_lower_moves_with_machine_axis_mapping():
    result = solve_fixed_z_staged_ball_placement(_example_payload())

    assert result["action"] == "move"
    assert result["schema_version"] == 2
    assert result["stage1"] == "Align_Y1"
    assert result["move_mode1"] == "Absolute"
    assert result["distance1_um"] == result["target1_um"] == 505.0
    assert "confirm" in result["confirm_text1"]

    moves = result["planned_moves"]
    assert [move["phase"] for move in moves] == [
        "raise_clearance",
        "raise_clearance",
        "move_to_solved_coordinates",
        "move_to_solved_coordinates",
        "lower_to_solved_coordinate",
        "lower_to_solved_coordinate",
    ]
    assert [move["stage"] for move in moves] == [
        "Align_Y1",
        "Align_Y2",
        "Align_Z1",
        "Align_Z2",
        "Align_Y1",
        "Align_Y2",
    ]
    assert all(move["confirm_required"] for move in moves)
    assert all(move["mode"] == "absolute" for move in moves)

    targets = result["state"]["target_positions_um"]
    assert targets["Align_X1"] == pytest.approx(289.0)
    assert targets["Align_X2"] == pytest.approx(989.0)
    assert targets["Align_Z1"] == pytest.approx(1.3458646616541354)
    assert targets["Align_Z2"] == pytest.approx(0.6541353383458642)
    assert targets["Align_Y1"] == pytest.approx(-0.3458646616541352)
    assert targets["Align_Y2"] == pytest.approx(0.34586466165413554)
    assert result["state"]["axis_mapping"]["machine_x"] == "simulation_z_optical_axis"
    assert result["state"]["axis_mapping"]["machine_z"] == "simulation_x"
    assert result["state"]["axis_mapping"]["machine_y"] == "simulation_y"


def test_migration_v2_recomputes_until_done_after_each_absolute_move():
    payload = _example_payload()
    seen_stages = []

    for _ in range(10):
        result = solve_fixed_z_staged_ball_placement(payload)
        if result["action"] == "done":
            break
        assert result["action"] == "move"
        move = result["moves"][0]
        seen_stages.append(move["stage"])
        payload["machine"]["positions_um"][move["stage"]] = move["target_um"]
        payload["state"] = result["state"]
    else:  # pragma: no cover - explicit failure path
        pytest.fail("planner did not converge to done")

    assert seen_stages == ["Align_Y1", "Align_Y2", "Align_Z1", "Align_Z2", "Align_Y1", "Align_Y2"]
    assert result["action"] == "done"


def test_migration_v2_uses_same_edge_overlap_collision_semantics_as_alignment_lab():
    payload = _example_payload()
    geometry = parse_geometry(payload)
    radius_um = geometry.balls[0].radius_um
    collision_pose_um = (0.0, -500.0 + radius_um - 1e-6, geometry.balls[0].y_um)
    v2_violations = no_go_violations(
        (collision_pose_um, geometry.balls[1].pose),
        geometry.balls,
        tuple(default_no_go_zones(geometry)),
    )

    radius_m = radius_um * 1e-6
    ui_collision_pose_m = (
        collision_pose_um[0] * 1e-6,
        DEFAULT_TAPER_TRENCH_Y_MAX + radius_m - 1e-12,
        collision_pose_um[2] * 1e-6,
    )
    ui_second_pose_m = (
        geometry.balls[1].x_um * 1e-6,
        geometry.balls[1].z_um * 1e-6,
        geometry.balls[1].y_um * 1e-6,
    )
    ui_zones = alignment_no_go_zones_for_layout(
        source_z=geometry.source.y_um * 1e-6,
        taper_z=geometry.detector.y_um * 1e-6,
        final_z=geometry.detector.y_um * 1e-6,
        ball_poses=(ui_collision_pose_m, ui_second_pose_m),
        ball_radii=(radius_m, geometry.balls[1].radius_um * 1e-6),
    )
    ui_violations = ball_lens_no_go_violations(
        (ui_collision_pose_m, ui_second_pose_m),
        (radius_m, geometry.balls[1].radius_um * 1e-6),
        ui_zones,
        ("ball_1", "ball_2"),
    )

    assert [violation["zone"] for violation in v2_violations] == ["trench_floor"]
    assert [violation.zone.name for violation in ui_violations] == ["trench_floor"]


def test_migration_v2_aborts_when_custom_no_go_zone_blocks_clearance_path():
    payload = copy.deepcopy(_example_payload())
    payload["limits"]["no_go_zones_um"] = [
        {
            "name": "blocks_raise_clearance",
            "machine_x_min_um": 0.0,
            "machine_x_max_um": 1278.0,
            "machine_y_min_um": 400.0,
            "machine_y_max_um": 600.0,
        }
    ]

    result = solve_fixed_z_staged_ball_placement(payload)

    assert result["action"] == "abort"
    assert "no-go" in result["message"]


def test_migration_v2_read_only_sequence_calls_tmpython_without_movestage():
    root = ET.parse(YASE_READ_ONLY_SEQUENCE).getroot()
    names = [statement.attrib["Name"] for statement in root.findall("Statement")]
    string_values = [
        parameter.attrib.get("StringValue", "")
        for parameter in root.iter("Parameter")
        if "StringValue" in parameter.attrib
    ]
    shrink_collapsed = [shrink.attrib.get("Collapsed") for shrink in root.findall("Shrink")]

    assert "StageCheckAllFiducialed" in names
    assert "TMPython_ExecuteScript" in names
    assert "MoveStage" not in names
    assert "python_alignment_solving.fixed_z_staged_ball_placement" in string_values
    assert "FixedZStagedBallPlacementStep" in string_values
    assert "true" in shrink_collapsed


def test_migration_v2_apply_move_sequence_has_popup_before_absolute_movestage():
    root = ET.parse(YASE_APPLY_MOVE_SEQUENCE).getroot()
    statements = root.findall("Statement")
    names = [statement.attrib["Name"] for statement in statements]
    string_values = [
        parameter.attrib.get("StringValue", "")
        for parameter in root.iter("Parameter")
        if "StringValue" in parameter.attrib
    ]

    assert "DeclareStrParam" in names
    assert "DeclareNumParam" in names
    assert "StageCheckAllFiducialed" in names
    assert "QueryStage" in names
    assert "calc" in names
    assert "Math_Absolute" in names
    assert "InRange" in names
    assert "DisplayExtdSelectionDialog" in names
    assert "MoveStage" in names
    assert "SEQ::SUB_SYS_AxisWaitFinishList" in names
    assert "SEQ::SUB_SysCheckAxisMove" in names
    assert {"Align_X1", "Align_Z1", "Align_Y1", "Align_X2", "Align_Z2", "Align_Y2"}.issubset(
        set(string_values)
    )
    assert "Abort" in string_values
    assert "Move" in string_values
    assert "Absolute" in string_values

    popup_index = names.index("DisplayExtdSelectionDialog")
    movestage_index = names.index("MoveStage")
    assert popup_index < movestage_index
