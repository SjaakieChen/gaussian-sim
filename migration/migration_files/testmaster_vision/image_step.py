"""Read a saved camera image and return simple recognition measurements."""

from __future__ import annotations

from pathlib import Path
from typing import Any

try:
    from testmaster_alignment.contracts import (
        JsonDict,
        abort_response,
        algorithm_block,
        as_dict,
        done_response,
        finite_float,
        finite_positive_float,
        require_schema,
    )
except ImportError:
    from contracts import (  # type: ignore[no-redef]
        JsonDict,
        abort_response,
        algorithm_block,
        as_dict,
        done_response,
        finite_float,
        finite_positive_float,
        require_schema,
    )

try:
    from tmpython.statement import TMPythonStatementJ
except ImportError:

    class TMPythonStatementJ:  # type: ignore[no-redef]
        """Fallback base class used outside the TestMaster machine."""

        pass


class ImageRecognitionStep(TMPythonStatementJ):
    """Recognize a bright or dark feature in a saved image.

    This first machine-facing vision step is deliberately read-only. It returns
    feature data in JSON and never requests a stage move.
    """

    def run(self, params_in: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._run(params_in)
        except Exception as exc:
            return abort_response(f"ImageRecognitionStep failed: {exc}")

    def _run(self, params_in: JsonDict) -> JsonDict:
        require_schema(params_in)
        image_path = _image_path(params_in)
        gray = _load_grayscale(image_path)
        feature = _recognize_feature(gray, params_in)

        height_px, width_px = gray.shape
        vision_result: JsonDict = {
            "source": "python_image_recognition",
            "image_path": str(image_path),
            "width_px": int(width_px),
            "height_px": int(height_px),
            "feature": feature,
            "confidence": feature["confidence"],
        }
        result = done_response(
            "image recognition complete",
            {
                "algorithm": "image_recognition",
                "vision": vision_result,
            },
        )
        result["vision"] = vision_result
        return result


def _image_path(params_in: JsonDict) -> Path:
    vision = as_dict(params_in.get("vision"))
    raw_path = vision.get("image_path") or params_in.get("image_path")
    if not raw_path:
        raise ValueError("vision.image_path is required")

    image_path = Path(str(raw_path))
    if not image_path.exists():
        raise ValueError(f"image file does not exist: {image_path}")
    if not image_path.is_file():
        raise ValueError(f"image path is not a file: {image_path}")
    return image_path


def _load_grayscale(image_path: Path):
    try:
        import cv2  # type: ignore[import-not-found]

        gray = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if gray is not None:
            return gray
    except ImportError:
        pass

    try:
        import numpy as np
        from PIL import Image

        with Image.open(image_path) as image:
            return np.asarray(image.convert("L"))
    except Exception as exc:
        raise ValueError(f"could not read image file: {image_path}") from exc


def _recognize_feature(gray, params_in: JsonDict) -> JsonDict:
    import numpy as np

    algorithm = algorithm_block(params_in)
    polarity = str(algorithm.get("polarity", "bright")).strip().lower()
    if polarity not in {"bright", "dark"}:
        raise ValueError("algorithm.polarity must be 'bright' or 'dark'")

    threshold = _threshold(gray, params_in, polarity)
    mask = gray <= threshold if polarity == "dark" else gray >= threshold

    min_area_px = int(finite_positive_float(algorithm.get("min_area_px"), "algorithm.min_area_px", 1.0))
    area_px = int(mask.sum())
    if area_px < min_area_px:
        raise ValueError(f"recognized feature area {area_px} px is below min_area_px {min_area_px}")

    ys, xs = np.nonzero(mask)
    x_min = int(xs.min())
    x_max = int(xs.max())
    y_min = int(ys.min())
    y_max = int(ys.max())
    confidence = min(1.0, area_px / max(float(min_area_px), 1.0))

    return {
        "polarity": polarity,
        "threshold": float(threshold),
        "area_px": area_px,
        "centroid_x_px": float(xs.mean()),
        "centroid_y_px": float(ys.mean()),
        "bbox_left_px": x_min,
        "bbox_top_px": y_min,
        "bbox_right_px": x_max,
        "bbox_bottom_px": y_max,
        "confidence": confidence,
    }


def _threshold(gray, params_in: JsonDict, polarity: str) -> float:
    import numpy as np

    algorithm = algorithm_block(params_in)
    if algorithm.get("threshold") is not None:
        return finite_float(algorithm.get("threshold"), "algorithm.threshold")

    mean = float(np.mean(gray))
    std = float(np.std(gray))
    if polarity == "dark":
        return max(0.0, mean - std)
    return min(255.0, mean + std)
