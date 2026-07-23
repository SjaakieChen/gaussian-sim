"""Tkinter UI shell for designing standard-position vision recognition rules."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
import re
import tkinter as tk
import traceback
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable, Sequence

import cv2
import numpy as np
from skimage.feature import canny
from skimage.transform import hough_circle, hough_circle_peaks, probabilistic_hough_line


try:
    from tmpython.statement import TMPythonStatementJ
except Exception:  # pragma: no cover - developer machines do not have TMPython

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Local fallback so the vision launcher can be tested outside TestMaster."""


DEFAULT_STANDARD_POSITION_IMAGE_ROOT = Path(__file__).resolve().parents[1] / "Standard position images"
DEFAULT_STANDARD_BATCH_NAMES = ("v4",)
OFFICIAL_BASELINE_FOLDER_NAME = "vision_baselines"
VISION_SCORE_FOLDER_NAME = "vision_scores"
V5_SEQUENCE_MEMORY_FOLDER_NAME = "v5_sequence_memory"
V5_SEQUENCE_MEMORY_FILE_NAME = "v5_sequence_memory.json"
IMAGE_EXTENSIONS = frozenset({".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff"})
LEGACY_POSITION_ID_RE = re.compile(r"^\d{3}$")
SEMANTIC_POSITION_ID_RE = re.compile(r"^\d+\.\d+\.\d+$")
IMAGE_POSITION_STEM_RE = re.compile(r"^(?P<id>\d{3}|\d+\.\d+\.\d+)(?:[_-].*)?$")
MAX_OVERLAY_ITEMS = 80
MAX_SILHOUETTE_CONTOUR_SEGMENTS = 900
DEFAULT_START_POSITION_ID = "1.1"
DEFAULT_GEOMETRY_SENSITIVITY = 0.65
MIN_GEOMETRY_SENSITIVITY = 0.05
MAX_GEOMETRY_SENSITIVITY = 1.00
DEFAULT_BRIGHT_RECTANGLE_SENSITIVITY = 0.65
MIN_BRIGHT_RECTANGLE_SENSITIVITY = 0.05
MAX_BRIGHT_RECTANGLE_SENSITIVITY = 1.00
DEFAULT_SILHOUETTE_SENSITIVITY = 0.65
MIN_SILHOUETTE_SENSITIVITY = 0.05
MAX_SILHOUETTE_SENSITIVITY = 1.00
MAX_VIEW_SCALE = 2.0
RECOGNITION_RERUN_DELAY_MS = 250
ROI_REQUIRED_MESSAGE = "Draw at least one ROI before running recognition"
EDGE_RECTANGLE_OVERLAY_COLOR = "#ffd23f"
BRIGHT_RECTANGLE_OVERLAY_COLOR = "#ff4fd8"
VISION_RECOGNITION_LAB_VERSION = "v3"
VISION_RECOGNITION_LAB_TITLE = f"Vision Recognition Lab {VISION_RECOGNITION_LAB_VERSION}"
FIXED_MEASUREMENT_SHORT_EDGE_LENGTH_UM = 500.0
COARSE_TOP_CAPTURE_IDS = frozenset({"2.1.1", "4.1.1"})
DIRECT_TOP_VIEW_MAX_Y_FRACTION = 0.65
FEATURE_ROLE_CHOICES = (
    "laser_reference",
    "ball_candidate",
    "ball_1_gross_ball",
    "ball_1_top_ball",
    "ball_1_side_ball",
    "ball_2_gross_ball",
    "ball_2_top_ball",
    "ball_2_side_ball",
    "trench_top_surface",
    "trench_bottom_floor",
    "chip_reference",
    "fiducial_candidate",
    "object_candidate",
    "ignore",
)

CAPTURE_REVIEW_CONTEXT_LABELS = {
    "2.1.1": "BALL 1 - COARSE TOP CAMERA VIEW",
    "2.4.1": "LASER RECTANGLE - FINE TOP CAMERA VIEW",
    "2.5.1": "BALL 1 - FINE TOP CAMERA VIEW",
    "2.6.1": "BALL 1 - SIDE MIRROR VIEW",
    "4.1.1": "BALL 2 - COARSE TOP CAMERA VIEW",
    "4.4.1": "LASER RECTANGLE - FINE TOP CAMERA VIEW",
    "4.5.1": "BALL 2 - FINE TOP CAMERA VIEW",
    "4.6.2": "BALL 2 - SIDE MIRROR VIEW",
}

CAPTURE_FEATURE_ROLE_OPTIONS = {
    "2.1.1": (
        ("ball_1_gross_ball", "Ball 1 circle (COARSE TOP view)"),
        ("ignore", "Ignore this detection"),
    ),
    "2.4.1": (
        ("laser_reference", "Laser rectangle (FINE TOP view)"),
        ("ignore", "Ignore this detection"),
    ),
    "2.5.1": (
        ("ball_1_top_ball", "Ball 1 circle (FINE TOP view)"),
        ("ignore", "Ignore this detection"),
    ),
    "2.6.1": (
        ("ball_1_side_ball", "Ball 1 circle (SIDE MIRROR view)"),
        ("trench_top_surface", "Trench top line (SIDE MIRROR view)"),
        ("trench_bottom_floor", "Trench floor line (SIDE MIRROR view)"),
        ("ignore", "Ignore this detection"),
    ),
    "4.1.1": (
        ("ball_2_gross_ball", "Ball 2 circle (COARSE TOP view)"),
        ("ignore", "Ignore this detection"),
    ),
    "4.4.1": (
        ("laser_reference", "Laser rectangle (FINE TOP view)"),
        ("ignore", "Ignore this detection"),
    ),
    "4.5.1": (
        ("ball_2_top_ball", "Ball 2 circle (FINE TOP view)"),
        ("ignore", "Ignore this detection"),
    ),
    "4.6.2": (
        ("ball_2_side_ball", "Ball 2 circle (SIDE MIRROR view)"),
        ("trench_top_surface", "Trench top line (SIDE MIRROR view)"),
        ("trench_bottom_floor", "Trench floor line (SIDE MIRROR view)"),
        ("ignore", "Ignore this detection"),
    ),
}


def feature_role_options_for_capture(capture_id: str | None) -> tuple[tuple[str, str], ...]:
    """Return canonical role keys and operator-facing labels for one review capture."""

    capture_key = str(capture_id or "").strip()
    return CAPTURE_FEATURE_ROLE_OPTIONS.get(
        capture_key,
        tuple((role, role) for role in FEATURE_ROLE_CHOICES),
    )


def feature_role_display_label(role: str, capture_id: str | None = None) -> str:
    role_key = str(role or "").strip()
    for option_role, display_label in feature_role_options_for_capture(capture_id):
        if option_role == role_key:
            return display_label
    return role_key


def feature_role_from_display_label(label: str, capture_id: str | None = None) -> str | None:
    value = str(label or "").strip()
    for role, display_label in feature_role_options_for_capture(capture_id):
        if value in {role, display_label}:
            return role
    return None


@dataclass(frozen=True)
class VisionLine:
    x1: float
    y1: float
    x2: float
    y2: float
    score: float
    label: str


@dataclass(frozen=True)
class VisionIntersection:
    x: float
    y: float
    score: float
    label: str


@dataclass(frozen=True)
class VisionCircle:
    x: float
    y: float
    radius: float
    score: float
    label: str


@dataclass(frozen=True)
class VisionRectangle:
    x1: float
    y1: float
    x2: float
    y2: float
    missing_side: str | None
    score: float
    label: str
    corners: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True)
class VisionSemicircle:
    x: float
    y: float
    radius: float
    orientation: str
    score: float
    label: str


@dataclass(frozen=True)
class VisionSilhouette:
    x: float
    y: float
    x1: float
    y1: float
    x2: float
    y2: float
    area: float
    score: float
    label: str
    contour_segments: tuple[tuple[float, float, float, float], ...] = ()
    circle_contour_segments: tuple[tuple[float, float, float, float], ...] = ()
    circle_x: float | None = None
    circle_y: float | None = None
    circle_radius: float | None = None


@dataclass(frozen=True)
class VisionRecognitionResult:
    algorithm_name: str
    display_name: str
    lines: tuple[VisionLine, ...]
    intersections: tuple[VisionIntersection, ...]
    circles: tuple[VisionCircle, ...]
    rectangles: tuple[VisionRectangle, ...]
    semicircles: tuple[VisionSemicircle, ...]
    silhouettes: tuple[VisionSilhouette, ...]
    message: str


@dataclass(frozen=True)
class VisionRectangleShortEdge:
    start: tuple[float, float]
    end: tuple[float, float]
    midpoint: tuple[float, float]
    length_px: float


@dataclass(frozen=True)
class VisionCircleReference:
    source: str
    x: float
    y: float
    radius: float | None
    label: str
    score: float


@dataclass(frozen=True)
class VisionRectangleCircleMeasurement:
    short_edge_length_um: float
    um_per_pixel: float
    short_edge: VisionRectangleShortEdge
    circle: VisionCircleReference
    dx_px: float
    dy_px: float
    distance_px: float
    dx_um: float
    dy_um: float
    distance_um: float


@dataclass(frozen=True)
class VisionRecognitionTreeItem:
    item_id: str
    roi_index: int
    shape_kind: str
    shape: Any
    source: str


@dataclass(frozen=True)
class VisionSelectedMeasurement:
    rectangle_selection: VisionRecognitionTreeItem
    circle_selection: VisionRecognitionTreeItem
    measurement: VisionRectangleCircleMeasurement


@dataclass(frozen=True)
class VisionRecognizer:
    name: str
    display_name: str
    mode: str
    quantile: float


@dataclass(frozen=True)
class VisionROI:
    kind: str
    x1: float
    y1: float
    x2: float
    y2: float
    orientation: str = "right"

    @property
    def normalized(self) -> "VisionROI":
        return VisionROI(
            kind=self.kind,
            x1=min(self.x1, self.x2),
            y1=min(self.y1, self.y2),
            x2=max(self.x1, self.x2),
            y2=max(self.y1, self.y2),
            orientation=self.orientation,
        )

    @property
    def width(self) -> float:
        roi = self.normalized
        return roi.x2 - roi.x1

    @property
    def height(self) -> float:
        roi = self.normalized
        return roi.y2 - roi.y1

    @property
    def label(self) -> str:
        roi = self.normalized
        display_kind = "edges" if self.kind == "box" else self.kind
        suffix = f" {self.orientation}" if self.kind == "semicircle" else ""
        return f"{display_kind}{suffix} ({roi.x1:.0f},{roi.y1:.0f})-({roi.x2:.0f},{roi.y2:.0f})"


@dataclass(frozen=True)
class AxisLineCandidate:
    axis: str
    coord: float
    start: float
    end: float
    length: float
    score: float


@dataclass(frozen=True)
class SegmentLineCandidate:
    x1: float
    y1: float
    x2: float
    y2: float
    ux: float
    uy: float
    nx: float
    ny: float
    angle: float
    offset: float
    start: float
    end: float
    length: float
    score: float


RECOGNIZERS = (
    VisionRecognizer(
        name="bright_threshold",
        display_name="Bright threshold",
        mode="bright",
        quantile=0.985,
    ),
    VisionRecognizer(
        name="dark_threshold",
        display_name="Dark threshold",
        mode="dark",
        quantile=0.015,
    ),
    VisionRecognizer(
        name="dark_adaptive",
        display_name="Dark adaptive",
        mode="dark_adaptive",
        quantile=0.975,
    ),
    VisionRecognizer(
        name="opencv_adaptive_dark",
        display_name="OpenCV adaptive dark",
        mode="opencv_adaptive_dark",
        quantile=0.975,
    ),
    VisionRecognizer(
        name="opencv_hough",
        display_name="OpenCV Canny + Hough",
        mode="opencv_hough",
        quantile=0.985,
    ),
    VisionRecognizer(
        name="opencv_hough_sized",
        display_name="OpenCV Hough size-prior",
        mode="opencv_hough_sized",
        quantile=0.985,
    ),
    VisionRecognizer(
        name="skimage_hough",
        display_name="scikit-image Canny + Hough",
        mode="skimage_hough",
        quantile=0.985,
    ),
    VisionRecognizer(
        name="skimage_hough_sized",
        display_name="scikit-image Hough size-prior",
        mode="skimage_hough_sized",
        quantile=0.985,
    ),
    VisionRecognizer(
        name="background_corrected_dark",
        display_name="Background-corrected dark",
        mode="background_dark",
        quantile=0.975,
    ),
    VisionRecognizer(
        name="dark_rim_edges",
        display_name="Dark rim edges",
        mode="dark_rim",
        quantile=0.98,
    ),
    VisionRecognizer(
        name="dark_multiscale",
        display_name="Dark multiscale",
        mode="dark_multiscale",
        quantile=0.975,
    ),
    VisionRecognizer(
        name="dark_silhouette",
        display_name="Dark silhouette",
        mode="dark_silhouette",
        quantile=0.30,
    ),
    VisionRecognizer(
        name="gradient_edges",
        display_name="Gradient edges",
        mode="gradient",
        quantile=0.985,
    ),
    VisionRecognizer(
        name="adaptive_contrast",
        display_name="Adaptive contrast",
        mode="adaptive_contrast",
        quantile=0.985,
    ),
)
RECOGNIZER_BY_NAME = {recognizer.name: recognizer for recognizer in RECOGNIZERS}
DEFAULT_GEOMETRY_RECOGNIZER_NAME = "skimage_hough_sized"
DEFAULT_SILHOUETTE_RECOGNIZER_NAME = "dark_silhouette"
DEFAULT_RECOGNIZER_NAME = DEFAULT_GEOMETRY_RECOGNIZER_NAME
GEOMETRY_RECOGNIZER_NAMES = (
    "skimage_hough_sized",
    "skimage_hough",
    "opencv_hough_sized",
    "opencv_hough",
)
HOUGH_GEOMETRY_RECOGNIZER_NAMES = frozenset(
    {"opencv_hough", "opencv_hough_sized", "skimage_hough", "skimage_hough_sized"}
)
GEOMETRY_ROI_KINDS = frozenset({"box", "edges", "rectangle", "circle"})
SILHOUETTE_RECOGNIZER_OFF_LABEL = "Off"


@dataclass(frozen=True)
class VisionPosition:
    id: str
    label: str
    batches: tuple[str, ...]
    machine_positions_um: dict[str, Any] | None = None
    camera_settings: dict[str, Any] | None = None

    @property
    def display_name(self) -> str:
        if self.label:
            return f"{self.id} - {self.label}"
        return self.id


@dataclass(frozen=True)
class VisionPositionImage:
    position_id: str
    position_label: str
    batch: str
    path: Path
    machine_positions_um: dict[str, Any] | None = None
    camera_settings: dict[str, Any] | None = None
    standard_positions_path: Path | None = None

    @property
    def display_name(self) -> str:
        return f"{self.batch} - {self.path.name}"


@dataclass(frozen=True)
class VisionPositionLibrary:
    positions: tuple[VisionPosition, ...]
    images: tuple[VisionPositionImage, ...]

    def position(self, position_id: str) -> VisionPosition | None:
        normalized_id = normalize_standard_position_id(position_id)
        for position in self.positions:
            if position.id == normalized_id:
                return position
        return None

    def images_for_position(self, position_id: str) -> tuple[VisionPositionImage, ...]:
        normalized_id = normalize_standard_position_id(position_id)
        return tuple(image for image in self.images if image.position_id == normalized_id)


def normalize_standard_position_id(value: Any) -> str:
    text = str(value).strip()
    if LEGACY_POSITION_ID_RE.fullmatch(text):
        return f"{int(text)}.0.0"
    return text


def standard_position_sort_key(position_id: str) -> tuple[int, tuple[int, int, int], str]:
    normalized_id = normalize_standard_position_id(position_id)
    if SEMANTIC_POSITION_ID_RE.fullmatch(normalized_id):
        parts = tuple(int(part) for part in normalized_id.split("."))
        return (0, parts, normalized_id)
    return (1, (0, 0, 0), normalized_id)


def load_standard_position_library(
    image_root: str | Path = DEFAULT_STANDARD_POSITION_IMAGE_ROOT,
    *,
    batch_names: Sequence[str] | None = DEFAULT_STANDARD_BATCH_NAMES,
) -> VisionPositionLibrary:
    root = Path(image_root)
    position_labels: dict[str, str] = {}
    position_batches: dict[str, set[str]] = {}
    position_machine_positions: dict[str, dict[str, Any]] = {}
    position_camera_settings: dict[str, dict[str, Any]] = {}
    images: list[VisionPositionImage] = []
    seen_images: set[tuple[str, Path]] = set()

    for batch_dir in _iter_batch_dirs(root, batch_names=batch_names):
        batch = batch_dir.name
        metadata_path = batch_dir / "standard_positions.json"
        for raw_position in _read_batch_positions(batch_dir):
            position_id = normalize_standard_position_id(raw_position.get("id", ""))
            if not position_id:
                continue
            label = str(raw_position.get("label") or "").strip()
            _record_position(position_id, label, batch, position_labels, position_batches)
            machine_positions = _position_machine_positions(raw_position)
            camera_settings = _position_camera_settings(raw_position)
            if machine_positions is not None:
                position_machine_positions[position_id] = machine_positions
            if camera_settings is not None:
                position_camera_settings[position_id] = camera_settings

            for captured_image in _position_captured_images(raw_position):
                image_path = batch_dir / str(captured_image)
                _record_image(
                    position_id,
                    label,
                    batch,
                    image_path,
                    images,
                    seen_images,
                    machine_positions_um=machine_positions,
                    camera_settings=camera_settings,
                    standard_positions_path=metadata_path,
                )

        for image_path in _iter_image_files(batch_dir):
            position_id = _infer_position_id_from_image_path(image_path)
            if not position_id:
                continue
            if position_id not in position_labels:
                _record_position(position_id, "", batch, position_labels, position_batches)
            _record_image(
                position_id,
                position_labels.get(position_id, ""),
                batch,
                image_path,
                images,
                seen_images,
                machine_positions_um=position_machine_positions.get(position_id),
                camera_settings=position_camera_settings.get(position_id),
                standard_positions_path=metadata_path,
            )

    positions = tuple(
        VisionPosition(
            id=position_id,
            label=position_labels[position_id],
            batches=tuple(sorted(position_batches[position_id], key=_batch_sort_key)),
            machine_positions_um=deepcopy(position_machine_positions.get(position_id)),
            camera_settings=deepcopy(position_camera_settings.get(position_id)),
        )
        for position_id in sorted(position_labels, key=standard_position_sort_key)
    )
    images.sort(
        key=lambda image: (
            standard_position_sort_key(image.position_id),
            _batch_sort_key(image.batch),
            image.path.name.lower(),
        )
    )
    return VisionPositionLibrary(positions=positions, images=tuple(images))


def _record_position(
    position_id: str,
    label: str,
    batch: str,
    labels: dict[str, str],
    batches: dict[str, set[str]],
) -> None:
    labels.setdefault(position_id, label)
    if label and not labels[position_id]:
        labels[position_id] = label
    batches.setdefault(position_id, set()).add(batch)


def _record_image(
    position_id: str,
    label: str,
    batch: str,
    image_path: Path,
    images: list[VisionPositionImage],
    seen_images: set[tuple[str, Path]],
    *,
    machine_positions_um: dict[str, Any] | None = None,
    camera_settings: dict[str, Any] | None = None,
    standard_positions_path: Path | None = None,
) -> None:
    if image_path.suffix.lower() not in IMAGE_EXTENSIONS or not image_path.is_file():
        return
    key = (position_id, image_path.resolve())
    if key in seen_images:
        return
    seen_images.add(key)
    images.append(
        VisionPositionImage(
            position_id=position_id,
            position_label=label,
            batch=batch,
            path=image_path,
            machine_positions_um=deepcopy(machine_positions_um),
            camera_settings=deepcopy(camera_settings),
            standard_positions_path=(
                standard_positions_path if standard_positions_path and standard_positions_path.is_file() else None
            ),
        )
    )


def _position_machine_positions(raw_position: dict[str, Any]) -> dict[str, Any] | None:
    value = raw_position.get("machine_positions_um")
    return deepcopy(value) if isinstance(value, dict) else None


def _position_camera_settings(raw_position: dict[str, Any]) -> dict[str, Any] | None:
    value = raw_position.get("camera_settings")
    return deepcopy(value) if isinstance(value, dict) else None


def _position_captured_images(raw_position: dict[str, Any]) -> tuple[str, ...]:
    captured_images = raw_position.get("captured_images")
    if isinstance(captured_images, list):
        return tuple(str(image) for image in captured_images if image)
    captured_image = raw_position.get("captured_image")
    if captured_image:
        return (str(captured_image),)
    return ()


def _iter_batch_dirs(
    root: Path,
    *,
    batch_names: Sequence[str] | None = DEFAULT_STANDARD_BATCH_NAMES,
) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    allowed_batches = None if batch_names is None else {str(batch).lower() for batch in batch_names}
    if root.name.lower().startswith("v"):
        candidates = (root,)
    else:
        candidates = tuple(path for path in root.iterdir() if path.is_dir() and path.name.lower().startswith("v"))
    if allowed_batches is not None:
        candidates = tuple(path for path in candidates if path.name.lower() in allowed_batches)
    return tuple(
        sorted(
            candidates,
            key=lambda path: _batch_sort_key(path.name),
        )
    )


def _iter_image_files(batch_dir: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            (
                path
                for path in batch_dir.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
            ),
            key=lambda path: path.name.lower(),
        )
    )


def _read_batch_positions(batch_dir: Path) -> tuple[dict[str, Any], ...]:
    metadata_path = batch_dir / "standard_positions.json"
    if not metadata_path.is_file():
        return ()
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    positions = data.get("positions")
    if not isinstance(positions, list):
        return ()
    return tuple(position for position in positions if isinstance(position, dict))


def _infer_position_id_from_image_path(path: Path) -> str:
    match = IMAGE_POSITION_STEM_RE.fullmatch(path.stem)
    if not match:
        return ""
    return normalize_standard_position_id(match.group("id"))


def _batch_sort_key(batch: str) -> tuple[int, int, str]:
    match = re.fullmatch(r"v(\d+)", batch, flags=re.IGNORECASE)
    if match:
        return (0, int(match.group(1)), batch.lower())
    return (1, 0, batch.lower())


def fit_subsample_factor(
    image_width: int,
    image_height: int,
    viewport_width: int,
    viewport_height: int,
) -> int:
    if image_width <= 0 or image_height <= 0:
        return 1
    width_ratio = image_width / max(viewport_width, 1)
    height_ratio = image_height / max(viewport_height, 1)
    return max(1, math.ceil(max(width_ratio, height_ratio)))


def read_grayscale_image(path: str | Path) -> np.ndarray:
    from matplotlib import image as mpl_image

    raw = np.asarray(mpl_image.imread(str(path)))
    if raw.ndim == 2:
        gray = raw.astype(float, copy=False)
    elif raw.ndim == 3 and raw.shape[2] >= 3:
        channels = raw[..., :3].astype(float, copy=False)
        gray = 0.2126 * channels[..., 0] + 0.7152 * channels[..., 1] + 0.0722 * channels[..., 2]
    else:
        raise ValueError(f"unsupported image array shape: {raw.shape}")
    if np.issubdtype(raw.dtype, np.integer):
        max_value = float(np.iinfo(raw.dtype).max)
        if max_value > 0.0:
            gray = gray / max_value
    gray = np.nan_to_num(gray, nan=0.0, posinf=1.0, neginf=0.0)
    if gray.size and gray.max() > 1.0:
        gray = gray / max(float(gray.max()), 1.0)
    return np.clip(gray, 0.0, 1.0)


def detect_coarse_top_ball_circle(
    gray_image: np.ndarray,
    rois: Sequence[VisionROI],
    capture_id: str,
) -> VisionCircle:
    """Detect only the direct top-view ball and exclude the lower mirror."""

    if capture_id not in COARSE_TOP_CAPTURE_IDS:
        raise ValueError(f"{capture_id} is not a V6 coarse top capture")
    if gray_image.ndim != 2 or not rois:
        raise ValueError("coarse top recognition requires a grayscale image and ROI")

    try:
        from .python_vision_geometry.position_bias_planner import (
            auto_detect_gross_ball_circle,
            gross_auto_feature_spec,
        )
    except ImportError:
        from python_vision_geometry.position_bias_planner import (  # type: ignore[no-redef]
            auto_detect_gross_ball_circle,
            gross_auto_feature_spec,
        )

    height, width = gray_image.shape
    direct_view_max_y = int(math.floor(height * DIRECT_TOP_VIEW_MAX_Y_FRACTION))
    roi = rois[0].normalized
    x1 = max(0, int(math.floor(roi.x1)))
    y1 = max(0, int(math.floor(roi.y1)))
    x2 = min(width, int(math.ceil(roi.x2)))
    y2 = min(direct_view_max_y, int(math.ceil(roi.y2)))
    if x2 <= x1 or y2 <= y1:
        raise ValueError(
            "coarse top ROI does not overlap the upper direct camera view; "
            "the lower mirror is not valid for this capture"
        )

    spec = gross_auto_feature_spec({}, capture_id)
    spec["roi"] = [x1, y1, x2, y2]
    gray_float = np.asarray(gray_image, dtype=float)
    if gray_float.size and float(np.nanmax(gray_float)) <= 1.0:
        gray_float = gray_float * 255.0
    gray_u8 = np.clip(gray_float, 0.0, 255.0).astype(np.uint8)
    shape = auto_detect_gross_ball_circle(gray_u8, spec, capture_id)
    if float(shape["y"]) >= direct_view_max_y:
        raise ValueError("coarse top detection entered the lower mirror region")
    return VisionCircle(
        x=float(shape["x"]),
        y=float(shape["y"]),
        radius=float(shape["radius"]),
        score=float(shape["score"]),
        label=f"{capture_id} upper direct-view ball",
    )


def recognize_coarse_top_ball(
    gray_image: np.ndarray,
    rois: Sequence[VisionROI],
    capture_id: str,
) -> VisionRecognitionResult:
    try:
        circle = detect_coarse_top_ball_circle(gray_image, rois, capture_id)
    except (ImportError, RuntimeError, TypeError, ValueError) as exc:
        return VisionRecognitionResult(
            algorithm_name="v6_coarse_top_ball",
            display_name="V6 coarse top ball",
            lines=(),
            intersections=(),
            circles=(),
            rectangles=(),
            semicircles=(),
            silhouettes=(),
            message=f"Coarse top ball not detected; lower mirror excluded: {exc}",
        )
    return VisionRecognitionResult(
        algorithm_name="v6_coarse_top_ball",
        display_name="V6 coarse top ball",
        lines=(),
        intersections=(),
        circles=(circle,),
        rectangles=(),
        semicircles=(),
        silhouettes=(),
        message=(
            f"Detected {capture_id} ball in the upper direct camera view; "
            "lower mirror excluded"
        ),
    )


def detect_side_trench_ruler_lines(
    gray_image: np.ndarray,
    rois: Sequence[VisionROI],
) -> tuple[VisionLine, VisionLine] | tuple[()]:
    """Propose the two horizontal mirror lines whose physical spacing is 300 um."""

    if gray_image.ndim != 2 or not rois:
        return ()
    height, width = gray_image.shape
    x1 = max(0, int(min(roi.normalized.x1 for roi in rois)))
    y1 = max(0, int(min(roi.normalized.y1 for roi in rois)))
    x2 = min(width, int(math.ceil(max(roi.normalized.x2 for roi in rois))))
    y2 = min(height, int(math.ceil(max(roi.normalized.y2 for roi in rois))))
    if x2 - x1 < 40 or y2 - y1 < 30:
        return ()

    crop = np.clip(gray_image[y1:y2, x1:x2], 0.0, 1.0).astype(np.float64)
    if crop.shape[0] < 9:
        return ()
    vertical_gradient = np.abs(np.diff(crop, axis=0))
    coverage = max(1, int(vertical_gradient.shape[1] * 0.2))
    # Average the strongest 20% of each row's vertical edges. This keeps a
    # trench line that is visible on only one side of a tower while rejecting
    # small local ball highlights.
    gradient = np.mean(np.sort(vertical_gradient, axis=1)[:, -coverage:], axis=1)
    candidates = [
        index
        for index in range(5, gradient.size - 5)
        if gradient[index] == np.max(gradient[index - 4 : index + 5])
    ]
    candidates.sort(key=lambda index: float(gradient[index]), reverse=True)
    pair: tuple[int, int] | None = None
    pair_score = -math.inf
    for first in candidates[:30]:
        for second in candidates[:30]:
            separation = abs(second - first)
            if not 50 <= separation <= 1000:
                continue
            ordered = tuple(sorted((first, second)))
            score = float(gradient[ordered[0]] + gradient[ordered[1]])
            if score > pair_score:
                pair = ordered
                pair_score = score
    if pair is None:
        return ()

    floor_raw_y = float(y1 + pair[0])
    top_raw_y = float(y1 + pair[1])
    floor = VisionLine(
        x1=float(x1),
        y1=floor_raw_y,
        x2=float(x2 - 1),
        y2=floor_raw_y,
        score=float(gradient[pair[0]]),
        label="trench floor proposal (mirror raw top)",
    )
    top = VisionLine(
        x1=float(x1),
        y1=top_raw_y,
        x2=float(x2 - 1),
        y2=top_raw_y,
        score=float(gradient[pair[1]]),
        label="trench top proposal (mirror raw bottom)",
    )
    return floor, top


