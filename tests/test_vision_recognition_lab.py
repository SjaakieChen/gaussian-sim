import json
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

from vision_recognition_lab import (
    BRIGHT_RECTANGLE_OVERLAY_COLOR,
    DEFAULT_STANDARD_BATCH_NAMES,
    EDGE_RECTANGLE_OVERLAY_COLOR,
    FIXED_MEASUREMENT_SHORT_EDGE_LENGTH_UM,
    OFFICIAL_BASELINE_FOLDER_NAME,
    ROI_REQUIRED_MESSAGE,
    VISION_SCORE_FOLDER_NAME,
    V5_SEQUENCE_MEMORY_FILE_NAME,
    V5_SEQUENCE_MEMORY_FOLDER_NAME,
    VisionCircle,
    VisionCircleReference,
    VisionRectangle,
    VisionROI,
    VisionRecognitionLab,
    VisionRecognitionLabStep,
    VisionRecognitionResult,
    VisionSilhouette,
    VISION_RECOGNITION_LAB_TITLE,
    bright_rectangle_sensitivity_from_scale_x,
    default_feature_role_for_selection,
    downsample_for_recognition,
    fit_subsample_factor,
    geometry_sensitivity_from_scale_x,
    load_standard_position_library,
    normalize_standard_position_id,
    parse_short_edge_length_um,
    rectangle_circle_measurement,
    relative_measurement_payload_from_measurements,
    recognize_shapes,
    selected_rectangle_short_edge,
    silhouette_sensitivity_from_scale_x,
    standard_position_sort_key,
    vision_roi_to_dict,
    vision_session_payload,
)


ROOT = Path(__file__).resolve().parents[1]
STANDARD_POSITION_IMAGE_ROOT = ROOT / "Standard position images"
STANDARD_POSITIONS = STANDARD_POSITION_IMAGE_ROOT / "v4" / "standard_positions.json"


def _make_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")
    root.withdraw()
    return root


def test_standard_position_json_uses_semantic_ids_and_matching_image_names():
    data = json.loads(STANDARD_POSITIONS.read_text(encoding="utf-8"))

    position_ids = [position["id"] for position in data["positions"]]
    assert "1.1" in position_ids
    assert "4.6.2" in position_ids
    assert {
        position["id"]: position["captured_images"]
        for position in data["positions"]
        if position["captured_images"]
    } == {
        "1.1": ["newhead/1.1.1.PNG"],
        "2.1": ["newhead/2.1.1.PNG"],
        "2.2": ["newhead/2.2.1.PNG"],
        "2.3": ["newhead/2.3.1.PNG"],
        "2.4": ["newhead/2.4.1.PNG"],
        "2.5": ["newhead/2.5.1.PNG"],
        "2.6": ["newhead/2.6.1.PNG"],
        "3.0": ["newhead/3.0.1.PNG"],
        "4.1": ["newhead/4.1.1.PNG"],
        "4.2": ["newhead/4.2.1.PNG"],
        "4.3": ["newhead/4.3.1.PNG"],
        "4.4": ["newhead/4.4.1.PNG"],
        "4.5": ["newhead/4.5.1.PNG"],
        "4.6.1": ["newhead/4.6.1.PNG"],
        "4.6.2": ["newhead/4.6.2.PNG"],
    }


def test_standard_position_library_groups_images_by_position_across_batches():
    library = load_standard_position_library(STANDARD_POSITION_IMAGE_ROOT)

    assert DEFAULT_STANDARD_BATCH_NAMES == ("v4",)
    position_ids = [position.id for position in library.positions]
    assert "6.0.0" not in position_ids
    assert {image.batch for image in library.images} == {"v4"}
    for expected_position_id in [
        "1.1",
        "2.1",
        "3.0",
        "4.6.2",
    ]:
        assert expected_position_id in position_ids
    assert library.images_for_position("1.0") == ()
    position_images = library.images_for_position("3.0")
    assert [(image.batch, image.path.name) for image in position_images] == [("v4", "3.0.1.PNG")]


def test_standard_position_library_loads_v4_captured_images_list():
    library = load_standard_position_library(STANDARD_POSITION_IMAGE_ROOT)

    position_images = library.images_for_position("1.1")
    assert [(image.batch, image.path.as_posix().replace("\\", "/").split("Standard position images/")[-1]) for image in position_images] == [
        ("v4", "v4/newhead/1.1.1.PNG")
    ]
    assert [(image.batch, image.path.name) for image in library.images_for_position("4.6.2")] == [
        ("v4", "4.6.2.PNG")
    ]


def test_standard_position_library_attaches_machine_coordinates_to_v4_images():
    library = load_standard_position_library(STANDARD_POSITION_IMAGE_ROOT)

    image = library.images_for_position("2.4")[0]

    assert image.standard_positions_path == STANDARD_POSITIONS
    assert image.machine_positions_um["camera"]["x"] == -38997
    assert image.machine_positions_um["camera"]["y"] == -45395
    assert image.machine_positions_um["tower_1"]["z"] == 15198
    assert image.camera_settings["zoom"]["value"] == 4500


def test_standard_position_id_helpers_normalize_legacy_three_digit_inputs():
    assert normalize_standard_position_id(f"{3:03d}") == "3.0.0"
    assert sorted(["10.0.0", "2.0.0", "1.1.0"], key=standard_position_sort_key) == [
        "1.1.0",
        "2.0.0",
        "10.0.0",
    ]


def test_image_fit_subsample_factor_keeps_full_frame_visible():
    assert fit_subsample_factor(2592, 1944, 900, 600) == 4
    assert fit_subsample_factor(640, 480, 900, 600) == 1


def test_shape_recognition_uses_full_resolution_image():
    import numpy as np

    image = np.zeros((1944, 2592), dtype=float)

    analysis, scale = downsample_for_recognition(image)

    assert analysis is image
    assert analysis.shape == image.shape
    assert scale == 1


def test_default_feature_roles_match_v5_sequence_memory_contract():
    assert default_feature_role_for_selection("rectangle", "rectangle") == "laser_reference"
    assert default_feature_role_for_selection("circle", "circle") == "ball_candidate"
    assert default_feature_role_for_selection("silhouette", "silhouette_circle") == "ball_candidate"
    assert default_feature_role_for_selection("line", "line") == "side_reference"


def test_shape_recognition_detects_lines_intersections_and_circles():
    import numpy as np

    image = np.zeros((140, 180), dtype=float)
    image[35, 20:130] = 1.0
    image[15:100, 80] = 1.0
    yy, xx = np.ogrid[:140, :180]
    circle = np.abs((xx - 135) ** 2 + (yy - 95) ** 2 - 18**2) <= 18
    image[circle] = 1.0

    result = recognize_shapes(image, "bright_threshold")

    assert len(result.lines) >= 2
    assert any(abs(intersection.x - 80) <= 3 and abs(intersection.y - 35) <= 3 for intersection in result.intersections)
    assert any(abs(circle.x - 135) <= 4 and abs(circle.y - 95) <= 4 for circle in result.circles)


def test_shape_recognition_limits_lines_and_intersections_to_box_roi():
    import numpy as np

    image = np.zeros((160, 220), dtype=float)
    image[40, 20:120] = 1.0
    image[10:90, 70] = 1.0
    image[110, 20:120] = 1.0
    image[80:150, 70] = 1.0

    result = recognize_shapes(image, "bright_threshold", (VisionROI("box", 0, 0, 130, 80),))

    assert len(result.lines) >= 2
    assert any(abs(intersection.x - 70) <= 3 and abs(intersection.y - 40) <= 3 for intersection in result.intersections)
    assert all(intersection.y < 85 for intersection in result.intersections)
    assert result.circles == ()


def test_shape_recognition_limits_lines_and_intersections_to_edges_roi():
    import numpy as np

    image = np.zeros((160, 220), dtype=float)
    image[40, 20:120] = 1.0
    image[10:90, 70] = 1.0
    image[110, 20:120] = 1.0
    image[80:150, 70] = 1.0

    result = recognize_shapes(image, "bright_threshold", (VisionROI("edges", 0, 0, 130, 80),))

    assert len(result.lines) >= 2
    assert any(abs(intersection.x - 70) <= 3 and abs(intersection.y - 40) <= 3 for intersection in result.intersections)
    assert all(intersection.y < 85 for intersection in result.intersections)
    assert result.rectangles == ()


def test_rectangle_roi_detects_rectangle_with_missing_left_side():
    import numpy as np

    image = np.ones((140, 220), dtype=float)
    image[35:38, 45:175] = 0.0
    image[105:108, 45:175] = 0.0
    image[35:108, 172:175] = 0.0

    result = recognize_shapes(
        image,
        "opencv_hough",
        (VisionROI("rectangle", 25, 20, 190, 120),),
        geometry_sensitivity=1.0,
    )

    assert result.lines == ()
    assert result.intersections == ()
    assert result.rectangles
    rectangle = result.rectangles[0]
    assert rectangle.label == "rectangle"
    assert rectangle.missing_side == "left"
    assert abs(rectangle.x1 - 45) <= 6
    assert abs(rectangle.x2 - 174) <= 6
    assert abs(rectangle.y1 - 36) <= 6
    assert abs(rectangle.y2 - 106) <= 6


