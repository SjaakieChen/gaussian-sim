import copy
import json
import re
import tkinter as tk
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from migrations.migration_v6.python_vision_geometry import v6_offset_workflow as v6_module
from migrations.migration_v6.python_vision_geometry.position_bias_planner import (
    DEFAULT_GROSS_AUTO_FEATURE_SPECS,
    auto_detect_gross_ball_session,
)
from migrations.migration_v6.python_vision_geometry.v6_offset_workflow import (
    CAPTURE_SPECS,
    CORRECTION_PREREQUISITES,
    DEFAULT_STANDARD_BASELINE_DIR,
    DEFAULT_STANDARD_POSITIONS_PATH,
    FINAL_CENTER_SPACING_UM,
    FINAL_TARGETS_UM,
    OFFSET_SPECS,
    SCHEMA_VERSION,
    TRANSITION_SPECS,
    final_layout_clearance,
    initialize_v6_memory,
    mirror_ball_reference_delta_px,
    reviewed_trench_geometry,
    run_v6_vision_workflow,
    validate_reviewed_capture_session,
)
from migrations.migration_v6.vision_recognition_lab import (
    DIRECT_TOP_VIEW_MAX_Y_FRACTION,
    FEATURE_ROLE_CHOICES,
    VisionROI,
    VisionRecognitionLab,
    detect_coarse_top_ball_circle,
    detect_side_trench_ruler_lines,
    feature_role_display_label,
    feature_role_options_for_capture,
    photo_image_from_grayscale,
    read_grayscale_image,
)


ROOT = Path(__file__).resolve().parents[1]
V4_STANDARD_POSITIONS = ROOT / "Standard position images" / "v4" / "standard_positions.json"
V6 = ROOT / "migrations" / "migration_v6"
V6_STANDARD_POSITIONS = V6 / "standard_positions.json"
V6_STANDARD_POSITIONS_COPY = V6 / "standard_positions_v4" / "standard_positions.json"
V6_MEASUREMENT_PLAN = V6 / "measurement_plan.json"
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


def _convergence_sequence_path(capture_id):
    return V6_WORKFLOW_DIR / f"SUB_V6Converge_{capture_id}_Guarded.xseq"


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
    return f"{int(value)}.0" if value.is_integer() else f"{value:.12g}"


def _setting_value(position, name):
    raw = (position.get("camera_settings") or {}).get(name)
    if isinstance(raw, dict):
        raw = raw.get("value")
    return raw


def _standard_positions_payload():
    return json.loads(V4_STANDARD_POSITIONS.read_text(encoding="utf-8"))


def _tower_clearance_y_by_tower():
    result = {}
    for tower in ("tower_1", "tower_2"):
        result[tower] = max(
            float(position["machine_positions_um"][tower]["y"])
            for position in _standard_positions_payload()["positions"]
            if position["machine_positions_um"].get(tower, {}).get("y") is not None
        )
    return result


def _expected_moves(position):
    raises = []
    camera_moves = []
    lateral_moves = []
    lowers = []
    machine_positions = position.get("machine_positions_um") or {}
    clearances = _tower_clearance_y_by_tower()
    tower_targets = []
    for tower, stages in (
        ("tower_1", {"x": "Align_X1", "y": "Align_Y1", "z": "Align_Z1"}),
        ("tower_2", {"x": "Align_X2", "y": "Align_Y2", "z": "Align_Z2"}),
    ):
        values = machine_positions.get(tower) or {}
        target_x, target_y, target_z = values.get("x"), values.get("y"), values.get("z")
        has_lateral_target = target_x is not None or target_z is not None
        clearance = None
        if has_lateral_target:
            clearance = max(clearances[tower], float(target_y))
            raises.append((stages["y"], _float_string(clearance)))
        tower_targets.append(
            (stages, target_x, target_y, target_z, has_lateral_target, clearance)
        )

    camera = machine_positions.get("camera") or {}
    for axis, stage in (("x", "Camera_X"), ("z", "Camera_Z"), ("y", "Camera_Y")):
        if camera.get(axis) is not None:
            camera_moves.append((stage, _float_string(camera[axis])))
    zoom = _setting_value(position, "zoom")
    if zoom is not None:
        camera_moves.append(("Zoom", _float_string(zoom)))
    camera_moves.sort(key=lambda item: STAGE_ORDER.index(item[0]))

    for stages, target_x, target_y, target_z, has_lateral_target, clearance in tower_targets:
        if target_z is not None:
            lateral_moves.append((stages["z"], _float_string(target_z)))
        if target_x is not None:
            lateral_moves.append((stages["x"], _float_string(target_x)))
        if target_y is not None and (not has_lateral_target or float(target_y) != clearance):
            lowers.append((stages["y"], _float_string(target_y)))
    return raises + camera_moves + lateral_moves + lowers


def _move_targets(path):
    targets = []
    for statement in _statements(path):
        if statement.attrib["Name"] == "MoveStage":
            params = _params_by_name(statement)
            targets.append((params["Stage"]["StringValue"], params["Distance [um]"]["StringValue"]))
    return targets


def _analog_targets(path):
    targets = {}
    for statement in _statements(path):
        if statement.attrib["Name"] == "SetAnalogOut":
            params = _params_by_name(statement)
            targets[params["Analog Line"]["StringValue"]] = params["Value"]["StringValue"]
    return targets


def _tmpython_params(path):
    statement = next(statement for statement in _statements(path) if statement.attrib["Name"] == "TMPython_ExecuteScript")
    return _params_by_name(statement)


def _pose(*, camera_x=-38000.0, camera_y=-45000.0, camera_z=-93000.0, zoom=4500.0):
    return {
        "camera": {
            "machine_x_um": camera_x,
            "machine_y_um": camera_y,
            "machine_z_um": camera_z,
        },
        "tower_1": {"machine_x_um": 1000.0, "machine_y_um": 17000.0, "machine_z_um": 2000.0},
        "tower_2": {"machine_x_um": 3000.0, "machine_y_um": 13000.0, "machine_z_um": 5000.0},
        "zoom": {"zoom_um": zoom},
    }


def _legacy_pose():
    pose = _pose()
    return {
        "camera": {"x": pose["camera"]["machine_x_um"], "y": pose["camera"]["machine_y_um"], "z": pose["camera"]["machine_z_um"]},
        "tower_1": {
            "x": pose["tower_1"]["machine_x_um"],
            "y": pose["tower_1"]["machine_y_um"],
            "z": pose["tower_1"]["machine_z_um"],
        },
        "tower_2": {
            "x": pose["tower_2"]["machine_x_um"],
            "y": pose["tower_2"]["machine_y_um"],
            "z": pose["tower_2"]["machine_z_um"],
        },
        "zoom": {"value": 4500.0},
    }


def test_photo_image_from_grayscale_uses_explicit_tk_master():
    first_root = None
    second_root = None
    try:
        first_root = tk.Tk()
        first_root.withdraw()
        second_root = tk.Tk()
        second_root.withdraw()
    except tk.TclError as exc:
        if second_root is not None:
            second_root.destroy()
        if first_root is not None:
            first_root.destroy()
        pytest.skip(f"Tk unavailable: {exc}")
    try:
        image = photo_image_from_grayscale(np.zeros((4, 4), dtype=float), master=second_root)
        canvas = tk.Canvas(second_root, width=4, height=4)
        canvas.create_image(0, 0, image=image, anchor="nw")
    finally:
        if second_root is not None:
            second_root.destroy()
        if first_root is not None:
            first_root.destroy()


def _circle_session(x, y, *, radius=50.0, role="ball_candidate"):
    return {
        "selected_recognition": {
            "roi_1": [
                {
                    "shape_kind": "circle",
                    "feature_role": role,
                    "selection_index": 1,
                    "shape": {"x": x, "y": y, "radius": radius},
                }
            ]
        }
    }


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