def photo_image_from_grayscale(gray_image: np.ndarray, *, master: tk.Misc | None = None) -> tk.PhotoImage:
    """Build a Tk image from a normalized grayscale array without Pillow."""

    if gray_image.ndim != 2:
        raise ValueError(f"expected a 2D grayscale image, got shape {gray_image.shape}")
    height, width = gray_image.shape
    if width <= 0 or height <= 0:
        raise ValueError("cannot display an empty image")
    gray_u8 = np.clip(np.rint(gray_image * 255.0), 0, 255).astype(np.uint8, copy=False)
    header = f"P5\n{width} {height}\n255\n".encode("ascii")
    return tk.PhotoImage(master=master, data=header + gray_u8.tobytes())


def load_display_photo_image(
    path: str | Path,
    gray_image: np.ndarray | None = None,
    *,
    master: tk.Misc | None = None,
) -> tk.PhotoImage:
    """Load an image for Tk display, falling back to grayscale PGM for BMP captures."""

    try:
        return tk.PhotoImage(master=master, file=str(path))
    except tk.TclError:
        if gray_image is None:
            gray_image = read_grayscale_image(path)
        return photo_image_from_grayscale(gray_image, master=master)


def recognize_shapes(
    gray_image: np.ndarray,
    algorithm_name: str = DEFAULT_RECOGNIZER_NAME,
    rois: tuple[VisionROI, ...] | list[VisionROI] = (),
    *,
    geometry_sensitivity: float | None = None,
    bright_rectangle_sensitivity: float | None = None,
    silhouette_algorithm_name: str | None = None,
    silhouette_sensitivity: float | None = None,
) -> VisionRecognitionResult:
    recognizer = RECOGNIZER_BY_NAME[algorithm_name]
    geometry_sensitivity_value = (
        DEFAULT_GEOMETRY_SENSITIVITY
        if geometry_sensitivity is None
        else clamp_geometry_sensitivity(geometry_sensitivity)
    )
    bright_rectangle_sensitivity_value = (
        DEFAULT_BRIGHT_RECTANGLE_SENSITIVITY
        if bright_rectangle_sensitivity is None
        else clamp_bright_rectangle_sensitivity(bright_rectangle_sensitivity)
    )
    analysis, scale = downsample_for_recognition(gray_image)
    mask = recognition_mask(
        analysis,
        recognizer,
        sensitivity=(
            silhouette_sensitivity
            if recognizer.mode == "dark_silhouette"
            else geometry_sensitivity_value
        ),
    )
    silhouette_recognizer = None
    if silhouette_algorithm_name:
        silhouette_recognizer = RECOGNIZER_BY_NAME[silhouette_algorithm_name]
    elif recognizer.mode == "dark_silhouette":
        silhouette_recognizer = recognizer
    selected_rois = tuple(rois)
    edge_rois = tuple(roi for roi in selected_rois if roi.kind in {"box", "edges"})
    rectangle_rois = tuple(roi for roi in selected_rois if roi.kind == "rectangle")
    circle_rois = tuple(roi for roi in selected_rois if roi.kind == "circle")
    semicircle_rois = tuple(roi for roi in selected_rois if roi.kind == "semicircle")
    silhouette_rois = tuple(roi for roi in selected_rois if roi.kind == "silhouette")
    if selected_rois:
        line_mask = mask_for_rois(mask, edge_rois, scale) if edge_rois else np.zeros_like(mask, dtype=bool)
        rectangle_mask = (
            mask_for_rois(mask, rectangle_rois, scale) if rectangle_rois else np.zeros_like(mask, dtype=bool)
        )
        circle_mask = mask_for_rois(mask, circle_rois, scale) if circle_rois else np.zeros_like(mask, dtype=bool)
        semicircle_mask = (
            mask_for_rois(mask, semicircle_rois, scale) if semicircle_rois else np.zeros_like(mask, dtype=bool)
        )
    else:
        line_mask = mask
        rectangle_mask = np.zeros_like(mask, dtype=bool)
        circle_mask = mask
        semicircle_mask = np.zeros_like(mask, dtype=bool)
    height, width = analysis.shape

    lines = [
        *_projection_lines(line_mask, scale),
        *_component_lines(line_mask, scale),
        *_library_hough_lines(line_mask, edge_rois, scale, recognizer, geometry_sensitivity_value),
    ]
    lines = tuple(_dedupe_lines(lines))
    rectangle_lines = _dedupe_lines(
        [
            *_projection_lines(rectangle_mask, scale),
            *_component_lines(rectangle_mask, scale),
            *_library_hough_lines(
                rectangle_mask,
                rectangle_rois,
                scale,
                recognizer,
                geometry_sensitivity_value,
            ),
        ]
    )
    rectangles = tuple(
        _dedupe_rectangles(
            [
                *_roi_rectangles(rectangle_lines, rectangle_rois),
                *_roi_bright_rectangles(
                    analysis,
                    rectangle_rois,
                    scale,
                    bright_rectangle_sensitivity_value,
                ),
            ]
        )
    )
    circles = tuple(
        _dedupe_circles(
            [
                *_component_circles(circle_mask, scale),
                *_library_hough_circles(
                    analysis,
                    circle_mask,
                    circle_rois,
                    scale,
                    recognizer,
                    geometry_sensitivity_value,
                ),
            ]
        )
    )
    semicircles = tuple(_roi_semicircles(semicircle_mask, semicircle_rois, scale))
    if silhouette_recognizer is not None:
        silhouette_source_mask = (
            mask
            if silhouette_recognizer is recognizer
            else recognition_mask(analysis, silhouette_recognizer, sensitivity=silhouette_sensitivity)
        )
        if selected_rois:
            silhouette_mask = (
                mask_for_rois(silhouette_source_mask, silhouette_rois, scale)
                if silhouette_rois
                else np.zeros_like(mask, dtype=bool)
            )
        else:
            silhouette_mask = silhouette_source_mask
        silhouettes = tuple(_component_silhouettes(silhouette_mask, scale))
    else:
        silhouettes = ()
    intersections = tuple(_line_intersections(lines, width * scale, height * scale))
    if edge_rois:
        intersections = tuple(
            intersection
            for intersection in intersections
            if any(point_in_roi(intersection.x, intersection.y, roi) for roi in edge_rois)
        )
    algorithm_name_text = recognizer.name
    display_name_text = recognizer.display_name
    if silhouette_recognizer is not None and silhouette_recognizer is not recognizer:
        algorithm_name_text = f"{recognizer.name}+{silhouette_recognizer.name}"
        display_name_text = f"{recognizer.display_name} + {silhouette_recognizer.display_name}"
    message = (
        f"{display_name_text}: "
        f"{len(lines)} lines, {len(intersections)} intersections, "
        f"{len(circles)} circles, {len(rectangles)} rectangles, "
        f"{len(semicircles)} semicircles, {len(silhouettes)} silhouettes"
        + (f" inside {len(selected_rois)} ROI" if selected_rois else "")
    )
    return VisionRecognitionResult(
        algorithm_name=algorithm_name_text,
        display_name=display_name_text,
        lines=lines,
        intersections=intersections,
        circles=circles,
        rectangles=rectangles,
        semicircles=semicircles,
        silhouettes=silhouettes,
        message=message,
    )


def downsample_for_recognition(gray_image: np.ndarray) -> tuple[np.ndarray, int]:
    if gray_image.ndim != 2:
        raise ValueError("shape recognition expects a 2D grayscale image")
    return gray_image, 1


def mask_for_rois(mask: np.ndarray, rois: tuple[VisionROI, ...], scale: int) -> np.ndarray:
    if not rois:
        return mask
    roi_mask = roi_mask_for_shape(mask.shape, rois, scale)
    if roi_mask is None:
        return np.zeros_like(mask, dtype=bool)
    return mask & roi_mask


def roi_mask_for_shape(
    image_shape: tuple[int, int],
    rois: tuple[VisionROI, ...],
    scale: int,
) -> np.ndarray | None:
    if not rois:
        return None
    height, width = image_shape
    mask = np.zeros(image_shape, dtype=bool)
    yy, xx = np.ogrid[:height, :width]
    full_x = xx * scale
    full_y = yy * scale
    for roi in rois:
        normalized = roi.normalized
        if normalized.width < 2.0 or normalized.height < 2.0:
            continue
        if normalized.kind == "circle":
            mask |= circle_roi_mask(full_x, full_y, normalized)
        elif normalized.kind == "semicircle":
            mask |= circle_roi_mask(full_x, full_y, normalized)
        else:
            mask |= (
                (full_x >= normalized.x1)
                & (full_x <= normalized.x2)
                & (full_y >= normalized.y1)
                & (full_y <= normalized.y2)
            )
    return mask


def point_in_roi(x: float, y: float, roi: VisionROI) -> bool:
    normalized = roi.normalized
    if normalized.kind == "circle":
        return bool(circle_roi_mask(np.asarray([[x]]), np.asarray([[y]]), normalized)[0, 0])
    if normalized.kind == "semicircle":
        return bool(circle_roi_mask(np.asarray([[x]]), np.asarray([[y]]), normalized)[0, 0])
    return normalized.x1 <= x <= normalized.x2 and normalized.y1 <= y <= normalized.y2


def parse_short_edge_length_um(value: Any) -> float:
    try:
        length_um = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("Short edge um must be a positive finite number.") from exc
    if length_um <= 0.0 or not math.isfinite(length_um):
        raise ValueError("Short edge um must be a positive finite number.")
    return length_um


def rectangle_measurement_corners(rectangle: VisionRectangle) -> tuple[tuple[float, float], ...]:
    if len(rectangle.corners) == 4:
        return tuple((float(x), float(y)) for x, y in rectangle.corners)
    return (
        (float(rectangle.x1), float(rectangle.y1)),
        (float(rectangle.x2), float(rectangle.y1)),
        (float(rectangle.x2), float(rectangle.y2)),
        (float(rectangle.x1), float(rectangle.y2)),
    )


def selected_rectangle_short_edge(
    rectangle: VisionRectangle,
    circle_x: float,
    circle_y: float,
) -> VisionRectangleShortEdge:
    corners = rectangle_measurement_corners(rectangle)
    edges: list[VisionRectangleShortEdge] = []
    for index, start in enumerate(corners):
        end = corners[(index + 1) % len(corners)]
        midpoint = (0.5 * (start[0] + end[0]), 0.5 * (start[1] + end[1]))
        length_px = math.hypot(end[0] - start[0], end[1] - start[1])
        if length_px > 0.0:
            edges.append(
                VisionRectangleShortEdge(
                    start=start,
                    end=end,
                    midpoint=midpoint,
                    length_px=float(length_px),
                )
            )
    if not edges:
        raise ValueError("Rectangle has no measurable edge.")
    short_edges = sorted(edges, key=lambda edge: edge.length_px)[:2]
    return min(
        short_edges,
        key=lambda edge: math.hypot(edge.midpoint[0] - circle_x, edge.midpoint[1] - circle_y),
    )


def rectangle_circle_measurement(
    rectangle: VisionRectangle,
    circle: VisionCircleReference,
    short_edge_length_um: float,
) -> VisionRectangleCircleMeasurement:
    length_um = parse_short_edge_length_um(short_edge_length_um)
    short_edge = selected_rectangle_short_edge(rectangle, circle.x, circle.y)
    if short_edge.length_px <= 0.0 or not math.isfinite(short_edge.length_px):
        raise ValueError("Rectangle short edge must have a positive finite pixel length.")
    um_per_pixel = length_um / short_edge.length_px
    dx_px = short_edge.midpoint[0] - circle.x
    dy_px = short_edge.midpoint[1] - circle.y
    distance_px = math.hypot(dx_px, dy_px)
    return VisionRectangleCircleMeasurement(
        short_edge_length_um=float(length_um),
        um_per_pixel=float(um_per_pixel),
        short_edge=short_edge,
        circle=circle,
        dx_px=float(dx_px),
        dy_px=float(dy_px),
        distance_px=float(distance_px),
        dx_um=float(dx_px * um_per_pixel),
        dy_um=float(dy_px * um_per_pixel),
        distance_um=float(distance_px * um_per_pixel),
    )


def _axis_delta_payload(
    *,
    x: float,
    y: float,
    origin_x: float,
    origin_y: float,
    um_per_pixel: float,
) -> dict[str, Any]:
    dx_px = float(x - origin_x)
    dy_px = float(y - origin_y)
    distance_px = float(math.hypot(dx_px, dy_px))
    return {
        "px": {
            "dx": dx_px,
            "dy": dy_px,
            "distance": distance_px,
        },
        "um": {
            "x": float(dx_px * um_per_pixel),
            "y": float(dy_px * um_per_pixel),
            "dx": float(dx_px * um_per_pixel),
            "dy": float(dy_px * um_per_pixel),
            "distance": float(distance_px * um_per_pixel),
        },
    }


