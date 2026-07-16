import copy
import importlib.util
import json
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from interactive_setup import DEFAULT_CLIPPING_RADIUS_FACTOR, LaserSource, default_ball_lens_layout
from migrations.migration_v1.python_alignment_solving.fixed_z_alignment_solver import solve_fixed_z_alignment


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_INPUT = ROOT / "migrations" / "migration_v1" / "python_alignment_solving" / "examples" / "fixed_z_alignment_input.json"
EXAMPLE_OUTPUT = ROOT / "migrations" / "migration_v1" / "python_alignment_solving" / "examples" / "fixed_z_alignment_output.json"
YASE_SEQUENCE = (
    ROOT
    / "migrations"
    / "migration_v1"
    / "SUB_alignment_solving"
    / "SUB_FixedZAlignmentSolving_ReadOnly.xseq"
)
YASE_APPLY_MOVE_SEQUENCE = (
    ROOT
    / "migrations"
    / "migration_v1"
    / "SUB_alignment_solving"
    / "SUB_ApplyFixedZAlignmentSolveMove.xseq"
)


def _load_alignment_module(name):
    package_name = "alignment_algorithms"
    package = sys.modules.get(package_name)
    if package is None:
        package = types.ModuleType(package_name)
        package.__path__ = [str(ROOT / package_name)]
        sys.modules[package_name] = package

    qualified_name = f"{package_name}.{name}"
    if qualified_name in sys.modules:
        return sys.modules[qualified_name]

    spec = importlib.util.spec_from_file_location(qualified_name, ROOT / package_name / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _example_payload():
    return json.loads(EXAMPLE_INPUT.read_text(encoding="utf-8"))


def _example_output():
    return json.loads(EXAMPLE_OUTPUT.read_text(encoding="utf-8"))


def _simulation_geometry_from_payload(payload):
    base = _load_alignment_module("base")
    source = LaserSource()
    source_info = payload["geometry_um"]["laser"]
    source.x_offset = source_info["x_um"] * 1e-6
    source.y_offset = source_info["z_um"] * 1e-6
    source.position = source_info["optical_y_um"] * 1e-6
    source.x_angle = source_info["x_angle_mrad"] * 1e-3
    source.y_angle = source_info["z_angle_mrad"] * 1e-3

    balls, tapers, _final_z = default_ball_lens_layout()
    for ball, ball_info in zip(balls, payload["geometry_um"]["balls"]):
        ball.x_offset = ball_info["x_um"] * 1e-6
        ball.y_offset = ball_info["z_um"] * 1e-6
        ball.position = ball_info["optical_y_um"] * 1e-6
        ball.diameter = ball_info["diameter_um"] * 1e-6
        ball.refractive_index = ball_info["refractive_index"]

    taper = tapers[0]
    fiber_info = payload["geometry_um"]["fiber"]
    taper.x_offset = fiber_info["x_um"] * 1e-6
    taper.y_offset = fiber_info["z_um"] * 1e-6
    taper.position = fiber_info["optical_y_um"] * 1e-6

    poses = tuple((ball.x_offset, ball.y_offset, ball.position) for ball in balls)
    return base.AlignmentModelGeometry(
        source=base.SourceGeometry(
            name=source.name,
            position=source.position,
            wavelength=source.wavelength,
            waist_radius=source.waist_radius,
            waist_radius_y=source.waist_radius_y,
            rayleigh_range=source.rayleigh_range,
            rayleigh_range_y=source.rayleigh_range_y,
            waist_position=source.waist_position,
            power=source.power,
            x_offset=source.x_offset,
            y_offset=source.y_offset,
            x_angle=source.x_angle,
            y_angle=source.y_angle,
        ),
        taper=base.TaperGeometry(
            name=taper.name,
            position=taper.position,
            width=taper.width,
            height=taper.height,
            mode_radius_x=taper.mode_radius_x,
            mode_radius_y=taper.mode_radius_y,
            extra_transmission=taper.extra_transmission,
            facet_refractive_index=taper.facet_refractive_index,
            x_offset=taper.x_offset,
            y_offset=taper.y_offset,
        ),
        balls=tuple(
            base.BallLensGeometry(
                name=ball.name,
                position=ball.position,
                diameter=ball.diameter,
                refractive_index=ball.refractive_index,
                x_offset=ball.x_offset,
                y_offset=ball.y_offset,
            )
            for ball in balls
        ),
        current_poses=poses,
        starting_poses=poses,
        clipping_radius_factor=DEFAULT_CLIPPING_RADIUS_FACTOR,
    )


def test_migration_v1_fixed_z_solver_matches_simulation_fixed_z_transverse_solve():
    payload = _example_payload()
    _load_alignment_module("given_positions")
    position_solve = _load_alignment_module("position_solve")

    result = solve_fixed_z_alignment(payload)
    target = result["state"]["target_positions_um"]

    geometry = _simulation_geometry_from_payload(payload)
    z_positions = tuple(pose[2] for pose in geometry.current_poses)
    candidate = position_solve._PositionSolver(axial_search_window=0.0)._candidate_from_z_positions(geometry, z_positions)

    assert candidate is not None
    assert np.isclose(target["Align_X1"] * 1e-6, candidate.poses[0][0])
    assert np.isclose(target["Align_Z1"] * 1e-6, candidate.poses[0][1])
    assert np.isclose(target["Align_Y1"] * 1e-6, candidate.poses[0][2])
    assert np.isclose(target["Align_X2"] * 1e-6, candidate.poses[1][0])
    assert np.isclose(target["Align_Z2"] * 1e-6, candidate.poses[1][1])
    assert np.isclose(target["Align_Y2"] * 1e-6, candidate.poses[1][2])


def test_migration_v1_fixed_z_solver_matches_checked_in_example_output():
    assert solve_fixed_z_alignment(_example_payload()) == _example_output()


def test_migration_v1_fixed_z_solver_detours_around_custom_no_go_zone():
    payload = _example_payload()
    payload = copy.deepcopy(payload)
    for ball in payload["geometry_um"]["balls"]:
        ball["diameter_um"] = 1.0
    payload["limits"]["no_go_zones_um"] = [
        {
            "name": "test_block_between_current_and_target",
            "optical_y_min_um": 288.0,
            "optical_y_max_um": 290.0,
            "x_min_um": 7.0,
            "x_max_um": 8.0,
            "z_min_um": -5.5,
            "z_max_um": -4.5,
        }
    ]

    result = solve_fixed_z_alignment(payload)

    assert result["action"] == "move"
    assert result["stage1"] == "Align_X1"
    assert len(result["state"]["path_um"]) >= 4
    assert result["state"]["path_um"][0]["Align_X1"] == 12.0
    assert result["state"]["path_um"][1]["Align_Z1"] == -8.0
    assert all(
        not (
            7.0 < waypoint["Align_X1"] < 8.0
            and -5.5 < waypoint["Align_Z1"] < -4.5
            and waypoint["Align_Y1"] == 289.0
        )
        for waypoint in result["state"]["path_um"]
    )


def test_migration_v1_yase_sequence_is_read_only_tmpython_handoff():
    root = ET.parse(YASE_SEQUENCE).getroot()
    names = [statement.attrib["Name"] for statement in root.findall("Statement")]
    string_values = [
        parameter.attrib.get("StringValue", "")
        for parameter in root.iter("Parameter")
        if "StringValue" in parameter.attrib
    ]

    assert "TMPython_ExecuteScript" in names
    assert "MoveStage" not in names
    assert "Python_310_PYTHON_AUTOMATION_INTERPRETER" in string_values
    assert "fixed_z_alignment_solver" in string_values
    assert "FixedZAlignmentSolveStep" in string_values
    parameter_names = [parameter.attrib["Name"] for parameter in root.iter("Parameter")]
    assert "ParamIn" in parameter_names
    assert "ParamOut" in parameter_names


def test_migration_v1_yase_apply_move_sequence_contains_guarded_movestage():
    root = ET.parse(YASE_APPLY_MOVE_SEQUENCE).getroot()
    names = [statement.attrib["Name"] for statement in root.findall("Statement")]
    string_values = [
        parameter.attrib.get("StringValue", "")
        for parameter in root.iter("Parameter")
        if "StringValue" in parameter.attrib
    ]

    assert "DeclareStrParam" in names
    assert "DeclareNumParam" in names
    assert "StageCheckAllFiducialed" in names
    assert "Math_Absolute" in names
    assert "InRange" in names
    assert "MoveStage" in names
    assert "SEQ::SUB_SYS_AxisWaitFinishList" in names
    assert "SEQ::SUB_SysCheckAxisMove" in names
    assert {"Align_X1", "Align_Z1", "Align_X2", "Align_Z2"}.issubset(set(string_values))
