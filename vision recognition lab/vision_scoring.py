"""Offline scoring for reviewed vision recognition lab session payloads."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


SCORING_SCHEMA_VERSION = 1
DEFAULT_MAX_ABS_XY_ERROR_UM = 5.0
DEFAULT_MAX_DISTANCE_ERROR_UM = 5.0
DEFAULT_MAX_SHAPE_ERROR_PX = 5.0
DEFAULT_STANDARD_POSITIONS_PATH = (
    Path(__file__).resolve().parents[1] / "Standard position images" / "v4" / "standard_positions.json"
)
DEFAULT_STANDARD_BASELINE_DIR = (
    Path(__file__).resolve().parents[1] / "Standard position images" / "v4" / "vision_baselines"
)
DEFAULT_VISION_RESULT_DIR = (
    Path(__file__).resolve().parents[1] / "Standard position images" / "v4" / "vision_results"
)
EXTRA_V4_SCOREABLE_CAPTURE_IDS = frozenset({"4.6.2"})
CSV_FIELDNAMES = (
    "capture_id",
    "position_id",
    "image",
    "ok",
    "passed",
    "status",
    "max_abs_xy_error_um",
    "rms_xy_error_um",
    "max_distance_error_um",
    "max_shape_error_px",
    "max_ball_center_error_px",
    "max_rectangle_center_error_px",
    "max_rectangle_corner_error_px",
    "origin_center_error_px",
    "baseline_path",
    "candidate_path",
)


@dataclass(frozen=True)
class ScoreableCapture:
    """A v4 standard-position image that should participate in offline scoring."""

    position_id: str
    label: str
    image_rel_path: str
    image_path: Path
    capture_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "label": self.label,
            "image_rel_path": self.image_rel_path,
            "image_path": str(self.image_path),
            "capture_id": self.capture_id,
        }


def discover_v4_scoreable_captures(
    standard_positions_path: str | Path = DEFAULT_STANDARD_POSITIONS_PATH,
) -> tuple[ScoreableCapture, ...]:
    """Return v4 `.1.PNG` captures plus the extra `4.6.2.PNG` settings capture."""

    source_path = Path(standard_positions_path)
    data = json.loads(source_path.read_text(encoding="utf-8"))
    image_root = source_path.parent
    captures: list[ScoreableCapture] = []
    for raw_position in data.get("positions", ()):
        position_id = str(raw_position.get("id", "")).strip()
        label = str(raw_position.get("label") or "").strip()
        for raw_image_path in raw_position.get("captured_images") or ():
            image_rel_path = str(raw_image_path).replace("\\", "/")
            capture_id = _path_stem(image_rel_path)
            if not is_scoreable_v4_capture_id(capture_id):
                continue
            captures.append(
                ScoreableCapture(
                    position_id=position_id,
                    label=label,
                    image_rel_path=image_rel_path,
                    image_path=image_root / image_rel_path,
                    capture_id=capture_id,
                )
            )
    captures.sort(key=lambda capture: _numeric_id_sort_key(capture.capture_id))
    return tuple(captures)


def is_scoreable_v4_capture_id(capture_id: str) -> bool:
    normalized = str(capture_id).strip()
    return normalized.endswith(".1") or normalized in EXTRA_V4_SCOREABLE_CAPTURE_IDS


def save_standard_session_file(
    result_path: str | Path,
    *,
    baseline_dir: str | Path = DEFAULT_STANDARD_BASELINE_DIR,
    capture_id: str | None = None,
    standard_positions_path: str | Path = DEFAULT_STANDARD_POSITIONS_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save one reviewed vision-lab result as the official baseline for its capture."""

    source_path = Path(result_path)
    payload = _read_json(source_path)
    return save_standard_session_payload(
        payload,
        baseline_dir=baseline_dir,
        capture_id=capture_id,
        standard_positions_path=standard_positions_path,
        overwrite=overwrite,
        source_result_path=source_path,
    )


def save_standard_session_payload(
    payload: dict[str, Any],
    *,
    baseline_dir: str | Path = DEFAULT_STANDARD_BASELINE_DIR,
    capture_id: str | None = None,
    standard_positions_path: str | Path = DEFAULT_STANDARD_POSITIONS_PATH,
    overwrite: bool = False,
    source_result_path: str | Path | None = None,
) -> dict[str, Any]:
    """Save one reviewed vision-lab session payload as the official baseline."""

    resolved_capture_id = _resolve_capture_id(payload, capture_id)
    capture = _required_scoreable_capture(resolved_capture_id, standard_positions_path)
    output_path = standard_baseline_path(capture.capture_id, baseline_dir)
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"standard already exists for {capture.capture_id}: {output_path}")
    payload = dict(payload)
    payload["standard_capture_id"] = capture.capture_id
    payload["standard_position_id"] = capture.position_id
    payload["standard_image_rel_path"] = capture.image_rel_path
    _write_json(output_path, payload)
    return {
        "schema_version": SCORING_SCHEMA_VERSION,
        "kind": "vision_standard_saved",
        "ok": True,
        "capture_id": capture.capture_id,
        "position_id": capture.position_id,
        "image_rel_path": capture.image_rel_path,
        "source_result_path": str(source_result_path) if source_result_path is not None else None,
        "baseline_path": str(output_path),
        "overwrite": overwrite,
        "status": f"standard saved for {capture.capture_id}",
    }


