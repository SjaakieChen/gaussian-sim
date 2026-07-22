import json
import math
from pathlib import Path

import pytest

import vision_scoring
from vision_scoring import (
    capture_run_from_image,
    capture_standard_from_image,
    DEFAULT_MAX_ABS_XY_ERROR_UM,
    DEFAULT_MAX_DISTANCE_ERROR_UM,
    DEFAULT_MAX_SHAPE_ERROR_PX,
    discover_v4_scoreable_captures,
    save_standard_session_file,
    score_against_standard_file,
    score_collection_payload,
    score_session_payloads,
    standard_baseline_path,
)


ROOT = Path(__file__).resolve().parents[1]
V4_STANDARD_POSITIONS = ROOT / "Standard position images" / "v4" / "standard_positions.json"


def _session_payload(
    *,
    image_name: str = "1.1.1.PNG",
    origin_x_px: float = 100.0,
    origin_y_px: float = 200.0,
    edge_x_um: float = -1500.0,
    edge_y_um: float = 25.0,
    edge_distance_um: float = 1500.208318,
    extra_x_um: float = 1312.5,
    extra_y_um: float = 50.0,
    extra_distance_um: float = 1313.452,
) -> dict:
    origin_circle = {
        "selection_index": 1,
        "roi_index": 2,
        "source": "circle",
        "center_px": {
            "x": origin_x_px,
            "y": origin_y_px,
            "radius": 18.0,
        },
        "relative_um": {
            "x": 0.0,
            "y": 0.0,
            "dx": 0.0,
            "dy": 0.0,
            "distance": 0.0,
        },
        "x_um": 0.0,
        "y_um": 0.0,
        "distance_um": 0.0,
    }
    extra_circle = {
        "selection_index": 2,
        "roi_index": 3,
        "source": "circle",
        "center_px": {
            "x": 205.0,
            "y": 204.0,
            "radius": 18.0,
        },
        "relative_um": {
            "x": extra_x_um,
            "y": extra_y_um,
            "dx": extra_x_um,
            "dy": extra_y_um,
            "distance": extra_distance_um,
        },
        "x_um": extra_x_um,
        "y_um": extra_y_um,
        "distance_um": extra_distance_um,
    }
    return {
        "schema_version": 3,
        "image_path": f"C:/captures/{image_name}",
        "relative_measurement": {
            "origin": "first_selected_circle_center",
            "origin_circle": origin_circle,
            "edge_midpoint_relative_um": {
                "x": edge_x_um,
                "y": edge_y_um,
                "dx": edge_x_um,
                "dy": edge_y_um,
                "distance": edge_distance_um,
            },
            "circles": [origin_circle, extra_circle],
        },
    }


def _selected_shape_payload(
    *,
    image_name: str = "1.1.1.PNG",
    ball_x: float = 100.0,
    ball_y: float = 200.0,
    ball_radius: float = 18.0,
    rect_dx: float = 0.0,
    rect_dy: float = 0.0,
) -> dict:
    return {
        "schema_version": 3,
        "image_path": f"C:/captures/{image_name}",
        "relative_measurement": None,
        "selected_recognition": {
            "roi_1": [
                {
                    "roi_index": 1,
                    "roi": {"kind": "circle", "x1": 80, "y1": 180, "x2": 120, "y2": 220},
                    "shape_kind": "circle",
                    "source": "circle",
                    "shape": {
                        "x": ball_x,
                        "y": ball_y,
                        "radius": ball_radius,
                        "score": 0.95,
                        "label": "circle",
                    },
                }
            ],
            "roi_2": [
                {
                    "roi_index": 2,
                    "roi": {"kind": "rectangle", "x1": 10, "y1": 20, "x2": 130, "y2": 90},
                    "shape_kind": "rectangle",
                    "source": "rectangle",
                    "shape": {
                        "x1": 20.0 + rect_dx,
                        "y1": 30.0 + rect_dy,
                        "x2": 120.0 + rect_dx,
                        "y2": 80.0 + rect_dy,
                        "missing_side": None,
                        "score": 0.9,
                        "label": "rectangle",
                        "corners": [
                            {"x": 20.0 + rect_dx, "y": 30.0 + rect_dy},
                            {"x": 120.0 + rect_dx, "y": 30.0 + rect_dy},
                            {"x": 120.0 + rect_dx, "y": 80.0 + rect_dy},
                            {"x": 20.0 + rect_dx, "y": 80.0 + rect_dy},
                        ],
                    },
                }
            ],
        },
    }