def relative_measurement_payload_from_measurements(
    measurements: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    if not measurements:
        return None
    origin_measurement = measurements[0]
    try:
        um_per_pixel = float(origin_measurement["um_per_pixel"])
        origin_center = origin_measurement["circle_center"]
        origin_x = float(origin_center["x"])
        origin_y = float(origin_center["y"])
        short_edge = origin_measurement["short_edge"]
        midpoint = short_edge["midpoint"]
        midpoint_x = float(midpoint["x"])
        midpoint_y = float(midpoint["y"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Measurement payload is missing required relative-position fields.") from exc

    if um_per_pixel <= 0.0 or not math.isfinite(um_per_pixel):
        raise ValueError("Measurement payload has invalid um/px conversion.")

    edge_delta = _axis_delta_payload(
        x=midpoint_x,
        y=midpoint_y,
        origin_x=origin_x,
        origin_y=origin_y,
        um_per_pixel=um_per_pixel,
    )
    circle_payloads: list[dict[str, Any]] = []
    for index, measurement in enumerate(measurements, start=1):
        try:
            circle_center = measurement["circle_center"]
            circle_x = float(circle_center["x"])
            circle_y = float(circle_center["y"])
            radius = circle_center.get("radius")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Measurement payload is missing a circle center.") from exc
        circle_delta = _axis_delta_payload(
            x=circle_x,
            y=circle_y,
            origin_x=origin_x,
            origin_y=origin_y,
            um_per_pixel=um_per_pixel,
        )
        relative_um = circle_delta["um"]
        circle_payloads.append(
            {
                "selection_index": index,
                "roi_index": measurement.get("circle_roi_index"),
                "source": measurement.get("circle_source"),
                "feature_role": measurement.get("circle_feature_role") or "ball_candidate",
                "center_px": {
                    "x": circle_x,
                    "y": circle_y,
                    "radius": radius,
                },
                "relative_px": circle_delta["px"],
                "relative_um": relative_um,
                "x_um": relative_um["x"],
                "y_um": relative_um["y"],
                "distance_um": relative_um["distance"],
            }
        )

    edge_relative_um = edge_delta["um"]
    return {
        "origin": "first_selected_circle_center",
        "coordinate_system": {
            "origin": "first_selected_circle_center",
            "x_axis": "image +x",
            "y_axis": "image +y",
            "units": "um",
        },
        "short_edge_length_um": origin_measurement.get("short_edge_length_um"),
        "um_per_pixel": um_per_pixel,
        "rectangle_roi_index": origin_measurement.get("rectangle_roi_index"),
        "rectangle_roi": origin_measurement.get("rectangle_roi"),
        "origin_circle": circle_payloads[0],
        "measure_edge": {
            "start_px": short_edge.get("start"),
            "end_px": short_edge.get("end"),
            "midpoint_px": {
                "x": midpoint_x,
                "y": midpoint_y,
            },
            "length_px": short_edge.get("length_px"),
            "midpoint_relative_px": edge_delta["px"],
            "midpoint_relative_um": edge_relative_um,
            "x_um": edge_relative_um["x"],
            "y_um": edge_relative_um["y"],
            "distance_um": edge_relative_um["distance"],
        },
        "edge_midpoint_relative_um": edge_relative_um,
        "circles": circle_payloads,
        "circle_positions_relative_um": [
            {
                "selection_index": circle["selection_index"],
                "roi_index": circle["roi_index"],
                "source": circle["source"],
                "feature_role": circle.get("feature_role"),
                "x_um": circle["x_um"],
                "y_um": circle["y_um"],
                "dx": circle["relative_um"]["dx"],
                "dy": circle["relative_um"]["dy"],
                "distance": circle["distance_um"],
            }
            for circle in circle_payloads
        ],
    }


def format_relative_measurement_for_yase(
    relative_measurement: dict[str, Any] | None,
    fallback_status: str,
) -> str:
    if relative_measurement is None:
        return fallback_status
    origin = relative_measurement["origin_circle"]
    edge_um = relative_measurement["edge_midpoint_relative_um"]
    parts = [
        (
            "Vision relative measurement: "
            f"origin circle ROI {origin.get('roi_index')} = (0.000, 0.000) um; "
            f"edge midpoint x={edge_um['x']:.3f} um, y={edge_um['y']:.3f} um, "
            f"dist={edge_um['distance']:.3f} um"
        )
    ]
    other_circles = relative_measurement["circles"][1:]
    if other_circles:
        circle_text = "; ".join(
            (
                f"circle ROI {circle.get('roi_index')} "
                f"x={circle['x_um']:.3f} um, y={circle['y_um']:.3f} um, "
                f"dist={circle['distance_um']:.3f} um"
            )
            for circle in other_circles
        )
        parts.append(f"other circles vs origin: {circle_text}")
    return " | ".join(parts)


def default_feature_role_for_selection(shape_kind: str, source: str) -> str:
    """Return the V6 semantic role implied by a selected recognition item."""

    normalized_kind = str(shape_kind or "").strip()
    normalized_source = str(source or "").strip()
    if normalized_kind == "rectangle":
        return "laser_reference"
    if normalized_kind == "line" or normalized_source == "side_reference_line":
        return "trench_top_surface"
    if normalized_kind == "circle" or normalized_source == "silhouette_circle":
        return "ball_candidate"
    if normalized_kind == "silhouette":
        return "object_candidate"
    if normalized_kind == "intersection":
        return "fiducial_candidate"
    return "unknown"


def initial_selected_recognition_items(session: dict[str, Any]) -> list[dict[str, Any]]:
    selected = session.get("selected_recognition")
    if not isinstance(selected, dict):
        return []
    items: list[dict[str, Any]] = []
    for roi_key in sorted(selected):
        values = selected.get(roi_key)
        if isinstance(values, list):
            items.extend(value for value in values if isinstance(value, dict))
    return items


def recognition_payload_center(item: dict[str, Any]) -> tuple[float, float] | None:
    shape = item.get("shape")
    if not isinstance(shape, dict):
        return None
    kind = str(item.get("shape_kind") or "")
    try:
        if kind in {"circle", "intersection", "semicircle", "silhouette"}:
            return float(shape["x"]), float(shape["y"])
        if kind == "line":
            return (
                0.5 * (float(shape["x1"]) + float(shape["x2"])),
                0.5 * (float(shape["y1"]) + float(shape["y2"])),
            )
        if kind == "rectangle":
            corners = shape.get("corners")
            if isinstance(corners, list) and len(corners) == 4:
                return (
                    sum(float(corner["x"]) for corner in corners) / 4.0,
                    sum(float(corner["y"]) for corner in corners) / 4.0,
                )
            return (
                0.5 * (float(shape["x1"]) + float(shape["x2"])),
                0.5 * (float(shape["y1"]) + float(shape["y2"])),
            )
    except (KeyError, TypeError, ValueError):
        return None
    return None


def expected_role_for_capture(capture_id: str, shape_kind: str) -> str:
    capture_key = str(capture_id)
    roles = {
        "2.1.1": "ball_1_gross_ball",
        "2.4.1": "laser_reference",
        "2.5.1": "ball_1_top_ball",
        "2.6.1": "ball_1_side_ball",
        "4.1.1": "ball_2_gross_ball",
        "4.4.1": "laser_reference",
        "4.5.1": "ball_2_top_ball",
        "4.6.2": "ball_2_side_ball",
    }
    if capture_key in {"2.6.1", "4.6.2"} and shape_kind == "line":
        return "trench_top_surface"
    return roles.get(capture_key, default_feature_role_for_selection(shape_kind, shape_kind))


def circle_roi_mask(full_x: np.ndarray, full_y: np.ndarray, roi: VisionROI) -> np.ndarray:
    center_x = 0.5 * (roi.x1 + roi.x2)
    center_y = 0.5 * (roi.y1 + roi.y2)
    radius = 0.5 * max(roi.width, roi.height)
    return (full_x - center_x) ** 2 + (full_y - center_y) ** 2 <= radius**2


def semicircle_mask(full_x: np.ndarray, full_y: np.ndarray, roi: VisionROI) -> np.ndarray:
    center_x = 0.5 * (roi.x1 + roi.x2)
    center_y = 0.5 * (roi.y1 + roi.y2)
    radius = 0.5 * max(roi.width, roi.height)
    disk = (full_x - center_x) ** 2 + (full_y - center_y) ** 2 <= radius**2
    if roi.orientation == "left":
        return disk & (full_x <= center_x)
    if roi.orientation == "right":
        return disk & (full_x >= center_x)
    if roi.orientation == "up":
        return disk & (full_y <= center_y)
    if roi.orientation == "down":
        return disk & (full_y >= center_y)
    return disk


def recognition_mask(
    gray_image: np.ndarray,
    recognizer: VisionRecognizer,
    *,
    sensitivity: float | None = None,
) -> np.ndarray:
    if not gray_image.size:
        return np.zeros_like(gray_image, dtype=bool)

    if recognizer.mode == "bright":
        threshold = float(np.quantile(gray_image, recognizer.quantile))
        mask = gray_image > threshold
        if not mask.any():
            mask = gray_image >= float(gray_image.max())
    elif recognizer.mode == "dark":
        threshold = float(np.quantile(gray_image, recognizer.quantile))
        mask = gray_image < threshold
        if not mask.any():
            mask = gray_image <= float(gray_image.min())
    elif recognizer.mode == "gradient":
        gradient = gradient_magnitude(gray_image)
        threshold = float(np.quantile(gradient, recognizer.quantile))
        mask = gradient > threshold
        if not mask.any():
            mask = gradient >= float(gradient.max())
    elif recognizer.mode == "dark_adaptive":
        feature = np.maximum(local_mean(gray_image, radius=9) - gray_image, 0.0)
        mask = high_feature_mask(feature, recognizer.quantile)
    elif recognizer.mode == "opencv_adaptive_dark":
        mask = opencv_adaptive_dark_mask(gray_image)
        if not mask.any():
            feature = np.maximum(local_mean(gray_image, radius=17) - gray_image, 0.0)
            mask = high_feature_mask(feature, recognizer.quantile)
    elif recognizer.mode in {"opencv_hough", "opencv_hough_sized"}:
        mask = opencv_canny_mask(gray_image, sensitivity=sensitivity)
        if not mask.any():
            mask = high_feature_mask(gradient_magnitude(gray_image), recognizer.quantile)
    elif recognizer.mode in {"skimage_hough", "skimage_hough_sized"}:
        mask = skimage_canny_mask(gray_image, sensitivity=sensitivity)
        if not mask.any():
            mask = high_feature_mask(gradient_magnitude(gray_image), recognizer.quantile)
    elif recognizer.mode == "background_dark":
        feature = np.maximum(local_mean(gray_image, radius=23) - gray_image, 0.0)
        mask = high_feature_mask(feature, recognizer.quantile)
    elif recognizer.mode == "dark_rim":
        dark_feature = np.maximum(local_mean(gray_image, radius=11) - gray_image, 0.0)
        feature = gradient_magnitude(gray_image) * dark_feature
        mask = high_feature_mask(feature, recognizer.quantile)
    elif recognizer.mode == "dark_multiscale":
        feature = np.maximum.reduce(
            (
                np.maximum(local_mean(gray_image, radius=5) - gray_image, 0.0),
                np.maximum(local_mean(gray_image, radius=13) - gray_image, 0.0),
                np.maximum(local_mean(gray_image, radius=27) - gray_image, 0.0),
            )
        )
        mask = high_feature_mask(feature, recognizer.quantile)
    elif recognizer.mode == "dark_silhouette":
        quantile = recognizer.quantile if sensitivity is None else clamp_silhouette_sensitivity(sensitivity)
        threshold_cap = 0.18 if sensitivity is None else 0.04 + 0.47 * quantile
        threshold = min(threshold_cap, float(np.quantile(gray_image, quantile)))
        mask = gray_image < threshold
        if not mask.any():
            mask = gray_image <= float(gray_image.min())
    elif recognizer.mode == "adaptive_contrast":
        feature = np.abs(gray_image - local_mean(gray_image, radius=9))
        mask = high_feature_mask(feature, recognizer.quantile)
    else:
        raise ValueError(f"unknown recognizer mode: {recognizer.mode}")

    cleaned = clean_binary_mask(mask)
    return cleaned if cleaned.any() else mask


def clamp_silhouette_sensitivity(value: float) -> float:
    return min(MAX_SILHOUETTE_SENSITIVITY, max(MIN_SILHOUETTE_SENSITIVITY, float(value)))


def clamp_geometry_sensitivity(value: float) -> float:
    return min(MAX_GEOMETRY_SENSITIVITY, max(MIN_GEOMETRY_SENSITIVITY, float(value)))


def clamp_bright_rectangle_sensitivity(value: float) -> float:
    return min(MAX_BRIGHT_RECTANGLE_SENSITIVITY, max(MIN_BRIGHT_RECTANGLE_SENSITIVITY, float(value)))


def sensitivity_from_scale_x(x: float, width: float, minimum: float, maximum: float) -> float:
    slider_padding = 8.0
    usable_width = max(float(width) - 2.0 * slider_padding, 1.0)
    position = min(1.0, max(0.0, (float(x) - slider_padding) / usable_width))
    return float(minimum) + position * (float(maximum) - float(minimum))


def geometry_sensitivity_from_scale_x(x: float, width: float) -> float:
    return clamp_geometry_sensitivity(
        sensitivity_from_scale_x(
            x,
            width,
            MIN_GEOMETRY_SENSITIVITY,
            MAX_GEOMETRY_SENSITIVITY,
        )
    )


def bright_rectangle_sensitivity_from_scale_x(x: float, width: float) -> float:
    return clamp_bright_rectangle_sensitivity(
        sensitivity_from_scale_x(
            x,
            width,
            MIN_BRIGHT_RECTANGLE_SENSITIVITY,
            MAX_BRIGHT_RECTANGLE_SENSITIVITY,
        )
    )


def silhouette_sensitivity_from_scale_x(x: float, width: float) -> float:
    return clamp_silhouette_sensitivity(
        sensitivity_from_scale_x(
            x,
            width,
            MIN_SILHOUETTE_SENSITIVITY,
            MAX_SILHOUETTE_SENSITIVITY,
        )
    )


def high_feature_mask(feature: np.ndarray, quantile: float) -> np.ndarray:
    feature = np.nan_to_num(feature.astype(float, copy=False), nan=0.0, posinf=0.0, neginf=0.0)
    if not feature.size or float(feature.max()) <= 1e-12:
        return np.zeros_like(feature, dtype=bool)
    threshold = float(np.quantile(feature, quantile))
    mask = feature > threshold
    if not mask.any():
        mask = feature >= float(feature.max())
    return mask


def grayscale_to_uint8(gray_image: np.ndarray) -> np.ndarray:
    return np.rint(np.clip(gray_image, 0.0, 1.0) * 255.0).astype(np.uint8)


def opencv_adaptive_dark_mask(gray_image: np.ndarray) -> np.ndarray:
    gray_u8 = grayscale_to_uint8(gray_image)
    min_dimension = max(3, min(gray_u8.shape))
    block_size = max(15, min(151, int(min_dimension // 30) | 1))
    if block_size >= min_dimension:
        block_size = max(3, (min_dimension - 1) | 1)
    thresholded = cv2.adaptiveThreshold(
        gray_u8,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        block_size,
        3,
    )
    return thresholded > 0


def opencv_canny_mask(gray_image: np.ndarray, *, sensitivity: float | None = None) -> np.ndarray:
    sensitivity_value = (
        DEFAULT_GEOMETRY_SENSITIVITY
        if sensitivity is None
        else clamp_geometry_sensitivity(sensitivity)
    )
    gray_u8 = grayscale_to_uint8(gray_image)
    blurred = cv2.medianBlur(gray_u8, 5) if min(gray_u8.shape) >= 5 else gray_u8
    low_threshold = int(round(85.0 - 60.0 * sensitivity_value))
    high_threshold = int(round(190.0 - 105.0 * sensitivity_value))
    edges = cv2.Canny(blurred, max(10, low_threshold), max(30, high_threshold))
    return edges > 0


def skimage_canny_mask(gray_image: np.ndarray, *, sensitivity: float | None = None) -> np.ndarray:
    sensitivity_value = (
        DEFAULT_GEOMETRY_SENSITIVITY
        if sensitivity is None
        else clamp_geometry_sensitivity(sensitivity)
    )
    low_threshold = max(0.01, 0.075 - 0.05 * sensitivity_value)
    high_threshold = max(low_threshold + 0.02, 0.23 - 0.15 * sensitivity_value)
    return np.asarray(
        canny(
            gray_image.astype(float, copy=False),
            sigma=2.0,
            low_threshold=low_threshold,
            high_threshold=high_threshold,
        ),
        dtype=bool,
    )


def gradient_magnitude(gray_image: np.ndarray) -> np.ndarray:
    gy, gx = np.gradient(gray_image.astype(float, copy=False))
    return np.hypot(gx, gy)


def local_mean(image: np.ndarray, radius: int = 9) -> np.ndarray:
    pad = int(max(radius, 1))
    padded = np.pad(image, pad, mode="edge")
    integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant").cumsum(axis=0).cumsum(axis=1)
    size = 2 * pad + 1
    total = (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    )
    return total / float(size * size)


def clean_binary_mask(mask: np.ndarray) -> np.ndarray:
    if mask.size == 0:
        return mask.astype(bool)
    padded = np.pad(mask.astype(np.uint8), 1)
    neighbors = (
        padded[:-2, :-2]
        + padded[:-2, 1:-1]
        + padded[:-2, 2:]
        + padded[1:-1, :-2]
        + padded[1:-1, 1:-1]
        + padded[1:-1, 2:]
        + padded[2:, :-2]
        + padded[2:, 1:-1]
        + padded[2:, 2:]
    )
    return mask & (neighbors >= 2)


def dilate_binary_mask(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    result = mask.astype(bool, copy=False)
    for _ in range(max(0, iterations)):
        padded = np.pad(result, 1)
        result = (
            padded[:-2, :-2]
            | padded[:-2, 1:-1]
            | padded[:-2, 2:]
            | padded[1:-1, :-2]
            | padded[1:-1, 1:-1]
            | padded[1:-1, 2:]
            | padded[2:, :-2]
            | padded[2:, 1:-1]
            | padded[2:, 2:]
        )
    return result


def fill_binary_holes(mask: np.ndarray) -> np.ndarray:
    if mask.size == 0:
        return mask.astype(bool)
    result = mask.astype(bool, copy=True)
    background = ~result
    height, width = result.shape
    visited = np.zeros(result.shape, dtype=bool)
    stack: list[tuple[int, int]] = []

    for x in range(width):
        if background[0, x]:
            stack.append((0, x))
        if height > 1 and background[height - 1, x]:
            stack.append((height - 1, x))
    for y in range(1, max(height - 1, 1)):
        if background[y, 0]:
            stack.append((y, 0))
        if width > 1 and background[y, width - 1]:
            stack.append((y, width - 1))

    while stack:
        y, x = stack.pop()
        if visited[y, x] or not background[y, x]:
            continue
        visited[y, x] = True
        if y > 0:
            stack.append((y - 1, x))
        if y + 1 < height:
            stack.append((y + 1, x))
        if x > 0:
            stack.append((y, x - 1))
        if x + 1 < width:
            stack.append((y, x + 1))

    return result | (background & ~visited)


def _projection_lines(mask: np.ndarray, scale: int) -> list[VisionLine]:
    height, width = mask.shape
    lines: list[VisionLine] = []

    row_threshold = max(10, int(width * 0.18))
    row_groups = _consecutive_groups(np.flatnonzero(mask.sum(axis=1) >= row_threshold))
    for group in row_groups[:12]:
        rows = np.asarray(group)
        xs = np.flatnonzero(mask[rows].any(axis=0))
        if xs.size < row_threshold:
            continue
        y = float(rows.mean() * scale)
        lines.append(
            VisionLine(
                x1=float(xs.min() * scale),
                y1=y,
                x2=float(xs.max() * scale),
                y2=y,
                score=min(1.0, float(xs.size / max(width, 1))),
                label="horizontal",
            )
        )

    column_threshold = max(10, int(height * 0.18))
    column_groups = _consecutive_groups(np.flatnonzero(mask.sum(axis=0) >= column_threshold))
    for group in column_groups[:12]:
        columns = np.asarray(group)
        ys = np.flatnonzero(mask[:, columns].any(axis=1))
        if ys.size < column_threshold:
            continue
        x = float(columns.mean() * scale)
        lines.append(
            VisionLine(
                x1=x,
                y1=float(ys.min() * scale),
                x2=x,
                y2=float(ys.max() * scale),
                score=min(1.0, float(ys.size / max(height, 1))),
                label="vertical",
            )
        )
    return lines


def _component_lines(mask: np.ndarray, scale: int) -> list[VisionLine]:
    lines: list[VisionLine] = []
    for component in _connected_components(mask, min_pixels=18):
        xs, ys = component
        if xs.size < 18:
            continue
        width = float(xs.max() - xs.min() + 1)
        height = float(ys.max() - ys.min() + 1)
        if max(width, height) < 18:
            continue
        coords = np.column_stack((xs.astype(float), ys.astype(float)))
        center = coords.mean(axis=0)
        centered = coords - center
        cov = centered.T @ centered / max(len(coords) - 1, 1)
        values, vectors = np.linalg.eigh(cov)
        order = np.argsort(values)
        major = max(float(values[order[-1]]), 1e-9)
        minor = max(float(values[order[0]]), 1e-9)
        if major / minor < 10.0:
            continue
        direction = vectors[:, order[-1]]
        projection = centered @ direction
        start = center + direction * projection.min()
        end = center + direction * projection.max()
        if float(np.linalg.norm(end - start)) < 24.0:
            continue
        lines.append(
            VisionLine(
                x1=float(start[0] * scale),
                y1=float(start[1] * scale),
                x2=float(end[0] * scale),
                y2=float(end[1] * scale),
                score=min(1.0, major / (major + minor)),
                label="component",
            )
        )
    return lines


def _component_circles(mask: np.ndarray, scale: int) -> list[VisionCircle]:
    circles: list[VisionCircle] = []
    for xs, ys in _connected_components(mask, min_pixels=24):
        width = float(xs.max() - xs.min() + 1)
        height = float(ys.max() - ys.min() + 1)
        radius = 0.25 * (width + height)
        if radius < 5.0:
            continue
        aspect = max(width, height) / max(min(width, height), 1.0)
        if aspect > 1.35:
            continue
        area = float(xs.size)
        circumference = max(2.0 * math.pi * radius, 1.0)
        fill_ratio = area / max(width * height, 1.0)
        if fill_ratio < 0.08 or fill_ratio > 0.85:
            continue
        circles.append(
            VisionCircle(
                x=float(0.5 * (xs.min() + xs.max()) * scale),
                y=float(0.5 * (ys.min() + ys.max()) * scale),
                radius=float(radius * scale),
                score=min(1.0, area / circumference),
                label="component",
            )
        )
    circles.sort(key=lambda circle: circle.score, reverse=True)
    return circles[:20]


def _library_hough_lines(
    mask: np.ndarray,
    rois: tuple[VisionROI, ...],
    scale: int,
    recognizer: VisionRecognizer,
    sensitivity: float,
) -> list[VisionLine]:
    if recognizer.mode in {"opencv_hough", "opencv_hough_sized"}:
        return _opencv_hough_lines(mask, rois, scale, sensitivity)
    if recognizer.mode in {"skimage_hough", "skimage_hough_sized"}:
        return _skimage_hough_lines(mask, rois, scale, sensitivity)
    return []


def _library_hough_circles(
    gray_image: np.ndarray,
    mask: np.ndarray,
    rois: tuple[VisionROI, ...],
    scale: int,
    recognizer: VisionRecognizer,
    sensitivity: float,
) -> list[VisionCircle]:
    if not rois:
        return []
    if recognizer.mode == "opencv_hough":
        return _opencv_hough_circles(gray_image, rois, scale, sensitivity)
    if recognizer.mode == "opencv_hough_sized":
        return _opencv_hough_circles(gray_image, rois, scale, sensitivity, prefer_roi_radius=True)
    if recognizer.mode == "skimage_hough":
        return _skimage_hough_circles(mask, rois, scale, sensitivity)
    if recognizer.mode == "skimage_hough_sized":
        return _skimage_hough_circles(mask, rois, scale, sensitivity, prefer_roi_radius=True)
    return []


def _opencv_hough_lines(
    mask: np.ndarray,
    rois: tuple[VisionROI, ...],
    scale: int,
    sensitivity: float,
) -> list[VisionLine]:
    sensitivity = clamp_geometry_sensitivity(sensitivity)
    lines: list[VisionLine] = []
    for bounds, roi in _rectangular_search_bounds(mask.shape, rois, scale):
        x1, y1, x2, y2 = bounds
        crop = (mask[y1:y2, x1:x2].astype(np.uint8)) * 255
        if crop.size == 0 or not crop.any():
            continue
        crop_height, crop_width = crop.shape
        max_dimension = max(crop_width, crop_height)
        min_dimension = min(crop_width, crop_height)
        min_length = max(8, int((0.28 - 0.18 * sensitivity) * max_dimension))
        threshold = max(8, int((0.13 - 0.09 * sensitivity) * min_dimension))
        max_line_gap = max(4, int((0.015 + 0.095 * sensitivity) * max_dimension))
        raw_lines = cv2.HoughLinesP(
            crop,
            rho=1,
            theta=np.pi / 180.0,
            threshold=threshold,
            minLineLength=min_length,
            maxLineGap=max_line_gap,
        )
        if raw_lines is None:
            continue
        crop_lines: list[VisionLine] = []
        for raw_line in raw_lines[:30]:
            local_x1, local_y1, local_x2, local_y2 = (float(value) for value in raw_line[0])
            length = math.hypot(local_x2 - local_x1, local_y2 - local_y1)
            crop_lines.append(
                VisionLine(
                    x1=(x1 + local_x1) * scale,
                    y1=(y1 + local_y1) * scale,
                    x2=(x1 + local_x2) * scale,
                    y2=(y1 + local_y2) * scale,
                    score=min(1.0, length / max(crop_width, crop_height, 1)),
                    label="opencv hough",
                )
            )
        lines.extend(_merge_axis_aligned_hough_lines(crop_lines, roi, max_line_gap * scale))
    return lines


def _skimage_hough_lines(
    mask: np.ndarray,
    rois: tuple[VisionROI, ...],
    scale: int,
    sensitivity: float,
) -> list[VisionLine]:
    sensitivity = clamp_geometry_sensitivity(sensitivity)
    lines: list[VisionLine] = []
    for bounds, _roi in _rectangular_search_bounds(mask.shape, rois, scale):
        x1, y1, x2, y2 = bounds
        crop = mask[y1:y2, x1:x2]
        if crop.size == 0 or not crop.any():
            continue
        crop_height, crop_width = crop.shape
        max_dimension = max(crop_width, crop_height)
        min_dimension = min(crop_width, crop_height)
        raw_lines = probabilistic_hough_line(
            crop,
            threshold=max(8, int((0.07 - 0.04 * sensitivity) * min_dimension)),
            line_length=max(8, int((0.24 - 0.14 * sensitivity) * max_dimension)),
            line_gap=max(3, int((0.01 + 0.07 * sensitivity) * max_dimension)),
        )
        for (local_x1, local_y1), (local_x2, local_y2) in raw_lines[:30]:
            length = math.hypot(local_x2 - local_x1, local_y2 - local_y1)
            lines.append(
                VisionLine(
                    x1=(x1 + float(local_x1)) * scale,
                    y1=(y1 + float(local_y1)) * scale,
                    x2=(x1 + float(local_x2)) * scale,
                    y2=(y1 + float(local_y2)) * scale,
                    score=min(1.0, length / max(crop_width, crop_height, 1)),
                    label="skimage hough",
                )
            )
    return lines


def _opencv_hough_circles(
    gray_image: np.ndarray,
    rois: tuple[VisionROI, ...],
    scale: int,
    sensitivity: float,
    *,
    prefer_roi_radius: bool = False,
) -> list[VisionCircle]:
    sensitivity = clamp_geometry_sensitivity(sensitivity)
    circles: list[VisionCircle] = []
    for bounds, roi in _rectangular_search_bounds(gray_image.shape, rois, scale):
        x1, y1, x2, y2 = bounds
        crop = grayscale_to_uint8(gray_image[y1:y2, x1:x2])
        if crop.size == 0:
            continue
        crop = cv2.medianBlur(crop, 5) if min(crop.shape) >= 5 else crop
        roi_radius = max(5.0, 0.5 * min(roi.width, roi.height) / max(scale, 1))
        if prefer_roi_radius:
            min_radius = max(4, int(0.70 * roi_radius))
            max_radius = max(min_radius + 2, int(1.30 * roi_radius))
            min_distance = max(8.0, 0.45 * roi_radius)
        else:
            min_radius = max(4, int(0.35 * roi_radius))
            max_radius = max(min_radius + 2, int(1.08 * roi_radius))
            min_distance = max(12.0, 0.75 * roi_radius)
        raw_circles = cv2.HoughCircles(
            crop,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=min_distance,
            param1=max(35, int(round(120.0 - 70.0 * sensitivity))),
            param2=max(8, int(round(28.0 - 16.0 * sensitivity))),
            minRadius=min_radius,
            maxRadius=max_radius,
        )
        if raw_circles is None:
            continue
        crop_circles: list[VisionCircle] = []
        for local_x, local_y, radius in np.round(raw_circles[0, :8]).astype(float):
            circle_x = (x1 + local_x) * scale
            circle_y = (y1 + local_y) * scale
            circle_radius = radius * scale
            if not point_in_roi(circle_x, circle_y, roi):
                continue
            if prefer_roi_radius:
                score = _circle_roi_prior_score(
                    circle_x,
                    circle_y,
                    circle_radius,
                    roi,
                    roi_radius,
                    scale,
                    base_score=1.0,
                )
                label = "opencv hough size-prior"
            else:
                score = 0.90
                label = "opencv hough"
            crop_circles.append(
                VisionCircle(
                    x=float(circle_x),
                    y=float(circle_y),
                    radius=float(circle_radius),
                    score=float(score),
                    label=label,
                )
            )
        crop_circles.sort(key=lambda circle: circle.score, reverse=True)
        circles.extend(crop_circles)
    return circles


def _skimage_hough_circles(
    mask: np.ndarray,
    rois: tuple[VisionROI, ...],
    scale: int,
    sensitivity: float,
    *,
    prefer_roi_radius: bool = False,
) -> list[VisionCircle]:
    sensitivity = clamp_geometry_sensitivity(sensitivity)
    circles: list[VisionCircle] = []
    for bounds, roi in _rectangular_search_bounds(mask.shape, rois, scale):
        x1, y1, x2, y2 = bounds
        crop = mask[y1:y2, x1:x2]
        if crop.size == 0 or not crop.any():
            continue
        roi_radius = max(5.0, 0.5 * min(roi.width, roi.height) / max(scale, 1))
        if prefer_roi_radius:
            min_radius = max(4, int(0.70 * roi_radius))
            max_radius = max(min_radius + 2, int(1.30 * roi_radius))
            min_distance = max(4, int(0.45 * roi_radius))
            total_peaks = 8
        else:
            min_radius = max(4, int(0.35 * roi_radius))
            max_radius = max(min_radius + 2, int(1.08 * roi_radius))
            min_distance = max(4, int(0.5 * roi_radius))
            total_peaks = 5
        radius_step = max(1, int((max_radius - min_radius) / 24))
        hough_radii = np.arange(min_radius, max_radius + 1, radius_step)
        if hough_radii.size == 0:
            continue
        hough_res = hough_circle(crop.astype(bool), hough_radii)
        accums, centers_x, centers_y, radii = hough_circle_peaks(
            hough_res,
            hough_radii,
            total_num_peaks=total_peaks,
            min_xdistance=min_distance,
            min_ydistance=min_distance,
            threshold=0.08 + 0.18 * (1.0 - sensitivity),
        )
        crop_circles: list[VisionCircle] = []
        for accum, local_x, local_y, radius in zip(accums, centers_x, centers_y, radii):
            circle_x = (x1 + float(local_x)) * scale
            circle_y = (y1 + float(local_y)) * scale
            circle_radius = float(radius) * scale
            if not point_in_roi(circle_x, circle_y, roi):
                continue
            if prefer_roi_radius:
                score = _circle_roi_prior_score(
                    circle_x,
                    circle_y,
                    circle_radius,
                    roi,
                    roi_radius,
                    scale,
                    base_score=min(1.0, float(accum)),
                )
                label = "skimage hough size-prior"
            else:
                score = min(1.0, float(accum))
                label = "skimage hough"
            crop_circles.append(
                VisionCircle(
                    x=float(circle_x),
                    y=float(circle_y),
                    radius=float(circle_radius),
                    score=float(score),
                    label=label,
                )
            )
        crop_circles.sort(key=lambda circle: circle.score, reverse=True)
        circles.extend(crop_circles)
    return circles


def _circle_roi_prior_score(
    circle_x: float,
    circle_y: float,
    circle_radius: float,
    roi: VisionROI,
    roi_radius: float,
    scale: int,
    *,
    base_score: float,
) -> float:
    roi_center_x = 0.5 * (roi.x1 + roi.x2)
    roi_center_y = 0.5 * (roi.y1 + roi.y2)
    target_radius = roi_radius * max(scale, 1)
    radius_error = abs(circle_radius - target_radius) / max(target_radius, 1.0)
    center_error = math.hypot(circle_x - roi_center_x, circle_y - roi_center_y) / max(target_radius, 1.0)
    prior_score = 1.0 - 0.62 * min(radius_error, 1.0) - 0.25 * min(center_error, 1.0)
    return max(0.05, min(0.99, 0.55 * min(max(base_score, 0.0), 1.0) + 0.45 * prior_score))


def _merge_axis_aligned_hough_lines(
    lines: list[VisionLine],
    roi: VisionROI,
    max_gap: float,
) -> list[VisionLine]:
    if len(lines) <= 1:
        return lines

    angle_tolerance = 0.18
    max_gap = max(4.0, float(max_gap))
    coord_tolerance = max(5.0, min(18.0, 0.35 * max_gap))
    horizontal: list[tuple[float, float, float, float, float]] = []
    vertical: list[tuple[float, float, float, float, float]] = []
    other: list[VisionLine] = []

    for line in lines:
        dx = line.x2 - line.x1
        dy = line.y2 - line.y1
        length = math.hypot(dx, dy)
        if length <= 0.0:
            continue
        angle = math.atan2(dy, dx)
        horizontal_delta = min(abs(angle), abs(math.pi - abs(angle)))
        vertical_delta = abs(abs(angle) - math.pi / 2.0)
        if horizontal_delta <= angle_tolerance:
            horizontal.append(
                (
                    0.5 * (line.y1 + line.y2),
                    min(line.x1, line.x2),
                    max(line.x1, line.x2),
                    line.score,
                    length,
                )
            )
        elif vertical_delta <= angle_tolerance:
            vertical.append(
                (
                    0.5 * (line.x1 + line.x2),
                    min(line.y1, line.y2),
                    max(line.y1, line.y2),
                    line.score,
                    length,
                )
            )
        else:
            other.append(line)

    merged = [
        *_merge_axis_line_entries(horizontal, roi, max_gap, coord_tolerance, horizontal=True),
        *_merge_axis_line_entries(vertical, roi, max_gap, coord_tolerance, horizontal=False),
        *other,
    ]
    return merged or lines


def _merge_axis_line_entries(
    entries: list[tuple[float, float, float, float, float]],
    roi: VisionROI,
    max_gap: float,
    coord_tolerance: float,
    *,
    horizontal: bool,
) -> list[VisionLine]:
    if not entries:
        return []

    clusters: list[list[tuple[float, float, float, float, float]]] = []
    for entry in sorted(entries, key=lambda item: item[0]):
        if not clusters:
            clusters.append([entry])
            continue
        current = clusters[-1]
        average_coord = sum(item[0] * item[4] for item in current) / max(
            sum(item[4] for item in current),
            1e-9,
        )
        if abs(entry[0] - average_coord) <= coord_tolerance:
            current.append(entry)
        else:
            clusters.append([entry])

    result: list[VisionLine] = []
    for cluster in clusters:
        intervals = sorted(cluster, key=lambda item: item[1])
        current_start = intervals[0][1]
        current_end = intervals[0][2]
        weighted_coord_sum = intervals[0][0] * intervals[0][4]
        weight_sum = intervals[0][4]
        best_score = intervals[0][3]

        def flush_interval() -> None:
            if current_end <= current_start:
                return
            coord = weighted_coord_sum / max(weight_sum, 1e-9)
            span = current_end - current_start
            target_span = max(roi.width if horizontal else roi.height, 1.0)
            score = min(1.0, max(best_score, span / target_span))
            if horizontal:
                result.append(
                    VisionLine(
                        x1=float(current_start),
                        y1=float(coord),
                        x2=float(current_end),
                        y2=float(coord),
                        score=score,
                        label="opencv hough",
                    )
                )
            else:
                result.append(
                    VisionLine(
                        x1=float(coord),
                        y1=float(current_start),
                        x2=float(coord),
                        y2=float(current_end),
                        score=score,
                        label="opencv hough",
                    )
                )

        for coord, start, end, score, length in intervals[1:]:
            if start - current_end <= max_gap:
                current_end = max(current_end, end)
                weighted_coord_sum += coord * length
                weight_sum += length
                best_score = max(best_score, score)
            else:
                flush_interval()
                current_start = start
                current_end = end
                weighted_coord_sum = coord * length
                weight_sum = length
                best_score = score
        flush_interval()
    return result


def _rectangular_search_bounds(
    image_shape: tuple[int, int],
    rois: tuple[VisionROI, ...],
    scale: int,
) -> list[tuple[tuple[int, int, int, int], VisionROI]]:
    height, width = image_shape
    if not rois:
        if height * width > 1_000_000:
            return []
        rois = (VisionROI("box", 0, 0, width * scale, height * scale),)
    bounds: list[tuple[tuple[int, int, int, int], VisionROI]] = []
    for roi in rois:
        normalized = roi.normalized
        x1 = max(0, int(math.floor(normalized.x1 / max(scale, 1))))
        y1 = max(0, int(math.floor(normalized.y1 / max(scale, 1))))
        x2 = min(width, int(math.ceil(normalized.x2 / max(scale, 1))) + 1)
        y2 = min(height, int(math.ceil(normalized.y2 / max(scale, 1))) + 1)
        if x2 - x1 < 3 or y2 - y1 < 3:
            continue
        bounds.append(((x1, y1, x2, y2), normalized))
    return bounds


def _roi_bright_rectangles(
    gray_image: np.ndarray,
    rois: tuple[VisionROI, ...],
    scale: int,
    sensitivity: float,
) -> list[VisionRectangle]:
    rectangles: list[VisionRectangle] = []
    if not rois:
        return rectangles
    sensitivity = clamp_bright_rectangle_sensitivity(sensitivity)
    for bounds, _roi in _rectangular_search_bounds(gray_image.shape, rois, scale):
        x1, y1, x2, y2 = bounds
        crop = gray_image[y1:y2, x1:x2]
        if crop.size == 0:
            continue
        mask = bright_silhouette_mask(crop, sensitivity=sensitivity)
        crop_height, crop_width = crop.shape
        min_pixels = max(24, int(0.025 * crop_width * crop_height))
        for component_xs, component_ys in _connected_components(mask, min_pixels=min_pixels):
            rectangle = _bright_rectangle_from_component(
                component_xs,
                component_ys,
                crop_width,
                crop_height,
                x1,
                y1,
                scale,
            )
            if rectangle is not None:
                rectangles.append(rectangle)
    return _dedupe_rectangles(rectangles)


def bright_silhouette_mask(gray_image: np.ndarray, *, sensitivity: float | None = None) -> np.ndarray:
    if gray_image.size == 0:
        return np.zeros_like(gray_image, dtype=bool)
    sensitivity_value = (
        DEFAULT_BRIGHT_RECTANGLE_SENSITIVITY
        if sensitivity is None
        else clamp_bright_rectangle_sensitivity(sensitivity)
    )
    gray = np.nan_to_num(gray_image.astype(float, copy=False), nan=0.0, posinf=1.0, neginf=0.0)
    dynamic_range = float(gray.max() - gray.min())
    if dynamic_range <= 1e-6:
        return np.zeros_like(gray, dtype=bool)
    threshold = _otsu_threshold(gray)
    threshold -= (sensitivity_value - DEFAULT_GEOMETRY_SENSITIVITY) * 0.08 * dynamic_range
    threshold = min(float(gray.max()) - 1e-6, max(float(gray.min()) + 1e-6, threshold))
    mask = gray > threshold
    mask = clean_binary_mask(mask)
    mask = fill_binary_holes(mask)
    return mask if mask.any() else gray >= float(gray.max())


def _otsu_threshold(gray_image: np.ndarray) -> float:
    gray_u8 = grayscale_to_uint8(gray_image)
    threshold, _thresholded = cv2.threshold(
        gray_u8,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )
    return float(threshold) / 255.0


def _bright_rectangle_from_component(
    xs: np.ndarray,
    ys: np.ndarray,
    crop_width: int,
    crop_height: int,
    offset_x: int,
    offset_y: int,
    scale: int,
) -> VisionRectangle | None:
    if xs.size == 0:
        return None
    x_min = int(xs.min())
    x_max = int(xs.max())
    y_min = int(ys.min())
    y_max = int(ys.max())
    width = float(x_max - x_min + 1)
    height = float(y_max - y_min + 1)
    if width < max(12.0, 0.20 * crop_width) or height < max(12.0, 0.20 * crop_height):
        return None
    area = float(xs.size)
    bbox_area = max(width * height, 1.0)
    crop_area = max(float(crop_width * crop_height), 1.0)
    fill_ratio = area / bbox_area
    crop_coverage = area / crop_area
    # If the bright component is effectively the whole ROI, the ROI probably does not contain darker background.
    if crop_coverage > 0.88 or (width > 0.96 * crop_width and height > 0.96 * crop_height):
        return None
    if fill_ratio < 0.35:
        return None
    if xs.size >= 5:
        points = np.column_stack((xs.astype(np.float32), ys.astype(np.float32)))
        rect = cv2.minAreaRect(points)
        rect_width, rect_height = (float(value) for value in rect[1])
        min_side = min(rect_width, rect_height)
        max_side = max(rect_width, rect_height)
        if min_side >= max(8.0, 0.14 * min(crop_width, crop_height)) and max_side >= max(
            12.0,
            0.20 * max(crop_width, crop_height),
        ):
            rect_area = max(rect_width * rect_height, 1.0)
            rotated_fill_ratio = min(1.0, area / rect_area)
            if rotated_fill_ratio >= 0.42:
                local_corners = _order_rectangle_corners(cv2.boxPoints(rect))
                global_corners = tuple(
                    (
                        float((offset_x + corner_x) * scale),
                        float((offset_y + corner_y) * scale),
                    )
                    for corner_x, corner_y in local_corners
                )
                return _vision_rectangle_from_corners(
                    global_corners,
                    missing_side=None,
                    score=min(1.0, 0.55 + 0.35 * rotated_fill_ratio + 0.10 * min(1.0, crop_coverage / 0.65)),
                    label="bright silhouette",
                )
    corners = (
        (float((offset_x + x_min) * scale), float((offset_y + y_min) * scale)),
        (float((offset_x + x_max) * scale), float((offset_y + y_min) * scale)),
        (float((offset_x + x_max) * scale), float((offset_y + y_max) * scale)),
        (float((offset_x + x_min) * scale), float((offset_y + y_max) * scale)),
    )
    return _vision_rectangle_from_corners(
        corners,
        missing_side=None,
        score=min(1.0, 0.55 + 0.35 * fill_ratio + 0.10 * min(1.0, crop_coverage / 0.65)),
        label="bright silhouette",
    )


def _roi_rectangles(lines: list[VisionLine], rois: tuple[VisionROI, ...]) -> list[VisionRectangle]:
    rectangles: list[VisionRectangle] = []
    if not rois:
        return rectangles
    for roi in rois:
        normalized = roi.normalized
        roi_lines = [
            line
            for line in lines
            if point_in_roi(0.5 * (line.x1 + line.x2), 0.5 * (line.y1 + line.y2), normalized)
        ]
        rectangle = _rectangle_from_lines(roi_lines, normalized)
        if rectangle is not None:
            rectangles.append(rectangle)
    return _dedupe_rectangles(rectangles)


def _rectangle_from_lines(lines: list[VisionLine], roi: VisionROI) -> VisionRectangle | None:
    candidates = [
        candidate
        for candidate in (
            _axis_aligned_rectangle_from_lines(lines, roi),
            _rotated_rectangle_from_lines(lines, roi),
        )
        if candidate is not None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda rectangle: rectangle.score)


def _axis_aligned_rectangle_from_lines(lines: list[VisionLine], roi: VisionROI) -> VisionRectangle | None:
    horizontal: list[AxisLineCandidate] = []
    vertical: list[AxisLineCandidate] = []
    for line in lines:
        candidate = _axis_line_candidate(line)
        if candidate is None:
            continue
        if candidate.axis == "horizontal":
            horizontal.append(candidate)
        elif candidate.axis == "vertical":
            vertical.append(candidate)
    if len(horizontal) < 2 or not vertical:
        return None

    min_width = max(12.0, 0.15 * roi.width)
    min_height = max(12.0, 0.15 * roi.height)
    tolerance = max(8.0, 0.045 * max(roi.width, roi.height))
    endpoint_tolerance = max(10.0, 0.065 * max(roi.width, roi.height))
    best: VisionRectangle | None = None
    best_score = -1.0

    for first_index, first in enumerate(horizontal):
        for second in horizontal[first_index + 1 :]:
            top, bottom = (first, second) if first.coord <= second.coord else (second, first)
            height = bottom.coord - top.coord
            if height < min_height:
                continue
            if abs(top.start - bottom.start) > endpoint_tolerance:
                continue
            if abs(top.end - bottom.end) > endpoint_tolerance:
                continue
            left_x = 0.5 * (top.start + bottom.start)
            right_x = 0.5 * (top.end + bottom.end)
            width = right_x - left_x
            if width < min_width:
                continue

            left_side = _matching_rectangle_side(vertical, left_x, top.coord, bottom.coord, tolerance)
            right_side = _matching_rectangle_side(vertical, right_x, top.coord, bottom.coord, tolerance)
            if left_side is None and right_side is None:
                continue

            missing_side = None
            if left_side is None:
                missing_side = "left"
                side = right_side
            elif right_side is None:
                missing_side = "right"
                side = left_side
            else:
                side = max((left_side, right_side), key=lambda item: item.score)

            side_coverage = 0.0 if side is None else min(1.0, side.length / max(height, 1.0))
            edge_alignment = 1.0 - min(
                1.0,
                (abs(top.start - bottom.start) + abs(top.end - bottom.end)) / max(2.0 * endpoint_tolerance, 1.0),
            )
            horizontal_coverage = min(1.0, (top.length + bottom.length) / max(2.0 * width, 1.0))
            score = 0.35 * horizontal_coverage + 0.35 * side_coverage + 0.30 * edge_alignment
            if missing_side is not None:
                score *= 0.88
            if score > best_score:
                best_score = score
                best = VisionRectangle(
                    x1=float(left_x),
                    y1=float(top.coord),
                    x2=float(right_x),
                    y2=float(bottom.coord),
                    missing_side=missing_side,
                    score=float(score),
                    label="rectangle",
                    corners=(
                        (float(left_x), float(top.coord)),
                        (float(right_x), float(top.coord)),
                        (float(right_x), float(bottom.coord)),
                        (float(left_x), float(bottom.coord)),
                    ),
                )

    return best


def _rotated_rectangle_from_lines(lines: list[VisionLine], roi: VisionROI) -> VisionRectangle | None:
    candidates = [
        candidate
        for candidate in (_segment_line_candidate(line) for line in lines)
        if candidate is not None and candidate.length >= 10.0
    ]
    if len(candidates) < 3:
        return None

    min_width = max(12.0, 0.15 * max(roi.width, roi.height))
    min_height = max(10.0, 0.10 * min(roi.width, roi.height))
    line_tolerance = max(8.0, 0.055 * max(roi.width, roi.height))
    endpoint_tolerance = max(12.0, 0.080 * max(roi.width, roi.height))
    best: VisionRectangle | None = None
    best_score = -1.0

    for first_index, first in enumerate(candidates):
        for second in candidates[first_index + 1 :]:
            if _angle_delta_mod_pi(first.angle, second.angle) > 0.20:
                continue
            u = np.asarray((first.ux, first.uy), dtype=float)
            n = np.asarray((first.nx, first.ny), dtype=float)
            second_offset = _line_midpoint_offset(second, n)
            height = abs(second_offset - first.offset)
            if height < min_height:
                continue
            first_start, first_end = first.start, first.end
            second_start, second_end = _project_segment_interval(second, u)
            if abs(first_start - second_start) > endpoint_tolerance:
                continue
            if abs(first_end - second_end) > endpoint_tolerance:
                continue
            start = 0.5 * (first_start + second_start)
            end = 0.5 * (first_end + second_end)
            width = end - start
            if width < min_width:
                continue
            d1, d2 = sorted((first.offset, second_offset))
            start_side = _matching_rotated_rectangle_side(candidates, u, n, start, d1, d2, line_tolerance)
            end_side = _matching_rotated_rectangle_side(candidates, u, n, end, d1, d2, line_tolerance)
            if start_side is None and end_side is None:
                continue
            missing_side = None
            if start_side is None:
                missing_side = "left"
                side = end_side
            elif end_side is None:
                missing_side = "right"
                side = start_side
            else:
                side = max((start_side, end_side), key=lambda item: item.score)

            corners = tuple(
                _point_from_axes(u, n, axis_position, offset)
                for axis_position, offset in (
                    (start, d1),
                    (end, d1),
                    (end, d2),
                    (start, d2),
                )
            )
            if not all(point_in_roi(x, y, roi) for x, y in corners):
                continue
            side_coverage = 0.0 if side is None else min(1.0, side.length / max(height, 1.0))
            endpoint_alignment = 1.0 - min(
                1.0,
                (abs(first_start - second_start) + abs(first_end - second_end))
                / max(2.0 * endpoint_tolerance, 1.0),
            )
            parallel_coverage = min(1.0, (first.length + second.length) / max(2.0 * width, 1.0))
            score = 0.35 * parallel_coverage + 0.35 * side_coverage + 0.30 * endpoint_alignment
            if missing_side is not None:
                score *= 0.88
            if score > best_score:
                best_score = score
                best = _vision_rectangle_from_corners(
                    corners,
                    missing_side=missing_side,
                    score=float(score),
                    label="rectangle",
                )
    return best


def _axis_line_candidate(line: VisionLine) -> AxisLineCandidate | None:
    dx = line.x2 - line.x1
    dy = line.y2 - line.y1
    length = math.hypot(dx, dy)
    if length < 8.0:
        return None
    angle = math.atan2(dy, dx)
    horizontal_delta = min(abs(angle), abs(math.pi - abs(angle)))
    vertical_delta = abs(abs(angle) - math.pi / 2.0)
    angle_tolerance = 0.20
    if horizontal_delta <= angle_tolerance:
        return AxisLineCandidate(
            axis="horizontal",
            coord=0.5 * (line.y1 + line.y2),
            start=min(line.x1, line.x2),
            end=max(line.x1, line.x2),
            length=length,
            score=line.score,
        )
    if vertical_delta <= angle_tolerance:
        return AxisLineCandidate(
            axis="vertical",
            coord=0.5 * (line.x1 + line.x2),
            start=min(line.y1, line.y2),
            end=max(line.y1, line.y2),
            length=length,
            score=line.score,
        )
    return None


def _segment_line_candidate(line: VisionLine) -> SegmentLineCandidate | None:
    dx = line.x2 - line.x1
    dy = line.y2 - line.y1
    length = math.hypot(dx, dy)
    if length < 8.0:
        return None
    ux = dx / length
    uy = dy / length
    if ux < -1e-9 or (abs(ux) <= 1e-9 and uy < 0.0):
        ux = -ux
        uy = -uy
    nx = -uy
    ny = ux
    angle = math.atan2(uy, ux)
    if angle < 0.0:
        angle += math.pi
    start, end = sorted((line.x1 * ux + line.y1 * uy, line.x2 * ux + line.y2 * uy))
    offset = 0.5 * (line.x1 * nx + line.y1 * ny + line.x2 * nx + line.y2 * ny)
    return SegmentLineCandidate(
        x1=line.x1,
        y1=line.y1,
        x2=line.x2,
        y2=line.y2,
        ux=float(ux),
        uy=float(uy),
        nx=float(nx),
        ny=float(ny),
        angle=float(angle),
        offset=float(offset),
        start=float(start),
        end=float(end),
        length=float(length),
        score=line.score,
    )


def _angle_delta_mod_pi(first: float, second: float) -> float:
    delta = abs(math.atan2(math.sin(first - second), math.cos(first - second)))
    return min(delta, abs(math.pi - delta))


def _line_midpoint_offset(line: SegmentLineCandidate, normal: np.ndarray) -> float:
    return 0.5 * (
        line.x1 * float(normal[0])
        + line.y1 * float(normal[1])
        + line.x2 * float(normal[0])
        + line.y2 * float(normal[1])
    )


def _project_segment_interval(line: SegmentLineCandidate, axis: np.ndarray) -> tuple[float, float]:
    first = line.x1 * float(axis[0]) + line.y1 * float(axis[1])
    second = line.x2 * float(axis[0]) + line.y2 * float(axis[1])
    return (float(min(first, second)), float(max(first, second)))


def _matching_rotated_rectangle_side(
    lines: list[SegmentLineCandidate],
    axis: np.ndarray,
    normal: np.ndarray,
    target_axis_position: float,
    first_offset: float,
    second_offset: float,
    tolerance: float,
) -> SegmentLineCandidate | None:
    offset_span = second_offset - first_offset
    if offset_span <= 0.0:
        return None
    side_angle = math.atan2(float(normal[1]), float(normal[0]))
    matches: list[tuple[float, SegmentLineCandidate]] = []
    for line in lines:
        if min(_angle_delta_mod_pi(line.angle, side_angle), abs(_angle_delta_mod_pi(line.angle, side_angle) - math.pi)) > 0.22:
            continue
        axis_start, axis_end = _project_segment_interval(line, axis)
        line_axis_position = 0.5 * (axis_start + axis_end)
        if abs(line_axis_position - target_axis_position) > tolerance:
            continue
        offset_start, offset_end = _project_segment_interval(line, normal)
        overlap = max(0.0, min(offset_end, second_offset) - max(offset_start, first_offset))
        coverage = overlap / max(offset_span, 1.0)
        if coverage < 0.55:
            continue
        connect_score = 1.0 - min(1.0, abs(line_axis_position - target_axis_position) / max(tolerance, 1.0))
        matches.append((0.7 * coverage + 0.3 * connect_score, line))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _point_from_axes(axis: np.ndarray, normal: np.ndarray, axis_position: float, offset: float) -> tuple[float, float]:
    point = axis * float(axis_position) + normal * float(offset)
    return float(point[0]), float(point[1])


def _order_rectangle_corners(corners: np.ndarray) -> tuple[tuple[float, float], ...]:
    points = np.asarray(corners, dtype=float).reshape(-1, 2)
    if points.shape[0] != 4:
        return tuple((float(x), float(y)) for x, y in points)
    center = points.mean(axis=0)
    order = np.argsort(np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0]))
    ordered = points[order]
    start_index = int(np.argmin(ordered[:, 0] + ordered[:, 1]))
    ordered = np.roll(ordered, -start_index, axis=0)
    return tuple((float(x), float(y)) for x, y in ordered)


def _vision_rectangle_from_corners(
    corners: tuple[tuple[float, float], ...],
    *,
    missing_side: str | None,
    score: float,
    label: str,
) -> VisionRectangle:
    xs = [corner[0] for corner in corners]
    ys = [corner[1] for corner in corners]
    return VisionRectangle(
        x1=float(min(xs)),
        y1=float(min(ys)),
        x2=float(max(xs)),
        y2=float(max(ys)),
        missing_side=missing_side,
        score=float(score),
        label=label,
        corners=tuple((float(x), float(y)) for x, y in corners),
    )


def _matching_rectangle_side(
    vertical_lines: list[AxisLineCandidate],
    x: float,
    top_y: float,
    bottom_y: float,
    tolerance: float,
) -> AxisLineCandidate | None:
    height = bottom_y - top_y
    if height <= 0.0:
        return None
    matches: list[tuple[float, AxisLineCandidate]] = []
    for line in vertical_lines:
        if abs(line.coord - x) > tolerance:
            continue
        if line.start > top_y + tolerance or line.end < bottom_y - tolerance:
            continue
        overlap = max(0.0, min(line.end, bottom_y) - max(line.start, top_y))
        coverage = overlap / max(height, 1.0)
        if coverage < 0.65:
            continue
        connect_score = 1.0 - min(1.0, abs(line.coord - x) / max(tolerance, 1.0))
        matches.append((0.7 * coverage + 0.3 * connect_score, line))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _dedupe_rectangles(rectangles: list[VisionRectangle]) -> list[VisionRectangle]:
    result: list[VisionRectangle] = []
    for rectangle in sorted(
        rectangles,
        key=lambda item: (item.label == "bright silhouette", item.score),
        reverse=True,
    ):
        center_x = 0.5 * (rectangle.x1 + rectangle.x2)
        center_y = 0.5 * (rectangle.y1 + rectangle.y2)
        duplicate = False
        for existing in result:
            existing_center_x = 0.5 * (existing.x1 + existing.x2)
            existing_center_y = 0.5 * (existing.y1 + existing.y2)
            center_delta = math.hypot(center_x - existing_center_x, center_y - existing_center_y)
            size_delta = abs((rectangle.x2 - rectangle.x1) - (existing.x2 - existing.x1)) + abs(
                (rectangle.y2 - rectangle.y1) - (existing.y2 - existing.y1)
            )
            if center_delta < 10.0 and size_delta < 18.0:
                duplicate = True
                break
        if not duplicate:
            result.append(rectangle)
    return result[:20]


def _roi_semicircles(mask: np.ndarray, rois: tuple[VisionROI, ...], scale: int) -> list[VisionSemicircle]:
    semicircles: list[VisionSemicircle] = []
    if not rois:
        return semicircles
    height, width = mask.shape
    yy, xx = np.ogrid[:height, :width]
    full_x = xx * scale
    full_y = yy * scale
    for roi in rois:
        normalized = roi.normalized
        roi_radius = 0.5 * max(normalized.width, normalized.height)
        if roi_radius < 5.0:
            continue
        search_mask = mask & circle_roi_mask(full_x, full_y, normalized)
        component_mask = dilate_binary_mask(search_mask, iterations=2)
        for xs, ys in _connected_components(component_mask, min_pixels=18):
            x1 = max(0, int(xs.min()) - 2)
            x2 = min(width - 1, int(xs.max()) + 2)
            y1 = max(0, int(ys.min()) - 2)
            y2 = min(height - 1, int(ys.max()) + 2)
            local_points = np.argwhere(search_mask[y1 : y2 + 1, x1 : x2 + 1])
            if local_points.shape[0] < 8:
                continue
            component_ys = local_points[:, 0] + y1
            component_xs = local_points[:, 1] + x1
            semicircle = _component_semicircle(component_xs, component_ys, scale)
            if semicircle is None:
                continue
            if not point_in_roi(semicircle.x, semicircle.y, normalized):
                continue
            semicircles.append(semicircle)
    return _dedupe_semicircles(semicircles)[:20]


def _component_silhouettes(mask: np.ndarray, scale: int) -> list[VisionSilhouette]:
    silhouettes: list[VisionSilhouette] = []
    for xs, ys in _connected_components(mask, min_pixels=80):
        width = float(xs.max() - xs.min() + 1)
        height = float(ys.max() - ys.min() + 1)
        if width < 12.0 or height < 12.0:
            continue
        area_pixels = float(xs.size)
        fill_ratio = area_pixels / max(width * height, 1.0)
        if fill_ratio < 0.08:
            continue
        boundary_xs, boundary_ys, contour_segments = _component_contour(xs, ys, scale)
        circle = _fit_silhouette_circle(boundary_xs, boundary_ys, xs, ys, scale)
        circle_x, circle_y, circle_radius = circle if circle is not None else (None, None, None)
        circle_contour_segments = (
            _silhouette_circle_contour(xs, ys, circle_x, circle_y, circle_radius, scale)
            if circle is not None
            else ()
        )
        silhouettes.append(
            VisionSilhouette(
                x=float(xs.mean() * scale),
                y=float(ys.mean() * scale),
                x1=float(xs.min() * scale),
                y1=float(ys.min() * scale),
                x2=float(xs.max() * scale),
                y2=float(ys.max() * scale),
                area=float(area_pixels * scale * scale),
                score=min(1.0, fill_ratio * 1.8),
                label="dark silhouette",
                contour_segments=contour_segments,
                circle_contour_segments=circle_contour_segments,
                circle_x=circle_x,
                circle_y=circle_y,
                circle_radius=circle_radius,
            )
        )
    silhouettes.sort(key=lambda silhouette: silhouette.area, reverse=True)
    return silhouettes[:20]


def _component_contour(
    xs: np.ndarray,
    ys: np.ndarray,
    scale: int,
) -> tuple[np.ndarray, np.ndarray, tuple[tuple[float, float, float, float], ...]]:
    component_pixels = set(zip(xs.tolist(), ys.tolist()))
    boundary_xs: list[int] = []
    boundary_ys: list[int] = []
    segments: list[tuple[float, float, float, float]] = []
    for raw_x, raw_y in zip(xs, ys):
        x = int(raw_x)
        y = int(raw_y)
        is_boundary = False
        left = float(x * scale)
        top = float(y * scale)
        right = float((x + 1) * scale)
        bottom = float((y + 1) * scale)
        if (x, y - 1) not in component_pixels:
            segments.append((left, top, right, top))
            is_boundary = True
        if (x + 1, y) not in component_pixels:
            segments.append((right, top, right, bottom))
            is_boundary = True
        if (x, y + 1) not in component_pixels:
            segments.append((left, bottom, right, bottom))
            is_boundary = True
        if (x - 1, y) not in component_pixels:
            segments.append((left, top, left, bottom))
            is_boundary = True
        if is_boundary:
            boundary_xs.append(x)
            boundary_ys.append(y)

    if len(segments) > MAX_SILHOUETTE_CONTOUR_SEGMENTS:
        step = len(segments) / MAX_SILHOUETTE_CONTOUR_SEGMENTS
        segments = [segments[int(index * step)] for index in range(MAX_SILHOUETTE_CONTOUR_SEGMENTS)]

    return (
        np.asarray(boundary_xs, dtype=float),
        np.asarray(boundary_ys, dtype=float),
        tuple(segments),
    )


def _fit_silhouette_circle(
    boundary_xs: np.ndarray,
    boundary_ys: np.ndarray,
    component_xs: np.ndarray,
    component_ys: np.ndarray,
    scale: int,
) -> tuple[float, float, float] | None:
    if boundary_xs.size < 24:
        return None

    min_x = float(component_xs.min())
    max_x = float(component_xs.max())
    min_y = float(component_ys.min())
    max_y = float(component_ys.max())
    width = max(max_x - min_x + 1.0, 1.0)
    height = max(max_y - min_y + 1.0, 1.0)
    min_radius = max(5.0, min(width, height) * 0.18)
    max_radius = max(width, height) * 1.25

    candidates: list[tuple[float, float, float, float]] = []
    candidate_masks = [np.ones(boundary_xs.shape, dtype=bool)]
    y_span = max_y - min_y
    for fraction in (0.35, 0.40, 0.45, 0.50, 0.55, 0.60):
        candidate_masks.append(boundary_ys >= min_y + fraction * y_span)

    for candidate_mask in candidate_masks:
        if int(candidate_mask.sum()) < 24:
            continue
        fitted = _fit_circle_to_points(boundary_xs[candidate_mask], boundary_ys[candidate_mask])
        if fitted is None:
            continue
        center_x, center_y, radius, median_error = fitted
        if radius < min_radius or radius > max_radius:
            continue
        margin = max(radius * 0.35, 4.0)
        if not (min_x - margin <= center_x <= max_x + margin and min_y - margin <= center_y <= max_y + margin):
            continue
        radial_score = max(0.0, 1.0 - median_error / max(0.20 * radius, 1.0))
        coverage = float(candidate_mask.sum()) / max(2.0 * math.pi * radius, 1.0)
        coverage_score = min(1.0, coverage / 0.45)
        score = 0.76 * radial_score + 0.24 * coverage_score
        candidates.append((score, center_x, center_y, radius))

    if not candidates:
        return None
    _, center_x, center_y, radius = max(candidates, key=lambda candidate: candidate[0])
    return float(center_x * scale), float(center_y * scale), float(radius * scale)


def _silhouette_circle_contour(
    component_xs: np.ndarray,
    component_ys: np.ndarray,
    circle_x: float,
    circle_y: float,
    circle_radius: float,
    scale: int,
) -> tuple[tuple[float, float, float, float], ...]:
    center_x = circle_x / scale
    center_y = circle_y / scale
    radius = circle_radius / scale
    tolerance = max(1.5, 0.025 * radius)
    selected = (component_xs.astype(float) - center_x) ** 2 + (
        component_ys.astype(float) - center_y
    ) ** 2 <= (radius + tolerance) ** 2
    if int(selected.sum()) < 24:
        return ()

    selected_xs = component_xs[selected]
    selected_ys = component_ys[selected]
    x1 = int(max(0, math.floor(center_x - radius - tolerance - 2)))
    x2 = int(math.ceil(center_x + radius + tolerance + 2))
    y1 = int(max(0, math.floor(center_y - radius - tolerance - 2)))
    y2 = int(math.ceil(center_y + radius + tolerance + 2))
    local = np.zeros((y2 - y1 + 1, x2 - x1 + 1), dtype=bool)
    local[selected_ys - y1, selected_xs - x1] = True

    yy, xx = np.ogrid[y1 : y2 + 1, x1 : x2 + 1]
    disk = (xx.astype(float) - center_x) ** 2 + (yy.astype(float) - center_y) ** 2 <= (
        radius + tolerance
    ) ** 2
    local = fill_binary_holes(local) & disk
    if int(local.sum()) < 24:
        return ()

    ys, xs = np.nonzero(local)
    _, _, contour_segments = _component_contour(xs + x1, ys + y1, scale)
    return contour_segments


def _fit_circle_to_points(
    xs: np.ndarray,
    ys: np.ndarray,
) -> tuple[float, float, float, float] | None:
    if xs.size < 3:
        return None
    matrix = np.column_stack((2.0 * xs, 2.0 * ys, np.ones_like(xs)))
    if np.linalg.matrix_rank(matrix) < 3:
        return None
    target = xs * xs + ys * ys
    try:
        center_x, center_y, offset = np.linalg.lstsq(matrix, target, rcond=None)[0]
    except np.linalg.LinAlgError:
        return None
    radius_squared = float(center_x * center_x + center_y * center_y + offset)
    if radius_squared <= 0.0 or not math.isfinite(radius_squared):
        return None
    radius = math.sqrt(radius_squared)
    distances = np.hypot(xs - center_x, ys - center_y)
    median_error = float(np.median(np.abs(distances - radius)))
    return float(center_x), float(center_y), float(radius), median_error


def _component_semicircle(xs: np.ndarray, ys: np.ndarray, scale: int) -> VisionSemicircle | None:
    width = float(xs.max() - xs.min() + 1)
    height = float(ys.max() - ys.min() + 1)
    if max(width, height) < 10.0:
        return None

    x_min = float(xs.min())
    x_max = float(xs.max())
    y_min = float(ys.min())
    y_max = float(ys.max())
    mid_x = 0.5 * (x_min + x_max)
    mid_y = 0.5 * (y_min + y_max)
    candidates = (
        _semicircle_candidate(xs, ys, x_min, mid_y, 0.5 * height, "right"),
        _semicircle_candidate(xs, ys, x_max, mid_y, 0.5 * height, "left"),
        _semicircle_candidate(xs, ys, mid_x, y_max, 0.5 * width, "up"),
        _semicircle_candidate(xs, ys, mid_x, y_min, 0.5 * width, "down"),
    )
    best = max(candidates, key=lambda candidate: candidate["score"])
    if float(best["score"]) < 0.52:
        return None
    return VisionSemicircle(
        x=float(best["center_x"]) * scale,
        y=float(best["center_y"]) * scale,
        radius=float(best["radius"]) * scale,
        orientation=str(best["orientation"]),
        score=float(best["score"]),
        label="component arc",
    )


def _semicircle_candidate(
    xs: np.ndarray,
    ys: np.ndarray,
    center_x: float,
    center_y: float,
    radius: float,
    orientation: str,
) -> dict[str, float | str]:
    if radius < 5.0:
        return {
            "score": 0.0,
            "center_x": center_x,
            "center_y": center_y,
            "radius": radius,
            "orientation": orientation,
        }

    distances = np.hypot(xs.astype(float) - center_x, ys.astype(float) - center_y)
    radial_error = np.abs(distances - radius)
    tolerance = max(2.0, 0.18 * radius)
    radial_fraction = float((radial_error <= tolerance).sum() / max(xs.size, 1))
    radial_score = max(0.0, 1.0 - float(np.median(radial_error)) / tolerance)

    width = float(xs.max() - xs.min() + 1)
    height = float(ys.max() - ys.min() + 1)
    if orientation in {"right", "left"}:
        span_ratio = height / max(2.0 * radius, 1.0)
        depth_ratio = width / max(radius, 1.0)
    else:
        span_ratio = width / max(2.0 * radius, 1.0)
        depth_ratio = height / max(radius, 1.0)

    if span_ratio < 0.58 or depth_ratio < 0.42 or depth_ratio > 1.38:
        score = 0.0
    else:
        span_score = min(span_ratio, 1.0)
        depth_score = max(0.0, 1.0 - abs(depth_ratio - 1.0) / 0.62)
        score = (
            0.42 * radial_fraction
            + 0.28 * radial_score
            + 0.20 * span_score
            + 0.10 * depth_score
        )
    return {
        "score": min(1.0, score),
        "center_x": center_x,
        "center_y": center_y,
        "radius": radius,
        "orientation": orientation,
    }


def _dedupe_semicircles(semicircles: list[VisionSemicircle]) -> list[VisionSemicircle]:
    result: list[VisionSemicircle] = []
    for semicircle in sorted(semicircles, key=lambda item: item.score, reverse=True):
        if any(
            math.hypot(semicircle.x - existing.x, semicircle.y - existing.y)
            < max(8.0, 0.25 * max(semicircle.radius, existing.radius))
            and abs(semicircle.radius - existing.radius) < max(6.0, 0.25 * existing.radius)
            for existing in result
        ):
            continue
        result.append(semicircle)
    return result


def _dedupe_circles(circles: list[VisionCircle]) -> list[VisionCircle]:
    result: list[VisionCircle] = []
    for circle in sorted(
        circles,
        key=lambda item: (0 if item.label == "component" else 1, item.score),
        reverse=True,
    ):
        duplicate = False
        for existing in result:
            center_delta = math.hypot(circle.x - existing.x, circle.y - existing.y)
            radius_delta = abs(circle.radius - existing.radius)
            if center_delta < max(6.0, 0.18 * max(circle.radius, existing.radius)) and radius_delta < max(
                5.0,
                0.20 * existing.radius,
            ):
                duplicate = True
                break
        if duplicate:
            continue
        result.append(circle)
        if len(result) >= 20:
            break
    return result


def _connected_components(mask: np.ndarray, min_pixels: int) -> list[tuple[np.ndarray, np.ndarray]]:
    height, width = mask.shape
    visited = np.zeros(mask.shape, dtype=bool)
    components: list[tuple[np.ndarray, np.ndarray]] = []
    true_pixels = np.argwhere(mask)
    for y0, x0 in true_pixels:
        if visited[y0, x0]:
            continue
        stack = [(int(y0), int(x0))]
        visited[y0, x0] = True
        xs: list[int] = []
        ys: list[int] = []
        while stack:
            y, x = stack.pop()
            xs.append(x)
            ys.append(y)
            for neighbor_y in range(max(0, y - 1), min(height, y + 2)):
                for neighbor_x in range(max(0, x - 1), min(width, x + 2)):
                    if visited[neighbor_y, neighbor_x] or not mask[neighbor_y, neighbor_x]:
                        continue
                    visited[neighbor_y, neighbor_x] = True
                    stack.append((neighbor_y, neighbor_x))
        if len(xs) >= min_pixels:
            components.append((np.asarray(xs), np.asarray(ys)))
    return components


def _line_intersections(
    lines: tuple[VisionLine, ...] | list[VisionLine],
    image_width: float,
    image_height: float,
) -> list[VisionIntersection]:
    intersections: list[VisionIntersection] = []
    for index, first in enumerate(lines):
        for second in lines[index + 1 :]:
            point = _segment_intersection(first, second)
            if point is None:
                continue
            x, y = point
            if 0.0 <= x <= image_width and 0.0 <= y <= image_height:
                intersections.append(
                    VisionIntersection(
                        x=x,
                        y=y,
                        score=min(first.score, second.score),
                        label=f"{first.label} x {second.label}",
                    )
                )
    return _dedupe_intersections(intersections)[:30]


def _segment_intersection(first: VisionLine, second: VisionLine) -> tuple[float, float] | None:
    x1, y1, x2, y2 = first.x1, first.y1, first.x2, first.y2
    x3, y3, x4, y4 = second.x1, second.y1, second.x2, second.y2
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) < 1e-9:
        return None
    px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / denominator
    py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / denominator
    if not (_within_segment(px, py, first) and _within_segment(px, py, second)):
        return None
    return px, py


def _within_segment(x: float, y: float, line: VisionLine, tolerance: float = 3.0) -> bool:
    return (
        min(line.x1, line.x2) - tolerance <= x <= max(line.x1, line.x2) + tolerance
        and min(line.y1, line.y2) - tolerance <= y <= max(line.y1, line.y2) + tolerance
    )


def _consecutive_groups(values: np.ndarray) -> list[list[int]]:
    if values.size == 0:
        return []
    groups: list[list[int]] = [[int(values[0])]]
    for value in values[1:]:
        current = int(value)
        if current == groups[-1][-1] + 1:
            groups[-1].append(current)
        else:
            groups.append([current])
    return groups


def _dedupe_lines(lines: list[VisionLine]) -> list[VisionLine]:
    result: list[VisionLine] = []
    for line in sorted(lines, key=lambda item: item.score, reverse=True):
        midpoint = (0.5 * (line.x1 + line.x2), 0.5 * (line.y1 + line.y2))
        angle = math.atan2(line.y2 - line.y1, line.x2 - line.x1)
        duplicate = False
        for existing in result:
            existing_midpoint = (
                0.5 * (existing.x1 + existing.x2),
                0.5 * (existing.y1 + existing.y2),
            )
            existing_angle = math.atan2(existing.y2 - existing.y1, existing.x2 - existing.x1)
            angle_delta = abs(math.atan2(math.sin(angle - existing_angle), math.cos(angle - existing_angle)))
            midpoint_delta = math.hypot(midpoint[0] - existing_midpoint[0], midpoint[1] - existing_midpoint[1])
            if midpoint_delta < 8.0 and min(angle_delta, abs(math.pi - angle_delta)) < 0.12:
                duplicate = True
                break
        if not duplicate:
            result.append(line)
        if len(result) >= 30:
            break
    return result


def _dedupe_intersections(intersections: list[VisionIntersection]) -> list[VisionIntersection]:
    result: list[VisionIntersection] = []
    for intersection in sorted(intersections, key=lambda item: item.score, reverse=True):
        if any(math.hypot(intersection.x - existing.x, intersection.y - existing.y) < 8.0 for existing in result):
            continue
        result.append(intersection)
    return result


def semicircle_orientation(start_x: float, start_y: float, end_x: float, end_y: float) -> str:
    dx = end_x - start_x
    dy = end_y - start_y
    if abs(dx) >= abs(dy):
        return "right" if dx >= 0.0 else "left"
    return "down" if dy >= 0.0 else "up"


def semicircle_arc_start(orientation: str) -> int:
    return {
        "right": -90,
        "left": 90,
        "up": 0,
        "down": 180,
    }.get(orientation, -90)


class VisionRecognitionLab(tk.Toplevel):
    """UI shell for choosing standard-position images and future vision shapes."""

    def __init__(
        self,
        master: tk.Misc | None = None,
        *,
        image_root: str | Path = DEFAULT_STANDARD_POSITION_IMAGE_ROOT,
        captured_image_path: str | Path | None = None,
        session_done_callback: Callable[[], None] | None = None,
        show_session_done_button: bool = False,
        initial_session: dict[str, Any] | None = None,
        capture_id: str | None = None,
    ) -> None:
        super().__init__(master)
        self.image_root = Path(image_root)
        self._captured_image_path = Path(captured_image_path) if captured_image_path is not None else None
        self._session_done_callback = session_done_callback
        self._show_session_done_button = show_session_done_button
        self._initial_session = deepcopy(initial_session) if isinstance(initial_session, dict) else None
        self._capture_id = str(capture_id or "").strip()
        self._feature_role_options = feature_role_options_for_capture(self._capture_id)
        self._feature_role_keys = tuple(role for role, _label in self._feature_role_options)
        self._feature_role_display_values = tuple(label for _role, label in self._feature_role_options)
        self._session_saved = False
        self._session_cancelled = False
        self.library = VisionPositionLibrary(positions=(), images=())
        self._position_display_to_id: dict[str, str] = {}
        self._image_item_to_image: dict[str, VisionPositionImage] = {}
        self._current_images: tuple[VisionPositionImage, ...] = ()
        self._selected_image: VisionPositionImage | None = None
        self._last_v5_sequence_memory_path: Path | None = None
        self._source_gray_image: np.ndarray | None = None
        self._source_photo_image: tk.PhotoImage | None = None
        self._photo_image: tk.PhotoImage | None = None
        self._image_canvas_item: int | None = None
        self._display_origin = (0, 0)
        self._display_subsample = 1.0
        self._view_scale: float | None = None
        self._rois: list[VisionROI] = []
        self._roi_drag: dict[str, float | str] | None = None
        self._roi_preview_item: int | None = None
        self._pending_recognition_after_id: str | None = None
        self._geometry_recognizer_display_to_name = {
            recognizer.display_name: recognizer.name
            for recognizer in RECOGNIZERS
            if recognizer.name in GEOMETRY_RECOGNIZER_NAMES
        }
        self._recognizer_display_to_name = self._geometry_recognizer_display_to_name
        self._silhouette_recognizer_display_to_name = {
            SILHOUETTE_RECOGNIZER_OFF_LABEL: "",
            RECOGNIZER_BY_NAME[DEFAULT_SILHOUETTE_RECOGNIZER_NAME].display_name: DEFAULT_SILHOUETTE_RECOGNIZER_NAME,
        }
        self._recognition_result: VisionRecognitionResult | None = None
        self._recognition_tree_items: dict[str, VisionRecognitionTreeItem] = {}
        self._recognition_item_role_overrides: dict[str, str] = {}
        self._selected_recognition_item_ids: set[str] = set()
        self._selected_recognition_item_order: list[str] = []
        self._active_recognition_item_ids: set[str] = set()
        self._selected_measurement: VisionRectangleCircleMeasurement | None = None
        self._selected_measurements: tuple[VisionSelectedMeasurement, ...] = ()
        self._recognition_legend_visible = False

        review_context = CAPTURE_REVIEW_CONTEXT_LABELS.get(self._capture_id)
        self.title(
            f"{VISION_RECOGNITION_LAB_TITLE} - {self._capture_id} - {review_context}"
            if review_context
            else VISION_RECOGNITION_LAB_TITLE
        )
        self.minsize(980, 620)
        self.geometry("1180x760")
        self._build_ui()
        if self._captured_image_path is not None:
            self.load_captured_image(self._captured_image_path)
            if self._initial_session:
                self.preload_review_session(self._initial_session)
        else:
            self.reload_library()

    def _build_ui(self) -> None:
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Position").grid(row=0, column=0, padx=(0, 6), sticky="w")
        self.position_var = tk.StringVar(value="")
        self.position_combobox = ttk.Combobox(
            top,
            textvariable=self.position_var,
            state="readonly",
            width=48,
        )
        self.position_combobox.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self.position_combobox.bind("<<ComboboxSelected>>", self._on_position_selected)
        self.refresh_button = ttk.Button(top, text="Refresh", command=self.reload_library)
        self.refresh_button.grid(row=0, column=2, sticky="e")

        self.position_summary_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.position_summary_var, anchor="w").grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=(4, 0),
        )

        main = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        sidebar = ttk.Frame(main, padding=(0, 0, 8, 0))
        sidebar.columnconfigure(0, weight=1)
        main.add(sidebar, weight=1)

        ttk.Label(sidebar, text="Images").grid(row=0, column=0, sticky="w")
        tree_frame = ttk.Frame(sidebar)
        tree_frame.grid(row=1, column=0, sticky="nsew", pady=(2, 8))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)
        sidebar.rowconfigure(1, weight=1)

        self.image_tree = ttk.Treeview(
            tree_frame,
            columns=("batch", "file"),
            show="headings",
            selectmode="browse",
            height=12,
        )
        self.image_tree.heading("batch", text="Batch")
        self.image_tree.heading("file", text="File")
        self.image_tree.column("batch", width=64, stretch=False)
        self.image_tree.column("file", width=190, stretch=True)
        self.image_tree.grid(row=0, column=0, sticky="nsew")
        image_scroll = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.image_tree.yview)
        image_scroll.grid(row=0, column=1, sticky="ns")
        self.image_tree.configure(yscrollcommand=image_scroll.set)
        self.image_tree.bind("<<TreeviewSelect>>", self._on_image_selected)

        algorithm = ttk.LabelFrame(sidebar, text="Recognizers", padding=(6, 4))
        algorithm.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        algorithm.columnconfigure(1, weight=1)
        default_recognizer = RECOGNIZER_BY_NAME[DEFAULT_GEOMETRY_RECOGNIZER_NAME].display_name
        self.recognizer_var = tk.StringVar(value=default_recognizer)
        self.geometry_recognizer_label = ttk.Label(algorithm, text="Geometry ROIs")
        self.geometry_recognizer_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.recognizer_combobox = ttk.Combobox(
            algorithm,
            textvariable=self.recognizer_var,
            values=tuple(self._geometry_recognizer_display_to_name),
            state="readonly",
            width=28,
        )
        self.recognizer_combobox.grid(row=0, column=1, sticky="ew", pady=(0, 4))
        self.recognizer_combobox.bind("<<ComboboxSelected>>", self._on_recognizer_selected)
        self.geometry_sensitivity_var = tk.DoubleVar(value=DEFAULT_GEOMETRY_SENSITIVITY)
        self.geometry_sensitivity_text_var = tk.StringVar(value=f"{DEFAULT_GEOMETRY_SENSITIVITY:.2f}")
        self.geometry_sensitivity_label = ttk.Label(algorithm, text="Hough sens.")
        self.geometry_sensitivity_label.grid(row=1, column=0, sticky="w", padx=(0, 6))
        self.geometry_sensitivity_frame = ttk.Frame(algorithm)
        self.geometry_sensitivity_frame.grid(row=1, column=1, sticky="ew", pady=(0, 4))
        self.geometry_sensitivity_frame.columnconfigure(0, weight=1)
        self.geometry_sensitivity_scale = ttk.Scale(
            self.geometry_sensitivity_frame,
            from_=MIN_GEOMETRY_SENSITIVITY,
            to=MAX_GEOMETRY_SENSITIVITY,
            orient=tk.HORIZONTAL,
            variable=self.geometry_sensitivity_var,
            command=self._on_geometry_sensitivity_changed,
        )
        self.geometry_sensitivity_scale.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.geometry_sensitivity_scale.bind("<Button-1>", self._on_geometry_sensitivity_scale_mouse)
        self.geometry_sensitivity_scale.bind("<B1-Motion>", self._on_geometry_sensitivity_scale_mouse)
        self.geometry_sensitivity_entry = ttk.Entry(
            self.geometry_sensitivity_frame,
            textvariable=self.geometry_sensitivity_text_var,
            width=6,
            justify="right",
        )
        self.geometry_sensitivity_entry.grid(row=0, column=1, sticky="e")
        self.geometry_sensitivity_entry.bind("<Return>", self._on_geometry_sensitivity_entry_commit)
        self.geometry_sensitivity_entry.bind("<KP_Enter>", self._on_geometry_sensitivity_entry_commit)
        self.geometry_sensitivity_entry.bind("<FocusOut>", self._on_geometry_sensitivity_entry_commit)
        self.bright_rectangle_sensitivity_var = tk.DoubleVar(value=DEFAULT_BRIGHT_RECTANGLE_SENSITIVITY)
        self.bright_rectangle_sensitivity_text_var = tk.StringVar(value=f"{DEFAULT_BRIGHT_RECTANGLE_SENSITIVITY:.2f}")
        self.bright_rectangle_sensitivity_label = ttk.Label(algorithm, text="Rect bright sens.")
        self.bright_rectangle_sensitivity_label.grid(row=2, column=0, sticky="w", padx=(0, 6))
        self.bright_rectangle_sensitivity_frame = ttk.Frame(algorithm)
        self.bright_rectangle_sensitivity_frame.grid(row=2, column=1, sticky="ew", pady=(0, 4))
        self.bright_rectangle_sensitivity_frame.columnconfigure(0, weight=1)
        self.bright_rectangle_sensitivity_scale = ttk.Scale(
            self.bright_rectangle_sensitivity_frame,
            from_=MIN_BRIGHT_RECTANGLE_SENSITIVITY,
            to=MAX_BRIGHT_RECTANGLE_SENSITIVITY,
            orient=tk.HORIZONTAL,
            variable=self.bright_rectangle_sensitivity_var,
            command=self._on_bright_rectangle_sensitivity_changed,
        )
        self.bright_rectangle_sensitivity_scale.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.bright_rectangle_sensitivity_scale.bind("<Button-1>", self._on_bright_rectangle_sensitivity_scale_mouse)
        self.bright_rectangle_sensitivity_scale.bind("<B1-Motion>", self._on_bright_rectangle_sensitivity_scale_mouse)
        self.bright_rectangle_sensitivity_entry = ttk.Entry(
            self.bright_rectangle_sensitivity_frame,
            textvariable=self.bright_rectangle_sensitivity_text_var,
            width=6,
            justify="right",
        )
        self.bright_rectangle_sensitivity_entry.grid(row=0, column=1, sticky="e")
        self.bright_rectangle_sensitivity_entry.bind("<Return>", self._on_bright_rectangle_sensitivity_entry_commit)
        self.bright_rectangle_sensitivity_entry.bind("<KP_Enter>", self._on_bright_rectangle_sensitivity_entry_commit)
        self.bright_rectangle_sensitivity_entry.bind("<FocusOut>", self._on_bright_rectangle_sensitivity_entry_commit)
        default_silhouette_recognizer = RECOGNIZER_BY_NAME[DEFAULT_SILHOUETTE_RECOGNIZER_NAME].display_name
        self.silhouette_recognizer_var = tk.StringVar(value=default_silhouette_recognizer)
        self.silhouette_recognizer_label = ttk.Label(algorithm, text="Silhouette ROI")
        self.silhouette_recognizer_label.grid(row=3, column=0, sticky="w", padx=(0, 6))
        self.silhouette_recognizer_combobox = ttk.Combobox(
            algorithm,
            textvariable=self.silhouette_recognizer_var,
            values=tuple(self._silhouette_recognizer_display_to_name),
            state="readonly",
            width=28,
        )
        self.silhouette_recognizer_combobox.grid(row=3, column=1, sticky="ew", pady=(0, 4))
        self.silhouette_recognizer_combobox.bind("<<ComboboxSelected>>", self._on_recognizer_selected)
        self.silhouette_sensitivity_var = tk.DoubleVar(value=DEFAULT_SILHOUETTE_SENSITIVITY)
        self.silhouette_sensitivity_text_var = tk.StringVar(value=f"{DEFAULT_SILHOUETTE_SENSITIVITY:.2f}")
        self.silhouette_sensitivity_label = ttk.Label(algorithm, text="Dark sil. sens.")
        self.silhouette_sensitivity_label.grid(row=4, column=0, sticky="w", padx=(0, 6))
        self.silhouette_sensitivity_frame = ttk.Frame(algorithm)
        self.silhouette_sensitivity_frame.grid(row=4, column=1, sticky="ew", pady=(0, 4))
        self.silhouette_sensitivity_frame.columnconfigure(0, weight=1)
        self.silhouette_sensitivity_scale = ttk.Scale(
            self.silhouette_sensitivity_frame,
            from_=MIN_SILHOUETTE_SENSITIVITY,
            to=MAX_SILHOUETTE_SENSITIVITY,
            orient=tk.HORIZONTAL,
            variable=self.silhouette_sensitivity_var,
            command=self._on_silhouette_sensitivity_changed,
        )
        self.silhouette_sensitivity_scale.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        self.silhouette_sensitivity_scale.bind("<Button-1>", self._on_silhouette_sensitivity_scale_mouse)
        self.silhouette_sensitivity_scale.bind("<B1-Motion>", self._on_silhouette_sensitivity_scale_mouse)
        self.silhouette_sensitivity_entry = ttk.Entry(
            self.silhouette_sensitivity_frame,
            textvariable=self.silhouette_sensitivity_text_var,
            width=6,
            justify="right",
        )
        self.silhouette_sensitivity_entry.grid(
            row=0,
            column=1,
            sticky="e",
        )
        self.silhouette_sensitivity_entry.bind("<Return>", self._on_silhouette_sensitivity_entry_commit)
        self.silhouette_sensitivity_entry.bind("<KP_Enter>", self._on_silhouette_sensitivity_entry_commit)
        self.silhouette_sensitivity_entry.bind("<FocusOut>", self._on_silhouette_sensitivity_entry_commit)
        ttk.Button(algorithm, text="Run", command=self.run_recognition).grid(row=5, column=0, sticky="ew", padx=(0, 4))
        ttk.Button(algorithm, text="Clear", command=self.clear_recognition).grid(row=5, column=1, sticky="ew")

        tools = ttk.LabelFrame(sidebar, text="Shape design", padding=(6, 4))
        tools.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        tools.columnconfigure(0, weight=1)
        self.shape_tool_var = tk.StringVar(value="silhouette")
        for row, (label, value) in enumerate(
            (
                ("Silhouette ROI", "silhouette"),
                ("Edges ROI", "edges"),
                ("Rectangle ROI", "rectangle"),
                ("Circle ROI", "circle"),
            )
        ):
            ttk.Radiobutton(
                tools,
                text=label,
                value=value,
                variable=self.shape_tool_var,
                command=self._on_tool_selected,
            ).grid(row=row, column=0, sticky="w", pady=1)
        roi_buttons = ttk.Frame(tools)
        roi_buttons.grid(row=5, column=0, sticky="ew", pady=(4, 0))
        roi_buttons.columnconfigure(0, weight=1)
        roi_buttons.columnconfigure(1, weight=1)
        ttk.Button(roi_buttons, text="Clear ROIs", command=self.clear_rois).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        self.roi_count_var = tk.StringVar(value="ROIs: 0")
        ttk.Label(roi_buttons, textvariable=self.roi_count_var, anchor="e").grid(row=0, column=1, sticky="ew")
        if self._show_session_done_button:
            ttk.Button(roi_buttons, text="Save + Close", command=self._finish_session).grid(
                row=1,
                column=0,
                sticky="ew",
                pady=(4, 0),
                padx=(0, 4),
            )
            ttk.Button(roi_buttons, text="Cancel", command=self._cancel_session).grid(
                row=1,
                column=1,
                sticky="ew",
                pady=(4, 0),
            )

        result_frame = ttk.LabelFrame(sidebar, text="Detected shapes", padding=(6, 4))
        result_frame.grid(row=4, column=0, sticky="nsew", pady=(0, 8))
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        sidebar.rowconfigure(4, weight=1)
        self.recognition_tree = ttk.Treeview(
            result_frame,
            columns=("use", "roi", "type", "role", "target", "score"),
            show="headings",
            selectmode="extended",
            height=7,
        )
        self.recognition_tree.heading("use", text="Use")
        self.recognition_tree.heading("roi", text="ROI")
        self.recognition_tree.heading("type", text="Type")
        self.recognition_tree.heading("role", text="Role")
        self.recognition_tree.heading("target", text="Target")
        self.recognition_tree.heading("score", text="Score")
        self.recognition_tree.column("use", width=42, stretch=False)
        self.recognition_tree.column("roi", width=46, stretch=False)
        self.recognition_tree.column("type", width=70, stretch=False)
        self.recognition_tree.column("role", width=116, stretch=False)
        self.recognition_tree.column("target", width=130, stretch=True)
        self.recognition_tree.column("score", width=54, stretch=False)
        self.recognition_tree.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(result_frame, orient=tk.VERTICAL, command=self.recognition_tree.yview)
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.recognition_tree.configure(yscrollcommand=result_scroll.set)
        self.recognition_tree.bind("<<TreeviewSelect>>", self._on_recognition_tree_selected)
        self.recognition_tree.bind("<Double-1>", self._use_selected_recognition_row)
        self.recognition_tree.bind("<Return>", self._use_selected_recognition_row)

        recognition_actions = ttk.Frame(result_frame)
        recognition_actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        recognition_actions.columnconfigure(0, weight=1)
        recognition_actions.columnconfigure(1, weight=1)
        recognition_actions.columnconfigure(2, weight=1)
        recognition_actions.columnconfigure(3, weight=1)
        recognition_actions.columnconfigure(4, weight=1)
        recognition_actions.columnconfigure(5, weight=0)
        ttk.Button(recognition_actions, text="Use selected", command=self._use_selected_recognition_row).grid(
            row=0,
            column=0,
            sticky="ew",
            padx=(0, 4),
        )
        ttk.Button(recognition_actions, text="Deselect", command=self._clear_selected_recognition_roi).grid(
            row=0,
            column=1,
            sticky="ew",
            padx=(0, 4),
        )
        self.save_official_button = ttk.Button(
            recognition_actions,
            text="Save official",
            command=self._save_official_baseline_from_ui,
        )
        self.save_official_button.grid(
            row=0,
            column=2,
            sticky="ew",
            padx=(0, 4),
        )
        self.score_official_button = ttk.Button(
            recognition_actions,
            text="Score",
            command=self._score_against_official_baseline_from_ui,
        )
        self.score_official_button.grid(
            row=0,
            column=3,
            sticky="ew",
            padx=(0, 4),
        )
        self.save_v5_memory_button = ttk.Button(
            recognition_actions,
            text="Save v5",
            command=self._save_v5_sequence_memory_from_ui,
        )
        self.save_v5_memory_button.grid(
            row=0,
            column=4,
            sticky="ew",
            padx=(0, 4),
        )
        if self._captured_image_path is not None:
            self.save_official_button.grid_remove()
            self.score_official_button.grid_remove()
            self.save_v5_memory_button.grid_remove()
        self.recognition_legend_toggle_button = ttk.Button(
            recognition_actions,
            text="Legend",
            command=self._toggle_recognition_legend,
            width=7,
        )
        self.recognition_legend_toggle_button.grid(
            row=0,
            column=5,
            sticky="e",
        )
        review_context = CAPTURE_REVIEW_CONTEXT_LABELS.get(self._capture_id)
        role_row = 1
        self.feature_role_context_var = tk.StringVar(
            value=f"Review target: {review_context}" if review_context else ""
        )
        self.feature_role_context_label = ttk.Label(
            recognition_actions,
            textvariable=self.feature_role_context_var,
            anchor="w",
        )
        if review_context:
            self.feature_role_context_label.grid(
                row=1,
                column=0,
                columnspan=6,
                sticky="ew",
                pady=(4, 0),
            )
            role_row = 2
        ttk.Label(recognition_actions, text="Feature role").grid(
            row=role_row,
            column=0,
            sticky="w",
            pady=(4, 0),
        )
        initial_role = (
            "ball_candidate"
            if not self._capture_id and "ball_candidate" in self._feature_role_keys
            else self._feature_role_keys[0]
        )
        self.feature_role_var = tk.StringVar(
            value=feature_role_display_label(initial_role, self._capture_id)
        )
        self.feature_role_combobox = ttk.Combobox(
            recognition_actions,
            textvariable=self.feature_role_var,
            values=self._feature_role_display_values,
            state="readonly",
            width=34,
        )
        self.feature_role_combobox.grid(
            row=role_row,
            column=1,
            columnspan=4,
            sticky="ew",
            pady=(4, 0),
            padx=(0, 4),
        )
        ttk.Button(recognition_actions, text="Set role", command=self._set_selected_recognition_role).grid(
            row=role_row,
            column=5,
            sticky="ew",
            pady=(4, 0),
        )

        measurement = ttk.Frame(result_frame)
        measurement.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        measurement.columnconfigure(1, weight=1)
        ttk.Label(measurement, text="Short edge um").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.short_edge_um_var = tk.StringVar(value=f"{FIXED_MEASUREMENT_SHORT_EDGE_LENGTH_UM:g}")
        ttk.Label(measurement, textvariable=self.short_edge_um_var, anchor="e", width=8).grid(
            row=0,
            column=1,
            sticky="w",
        )
        self.measurement_var = tk.StringVar(value="Measurement: select a rectangle and circle/blob")
        ttk.Label(measurement, textvariable=self.measurement_var, anchor="w", wraplength=260).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(3, 0),
        )
        self._build_recognition_legend(result_frame)

        self.tool_status_var = tk.StringVar(value="Drawing: UI only")
        ttk.Label(sidebar, textvariable=self.tool_status_var, anchor="w", wraplength=260).grid(
            row=6,
            column=0,
            sticky="ew",
        )

        image_panel = ttk.Frame(main)
        image_panel.rowconfigure(1, weight=1)
        image_panel.columnconfigure(0, weight=1)
        main.add(image_panel, weight=4)

        self.image_status_var = tk.StringVar(value="")
        ttk.Label(image_panel, textvariable=self.image_status_var, anchor="w").grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 4),
        )

        canvas_frame = ttk.Frame(image_panel)
        canvas_frame.grid(row=1, column=0, sticky="nsew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)
        self.image_canvas = tk.Canvas(
            canvas_frame,
            background="#151515",
            highlightthickness=1,
            highlightbackground="#9a9a9a",
        )
        self.image_canvas.grid(row=0, column=0, sticky="nsew")
        self.image_canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.image_canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.image_canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.image_canvas.bind("<MouseWheel>", self._on_canvas_mouse_wheel)
        self.image_canvas.bind("<Button-4>", self._on_canvas_mouse_wheel)
        self.image_canvas.bind("<Button-5>", self._on_canvas_mouse_wheel)
        self.image_canvas.bind("<Configure>", self._on_canvas_configure)
        x_scroll = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.image_canvas.xview)
        y_scroll = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.image_canvas.yview)
        x_scroll.grid(row=1, column=0, sticky="ew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        self.image_canvas.configure(xscrollcommand=x_scroll.set, yscrollcommand=y_scroll.set)
        self._refresh_parameter_visibility()

    def _build_recognition_legend(self, parent: tk.Widget) -> None:
        legend = ttk.Frame(parent)
        self.recognition_legend_frame = legend
        legend.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        legend.columnconfigure(1, weight=1)

        ttk.Label(legend, text="Rectangle overlays").grid(row=0, column=0, columnspan=2, sticky="w")
        self.edge_rectangle_legend_swatch = tk.Label(
            legend,
            background=EDGE_RECTANGLE_OVERLAY_COLOR,
            width=2,
            relief=tk.SOLID,
            borderwidth=1,
        )
        self.edge_rectangle_legend_swatch.grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(2, 0))
        ttk.Label(legend, text="Yellow = edge/line rectangle").grid(row=1, column=1, sticky="w", pady=(2, 0))

        self.bright_rectangle_legend_swatch = tk.Label(
            legend,
            background=BRIGHT_RECTANGLE_OVERLAY_COLOR,
            width=2,
            relief=tk.SOLID,
            borderwidth=1,
        )
        self.bright_rectangle_legend_swatch.grid(row=2, column=0, sticky="w", padx=(0, 6), pady=(2, 0))
        ttk.Label(legend, text="Magenta = bright silhouette rectangle").grid(row=2, column=1, sticky="w", pady=(2, 0))
        ttk.Label(legend, text="Dashed edge = inferred missing side").grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="w",
            pady=(2, 0),
        )
        legend.grid_remove()

    def _toggle_recognition_legend(self) -> None:
        if self._recognition_legend_visible:
            self.recognition_legend_frame.grid_remove()
            self.recognition_legend_toggle_button.configure(text="Legend")
            self._recognition_legend_visible = False
            return
        self.recognition_legend_frame.grid()
        self.recognition_legend_toggle_button.configure(text="Hide")
        self._recognition_legend_visible = True

    def reload_library(self) -> None:
        if self._captured_image_path is not None:
            self.load_captured_image(self._captured_image_path)
            return
        self.library = load_standard_position_library(self.image_root)
        self._position_display_to_id = {
            position.display_name: position.id
            for position in self.library.positions
        }
        values = tuple(position.display_name for position in self.library.positions)
        self.position_combobox.configure(values=values)
        if values:
            current_id = self.selected_position_id()
            if current_id and self.library.position(current_id) is not None:
                self.select_position(current_id)
            elif self.library.position(DEFAULT_START_POSITION_ID) is not None:
                self.select_position(DEFAULT_START_POSITION_ID)
            else:
                self.position_var.set(values[0])
                self._populate_images_for_selected_position()
        else:
            self.position_var.set("")
            self._populate_images(())
            self.position_summary_var.set("No standard positions found")

    def load_captured_image(self, image_path: str | Path) -> None:
        path = Path(image_path)
        image = VisionPositionImage(
            position_id="captured",
            position_label="captured image",
            batch="capture",
            path=path,
        )
        position = VisionPosition(id="captured", label="captured image", batches=("capture",))
        self.library = VisionPositionLibrary(positions=(position,), images=(image,))
        self._position_display_to_id = {position.display_name: position.id}
        self.position_combobox.configure(values=(position.display_name,), state="disabled")
        self.refresh_button.configure(state="disabled")
        self.position_var.set(position.display_name)
        self.position_summary_var.set(f"Captured image | {path}")
        self._populate_images((image,))

    def preload_review_session(self, session: dict[str, Any]) -> None:
        """Load baseline ROIs, run live recognition, and preselect nearest proposals."""

        raw_rois = session.get("rois")
        if not isinstance(raw_rois, list):
            raw_rois = []
        self._rois = []
        for raw_roi in raw_rois:
            if not isinstance(raw_roi, dict):
                continue
            try:
                roi = VisionROI(
                    kind=str(raw_roi.get("kind") or "box"),
                    x1=float(raw_roi["x1"]),
                    y1=float(raw_roi["y1"]),
                    x2=float(raw_roi["x2"]),
                    y2=float(raw_roi["y2"]),
                    orientation=str(raw_roi.get("orientation") or "right"),
                ).normalized
            except (KeyError, TypeError, ValueError):
                continue
            if roi.width >= 3.0 and roi.height >= 3.0:
                self._rois.append(roi)
        self._update_roi_count()
        self._refresh_parameter_visibility()
        if self._rois:
            self.run_recognition()
            self._preselect_nearest_initial_features(session)
        else:
            self._render_current_image()
        self.tool_status_var.set(
            "Baseline proposals loaded on the live image; review, replace, assign roles, then save."
        )

    def _preselect_nearest_initial_features(self, session: dict[str, Any]) -> None:
        expected = initial_selected_recognition_items(session)
        if not expected or not self._recognition_tree_items:
            return
        used: set[str] = set()
        for expected_item in expected:
            expected_kind = str(expected_item.get("shape_kind") or "")
            expected_center = recognition_payload_center(expected_item)
            if expected_center is None:
                continue
            expected_roi_index = int(expected_item.get("roi_index") or 0)
            candidates: list[tuple[float, str]] = []
            for item_id, item in self._recognition_tree_items.items():
                if item_id in used or item.shape_kind != expected_kind:
                    continue
                if expected_roi_index and item.roi_index != expected_roi_index:
                    continue
                current_center = self._shape_center(item.shape_kind, item.shape)
                distance = math.hypot(
                    current_center[0] - expected_center[0],
                    current_center[1] - expected_center[1],
                )
                candidates.append((distance, item_id))
            if not candidates:
                continue
            distance, item_id = min(candidates)
            roi = self._rois[self._recognition_tree_items[item_id].roi_index - 1]
            if distance > 0.4 * math.hypot(roi.width, roi.height):
                continue
            used.add(item_id)
            self._selected_recognition_item_ids.add(item_id)
            self._selected_recognition_item_order.append(item_id)
            role = str(expected_item.get("feature_role") or "").strip()
            if not role:
                role = expected_role_for_capture(self._capture_id, expected_kind)
            if role in self._feature_role_keys:
                self._recognition_item_role_overrides[item_id] = role
        self._update_recognition_tree_selection_marks()
        self._update_selected_measurement()
        self._render_current_image()

    def selected_position_id(self) -> str:
        raw = self.position_var.get().strip()
        if raw in self._position_display_to_id:
            return self._position_display_to_id[raw]
        return normalize_standard_position_id(raw.split(" - ", 1)[0])

    def select_position(self, position_id: str) -> None:
        normalized_id = normalize_standard_position_id(position_id)
        position = self.library.position(normalized_id)
        if position is None:
            return
        self.position_var.set(position.display_name)
        self._populate_images_for_selected_position()

    def current_images(self) -> tuple[VisionPositionImage, ...]:
        return self._current_images

    def current_rois(self) -> tuple[VisionROI, ...]:
        return tuple(self._rois)

    def reviewed_mirror_roi_payload(self) -> dict[str, float] | None:
        if self._capture_id not in {"2.6.1", "4.6.2"} or not self._rois:
            return None
        return {
            "x1": min(roi.normalized.x1 for roi in self._rois),
            "y1": min(roi.normalized.y1 for roi in self._rois),
            "x2": max(roi.normalized.x2 for roi in self._rois),
            "y2": max(roi.normalized.y2 for roi in self._rois),
            "source": "reviewed_union_of_side_rois",
        }

    def add_roi(self, roi: VisionROI) -> None:
        normalized = roi.normalized
        if normalized.width < 3.0 or normalized.height < 3.0:
            return
        had_result = self._recognition_result is not None
        self._rois.append(normalized)
        self._update_roi_count()
        self._refresh_parameter_visibility()
        if had_result:
            self.run_recognition(preserve_view=True)
        else:
            self._render_current_image()

    def clear_rois(self) -> None:
        self._cancel_pending_recognition()
        self._rois.clear()
        self._roi_drag = None
        self._roi_preview_item = None
        self._recognition_result = None
        self._populate_recognition_tree(None)
        self._update_roi_count()
        self._refresh_parameter_visibility()
        self._render_current_image()
        self.tool_status_var.set("ROIs: 0")

    def selected_recognizer_name(self) -> str:
        display_name = self.recognizer_var.get().strip()
        return self._geometry_recognizer_display_to_name.get(display_name, DEFAULT_GEOMETRY_RECOGNIZER_NAME)

    def selected_silhouette_recognizer_name(self) -> str | None:
        display_name = self.silhouette_recognizer_var.get().strip()
        return self._silhouette_recognizer_display_to_name.get(display_name) or None

    def selected_geometry_sensitivity(self) -> float:
        return clamp_geometry_sensitivity(self.geometry_sensitivity_var.get())

    def selected_bright_rectangle_sensitivity(self) -> float:
        return clamp_bright_rectangle_sensitivity(self.bright_rectangle_sensitivity_var.get())

    def selected_silhouette_sensitivity(self) -> float:
        return clamp_silhouette_sensitivity(self.silhouette_sensitivity_var.get())

    def selected_short_edge_length_um(self) -> float:
        return FIXED_MEASUREMENT_SHORT_EDGE_LENGTH_UM

    def selected_recognition_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for selection_index, item_id in enumerate(self._ordered_selected_recognition_item_ids(), start=1):
            item = self._recognition_tree_items.get(item_id)
            if item is None:
                continue
            payload.setdefault(f"roi_{item.roi_index}", []).append(
                self._recognition_tree_item_to_dict(
                    item,
                    selection_index=selection_index,
                    feature_role=self._feature_role_for_item_id(item_id),
                )
            )
        return payload

    def selected_measurement_payload(self) -> dict[str, Any] | None:
        if self._selected_measurement is None:
            return None
        if not self._selected_measurements:
            return None
        return self._selected_measurement_to_dict(self._selected_measurements[0])

    def selected_measurements_payload(self) -> list[dict[str, Any]]:
        return [self._selected_measurement_to_dict(selected) for selected in self._selected_measurements]

    def selected_relative_measurement_payload(self) -> dict[str, Any] | None:
        return relative_measurement_payload_from_measurements(self.selected_measurements_payload())

    def selected_yase_display_status(self, fallback_status: str) -> str:
        return format_relative_measurement_for_yase(self.selected_relative_measurement_payload(), fallback_status)

    def current_session_payload(self, status: str) -> dict[str, Any]:
        if self._selected_image is None:
            raise ValueError("No image is loaded.")
        dimensions = None
        if self._source_gray_image is not None:
            dimensions = {
                "image_width_px": int(self._source_gray_image.shape[1]),
                "image_height_px": int(self._source_gray_image.shape[0]),
            }
        return vision_session_payload(
            image_path=self._selected_image.path,
            rois=self.current_rois(),
            result=self._recognition_result,
            status=status,
            selected_recognition=self.selected_recognition_payload(),
            measurement=self.selected_measurement_payload(),
            measurements=self.selected_measurements_payload(),
            relative_measurement=self.selected_relative_measurement_payload(),
            yase_display=self.selected_yase_display_status(status),
            capture_id=self._capture_id or None,
            mirror_roi=self.reviewed_mirror_roi_payload(),
            image_dimensions_px=dimensions,
        )

    def official_baseline_path(self) -> Path:
        if self._selected_image is None:
            raise ValueError("No image is loaded.")
        return self._official_baseline_dir(self._selected_image) / f"{self._selected_image.path.stem}.json"

    def official_score_path(self) -> Path:
        if self._selected_image is None:
            raise ValueError("No image is loaded.")
        return self._official_score_dir(self._selected_image) / f"{self._selected_image.path.stem}_score.json"

    def v5_sequence_memory_path(self) -> Path:
        if self._selected_image is None:
            raise ValueError("No image is loaded.")
        image = self._selected_image
        if image.batch and image.batch != "capture":
            return self.image_root / image.batch / V5_SEQUENCE_MEMORY_FOLDER_NAME / V5_SEQUENCE_MEMORY_FILE_NAME
        return image.path.parent / V5_SEQUENCE_MEMORY_FOLDER_NAME / V5_SEQUENCE_MEMORY_FILE_NAME

    def save_v5_sequence_memory(
        self,
        *,
        official_baseline: bool = False,
        session_payload: dict[str, Any] | None = None,
    ) -> Path:
        if self._selected_image is None:
            raise ValueError("No image is loaded.")
        capture_id = self._selected_image.path.stem
        session = deepcopy(
            session_payload
            if session_payload is not None
            else self.current_session_payload(f"V5 sequence memory saved for {capture_id}")
        )
        if not isinstance(session.get("selected_recognition"), dict) or not session["selected_recognition"]:
            raise ValueError("Select at least one detected shape with Use selected before saving v5 memory.")

        initialize_sequence_memory, run_sequence_memory_workflow = _v5_sequence_memory_api()
        memory_path = self.v5_sequence_memory_path()
        if memory_path.is_file():
            memory_source: dict[str, Any] = {"memory_path": str(memory_path)}
        else:
            init_payload: dict[str, Any] = {
                "schema_version": 1,
                "command": "init",
                "auto_detect_gross_sessions": False,
                "auto_detect_missing_sessions": False,
                "auto_detect_side_references": False,
            }
            standard_positions_path = self._standard_positions_path_for_image(self._selected_image)
            if standard_positions_path is not None:
                init_payload["standard_positions_path"] = str(standard_positions_path)
            memory_source = {"memory": initialize_sequence_memory(init_payload)}

        record_payload: dict[str, Any] = {
            "schema_version": 1,
            "command": "record",
            **memory_source,
            "capture_id": capture_id,
            "position_id": self._selected_image.position_id,
            "label": self._selected_image.position_label,
            "image_path": str(self._selected_image.path),
            "review_status": "official_baseline" if official_baseline else "reviewed",
            "session": session,
            "official_baseline": bool(official_baseline),
            "output_path": str(memory_path),
        }
        if self._selected_image.machine_positions_um is not None:
            record_payload["machine_positions_um"] = deepcopy(self._selected_image.machine_positions_um)

        result = run_sequence_memory_workflow(record_payload)
        if result.get("ok") is not True:
            raise ValueError(str(result.get("status") or "v5 sequence memory save failed"))
        self._last_v5_sequence_memory_path = memory_path
        return memory_path

    def save_official_baseline(self, *, confirm_replace: bool = False) -> Path | None:
        if self._selected_image is None:
            raise ValueError("No image is loaded.")
        capture_id = self._selected_image.path.stem
        payload = self.current_session_payload(f"Official vision measurement saved for {capture_id}")
        selected_recognition = payload["selected_recognition"]
        if not selected_recognition:
            raise ValueError("Select at least one detected shape with Use selected before saving.")
        output_path = self.official_baseline_path()
        if output_path.exists() and confirm_replace:
            replace = messagebox.askyesno(
                "Replace official measurement?",
                f"Replace the existing official measurement for {capture_id}?",
                parent=self,
            )
            if not replace:
                return None
        payload["standard_capture_id"] = capture_id
        payload["standard_position_id"] = self._selected_image.position_id
        payload["standard_image_rel_path"] = self._selected_image_rel_path(self._selected_image)
        payload["official_baseline"] = {
            "schema_version": 1,
            "capture_id": capture_id,
            "position_id": self._selected_image.position_id,
            "image_rel_path": self._selected_image_rel_path(self._selected_image),
            "baseline_path": str(output_path),
        }
        _write_json(output_path, payload)
        return output_path

    def _save_official_baseline_from_ui(self) -> None:
        try:
            output_path = self.save_official_baseline(confirm_replace=True)
        except (OSError, ValueError) as exc:
            self.tool_status_var.set(f"Official save failed: {exc}")
            messagebox.showerror("Official save failed", str(exc), parent=self)
            return
        if output_path is None:
            self.tool_status_var.set("Official save cancelled")
            return
        v5_path: Path | None = None
        try:
            v5_path = self.save_v5_sequence_memory(official_baseline=True)
        except (ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
            self.tool_status_var.set(f"Official saved; v5 memory sync failed: {exc}")
            messagebox.showwarning(
                "Official saved",
                f"Saved official measurement:\n{output_path}\n\nV5 memory sync failed:\n{exc}",
                parent=self,
            )
            return
        self.tool_status_var.set(f"Official saved: {output_path} | v5 memory: {v5_path}")
        messagebox.showinfo(
            "Official saved",
            f"Saved official measurement:\n{output_path}\n\nSaved v5 memory:\n{v5_path}",
            parent=self,
        )

    def _save_v5_sequence_memory_from_ui(self) -> None:
        try:
            output_path = self.save_v5_sequence_memory()
        except (ImportError, OSError, ValueError, json.JSONDecodeError) as exc:
            self.tool_status_var.set(f"V5 memory save failed: {exc}")
            messagebox.showerror("V5 memory save failed", str(exc), parent=self)
            return
        self.tool_status_var.set(f"V5 memory saved: {output_path}")
        messagebox.showinfo("V5 memory saved", f"Saved v5 sequence memory:\n{output_path}", parent=self)

    def score_against_official_baseline(self) -> tuple[Path, dict[str, Any]]:
        if self._selected_image is None:
            raise ValueError("No image is loaded.")
        baseline_path = self.official_baseline_path()
        if not baseline_path.is_file():
            raise FileNotFoundError(f"No official measurement saved for {self._selected_image.path.stem}: {baseline_path}")
        baseline_payload = json.loads(baseline_path.read_text(encoding="utf-8"))
        capture_id = self._selected_image.path.stem
        candidate_payload = self.current_session_payload(f"Vision score checked against official for {capture_id}")
        from vision_scoring import score_session_payloads

        score = score_session_payloads(
            baseline_payload,
            candidate_payload,
            capture_id=capture_id,
            position_id=self._selected_image.position_id,
            image_rel_path=self._selected_image_rel_path(self._selected_image),
            baseline_path=baseline_path,
        )
        output_path = self.official_score_path()
        score["score_path"] = str(output_path)
        _write_json(output_path, score)
        return output_path, score

    def _score_against_official_baseline_from_ui(self) -> None:
        try:
            output_path, score = self.score_against_official_baseline()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.tool_status_var.set(f"Official score failed: {exc}")
            messagebox.showerror("Official score failed", str(exc), parent=self)
            return
        status = "Score passed" if score.get("passed") is True else "Score failed"
        self.tool_status_var.set(f"{status}: {score.get('status')}")
        messagebox.showinfo(
            "Official score",
            self._format_official_score_message(score, output_path),
            parent=self,
        )

    def _official_baseline_dir(self, image: VisionPositionImage) -> Path:
        if image.batch and image.batch != "capture":
            return self.image_root / image.batch / OFFICIAL_BASELINE_FOLDER_NAME
        return image.path.parent / OFFICIAL_BASELINE_FOLDER_NAME

    def _official_score_dir(self, image: VisionPositionImage) -> Path:
        if image.batch and image.batch != "capture":
            return self.image_root / image.batch / VISION_SCORE_FOLDER_NAME
        return image.path.parent / VISION_SCORE_FOLDER_NAME

    def _standard_positions_path_for_image(self, image: VisionPositionImage) -> Path | None:
        if image.standard_positions_path is not None and image.standard_positions_path.is_file():
            return image.standard_positions_path
        if image.batch and image.batch != "capture":
            metadata_path = self.image_root / image.batch / "standard_positions.json"
            if metadata_path.is_file():
                return metadata_path
        return None

    def _format_official_score_message(self, score: dict[str, Any], output_path: Path) -> str:
        lines = [
            f"{'PASS' if score.get('passed') is True else 'FAIL'}: {score.get('status')}",
        ]
        metrics = score.get("metrics") if isinstance(score.get("metrics"), dict) else {}
        if metrics.get("max_shape_error_px") is not None:
            lines.append(f"Max shape error: {float(metrics['max_shape_error_px']):.3f} px")
        if metrics.get("max_ball_center_error_px") is not None:
            lines.append(f"Max ball center error: {float(metrics['max_ball_center_error_px']):.3f} px")
        if metrics.get("max_rectangle_center_error_px") is not None:
            lines.append(f"Max rectangle center error: {float(metrics['max_rectangle_center_error_px']):.3f} px")
        if metrics.get("max_abs_xy_error_um") is not None:
            lines.append(f"Max relative XY error: {float(metrics['max_abs_xy_error_um']):.3f} um")
        if metrics.get("max_distance_error_um") is not None:
            lines.append(f"Max distance error: {float(metrics['max_distance_error_um']):.3f} um")
        lines.append(f"Score JSON: {output_path}")
        return "\n".join(lines)

    def _selected_image_rel_path(self, image: VisionPositionImage) -> str:
        if image.batch and image.batch != "capture":
            try:
                return image.path.relative_to(self.image_root / image.batch).as_posix()
            except ValueError:
                pass
        try:
            return image.path.relative_to(self.image_root).as_posix()
        except ValueError:
            return image.path.name

    def _selected_measurement_to_dict(self, selected: VisionSelectedMeasurement) -> dict[str, Any]:
        measurement = selected.measurement
        rectangle_selection = selected.rectangle_selection
        circle_selection = selected.circle_selection
        rectangle_feature_role = self._feature_role_for_item_id(rectangle_selection.item_id)
        circle_feature_role = self._feature_role_for_item_id(circle_selection.item_id)
        return {
            "short_edge_length_um": measurement.short_edge_length_um,
            "um_per_pixel": measurement.um_per_pixel,
            "rectangle_roi_index": rectangle_selection.roi_index,
            "rectangle_roi": vision_roi_to_dict(self._rois[rectangle_selection.roi_index - 1]),
            "rectangle_feature_role": rectangle_feature_role,
            "circle_roi_index": circle_selection.roi_index,
            "circle_roi": vision_roi_to_dict(self._rois[circle_selection.roi_index - 1]),
            "circle_source": measurement.circle.source,
            "circle_feature_role": circle_feature_role,
            "short_edge": {
                "start": {"x": measurement.short_edge.start[0], "y": measurement.short_edge.start[1]},
                "end": {"x": measurement.short_edge.end[0], "y": measurement.short_edge.end[1]},
                "midpoint": {
                    "x": measurement.short_edge.midpoint[0],
                    "y": measurement.short_edge.midpoint[1],
                },
                "length_px": measurement.short_edge.length_px,
            },
            "circle_center": {
                "x": measurement.circle.x,
                "y": measurement.circle.y,
                "radius": measurement.circle.radius,
            },
            "delta_px": {
                "dx": measurement.dx_px,
                "dy": measurement.dy_px,
                "distance": measurement.distance_px,
            },
            "delta_um": {
                "dx": measurement.dx_um,
                "dy": measurement.dy_um,
                "distance": measurement.distance_um,
            },
        }

    def run_recognition(self, *, preserve_view: bool = False) -> VisionRecognitionResult | None:
        if self._source_gray_image is None:
            self.tool_status_var.set("Recognition: no image loaded")
            return None
        view_state = self._canvas_view_state() if preserve_view else None
        self._cancel_pending_recognition()
        if not self._rois:
            self._recognition_result = None
            self._populate_recognition_tree(None)
            self._render_current_image()
            if view_state is not None:
                self._restore_canvas_view_state(view_state)
            self.tool_status_var.set(ROI_REQUIRED_MESSAGE)
            return None
        if self._capture_id in COARSE_TOP_CAPTURE_IDS:
            result = recognize_coarse_top_ball(
                self._source_gray_image,
                tuple(self._rois),
                self._capture_id,
            )
        else:
            result = recognize_shapes(
                self._source_gray_image,
                self.selected_recognizer_name(),
                tuple(self._rois),
                geometry_sensitivity=self.selected_geometry_sensitivity(),
                bright_rectangle_sensitivity=self.selected_bright_rectangle_sensitivity(),
                silhouette_algorithm_name=self.selected_silhouette_recognizer_name(),
                silhouette_sensitivity=self.selected_silhouette_sensitivity(),
            )
        if self._capture_id in {"2.6.1", "4.6.2"}:
            trench_lines = detect_side_trench_ruler_lines(self._source_gray_image, tuple(self._rois))
            if trench_lines:
                result = VisionRecognitionResult(
                    algorithm_name=f"{result.algorithm_name}+two_line_trench_ruler",
                    display_name=f"{result.display_name} + two-line trench ruler",
                    lines=tuple(trench_lines) + result.lines,
                    intersections=result.intersections,
                    circles=result.circles,
                    rectangles=result.rectangles,
                    semicircles=result.semicircles,
                    silhouettes=result.silhouettes,
                    message=(
                        f"{result.message}; proposed physical trench floor and top lines "
                        "for the reviewed 300 um mirror ruler"
                    ),
                )
        self._recognition_result = result
        self._populate_recognition_tree(result)
        self._render_current_image()
        if view_state is not None:
            self._restore_canvas_view_state(view_state)
        self.tool_status_var.set(result.message)
        return result

    def clear_recognition(self) -> None:
        self._cancel_pending_recognition()
        self._recognition_result = None
        self._populate_recognition_tree(None)
        self._render_current_image()
        self.tool_status_var.set("Recognition cleared")

    def _finish_session(self) -> None:
        self._session_saved = True
        self._session_cancelled = False
        if self._session_done_callback is not None:
            self._session_done_callback()
            return
        self.destroy()

    def _cancel_session(self) -> None:
        self._session_saved = False
        self._session_cancelled = True
        if self._session_done_callback is not None:
            self._session_done_callback()
            return
        self.destroy()

    def _on_position_selected(self, _event: tk.Event | None = None) -> None:
        self._populate_images_for_selected_position()

    def _populate_images_for_selected_position(self) -> None:
        position_id = self.selected_position_id()
        position = self.library.position(position_id)
        images = self.library.images_for_position(position_id)
        self._populate_images(images)
        if position is None:
            self.position_summary_var.set("")
            return
        batch_text = ", ".join(position.batches) if position.batches else "no batches"
        self.position_summary_var.set(
            f"{position.id} - {position.label or 'unlabelled'} | batches: {batch_text} | images: {len(images)}"
        )

    def _populate_images(self, images: tuple[VisionPositionImage, ...]) -> None:
        self._current_images = images
        self._image_item_to_image.clear()
        for item_id in self.image_tree.get_children():
            self.image_tree.delete(item_id)
        for index, image in enumerate(images):
            item_id = f"image_{index}"
            self._image_item_to_image[item_id] = image
            self.image_tree.insert("", "end", iid=item_id, values=(image.batch, self._image_tree_file_label(image)))
        if images:
            first_item = "image_0"
            self.image_tree.selection_set(first_item)
            self.image_tree.focus(first_item)
            self._load_image(images[0])
        else:
            self._clear_image("No captured image for this position")

    def _image_tree_file_label(self, image: VisionPositionImage) -> str:
        if image.batch and image.batch != "capture":
            try:
                return image.path.relative_to(self.image_root / image.batch).as_posix()
            except ValueError:
                pass
        return image.path.name

    def _on_image_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.image_tree.selection()
        if not selection:
            return
        image = self._image_item_to_image.get(selection[0])
        if image is not None:
            self._load_image(image)

    def _load_image(self, image: VisionPositionImage) -> None:
        self._cancel_pending_recognition()
        self.image_canvas.delete("all")
        self._selected_image = image
        self._source_photo_image = None
        self._source_gray_image = None
        self._photo_image = None
        self._image_canvas_item = None
        self._view_scale = None
        self._recognition_result = None
        self._rois.clear()
        self._update_roi_count()
        self._refresh_parameter_visibility()
        self._populate_recognition_tree(None)
        recognition_error = ""
        try:
            gray_image = read_grayscale_image(image.path)
        except (OSError, ValueError) as exc:
            gray_image = None
            recognition_error = f"Recognition unavailable: {exc}"
        try:
            photo = load_display_photo_image(image.path, gray_image, master=self)
        except (OSError, ValueError, tk.TclError) as exc:
            self._clear_image(f"Could not load {image.path.name}: {exc}")
            return
        self._source_gray_image = gray_image
        self._source_photo_image = photo
        self._render_current_image()
        if recognition_error:
            self.tool_status_var.set(recognition_error)

    def _render_current_image(
        self,
        *,
        anchor_image_point: tuple[float, float] | None = None,
        anchor_canvas_point: tuple[float, float] | None = None,
    ) -> None:
        if self._source_photo_image is None or self._selected_image is None:
            return

        canvas_width, canvas_height = self._canvas_viewport_size()
        if (
            anchor_image_point is None
            and self._image_canvas_item is not None
            and self._source_photo_image is not None
        ):
            center_canvas_x = self.image_canvas.canvasx(canvas_width / 2.0)
            center_canvas_y = self.image_canvas.canvasy(canvas_height / 2.0)
            anchor_image_point = self._canvas_to_image_point(center_canvas_x, center_canvas_y)
            anchor_canvas_point = (canvas_width / 2.0, canvas_height / 2.0)

        self.image_canvas.delete("all")
        source = self._source_photo_image
        image = self._selected_image
        source_width = source.width()
        source_height = source.height()
        fit_scale = 1.0 / fit_subsample_factor(source_width, source_height, canvas_width, canvas_height)
        requested_scale = fit_scale if self._view_scale is None else max(fit_scale, min(MAX_VIEW_SCALE, self._view_scale))
        display, actual_scale = self._display_photo_for_scale(source, requested_scale)
        display_width = display.width()
        display_height = display.height()
        x = max(0, (canvas_width - display_width) // 2)
        y = max(0, (canvas_height - display_height) // 2)
        scroll_width = max(canvas_width, display_width + 2 * x)
        scroll_height = max(canvas_height, display_height + 2 * y)

        self._photo_image = display
        self._display_origin = (x, y)
        self._display_subsample = 1.0 / actual_scale
        self._view_scale = actual_scale
        self._image_canvas_item = self.image_canvas.create_image(x, y, image=display, anchor="nw")
        self.image_canvas.configure(scrollregion=(0, 0, scroll_width, scroll_height))
        if anchor_image_point is not None and anchor_canvas_point is not None:
            self._scroll_to_image_point(anchor_image_point, anchor_canvas_point, scroll_width, scroll_height)
        elif display_width <= canvas_width and display_height <= canvas_height:
            self.image_canvas.xview_moveto(0.0)
            self.image_canvas.yview_moveto(0.0)
        scale_text = f"{actual_scale * 100:.0f}%"
        self.image_status_var.set(
            f"{image.position_id} | {image.display_name} | "
            f"{source_width} x {source_height} px | view {display_width} x {display_height} px ({scale_text})"
        )
        self._draw_roi_overlay()
        self._draw_recognition_overlay()

    def _display_photo_for_scale(self, source: tk.PhotoImage, scale: float) -> tuple[tk.PhotoImage, float]:
        if scale >= 1.0:
            zoom = max(1, min(int(MAX_VIEW_SCALE), int(round(scale))))
            return source.zoom(zoom, zoom), float(zoom)
        subsample = max(1, int(round(1.0 / max(scale, 1e-9))))
        return source.subsample(subsample, subsample), 1.0 / subsample

    def _scroll_to_image_point(
        self,
        image_point: tuple[float, float],
        canvas_point: tuple[float, float],
        scroll_width: int,
        scroll_height: int,
    ) -> None:
        canvas_width, canvas_height = self._canvas_viewport_size()
        image_canvas_x, image_canvas_y = self._image_to_canvas_point(*image_point)
        target_left = max(0.0, min(image_canvas_x - canvas_point[0], float(max(scroll_width - canvas_width, 0))))
        target_top = max(0.0, min(image_canvas_y - canvas_point[1], float(max(scroll_height - canvas_height, 0))))
        if scroll_width > canvas_width:
            self.image_canvas.xview_moveto(target_left / max(float(scroll_width), 1.0))
        else:
            self.image_canvas.xview_moveto(0.0)
        if scroll_height > canvas_height:
            self.image_canvas.yview_moveto(target_top / max(float(scroll_height), 1.0))
        else:
            self.image_canvas.yview_moveto(0.0)

    def _view_scale_levels(self) -> tuple[float, ...]:
        if self._source_photo_image is None:
            return (1.0,)
        source_width = self._source_photo_image.width()
        source_height = self._source_photo_image.height()
        canvas_width, canvas_height = self._canvas_viewport_size()
        fit_scale = 1.0 / fit_subsample_factor(source_width, source_height, canvas_width, canvas_height)
        levels = [fit_scale]
        levels.extend(1.0 / denominator for denominator in (8, 6, 5, 4, 3, 2))
        levels.extend(float(zoom) for zoom in range(1, int(MAX_VIEW_SCALE) + 1))
        return tuple(sorted({round(level, 6) for level in levels if fit_scale <= level <= MAX_VIEW_SCALE}))

    def _canvas_viewport_size(self) -> tuple[int, int]:
        width = self.image_canvas.winfo_width()
        height = self.image_canvas.winfo_height()
        if width <= 1 or height <= 1:
            self.update_idletasks()
            width = self.image_canvas.winfo_width()
            height = self.image_canvas.winfo_height()
        return max(width, 1), max(height, 1)

    def _canvas_view_state(self) -> tuple[tuple[float, float], tuple[float, float]]:
        return self.image_canvas.xview(), self.image_canvas.yview()

    def _restore_canvas_view_state(self, state: tuple[tuple[float, float], tuple[float, float]]) -> None:
        xview, yview = state
        if xview:
            self.image_canvas.xview_moveto(xview[0])
        if yview:
            self.image_canvas.yview_moveto(yview[0])

    def _on_canvas_configure(self, _event: tk.Event | None = None) -> None:
        self._render_current_image()

    def _clear_image(self, message: str) -> None:
        self._cancel_pending_recognition()
        self._selected_image = None
        self._source_gray_image = None
        self._source_photo_image = None
        self._photo_image = None
        self._image_canvas_item = None
        self._view_scale = None
        self._recognition_result = None
        self._rois.clear()
        self._update_roi_count()
        self._refresh_parameter_visibility()
        self._populate_recognition_tree(None)
        self.image_canvas.delete("all")
        self.image_canvas.configure(scrollregion=(0, 0, 1, 1))
        self.image_canvas.create_text(20, 20, text=message, anchor="nw", fill="#f1f1f1")
        self.image_status_var.set(message)

    def _on_recognizer_selected(self, _event: tk.Event | None = None) -> None:
        self._refresh_parameter_visibility()
        self._clear_stale_recognition(
            "Recognizer changed; press Run to update the current ROIs"
        )

    def _on_geometry_sensitivity_changed(self, value: str | None = None) -> None:
        sensitivity = clamp_geometry_sensitivity(
            float(value) if value is not None else self.geometry_sensitivity_var.get()
        )
        self.geometry_sensitivity_text_var.set(f"{sensitivity:.2f}")
        if self._recognition_result is not None:
            self._schedule_recognition_rerun()

    def _on_geometry_sensitivity_entry_commit(self, _event: tk.Event | None = None) -> str:
        try:
            sensitivity = clamp_geometry_sensitivity(float(self.geometry_sensitivity_text_var.get()))
        except ValueError:
            sensitivity = self.selected_geometry_sensitivity()
        self.geometry_sensitivity_var.set(sensitivity)
        self._on_geometry_sensitivity_changed(str(sensitivity))
        return "break"

    def _on_geometry_sensitivity_scale_mouse(self, event: tk.Event) -> str:
        width = max(self.geometry_sensitivity_scale.winfo_width(), 1)
        sensitivity = geometry_sensitivity_from_scale_x(float(event.x), float(width))
        self.geometry_sensitivity_var.set(sensitivity)
        self._on_geometry_sensitivity_changed(str(sensitivity))
        return "break"

    def _on_bright_rectangle_sensitivity_changed(self, value: str | None = None) -> None:
        sensitivity = clamp_bright_rectangle_sensitivity(
            float(value) if value is not None else self.bright_rectangle_sensitivity_var.get()
        )
        self.bright_rectangle_sensitivity_text_var.set(f"{sensitivity:.2f}")
        if self._recognition_result is not None:
            self._schedule_recognition_rerun()

    def _on_bright_rectangle_sensitivity_entry_commit(self, _event: tk.Event | None = None) -> str:
        try:
            sensitivity = clamp_bright_rectangle_sensitivity(float(self.bright_rectangle_sensitivity_text_var.get()))
        except ValueError:
            sensitivity = self.selected_bright_rectangle_sensitivity()
        self.bright_rectangle_sensitivity_var.set(sensitivity)
        self._on_bright_rectangle_sensitivity_changed(str(sensitivity))
        return "break"

    def _on_bright_rectangle_sensitivity_scale_mouse(self, event: tk.Event) -> str:
        width = max(self.bright_rectangle_sensitivity_scale.winfo_width(), 1)
        sensitivity = bright_rectangle_sensitivity_from_scale_x(float(event.x), float(width))
        self.bright_rectangle_sensitivity_var.set(sensitivity)
        self._on_bright_rectangle_sensitivity_changed(str(sensitivity))
        return "break"

    def _on_silhouette_sensitivity_changed(self, value: str | None = None) -> None:
        sensitivity = clamp_silhouette_sensitivity(
            float(value) if value is not None else self.silhouette_sensitivity_var.get()
        )
        self.silhouette_sensitivity_text_var.set(f"{sensitivity:.2f}")
        if self._recognition_result is not None:
            self._schedule_recognition_rerun()

    def _on_silhouette_sensitivity_entry_commit(self, _event: tk.Event | None = None) -> str:
        try:
            sensitivity = clamp_silhouette_sensitivity(float(self.silhouette_sensitivity_text_var.get()))
        except ValueError:
            sensitivity = self.selected_silhouette_sensitivity()
        self.silhouette_sensitivity_var.set(sensitivity)
        self._on_silhouette_sensitivity_changed(str(sensitivity))
        return "break"

    def _on_silhouette_sensitivity_scale_mouse(self, event: tk.Event) -> str:
        width = max(self.silhouette_sensitivity_scale.winfo_width(), 1)
        sensitivity = silhouette_sensitivity_from_scale_x(float(event.x), float(width))
        self.silhouette_sensitivity_var.set(sensitivity)
        self._on_silhouette_sensitivity_changed(str(sensitivity))
        return "break"

    def _clear_stale_recognition(self, message: str) -> None:
        self._cancel_pending_recognition()
        if self._recognition_result is not None:
            view_state = self._canvas_view_state()
            self._recognition_result = None
            self._populate_recognition_tree(None)
            self._render_current_image()
            self._restore_canvas_view_state(view_state)
        self.tool_status_var.set(message)

    def _refresh_parameter_visibility(self) -> None:
        if not hasattr(self, "geometry_sensitivity_label"):
            return
        selected_tool = self.shape_tool_var.get() if hasattr(self, "shape_tool_var") else ""
        geometry_visible = selected_tool in GEOMETRY_ROI_KINDS or any(
            roi.kind in GEOMETRY_ROI_KINDS for roi in self._rois
        )
        has_rectangle_roi = any(roi.kind == "rectangle" for roi in self._rois)
        has_silhouette_roi = any(roi.kind == "silhouette" for roi in self._rois)

        self._set_parameter_row_visible(
            (self.geometry_recognizer_label, self.recognizer_combobox),
            geometry_visible,
        )
        self._set_parameter_row_visible(
            (self.geometry_sensitivity_label, self.geometry_sensitivity_frame),
            geometry_visible and self.selected_recognizer_name() in HOUGH_GEOMETRY_RECOGNIZER_NAMES,
        )
        self._set_parameter_row_visible(
            (self.bright_rectangle_sensitivity_label, self.bright_rectangle_sensitivity_frame),
            selected_tool == "rectangle" or has_rectangle_roi,
        )
        silhouette_visible = selected_tool == "silhouette" or has_silhouette_roi
        self._set_parameter_row_visible(
            (self.silhouette_recognizer_label, self.silhouette_recognizer_combobox),
            silhouette_visible,
        )
        self._set_parameter_row_visible(
            (self.silhouette_sensitivity_label, self.silhouette_sensitivity_frame),
            silhouette_visible,
        )

    @staticmethod
    def _set_parameter_row_visible(widgets: tuple[tk.Widget, ...], visible: bool) -> None:
        for widget in widgets:
            if visible:
                widget.grid()
            else:
                widget.grid_remove()

    def _schedule_recognition_rerun(self) -> None:
        self._cancel_pending_recognition()
        self._pending_recognition_after_id = self.after(
            RECOGNITION_RERUN_DELAY_MS,
            self._run_scheduled_recognition,
        )

    def _run_scheduled_recognition(self) -> None:
        self._pending_recognition_after_id = None
        if self._recognition_result is not None:
            self.run_recognition(preserve_view=True)

    def _cancel_pending_recognition(self) -> None:
        if self._pending_recognition_after_id is None:
            return
        try:
            self.after_cancel(self._pending_recognition_after_id)
        except tk.TclError:
            pass
        self._pending_recognition_after_id = None

    def _on_tool_selected(self) -> None:
        self._refresh_parameter_visibility()
        self.tool_status_var.set(f"Selected: {self.shape_tool_var.get()}")

    def _on_canvas_press(self, event: tk.Event) -> None:
        canvas_x = self.image_canvas.canvasx(event.x)
        canvas_y = self.image_canvas.canvasy(event.y)
        image_x, image_y = self._canvas_to_image_point(canvas_x, canvas_y)
        tool = self.shape_tool_var.get()
        if tool in {"edges", "rectangle", "circle", "semicircle", "silhouette"} and self._source_photo_image is not None:
            self._roi_drag = {
                "kind": tool,
                "start_x": image_x,
                "start_y": image_y,
                "current_x": image_x,
                "current_y": image_y,
            }
            self._draw_roi_preview()
            return
        self.tool_status_var.set(
            f"Selected: {self.shape_tool_var.get()} | x={int(image_x)}, y={int(image_y)}"
        )

    def _on_canvas_drag(self, event: tk.Event) -> None:
        if self._roi_drag is None:
            return
        canvas_x = self.image_canvas.canvasx(event.x)
        canvas_y = self.image_canvas.canvasy(event.y)
        image_x, image_y = self._canvas_to_image_point(canvas_x, canvas_y)
        self._roi_drag["current_x"] = image_x
        self._roi_drag["current_y"] = image_y
        self._draw_roi_preview()

    def _on_canvas_mouse_wheel(self, event: tk.Event) -> str:
        if self._source_photo_image is None:
            return "break"
        levels = self._view_scale_levels()
        if len(levels) <= 1:
            return "break"
        delta = getattr(event, "delta", 0)
        button_number = getattr(event, "num", None)
        direction = 1 if delta > 0 or button_number == 4 else -1
        current_scale = self._view_scale if self._view_scale is not None else levels[0]
        current_index = min(range(len(levels)), key=lambda index: abs(levels[index] - current_scale))
        next_index = max(0, min(len(levels) - 1, current_index + direction))
        if next_index == current_index:
            return "break"
        canvas_x = self.image_canvas.canvasx(event.x)
        canvas_y = self.image_canvas.canvasy(event.y)
        image_point = self._canvas_to_image_point(canvas_x, canvas_y)
        self._view_scale = levels[next_index]
        self._render_current_image(
            anchor_image_point=image_point,
            anchor_canvas_point=(float(event.x), float(event.y)),
        )
        return "break"

    def _on_canvas_release(self, event: tk.Event) -> None:
        if self._roi_drag is None:
            return
        canvas_x = self.image_canvas.canvasx(event.x)
        canvas_y = self.image_canvas.canvasy(event.y)
        image_x, image_y = self._canvas_to_image_point(canvas_x, canvas_y)
        kind = str(self._roi_drag["kind"])
        start_x = float(self._roi_drag["start_x"])
        start_y = float(self._roi_drag["start_y"])
        if kind == "circle":
            radius = max(abs(image_x - start_x), abs(image_y - start_y))
            roi = VisionROI("circle", start_x - radius, start_y - radius, start_x + radius, start_y + radius)
        elif kind == "semicircle":
            radius = max(abs(image_x - start_x), abs(image_y - start_y))
            orientation = semicircle_orientation(start_x, start_y, image_x, image_y)
            roi = VisionROI(
                "semicircle",
                start_x - radius,
                start_y - radius,
                start_x + radius,
                start_y + radius,
                orientation=orientation,
            )
        elif kind == "silhouette":
            roi = VisionROI("silhouette", start_x, start_y, image_x, image_y)
        elif kind == "edges":
            roi = VisionROI("edges", start_x, start_y, image_x, image_y)
        elif kind == "rectangle":
            roi = VisionROI("rectangle", start_x, start_y, image_x, image_y)
        else:
            roi = VisionROI("box", start_x, start_y, image_x, image_y)
        self._roi_drag = None
        self.image_canvas.delete("roi_preview")
        self._roi_preview_item = None
        self.add_roi(roi)
        self.tool_status_var.set(f"ROIs: {len(self._rois)}")

    def _draw_recognition_overlay(self) -> None:
        result = self._recognition_result
        if result is None:
            return

        drawn = 0
        for line in result.lines[:MAX_OVERLAY_ITEMS]:
            x1, y1 = self._image_to_canvas_point(line.x1, line.y1)
            x2, y2 = self._image_to_canvas_point(line.x2, line.y2)
            self.image_canvas.create_line(
                x1,
                y1,
                x2,
                y2,
                fill="#00d9ff",
                width=2,
                tags=("recognition_overlay", "line"),
            )
            drawn += 1

        for circle in result.circles[:MAX_OVERLAY_ITEMS]:
            x, y = self._image_to_canvas_point(circle.x, circle.y)
            radius = circle.radius / self._display_subsample
            self.image_canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                outline="#7cff4f",
                width=2,
                tags=("recognition_overlay", "circle"),
            )
            drawn += 1

        for rectangle in result.rectangles[:MAX_OVERLAY_ITEMS]:
            self._draw_rectangle_overlay(rectangle)
            drawn += 1

        for semicircle in result.semicircles[:MAX_OVERLAY_ITEMS]:
            self._draw_semicircle_overlay(semicircle)
            drawn += 1

        for silhouette in result.silhouettes[:MAX_OVERLAY_ITEMS]:
            self._draw_silhouette_overlay(silhouette)
            drawn += 1

        for intersection in result.intersections[:MAX_OVERLAY_ITEMS]:
            x, y = self._image_to_canvas_point(intersection.x, intersection.y)
            size = 6
            self.image_canvas.create_line(
                x - size,
                y,
                x + size,
                y,
                fill="#ffd23f",
                width=2,
                tags=("recognition_overlay", "intersection"),
            )
            self.image_canvas.create_line(
                x,
                y - size,
                x,
                y + size,
                fill="#ffd23f",
                width=2,
                tags=("recognition_overlay", "intersection"),
            )
            drawn += 1

        drawn += self._draw_active_recognition_overlay()
        drawn += self._draw_selected_recognition_overlay()
        if drawn:
            self.image_canvas.tag_raise("recognition_overlay")
            self.image_canvas.tag_raise("active_recognition")
            self.image_canvas.tag_raise("selected_recognition")

    def _draw_active_recognition_overlay(self) -> int:
        drawn = 0
        for item_id in sorted(self._active_recognition_item_ids):
            item = self._recognition_tree_items.get(item_id)
            if item is None or item_id in self._selected_recognition_item_ids:
                continue
            drawn += self._draw_recognition_item_highlight(
                item,
                color="#00a8ff",
                width=3,
                tags=("recognition_overlay", "active_recognition"),
            )
        return drawn

    def _draw_selected_recognition_overlay(self) -> int:
        drawn = 0
        selected_items = [
            self._recognition_tree_items[item_id]
            for item_id in self._selected_recognition_item_ids
            if item_id in self._recognition_tree_items
        ]
        for item in selected_items:
            drawn += self._draw_recognition_item_highlight(
                item,
                color="#ff2a6d",
                width=3,
                tags=("recognition_overlay", "selected_recognition"),
                corner_points=item.shape_kind == "rectangle",
            )

        for selected in self._selected_measurements:
            measurement = selected.measurement
            edge = measurement.short_edge
            x1, y1 = self._image_to_canvas_point(edge.start[0], edge.start[1])
            x2, y2 = self._image_to_canvas_point(edge.end[0], edge.end[1])
            midpoint_x, midpoint_y = self._image_to_canvas_point(edge.midpoint[0], edge.midpoint[1])
            circle_x, circle_y = self._image_to_canvas_point(measurement.circle.x, measurement.circle.y)
            self.image_canvas.create_line(
                x1,
                y1,
                x2,
                y2,
                fill="#ff2a6d",
                width=4,
                tags=("recognition_overlay", "selected_recognition", "selected_short_edge"),
            )
            self.image_canvas.create_line(
                circle_x,
                circle_y,
                midpoint_x,
                midpoint_y,
                fill="#ffffff",
                width=2,
                dash=(5, 3),
                tags=("recognition_overlay", "selected_recognition", "measurement_delta"),
            )
            midpoint_size = 4
            self.image_canvas.create_oval(
                midpoint_x - midpoint_size,
                midpoint_y - midpoint_size,
                midpoint_x + midpoint_size,
                midpoint_y + midpoint_size,
                outline="#ff2a6d",
                fill="#ff2a6d",
                width=1,
                tags=("recognition_overlay", "selected_recognition", "selected_short_edge_midpoint"),
            )
            drawn += 3
        return drawn

    def _draw_recognition_item_highlight(
        self,
        item: VisionRecognitionTreeItem,
        *,
        color: str,
        width: int,
        tags: tuple[str, ...],
        corner_points: bool = False,
    ) -> int:
        if item.shape_kind == "line":
            shape = item.shape
            x1, y1 = self._image_to_canvas_point(shape.x1, shape.y1)
            x2, y2 = self._image_to_canvas_point(shape.x2, shape.y2)
            self.image_canvas.create_line(x1, y1, x2, y2, fill=color, width=width, tags=tags)
            return 1
        if item.shape_kind == "rectangle":
            return self._draw_highlight_rectangle(
                item.shape,
                color=color,
                width=width,
                tags=tags,
                corner_points=corner_points,
            )
        if item.shape_kind == "circle":
            return self._draw_highlight_circle(
                VisionCircleReference(
                    source="circle",
                    x=float(item.shape.x),
                    y=float(item.shape.y),
                    radius=float(item.shape.radius),
                    label=item.shape.label,
                    score=float(item.shape.score),
                ),
                color=color,
                width=width,
                tags=tags,
            )
        if item.shape_kind == "semicircle":
            shape = item.shape
            circle = VisionCircleReference(
                source="semicircle",
                x=float(shape.x),
                y=float(shape.y),
                radius=float(shape.radius),
                label=shape.label,
                score=float(shape.score),
            )
            return self._draw_highlight_circle(circle, color=color, width=width, tags=tags)
        if item.shape_kind == "silhouette":
            try:
                return self._draw_highlight_circle(
                    self._circle_reference_from_item(item),
                    color=color,
                    width=width,
                    tags=tags,
                )
            except ValueError:
                shape = item.shape
                rectangle = VisionRectangle(
                    x1=shape.x1,
                    y1=shape.y1,
                    x2=shape.x2,
                    y2=shape.y2,
                    missing_side=None,
                    score=shape.score,
                    label=shape.label,
                )
                return self._draw_highlight_rectangle(
                    rectangle,
                    color=color,
                    width=width,
                    tags=tags,
                    corner_points=corner_points,
                )
        if item.shape_kind == "intersection":
            shape = item.shape
            x, y = self._image_to_canvas_point(shape.x, shape.y)
            size = 8
            self.image_canvas.create_line(x - size, y, x + size, y, fill=color, width=width, tags=tags)
            self.image_canvas.create_line(x, y - size, x, y + size, fill=color, width=width, tags=tags)
            return 2
        return 0

    def _draw_highlight_rectangle(
        self,
        rectangle: VisionRectangle,
        *,
        color: str,
        width: int,
        tags: tuple[str, ...],
        corner_points: bool = False,
    ) -> int:
        corners = rectangle_measurement_corners(rectangle)
        canvas_corners = [self._image_to_canvas_point(x, y) for x, y in corners]
        drawn = 0
        for index, start in enumerate(canvas_corners):
            end = canvas_corners[(index + 1) % len(canvas_corners)]
            self.image_canvas.create_line(
                start[0],
                start[1],
                end[0],
                end[1],
                fill=color,
                width=width,
                tags=tags,
            )
            drawn += 1
        if corner_points:
            marker_radius = 5
            marker_tags = (*tags, "rectangle_corner")
            for x, y in canvas_corners:
                self.image_canvas.create_oval(
                    x - marker_radius,
                    y - marker_radius,
                    x + marker_radius,
                    y + marker_radius,
                    outline="#ffffff",
                    fill=color,
                    width=1,
                    tags=marker_tags,
                )
                drawn += 1
        return drawn

    def _draw_highlight_circle(
        self,
        circle: VisionCircleReference,
        *,
        color: str,
        width: int,
        tags: tuple[str, ...],
    ) -> int:
        x, y = self._image_to_canvas_point(circle.x, circle.y)
        radius = max(5.0, (circle.radius or 5.0) / self._display_subsample)
        self.image_canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            outline=color,
            width=width,
            tags=tags,
        )
        center_size = 6
        self.image_canvas.create_line(
            x - center_size,
            y,
            x + center_size,
            y,
            fill=color,
            width=max(1, width - 1),
            tags=tags,
        )
        self.image_canvas.create_line(
            x,
            y - center_size,
            x,
            y + center_size,
            fill=color,
            width=max(1, width - 1),
            tags=tags,
        )
        return 3

    def _draw_roi_overlay(self) -> None:
        for index, roi in enumerate(self._rois, start=1):
            self._draw_roi_shape(roi, tags=("roi_overlay", f"roi_{index}"), width=2)
        if self._rois:
            self.image_canvas.tag_raise("roi_overlay")

    def _recognition_tree_item_to_dict(
        self,
        item: VisionRecognitionTreeItem,
        *,
        selection_index: int | None = None,
        feature_role: str | None = None,
    ) -> dict[str, Any]:
        shape = item.shape
        if item.shape_kind == "line":
            shape_payload = vision_line_to_dict(shape)
        elif item.shape_kind == "circle":
            shape_payload = vision_circle_to_dict(shape)
        elif item.shape_kind == "rectangle":
            shape_payload = vision_rectangle_to_dict(shape)
        elif item.shape_kind == "semicircle":
            shape_payload = {
                "x": shape.x,
                "y": shape.y,
                "radius": shape.radius,
                "orientation": shape.orientation,
                "score": shape.score,
                "label": shape.label,
            }
        elif item.shape_kind == "silhouette":
            shape_payload = vision_silhouette_to_dict(shape)
        elif item.shape_kind == "intersection":
            shape_payload = {
                "x": shape.x,
                "y": shape.y,
                "score": shape.score,
                "label": shape.label,
            }
        else:
            shape_payload = {}
        payload = {
            "roi_index": item.roi_index,
            "roi": vision_roi_to_dict(self._rois[item.roi_index - 1]),
            "shape_kind": item.shape_kind,
            "source": item.source,
            "feature_role": feature_role or self._feature_role_for_item_id(item.item_id),
            "shape": shape_payload,
        }
        if selection_index is not None:
            payload["selection_index"] = int(selection_index)
        return payload

    def _shape_center(self, shape_kind: str, shape: Any) -> tuple[float, float]:
        if shape_kind == "line":
            return 0.5 * (shape.x1 + shape.x2), 0.5 * (shape.y1 + shape.y2)
        if shape_kind in {"circle", "semicircle", "silhouette", "intersection"}:
            return float(shape.x), float(shape.y)
        if shape_kind == "rectangle":
            corners = rectangle_measurement_corners(shape)
            return (
                sum(x for x, _y in corners) / len(corners),
                sum(y for _x, y in corners) / len(corners),
            )
        raise ValueError(f"unsupported shape kind: {shape_kind}")

    def _compatible_roi_kinds(self, shape_kind: str) -> frozenset[str]:
        if shape_kind in {"line", "intersection"}:
            return frozenset({"box", "edges"})
        if shape_kind == "rectangle":
            return frozenset({"rectangle"})
        if shape_kind == "circle":
            return frozenset({"circle"})
        if shape_kind == "semicircle":
            return frozenset({"semicircle"})
        if shape_kind == "silhouette":
            return frozenset({"silhouette"})
        return frozenset()

    def _roi_indexes_for_shape(self, shape_kind: str, shape: Any) -> tuple[int, ...]:
        center_x, center_y = self._shape_center(shape_kind, shape)
        compatible_kinds = self._compatible_roi_kinds(shape_kind)
        return tuple(
            index
            for index, roi in enumerate(self._rois, start=1)
            if roi.kind in compatible_kinds and point_in_roi(center_x, center_y, roi)
        )

    def _insert_recognition_tree_rows(
        self,
        row: int,
        *,
        shape_kind: str,
        shape: Any,
        type_label: str,
        target: str,
        score: float,
        source: str,
    ) -> int:
        for roi_index in self._roi_indexes_for_shape(shape_kind, shape):
            item_id = f"{shape_kind}_{row}"
            self._recognition_tree_items[item_id] = VisionRecognitionTreeItem(
                item_id=item_id,
                roi_index=roi_index,
                shape_kind=shape_kind,
                shape=shape,
                source=source,
            )
            role = self._feature_role_for_item_id(item_id)
            self.recognition_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    "",
                    f"R{roi_index}",
                    type_label,
                    feature_role_display_label(role, self._capture_id),
                    target,
                    f"{score:.2f}",
                ),
            )
            row += 1
        return row

    def _recognition_tree_selected_item_ids(self) -> tuple[str, ...]:
        selected = tuple(
            item_id for item_id in self.recognition_tree.selection() if item_id in self._recognition_tree_items
        )
        if selected:
            focused = self.recognition_tree.focus()
            if focused in selected:
                return (focused, *(item_id for item_id in selected if item_id != focused))
            return selected
        item_id = self.recognition_tree.focus()
        if item_id in self._recognition_tree_items:
            return (item_id,)
        return ()

    def _ordered_selected_recognition_item_ids(self) -> tuple[str, ...]:
        ordered = [
            item_id
            for item_id in self._selected_recognition_item_order
            if item_id in self._selected_recognition_item_ids and item_id in self._recognition_tree_items
        ]
        missing = [
            item_id
            for item_id in self.recognition_tree.get_children()
            if item_id in self._selected_recognition_item_ids and item_id not in ordered
        ]
        return tuple(ordered + missing)

    def _default_feature_role_for_item_id(self, item_id: str) -> str:
        item = self._recognition_tree_items[item_id]
        if self._capture_id in CAPTURE_FEATURE_ROLE_OPTIONS:
            expected_role = expected_role_for_capture(self._capture_id, item.shape_kind)
            if expected_role in self._feature_role_keys:
                return expected_role
            return self._feature_role_keys[0]
        return default_feature_role_for_selection(item.shape_kind, item.source)

    def _feature_role_for_item_id(self, item_id: str) -> str:
        return self._recognition_item_role_overrides.get(item_id) or self._default_feature_role_for_item_id(item_id)

    def _on_recognition_tree_selected(self, _event: tk.Event | None = None) -> None:
        self._active_recognition_item_ids = set(self._recognition_tree_selected_item_ids())
        selected_ids = self._recognition_tree_selected_item_ids()
        if selected_ids and hasattr(self, "feature_role_var"):
            self.feature_role_var.set(
                feature_role_display_label(
                    self._feature_role_for_item_id(selected_ids[0]),
                    self._capture_id,
                )
            )
        self._render_current_image()

    def _use_selected_recognition_row(self, _event: tk.Event | None = None) -> str:
        item_ids = self._recognition_tree_selected_item_ids()
        if not item_ids:
            return "break"
        for item_id in item_ids:
            self._selected_recognition_item_ids.add(item_id)
            if item_id not in self._selected_recognition_item_order:
                self._selected_recognition_item_order.append(item_id)
        self._active_recognition_item_ids = set(item_ids)
        self._update_recognition_tree_selection_marks()
        self._update_selected_measurement()
        self._render_current_image()
        return "break"

    def _set_selected_recognition_role(self) -> None:
        selected_label = self.feature_role_var.get().strip()
        role = feature_role_from_display_label(selected_label, self._capture_id)
        if role is None or role not in self._feature_role_keys:
            context = CAPTURE_REVIEW_CONTEXT_LABELS.get(self._capture_id, "this review")
            self.tool_status_var.set(f"Role is not valid for {context}")
            return
        item_ids = self._recognition_tree_selected_item_ids()
        if not item_ids:
            self.tool_status_var.set("Select a detected shape before setting a role")
            return
        for item_id in item_ids:
            if item_id in self._recognition_tree_items:
                default_role = self._default_feature_role_for_item_id(item_id)
                if role == default_role:
                    self._recognition_item_role_overrides.pop(item_id, None)
                else:
                    self._recognition_item_role_overrides[item_id] = role
        self._update_recognition_tree_selection_marks()
        self._update_selected_measurement()
        self._render_current_image()
        self.tool_status_var.set(
            f"Role: {feature_role_display_label(role, self._capture_id)}"
        )

    def _clear_selected_recognition_roi(self) -> None:
        item_ids = set(self._recognition_tree_selected_item_ids())
        if item_ids:
            self._selected_recognition_item_ids.difference_update(item_ids)
            self._selected_recognition_item_order = [
                item_id for item_id in self._selected_recognition_item_order if item_id not in item_ids
            ]
            self.tool_status_var.set("Deselected selected detection")
        else:
            self._selected_recognition_item_ids.clear()
            self._selected_recognition_item_order.clear()
            self.tool_status_var.set("Deselected all detections")
        self._update_recognition_tree_selection_marks()
        self._update_selected_measurement()
        self._render_current_image()

    def _update_recognition_tree_selection_marks(self) -> None:
        for item_id in self.recognition_tree.get_children():
            values = list(self.recognition_tree.item(item_id, "values"))
            if len(values) < 6:
                continue
            values[0] = "Yes" if item_id in self._selected_recognition_item_ids else ""
            values[3] = feature_role_display_label(
                self._feature_role_for_item_id(item_id),
                self._capture_id,
            )
            self.recognition_tree.item(item_id, values=tuple(values))

    def _selected_measurement_items(
        self,
    ) -> tuple[VisionRecognitionTreeItem | None, tuple[VisionRecognitionTreeItem, ...]]:
        selected_items = [
            self._recognition_tree_items[item_id]
            for item_id in self._ordered_selected_recognition_item_ids()
            if item_id in self._recognition_tree_items
        ]
        rectangles = [item for item in selected_items if item.shape_kind == "rectangle"]
        circle_like = [
            item
            for item in selected_items
            if item.shape_kind == "circle" or item.source == "silhouette_circle"
        ]
        if len(rectangles) != 1:
            return None, tuple(circle_like)
        return rectangles[0], tuple(circle_like)

    def _circle_reference_from_item(self, item: VisionRecognitionTreeItem) -> VisionCircleReference:
        shape = item.shape
        if item.shape_kind == "circle":
            return VisionCircleReference(
                source="circle",
                x=float(shape.x),
                y=float(shape.y),
                radius=float(shape.radius),
                label=shape.label,
                score=float(shape.score),
            )
        if (
            item.shape_kind == "silhouette"
            and shape.circle_x is not None
            and shape.circle_y is not None
            and shape.circle_radius is not None
        ):
            return VisionCircleReference(
                source="silhouette_circle",
                x=float(shape.circle_x),
                y=float(shape.circle_y),
                radius=float(shape.circle_radius),
                label=shape.label,
                score=float(shape.score),
            )
        raise ValueError("Selected circle/blob has no fitted circle center.")

    def _update_selected_measurement(self) -> None:
        self._selected_measurement = None
        self._selected_measurements = ()
        rectangle_selection, circle_selections = self._selected_measurement_items()
        if rectangle_selection is None or not circle_selections:
            self._update_measurement_display()
            return
        selected_measurements: list[VisionSelectedMeasurement] = []
        try:
            short_edge_length_um = self.selected_short_edge_length_um()
            for circle_selection in circle_selections:
                circle = self._circle_reference_from_item(circle_selection)
                selected_measurements.append(
                    VisionSelectedMeasurement(
                        rectangle_selection=rectangle_selection,
                        circle_selection=circle_selection,
                        measurement=rectangle_circle_measurement(
                            rectangle_selection.shape,
                            circle,
                            short_edge_length_um,
                        ),
                    )
                )
            self._selected_measurements = tuple(selected_measurements)
            self._selected_measurement = self._selected_measurements[0].measurement if self._selected_measurements else None
            self._update_measurement_display()
        except ValueError as exc:
            self._update_measurement_display(str(exc))

    def _on_short_edge_um_entry_commit(self, _event: tk.Event | None = None) -> str:
        self.short_edge_um_var.set(f"{FIXED_MEASUREMENT_SHORT_EDGE_LENGTH_UM:g}")
        self._update_selected_measurement()
        self._render_current_image()
        return "break"

    def _update_measurement_display(self, error: str | None = None) -> None:
        if not hasattr(self, "measurement_var"):
            return
        if error:
            self.measurement_var.set(f"Measurement: {error}")
            return
        if self._selected_measurement is None:
            selected_items = [
                self._recognition_tree_items[item_id]
                for item_id in self._selected_recognition_item_ids
                if item_id in self._recognition_tree_items
            ]
            rectangle_count = sum(1 for item in selected_items if item.shape_kind == "rectangle")
            circle_count = sum(
                1 for item in selected_items if item.shape_kind == "circle" or item.source == "silhouette_circle"
            )
            if rectangle_count != 1:
                self.measurement_var.set("Measurement: use exactly one rectangle/edge ROI")
            elif circle_count < 1:
                self.measurement_var.set("Measurement: use at least one circle/blob ROI")
            else:
                self.measurement_var.set("Measurement: select a rectangle and circle/blob")
            return
        measurement_count = len(self._selected_measurements)
        measurement = self._selected_measurements[0].measurement
        prefix = "Measurement" if measurement_count == 1 else f"Measurements: {measurement_count} targets"
        self.measurement_var.set(
            f"{prefix}: "
            f"edge {measurement.short_edge.length_px:.2f} px, "
            f"{measurement.um_per_pixel:.6g} um/px | "
            f"dx {measurement.dx_px:.2f} px ({measurement.dx_um:.2f} um), "
            f"dy {measurement.dy_px:.2f} px ({measurement.dy_um:.2f} um), "
            f"dist {measurement.distance_px:.2f} px ({measurement.distance_um:.2f} um)"
        )

    def _draw_roi_preview(self) -> None:
        if self._roi_drag is None:
            return
        if self._roi_preview_item is not None:
            self.image_canvas.delete("roi_preview")
            self._roi_preview_item = None
        kind = str(self._roi_drag["kind"])
        start_x = float(self._roi_drag["start_x"])
        start_y = float(self._roi_drag["start_y"])
        current_x = float(self._roi_drag["current_x"])
        current_y = float(self._roi_drag["current_y"])
        if kind == "circle":
            radius = max(abs(current_x - start_x), abs(current_y - start_y))
            roi = VisionROI("circle", start_x - radius, start_y - radius, start_x + radius, start_y + radius)
        elif kind == "semicircle":
            radius = max(abs(current_x - start_x), abs(current_y - start_y))
            orientation = semicircle_orientation(start_x, start_y, current_x, current_y)
            roi = VisionROI(
                "semicircle",
                start_x - radius,
                start_y - radius,
                start_x + radius,
                start_y + radius,
                orientation=orientation,
            )
        elif kind == "silhouette":
            roi = VisionROI("silhouette", start_x, start_y, current_x, current_y)
        elif kind == "edges":
            roi = VisionROI("edges", start_x, start_y, current_x, current_y)
        elif kind == "rectangle":
            roi = VisionROI("rectangle", start_x, start_y, current_x, current_y)
        else:
            roi = VisionROI("box", start_x, start_y, current_x, current_y)
        self._roi_preview_item = self._draw_roi_shape(
            roi,
            tags=("roi_overlay", "roi_preview"),
            width=2,
            dash=(4, 3),
        )

    def _draw_roi_shape(
        self,
        roi: VisionROI,
        *,
        tags: tuple[str, ...],
        width: int,
        dash: tuple[int, int] | None = None,
    ) -> int:
        normalized = roi.normalized
        x1, y1 = self._image_to_canvas_point(normalized.x1, normalized.y1)
        x2, y2 = self._image_to_canvas_point(normalized.x2, normalized.y2)
        if normalized.kind == "circle":
            color = "#ff4fd8"
        elif normalized.kind == "rectangle":
            color = "#ffd23f"
        elif normalized.kind == "semicircle":
            color = "#9b5cff"
        elif normalized.kind == "silhouette":
            color = "#4dff73"
        else:
            color = "#ff9f1c"
        if normalized.kind == "circle":
            return self.image_canvas.create_oval(
                x1,
                y1,
                x2,
                y2,
                outline=color,
                width=width,
                dash=dash,
                tags=tags,
            )
        if normalized.kind == "semicircle":
            boundary_dash = (2, 3) if dash is None else dash
            self.image_canvas.create_oval(
                x1,
                y1,
                x2,
                y2,
                outline=color,
                width=max(1, width - 1),
                dash=boundary_dash,
                tags=tags,
            )
            return self.image_canvas.create_arc(
                x1,
                y1,
                x2,
                y2,
                start=semicircle_arc_start(normalized.orientation),
                extent=180,
                style=tk.ARC,
                outline=color,
                width=width,
                tags=tags,
            )
        return self.image_canvas.create_rectangle(
            x1,
            y1,
            x2,
            y2,
            outline=color,
            width=width,
            dash=dash,
            tags=tags,
        )

    def _populate_recognition_tree(self, result: VisionRecognitionResult | None) -> None:
        if not hasattr(self, "recognition_tree"):
            return
        for item_id in self.recognition_tree.get_children():
            self.recognition_tree.delete(item_id)
        self._recognition_tree_items.clear()
        self._recognition_item_role_overrides.clear()
        self._selected_recognition_item_ids.clear()
        self._selected_recognition_item_order.clear()
        self._active_recognition_item_ids.clear()
        self._selected_measurement = None
        self._selected_measurements = ()
        self._update_measurement_display()
        if result is None:
            return
        row = 0
        for line in result.lines[:30]:
            row = self._insert_recognition_tree_rows(
                row,
                shape_kind="line",
                shape=line,
                type_label="Line",
                target=f"{line.label} ({line.x1:.0f},{line.y1:.0f})",
                score=line.score,
                source="line",
            )
        for circle in result.circles[:20]:
            row = self._insert_recognition_tree_rows(
                row,
                shape_kind="circle",
                shape=circle,
                type_label="Circle",
                target=f"({circle.x:.0f},{circle.y:.0f}) r={circle.radius:.0f}",
                score=circle.score,
                source="circle",
            )
        for rectangle in result.rectangles[:20]:
            missing = rectangle.missing_side or "none"
            row = self._insert_recognition_tree_rows(
                row,
                shape_kind="rectangle",
                shape=rectangle,
                type_label="Rect",
                target=(
                    f"{rectangle.label} ({rectangle.x1:.0f},{rectangle.y1:.0f})-"
                    f"({rectangle.x2:.0f},{rectangle.y2:.0f}) missing={missing}"
                ),
                score=rectangle.score,
                source="rectangle",
            )
        for semicircle in result.semicircles[:20]:
            row = self._insert_recognition_tree_rows(
                row,
                shape_kind="semicircle",
                shape=semicircle,
                type_label="Semi",
                target=f"{semicircle.orientation} ({semicircle.x:.0f},{semicircle.y:.0f}) r={semicircle.radius:.0f}",
                score=semicircle.score,
                source="semicircle",
            )
        for silhouette in result.silhouettes[:20]:
            silhouette_detail = f"({silhouette.x:.0f},{silhouette.y:.0f}) area={silhouette.area:.0f}"
            source = "silhouette"
            if (
                silhouette.circle_x is not None
                and silhouette.circle_y is not None
                and silhouette.circle_radius is not None
            ):
                source = "silhouette_circle"
                silhouette_detail += (
                    f" circle=({silhouette.circle_x:.0f},{silhouette.circle_y:.0f})"
                    f" r={silhouette.circle_radius:.0f}"
                )
            row = self._insert_recognition_tree_rows(
                row,
                shape_kind="silhouette",
                shape=silhouette,
                type_label="Blob",
                target=silhouette_detail,
                score=silhouette.score,
                source=source,
            )
        for intersection in result.intersections[:30]:
            row = self._insert_recognition_tree_rows(
                row,
                shape_kind="intersection",
                shape=intersection,
                type_label="Cross",
                target=f"({intersection.x:.0f},{intersection.y:.0f})",
                score=intersection.score,
                source="intersection",
            )

    def _draw_rectangle_overlay(self, rectangle: VisionRectangle) -> None:
        color = BRIGHT_RECTANGLE_OVERLAY_COLOR if rectangle.label == "bright silhouette" else EDGE_RECTANGLE_OVERLAY_COLOR
        source_tag = "bright_rectangle" if rectangle.label == "bright silhouette" else "edge_rectangle"
        if len(rectangle.corners) == 4:
            canvas_corners = [self._image_to_canvas_point(x, y) for x, y in rectangle.corners]
            sides = {
                "top": (*canvas_corners[0], *canvas_corners[1]),
                "right": (*canvas_corners[1], *canvas_corners[2]),
                "bottom": (*canvas_corners[2], *canvas_corners[3]),
                "left": (*canvas_corners[3], *canvas_corners[0]),
            }
        else:
            x1, y1 = self._image_to_canvas_point(rectangle.x1, rectangle.y1)
            x2, y2 = self._image_to_canvas_point(rectangle.x2, rectangle.y2)
            sides = {
                "top": (x1, y1, x2, y1),
                "right": (x2, y1, x2, y2),
                "bottom": (x1, y2, x2, y2),
                "left": (x1, y1, x1, y2),
            }
        for side, coordinates in sides.items():
            options: dict[str, Any] = {
                "fill": color,
                "width": 2,
                "tags": ("recognition_overlay", "rectangle", source_tag),
            }
            if rectangle.missing_side == side:
                options["dash"] = (4, 4)
            self.image_canvas.create_line(*coordinates, **options)

    def _draw_semicircle_overlay(self, semicircle: VisionSemicircle) -> None:
        x, y = self._image_to_canvas_point(semicircle.x, semicircle.y)
        radius = semicircle.radius / self._display_subsample
        self.image_canvas.create_oval(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            outline="#b88cff",
            width=2,
            dash=(2, 3),
            tags=("recognition_overlay", "semicircle_circle"),
        )
        self.image_canvas.create_arc(
            x - radius,
            y - radius,
            x + radius,
            y + radius,
            start=semicircle_arc_start(semicircle.orientation),
            extent=180,
            style=tk.ARC,
            outline="#d6a5ff",
            width=3,
            tags=("recognition_overlay", "semicircle"),
        )

    def _draw_silhouette_overlay(self, silhouette: VisionSilhouette) -> None:
        for x1_image, y1_image, x2_image, y2_image in silhouette.circle_contour_segments:
            x1, y1 = self._image_to_canvas_point(x1_image, y1_image)
            x2, y2 = self._image_to_canvas_point(x2_image, y2_image)
            self.image_canvas.create_line(
                x1,
                y1,
                x2,
                y2,
                fill="#ffe866",
                width=2,
                tags=("recognition_overlay", "silhouette", "silhouette_circle_contour"),
            )

        if (
            silhouette.circle_x is not None
            and silhouette.circle_y is not None
            and silhouette.circle_radius is not None
            and silhouette.circle_radius > 0.0
        ):
            circle_x, circle_y = self._image_to_canvas_point(silhouette.circle_x, silhouette.circle_y)
            circle_radius = silhouette.circle_radius / self._display_subsample
            self.image_canvas.create_oval(
                circle_x - circle_radius,
                circle_y - circle_radius,
                circle_x + circle_radius,
                circle_y + circle_radius,
                outline="#4dff73",
                width=1,
                tags=("recognition_overlay", "silhouette", "silhouette_circle"),
            )
            circle_center_size = 5
            self.image_canvas.create_line(
                circle_x - circle_center_size,
                circle_y,
                circle_x + circle_center_size,
                circle_y,
                fill="#4dff73",
                width=1,
                tags=("recognition_overlay", "silhouette", "silhouette_circle_center"),
            )
            self.image_canvas.create_line(
                circle_x,
                circle_y - circle_center_size,
                circle_x,
                circle_y + circle_center_size,
                fill="#4dff73",
                width=1,
                tags=("recognition_overlay", "silhouette", "silhouette_circle_center"),
            )

    def _image_to_canvas_point(self, x: float, y: float) -> tuple[float, float]:
        origin_x, origin_y = self._display_origin
        return origin_x + x / self._display_subsample, origin_y + y / self._display_subsample

    def _canvas_to_image_point(self, x: float, y: float) -> tuple[float, float]:
        origin_x, origin_y = self._display_origin
        image_x = max(0.0, (x - origin_x) * self._display_subsample)
        image_y = max(0.0, (y - origin_y) * self._display_subsample)
        if self._source_photo_image is not None:
            image_x = min(image_x, float(max(self._source_photo_image.width() - 1, 0)))
            image_y = min(image_y, float(max(self._source_photo_image.height() - 1, 0)))
        return image_x, image_y

    def _update_roi_count(self) -> None:
        if hasattr(self, "roi_count_var"):
            self.roi_count_var.set(f"ROIs: {len(self._rois)}")

    def destroy(self) -> None:
        self._cancel_pending_recognition()
        super().destroy()