def _side_session(ball_y, *, target="ball_1", top_y=900.0, floor_y=650.0):
    role = f"{target}_side_ball"
    session = _circle_session(400.0, ball_y, radius=50.0, role=role)
    session["mirror_roi"] = {"x1": 0.0, "y1": 600.0, "x2": 1000.0, "y2": 1000.0}
    session["selected_recognition"]["roi_1"].extend(
        [
            {
                "shape_kind": "line",
                "feature_role": "trench_bottom_floor",
                "selection_index": 2,
                "shape": {"x1": 100.0, "y1": floor_y, "x2": 900.0, "y2": floor_y},
            },
            {
                "shape_kind": "line",
                "feature_role": "trench_top_surface",
                "selection_index": 3,
                "shape": {"x1": 100.0, "y1": top_y, "x2": 900.0, "y2": top_y},
            },
        ]
    )
    return session


def _record(capture_id, session, *, pose=None, revision=1):
    spec = copy.deepcopy(CAPTURE_SPECS[capture_id])
    pose = copy.deepcopy(pose or _pose())
    return {
        **spec,
        "capture_id": capture_id,
        "revision": revision,
        "session": copy.deepcopy(session),
        "machine_positions_um": pose,
        "image_dimensions_px": {"image_width_px": 2592, "image_height_px": 1944},
        "calibration_context": {
            "view": spec["view"],
            "zoom_um": pose["zoom"]["zoom_um"],
            "image_width_px": 2592,
            "image_height_px": 1944,
        },
    }


def _memory_with_records(*, standard_baselines=None, records):
    memory = initialize_v6_memory({"schema_version": SCHEMA_VERSION})
    memory["standard_baselines"] = copy.deepcopy(standard_baselines or {})
    memory["capture_records"] = copy.deepcopy(records)
    return memory


def _valid_workflow_records():
    top_scale_px_per_um = 1.0 / 5.0
    return {
        "2.1.1": _record(
            "2.1.1",
            _circle_session(100.0, 100.0, role="ball_1_gross_ball"),
        ),
        "2.4.1": _record("2.4.1", _rectangle_session()),
        "2.5.1": _record(
            "2.5.1",
            _circle_session(
                100.0 + 289.0 * top_scale_px_per_um,
                50.0,
                role="ball_1_top_ball",
            ),
        ),
        "2.6.1": _record("2.6.1", _side_session(900.0)),
        "4.1.1": _record(
            "4.1.1",
            _circle_session(100.0, 100.0, role="ball_2_gross_ball"),
        ),
        "4.4.1": _record("4.4.1", _rectangle_session()),
        "4.5.1": _record(
            "4.5.1",
            _circle_session(
                100.0 + 989.0 * top_scale_px_per_um,
                50.0,
                role="ball_2_top_ball",
            ),
        ),
        "4.6.2": _record(
            "4.6.2",
            _side_session(900.0, target="ball_2"),
        ),
    }


def _mark_converged(memory, capture_id):
    record = memory["capture_records"][capture_id]
    memory["convergence"][capture_id] = {
        "capture_id": capture_id,
        "capture_revision": record["revision"],
        "status": "converged",
    }


def _satisfy_correction_prerequisites(memory, capture_id):
    required_capture_ids = CAPTURE_IDS[: CAPTURE_IDS.index(capture_id) + 1]
    for default_capture_id, record in _valid_workflow_records().items():
        if default_capture_id not in required_capture_ids:
            continue
        memory["capture_records"].setdefault(default_capture_id, record)
    prerequisite = CORRECTION_PREREQUISITES[capture_id]
    for required_capture_id in prerequisite.get("converged", ()):
        _mark_converged(memory, required_capture_id)
    for transition_id in prerequisite.get("transitions", ()):
        memory["transition_records"][transition_id] = {
            "transition_id": transition_id,
            "status": "complete",
        }
    return memory


def _complete_workflow_state(memory):
    for default_capture_id, record in _valid_workflow_records().items():
        memory["capture_records"].setdefault(default_capture_id, record)
    for capture_id in OFFSET_SPECS:
        _mark_converged(memory, capture_id)
    for transition_id in TRANSITION_SPECS:
        memory["transition_records"][transition_id] = {
            "transition_id": transition_id,
            "status": "complete",
        }
    return memory


def _ball_2_fine_memory(*, measured_machine_x_um, measured_machine_z_um):
    ball_pose = _pose(camera_z=-93000.0)
    reference_pose = _pose(camera_z=-93000.0 - measured_machine_z_um)
    memory = _memory_with_records(
        records={
            "4.4.1": _record(
                "4.4.1",
                _rectangle_session(),
                pose=reference_pose,
            ),
            "4.5.1": _record(
                "4.5.1",
                _circle_session(
                    100.0 + measured_machine_x_um / 5.0,
                    50.0,
                    role="ball_2_top_ball",
                ),
                pose=ball_pose,
            ),
        }
    )
    _satisfy_correction_prerequisites(memory, "4.5.1")
    return memory, ball_pose


def test_v6_standard_positions_are_exact_v4_copies():
    source = json.loads(V4_STANDARD_POSITIONS.read_text(encoding="utf-8"))
    assert json.loads(V6_STANDARD_POSITIONS.read_text(encoding="utf-8")) == source
    assert json.loads(V6_STANDARD_POSITIONS_COPY.read_text(encoding="utf-8")) == source


def test_v6_default_runtime_data_is_the_complete_copy_ready_v6_set():
    assert DEFAULT_STANDARD_POSITIONS_PATH.resolve() == V6_STANDARD_POSITIONS_COPY.resolve()
    assert DEFAULT_STANDARD_BASELINE_DIR.resolve() == (
        V6 / "standard_positions_v4" / "vision_baselines"
    ).resolve()
    assert DEFAULT_STANDARD_POSITIONS_PATH.is_file()

    for capture_id in CAPTURE_IDS:
        baseline_path = DEFAULT_STANDARD_BASELINE_DIR / f"{capture_id}.json"
        assert baseline_path.is_file()
        validate_reviewed_capture_session(
            capture_id,
            json.loads(baseline_path.read_text(encoding="utf-8")),
            {},
        )


@pytest.mark.parametrize(
    ("capture_id", "expected_role"),
    (
        ("2.1.1", "ball_1_gross_ball"),
        ("4.1.1", "ball_2_gross_ball"),
    ),
)
def test_v6_coarse_baselines_and_auto_detection_use_upper_direct_view(
    capture_id,
    expected_role,
):
    image_path = ROOT / "Standard position images" / "v4" / "newhead" / f"{capture_id}.PNG"
    gray = read_grayscale_image(image_path)
    direct_view_max_y = gray.shape[0] * DIRECT_TOP_VIEW_MAX_Y_FRACTION
    baseline = json.loads(
        (DEFAULT_STANDARD_BASELINE_DIR / f"{capture_id}.json").read_text(encoding="utf-8")
    )
    selected = [
        item
        for items in baseline["selected_recognition"].values()
        for item in items
        if item["feature_role"] == expected_role
    ]

    assert len(selected) == 1
    assert selected[0]["shape_kind"] == "circle"
    assert selected[0]["shape"]["y"] < direct_view_max_y
    assert selected[0]["roi"]["y2"] <= direct_view_max_y

    spec = DEFAULT_GROSS_AUTO_FEATURE_SPECS[capture_id]
    assert spec["roi"][3] <= direct_view_max_y
    auto_session = auto_detect_gross_ball_session({}, capture_id, image_path)
    detected = auto_session["selected_recognition"]["roi_1"][0]["shape"]
    assert detected["y"] < direct_view_max_y
    assert detected["x"] == pytest.approx(selected[0]["shape"]["x"], abs=1.0)
    assert detected["y"] == pytest.approx(selected[0]["shape"]["y"], abs=1.0)
    assert detected["radius"] == pytest.approx(selected[0]["shape"]["radius"], abs=1.0)


