import json
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

from vision_recognition_lab import (
    BRIGHT_RECTANGLE_OVERLAY_COLOR,
    EDGE_RECTANGLE_OVERLAY_COLOR,
    ROI_REQUIRED_MESSAGE,
    VisionRectangle,
    VisionROI,
    VisionRecognitionLab,
    VisionRecognitionLabStep,
    VisionRecognitionResult,
    VISION_RECOGNITION_LAB_TITLE,
    bright_rectangle_sensitivity_from_scale_x,
    downsample_for_recognition,
    fit_subsample_factor,
    geometry_sensitivity_from_scale_x,
    load_standard_position_library,
    normalize_standard_position_id,
    read_grayscale_image,
    recognize_shapes,
    silhouette_sensitivity_from_scale_x,
    standard_position_sort_key,
    vision_roi_to_dict,
)


ROOT = Path(__file__).resolve().parents[1]
STANDARD_POSITION_IMAGE_ROOT = ROOT / "Standard position images"
STANDARD_POSITIONS = STANDARD_POSITION_IMAGE_ROOT / "v2" / "standard_positions.json"


def _make_root():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk is not available: {exc}")
    root.withdraw()
    return root


def test_standard_position_json_uses_semantic_ids_and_matching_image_names():
    data = json.loads(STANDARD_POSITIONS.read_text(encoding="utf-8"))

    assert [position["id"] for position in data["positions"]] == [
        "1.0.0",
        "2.0.0",
        "3.0.0",
        "4.0.0",
        "5.0.0",
        "6.0.0",
    ]
    assert {
        position["id"]: position["captured_image"]
        for position in data["positions"]
        if position["captured_image"]
    } == {
        "3.0.0": "3.0.0.png",
        "4.0.0": "4.0.0.png",
        "5.0.0": "5.0.0.png",
        "6.0.0": "6.0.0.png",
    }


def test_standard_position_library_groups_images_by_position_across_batches():
    library = load_standard_position_library(STANDARD_POSITION_IMAGE_ROOT)

    assert [position.id for position in library.positions] == [
        "1.0.0",
        "2.0.0",
        "3.0.0",
        "4.0.0",
        "5.0.0",
        "6.0.0",
    ]
    assert library.images_for_position("1.0.0") == ()
    position_images = library.images_for_position("3.0.0")
    assert [(image.batch, image.path.name) for image in position_images] == [("v2", "3.0.0.png")]


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


def test_dark_silhouette_finds_probe_shape_on_standard_position_6():
    image = read_grayscale_image(STANDARD_POSITION_IMAGE_ROOT / "v2" / "6.0.0.png")

    wrong_roi_result = recognize_shapes(
        image,
        "dark_adaptive",
        (VisionROI("semicircle", 620, 220, 1040, 720, orientation="down"),),
        silhouette_algorithm_name="dark_silhouette",
    )
    assert wrong_roi_result.silhouettes == ()

    result = recognize_shapes(
        image,
        "dark_adaptive",
        (VisionROI("silhouette", 620, 220, 1040, 720),),
        silhouette_algorithm_name="dark_silhouette",
    )

    assert result.silhouettes
    assert result.algorithm_name == "dark_adaptive+dark_silhouette"
    silhouette = result.silhouettes[0]
    assert abs(silhouette.x - 840) <= 12
    assert abs(silhouette.y - 400) <= 12
    assert 620 <= silhouette.x1 <= 660
    assert 210 <= silhouette.y1 <= 240
    assert 1020 <= silhouette.x2 <= 1050
    assert 640 <= silhouette.y2 <= 670
    assert silhouette.area > 100_000
    assert len(silhouette.contour_segments) > 100
    assert len(silhouette.circle_contour_segments) > 100
    assert silhouette.circle_x is not None
    assert silhouette.circle_y is not None
    assert silhouette.circle_radius is not None
    assert abs(silhouette.circle_x - 837) <= 15
    assert abs(silhouette.circle_y - 523) <= 25
    assert abs(silhouette.circle_radius - 125) <= 25


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
            assert lab.selected_position_id() == "6.0.0"
            assert lab.title() == VISION_RECOGNITION_LAB_TITLE
            assert lab.title().endswith("v3")
            assert [(image.batch, image.path.name) for image in lab.current_images()] == [
                ("v2", "6.0.0.png")
            ]
            lab.select_position("3.0.0")
            lab.update_idletasks()

            assert lab.selected_position_id() == "3.0.0"
            assert [(image.batch, image.path.name) for image in lab.current_images()] == [
                ("v2", "3.0.0.png")
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
            assert lab.selected_recognizer_name() == "opencv_hough"
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
                "dark_adaptive",
                "opencv_adaptive_dark",
                "opencv_hough",
                "skimage_hough",
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
            lab.recognizer_var.set("Dark adaptive")
            lab._on_recognizer_selected()  # pylint: disable=protected-access
            assert not managed(lab.geometry_sensitivity_label)  # pylint: disable=protected-access
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
            lab.select_position("6.0.0")
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
            lab.select_position("6.0.0")
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
        finally:
            lab.destroy()
    finally:
        root.destroy()