def test_rectangle_roi_detects_slanted_rectangle_with_missing_left_side():
    import math
    import numpy as np

    image = np.zeros((180, 260), dtype=float)
    center = np.asarray((130.0, 88.0))
    axis = np.asarray((math.cos(math.radians(15.0)), math.sin(math.radians(15.0))))
    normal = np.asarray((-axis[1], axis[0]))
    half_width = 58.0
    half_height = 30.0
    top_left = center - axis * half_width - normal * half_height
    top_right = center + axis * half_width - normal * half_height
    bottom_right = center + axis * half_width + normal * half_height
    bottom_left = center - axis * half_width + normal * half_height

    def draw_segment(start, end):
        steps = int(max(abs(float(end[0] - start[0])), abs(float(end[1] - start[1]))) * 3)
        for index in range(max(steps, 1) + 1):
            point = start + (end - start) * (index / max(steps, 1))
            x = int(round(float(point[0])))
            y = int(round(float(point[1])))
            image[max(0, y - 1) : min(image.shape[0], y + 2), max(0, x - 1) : min(image.shape[1], x + 2)] = 1.0

    draw_segment(top_left + axis * 4.0, top_right - axis * 4.0)
    draw_segment(bottom_left + axis * 4.0, bottom_right - axis * 4.0)
    draw_segment(top_right + normal * 4.0, bottom_right - normal * 4.0)

    result = recognize_shapes(
        image,
        "bright_threshold",
        (VisionROI("rectangle", 45, 20, 215, 155),),
    )

    assert result.rectangles
    rectangle = result.rectangles[0]
    assert rectangle.label == "rectangle"
    assert rectangle.missing_side == "left"
    assert len(rectangle.corners) == 4
    expected = (top_left, top_right, bottom_right, bottom_left)
    for expected_corner in expected:
        assert min(
            math.hypot(corner[0] - float(expected_corner[0]), corner[1] - float(expected_corner[1]))
            for corner in rectangle.corners
        ) <= 8.0


def test_rectangle_roi_detects_bright_rectangle_silhouette_on_dark_background():
    import numpy as np

    height, width = 150, 240
    yy, xx = np.ogrid[:height, :width]
    image = 0.28 + 0.08 * (xx / width) + 0.03 * np.sin(yy / 10.0)
    image = np.clip(image, 0.0, 1.0)
    image[45:106, 55:186] = 0.88
    image[68:73, 92:100] = 0.42
    image[66:76, 95:98] = 0.42
    image[82:87, 142:150] = 0.44
    image[80:90, 145:148] = 0.44

    result = recognize_shapes(
        image,
        "dark_adaptive",
        (VisionROI("rectangle", 35, 25, 205, 125),),
        bright_rectangle_sensitivity=0.65,
    )

    assert result.rectangles
    rectangle = result.rectangles[0]
    assert rectangle.label == "bright silhouette"
    assert rectangle.missing_side is None
    assert abs(rectangle.x1 - 55) <= 3
    assert abs(rectangle.x2 - 185) <= 3
    assert abs(rectangle.y1 - 45) <= 3
    assert abs(rectangle.y2 - 105) <= 3


def test_rectangle_roi_detects_slanted_bright_rectangle_silhouette():
    import math
    import numpy as np
    import cv2

    height, width = 180, 260
    yy, xx = np.ogrid[:height, :width]
    image = np.clip(0.26 + 0.08 * (xx / width) + 0.03 * np.cos(yy / 12.0), 0.0, 1.0)
    center = np.asarray((132.0, 90.0))
    axis = np.asarray((math.cos(math.radians(-18.0)), math.sin(math.radians(-18.0))))
    normal = np.asarray((-axis[1], axis[0]))
    half_width = 62.0
    half_height = 28.0
    corners = np.asarray(
        [
            center - axis * half_width - normal * half_height,
            center + axis * half_width - normal * half_height,
            center + axis * half_width + normal * half_height,
            center - axis * half_width + normal * half_height,
        ],
        dtype=np.int32,
    )
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [corners], 255)
    image[mask > 0] = 0.88

    result = recognize_shapes(
        image,
        "dark_adaptive",
        (VisionROI("rectangle", 35, 20, 225, 160),),
        bright_rectangle_sensitivity=0.65,
    )

    assert result.rectangles
    rectangle = result.rectangles[0]
    assert rectangle.label == "bright silhouette"
    assert rectangle.missing_side is None
    assert len(rectangle.corners) == 4
    assert rectangle.x2 - rectangle.x1 > 110
    assert rectangle.y2 - rectangle.y1 > 70
    assert max(abs(corner[1] - rectangle.corners[0][1]) for corner in rectangle.corners[1:]) > 20


def test_short_edge_length_input_requires_positive_finite_value():
    assert parse_short_edge_length_um("500") == pytest.approx(500.0)

    for value in ("0", "-1", "nan", "inf", "bad"):
        with pytest.raises(ValueError):
            parse_short_edge_length_um(value)


def test_vision_recognition_lab_uses_fixed_500um_short_edge_length():
    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, image_root=STANDARD_POSITION_IMAGE_ROOT)
        try:
            lab.short_edge_um_var.set("1000")
            assert lab.selected_short_edge_length_um() == pytest.approx(FIXED_MEASUREMENT_SHORT_EDGE_LENGTH_UM)
            assert lab.selected_short_edge_length_um() == pytest.approx(500.0)
            lab._on_short_edge_um_entry_commit()  # pylint: disable=protected-access
            assert lab.short_edge_um_var.get() == "500"
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_rectangle_short_edge_measurement_uses_axis_aligned_short_side():
    rectangle = VisionRectangle(
        x1=10,
        y1=20,
        x2=110,
        y2=60,
        missing_side=None,
        score=1.0,
        label="rectangle",
    )
    circle = VisionCircleReference(
        source="circle",
        x=130,
        y=40,
        radius=15,
        label="circle",
        score=1.0,
    )

    measurement = rectangle_circle_measurement(rectangle, circle, 500)

    assert measurement.short_edge.length_px == pytest.approx(40.0)
    assert measurement.short_edge.midpoint == pytest.approx((110.0, 40.0))
    assert measurement.um_per_pixel == pytest.approx(12.5)
    assert measurement.dx_px == pytest.approx(-20.0)
    assert measurement.dx_um == pytest.approx(-250.0)


def test_relative_measurement_payload_uses_first_circle_as_um_origin():
    first = {
        "short_edge_length_um": 500.0,
        "um_per_pixel": 12.5,
        "rectangle_roi_index": 1,
        "rectangle_roi": vision_roi_to_dict(VisionROI("rectangle", 0, 0, 140, 90)),
        "circle_roi_index": 2,
        "circle_roi": vision_roi_to_dict(VisionROI("circle", 180, 0, 260, 90)),
        "circle_source": "circle",
        "circle_feature_role": "ball_candidate",
        "short_edge": {
            "start": {"x": 100.0, "y": 20.0},
            "end": {"x": 100.0, "y": 60.0},
            "midpoint": {"x": 100.0, "y": 40.0},
            "length_px": 40.0,
        },
        "circle_center": {"x": 220.0, "y": 40.0, "radius": 18.0},
        "delta_px": {"dx": -120.0, "dy": 0.0, "distance": 120.0},
        "delta_um": {"dx": -1500.0, "dy": 0.0, "distance": 1500.0},
    }
    second = dict(first)
    second["circle_roi_index"] = 3
    second["circle_roi"] = vision_roi_to_dict(VisionROI("circle", 285, 0, 365, 90))
    second["circle_center"] = {"x": 325.0, "y": 40.0, "radius": 18.0}

    relative = relative_measurement_payload_from_measurements((first, second))

    assert relative is not None
    assert relative["origin"] == "first_selected_circle_center"
    assert relative["origin_circle"]["roi_index"] == 2
    assert relative["origin_circle"]["feature_role"] == "ball_candidate"
    assert relative["origin_circle"]["x_um"] == pytest.approx(0.0)
    assert relative["origin_circle"]["y_um"] == pytest.approx(0.0)
    assert relative["edge_midpoint_relative_um"]["x"] == pytest.approx(-1500.0)
    assert relative["edge_midpoint_relative_um"]["y"] == pytest.approx(0.0)
    assert relative["edge_midpoint_relative_um"]["distance"] == pytest.approx(1500.0)
    assert relative["circles"][1]["roi_index"] == 3
    assert relative["circles"][1]["feature_role"] == "ball_candidate"
    assert relative["circles"][1]["x_um"] == pytest.approx(1312.5)
    assert relative["circles"][1]["y_um"] == pytest.approx(0.0)