def test_v6_coarse_top_recognizer_rejects_lower_mirror_roi():
    image_path = ROOT / "Standard position images" / "v4" / "newhead" / "2.1.1.PNG"
    gray = read_grayscale_image(image_path)

    with pytest.raises(ValueError, match="lower mirror"):
        detect_coarse_top_ball_circle(
            gray,
            (VisionROI("circle", 1100.0, 1500.0, 1700.0, 1944.0),),
            "2.1.1",
        )


def test_v6_workflow_rejects_coarse_top_circle_in_lower_mirror_region():
    assert (
        v6_module.DEFAULT_DIRECT_TOP_VIEW_MAX_Y_FRACTION
        == DIRECT_TOP_VIEW_MAX_Y_FRACTION
    )
    session = _circle_session(1275.5, 1713.5, role="ball_1_gross_ball")
    session["image_dimensions_px"] = {
        "image_width_px": 2592,
        "image_height_px": 1944,
    }

    with pytest.raises(ValueError, match="lower mirror region"):
        validate_reviewed_capture_session("2.1.1", session, {})


def test_v6_measurement_plan_and_operator_docs_use_schema_2_canonical_contract():
    plan = json.loads(V6_MEASUREMENT_PLAN.read_text(encoding="utf-8"))
    assert plan["schema_version"] == 2
    assert plan["canonical_axes"]["image_right"]["machine_axis"] == "machine_x_um"
    assert plan["canonical_axes"]["image_up"]["machine_axis"] == "machine_z_um"
    assert plan["canonical_axes"]["mirror_corrected_vertical"]["machine_axis"] == "machine_y_um"
    assert plan["final_targets_um"]["ball_1"] == {
        "machine_x_um": 289.0,
        "machine_y_um": 0.0,
        "machine_z_um": 0.0,
    }
    assert plan["final_targets_um"]["ball_2"]["machine_x_um"] == 989.0
    assert plan["final_targets_um"]["ball_center_spacing_machine_x_um"] == 700.0
    assert plan["physical_constants_um"]["trench_top_to_floor_um"] == 300.0
    assert plan["final_clearance_model_um"] == {
        "strict_positive_gap_required": True,
        "source_to_ball_1_surface_gap_um": 39.0,
        "ball_1_to_ball_2_surface_gap_um": 200.0,
        "ball_2_to_taper_surface_gap_um": 39.0,
        "ball_surface_to_trench_floor_gap_um": 50.0,
        "coordinate_frame": "reviewed final rectangle-relative machine X/Y/Z frame",
    }
    assert plan["convergence"]["max_correction_attempts"] == 8
    assert plan["convergence"]["max_reviewed_captures_including_final_check"] == 9
    assert plan["motion_policy"]["standard_position_order"][0].startswith(
        "raise every tower"
    )
    assert "2.5.1 converged" in plan["workflow_prerequisites"]["2.6.1"]

    readme = (V6 / "README.md").read_text(encoding="utf-8")
    assert "Do not rerun the memory initializer between normal steps" in readme
    assert "mirror_flipped_y_px = mirror_roi_bottom_y_px - full_image_y_px" in readme
    assert "--popup-scope all" in readme
    assert "ball 1 to ball 2 = 200 um" in readme
    assert "It is not physical machine validation" in readme.replace("\n", " ")

    mistakes = (ROOT / "COMMON_MISTAKES.md").read_text(encoding="utf-8")
    assert "Use medium speed for reviewed approach moves" in mistakes
    assert "Use slow speed for image-derived offset corrections" in mistakes


def test_v6_review_ui_preloads_live_proposal_allows_override_and_tracks_cancel():
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")
    root.withdraw()
    baseline = json.loads(
        (V6 / "standard_positions_v4" / "vision_baselines" / "2.1.1.json").read_text(
            encoding="utf-8"
        )
    )
    image_path = ROOT / "Standard position images" / "v4" / "newhead" / "2.1.1.PNG"
    lab = None
    try:
        lab = VisionRecognitionLab(
            root,
            captured_image_path=image_path,
            initial_session=baseline,
            capture_id="2.1.1",
            session_done_callback=lambda: None,
            show_session_done_button=True,
        )
        lab.update_idletasks()
        selected = lab.selected_recognition_payload()
        assert len(lab.current_rois()) == 1
        assert selected["roi_1"][0]["feature_role"] == "ball_1_gross_ball"
        assert selected["roi_1"][0]["shape"]["x"] == pytest.approx(1269.5, abs=1.0)
        assert selected["roi_1"][0]["shape"]["y"] == pytest.approx(619.5, abs=1.0)
        assert selected["roi_1"][0]["shape"]["radius"] == pytest.approx(62.4, abs=1.0)
        assert tuple(lab.feature_role_combobox.cget("values")) == (
            "Ball 1 circle (COARSE TOP view)",
            "Ignore this detection",
        )
        assert lab.feature_role_context_var.get() == (
            "Review target: BALL 1 - COARSE TOP CAMERA VIEW"
        )

        selected_item_id = next(iter(lab._selected_recognition_item_ids))  # pylint: disable=protected-access
        lab.recognition_tree.selection_set(selected_item_id)
        lab.recognition_tree.focus(selected_item_id)
        lab.feature_role_var.set("Ignore this detection")
        lab._set_selected_recognition_role()  # pylint: disable=protected-access
        assert lab.selected_recognition_payload()["roi_1"][0]["feature_role"] == "ignore"

        lab._cancel_session()  # pylint: disable=protected-access
        assert lab._session_cancelled is True  # pylint: disable=protected-access
        assert lab._session_saved is False  # pylint: disable=protected-access
    finally:
        if lab is not None:
            lab.destroy()
        root.destroy()


def test_v6_capture_role_choices_are_filtered_and_name_the_camera_view():
    expected_roles = {
        "2.1.1": ("ball_1_gross_ball", "ignore"),
        "2.4.1": ("laser_reference", "ignore"),
        "2.5.1": ("ball_1_top_ball", "ignore"),
        "2.6.1": (
            "ball_1_side_ball",
            "trench_top_surface",
            "trench_bottom_floor",
            "ignore",
        ),
        "4.1.1": ("ball_2_gross_ball", "ignore"),
        "4.4.1": ("laser_reference", "ignore"),
        "4.5.1": ("ball_2_top_ball", "ignore"),
        "4.6.2": (
            "ball_2_side_ball",
            "trench_top_surface",
            "trench_bottom_floor",
            "ignore",
        ),
    }

    for capture_id, expected in expected_roles.items():
        options = feature_role_options_for_capture(capture_id)
        roles = tuple(role for role, _label in options)
        labels = tuple(label for _role, label in options if _role != "ignore")
        assert roles == expected
        assert all(role in FEATURE_ROLE_CHOICES for role in roles)
        if capture_id in {"2.6.1", "4.6.2"}:
            assert all("SIDE MIRROR view" in label for label in labels)
            assert all("TOP view" not in label for label in labels)
        else:
            assert all("TOP view" in label for label in labels)
            assert all("SIDE MIRROR view" not in label for label in labels)

        for role in roles:
            assert feature_role_display_label(role, capture_id) == dict(options)[role]


def test_v6_side_review_ui_preloads_ball_two_ruler_lines_and_mirror_roi():
    import tkinter as tk

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")
    root.withdraw()
    try:
        for capture_id, ball_role in (
            ("2.6.1", "ball_1_side_ball"),
            ("4.6.2", "ball_2_side_ball"),
        ):
            baseline = json.loads(
                (
                    V6
                    / "standard_positions_v4"
                    / "vision_baselines"
                    / f"{capture_id}.json"
                ).read_text(encoding="utf-8")
            )
            image_path = (
                ROOT / "Standard position images" / "v4" / "newhead" / f"{capture_id}.PNG"
            )
            lab = VisionRecognitionLab(
                root,
                captured_image_path=image_path,
                initial_session=baseline,
                capture_id=capture_id,
                session_done_callback=lambda: None,
                show_session_done_button=True,
            )
            try:
                lab.update_idletasks()
                selected = [
                    item
                    for values in lab.selected_recognition_payload().values()
                    for item in values
                ]
                roles = {item["feature_role"] for item in selected}

                assert ball_role in roles
                assert "trench_top_surface" in roles
                assert "trench_bottom_floor" in roles
                assert lab.reviewed_mirror_roi_payload() is not None
                displayed_roles = tuple(lab.feature_role_combobox.cget("values"))
                assert all(
                    "SIDE MIRROR view" in label
                    for label in displayed_roles
                    if label != "Ignore this detection"
                )
                assert all("TOP view" not in label for label in displayed_roles)
            finally:
                lab.destroy()
    finally:
        root.destroy()