def test_discover_v4_scoreable_captures_includes_dot_one_images_and_4_6_2():
    captures = discover_v4_scoreable_captures(V4_STANDARD_POSITIONS)

    assert len(captures) == 15
    assert [capture.capture_id for capture in captures] == [
        "1.1.1",
        "2.1.1",
        "2.2.1",
        "2.3.1",
        "2.4.1",
        "2.5.1",
        "2.6.1",
        "3.0.1",
        "4.1.1",
        "4.2.1",
        "4.3.1",
        "4.4.1",
        "4.5.1",
        "4.6.1",
        "4.6.2",
    ]
    assert captures[-1].position_id == "4.6.2"
    assert captures[-1].image_rel_path == "newhead/4.6.2.PNG"


def test_identical_session_payloads_score_zero_error_and_pass():
    baseline = _session_payload()
    candidate = _session_payload()

    score = score_session_payloads(baseline, candidate, capture_id="1.1.1")

    assert score["ok"] is True
    assert score["passed"] is True
    assert score["metrics"]["origin_center_error_px"] == pytest.approx(0.0)
    assert score["metrics"]["max_abs_xy_error_um"] == pytest.approx(0.0)
    assert score["metrics"]["rms_xy_error_um"] == pytest.approx(0.0)
    assert score["metrics"]["max_distance_error_um"] == pytest.approx(0.0)
    assert score["thresholds"] == {
        "max_abs_xy_error_um": DEFAULT_MAX_ABS_XY_ERROR_UM,
        "max_distance_error_um": DEFAULT_MAX_DISTANCE_ERROR_UM,
        "max_shape_error_px": DEFAULT_MAX_SHAPE_ERROR_PX,
    }


def test_shifted_candidate_scores_signed_absolute_and_threshold_errors():
    baseline = _session_payload()
    candidate = _session_payload(
        origin_x_px=104.0,
        origin_y_px=195.0,
        edge_x_um=-1498.0,
        edge_y_um=22.0,
        edge_distance_um=1504.208318,
        extra_x_um=1313.5,
        extra_y_um=56.0,
        extra_distance_um=1320.452,
    )

    score = score_session_payloads(baseline, candidate, capture_id="1.1.1")

    assert score["ok"] is True
    assert score["passed"] is False
    assert score["status"] == "failed threshold"
    relative = score["comparisons"]["relative_measurement"]
    assert relative["origin_circle_center_px"]["signed_error"] == {
        "x": pytest.approx(4.0),
        "y": pytest.approx(-5.0),
    }
    assert score["metrics"]["origin_center_error_px"] == pytest.approx(math.hypot(4.0, -5.0))
    assert relative["edge_midpoint_relative_um"]["signed_error"] == {
        "x": pytest.approx(2.0),
        "y": pytest.approx(-3.0),
        "distance": pytest.approx(4.0),
    }
    extra = relative["additional_circles_relative_um"][0]
    assert extra["signed_error"] == {
        "x": pytest.approx(1.0),
        "y": pytest.approx(6.0),
        "distance": pytest.approx(7.0),
    }
    assert score["metrics"]["max_abs_xy_error_um"] == pytest.approx(6.0)
    assert score["metrics"]["rms_xy_error_um"] == pytest.approx(math.sqrt(12.5))
    assert score["metrics"]["max_distance_error_um"] == pytest.approx(7.0)


def test_missing_relative_measurement_fails_closed():
    baseline = _session_payload()
    candidate = _session_payload()
    del candidate["relative_measurement"]

    score = score_session_payloads(baseline, candidate, capture_id="1.1.1")

    assert score["ok"] is False
    assert score["passed"] is False
    assert "candidate relative_measurement is missing" in score["status"]
    assert score["metrics"] == {}