def test_rectangle_short_edge_measurement_uses_rotated_rectangle_corners():
    import math
    import numpy as np

    center = np.asarray((100.0, 80.0))
    axis = np.asarray((math.cos(math.radians(25.0)), math.sin(math.radians(25.0))))
    normal = np.asarray((-axis[1], axis[0]))
    half_width = 50.0
    half_height = 16.0
    corners = tuple(
        tuple(point)
        for point in (
            center - axis * half_width - normal * half_height,
            center + axis * half_width - normal * half_height,
            center + axis * half_width + normal * half_height,
            center - axis * half_width + normal * half_height,
        )
    )
    rectangle = VisionRectangle(
        x1=min(point[0] for point in corners),
        y1=min(point[1] for point in corners),
        x2=max(point[0] for point in corners),
        y2=max(point[1] for point in corners),
        missing_side=None,
        score=1.0,
        label="rectangle",
        corners=corners,
    )
    circle_x, circle_y = center + axis * 70.0

    edge = selected_rectangle_short_edge(rectangle, float(circle_x), float(circle_y))

    assert edge.length_px == pytest.approx(32.0)
    assert edge.midpoint[0] == pytest.approx(float((center + axis * half_width)[0]))
    assert edge.midpoint[1] == pytest.approx(float((center + axis * half_width)[1]))


def test_rectangle_short_edge_selection_uses_nearest_short_edge():
    rectangle = VisionRectangle(
        x1=0,
        y1=0,
        x2=120,
        y2=40,
        missing_side=None,
        score=1.0,
        label="rectangle",
    )

    left_edge = selected_rectangle_short_edge(rectangle, -20, 20)
    right_edge = selected_rectangle_short_edge(rectangle, 140, 20)

    assert left_edge.midpoint == pytest.approx((0.0, 20.0))
    assert right_edge.midpoint == pytest.approx((120.0, 20.0))


def test_shape_recognition_limits_circles_to_circle_roi():
    import numpy as np

    image = np.zeros((160, 220), dtype=float)
    yy, xx = np.ogrid[:160, :220]
    image[np.abs((xx - 160) ** 2 + (yy - 45) ** 2 - 18**2) <= 18] = 1.0
    image[np.abs((xx - 160) ** 2 + (yy - 120) ** 2 - 18**2) <= 18] = 1.0

    result = recognize_shapes(image, "bright_threshold", (VisionROI("circle", 140, 25, 180, 65),))

    assert result.lines == ()
    assert result.intersections == ()
    assert result.circles
    assert all(abs(circle.y - 45) <= 5 for circle in result.circles)
    assert not any(abs(circle.y - 120) <= 5 for circle in result.circles)


def test_shape_recognition_limits_semicircles_to_semicircle_roi():
    import numpy as np

    image = np.zeros((140, 180), dtype=float)
    yy, xx = np.ogrid[:140, :180]
    cx, cy, radius = 90, 70, 30
    right_arc = (np.abs((xx - cx) ** 2 + (yy - cy) ** 2 - radius**2) <= 35) & (xx >= cx)
    image[right_arc] = 1.0

    result = recognize_shapes(
        image,
        "bright_threshold",
        (VisionROI("semicircle", cx - radius, cy - radius, cx + radius, cy + radius, orientation="right"),),
    )

    assert result.lines == ()
    assert result.intersections == ()
    assert result.circles == ()
    assert result.semicircles
    assert result.semicircles[0].orientation == "right"
    assert abs(result.semicircles[0].x - cx) <= 1
    assert abs(result.semicircles[0].y - cy) <= 1


def test_semicircle_roi_detects_smaller_arc_inside_circular_search_area():
    import numpy as np

    image = np.zeros((160, 220), dtype=float)
    yy, xx = np.ogrid[:160, :220]
    cx, cy, radius = 128, 82, 22
    right_arc = (np.abs((xx - cx) ** 2 + (yy - cy) ** 2 - radius**2) <= 30) & (xx >= cx)
    image[right_arc] = 1.0

    result = recognize_shapes(
        image,
        "bright_threshold",
        (VisionROI("semicircle", 55, 20, 185, 150, orientation="right"),),
    )

    assert result.semicircles
    assert abs(result.semicircles[0].x - cx) <= 3
    assert abs(result.semicircles[0].y - cy) <= 3
    assert abs(result.semicircles[0].radius - radius) <= 4


def test_dark_adaptive_detects_black_circle_on_uneven_gray_background():
    import numpy as np

    yy, xx = np.ogrid[:150, :210]
    background = 0.72 + 0.16 * (xx / 210.0) + 0.04 * np.sin(yy / 8.0)
    image = np.clip(background, 0.0, 1.0)
    cx, cy, radius = 145, 75, 24
    circle = np.abs((xx - cx) ** 2 + (yy - cy) ** 2 - radius**2) <= 45
    image[circle] = 0.03

    result = recognize_shapes(
        image,
        "dark_adaptive",
        (VisionROI("circle", cx - radius - 4, cy - radius - 4, cx + radius + 4, cy + radius + 4),),
    )

    assert result.lines == ()
    assert result.intersections == ()
    assert result.circles
    assert all(abs(circle.x - cx) <= 6 and abs(circle.y - cy) <= 6 for circle in result.circles)
    assert result.semicircles == ()


def test_background_corrected_dark_detects_black_semicircle_on_uneven_gray_background():
    import numpy as np

    yy, xx = np.ogrid[:150, :210]
    background = 0.74 + 0.12 * (xx / 210.0) + 0.05 * np.cos(yy / 10.0)
    image = np.clip(background, 0.0, 1.0)
    cx, cy, radius = 95, 80, 32
    right_arc = (np.abs((xx - cx) ** 2 + (yy - cy) ** 2 - radius**2) <= 45) & (xx >= cx)
    image[right_arc] = 0.02

    result = recognize_shapes(
        image,
        "background_corrected_dark",
        (VisionROI("semicircle", cx - radius, cy - radius, cx + radius, cy + radius, orientation="right"),),
    )

    assert result.lines == ()
    assert result.intersections == ()
    assert result.circles == ()
    assert result.semicircles
    assert result.semicircles[0].orientation == "right"


def test_dark_silhouette_finds_probe_shape_without_standard_picture_fixture():
    import numpy as np

    yy, xx = np.ogrid[:260, :320]
    image = np.ones((260, 320), dtype=float)
    circular_head = (xx - 160) ** 2 + (yy - 125) ** 2 <= 58**2
    stem = (132 <= xx) & (xx <= 188) & (125 <= yy) & (yy <= 220)
    image[circular_head | stem] = 0.02

    wrong_roi_result = recognize_shapes(
        image,
        "dark_adaptive",
        (VisionROI("semicircle", 80, 50, 240, 235, orientation="down"),),
        silhouette_algorithm_name="dark_silhouette",
    )
    assert wrong_roi_result.silhouettes == ()

    result = recognize_shapes(
        image,
        "dark_adaptive",
        (VisionROI("silhouette", 80, 50, 240, 235),),
        silhouette_algorithm_name="dark_silhouette",
    )

    assert result.silhouettes
    assert result.algorithm_name == "dark_adaptive+dark_silhouette"
    silhouette = result.silhouettes[0]
    assert abs(silhouette.x - 160) <= 5
    assert abs(silhouette.y - 138) <= 8
    assert 100 <= silhouette.x1 <= 105
    assert 65 <= silhouette.y1 <= 70
    assert 215 <= silhouette.x2 <= 220
    assert 215 <= silhouette.y2 <= 225
    assert silhouette.area > 12_000
    assert len(silhouette.contour_segments) > 80
    assert len(silhouette.circle_contour_segments) > 80
    assert silhouette.circle_x is not None
    assert silhouette.circle_y is not None
    assert silhouette.circle_radius is not None
    assert abs(silhouette.circle_x - 160) <= 5
    assert abs(silhouette.circle_y - 142) <= 8
    assert abs(silhouette.circle_radius - 66) <= 8


def test_dark_silhouette_sensitivity_controls_gray_cutoff():
    import numpy as np

    image = np.ones((80, 100), dtype=float)
    image[10:30, 10:30] = 0.03
    image[42:64, 62:86] = 0.24

    low = recognize_shapes(
        image,
        "dark_adaptive",
        silhouette_algorithm_name="dark_silhouette",
        silhouette_sensitivity=0.05,
    )
    high = recognize_shapes(
        image,
        "dark_adaptive",
        silhouette_algorithm_name="dark_silhouette",
        silhouette_sensitivity=0.60,
    )

    assert len(low.silhouettes) == 1
    assert len(high.silhouettes) == 2