def vision_roi_to_dict(roi: VisionROI) -> dict[str, Any]:
    normalized = roi.normalized
    return {
        "kind": normalized.kind,
        "x1": normalized.x1,
        "y1": normalized.y1,
        "x2": normalized.x2,
        "y2": normalized.y2,
        "orientation": normalized.orientation,
    }


def vision_line_to_dict(line: VisionLine) -> dict[str, Any]:
    return {
        "x1": line.x1,
        "y1": line.y1,
        "x2": line.x2,
        "y2": line.y2,
        "score": line.score,
        "label": line.label,
    }


def vision_circle_to_dict(circle: VisionCircle) -> dict[str, Any]:
    return {
        "x": circle.x,
        "y": circle.y,
        "radius": circle.radius,
        "score": circle.score,
        "label": circle.label,
    }


def vision_rectangle_to_dict(rectangle: VisionRectangle) -> dict[str, Any]:
    return {
        "x1": rectangle.x1,
        "y1": rectangle.y1,
        "x2": rectangle.x2,
        "y2": rectangle.y2,
        "missing_side": rectangle.missing_side,
        "score": rectangle.score,
        "label": rectangle.label,
        "corners": [{"x": x, "y": y} for x, y in rectangle.corners],
    }


def vision_silhouette_to_dict(silhouette: VisionSilhouette) -> dict[str, Any]:
    return {
        "x": silhouette.x,
        "y": silhouette.y,
        "x1": silhouette.x1,
        "y1": silhouette.y1,
        "x2": silhouette.x2,
        "y2": silhouette.y2,
        "area": silhouette.area,
        "score": silhouette.score,
        "label": silhouette.label,
        "circle_x": silhouette.circle_x,
        "circle_y": silhouette.circle_y,
        "circle_radius": silhouette.circle_radius,
    }