def test_selected_ball_and_rectangle_payloads_score_without_relative_measurement():
    baseline = _selected_shape_payload()
    candidate = _selected_shape_payload(
        ball_x=103.0,
        ball_y=196.0,
        ball_radius=19.0,
        rect_dx=2.0,
        rect_dy=1.0,
    )

    score = score_session_payloads(baseline, candidate, capture_id="1.1.1")

    assert score["ok"] is True
    assert score["passed"] is True
    assert "relative_measurement" not in score["comparisons"]
    assert score["comparisons"]["selected_shapes"]["balls"][0]["signed_error"] == {
        "x": pytest.approx(3.0),
        "y": pytest.approx(-4.0),
        "radius": pytest.approx(1.0),
    }
    assert score["comparisons"]["selected_shapes"]["rectangles"][0]["center_signed_error"] == {
        "x": pytest.approx(2.0),
        "y": pytest.approx(1.0),
    }
    assert score["comparisons"]["selected_shapes"]["rectangles"][0]["corners"][0]["signed_error"] == {
        "x": pytest.approx(2.0),
        "y": pytest.approx(1.0),
    }
    assert score["metrics"]["max_shape_error_px"] == pytest.approx(4.0)
    assert score["metrics"]["max_ball_center_error_px"] == pytest.approx(5.0)
    assert score["metrics"]["max_rectangle_center_error_px"] == pytest.approx(math.sqrt(5.0))
    assert score["metrics"]["max_rectangle_corner_error_px"] == pytest.approx(math.sqrt(5.0))


def test_selected_rectangle_corner_error_scores_even_when_bbox_is_unchanged():
    baseline = _selected_shape_payload()
    candidate = _selected_shape_payload()
    candidate_rectangle = candidate["selected_recognition"]["roi_2"][0]["shape"]
    candidate_rectangle["corners"][2]["x"] += 7.0

    score = score_session_payloads(
        baseline,
        candidate,
        capture_id="1.1.1",
        max_shape_error_px=5.0,
    )

    assert score["ok"] is True
    assert score["passed"] is False
    assert score["comparisons"]["selected_shapes"]["rectangles"][0]["corners"][2]["signed_error"] == {
        "x": pytest.approx(7.0),
        "y": pytest.approx(0.0),
    }
    assert score["metrics"]["max_shape_error_px"] == pytest.approx(7.0)
    assert score["metrics"]["max_rectangle_corner_error_px"] == pytest.approx(7.0)


def test_selected_shape_payload_fails_threshold_when_pixel_error_is_too_large():
    score = score_session_payloads(
        _selected_shape_payload(),
        _selected_shape_payload(ball_x=106.0),
        capture_id="1.1.1",
        max_shape_error_px=5.0,
    )

    assert score["ok"] is True
    assert score["passed"] is False
    assert score["metrics"]["max_shape_error_px"] == pytest.approx(6.0)


def test_selected_shape_count_mismatch_fails_closed():
    baseline = _selected_shape_payload()
    candidate = _selected_shape_payload()
    candidate["selected_recognition"]["roi_2"] = []

    score = score_session_payloads(baseline, candidate, capture_id="1.1.1")

    assert score["ok"] is False
    assert score["passed"] is False
    assert "selected rectangle counts differ" in score["status"]


def test_score_collection_payload_summarizes_compact_rows():
    passing = score_session_payloads(_session_payload(), _session_payload(), capture_id="1.1.1")
    failing = score_session_payloads(_session_payload(), {}, capture_id="2.1.1")

    payload = score_collection_payload([passing, failing])

    assert payload["summary"]["total"] == 2
    assert payload["summary"]["passed"] == 1
    assert payload["summary"]["failed"] == 1
    assert payload["summary_rows"][0]["capture_id"] == "1.1.1"
    assert payload["summary_rows"][1]["status"].startswith("scoring failed closed")