def test_v6_xseq_files_parse_have_valid_gotos_and_avoid_forbidden_fields():
    assert len(V6_XSEQ_FILES) == 48
    for path in V6_XSEQ_FILES:
        statements = _statements(path)
        label_list = [
            statement.attrib["Label"]
            for statement in statements
            if statement.attrib.get("Label")
        ]
        assert len(label_list) == len(set(label_list)), f"{path}: duplicate labels"
        labels = set(label_list)
        for statement in statements:
            if statement.attrib["Name"] == "Goto":
                target = _params_by_name(statement)["Label"]["StringValue"]
                assert target in labels, f"{path}: missing Goto label {target}"

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


def test_v6_hardcoded_position_sequences_match_v4_targets_settings_and_medium_speed():
    expected_velocity = {
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
    for position in _standard_positions_payload()["positions"]:
        path = _position_sequence_path(position)
        assert path.is_file()
        assert _move_targets(path) == _expected_moves(position)
        analogs = _analog_targets(path)
        assert analogs == {
            "cam_12_ExpTime": _float_string(_setting_value(position, "exposure")),
            "Illu_Coax": "0.9",
            "Illu_1": "0.9",
            "Illu_2": "0.9",
        }
        for statement in _statements(path):
            if statement.attrib["Name"] == "MoveStage":
                params = _params_by_name(statement)
                assert params["Velocity [um/s]"]["VariableName"] == expected_velocity[params["Stage"]["StringValue"]]

    all_text = "\n".join(path.read_text(encoding="ISO-8859-1") for path in V6_XSEQ_FILES)
    for forbidden_velocity in ["VelocityCameraFast", "VelocityCameraXFast", "VelocityAlignFast"]:
        assert forbidden_velocity not in all_text


def test_v6_standard_position_towers_raise_before_lateral_motion_then_lower():
    clearances = _tower_clearance_y_by_tower()

    for position in _standard_positions_payload()["positions"]:
        moves = _move_targets(_position_sequence_path(position))
        for tower, stages in {
            "tower_1": ("Align_Y1", "Align_Z1", "Align_X1"),
            "tower_2": ("Align_Y2", "Align_Z2", "Align_X2"),
        }.items():
            target = ((position.get("machine_positions_um") or {}).get(tower) or {})
            if target.get("y") is None or (target.get("x") is None and target.get("z") is None):
                continue

            stage_y, stage_z, stage_x = stages
            first_y_index = next(index for index, move in enumerate(moves) if move[0] == stage_y)
            lateral_indices = [
                index for index, move in enumerate(moves) if move[0] in {stage_z, stage_x}
            ]
            camera_indices = [
                index for index, move in enumerate(moves) if move[0].startswith("Camera_")
            ]
            clearance = max(clearances[tower], float(target["y"]))

            assert lateral_indices
            assert first_y_index < min(lateral_indices)
            assert first_y_index < min(camera_indices)
            assert moves[first_y_index] == (stage_y, _float_string(clearance))
            if target.get("z") is not None and target.get("x") is not None:
                assert next(
                    index for index, move in enumerate(moves) if move[0] == stage_z
                ) < next(
                    index for index, move in enumerate(moves) if move[0] == stage_x
                )
            if float(target["y"]) < clearance:
                assert moves[-1] == (
                    stage_y,
                    _float_string(target["y"]),
                )


def test_v6_offset_apply_uses_slow_tower_speeds_and_keeps_operator_confirmation():
    path = V6_WORKFLOW_DIR / "SUB_V6ApplyOffsetCorrectionMove_Guarded.xseq"
    text = path.read_text(encoding="ISO-8859-1")
    names = _statement_names(path)
    assert "VelocityAlignXSlow" in text
    assert "VelocityAlignSlow" in text
    assert "VelocityAlignMedium" not in text
    assert "DisplayExtdSelectionDialog" in names
    assert "MoveStage" in names
    assert "Camera_" not in text


def test_v6_capture_queries_all_stages_immediately_before_and_after_grab():
    positions = {position["id"]: position for position in _standard_positions_payload()["positions"]}
    for capture_id in CAPTURE_IDS:
        path = _capture_sequence_path(capture_id)
        names = _statement_names(path)
        strings = "\n".join(_string_values(path))
        grab_index = names.index("Grab")
        gate_index = names.index("DisplayExtdSelectionDialog")
        assert names.count("DisplayExtdSelectionDialog") == 1
        assert names.count("SetAnalogOut") == 4
        assert max(index for index, name in enumerate(names) if name == "SetAnalogOut") < gate_index
        assert names.count("QueryStage") == 20
        assert gate_index < grab_index - 10
        assert names[grab_index - 10 : grab_index] == ["QueryStage"] * 10
        assert names[grab_index + 1 : grab_index + 11] == ["QueryStage"] * 10
        assert names.index("IMAQWriteFile") > grab_index + 10
        assert "MoveStage" not in names
        position = positions[CAPTURE_SPECS[capture_id]["position_id"]]
        assert _analog_targets(path) == {
            "cam_12_ExpTime": _float_string(_setting_value(position, "exposure")),
            "Illu_Coax": "0.9",
            "Illu_1": "0.9",
            "Illu_2": "0.9",
        }
        assert '"schema_version":2' in strings
        assert '"capture_stability_tolerance_um":1.0' in strings
        assert '"source":"reapplied_standard_position_before_operator_gate"' in strings
        assert f'"capture_id":"{capture_id}"' in strings
        tmpython = _tmpython_params(path)
        assert tmpython["Interpreter"]["StringValue"] == "Python_310_PYTHON_AUTOMATION_INTERPRETER"
        assert tmpython["Module"]["StringValue"] == "python_vision_geometry.v6_offset_workflow"
        assert tmpython["Class"]["StringValue"] == "V6VisionReviewRecordStep"


def test_v6_record_capture_preloads_simulator_initial_session(monkeypatch, tmp_path):
    initial_session = _circle_session(123.0, 456.0, role="ball_1_gross_ball")
    seen = {}

    def fake_open_review_ui(
        image_path,
        *,
        capture_id,
        initial_session,
        roi_output_path=None,
        result_output_path=None,
    ):
        seen["image_path"] = image_path
        seen["capture_id"] = capture_id
        seen["initial_session"] = copy.deepcopy(initial_session)
        return copy.deepcopy(initial_session)

    monkeypatch.setattr(v6_module, "open_v6_vision_review_ui", fake_open_review_ui)

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "record_capture",
            "capture_id": "2.1.1",
            "image_path": "standard-example.png",
            "memory_path": str(tmp_path / "memory.json"),
            "machine_positions_before_grab_um": _pose(),
            "machine_positions_after_grab_um": _pose(),
            "initial_review_session": initial_session,
        }
    )

    assert result["ok"] is True
    assert seen == {
        "image_path": "standard-example.png",
        "capture_id": "2.1.1",
        "initial_session": initial_session,
    }