def vision_result_to_dict(result: VisionRecognitionResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "algorithm_name": result.algorithm_name,
        "display_name": result.display_name,
        "message": result.message,
        "lines": [vision_line_to_dict(line) for line in result.lines],
        "intersections": [
            {"x": intersection.x, "y": intersection.y, "score": intersection.score, "label": intersection.label}
            for intersection in result.intersections
        ],
        "circles": [vision_circle_to_dict(circle) for circle in result.circles],
        "rectangles": [vision_rectangle_to_dict(rectangle) for rectangle in result.rectangles],
        "semicircles": [
            {
                "x": semicircle.x,
                "y": semicircle.y,
                "radius": semicircle.radius,
                "orientation": semicircle.orientation,
                "score": semicircle.score,
                "label": semicircle.label,
            }
            for semicircle in result.semicircles
        ],
        "silhouettes": [vision_silhouette_to_dict(silhouette) for silhouette in result.silhouettes],
    }


def _write_json(path: str | Path, payload: dict[str, Any]) -> str:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(output_path)


def _v5_sequence_memory_api() -> tuple[Callable[[dict[str, Any]], dict[str, Any]], Callable[[dict[str, Any]], dict[str, Any]]]:
    try:
        from migrations.migration_v5.python_vision_geometry.sequence_memory_workflow import (
            initialize_sequence_memory,
            run_sequence_memory_workflow,
        )
    except ImportError as first_exc:
        try:
            from python_vision_geometry.sequence_memory_workflow import (
                initialize_sequence_memory,
                run_sequence_memory_workflow,
            )
        except ImportError as second_exc:
            raise ImportError(
                "Could not import the v5 sequence-memory workflow from the repo migrations package "
                "or from python_vision_geometry."
            ) from second_exc
        except Exception:
            raise
    except Exception:
        raise
    return initialize_sequence_memory, run_sequence_memory_workflow