def test_save_standard_and_score_against_it(tmp_path):
    baseline_dir = tmp_path / "standards"
    standard_result = tmp_path / "reviewed_1.1.1.json"
    candidate_result = tmp_path / "candidate_1.1.1.json"
    standard_result.write_text(json.dumps(_session_payload()), encoding="utf-8")
    candidate_result.write_text(
        json.dumps(
            _session_payload(
                edge_x_um=-1499.0,
                edge_y_um=27.0,
                edge_distance_um=1501.208318,
                extra_x_um=1311.5,
                extra_y_um=49.0,
                extra_distance_um=1314.452,
            )
        ),
        encoding="utf-8",
    )

    saved = save_standard_session_file(
        standard_result,
        baseline_dir=baseline_dir,
        standard_positions_path=V4_STANDARD_POSITIONS,
    )
    score = score_against_standard_file(
        candidate_result,
        baseline_dir=baseline_dir,
        standard_positions_path=V4_STANDARD_POSITIONS,
    )

    assert saved["ok"] is True
    assert saved["capture_id"] == "1.1.1"
    assert Path(saved["baseline_path"]) == standard_baseline_path("1.1.1", baseline_dir)
    assert Path(saved["baseline_path"]).is_file()
    assert score["ok"] is True
    assert score["passed"] is True
    assert score["capture_id"] == "1.1.1"
    assert score["metrics"]["max_abs_xy_error_um"] == pytest.approx(2.0)
    assert score["metrics"]["max_distance_error_um"] == pytest.approx(1.0)


def test_save_standard_accepts_selected_shapes_without_relative_measurement(tmp_path):
    baseline_dir = tmp_path / "standards"
    standard_result = tmp_path / "reviewed_1.1.1.json"
    standard_result.write_text(json.dumps(_selected_shape_payload()), encoding="utf-8")

    saved = save_standard_session_file(
        standard_result,
        baseline_dir=baseline_dir,
        standard_positions_path=V4_STANDARD_POSITIONS,
    )

    assert saved["ok"] is True
    assert Path(saved["baseline_path"]).is_file()
    payload = json.loads(Path(saved["baseline_path"]).read_text(encoding="utf-8"))
    assert payload["relative_measurement"] is None
    assert payload["selected_recognition"]["roi_1"][0]["shape_kind"] == "circle"


def test_score_against_standard_fails_closed_when_standard_is_missing(tmp_path):
    candidate_result = tmp_path / "candidate_4.6.2.json"
    candidate_result.write_text(json.dumps(_session_payload(image_name="4.6.2.PNG")), encoding="utf-8")

    score = score_against_standard_file(
        candidate_result,
        baseline_dir=tmp_path / "standards",
        standard_positions_path=V4_STANDARD_POSITIONS,
    )

    assert score["ok"] is False
    assert score["passed"] is False
    assert "no standard saved for 4.6.2" in score["status"]


def test_capture_standard_opens_session_and_saves_standard(tmp_path, monkeypatch):
    baseline_dir = tmp_path / "standards"
    result_output = tmp_path / "results" / "1.1.1_standard.json"

    def fake_session(image_path, *, result_output_path):
        payload = _session_payload(image_name=Path(image_path).name)
        Path(result_output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(result_output_path).write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(vision_scoring, "run_vision_session_for_image", fake_session)

    saved = capture_standard_from_image(
        "Standard position images/v4/newhead/1.1.1.PNG",
        result_output_path=result_output,
        baseline_dir=baseline_dir,
        standard_positions_path=V4_STANDARD_POSITIONS,
    )

    assert saved["ok"] is True
    assert saved["capture_id"] == "1.1.1"
    assert Path(saved["vision_result_path"]) == result_output
    assert standard_baseline_path("1.1.1", baseline_dir).is_file()


def test_capture_run_opens_session_and_scores_against_standard(tmp_path, monkeypatch):
    baseline_dir = tmp_path / "standards"
    standard_path = standard_baseline_path("1.1.1", baseline_dir)
    standard_path.parent.mkdir(parents=True)
    standard_path.write_text(json.dumps(_session_payload()), encoding="utf-8")
    result_output = tmp_path / "results" / "1.1.1_candidate.json"

    def fake_session(image_path, *, result_output_path):
        payload = _session_payload(image_name=Path(image_path).name, edge_x_um=-1499.0)
        Path(result_output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(result_output_path).write_text(json.dumps(payload), encoding="utf-8")
        return payload

    monkeypatch.setattr(vision_scoring, "run_vision_session_for_image", fake_session)

    score = capture_run_from_image(
        "Standard position images/v4/newhead/1.1.1.PNG",
        result_output_path=result_output,
        baseline_dir=baseline_dir,
        standard_positions_path=V4_STANDARD_POSITIONS,
    )

    assert score["ok"] is True
    assert score["passed"] is True
    assert Path(score["vision_result_path"]) == result_output
    assert score["metrics"]["max_abs_xy_error_um"] == pytest.approx(1.0)