def test_v6_convergence_wrappers_are_independent_and_main_calls_them_once():
    main_names = _statement_names(V6_WORKFLOW_DIR / "SUB_V6MainWorkflow_Guarded.xseq")
    for capture_id in OFFSET_CAPTURE_IDS:
        wrapper = _convergence_sequence_path(capture_id)
        names = _statement_names(wrapper)
        strings = "\n".join(_string_values(wrapper))
        assert names.count(f"SEQ::SUB_V6CaptureReviewRecord_{capture_id}_ReadOnly") == 1
        assert names.count(f"SEQ::SUB_V6OffsetCorrection_{capture_id}_Guarded") == 1
        assert "9.0" in strings
        assert "L_CaptureLoop" in strings
        assert main_names.count(f"SEQ::SUB_V6Converge_{capture_id}_Guarded") == 1
        assert f"SEQ::SUB_V6CaptureReviewRecord_{capture_id}_ReadOnly" not in main_names
        assert f"SEQ::SUB_V6OffsetCorrection_{capture_id}_Guarded" not in main_names

    for capture_id in ("2.4.1", "4.4.1"):
        assert main_names.count(f"SEQ::SUB_V6CaptureReviewRecord_{capture_id}_ReadOnly") == 1
    assert main_names.count("SEQ::SUB_V6FinalVerification_ReadOnly") == 1


def test_v6_final_verification_yase_is_read_only():
    path = V6_WORKFLOW_DIR / "SUB_V6FinalVerification_ReadOnly.xseq"
    names = _statement_names(path)
    strings = "\n".join(_string_values(path))
    assert "TMPython_ExecuteScript" in names
    assert "MoveStage" not in names
    assert "SetAnalogOut" not in names
    assert '"command":"verify_final_geometry"' in strings
    assert "final_geometry_verified" in strings


def test_v6_editor_saved_metadata_survives_semantic_generation():
    for name in [
        "SUB_V6ApplyApproachMove_Guarded.xseq",
        "SUB_V6ApplyOffsetCorrectionMove_Guarded.xseq",
        "SUB_V6MainWorkflow_Guarded.xseq",
        "SUB_V6SequenceMemoryInit_ReadOnly.xseq",
    ]:
        text = (V6_WORKFLOW_DIR / name).read_text(encoding="ISO-8859-1")
        assert "<Editor-Version>" in text
        assert "Completed V6 reviewed vision alignment workflow" in text
        assert 'Description="Enter comment text."' in text
        assert 'Name="EndSeq" Library="Standard"' in text


def test_v6_coarse_top_maps_image_right_to_machine_x_and_image_up_to_machine_z():
    standard = _circle_session(100.0, 200.0, role="ball_1_gross_ball")
    live = _circle_session(110.0, 190.0, role="ball_1_gross_ball")
    memory = _memory_with_records(
        standard_baselines={"2.1.1": standard},
        records={"2.1.1": _record("2.1.1", live)},
    )
    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory": memory,
            "machine_positions_um": _legacy_pose(),
        }
    )
    assert result["ok"] is True
    assert result["schema_version"] == 2
    assert result["stage1"] == "Align_Z1"
    assert result["delta1_um"] == pytest.approx(-50.0)
    assert result["stage2"] == "Align_X1"
    assert result["delta2_um"] == pytest.approx(-50.0)
    mapping = result["diagnostics"]["correction"]["view_mapping"]
    assert mapping["image_right"] == {"machine_axis": "machine_x_um", "sign": 1.0}
    assert mapping["image_up"] == {"machine_axis": "machine_z_um", "sign": 1.0}


def test_v6_lateral_correction_fails_closed_below_reviewed_clearance_height():
    low_pose = _pose()
    low_pose["tower_1"]["machine_y_um"] = 12000.0
    standard = _circle_session(100.0, 200.0, role="ball_1_gross_ball")
    live = _circle_session(110.0, 200.0, role="ball_1_gross_ball")
    memory = _memory_with_records(
        standard_baselines={"2.1.1": standard},
        records={"2.1.1": _record("2.1.1", live, pose=low_pose)},
    )

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory": memory,
            "machine_positions_um": low_pose,
        }
    )

    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "below its reviewed safe-height boundary" in result["status"]


def test_v6_ball_1_motion_fails_closed_when_ball_2_records_are_still_active():
    standard = _circle_session(100.0, 200.0, role="ball_1_gross_ball")
    live = _circle_session(110.0, 200.0, role="ball_1_gross_ball")
    memory = _memory_with_records(
        standard_baselines={"2.1.1": standard},
        records={
            "2.1.1": _record("2.1.1", live),
            "4.6.2": _record(
                "4.6.2",
                _side_session(900.0, target="ball_2"),
            ),
        },
    )

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory": memory,
            "machine_positions_um": _pose(),
        }
    )

    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "cannot re-enter ball_1 motion" in result["status"]


def test_v6_fine_top_targets_289_and_compensates_recorded_camera_xz_change():
    reference_pose = _pose(camera_x=-38000.0, camera_z=-93000.0)
    ball_pose = _pose(camera_x=-37990.0, camera_z=-92980.0)
    memory = _memory_with_records(
        records={
            "2.4.1": _record("2.4.1", _rectangle_session(), pose=reference_pose),
            "2.5.1": _record(
                "2.5.1",
                _circle_session(112.0, 44.0, role="ball_1_top_ball"),
                pose=ball_pose,
            ),
        }
    )
    _satisfy_correction_prerequisites(memory, "2.5.1")
    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.5.1",
            "memory": memory,
            "machine_positions_um": ball_pose,
        }
    )
    correction = result["diagnostics"]["correction"]
    assert correction["camera_compensation_um"] == {
        "machine_x_um": pytest.approx(10.0),
        "machine_z_um": pytest.approx(20.0),
    }
    assert correction["measured_machine_x_um"] == pytest.approx(70.0)
    assert correction["measured_machine_z_um"] == pytest.approx(50.0)
    assert correction["target_coordinates_um"] == {"machine_x_um": 289.0, "machine_z_um": 0.0}
    assert result["stage1"] == "Align_Z1"
    assert result["delta1_um"] == pytest.approx(-50.0)
    assert result["stage2"] == "Align_X1"
    assert result["delta2_um"] == pytest.approx(150.0)


def test_v6_ball_2_fine_correction_selects_axis_order_with_more_ball_clearance():
    memory, ball_pose = _ball_2_fine_memory(
        measured_machine_x_um=289.0,
        measured_machine_z_um=800.0,
    )

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "4.5.1",
            "memory": memory,
            "machine_positions_um": ball_pose,
        }
    )

    assert result["ok"] is True
    assert result["stage1"] == "Align_X2"
    assert result["stage2"] == "Align_Z2"
    path = result["diagnostics"]["collision_path"]
    assert path["required"] is True
    assert path["selected_move_order"] == ["Align_X2", "Align_Z2"]
    assert path["selected_path"]["minimum_surface_gap_um"] > 0.0


def test_v6_ball_2_fine_correction_rejects_a_path_starting_in_ball_overlap():
    memory, ball_pose = _ball_2_fine_memory(
        measured_machine_x_um=289.0,
        measured_machine_z_um=400.0,
    )

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "4.5.1",
            "memory": memory,
            "machine_positions_um": ball_pose,
        }
    )

    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "no strict ball-to-ball-safe axis order" in result["status"]


def test_v6_fine_top_fails_closed_when_zoom_context_differs():
    reference_pose = _pose(zoom=4500.0)
    ball_pose = _pose(zoom=4510.0)
    memory = _memory_with_records(
        records={
            "2.4.1": _record("2.4.1", _rectangle_session(), pose=reference_pose),
            "2.5.1": _record("2.5.1", _circle_session(100.0, 50.0, role="ball_1_top_ball"), pose=ball_pose),
        }
    )
    _satisfy_correction_prerequisites(memory, "2.5.1")
    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.5.1",
            "memory": memory,
            "machine_positions_um": ball_pose,
        }
    )
    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "zoom mismatch" in result["status"]


def test_v6_fine_top_fails_closed_when_image_dimensions_are_unknown():
    reference = _record("2.4.1", _rectangle_session())
    ball = _record("2.5.1", _circle_session(100.0, 50.0, role="ball_1_top_ball"))
    reference["image_dimensions_px"] = {}
    memory = _memory_with_records(records={"2.4.1": reference, "2.5.1": ball})
    _satisfy_correction_prerequisites(memory, "2.5.1")

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.5.1",
            "memory": memory,
            "machine_positions_um": _pose(),
        }
    )
    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "missing image_width_px" in result["status"]