def test_opencv_hough_detects_box_lines_intersection_and_circle_roi():
    import numpy as np

    image = np.ones((180, 240), dtype=float)
    image[50:53, 20:150] = 0.0
    image[20:130, 90:93] = 0.0
    yy, xx = np.ogrid[:180, :240]
    circle = np.abs((xx - 185) ** 2 + (yy - 105) ** 2 - 24**2) <= 55
    image[circle] = 0.0

    result = recognize_shapes(
        image,
        "opencv_hough",
        (VisionROI("box", 0, 0, 160, 145), VisionROI("circle", 155, 75, 215, 135)),
    )

    assert any(line.label == "opencv hough" for line in result.lines)
    assert any(abs(intersection.x - 90) <= 5 and abs(intersection.y - 51) <= 5 for intersection in result.intersections)
    assert any(
        circle.label == "opencv hough"
        and abs(circle.x - 185) <= 5
        and abs(circle.y - 105) <= 5
        and abs(circle.radius - 24) <= 5
        for circle in result.circles
    )


def test_opencv_hough_detects_circle_matching_drawn_roi_size_with_prior():
    import cv2
    import numpy as np

    image = np.ones((180, 240), dtype=np.uint8) * 255
    cv2.circle(image, (150, 92), 34, 0, 2)
    cv2.circle(image, (132, 92), 14, 0, 2)
    gray = image.astype(float) / 255.0

    result = recognize_shapes(
        gray,
        "opencv_hough_sized",
        (VisionROI("circle", 116, 58, 184, 126),),
        geometry_sensitivity=0.85,
    )

    size_prior_circles = [circle for circle in result.circles if circle.label == "opencv hough size-prior"]
    assert size_prior_circles
    assert abs(size_prior_circles[0].x - 150) <= 5
    assert abs(size_prior_circles[0].y - 92) <= 5
    assert abs(size_prior_circles[0].radius - 34) <= 5


def test_skimage_hough_detects_circle_matching_drawn_roi_size_with_prior():
    import cv2
    import numpy as np

    image = np.ones((180, 240), dtype=np.uint8) * 255
    cv2.circle(image, (150, 92), 34, 0, 2)
    cv2.circle(image, (132, 92), 14, 0, 2)
    gray = image.astype(float) / 255.0

    result = recognize_shapes(
        gray,
        "skimage_hough_sized",
        (VisionROI("circle", 116, 58, 184, 126),),
        geometry_sensitivity=0.85,
    )

    size_prior_circles = [circle for circle in result.circles if circle.label == "skimage hough size-prior"]
    assert size_prior_circles
    assert abs(size_prior_circles[0].x - 150) <= 5
    assert abs(size_prior_circles[0].y - 92) <= 5
    assert abs(size_prior_circles[0].radius - 34) <= 5


def test_opencv_hough_geometry_sensitivity_bridges_broken_line_segments():
    import math
    import numpy as np

    image = np.ones((150, 260), dtype=float)
    image[72:76, 20:112] = 0.0
    image[72:76, 132:232] = 0.0

    low = recognize_shapes(
        image,
        "opencv_hough",
        (VisionROI("box", 0, 40, 255, 110),),
        geometry_sensitivity=0.05,
    )
    high = recognize_shapes(
        image,
        "opencv_hough",
        (VisionROI("box", 0, 40, 255, 110),),
        geometry_sensitivity=1.0,
    )

    low_lengths = [
        math.hypot(line.x2 - line.x1, line.y2 - line.y1)
        for line in low.lines
        if line.label == "opencv hough"
    ]
    high_lengths = [
        math.hypot(line.x2 - line.x1, line.y2 - line.y1)
        for line in high.lines
        if line.label == "opencv hough"
    ]

    assert high_lengths
    assert max(high_lengths) > max(low_lengths or [0.0]) + 40
    assert max(high_lengths) > 180


def test_skimage_hough_detects_box_lines_and_circle_roi():
    import numpy as np

    image = np.ones((180, 240), dtype=float)
    image[50:53, 20:150] = 0.0
    image[20:130, 90:93] = 0.0
    yy, xx = np.ogrid[:180, :240]
    circle = np.abs((xx - 185) ** 2 + (yy - 105) ** 2 - 24**2) <= 55
    image[circle] = 0.0

    result = recognize_shapes(
        image,
        "skimage_hough",
        (VisionROI("box", 0, 0, 160, 145), VisionROI("circle", 155, 75, 215, 135)),
    )

    assert any(line.label == "skimage hough" for line in result.lines)
    assert any(abs(intersection.x - 90) <= 5 and abs(intersection.y - 51) <= 5 for intersection in result.intersections)
    assert any(abs(circle.x - 185) <= 5 and abs(circle.y - 105) <= 5 for circle in result.circles)


def test_opencv_adaptive_dark_detects_circle_roi_on_gray_background():
    import numpy as np

    height, width = 140, 180
    yy, xx = np.ogrid[:height, :width]
    background = 0.55 + 0.25 * (xx / width)
    image = np.repeat(background, height, axis=0).astype(float)
    circle = np.abs((xx - 105) ** 2 + (yy - 70) ** 2 - 22**2) <= 45
    image[circle] = 0.03

    result = recognize_shapes(
        image,
        "opencv_adaptive_dark",
        (VisionROI("circle", 78, 43, 132, 97),),
    )

    assert any(abs(circle.x - 105) <= 5 and abs(circle.y - 70) <= 5 for circle in result.circles)


def test_silhouette_sensitivity_scale_click_maps_to_pointer_position():
    assert silhouette_sensitivity_from_scale_x(8, 200) == pytest.approx(0.05)
    assert silhouette_sensitivity_from_scale_x(100, 200) == pytest.approx(0.525)
    assert silhouette_sensitivity_from_scale_x(192, 200) == pytest.approx(1.0)
    assert silhouette_sensitivity_from_scale_x(-100, 200) == pytest.approx(0.05)
    assert silhouette_sensitivity_from_scale_x(500, 200) == pytest.approx(1.0)


def test_geometry_sensitivity_scale_click_maps_to_pointer_position():
    assert geometry_sensitivity_from_scale_x(8, 200) == pytest.approx(0.05)
    assert geometry_sensitivity_from_scale_x(100, 200) == pytest.approx(0.525)
    assert geometry_sensitivity_from_scale_x(192, 200) == pytest.approx(1.0)
    assert geometry_sensitivity_from_scale_x(-100, 200) == pytest.approx(0.05)
    assert geometry_sensitivity_from_scale_x(500, 200) == pytest.approx(1.0)


def test_bright_rectangle_sensitivity_scale_click_maps_to_pointer_position():
    assert bright_rectangle_sensitivity_from_scale_x(8, 200) == pytest.approx(0.05)
    assert bright_rectangle_sensitivity_from_scale_x(100, 200) == pytest.approx(0.525)
    assert bright_rectangle_sensitivity_from_scale_x(192, 200) == pytest.approx(1.0)
    assert bright_rectangle_sensitivity_from_scale_x(-100, 200) == pytest.approx(0.05)
    assert bright_rectangle_sensitivity_from_scale_x(500, 200) == pytest.approx(1.0)


def test_shape_recognition_algorithms_are_swappable():
    import numpy as np

    image = np.ones((80, 100), dtype=float)
    image[30, 10:90] = 0.0
    image[10:70, 45] = 0.0

    dark_result = recognize_shapes(image, "dark_threshold")
    bright_result = recognize_shapes(image, "bright_threshold")

    assert dark_result.algorithm_name == "dark_threshold"
    assert bright_result.algorithm_name == "bright_threshold"
    assert len(dark_result.lines) >= 2
    assert dark_result.message != bright_result.message