def vision_session_payload(
    *,
    image_path: str | Path,
    rois: Sequence[VisionROI],
    result: VisionRecognitionResult | None,
    status: str,
    ok: bool = True,
    selected_recognition: dict[str, Any] | None = None,
    measurement: dict[str, Any] | None = None,
    measurements: Sequence[dict[str, Any]] | None = None,
    relative_measurement: dict[str, Any] | None = None,
    yase_display: str | None = None,
    capture_id: str | None = None,
    mirror_roi: dict[str, Any] | None = None,
    image_dimensions_px: dict[str, int] | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    roi_payload = [vision_roi_to_dict(roi) for roi in rois]
    measurement_payloads = list(measurements or ())
    if relative_measurement is None:
        relative_measurement = relative_measurement_payload_from_measurements(measurement_payloads)
    display_status = yase_display or format_relative_measurement_for_yase(relative_measurement, status)
    payload = {
        "schema_version": 3,
        "ok": ok,
        "action": action or ("vision_lab_saved" if roi_payload else "vision_lab_closed_without_rois"),
        "status": display_status,
        "session_status": status,
        "yase_display": display_status,
        "image_path": str(image_path),
        "roi_count": len(roi_payload),
        "ready_for_recognition": bool(roi_payload),
        "rois": roi_payload,
        "recognition_result": vision_result_to_dict(result),
        "selected_recognition": selected_recognition or {},
        "measurement": measurement,
        "measurements": measurement_payloads,
        "relative_measurement": relative_measurement,
    }
    if capture_id:
        payload["capture_id"] = capture_id
    if mirror_roi:
        payload["mirror_roi"] = deepcopy(mirror_roi)
        payload["mirror_view"] = True
        payload["mirror_flip_y"] = True
    if image_dimensions_px:
        payload["image_dimensions_px"] = deepcopy(image_dimensions_px)
    return payload


def run_vision_recognition_lab_session(
    image_path: str | Path,
    *,
    roi_output_path: str | Path | None = None,
    result_output_path: str | Path | None = None,
    initial_session: dict[str, Any] | None = None,
    capture_id: str | None = None,
) -> dict[str, Any]:
    source_path = Path(image_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"captured image does not exist: {source_path}")

    root = tk.Tk()
    root.withdraw()
    finished = False

    def finish() -> None:
        nonlocal finished
        finished = True
        root.quit()

    lab = VisionRecognitionLab(
        root,
        captured_image_path=source_path,
        session_done_callback=finish,
        show_session_done_button=True,
        initial_session=initial_session,
        capture_id=capture_id,
    )
    lab.protocol("WM_DELETE_WINDOW", lab._cancel_session)  # pylint: disable=protected-access
    try:
        root.mainloop()
        if lab._session_cancelled or not lab._session_saved:  # pylint: disable=protected-access
            payload = vision_session_payload(
                image_path=source_path,
                rois=(),
                result=None,
                status="Vision review cancelled; no memory update is allowed",
                ok=False,
                capture_id=capture_id,
                action="vision_lab_cancelled",
            )
            if result_output_path is not None:
                payload["result_output_path"] = str(Path(result_output_path))
                _write_json(result_output_path, payload)
            return payload
        status = "Vision recognition lab closed"
        if not finished:
            status = "Vision recognition lab ended"
        payload = vision_session_payload(
            image_path=source_path,
            rois=lab.current_rois(),
            result=lab._recognition_result,  # pylint: disable=protected-access
            status=status,
            selected_recognition=lab.selected_recognition_payload(),
            measurement=lab.selected_measurement_payload(),
            measurements=lab.selected_measurements_payload(),
            relative_measurement=lab.selected_relative_measurement_payload(),
            yase_display=lab.selected_yase_display_status(status),
            capture_id=capture_id,
            mirror_roi=lab.reviewed_mirror_roi_payload(),
            image_dimensions_px=(
                {
                    "image_width_px": int(lab._source_gray_image.shape[1]),  # pylint: disable=protected-access
                    "image_height_px": int(lab._source_gray_image.shape[0]),  # pylint: disable=protected-access
                }
                if lab._source_gray_image is not None  # pylint: disable=protected-access
                else None
            ),
        )
        if roi_output_path is not None:
            roi_payload = {
                "schema_version": payload["schema_version"],
                "image_path": payload["image_path"],
                "roi_count": payload["roi_count"],
                "rois": payload["rois"],
            }
            payload["roi_output_path"] = _write_json(roi_output_path, roi_payload)
        if result_output_path is not None:
            payload["result_output_path"] = str(Path(result_output_path))
            _write_json(result_output_path, payload)
        return payload
    finally:
        try:
            lab.destroy()
        except tk.TclError:
            pass
        try:
            root.destroy()
        except tk.TclError:
            pass


def run_vision_recognition_lab_from_params(params_in: dict[str, Any]) -> dict[str, Any]:
    image_path = params_in.get("image_path")
    if not image_path:
        raise ValueError("image_path is required")
    if params_in.get("dry_run"):
        gray_image = read_grayscale_image(image_path)
        payload = vision_session_payload(
            image_path=image_path,
            rois=(),
            result=None,
            status=f"dry run loaded {gray_image.shape[1]} x {gray_image.shape[0]} image",
        )
        result_output_path = params_in.get("result_output_path")
        if result_output_path:
            payload["result_output_path"] = str(Path(result_output_path))
            _write_json(result_output_path, payload)
        return payload
    return run_vision_recognition_lab_session(
        image_path,
        roi_output_path=params_in.get("roi_output_path"),
        result_output_path=params_in.get("result_output_path"),
        initial_session=params_in.get("initial_session"),
        capture_id=params_in.get("capture_id"),
    )


class VisionRecognitionLabStep(TMPythonStatementJ):
    """TMPython entrypoint that opens the existing ROI UI on a freshly captured image."""

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return run_vision_recognition_lab_from_params(params_in)
        except Exception as exc:  # fail closed for YASE callers
            return {
                "schema_version": 3,
                "ok": False,
                "action": "abort",
                "status": f"VisionRecognitionLabStep failed: {exc}",
                "session_status": f"VisionRecognitionLabStep failed: {exc}",
                "yase_display": f"VisionRecognitionLabStep failed: {exc}",
                "relative_measurement": None,
                "traceback": traceback.format_exc(),
            }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Open the vision recognition lab.")
    parser.add_argument("--image", help="Open the lab on this captured image instead of the standard-position library.")
    parser.add_argument("--roi-output", help="Write drawn ROIs to this JSON file when the lab closes.")
    parser.add_argument("--result-output", help="Write the full lab session result to this JSON file when the lab closes.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> dict[str, Any] | None:
    args = _parse_args(argv)
    if args.image:
        payload = run_vision_recognition_lab_session(
            args.image,
            roi_output_path=args.roi_output,
            result_output_path=args.result_output,
        )
        print(json.dumps(payload, sort_keys=True))
        return payload
    root = tk.Tk()
    root.withdraw()
    lab = VisionRecognitionLab(root)
    lab.protocol("WM_DELETE_WINDOW", root.destroy)
    root.mainloop()
    return None


if __name__ == "__main__":
    main()