def test_v6_side_uses_two_line_300_um_scale_and_bottom_mirror_inversion():
    session = _side_session(910.0)
    geometry = reviewed_trench_geometry(session, capture_id="2.6.1")
    assert geometry["line_separation_px"] == pytest.approx(250.0)
    assert geometry["um_per_pixel"] == pytest.approx(1.2)
    assert geometry["trench_floor_transform"]["mirror_flipped"]["image_y_px"] == pytest.approx(350.0)
    assert geometry["trench_top_transform"]["mirror_flipped"]["image_y_px"] == pytest.approx(100.0)

    memory = _memory_with_records(records={"2.6.1": _record("2.6.1", session)})
    _satisfy_correction_prerequisites(memory, "2.6.1")
    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.6.1",
            "memory": memory,
            "machine_positions_um": _pose(),
        }
    )
    correction = result["diagnostics"]["correction"]
    assert correction["residual_flipped_y_px"] == pytest.approx(-10.0)
    assert correction["measured_machine_y_um"] == pytest.approx(12.0)
    assert result["stage1"] == "Align_Y1"
    assert result["delta1_um"] == pytest.approx(-12.0)


def test_v6_side_insertion_fails_closed_before_top_alignment_and_transition():
    memory = _memory_with_records(
        records={"2.6.1": _record("2.6.1", _side_session(910.0))}
    )

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.6.1",
            "memory": memory,
            "machine_positions_um": _pose(),
        }
    )

    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "reviewed capture record for 2.5.1" in result["status"]


def test_v6_side_mirror_transform_uses_roi_local_coordinates_before_flip():
    transform = mirror_ball_reference_delta_px(
        ball_y_px=600.0,
        reference_y_px=900.0,
        mirror_roi={"x1": 0.0, "y1": 500.0, "x2": 1000.0, "y2": 1000.0},
    )
    assert transform["mirror_local"] == {"ball_y_px": 100.0, "reference_y_px": 400.0}
    assert transform["mirror_flipped"] == {"ball_y_px": 400.0, "reference_y_px": 100.0}
    assert transform["flipped_delta_y_px"] == pytest.approx(300.0)


@pytest.mark.parametrize(
    ("mutator", "expected"),
    [
        (lambda session: session.pop("mirror_roi"), "mirror_roi"),
        (
            lambda session: session["selected_recognition"]["roi_1"].pop(),
            "trench_top_surface",
        ),
        (
            lambda session: session["selected_recognition"]["roi_1"].append(
                {
                    "shape_kind": "line",
                    "feature_role": "trench_top_surface",
                    "shape": {"x1": 100.0, "y1": 880.0, "x2": 900.0, "y2": 880.0},
                }
            ),
            "exactly one line",
        ),
    ],
)
def test_v6_side_missing_or_ambiguous_features_fail_closed(mutator, expected):
    session = _side_session(910.0)
    mutator(session)
    memory = _memory_with_records(records={"2.6.1": _record("2.6.1", session)})
    _satisfy_correction_prerequisites(memory, "2.6.1")
    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.6.1",
            "memory": memory,
            "machine_positions_um": _pose(),
        }
    )
    assert result["ok"] is False
    assert result["move_count"] == 0
    assert expected in result["status"]


def test_v6_side_reversed_and_implausible_lines_fail_closed():
    reversed_session = _side_session(700.0, top_y=650.0, floor_y=900.0)
    with pytest.raises(ValueError, match="reversed side features"):
        reviewed_trench_geometry(reversed_session, capture_id="2.6.1")
    close_session = _side_session(700.0, top_y=655.0, floor_y=650.0)
    with pytest.raises(ValueError, match="implausible trench-line separation"):
        reviewed_trench_geometry(close_session, capture_id="2.6.1")


def test_v6_actual_side_images_propose_two_trench_ruler_lines():
    image_root = ROOT / "Standard position images" / "v4" / "newhead"
    roi = (VisionROI("edges", 50.0, 220.0, 2540.0, 1300.0),)
    expected = {"2.6.1.PNG": (362.0, 531.0), "4.6.2.PNG": (391.0, 752.0)}
    for image_name, expected_y in expected.items():
        lines = detect_side_trench_ruler_lines(read_grayscale_image(image_root / image_name), roi)
        assert len(lines) == 2
        assert (lines[0].y1, lines[1].y1) == pytest.approx(expected_y)
        assert "floor" in lines[0].label
        assert "top" in lines[1].label


def test_v6_bounded_step_clamps_residual_but_total_bound_fails_closed():
    standard = _circle_session(100.0, 200.0, role="ball_1_gross_ball")
    live = _circle_session(140.0, 200.0, role="ball_1_gross_ball")
    memory = _memory_with_records(
        standard_baselines={"2.1.1": standard},
        records={"2.1.1": _record("2.1.1", live)},
    )
    bounded = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory": memory,
            "machine_positions_um": _pose(),
            "max_step_um": 50.0,
        }
    )
    assert bounded["ok"] is True
    assert bounded["delta1_um"] == pytest.approx(-50.0)

    rejected = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory": memory,
            "machine_positions_um": _pose(),
            "max_total_residual_um": 100.0,
        }
    )
    assert rejected["ok"] is False
    assert rejected["move_count"] == 0
    assert "max_total_residual_um" in rejected["status"]


def test_v6_capture_stability_rejects_motion_and_does_not_update_memory(tmp_path):
    memory_path = tmp_path / "memory.json"
    run_v6_vision_workflow(
        {"schema_version": SCHEMA_VERSION, "command": "init", "memory_path": str(memory_path)}
    )
    before = _pose()
    after = _pose(camera_x=-37995.0)
    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "record_capture",
            "capture_id": "2.1.1",
            "image_path": "not-needed-for-supplied-review.bmp",
            "memory_path": str(memory_path),
            "machine_positions_before_grab_um": before,
            "machine_positions_after_grab_um": after,
            "review_session": _circle_session(100.0, 100.0, role="ball_1_gross_ball"),
        }
    )
    assert result["ok"] is False
    assert "capture rejected" in result["status"]
    assert json.loads(memory_path.read_text(encoding="utf-8"))["capture_records"] == {}


def test_v6_correction_rejects_a_record_without_exact_capture_pose():
    standard = _circle_session(100.0, 100.0, role="ball_1_gross_ball")
    record = _record(
        "2.1.1",
        _circle_session(110.0, 100.0, role="ball_1_gross_ball"),
    )
    record.pop("machine_positions_um")
    memory = _memory_with_records(
        standard_baselines={"2.1.1": standard},
        records={"2.1.1": record},
    )

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory": memory,
            "machine_positions_um": _pose(),
        }
    )

    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "missing its exact machine_positions_um" in result["status"]


def test_v6_cancelled_review_does_not_update_memory(tmp_path):
    memory_path = tmp_path / "memory.json"
    run_v6_vision_workflow(
        {"schema_version": SCHEMA_VERSION, "command": "init", "memory_path": str(memory_path)}
    )
    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "record_capture",
            "capture_id": "2.1.1",
            "image_path": "cancelled-review.bmp",
            "memory_path": str(memory_path),
            "machine_positions_before_grab_um": _pose(),
            "machine_positions_after_grab_um": _pose(),
            "review_session": {
                "ok": False,
                "action": "vision_lab_cancelled",
                "selected_recognition": {},
            },
        }
    )
    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "operator cancelled review" in result["status"]
    assert json.loads(memory_path.read_text(encoding="utf-8"))["capture_records"] == {}