def test_vision_recognition_lab_selects_position_images():
    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, image_root=STANDARD_POSITION_IMAGE_ROOT)
        try:
            assert lab.selected_position_id() == "1.1"
            assert lab.title() == VISION_RECOGNITION_LAB_TITLE
            assert lab.title().endswith("v3")
            position_values = tuple(lab.position_combobox.cget("values"))
            assert any(value.startswith("1.1 - ") for value in position_values)
            assert not any(value.startswith("6.0.0 - ") for value in position_values)
            assert [(image.batch, image.path.name) for image in lab.current_images()] == [
                ("v4", "1.1.1.PNG")
            ]
            assert lab.image_tree.item("image_0", "values") == ("v4", "newhead/1.1.1.PNG")
            lab.select_position("3.0")
            lab.update_idletasks()

            assert lab.selected_position_id() == "3.0"
            assert [(image.batch, image.path.name) for image in lab.current_images()] == [
                ("v4", "3.0.1.PNG")
            ]
            assert lab.image_tree.get_children()
            assert lab.shape_tool_var.get() == "silhouette"
            widget_texts = set()
            widgets = [lab]
            while widgets:
                widget = widgets.pop()
                widgets.extend(widget.winfo_children())
                try:
                    text = widget.cget("text")
                except tk.TclError:
                    continue
                if text:
                    widget_texts.add(text)
            assert "Edges ROI" in widget_texts
            assert "Rectangle ROI" in widget_texts
            assert "Geometry ROIs" in widget_texts
            assert "Rect bright sens." in widget_texts
            assert "Silhouette ROI" in widget_texts
            assert "Rectangle overlays" in widget_texts
            assert "Yellow = edge/line rectangle" in widget_texts
            assert "Magenta = bright silhouette rectangle" in widget_texts
            assert "Dashed edge = inferred missing side" in widget_texts
            assert "Box ROI" not in widget_texts
            assert "Semicircle ROI" not in widget_texts
            assert lab._photo_image is not None  # pylint: disable=protected-access
            assert lab._photo_image.width() <= lab.image_canvas.winfo_width()  # pylint: disable=protected-access
            assert lab._photo_image.height() <= lab.image_canvas.winfo_height()  # pylint: disable=protected-access
            assert lab.selected_recognizer_name() == "skimage_hough_sized"
            assert lab.selected_geometry_sensitivity() == pytest.approx(0.65)
            assert lab.selected_bright_rectangle_sensitivity() == pytest.approx(0.65)
            assert lab.selected_silhouette_recognizer_name() == "dark_silhouette"
            assert lab.selected_silhouette_sensitivity() == pytest.approx(0.65)
            managed = lambda widget: widget.winfo_manager() == "grid"
            assert not managed(lab.geometry_recognizer_label)  # pylint: disable=protected-access
            assert not managed(lab.recognizer_combobox)  # pylint: disable=protected-access
            assert not managed(lab.geometry_sensitivity_label)  # pylint: disable=protected-access
            assert not managed(lab.bright_rectangle_sensitivity_label)  # pylint: disable=protected-access
            assert managed(lab.silhouette_recognizer_label)  # pylint: disable=protected-access
            assert managed(lab.silhouette_sensitivity_label)  # pylint: disable=protected-access
            assert set(lab._geometry_recognizer_display_to_name.values()) == {  # pylint: disable=protected-access
                "skimage_hough_sized",
                "skimage_hough",
                "opencv_hough_sized",
                "opencv_hough",
            }
            lab.shape_tool_var.set("edges")
            lab._on_tool_selected()  # pylint: disable=protected-access
            assert managed(lab.geometry_recognizer_label)  # pylint: disable=protected-access
            assert managed(lab.recognizer_combobox)  # pylint: disable=protected-access
            assert managed(lab.geometry_sensitivity_label)  # pylint: disable=protected-access
            assert not managed(lab.bright_rectangle_sensitivity_label)  # pylint: disable=protected-access
            assert not managed(lab.silhouette_recognizer_label)  # pylint: disable=protected-access
            lab.recognizer_var.set("OpenCV Canny + Hough")
            lab._on_recognizer_selected()  # pylint: disable=protected-access
            assert managed(lab.geometry_sensitivity_label)  # pylint: disable=protected-access
            lab.recognizer_var.set("scikit-image Canny + Hough")
            lab._on_recognizer_selected()  # pylint: disable=protected-access
            assert managed(lab.geometry_sensitivity_label)  # pylint: disable=protected-access
            lab.shape_tool_var.set("rectangle")
            lab._on_tool_selected()  # pylint: disable=protected-access
            assert managed(lab.bright_rectangle_sensitivity_label)  # pylint: disable=protected-access
            assert not managed(lab.silhouette_recognizer_label)  # pylint: disable=protected-access
            lab.shape_tool_var.set("silhouette")
            lab._on_tool_selected()  # pylint: disable=protected-access
            initial_width = lab._photo_image.width()  # pylint: disable=protected-access
            initial_scale = lab._view_scale  # pylint: disable=protected-access
            lab._on_canvas_mouse_wheel(  # pylint: disable=protected-access
                SimpleNamespace(
                    delta=120,
                    num=None,
                    x=lab.image_canvas.winfo_width() // 2,
                    y=lab.image_canvas.winfo_height() // 2,
                )
            )
            lab.update_idletasks()
            assert lab._view_scale >= initial_scale  # pylint: disable=protected-access
            assert lab._photo_image.width() >= initial_width  # pylint: disable=protected-access
            lab.silhouette_sensitivity_var.set(0.55)
            lab._on_silhouette_sensitivity_changed("0.55")  # pylint: disable=protected-access
            assert lab.selected_silhouette_sensitivity() == pytest.approx(0.55)
            lab.silhouette_sensitivity_var.set(1.2)
            lab._on_silhouette_sensitivity_changed("1.2")  # pylint: disable=protected-access
            assert lab.selected_silhouette_sensitivity() == pytest.approx(1.0)
            lab.silhouette_sensitivity_text_var.set("0.42")
            lab._on_silhouette_sensitivity_entry_commit()  # pylint: disable=protected-access
            assert lab.selected_silhouette_sensitivity() == pytest.approx(0.42)
            assert lab.silhouette_sensitivity_text_var.get() == "0.42"
            lab.silhouette_sensitivity_text_var.set("2.0")
            lab._on_silhouette_sensitivity_entry_commit()  # pylint: disable=protected-access
            assert lab.selected_silhouette_sensitivity() == pytest.approx(1.0)
            assert lab.silhouette_sensitivity_text_var.get() == "1.00"
            lab.silhouette_sensitivity_text_var.set("bad")
            lab._on_silhouette_sensitivity_entry_commit()  # pylint: disable=protected-access
            assert lab.selected_silhouette_sensitivity() == pytest.approx(1.0)
            assert lab.silhouette_sensitivity_text_var.get() == "1.00"
            lab.geometry_sensitivity_var.set(0.35)
            lab._on_geometry_sensitivity_changed("0.35")  # pylint: disable=protected-access
            assert lab.selected_geometry_sensitivity() == pytest.approx(0.35)
            lab.geometry_sensitivity_text_var.set("0.78")
            lab._on_geometry_sensitivity_entry_commit()  # pylint: disable=protected-access
            assert lab.selected_geometry_sensitivity() == pytest.approx(0.78)
            assert lab.geometry_sensitivity_text_var.get() == "0.78"
            lab.bright_rectangle_sensitivity_var.set(0.33)
            lab._on_bright_rectangle_sensitivity_changed("0.33")  # pylint: disable=protected-access
            assert lab.selected_bright_rectangle_sensitivity() == pytest.approx(0.33)
            lab.bright_rectangle_sensitivity_text_var.set("0.81")
            lab._on_bright_rectangle_sensitivity_entry_commit()  # pylint: disable=protected-access
            assert lab.selected_bright_rectangle_sensitivity() == pytest.approx(0.81)
            assert lab.bright_rectangle_sensitivity_text_var.get() == "0.81"
            source = lab._source_photo_image  # pylint: disable=protected-access
            assert source is not None
            assert lab.run_recognition() is None
            assert lab.tool_status_var.get() == ROI_REQUIRED_MESSAGE
            assert not lab.recognition_tree.get_children()
            lab.add_roi(VisionROI("box", 0, 0, source.width(), source.height()))
            assert lab.current_rois()
            assert lab.run_recognition() is not None
            assert lab.recognition_tree.get_children()
            lab.recognizer_var.set("OpenCV adaptive dark")
            lab._on_recognizer_selected()  # pylint: disable=protected-access
            assert lab._recognition_result is None  # pylint: disable=protected-access
            assert not lab.recognition_tree.get_children()
            assert "press Run" in lab.tool_status_var.get()
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_vision_recognition_lab_highlights_clicked_row_and_measures_multiple_circle_rois():
    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, image_root=STANDARD_POSITION_IMAGE_ROOT)
        try:
            lab.update_idletasks()
            lab.add_roi(VisionROI("rectangle", 0, 0, 140, 90))
            lab.add_roi(VisionROI("circle", 180, 0, 260, 90))
            lab.add_roi(VisionROI("circle", 285, 0, 365, 90))
            result = VisionRecognitionResult(
                algorithm_name="test",
                display_name="test",
                lines=(),
                intersections=(),
                circles=(
                    VisionCircle(x=220, y=40, radius=18, score=0.9, label="circle"),
                    VisionCircle(x=325, y=40, radius=18, score=0.85, label="circle"),
                ),
                rectangles=(
                    VisionRectangle(
                        x1=10,
                        y1=10,
                        x2=110,
                        y2=50,
                        missing_side=None,
                        score=0.7,
                        label="rectangle",
                    ),
                    VisionRectangle(
                        x1=20,
                        y1=20,
                        x2=100,
                        y2=60,
                        missing_side=None,
                        score=0.8,
                        label="rectangle",
                    ),
                ),
                semicircles=(),
                silhouettes=(),
                message="test",
            )
            lab._recognition_result = result  # pylint: disable=protected-access
            lab._populate_recognition_tree(result)  # pylint: disable=protected-access

            assert tuple(lab.recognition_tree["columns"]) == ("use", "roi", "type", "role", "target", "score")
            rectangle_ids = [
                item_id
                for item_id, item in lab._recognition_tree_items.items()  # pylint: disable=protected-access
                if item.shape_kind == "rectangle"
            ]
            circle_ids = [
                item_id
                for item_id, item in lab._recognition_tree_items.items()  # pylint: disable=protected-access
                if item.shape_kind == "circle"
            ]
            assert len(rectangle_ids) == 2
            assert len(circle_ids) == 2

            lab.recognition_tree.selection_set(rectangle_ids[0])
            lab.recognition_tree.focus(rectangle_ids[0])
            lab._on_recognition_tree_selected()  # pylint: disable=protected-access
            assert lab.image_canvas.find_withtag("active_recognition")

            lab._use_selected_recognition_row()  # pylint: disable=protected-access
            assert lab.recognition_tree.item(rectangle_ids[0], "values")[0] == "Yes"
            assert lab.image_canvas.find_withtag("selected_recognition")
            assert lab.image_canvas.find_withtag("rectangle_corner")
            lab.recognition_tree.selection_set(rectangle_ids[1])
            lab.recognition_tree.focus(rectangle_ids[1])
            lab._use_selected_recognition_row()  # pylint: disable=protected-access
            assert lab.recognition_tree.item(rectangle_ids[0], "values")[0] == "Yes"
            assert lab.recognition_tree.item(rectangle_ids[1], "values")[0] == "Yes"

            lab.recognition_tree.selection_set(*circle_ids)
            lab.recognition_tree.focus(circle_ids[0])
            lab._use_selected_recognition_row()  # pylint: disable=protected-access
            assert lab.selected_measurement_payload() is None
            assert "exactly one rectangle/edge" in lab.measurement_var.get()

            lab.recognition_tree.selection_remove(*lab.recognition_tree.selection())
            lab.recognition_tree.focus("")
            lab._clear_selected_recognition_roi()  # pylint: disable=protected-access
            assert lab.recognition_tree.item(rectangle_ids[0], "values")[0] == ""
            assert lab.recognition_tree.item(rectangle_ids[1], "values")[0] == ""

            lab.recognition_tree.selection_set(rectangle_ids[1], *circle_ids)
            lab.recognition_tree.focus(rectangle_ids[1])
            lab._use_selected_recognition_row()  # pylint: disable=protected-access
            lab._use_selected_recognition_row()  # pylint: disable=protected-access
            assert lab.recognition_tree.item(rectangle_ids[1], "values")[0] == "Yes"
            measurement = lab.selected_measurement_payload()
            measurements = lab.selected_measurements_payload()
            assert measurement is not None
            assert len(measurements) == 2
            assert measurement["rectangle_roi_index"] == 1
            assert measurement["circle_roi_index"] == 2
            assert measurement["circle_source"] == "circle"
            assert measurement["short_edge"]["length_px"] == pytest.approx(40.0)
            assert measurement["um_per_pixel"] == pytest.approx(12.5)
            assert measurements[1]["circle_roi_index"] == 3
            relative = lab.selected_relative_measurement_payload()
            assert relative is not None
            assert relative["origin_circle"]["roi_index"] == 2
            assert relative["edge_midpoint_relative_um"]["x"] == pytest.approx(-1500.0)
            assert relative["edge_midpoint_relative_um"]["y"] == pytest.approx(0.0)
            assert relative["circles"][0]["x_um"] == pytest.approx(0.0)
            assert relative["circles"][0]["y_um"] == pytest.approx(0.0)
            assert relative["circles"][1]["roi_index"] == 3
            assert relative["circles"][1]["x_um"] == pytest.approx(1312.5)
            yase_display = lab.selected_yase_display_status("Vision recognition lab closed")
            assert "origin circle ROI 2 = (0.000, 0.000) um" in yase_display
            assert "edge midpoint x=-1500.000 um" in yase_display
            assert "circle ROI 3 x=1312.500 um" in yase_display
            assert lab.image_canvas.find_withtag("selected_short_edge")

            payload = vision_session_payload(
                image_path="image.bmp",
                rois=lab.current_rois(),
                result=result,
                status="ok",
                selected_recognition=lab.selected_recognition_payload(),
                measurement=measurement,
                measurements=measurements,
                relative_measurement=relative,
                yase_display=yase_display,
            )
            assert set(payload["selected_recognition"]) == {"roi_1", "roi_2", "roi_3"}
            assert len(payload["selected_recognition"]["roi_2"]) == 1
            rectangle_selection = payload["selected_recognition"]["roi_1"][0]
            first_circle_selection = payload["selected_recognition"]["roi_2"][0]
            second_circle_selection = payload["selected_recognition"]["roi_3"][0]
            assert rectangle_selection["feature_role"] == "laser_reference"
            assert first_circle_selection["feature_role"] == "ball_candidate"
            assert second_circle_selection["feature_role"] == "ball_candidate"
            assert rectangle_selection["selection_index"] == 1
            assert first_circle_selection["selection_index"] == 2
            assert second_circle_selection["selection_index"] == 3
            assert len(payload["measurements"]) == 2
            assert payload["measurement"]["rectangle_feature_role"] == "laser_reference"
            assert payload["measurement"]["circle_feature_role"] == "ball_candidate"
            assert payload["measurement"]["circle_source"] == "circle"
            assert payload["relative_measurement"]["origin_circle"]["roi_index"] == 2
            assert payload["relative_measurement"]["origin_circle"]["feature_role"] == "ball_candidate"
            assert payload["relative_measurement"]["circles"][1]["x_um"] == pytest.approx(1312.5)
            assert payload["yase_display"] == yase_display
            assert payload["status"] == yase_display
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_vision_recognition_lab_can_override_selected_feature_role():
    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, image_root=STANDARD_POSITION_IMAGE_ROOT)
        try:
            lab.update_idletasks()
            lab.add_roi(VisionROI("rectangle", 0, 0, 140, 90))
            lab.add_roi(VisionROI("circle", 180, 0, 260, 90))
            result = VisionRecognitionResult(
                algorithm_name="test",
                display_name="test",
                lines=(),
                intersections=(),
                circles=(
                    VisionCircle(
                        x=220,
                        y=40,
                        radius=18,
                        score=0.95,
                        label="circle",
                    ),
                ),
                rectangles=(
                    VisionRectangle(
                        x1=10,
                        y1=20,
                        x2=110,
                        y2=60,
                        missing_side=None,
                        score=0.9,
                        label="rectangle",
                    ),
                ),
                semicircles=(),
                silhouettes=(),
                message="test",
            )
            lab._recognition_result = result  # pylint: disable=protected-access
            lab._populate_recognition_tree(result)  # pylint: disable=protected-access
            rectangle_id = next(
                item_id
                for item_id, item in lab._recognition_tree_items.items()  # pylint: disable=protected-access
                if item.shape_kind == "rectangle"
            )
            circle_id = next(
                item_id
                for item_id, item in lab._recognition_tree_items.items()  # pylint: disable=protected-access
                if item.shape_kind == "circle"
            )

            lab.recognition_tree.selection_set(circle_id)
            lab.recognition_tree.focus(circle_id)
            lab.feature_role_var.set("ball_1_top_ball")
            lab._set_selected_recognition_role()  # pylint: disable=protected-access
            assert lab.recognition_tree.item(circle_id, "values")[3] == "ball_1_top_ball"

            lab.recognition_tree.selection_set(rectangle_id, circle_id)
            lab.recognition_tree.focus(rectangle_id)
            lab._use_selected_recognition_row()  # pylint: disable=protected-access

            payload = lab.selected_recognition_payload()
            measurement = lab.selected_measurement_payload()
            relative = lab.selected_relative_measurement_payload()

            assert payload["roi_2"][0]["feature_role"] == "ball_1_top_ball"
            assert measurement is not None
            assert measurement["circle_feature_role"] == "ball_1_top_ball"
            assert relative is not None
            assert relative["origin_circle"]["feature_role"] == "ball_1_top_ball"
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_vision_recognition_lab_measurement_can_use_silhouette_fitted_circle():
    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, image_root=STANDARD_POSITION_IMAGE_ROOT)
        try:
            lab.update_idletasks()
            lab.add_roi(VisionROI("rectangle", 0, 0, 140, 90))
            lab.add_roi(VisionROI("silhouette", 180, 0, 270, 100))
            result = VisionRecognitionResult(
                algorithm_name="test",
                display_name="test",
                lines=(),
                intersections=(),
                circles=(),
                rectangles=(
                    VisionRectangle(
                        x1=10,
                        y1=10,
                        x2=110,
                        y2=50,
                        missing_side=None,
                        score=0.7,
                        label="rectangle",
                    ),
                ),
                semicircles=(),
                silhouettes=(
                    VisionSilhouette(
                        x=225,
                        y=45,
                        x1=190,
                        y1=10,
                        x2=260,
                        y2=80,
                        area=1200,
                        score=0.9,
                        label="blob",
                        circle_x=225,
                        circle_y=40,
                        circle_radius=18,
                    ),
                ),
                message="test",
            )
            lab._recognition_result = result  # pylint: disable=protected-access
            lab._populate_recognition_tree(result)  # pylint: disable=protected-access

            item_ids = tuple(lab._recognition_tree_items)  # pylint: disable=protected-access
            lab.recognition_tree.selection_set(*item_ids)
            lab.recognition_tree.focus(item_ids[0])
            lab._use_selected_recognition_row()  # pylint: disable=protected-access

            measurement = lab.selected_measurement_payload()
            assert measurement is not None
            assert len(lab.selected_measurements_payload()) == 1
            assert measurement["circle_source"] == "silhouette_circle"
            assert measurement["circle_center"]["x"] == pytest.approx(225)
            assert lab.selected_relative_measurement_payload()["origin_circle"]["source"] == "silhouette_circle"
            assert "um/px" in lab.measurement_var.get()
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_vision_recognition_lab_has_save_official_button_and_writes_baseline_for_current_picture(tmp_path):
    import cv2
    import numpy as np

    image_path = tmp_path / "camera_frame.bmp"
    image = np.zeros((120, 180), dtype=np.uint8)
    image[10:50, 10:110] = 180
    image[20:60, 180 - 1 : 180] = 255
    assert cv2.imwrite(str(image_path), image)

    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, captured_image_path=image_path)
        try:
            lab.update_idletasks()
            assert lab.save_official_button.cget("text") == "Save official"
            assert lab.score_official_button.cget("text") == "Score"

            lab.add_roi(VisionROI("rectangle", 0, 0, 140, 90))
            lab.add_roi(VisionROI("circle", 180, 0, 270, 100))
            result = VisionRecognitionResult(
                algorithm_name="test",
                display_name="test",
                lines=(),
                intersections=(),
                circles=(VisionCircle(x=220, y=40, radius=18, score=0.9, label="circle"),),
                rectangles=(
                    VisionRectangle(
                        x1=10,
                        y1=10,
                        x2=110,
                        y2=50,
                        missing_side=None,
                        score=0.7,
                        label="rectangle",
                    ),
                ),
                semicircles=(),
                silhouettes=(),
                message="test",
            )
            lab._recognition_result = result  # pylint: disable=protected-access
            lab._populate_recognition_tree(result)  # pylint: disable=protected-access
            item_ids = tuple(lab._recognition_tree_items)  # pylint: disable=protected-access
            lab.recognition_tree.selection_set(*item_ids)
            lab.recognition_tree.focus(item_ids[0])
            lab._use_selected_recognition_row()  # pylint: disable=protected-access

            output_path = lab.save_official_baseline()

            assert output_path == tmp_path / OFFICIAL_BASELINE_FOLDER_NAME / "camera_frame.json"
            assert output_path.is_file()
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            assert payload["standard_capture_id"] == "camera_frame"
            assert payload["standard_position_id"] == "captured"
            assert payload["relative_measurement"]["origin_circle"]["source"] == "circle"

            score_path, score = lab.score_against_official_baseline()

            assert score_path == tmp_path / VISION_SCORE_FOLDER_NAME / "camera_frame_score.json"
            assert score_path.is_file()
            assert score["ok"] is True
            assert score["passed"] is True
            assert score["metrics"]["max_shape_error_px"] == pytest.approx(0.0)
            assert score["metrics"]["max_abs_xy_error_um"] == pytest.approx(0.0)
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_vision_recognition_lab_saves_reviewed_standard_session_to_v5_memory(tmp_path):
    import cv2
    import numpy as np

    image_root = tmp_path / "Standard position images"
    batch_dir = image_root / "v4"
    image_dir = batch_dir / "newhead"
    image_dir.mkdir(parents=True)
    image_path = image_dir / "2.4.1.PNG"
    image = np.zeros((120, 180), dtype=np.uint8)
    image[10:50, 10:110] = 180
    image[35:75, 135:175] = 255
    assert cv2.imwrite(str(image_path), image)
    (batch_dir / "standard_positions.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "positions": [
                    {
                        "id": "2.4",
                        "label": "top_close_zoom_focus_plank",
                        "captured_images": ["newhead/2.4.1.PNG"],
                        "machine_positions_um": {
                            "tower_1": {"x": 5331, "y": 12291, "z": 15198},
                            "tower_2": {"x": None, "y": None, "z": None},
                            "camera": {"x": -38997, "y": -45395, "z": -93995},
                        },
                        "camera_settings": {"zoom": {"value": 4500}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, image_root=image_root)
        try:
            lab.update_idletasks()
            assert lab.save_v5_memory_button.cget("text") == "Save v5"
            assert lab._selected_image is not None  # pylint: disable=protected-access
            assert lab._selected_image.path == image_path  # pylint: disable=protected-access

            lab.add_roi(VisionROI("rectangle", 0, 0, 130, 80))
            lab.add_roi(VisionROI("circle", 120, 20, 180, 90))
            result = VisionRecognitionResult(
                algorithm_name="test",
                display_name="test",
                lines=(),
                intersections=(),
                circles=(VisionCircle(x=150, y=55, radius=18, score=0.9, label="circle"),),
                rectangles=(
                    VisionRectangle(
                        x1=10,
                        y1=10,
                        x2=110,
                        y2=50,
                        missing_side=None,
                        score=0.7,
                        label="rectangle",
                    ),
                ),
                semicircles=(),
                silhouettes=(),
                message="test",
            )
            lab._recognition_result = result  # pylint: disable=protected-access
            lab._populate_recognition_tree(result)  # pylint: disable=protected-access
            item_ids = tuple(lab._recognition_tree_items)  # pylint: disable=protected-access
            lab.recognition_tree.selection_set(*item_ids)
            lab.recognition_tree.focus(item_ids[0])
            lab._use_selected_recognition_row()  # pylint: disable=protected-access

            output_path = lab.save_v5_sequence_memory()

            assert output_path == batch_dir / V5_SEQUENCE_MEMORY_FOLDER_NAME / V5_SEQUENCE_MEMORY_FILE_NAME
            memory = json.loads(output_path.read_text(encoding="utf-8"))
            record = memory["capture_records"]["2.4.1"]
            assert record["position_id"] == "2.4"
            assert record["review_status"] == "reviewed"
            assert record["machine_positions_um"]["camera"]["y"] == -45395
            assert record["machine_positions_um"]["tower_1"]["z"] == 15198
            assert record["session"]["selected_recognition"]["roi_1"][0]["shape_kind"] == "rectangle"
            assert record["session"]["selected_recognition"]["roi_2"][0]["shape_kind"] == "circle"
            assert memory["standard_positions_path"] == str(batch_dir / "standard_positions.json")
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_vision_recognition_lab_save_official_allows_circle_only_selection(tmp_path):
    import cv2
    import numpy as np

    image_path = tmp_path / "ball_only.bmp"
    image = np.zeros((80, 80), dtype=np.uint8)
    image[25:55, 25:55] = 220
    assert cv2.imwrite(str(image_path), image)

    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, captured_image_path=image_path)
        try:
            lab.update_idletasks()
            lab.add_roi(VisionROI("circle", 20, 20, 60, 60))
            result = VisionRecognitionResult(
                algorithm_name="test",
                display_name="test",
                lines=(),
                intersections=(),
                circles=(VisionCircle(x=40, y=40, radius=15, score=0.9, label="circle"),),
                rectangles=(),
                semicircles=(),
                silhouettes=(),
                message="test",
            )
            lab._recognition_result = result  # pylint: disable=protected-access
            lab._populate_recognition_tree(result)  # pylint: disable=protected-access
            item_ids = tuple(lab._recognition_tree_items)  # pylint: disable=protected-access
            lab.recognition_tree.selection_set(*item_ids)
            lab.recognition_tree.focus(item_ids[0])
            lab._use_selected_recognition_row()  # pylint: disable=protected-access

            output_path = lab.save_official_baseline()

            payload = json.loads(output_path.read_text(encoding="utf-8"))
            assert payload["standard_capture_id"] == "ball_only"
            assert payload["relative_measurement"] is None
            assert payload["selected_recognition"]["roi_1"][0]["shape_kind"] == "circle"
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_vision_recognition_lab_deselect_clears_selected_use_without_removing_roi(tmp_path):
    import cv2
    import numpy as np

    image_path = tmp_path / "remove_roi.bmp"
    image = np.zeros((100, 130), dtype=np.uint8)
    image[20:70, 20:90] = 220
    assert cv2.imwrite(str(image_path), image)

    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, captured_image_path=image_path)
        try:
            lab.update_idletasks()
            lab.add_roi(VisionROI("circle", 10, 10, 60, 60))
            lab.add_roi(VisionROI("rectangle", 65, 10, 120, 80))
            result = VisionRecognitionResult(
                algorithm_name="test",
                display_name="test",
                lines=(),
                intersections=(),
                circles=(VisionCircle(x=35, y=35, radius=18, score=0.9, label="circle"),),
                rectangles=(
                    VisionRectangle(
                        x1=70,
                        y1=20,
                        x2=110,
                        y2=70,
                        missing_side=None,
                        score=0.8,
                        label="rectangle",
                    ),
                ),
                semicircles=(),
                silhouettes=(),
                message="test",
            )
            lab._recognition_result = result  # pylint: disable=protected-access
            lab._populate_recognition_tree(result)  # pylint: disable=protected-access
            circle_item = next(
                item_id
                for item_id, item in lab._recognition_tree_items.items()  # pylint: disable=protected-access
                if item.shape_kind == "circle"
            )
            lab.recognition_tree.selection_set(circle_item)
            lab.recognition_tree.focus(circle_item)
            lab._use_selected_recognition_row()  # pylint: disable=protected-access
            assert lab.recognition_tree.item(circle_item, "values")[0] == "Yes"

            lab._clear_selected_recognition_roi()  # pylint: disable=protected-access

            assert len(lab.current_rois()) == 2
            assert [roi.kind for roi in lab.current_rois()] == ["circle", "rectangle"]
            assert lab._recognition_result is result  # pylint: disable=protected-access
            assert circle_item in lab.recognition_tree.get_children()
            assert lab.recognition_tree.item(circle_item, "values")[0] == ""
            assert "Deselected selected detection" in lab.tool_status_var.get()
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_vision_recognition_lab_opens_captured_bmp_image(tmp_path):
    import cv2
    import numpy as np

    image_path = tmp_path / "python_vision_input.bmp"
    image = np.zeros((48, 64), dtype=np.uint8)
    image[12:36, 18:46] = 220
    assert cv2.imwrite(str(image_path), image)

    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, captured_image_path=image_path)
        try:
            lab.update_idletasks()

            assert lab.selected_position_id() == "captured"
            assert [(item.batch, item.path.name) for item in lab.current_images()] == [
                ("capture", "python_vision_input.bmp")
            ]
            assert lab._source_gray_image is not None  # pylint: disable=protected-access
            assert lab._source_gray_image.shape == (48, 64)  # pylint: disable=protected-access
            assert lab._source_photo_image is not None  # pylint: disable=protected-access
            assert lab._source_photo_image.width() == 64  # pylint: disable=protected-access
            assert lab._source_photo_image.height() == 48  # pylint: disable=protected-access
            assert "Captured image" in lab.position_summary_var.get()
            assert str(image_path) in lab.position_summary_var.get()
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_vision_recognition_lab_step_dry_run_loads_captured_image(tmp_path):
    import cv2
    import numpy as np

    image_path = tmp_path / "python_vision_input.bmp"
    result_path = tmp_path / "vision_recognition_result.json"
    image = np.zeros((24, 32), dtype=np.uint8)
    image[:, 8:16] = 180
    assert cv2.imwrite(str(image_path), image)

    result = VisionRecognitionLabStep().run(
        {
            "schema_version": 3,
            "image_path": str(image_path),
            "result_output_path": str(result_path),
            "dry_run": True,
        }
    )

    assert result["ok"] is True
    assert result["action"] == "vision_lab_closed_without_rois"
    assert result["roi_count"] == 0
    assert "32 x 24" in result["status"]
    assert result_path.is_file()
    assert json.loads(result_path.read_text(encoding="utf-8"))["image_path"] == str(image_path)