def capture_standard_from_image(
    image_path: str | Path,
    *,
    result_output_path: str | Path | None = None,
    baseline_dir: str | Path = DEFAULT_STANDARD_BASELINE_DIR,
    capture_id: str | None = None,
    standard_positions_path: str | Path = DEFAULT_STANDARD_POSITIONS_PATH,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Open the recognition UI for an image, then save the reviewed result as the standard."""

    resolved_capture_id = _resolve_capture_id({"image_path": str(image_path)}, capture_id)
    output_path = Path(result_output_path) if result_output_path is not None else _default_result_output_path(
        resolved_capture_id,
        "standard",
    )
    payload = run_vision_session_for_image(image_path, result_output_path=output_path)
    saved = save_standard_session_payload(
        payload,
        baseline_dir=baseline_dir,
        capture_id=resolved_capture_id,
        standard_positions_path=standard_positions_path,
        overwrite=overwrite,
        source_result_path=output_path,
    )
    saved["vision_result_path"] = str(output_path)
    return saved


def capture_run_from_image(
    image_path: str | Path,
    *,
    result_output_path: str | Path | None = None,
    baseline_dir: str | Path = DEFAULT_STANDARD_BASELINE_DIR,
    capture_id: str | None = None,
    standard_positions_path: str | Path = DEFAULT_STANDARD_POSITIONS_PATH,
    max_abs_xy_error_um: float = DEFAULT_MAX_ABS_XY_ERROR_UM,
    max_distance_error_um: float = DEFAULT_MAX_DISTANCE_ERROR_UM,
    max_shape_error_px: float = DEFAULT_MAX_SHAPE_ERROR_PX,
) -> dict[str, Any]:
    """Open the recognition UI for an image, then score it against the saved standard."""

    resolved_capture_id = _resolve_capture_id({"image_path": str(image_path)}, capture_id)
    output_path = Path(result_output_path) if result_output_path is not None else _default_result_output_path(
        resolved_capture_id,
        "candidate",
    )
    run_vision_session_for_image(image_path, result_output_path=output_path)
    score = score_against_standard_file(
        output_path,
        baseline_dir=baseline_dir,
        capture_id=resolved_capture_id,
        standard_positions_path=standard_positions_path,
        max_abs_xy_error_um=max_abs_xy_error_um,
        max_distance_error_um=max_distance_error_um,
        max_shape_error_px=max_shape_error_px,
    )
    score["vision_result_path"] = str(output_path)
    return score


def run_vision_session_for_image(
    image_path: str | Path,
    *,
    result_output_path: str | Path,
) -> dict[str, Any]:
    from vision_recognition_lab import run_vision_recognition_lab_session

    return run_vision_recognition_lab_session(
        image_path,
        result_output_path=result_output_path,
    )


def score_against_standard_file(
    candidate_path: str | Path,
    *,
    baseline_dir: str | Path = DEFAULT_STANDARD_BASELINE_DIR,
    capture_id: str | None = None,
    standard_positions_path: str | Path = DEFAULT_STANDARD_POSITIONS_PATH,
    max_abs_xy_error_um: float = DEFAULT_MAX_ABS_XY_ERROR_UM,
    max_distance_error_um: float = DEFAULT_MAX_DISTANCE_ERROR_UM,
    max_shape_error_px: float = DEFAULT_MAX_SHAPE_ERROR_PX,
) -> dict[str, Any]:
    candidate_file = Path(candidate_path)
    candidate_payload = _read_json(candidate_file)
    resolved_capture_id = _resolve_capture_id(candidate_payload, capture_id)
    capture = _required_scoreable_capture(resolved_capture_id, standard_positions_path)
    baseline_file = standard_baseline_path(capture.capture_id, baseline_dir)
    if not baseline_file.is_file():
        score = _score_shell(
            capture_id=capture.capture_id,
            position_id=capture.position_id,
            image_rel_path=capture.image_rel_path,
            baseline_payload={},
            candidate_payload=candidate_payload,
            baseline_path=baseline_file,
            candidate_path=candidate_file,
            max_abs_xy_error_um=max_abs_xy_error_um,
            max_distance_error_um=max_distance_error_um,
            max_shape_error_px=max_shape_error_px,
        )
        score.update(
            {
                "ok": False,
                "passed": False,
                "status": f"scoring failed closed: no standard saved for {capture.capture_id}",
                "comparisons": {},
                "metrics": {},
            }
        )
        return score
    return score_session_files(
        baseline_file,
        candidate_file,
        capture_id=capture.capture_id,
        position_id=capture.position_id,
        image_rel_path=capture.image_rel_path,
        max_abs_xy_error_um=max_abs_xy_error_um,
        max_distance_error_um=max_distance_error_um,
        max_shape_error_px=max_shape_error_px,
    )


def standard_baseline_path(capture_id: str, baseline_dir: str | Path = DEFAULT_STANDARD_BASELINE_DIR) -> Path:
    return Path(baseline_dir) / f"{capture_id}.json"


def _default_result_output_path(capture_id: str, result_kind: str) -> Path:
    return DEFAULT_VISION_RESULT_DIR / f"{capture_id}_{result_kind}.json"


def score_session_files(
    baseline_path: str | Path,
    candidate_path: str | Path,
    *,
    capture_id: str | None = None,
    position_id: str | None = None,
    image_rel_path: str | None = None,
    max_abs_xy_error_um: float = DEFAULT_MAX_ABS_XY_ERROR_UM,
    max_distance_error_um: float = DEFAULT_MAX_DISTANCE_ERROR_UM,
    max_shape_error_px: float = DEFAULT_MAX_SHAPE_ERROR_PX,
) -> dict[str, Any]:
    baseline_file = Path(baseline_path)
    candidate_file = Path(candidate_path)
    return score_session_payloads(
        _read_json(baseline_file),
        _read_json(candidate_file),
        capture_id=capture_id,
        position_id=position_id,
        image_rel_path=image_rel_path,
        baseline_path=baseline_file,
        candidate_path=candidate_file,
        max_abs_xy_error_um=max_abs_xy_error_um,
        max_distance_error_um=max_distance_error_um,
        max_shape_error_px=max_shape_error_px,
    )


def score_session_payloads(
    baseline_payload: dict[str, Any],
    candidate_payload: dict[str, Any],
    *,
    capture_id: str | None = None,
    position_id: str | None = None,
    image_rel_path: str | None = None,
    baseline_path: str | Path | None = None,
    candidate_path: str | Path | None = None,
    max_abs_xy_error_um: float = DEFAULT_MAX_ABS_XY_ERROR_UM,
    max_distance_error_um: float = DEFAULT_MAX_DISTANCE_ERROR_UM,
    max_shape_error_px: float = DEFAULT_MAX_SHAPE_ERROR_PX,
) -> dict[str, Any]:
    """Score one candidate vision session against one reviewed baseline session."""

    resolved_capture_id = capture_id or _payload_capture_id(candidate_payload) or _payload_capture_id(baseline_payload)
    base = _score_shell(
        capture_id=resolved_capture_id,
        position_id=position_id,
        image_rel_path=image_rel_path,
        baseline_payload=baseline_payload,
        candidate_payload=candidate_payload,
        baseline_path=baseline_path,
        candidate_path=candidate_path,
        max_abs_xy_error_um=max_abs_xy_error_um,
        max_distance_error_um=max_distance_error_um,
        max_shape_error_px=max_shape_error_px,
    )
    comparisons: dict[str, Any] = {}
    metrics: dict[str, Any] = {}
    errors: list[str] = []

    try:
        shape_comparisons, shape_metrics = compare_selected_shapes(baseline_payload, candidate_payload)
        comparisons["selected_shapes"] = shape_comparisons
        metrics.update(shape_metrics)
    except ValueError as exc:
        errors.append(str(exc))

    baseline_relative = baseline_payload.get("relative_measurement")
    candidate_relative = candidate_payload.get("relative_measurement")
    if isinstance(baseline_relative, dict) and isinstance(candidate_relative, dict):
        try:
            relative_comparisons, relative_metrics = compare_relative_measurements(baseline_relative, candidate_relative)
            comparisons["relative_measurement"] = relative_comparisons
            metrics.update(relative_metrics)
        except ValueError as exc:
            errors.append(str(exc))
    elif isinstance(baseline_relative, dict) != isinstance(candidate_relative, dict):
        missing_payload = "candidate" if isinstance(baseline_relative, dict) else "baseline"
        errors.append(f"{missing_payload} relative_measurement is missing")

    if not comparisons:
        base.update(
            {
                "ok": False,
                "passed": False,
                "status": f"scoring failed closed: {'; '.join(errors) or 'no comparable selected shapes'}",
                "comparisons": {},
                "metrics": {},
            }
        )
        return base

    passed = True
    if metrics.get("max_shape_error_px") is not None:
        passed = passed and metrics["max_shape_error_px"] <= max_shape_error_px
    if metrics.get("max_abs_xy_error_um") is not None:
        passed = passed and metrics["max_abs_xy_error_um"] <= max_abs_xy_error_um
    if metrics.get("max_distance_error_um") is not None:
        passed = passed and metrics["max_distance_error_um"] <= max_distance_error_um
    base.update(
        {
            "ok": True,
            "passed": passed,
            "status": "passed" if passed else "failed threshold",
            "comparisons": comparisons,
            "metrics": metrics,
        }
    )
    return base


def compare_selected_shapes(
    baseline_payload: dict[str, Any],
    candidate_payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline_shapes = _selected_comparable_shapes(baseline_payload, "baseline")
    candidate_shapes = _selected_comparable_shapes(candidate_payload, "candidate")
    if len(baseline_shapes["balls"]) != len(candidate_shapes["balls"]):
        raise ValueError(
            "baseline and candidate selected ball counts differ "
            f"({len(baseline_shapes['balls'])} != {len(candidate_shapes['balls'])})"
        )
    if len(baseline_shapes["rectangles"]) != len(candidate_shapes["rectangles"]):
        raise ValueError(
            "baseline and candidate selected rectangle counts differ "
            f"({len(baseline_shapes['rectangles'])} != {len(candidate_shapes['rectangles'])})"
        )
    if not baseline_shapes["balls"] and not baseline_shapes["rectangles"]:
        raise ValueError("no selected balls or rectangles to compare")

    ball_comparisons = [
        _compare_ball_shape(index, baseline_ball, candidate_ball)
        for index, (baseline_ball, candidate_ball) in enumerate(
            zip(baseline_shapes["balls"], candidate_shapes["balls"]),
            start=1,
        )
    ]
    rectangle_comparisons = [
        _compare_rectangle_shape(index, baseline_rectangle, candidate_rectangle)
        for index, (baseline_rectangle, candidate_rectangle) in enumerate(
            zip(baseline_shapes["rectangles"], candidate_shapes["rectangles"]),
            start=1,
        )
    ]

    coordinate_errors: list[float] = []
    ball_center_errors: list[float] = []
    ball_radius_errors: list[float] = []
    rectangle_center_errors: list[float] = []
    rectangle_corner_errors: list[float] = []
    rectangle_size_errors: list[float] = []
    for comparison in ball_comparisons:
        coordinate_errors.extend(
            [
                comparison["signed_error"]["x"],
                comparison["signed_error"]["y"],
                comparison["signed_error"]["radius"],
            ]
        )
        ball_center_errors.append(comparison["center_error_px"])
        ball_radius_errors.append(comparison["abs_error"]["radius"])
    for comparison in rectangle_comparisons:
        for corner in comparison["corners"]:
            coordinate_errors.extend([corner["signed_error"]["x"], corner["signed_error"]["y"]])
            rectangle_corner_errors.append(corner["distance_error_px"])
        rectangle_center_errors.append(comparison["center_error_px"])
        rectangle_size_errors.extend(
            [
                comparison["abs_error"]["width"],
                comparison["abs_error"]["height"],
            ]
        )

    metrics = {
        "max_shape_error_px": max((abs(value) for value in coordinate_errors), default=0.0),
        "rms_shape_error_px": _rms(coordinate_errors),
        "max_ball_center_error_px": max(ball_center_errors, default=0.0),
        "max_ball_radius_error_px": max(ball_radius_errors, default=0.0),
        "max_rectangle_center_error_px": max(rectangle_center_errors, default=0.0),
        "max_rectangle_corner_error_px": max(rectangle_corner_errors, default=0.0),
        "max_rectangle_size_error_px": max(rectangle_size_errors, default=0.0),
        "compared_ball_count": len(ball_comparisons),
        "compared_rectangle_count": len(rectangle_comparisons),
    }
    return (
        {
            "balls": ball_comparisons,
            "rectangles": rectangle_comparisons,
        },
        metrics,
    )


def compare_relative_measurements(
    baseline_relative: dict[str, Any],
    candidate_relative: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    baseline_origin = _required_mapping(baseline_relative.get("origin_circle"), "baseline origin_circle")
    candidate_origin = _required_mapping(candidate_relative.get("origin_circle"), "candidate origin_circle")
    origin_comparison = _compare_xy(
        "origin_circle_center_px",
        _center_px(baseline_origin, "baseline origin_circle"),
        _center_px(candidate_origin, "candidate origin_circle"),
    )

    edge_comparison = _compare_relative_um(
        "edge_midpoint_relative_um",
        _required_mapping(
            baseline_relative.get("edge_midpoint_relative_um"),
            "baseline edge_midpoint_relative_um",
        ),
        _required_mapping(
            candidate_relative.get("edge_midpoint_relative_um"),
            "candidate edge_midpoint_relative_um",
        ),
    )

    baseline_circles = _required_circle_list(baseline_relative, "baseline")
    candidate_circles = _required_circle_list(candidate_relative, "candidate")
    if len(baseline_circles) != len(candidate_circles):
        raise ValueError(
            "baseline and candidate selected circle counts differ "
            f"({len(baseline_circles)} != {len(candidate_circles)})"
        )

    additional_circle_comparisons = []
    for index, (baseline_circle, candidate_circle) in enumerate(
        zip(baseline_circles[1:], candidate_circles[1:]),
        start=2,
    ):
        additional_circle_comparisons.append(
            _compare_relative_um(
                "circle_relative_um",
                _circle_relative_um(baseline_circle, f"baseline circle {index}"),
                _circle_relative_um(candidate_circle, f"candidate circle {index}"),
                selection_index=index,
                baseline_selection_index=baseline_circle.get("selection_index", index),
                candidate_selection_index=candidate_circle.get("selection_index", index),
                baseline_roi_index=baseline_circle.get("roi_index"),
                candidate_roi_index=candidate_circle.get("roi_index"),
            )
        )

    xy_errors_um = [
        edge_comparison["signed_error"]["x"],
        edge_comparison["signed_error"]["y"],
    ]
    distance_errors_um = [edge_comparison["signed_error"]["distance"]]
    for comparison in additional_circle_comparisons:
        xy_errors_um.extend(
            [
                comparison["signed_error"]["x"],
                comparison["signed_error"]["y"],
            ]
        )
        distance_errors_um.append(comparison["signed_error"]["distance"])

    metrics = {
        "origin_center_error_px": origin_comparison["distance_error"],
        "max_origin_center_abs_xy_error_px": max(
            origin_comparison["abs_error"]["x"],
            origin_comparison["abs_error"]["y"],
        ),
        "max_abs_xy_error_um": max((abs(value) for value in xy_errors_um), default=0.0),
        "rms_xy_error_um": _rms(xy_errors_um),
        "max_distance_error_um": max((abs(value) for value in distance_errors_um), default=0.0),
        "compared_circle_count": len(baseline_circles),
        "additional_circle_count": max(len(baseline_circles) - 1, 0),
    }
    return (
        {
            "origin_circle_center_px": origin_comparison,
            "edge_midpoint_relative_um": edge_comparison,
            "additional_circles_relative_um": additional_circle_comparisons,
        },
        metrics,
    )


def score_session_folders(
    baseline_dir: str | Path,
    candidate_dir: str | Path,
    *,
    standard_positions_path: str | Path = DEFAULT_STANDARD_POSITIONS_PATH,
    max_abs_xy_error_um: float = DEFAULT_MAX_ABS_XY_ERROR_UM,
    max_distance_error_um: float = DEFAULT_MAX_DISTANCE_ERROR_UM,
    max_shape_error_px: float = DEFAULT_MAX_SHAPE_ERROR_PX,
) -> dict[str, Any]:
    baseline_files = collect_session_result_files(baseline_dir)
    candidate_files = collect_session_result_files(candidate_dir)
    scores: list[dict[str, Any]] = []
    for capture in discover_v4_scoreable_captures(standard_positions_path):
        baseline_path = baseline_files.get(capture.capture_id)
        candidate_path = candidate_files.get(capture.capture_id)
        if baseline_path is None or candidate_path is None:
            scores.append(
                _missing_file_score(
                    capture,
                    baseline_path=baseline_path,
                    candidate_path=candidate_path,
                    max_abs_xy_error_um=max_abs_xy_error_um,
                    max_distance_error_um=max_distance_error_um,
                    max_shape_error_px=max_shape_error_px,
                )
            )
            continue
        scores.append(
            score_session_files(
                baseline_path,
                candidate_path,
                capture_id=capture.capture_id,
                position_id=capture.position_id,
                image_rel_path=capture.image_rel_path,
                max_abs_xy_error_um=max_abs_xy_error_um,
                max_distance_error_um=max_distance_error_um,
                max_shape_error_px=max_shape_error_px,
            )
        )
    return score_collection_payload(scores)


def collect_session_result_files(folder: str | Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for path in sorted(Path(folder).glob("*.json")):
        try:
            payload = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        capture_id = _payload_capture_id(payload) or path.stem
        result[capture_id] = path
    return result


def score_collection_payload(scores: Sequence[dict[str, Any]]) -> dict[str, Any]:
    score_list = list(scores)
    return {
        "schema_version": SCORING_SCHEMA_VERSION,
        "kind": "vision_score_collection",
        "summary": summarize_scores(score_list),
        "scores": score_list,
        "summary_rows": compact_score_rows(score_list),
    }


def summarize_scores(scores: Sequence[dict[str, Any]]) -> dict[str, Any]:
    ok_scores = [score for score in scores if score.get("ok") is True]
    passed_scores = [score for score in scores if score.get("passed") is True]
    failed_scores = [score for score in scores if score.get("passed") is not True]
    return {
        "total": len(scores),
        "ok": len(ok_scores),
        "passed": len(passed_scores),
        "failed": len(failed_scores),
        "max_abs_xy_error_um": _max_metric(ok_scores, "max_abs_xy_error_um"),
        "max_distance_error_um": _max_metric(ok_scores, "max_distance_error_um"),
        "max_shape_error_px": _max_metric(ok_scores, "max_shape_error_px"),
        "max_rectangle_corner_error_px": _max_metric(ok_scores, "max_rectangle_corner_error_px"),
        "max_origin_center_error_px": _max_metric(ok_scores, "origin_center_error_px"),
    }


def compact_score_rows(scores: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for score in scores:
        metrics = score.get("metrics") if isinstance(score.get("metrics"), dict) else {}
        rows.append(
            {
                "capture_id": score.get("capture_id") or "",
                "position_id": score.get("position_id") or "",
                "image": score.get("image_rel_path") or "",
                "ok": score.get("ok"),
                "passed": score.get("passed"),
                "status": score.get("status") or "",
                "max_abs_xy_error_um": metrics.get("max_abs_xy_error_um", ""),
                "rms_xy_error_um": metrics.get("rms_xy_error_um", ""),
                "max_distance_error_um": metrics.get("max_distance_error_um", ""),
                "max_shape_error_px": metrics.get("max_shape_error_px", ""),
                "max_ball_center_error_px": metrics.get("max_ball_center_error_px", ""),
                "max_rectangle_center_error_px": metrics.get("max_rectangle_center_error_px", ""),
                "max_rectangle_corner_error_px": metrics.get("max_rectangle_corner_error_px", ""),
                "origin_center_error_px": metrics.get("origin_center_error_px", ""),
                "baseline_path": score.get("baseline_path") or "",
                "candidate_path": score.get("candidate_path") or "",
            }
        )
    return rows


def write_score_outputs(
    payload: dict[str, Any],
    *,
    json_output: str | Path | None = None,
    csv_output: str | Path | None = None,
) -> None:
    if json_output:
        _write_json(json_output, payload)
    if csv_output:
        rows = payload.get("summary_rows")
        if rows is None:
            rows = compact_score_rows([payload])
        _write_csv(csv_output, rows)


def _score_shell(
    *,
    capture_id: str | None,
    position_id: str | None,
    image_rel_path: str | None,
    baseline_payload: dict[str, Any],
    candidate_payload: dict[str, Any],
    baseline_path: str | Path | None,
    candidate_path: str | Path | None,
    max_abs_xy_error_um: float,
    max_distance_error_um: float,
    max_shape_error_px: float,
) -> dict[str, Any]:
    return {
        "schema_version": SCORING_SCHEMA_VERSION,
        "kind": "vision_score",
        "capture_id": capture_id,
        "position_id": position_id,
        "image_rel_path": image_rel_path,
        "baseline_path": str(baseline_path) if baseline_path is not None else None,
        "candidate_path": str(candidate_path) if candidate_path is not None else None,
        "baseline_image_path": baseline_payload.get("image_path"),
        "candidate_image_path": candidate_payload.get("image_path"),
        "thresholds": {
            "max_abs_xy_error_um": float(max_abs_xy_error_um),
            "max_distance_error_um": float(max_distance_error_um),
            "max_shape_error_px": float(max_shape_error_px),
        },
    }


def _missing_file_score(
    capture: ScoreableCapture,
    *,
    baseline_path: Path | None,
    candidate_path: Path | None,
    max_abs_xy_error_um: float,
    max_distance_error_um: float,
    max_shape_error_px: float,
) -> dict[str, Any]:
    missing = []
    if baseline_path is None:
        missing.append("baseline")
    if candidate_path is None:
        missing.append("candidate")
    score = _score_shell(
        capture_id=capture.capture_id,
        position_id=capture.position_id,
        image_rel_path=capture.image_rel_path,
        baseline_payload={},
        candidate_payload={},
        baseline_path=baseline_path,
        candidate_path=candidate_path,
        max_abs_xy_error_um=max_abs_xy_error_um,
        max_distance_error_um=max_distance_error_um,
        max_shape_error_px=max_shape_error_px,
    )
    score.update(
        {
            "ok": False,
            "passed": False,
            "status": f"scoring failed closed: missing {' and '.join(missing)} result file",
            "comparisons": {},
            "metrics": {},
        }
    )
    return score


def _selected_comparable_shapes(payload: dict[str, Any], payload_name: str) -> dict[str, list[dict[str, Any]]]:
    items = _selected_recognition_items(payload, payload_name)
    balls = []
    rectangles = []
    for item in items:
        ball = _ball_shape_from_item(item)
        if ball is not None:
            balls.append(ball)
            continue
        rectangle = _rectangle_shape_from_item(item)
        if rectangle is not None:
            rectangles.append(rectangle)
    return {
        "balls": balls,
        "rectangles": rectangles,
    }


def _selected_recognition_items(payload: dict[str, Any], payload_name: str) -> list[dict[str, Any]]:
    raw_selected = payload.get("selected_recognition")
    if not isinstance(raw_selected, dict) or not raw_selected:
        raise ValueError(f"{payload_name} selected_recognition is missing")
    items: list[dict[str, Any]] = []
    for _roi_key, raw_items in sorted(raw_selected.items(), key=lambda item: _roi_sort_key(item[0])):
        if not isinstance(raw_items, list):
            continue
        for raw_item in raw_items:
            if isinstance(raw_item, dict):
                items.append(raw_item)
    if not items:
        raise ValueError(f"{payload_name} selected_recognition has no selected shapes")
    return items


def _ball_shape_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    shape_kind = item.get("shape_kind")
    source = item.get("source")
    shape = item.get("shape")
    if not isinstance(shape, dict):
        return None
    if shape_kind == "circle":
        return {
            "roi_index": item.get("roi_index"),
            "source": source,
            "shape_kind": shape_kind,
            "x": _number(shape, "x", "circle"),
            "y": _number(shape, "y", "circle"),
            "radius": _number(shape, "radius", "circle"),
        }
    if source == "silhouette_circle":
        return {
            "roi_index": item.get("roi_index"),
            "source": source,
            "shape_kind": shape_kind,
            "x": _number(shape, "circle_x", "silhouette_circle"),
            "y": _number(shape, "circle_y", "silhouette_circle"),
            "radius": _number(shape, "circle_radius", "silhouette_circle"),
        }
    return None


def _rectangle_shape_from_item(item: dict[str, Any]) -> dict[str, Any] | None:
    if item.get("shape_kind") != "rectangle":
        return None
    shape = item.get("shape")
    if not isinstance(shape, dict):
        return None
    x1 = _number(shape, "x1", "rectangle")
    y1 = _number(shape, "y1", "rectangle")
    x2 = _number(shape, "x2", "rectangle")
    y2 = _number(shape, "y2", "rectangle")
    corners = _rectangle_corners(shape)
    if not corners:
        corners = _bbox_rectangle_corners(x1, y1, x2, y2)
    if len(corners) != 4:
        raise ValueError(f"rectangle corners must contain four points, got {len(corners)}")
    center_x = sum(corner["x"] for corner in corners) / len(corners) if corners else 0.5 * (x1 + x2)
    center_y = sum(corner["y"] for corner in corners) / len(corners) if corners else 0.5 * (y1 + y2)
    return {
        "roi_index": item.get("roi_index"),
        "source": item.get("source"),
        "shape_kind": item.get("shape_kind"),
        "x1": x1,
        "y1": y1,
        "x2": x2,
        "y2": y2,
        "center_x": center_x,
        "center_y": center_y,
        "width": abs(x2 - x1),
        "height": abs(y2 - y1),
        "corners": corners,
    }


def _rectangle_corners(shape: dict[str, Any]) -> list[dict[str, float]]:
    raw_corners = shape.get("corners")
    if not isinstance(raw_corners, list):
        return []
    corners = []
    for index, raw_corner in enumerate(raw_corners, start=1):
        if not isinstance(raw_corner, dict):
            continue
        corners.append(
            {
                "x": _number(raw_corner, "x", f"rectangle corner {index}"),
                "y": _number(raw_corner, "y", f"rectangle corner {index}"),
            }
        )
    return _canonical_rectangle_corners(corners)


def _bbox_rectangle_corners(x1: float, y1: float, x2: float, y2: float) -> list[dict[str, float]]:
    left, right = sorted((float(x1), float(x2)))
    top, bottom = sorted((float(y1), float(y2)))
    return [
        {"x": left, "y": top},
        {"x": right, "y": top},
        {"x": right, "y": bottom},
        {"x": left, "y": bottom},
    ]


def _canonical_rectangle_corners(corners: list[dict[str, float]]) -> list[dict[str, float]]:
    if len(corners) != 4:
        return corners
    center_x = sum(corner["x"] for corner in corners) / 4.0
    center_y = sum(corner["y"] for corner in corners) / 4.0
    ordered = sorted(
        corners,
        key=lambda corner: math.atan2(corner["y"] - center_y, corner["x"] - center_x),
    )
    start_index = min(range(4), key=lambda index: ordered[index]["x"] + ordered[index]["y"])
    return [dict(ordered[(start_index + offset) % 4]) for offset in range(4)]


def _compare_ball_shape(index: int, baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    signed_error = {
        "x": candidate["x"] - baseline["x"],
        "y": candidate["y"] - baseline["y"],
        "radius": candidate["radius"] - baseline["radius"],
    }
    return {
        "selection_index": index,
        "baseline_roi_index": baseline.get("roi_index"),
        "candidate_roi_index": candidate.get("roi_index"),
        "baseline": baseline,
        "candidate": candidate,
        "signed_error": signed_error,
        "abs_error": {key: abs(value) for key, value in signed_error.items()},
        "center_error_px": math.hypot(signed_error["x"], signed_error["y"]),
    }


def _compare_rectangle_shape(index: int, baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    corner_comparisons = []
    for corner_index, (baseline_corner, candidate_corner) in enumerate(
        zip(baseline["corners"], candidate["corners"]),
        start=1,
    ):
        signed_error = {
            "x": candidate_corner["x"] - baseline_corner["x"],
            "y": candidate_corner["y"] - baseline_corner["y"],
        }
        corner_comparisons.append(
            {
                "corner_index": corner_index,
                "baseline": baseline_corner,
                "candidate": candidate_corner,
                "signed_error": signed_error,
                "abs_error": {key: abs(value) for key, value in signed_error.items()},
                "distance_error_px": math.hypot(signed_error["x"], signed_error["y"]),
            }
        )
    center_signed_error = {
        "x": candidate["center_x"] - baseline["center_x"],
        "y": candidate["center_y"] - baseline["center_y"],
    }
    size_signed_error = {
        "width": candidate["width"] - baseline["width"],
        "height": candidate["height"] - baseline["height"],
    }
    return {
        "selection_index": index,
        "baseline_roi_index": baseline.get("roi_index"),
        "candidate_roi_index": candidate.get("roi_index"),
        "baseline": baseline,
        "candidate": candidate,
        "corners": corner_comparisons,
        "center_signed_error": center_signed_error,
        "center_abs_error": {key: abs(value) for key, value in center_signed_error.items()},
        "signed_error": {**center_signed_error, **size_signed_error},
        "abs_error": {key: abs(value) for key, value in {**center_signed_error, **size_signed_error}.items()},
        "max_corner_error_px": max((corner["distance_error_px"] for corner in corner_comparisons), default=0.0),
        "center_error_px": math.hypot(center_signed_error["x"], center_signed_error["y"]),
    }


def _roi_sort_key(roi_key: str) -> tuple[int, str]:
    text = str(roi_key)
    if text.startswith("roi_"):
        try:
            return (int(text[4:]), text)
        except ValueError:
            pass
    return (0, text)


def _required_relative_measurement(payload: dict[str, Any], payload_name: str) -> dict[str, Any]:
    relative = payload.get("relative_measurement")
    if not isinstance(relative, dict):
        raise ValueError(f"{payload_name} relative_measurement is missing")
    return relative


def _resolve_capture_id(payload: dict[str, Any], capture_id: str | None) -> str:
    resolved = capture_id or payload.get("standard_capture_id") or _payload_capture_id(payload)
    if not resolved:
        raise ValueError("capture id is required when the result image_path is missing")
    return str(resolved).strip()


def _required_scoreable_capture(
    capture_id: str,
    standard_positions_path: str | Path,
) -> ScoreableCapture:
    captures = {
        capture.capture_id: capture
        for capture in discover_v4_scoreable_captures(standard_positions_path)
    }
    capture = captures.get(capture_id)
    if capture is None:
        raise ValueError(f"{capture_id} is not a scoreable v4 capture")
    return capture


def _required_circle_list(relative: dict[str, Any], payload_name: str) -> list[dict[str, Any]]:
    raw_circles = relative.get("circles")
    if not isinstance(raw_circles, list) or not raw_circles:
        raise ValueError(f"{payload_name} relative_measurement.circles is missing")
    circles: list[dict[str, Any]] = []
    for index, raw_circle in enumerate(raw_circles, start=1):
        circles.append(_required_mapping(raw_circle, f"{payload_name} circle {index}"))
    return circles


def _center_px(circle: dict[str, Any], label: str) -> dict[str, float]:
    center = _required_mapping(circle.get("center_px"), f"{label}.center_px")
    return {
        "x": _number(center, "x", f"{label}.center_px"),
        "y": _number(center, "y", f"{label}.center_px"),
    }


def _circle_relative_um(circle: dict[str, Any], label: str) -> dict[str, float]:
    raw_relative = circle.get("relative_um")
    relative = raw_relative if isinstance(raw_relative, dict) else {}
    return {
        "x": _number_with_fallback(circle, relative, "x_um", "x", "dx", label),
        "y": _number_with_fallback(circle, relative, "y_um", "y", "dy", label),
        "distance": _number_with_fallback(circle, relative, "distance_um", "distance", "distance", label),
    }


def _compare_relative_um(
    name: str,
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    **metadata: Any,
) -> dict[str, Any]:
    baseline_values = {
        "x": _number_with_fallback(baseline, baseline, "x", "x", "dx", f"baseline {name}"),
        "y": _number_with_fallback(baseline, baseline, "y", "y", "dy", f"baseline {name}"),
        "distance": _number(baseline, "distance", f"baseline {name}"),
    }
    candidate_values = {
        "x": _number_with_fallback(candidate, candidate, "x", "x", "dx", f"candidate {name}"),
        "y": _number_with_fallback(candidate, candidate, "y", "y", "dy", f"candidate {name}"),
        "distance": _number(candidate, "distance", f"candidate {name}"),
    }
    return _compare_xyz(name, baseline_values, candidate_values, units="um", **metadata)


def _compare_xy(name: str, baseline: dict[str, float], candidate: dict[str, float]) -> dict[str, Any]:
    dx = candidate["x"] - baseline["x"]
    dy = candidate["y"] - baseline["y"]
    comparison = {
        "name": name,
        "units": "px",
        "baseline": baseline,
        "candidate": candidate,
        "signed_error": {
            "x": dx,
            "y": dy,
        },
        "abs_error": {
            "x": abs(dx),
            "y": abs(dy),
        },
        "distance_error": math.hypot(dx, dy),
    }
    return comparison


def _compare_xyz(
    name: str,
    baseline: dict[str, float],
    candidate: dict[str, float],
    *,
    units: str,
    **metadata: Any,
) -> dict[str, Any]:
    signed_error = {
        "x": candidate["x"] - baseline["x"],
        "y": candidate["y"] - baseline["y"],
        "distance": candidate["distance"] - baseline["distance"],
    }
    comparison = {
        "name": name,
        "units": units,
        "baseline": baseline,
        "candidate": candidate,
        "signed_error": signed_error,
        "abs_error": {key: abs(value) for key, value in signed_error.items()},
    }
    comparison.update(metadata)
    return comparison


def _number_with_fallback(
    primary: dict[str, Any],
    secondary: dict[str, Any],
    primary_key: str,
    secondary_key: str,
    secondary_fallback_key: str,
    label: str,
) -> float:
    for mapping, key in (
        (primary, primary_key),
        (secondary, secondary_key),
        (secondary, secondary_fallback_key),
    ):
        value = mapping.get(key)
        if value is None:
            continue
        return _finite_float(value, f"{label}.{key}")
    raise ValueError(f"{label} is missing {primary_key}/{secondary_key}")


def _number(mapping: dict[str, Any], key: str, label: str) -> float:
    value = mapping.get(key)
    if value is None:
        raise ValueError(f"{label}.{key} is missing")
    return _finite_float(value, f"{label}.{key}")


def _finite_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} is not finite")
    return number


def _required_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is missing")
    return value


def _rms(values: Iterable[float]) -> float:
    values_list = list(values)
    if not values_list:
        return 0.0
    return math.sqrt(sum(value * value for value in values_list) / len(values_list))


def _max_metric(scores: Sequence[dict[str, Any]], key: str) -> float | None:
    values = []
    for score in scores:
        metrics = score.get("metrics")
        if isinstance(metrics, dict) and isinstance(metrics.get(key), (int, float)):
            values.append(float(metrics[key]))
    return max(values) if values else None


def _payload_capture_id(payload: dict[str, Any]) -> str | None:
    image_path = payload.get("image_path")
    if not image_path:
        return None
    return _path_stem(str(image_path))


def _path_stem(path_text: str) -> str:
    return Path(path_text.replace("\\", "/")).stem


def _numeric_id_sort_key(identifier: str) -> tuple[int, ...]:
    parts = []
    for raw_part in identifier.split("."):
        try:
            parts.append(int(raw_part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: str | Path, rows: Sequence[dict[str, Any]]) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({fieldname: row.get(fieldname, "") for fieldname in CSV_FIELDNAMES})


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score offline vision recognition lab session payloads.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list-captures", help="List v4 scoreable captures.")
    list_parser.add_argument("--standard-positions", default=str(DEFAULT_STANDARD_POSITIONS_PATH))
    list_parser.add_argument("--json-output")

    standard_parser = subparsers.add_parser(
        "set-standard",
        help="Store one reviewed vision-lab result as the official standard baseline.",
    )
    standard_parser.add_argument("result", help="Reviewed vision-lab result JSON to save as the standard.")
    standard_parser.add_argument("--baseline-dir", default=str(DEFAULT_STANDARD_BASELINE_DIR))
    standard_parser.add_argument("--capture-id", help="Override capture id when image_path cannot identify it.")
    standard_parser.add_argument("--standard-positions", default=str(DEFAULT_STANDARD_POSITIONS_PATH))
    standard_parser.add_argument("--overwrite", action="store_true")
    standard_parser.add_argument("--json-output")

    capture_standard_parser = subparsers.add_parser(
        "capture-standard",
        help="Open the UI on an image and save that reviewed session as the standard.",
    )
    capture_standard_parser.add_argument("--image", required=True)
    capture_standard_parser.add_argument("--result-output")
    capture_standard_parser.add_argument("--baseline-dir", default=str(DEFAULT_STANDARD_BASELINE_DIR))
    capture_standard_parser.add_argument("--capture-id", help="Override capture id when the image filename cannot identify it.")
    capture_standard_parser.add_argument("--standard-positions", default=str(DEFAULT_STANDARD_POSITIONS_PATH))
    capture_standard_parser.add_argument("--overwrite", action="store_true")
    capture_standard_parser.add_argument("--json-output")

    run_parser = subparsers.add_parser("run", help="Score one result against its saved standard baseline.")
    run_parser.add_argument("candidate", help="Candidate vision-lab result JSON to score.")
    run_parser.add_argument("--baseline-dir", default=str(DEFAULT_STANDARD_BASELINE_DIR))
    run_parser.add_argument("--capture-id", help="Override capture id when image_path cannot identify it.")
    run_parser.add_argument("--standard-positions", default=str(DEFAULT_STANDARD_POSITIONS_PATH))
    run_parser.add_argument("--json-output")
    run_parser.add_argument("--csv-output")
    _add_threshold_args(run_parser)

    capture_run_parser = subparsers.add_parser(
        "capture-run",
        help="Open the UI on an image and score that reviewed session against the saved standard.",
    )
    capture_run_parser.add_argument("--image", required=True)
    capture_run_parser.add_argument("--result-output")
    capture_run_parser.add_argument("--baseline-dir", default=str(DEFAULT_STANDARD_BASELINE_DIR))
    capture_run_parser.add_argument("--capture-id", help="Override capture id when the image filename cannot identify it.")
    capture_run_parser.add_argument("--standard-positions", default=str(DEFAULT_STANDARD_POSITIONS_PATH))
    capture_run_parser.add_argument("--json-output")
    capture_run_parser.add_argument("--csv-output")
    _add_threshold_args(capture_run_parser)

    pair_parser = subparsers.add_parser("score-pair", help="Score one baseline/candidate result pair.")
    pair_parser.add_argument("--baseline", required=True)
    pair_parser.add_argument("--candidate", required=True)
    pair_parser.add_argument("--capture-id")
    pair_parser.add_argument("--json-output")
    pair_parser.add_argument("--csv-output")
    _add_threshold_args(pair_parser)

    folder_parser = subparsers.add_parser("score-folders", help="Score result folders by v4 capture id.")
    folder_parser.add_argument("--baseline-dir", required=True)
    folder_parser.add_argument("--candidate-dir", required=True)
    folder_parser.add_argument("--standard-positions", default=str(DEFAULT_STANDARD_POSITIONS_PATH))
    folder_parser.add_argument("--json-output")
    folder_parser.add_argument("--csv-output")
    _add_threshold_args(folder_parser)
    return parser


def _add_threshold_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--max-abs-xy-error-um", type=float, default=DEFAULT_MAX_ABS_XY_ERROR_UM)
    parser.add_argument("--max-distance-error-um", type=float, default=DEFAULT_MAX_DISTANCE_ERROR_UM)
    parser.add_argument("--max-shape-error-px", type=float, default=DEFAULT_MAX_SHAPE_ERROR_PX)


def _print_quickstart(parser: argparse.ArgumentParser) -> None:
    parser.print_help()
    print(
        "\nQuick workflow:\n"
        "  1. Set the standard and open the recognition UI:\n"
        "     python \"vision recognition lab\\vision_scoring.py\" capture-standard --image \"Standard position images\\v4\\newhead\\1.1.1.PNG\"\n"
        "  2. Score a later reviewed run against that standard:\n"
        "     python \"vision recognition lab\\vision_scoring.py\" capture-run --image \"Standard position images\\v4\\newhead\\1.1.1.PNG\"\n"
        "\nUse set-standard/run only when you already have saved result JSON files.\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        _print_quickstart(parser)
        return 0
    args = parser.parse_args(argv)
    if args.command == "list-captures":
        payload = {
            "schema_version": SCORING_SCHEMA_VERSION,
            "kind": "vision_scoreable_captures",
            "captures": [
                capture.to_dict()
                for capture in discover_v4_scoreable_captures(args.standard_positions)
            ],
        }
        if args.json_output:
            _write_json(args.json_output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "set-standard":
        try:
            payload = save_standard_session_file(
                args.result,
                baseline_dir=args.baseline_dir,
                capture_id=args.capture_id,
                standard_positions_path=args.standard_positions,
                overwrite=args.overwrite,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            payload = {
                "schema_version": SCORING_SCHEMA_VERSION,
                "kind": "vision_standard_saved",
                "ok": False,
                "status": f"set-standard failed: {exc}",
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1
        if args.json_output:
            _write_json(args.json_output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "capture-standard":
        try:
            payload = capture_standard_from_image(
                args.image,
                result_output_path=args.result_output,
                baseline_dir=args.baseline_dir,
                capture_id=args.capture_id,
                standard_positions_path=args.standard_positions,
                overwrite=args.overwrite,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            payload = {
                "schema_version": SCORING_SCHEMA_VERSION,
                "kind": "vision_standard_saved",
                "ok": False,
                "status": f"capture-standard failed: {exc}",
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 1
        if args.json_output:
            _write_json(args.json_output, payload)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    if args.command == "run":
        try:
            payload = score_against_standard_file(
                args.candidate,
                baseline_dir=args.baseline_dir,
                capture_id=args.capture_id,
                standard_positions_path=args.standard_positions,
                max_abs_xy_error_um=args.max_abs_xy_error_um,
                max_distance_error_um=args.max_distance_error_um,
                max_shape_error_px=args.max_shape_error_px,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            payload = {
                "schema_version": SCORING_SCHEMA_VERSION,
                "kind": "vision_score",
                "ok": False,
                "passed": False,
                "status": f"run failed: {exc}",
            }
        payload["summary_rows"] = compact_score_rows([payload])
        write_score_outputs(payload, json_output=args.json_output, csv_output=args.csv_output)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["passed"] else 1

    if args.command == "capture-run":
        try:
            payload = capture_run_from_image(
                args.image,
                result_output_path=args.result_output,
                baseline_dir=args.baseline_dir,
                capture_id=args.capture_id,
                standard_positions_path=args.standard_positions,
                max_abs_xy_error_um=args.max_abs_xy_error_um,
                max_distance_error_um=args.max_distance_error_um,
                max_shape_error_px=args.max_shape_error_px,
            )
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            payload = {
                "schema_version": SCORING_SCHEMA_VERSION,
                "kind": "vision_score",
                "ok": False,
                "passed": False,
                "status": f"capture-run failed: {exc}",
            }
        payload["summary_rows"] = compact_score_rows([payload])
        write_score_outputs(payload, json_output=args.json_output, csv_output=args.csv_output)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["passed"] else 1

    if args.command == "score-pair":
        payload = score_session_files(
            args.baseline,
            args.candidate,
            capture_id=args.capture_id,
            max_abs_xy_error_um=args.max_abs_xy_error_um,
            max_distance_error_um=args.max_distance_error_um,
            max_shape_error_px=args.max_shape_error_px,
        )
        payload["summary_rows"] = compact_score_rows([payload])
        write_score_outputs(payload, json_output=args.json_output, csv_output=args.csv_output)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["passed"] else 1

    if args.command == "score-folders":
        payload = score_session_folders(
            args.baseline_dir,
            args.candidate_dir,
            standard_positions_path=args.standard_positions,
            max_abs_xy_error_um=args.max_abs_xy_error_um,
            max_distance_error_um=args.max_distance_error_um,
            max_shape_error_px=args.max_shape_error_px,
        )
        write_score_outputs(payload, json_output=args.json_output, csv_output=args.csv_output)
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["summary"]["failed"] == 0 else 1

    return 2


if __name__ == "__main__":
    sys.exit(main())