def test_v6_rerecord_retains_history_and_invalidates_dependent_plans(tmp_path):
    memory_path = tmp_path / "memory.json"
    run_v6_vision_workflow(
        {"schema_version": SCHEMA_VERSION, "command": "init", "memory_path": str(memory_path)}
    )
    request = {
        "schema_version": SCHEMA_VERSION,
        "command": "record_capture",
        "capture_id": "2.1.1",
        "image_path": "supplied-review.bmp",
        "memory_path": str(memory_path),
        "machine_positions_before_grab_um": _pose(),
        "machine_positions_after_grab_um": _pose(),
        "review_session": _circle_session(100.0, 100.0, role="ball_1_gross_ball"),
    }
    first = run_v6_vision_workflow(request)
    assert first["revision"] == 1
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    memory["correction_plans"]["2.1.1:r1"] = {
        "capture_id": "2.1.1",
        "status": "active",
    }
    memory["transition_records"]["2.1_to_2.4"] = {
        "from_position_id": "2.1",
        "status": "in_progress",
    }
    memory_path.write_text(json.dumps(memory), encoding="utf-8")

    second = run_v6_vision_workflow(
        {
            **request,
            "review_session": _circle_session(101.0, 100.0, role="ball_1_gross_ball"),
        }
    )
    assert second["revision"] == 2
    assert set(second["invalidated_plan_ids"]) == {"2.1.1:r1", "transition:2.1_to_2.4"}
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    assert len(memory["capture_history"]["2.1.1"]) == 1
    assert memory["capture_history"]["2.1.1"][0]["revision"] == 1
    assert memory["capture_records"]["2.1.1"]["revision"] == 2
    selected = memory["capture_records"]["2.1.1"]["session"]["selected_recognition"]["roi_1"][0]
    assert selected["shape"]["x"] == pytest.approx(101.0)
    assert selected["feature_role"] == "ball_1_gross_ball"
    assert memory["correction_plans"]["2.1.1:r1"]["status"] == "invalidated"
    assert "2.1_to_2.4" not in memory["transition_records"]


def test_v6_rerecording_reference_invalidates_all_downstream_convergence_and_transitions(
    tmp_path,
):
    memory_path = tmp_path / "memory.json"
    memory = _memory_with_records(records=_valid_workflow_records())
    _complete_workflow_state(memory)
    memory["transition_records"]["2.1_to_2.4"]["from_position_id"] = "2.1"
    memory_path.write_text(json.dumps(memory), encoding="utf-8")

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "record_capture",
            "capture_id": "2.4.1",
            "image_path": "supplied-review.bmp",
            "memory_path": str(memory_path),
            "machine_positions_before_grab_um": _pose(),
            "machine_positions_after_grab_um": _pose(),
            "review_session": _rectangle_session(center_x=101.0),
        }
    )

    assert result["ok"] is True
    persisted = json.loads(memory_path.read_text(encoding="utf-8"))
    assert persisted["convergence"]["2.1.1"]["status"] == "converged"
    for capture_id in ("2.5.1", "2.6.1", "4.1.1", "4.5.1", "4.6.2"):
        state = persisted["convergence"][capture_id]
        assert state["status"] == "invalidated"
        assert state["invalidated_by_capture_id"] == "2.4.1"
    assert set(persisted["transition_records"]) == {"2.1_to_2.4"}
    assert {
        "transition:2.4_to_2.5",
        "transition:2.5_to_2.6",
        "transition:4.1_to_4.4",
        "transition:4.4_to_4.5",
        "transition:4.5_to_4.6.2",
    }.issubset(set(result["invalidated_plan_ids"]))


def test_v6_fresh_capture_preserves_current_move_state_for_divergence_check(tmp_path):
    memory_path = tmp_path / "memory.json"
    standard = _circle_session(100.0, 100.0, role="ball_1_gross_ball")
    memory = _memory_with_records(
        standard_baselines={"2.1.1": standard},
        records={
            "2.1.1": _record(
                "2.1.1",
                _circle_session(110.0, 100.0, role="ball_1_gross_ball"),
            )
        },
    )
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    first = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory_path": str(memory_path),
            "machine_positions_um": _pose(),
        }
    )
    assert first["ok"] is True

    rerecorded = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "record_capture",
            "capture_id": "2.1.1",
            "image_path": "supplied-review.bmp",
            "memory_path": str(memory_path),
            "machine_positions_before_grab_um": _pose(),
            "machine_positions_after_grab_um": _pose(),
            "review_session": _circle_session(
                120.0,
                100.0,
                role="ball_1_gross_ball",
            ),
        }
    )
    assert rerecorded["ok"] is True
    persisted = json.loads(memory_path.read_text(encoding="utf-8"))
    assert persisted["convergence"]["2.1.1"]["status"] == "move_planned"
    assert persisted["convergence"]["2.1.1"]["capture_revision"] == 1

    second = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory_path": str(memory_path),
            "machine_positions_um": _pose(),
        }
    )
    assert second["ok"] is False
    assert "residual increased" in second["status"]


def test_v6_convergence_aborts_when_residual_increases_after_move(tmp_path):
    memory_path = tmp_path / "memory.json"
    standard = _circle_session(100.0, 100.0, role="ball_1_gross_ball")
    memory = _memory_with_records(
        standard_baselines={"2.1.1": standard},
        records={
            "2.1.1": _record(
                "2.1.1",
                _circle_session(110.0, 100.0, role="ball_1_gross_ball"),
                revision=1,
            )
        },
    )
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    first = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory_path": str(memory_path),
            "machine_positions_um": _pose(),
        }
    )
    assert first["ok"] is True
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    memory["capture_records"]["2.1.1"] = _record(
        "2.1.1",
        _circle_session(120.0, 100.0, role="ball_1_gross_ball"),
        revision=2,
    )
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    second = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory_path": str(memory_path),
            "machine_positions_um": _pose(),
        }
    )
    assert second["ok"] is False
    assert second["move_count"] == 0
    assert "residual increased" in second["status"]


def test_v6_convergence_aborts_when_one_axis_worsens_while_max_residual_drops(tmp_path):
    memory_path = tmp_path / "memory.json"
    standard = _circle_session(100.0, 100.0, role="ball_1_gross_ball")
    memory = _memory_with_records(
        standard_baselines={"2.1.1": standard},
        records={
            "2.1.1": _record(
                "2.1.1",
                _circle_session(120.0, 110.0, role="ball_1_gross_ball"),
                revision=1,
            )
        },
    )
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    first = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory_path": str(memory_path),
            "machine_positions_um": _pose(),
        }
    )
    assert first["ok"] is True
    assert first["diagnostics"]["convergence"]["max_abs_residual_um"] == pytest.approx(100.0)

    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    memory["capture_records"]["2.1.1"] = _record(
        "2.1.1",
        _circle_session(115.0, 112.0, role="ball_1_gross_ball"),
        revision=2,
    )
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    second = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory_path": str(memory_path),
            "machine_positions_um": _pose(),
        }
    )

    assert second["ok"] is False
    assert second["move_count"] == 0
    assert "machine_z_um" in second["status"]
    state = json.loads(memory_path.read_text(encoding="utf-8"))["convergence"]["2.1.1"]
    assert state["max_abs_residual_um"] == pytest.approx(75.0)
    assert state["diverged_axes"] == ["machine_z_um"]


def test_v6_final_fresh_check_never_plans_a_ninth_correction(tmp_path):
    memory_path = tmp_path / "memory.json"
    standard = _circle_session(100.0, 100.0, role="ball_1_gross_ball")
    memory = _memory_with_records(
        standard_baselines={"2.1.1": standard},
        records={
            "2.1.1": _record(
                "2.1.1",
                _circle_session(105.0, 100.0, role="ball_1_gross_ball"),
                revision=9,
            )
        },
    )
    memory["convergence"]["2.1.1"] = {
        "capture_id": "2.1.1",
        "capture_revision": 8,
        "attempt_count": 8,
        "max_attempts": 8,
        "max_abs_residual_um": 50.0,
        "status": "move_planned",
    }
    memory_path.write_text(json.dumps(memory), encoding="utf-8")

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_offset_correction",
            "capture_id": "2.1.1",
            "memory_path": str(memory_path),
            "machine_positions_um": _pose(),
        }
    )
    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "after 8 reviewed correction attempts" in result["status"]
    persisted = json.loads(memory_path.read_text(encoding="utf-8"))
    assert persisted["convergence"]["2.1.1"]["status"] == "max_attempts_exhausted"
    assert persisted["convergence"]["2.1.1"]["attempt_count"] == 8