def test_vision_roi_serialization_uses_normalized_coordinates():
    assert vision_roi_to_dict(VisionROI("rectangle", 30, 20, 10, 5)) == {
        "kind": "rectangle",
        "x1": 10,
        "y1": 5,
        "x2": 30,
        "y2": 20,
        "orientation": "right",
    }


def test_dark_silhouette_overlay_only_draws_circle_target_layers():
    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, image_root=STANDARD_POSITION_IMAGE_ROOT)
        try:
            lab.select_position("1.1")
            lab.update_idletasks()
            lab.add_roi(VisionROI("silhouette", 620, 220, 1040, 720))
            assert lab.run_recognition() is not None
            lab.update_idletasks()

            silhouette_items = lab.image_canvas.find_withtag("silhouette")
            assert silhouette_items
            assert lab.image_canvas.find_withtag("roi_overlay")
            assert lab.image_canvas.find_withtag("silhouette_circle_contour")
            assert lab.image_canvas.find_withtag("silhouette_circle")
            assert lab.image_canvas.find_withtag("silhouette_circle_center")
            assert not lab.image_canvas.find_withtag("silhouette_contour")
            allowed_tags = {
                "silhouette_circle_contour",
                "silhouette_circle",
                "silhouette_circle_center",
            }
            for item in silhouette_items:
                tags = set(lab.image_canvas.gettags(item))
                assert tags & allowed_tags

            for _ in range(3):
                lab._on_canvas_mouse_wheel(  # pylint: disable=protected-access
                    SimpleNamespace(
                        delta=120,
                        num=None,
                        x=lab.image_canvas.winfo_width() // 2,
                        y=lab.image_canvas.winfo_height() // 2,
                    )
                )
            lab.image_canvas.xview_moveto(0.2)
            lab.image_canvas.yview_moveto(0.2)
            before_view = (lab.image_canvas.xview(), lab.image_canvas.yview())
            lab.silhouette_sensitivity_var.set(0.7)
            lab._on_silhouette_sensitivity_changed("0.7")  # pylint: disable=protected-access
            after_view = (lab.image_canvas.xview(), lab.image_canvas.yview())
            assert lab._pending_recognition_after_id is not None  # pylint: disable=protected-access
            for before_axis, after_axis in zip(before_view, after_view):
                assert after_axis[0] == pytest.approx(before_axis[0])
                assert after_axis[1] == pytest.approx(before_axis[1])
        finally:
            lab.destroy()
    finally:
        root.destroy()