def test_v6_transition_rebases_standard_delta_and_outputs_canonical_axes():
    current = _pose()
    current["tower_1"] = {
        "machine_x_um": 5000.0,
        "machine_y_um": 12000.0,
        "machine_z_um": 15000.0,
    }
    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_transition_move",
            "transition_id": "2.1_to_2.4",
            "standard_positions": _standard_positions_payload(),
            "machine_positions_um": current,
            "move_tolerance_um": 0.0,
        }
    )
    assert result["ok"] is True
    targets = result["target_positions_um"]
    assert targets["camera"] == {
        "machine_x_um": pytest.approx(-38000.0),
        "machine_y_um": pytest.approx(-44399.0),
        "machine_z_um": pytest.approx(-89301.0),
    }
    assert targets["tower_1"] == {
        "machine_x_um": pytest.approx(5000.0),
        "machine_y_um": pytest.approx(11001.0),
        "machine_z_um": pytest.approx(17200.0),
    }
    assert result["stage1"] == "Align_Y1"
    assert result["target1_um"] == pytest.approx(17000.0)


def test_v6_top_to_side_transition_rejects_nonpositive_insertion_clearance():
    memory = _memory_with_records(
        records={
            "2.4.1": _record("2.4.1", _rectangle_session()),
            "2.5.1": _record(
                "2.5.1",
                _circle_session(100.0, 50.0, role="ball_1_top_ball"),
            ),
        }
    )
    _mark_converged(memory, "2.5.1")

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "next_transition_move",
            "transition_id": "2.5_to_2.6",
            "memory": memory,
            "machine_positions_um": _pose(),
        }
    )

    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "side insertion does not preserve strict" in result["status"]


def test_v6_yase_transition_requires_current_converged_source_capture(tmp_path):
    memory_path = tmp_path / "memory.json"
    run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "init",
            "memory_path": str(memory_path),
            "standard_positions_path": str(V6_STANDARD_POSITIONS),
        }
    )
    request = {
        "schema_version": SCHEMA_VERSION,
        "command": "next_transition_move",
        "transition_id": "2.1_to_2.4",
        "standard_positions": _standard_positions_payload(),
        "memory_path": str(memory_path),
        "machine_positions_um": _pose(),
    }
    missing = run_v6_vision_workflow(request)
    assert missing["ok"] is False
    assert missing["move_count"] == 0
    assert "reviewed capture record for 2.1.1" in missing["status"]

    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    memory["capture_records"]["2.1.1"] = _record(
        "2.1.1",
        _circle_session(100.0, 100.0, role="ball_1_gross_ball"),
    )
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    unconverged = run_v6_vision_workflow(request)
    assert unconverged["ok"] is False
    assert "requires capture 2.1.1 revision 1 to be converged" in unconverged["status"]

    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    memory["convergence"]["2.1.1"] = {
        "capture_id": "2.1.1",
        "capture_revision": 1,
        "status": "converged",
    }
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    ready = run_v6_vision_workflow(request)
    assert ready["ok"] is True
    assert ready["action"] == "transition_move"


def test_v6_transition_anchor_remains_fixed_across_loop_iterations(tmp_path):
    memory_path = tmp_path / "memory.json"
    run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "init",
            "memory_path": str(memory_path),
            "standard_positions_path": str(V6_STANDARD_POSITIONS),
        }
    )
    current = _pose()
    current["tower_1"] = {
        "machine_x_um": 5000.0,
        "machine_y_um": 17000.0,
        "machine_z_um": 15000.0,
    }
    memory = json.loads(memory_path.read_text(encoding="utf-8"))
    memory["capture_records"]["2.1.1"] = _record(
        "2.1.1",
        _circle_session(100.0, 100.0, role="ball_1_gross_ball"),
        pose=current,
    )
    memory["convergence"]["2.1.1"] = {
        "capture_id": "2.1.1",
        "capture_revision": 1,
        "status": "converged",
    }
    memory_path.write_text(json.dumps(memory), encoding="utf-8")
    request = {
        "schema_version": SCHEMA_VERSION,
        "command": "next_transition_move",
        "transition_id": "2.1_to_2.4",
        "standard_positions": _standard_positions_payload(),
        "memory_path": str(memory_path),
        "machine_positions_um": current,
        "move_tolerance_um": 0.0,
    }
    first = run_v6_vision_workflow(request)
    assert first["stage1"] == "Camera_Z"
    after = copy.deepcopy(current)
    after["camera"]["machine_z_um"] = first["target1_um"]
    second = run_v6_vision_workflow({**request, "machine_positions_um": after})
    assert second["target_positions_um"] == first["target_positions_um"]
    assert second["transition_anchor_positions_um"] == first["transition_anchor_positions_um"]
    assert second["stage1"] == "Zoom"


def test_v6_final_read_only_verification_proves_289_989_and_700():
    top_scale_px_per_um = 1.0 / 5.0
    records = {
        "2.4.1": _record("2.4.1", _rectangle_session()),
        "2.5.1": _record(
            "2.5.1",
            _circle_session(100.0 + 289.0 * top_scale_px_per_um, 50.0, role="ball_1_top_ball"),
        ),
        "2.6.1": _record("2.6.1", _side_session(900.0)),
        "4.4.1": _record("4.4.1", _rectangle_session()),
        "4.5.1": _record(
            "4.5.1",
            _circle_session(100.0 + 989.0 * top_scale_px_per_um, 50.0, role="ball_2_top_ball"),
        ),
        "4.6.2": _record("4.6.2", _side_session(900.0, target="ball_2")),
    }
    memory = _memory_with_records(records=records)
    _complete_workflow_state(memory)
    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "verify_final_geometry",
            "memory": memory,
        }
    )
    assert result["ok"] is True
    assert result["action"] == "final_geometry_verified"
    assert result["read_only"] is True
    assert result["move_count"] == 0
    assert result["target_coordinates_um"] == FINAL_TARGETS_UM
    assert result["measured_coordinates_um"]["ball_1"] == pytest.approx(FINAL_TARGETS_UM["ball_1"])
    assert result["measured_coordinates_um"]["ball_2"] == pytest.approx(FINAL_TARGETS_UM["ball_2"])
    assert result["measured_center_spacing_um"] == pytest.approx(FINAL_CENTER_SPACING_UM)
    assert result["collision_clearance"]["axial_surface_gaps_um"] == pytest.approx(
        {
            "source_to_ball_1_surface_gap_um": 39.0,
            "ball_1_to_ball_2_surface_gap_um": 200.0,
            "ball_2_to_taper_surface_gap_um": 39.0,
        }
    )
    assert result["collision_clearance"]["trench_floor_surface_gaps_um"] == pytest.approx(
        {"ball_1": 50.0, "ball_2": 50.0}
    )


def test_v6_final_layout_requires_strict_positive_surface_gaps():
    clearance = final_layout_clearance(
        {
            "ball_1": {
                "machine_x_um": 289.0,
                "machine_y_um": 0.0,
                "machine_z_um": 0.0,
            },
            "ball_2": {
                "machine_x_um": 789.0,
                "machine_y_um": 0.0,
                "machine_z_um": 0.0,
            },
        }
    )

    assert clearance["strictly_clear"] is False
    assert clearance["axial_surface_gaps_um"]["ball_1_to_ball_2_surface_gap_um"] == 0.0
    assert clearance["minimum_checked_surface_gap_um"] == 0.0


def test_v6_final_verification_fails_closed_without_completed_workflow_state():
    memory = _memory_with_records(records=_valid_workflow_records())

    result = run_v6_vision_workflow(
        {
            "schema_version": SCHEMA_VERSION,
            "command": "verify_final_geometry",
            "memory": memory,
        }
    )

    assert result["ok"] is False
    assert result["move_count"] == 0
    assert "to be converged before continuing" in result["status"]