def test_rectangle_overlay_uses_distinct_color_for_bright_silhouette_rectangle():
    root = _make_root()
    try:
        lab = VisionRecognitionLab(root, image_root=STANDARD_POSITION_IMAGE_ROOT)
        try:
            lab.select_position("1.1")
            lab.update_idletasks()
            lab._recognition_result = VisionRecognitionResult(  # pylint: disable=protected-access
                algorithm_name="test",
                display_name="test",
                lines=(),
                intersections=(),
                circles=(),
                rectangles=(
                    VisionRectangle(
                        x1=100,
                        y1=100,
                        x2=220,
                        y2=170,
                        missing_side=None,
                        score=1.0,
                        label="rectangle",
                    ),
                    VisionRectangle(
                        x1=260,
                        y1=100,
                        x2=380,
                        y2=170,
                        missing_side=None,
                        score=1.0,
                        label="bright silhouette",
                    ),
                ),
                semicircles=(),
                silhouettes=(),
                message="test",
            )
            lab._render_current_image()  # pylint: disable=protected-access

            edge_items = lab.image_canvas.find_withtag("edge_rectangle")
            bright_items = lab.image_canvas.find_withtag("bright_rectangle")

            assert edge_items
            assert bright_items
            assert {
                lab.image_canvas.itemcget(item, "fill")
                for item in edge_items
            } == {EDGE_RECTANGLE_OVERLAY_COLOR}
            assert {
                lab.image_canvas.itemcget(item, "fill")
                for item in bright_items
            } == {BRIGHT_RECTANGLE_OVERLAY_COLOR}
            assert lab.edge_rectangle_legend_swatch.cget("background") == EDGE_RECTANGLE_OVERLAY_COLOR
            assert lab.bright_rectangle_legend_swatch.cget("background") == BRIGHT_RECTANGLE_OVERLAY_COLOR
            assert not lab.recognition_legend_frame.winfo_ismapped()
            assert lab.recognition_legend_toggle_button.cget("text") == "Legend"
            lab._toggle_recognition_legend()  # pylint: disable=protected-access
            lab.update_idletasks()
            assert lab.recognition_legend_frame.winfo_ismapped()
            assert lab.recognition_legend_toggle_button.cget("text") == "Hide"
            lab._toggle_recognition_legend()  # pylint: disable=protected-access
            lab.update_idletasks()
            assert not lab.recognition_legend_frame.winfo_ismapped()
            assert lab.recognition_legend_toggle_button.cget("text") == "Legend"
        finally:
            lab.destroy()
    finally:
        root.destroy()
