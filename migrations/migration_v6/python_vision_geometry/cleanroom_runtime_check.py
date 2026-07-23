r"""Verify the copy-ready V6 Python runtime inside ``python_env``.

This module is designed to run from the machine-side Python working directory:

    .\.venv\Scripts\python.exe -m python_vision_geometry.cleanroom_runtime_check

It is read-only. It imports modules, validates copied JSON assets, and checks
that the reviewed vision algorithms are available. It does not call YASE, grab
camera images, open the review UI, or move hardware.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import re
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


JsonDict = dict[str, Any]

EXPECTED_CAPTURE_IDS = (
    "2.1.1",
    "2.4.1",
    "2.5.1",
    "2.6.1",
    "4.1.1",
    "4.4.1",
    "4.5.1",
    "4.6.2",
)
EXPECTED_OFFSET_CAPTURE_IDS = ("2.1.1", "2.5.1", "2.6.1", "4.1.1", "4.5.1", "4.6.2")
EXPECTED_TRANSITION_IDS = (
    "2.1_to_2.4",
    "2.4_to_2.5",
    "2.5_to_2.6",
    "4.1_to_4.4",
    "4.4_to_4.5",
    "4.5_to_4.6.2",
)
REQUIRED_PUBLIC_IMPORTS = (
    "numpy",
    "cv2",
    "matplotlib.image",
    "skimage.feature",
    "skimage.transform",
)
REQUIRED_RECOGNIZERS = {
    "bright_threshold",
    "dark_threshold",
    "dark_adaptive",
    "opencv_adaptive_dark",
    "opencv_hough",
    "opencv_hough_sized",
    "skimage_hough",
    "skimage_hough_sized",
    "background_corrected_dark",
    "dark_rim_edges",
    "dark_multiscale",
    "dark_silhouette",
    "gradient_edges",
    "adaptive_contrast",
}
REQUIRED_RUNTIME_FILES = (
    "requirements.txt",
    "vision_recognition_lab.py",
    "python_vision_geometry/__init__.py",
    "python_vision_geometry/v6_offset_workflow.py",
    "python_vision_geometry/cleanroom_runtime_check.py",
    "standard_positions_v4/standard_positions.json",
)
DEFAULT_SEQUENCER_INI_PATH = Path(r"D:\TestMasterData\config\Sequencer.ini")
YASE_JSON_STATEMENT_PATH_MARKER = r"#SM_ROOT#\Functions\JSON\JSON_Statements"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str
    data: JsonDict | None = None

    def payload(self) -> JsonDict:
        result: JsonDict = {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }
        if self.data:
            result["data"] = self.data
        return result


def ok(name: str, detail: str, data: JsonDict | None = None) -> CheckResult:
    return CheckResult(name=name, status="ok", detail=detail, data=data)


def warning(name: str, detail: str, data: JsonDict | None = None) -> CheckResult:
    return CheckResult(name=name, status="warning", detail=detail, data=data)


def fail(name: str, detail: str, data: JsonDict | None = None) -> CheckResult:
    return CheckResult(name=name, status="fail", detail=detail, data=data)


def runtime_root_from_module() -> Path:
    return Path(__file__).resolve().parents[1]


def import_runtime_module(
    module_name: str,
    *,
    runtime_root: Path,
    expected_relative_path: str,
) -> Any:
    """Import from one copied runtime without leaking module/path state."""

    top_level_name = module_name.split(".", 1)[0]
    saved_modules = {
        name: module
        for name, module in tuple(sys.modules.items())
        if name == top_level_name or name.startswith(f"{top_level_name}.")
    }
    original_sys_path = list(sys.path)
    for name in saved_modules:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(runtime_root))
    importlib.invalidate_caches()
    try:
        module = importlib.import_module(module_name)
        actual_path = Path(str(getattr(module, "__file__", ""))).resolve()
        expected_path = (runtime_root / expected_relative_path).resolve()
        if actual_path != expected_path:
            raise ImportError(
                f"{module_name} resolved to {actual_path}, expected copied runtime module "
                f"{expected_path}"
            )
        return module
    finally:
        for name in tuple(sys.modules):
            if name == top_level_name or name.startswith(f"{top_level_name}."):
                sys.modules.pop(name, None)
        sys.modules.update(saved_modules)
        sys.path[:] = original_sys_path
        importlib.invalidate_caches()


def run_checks(
    runtime_root: str | Path | None = None,
    *,
    require_tmpython: bool = False,
    check_tk: bool = True,
    sequencer_ini_path: str | Path | None = None,
    require_yase_json_statements: bool = False,
) -> JsonDict:
    root = Path(runtime_root).resolve() if runtime_root is not None else runtime_root_from_module()
    sequencer_path = (
        Path(sequencer_ini_path)
        if sequencer_ini_path is not None
        else DEFAULT_SEQUENCER_INI_PATH
    )
    checks: list[CheckResult] = []

    checks.extend(check_required_files(root))
    checks.extend(check_public_imports())
    checks.append(check_tmpython(require_tmpython=require_tmpython))
    checks.append(check_tmpython_stdout_pipe_risk())
    checks.append(
        check_yase_json_statement_registration(
            sequencer_path,
            required=require_yase_json_statements,
        )
    )

    lab_module = None
    workflow_module = None
    try:
        lab_module = import_runtime_module(
            "vision_recognition_lab",
            runtime_root=root,
            expected_relative_path="vision_recognition_lab.py",
        )
        checks.append(
            ok(
                "vision_recognition_lab_import",
                "imported top-level vision_recognition_lab from the copied runtime",
            )
        )
    except Exception as exc:  # pragma: no cover - exercised by machine failures
        checks.append(fail("vision_recognition_lab_import", f"{type(exc).__name__}: {exc}", {"traceback": traceback.format_exc()}))

    try:
        workflow_module = import_runtime_module(
            "python_vision_geometry.v6_offset_workflow",
            runtime_root=root,
            expected_relative_path="python_vision_geometry/v6_offset_workflow.py",
        )
        checks.append(
            ok(
                "v6_offset_workflow_import",
                "imported python_vision_geometry.v6_offset_workflow from the copied runtime",
            )
        )
    except Exception as exc:  # pragma: no cover - exercised by machine failures
        checks.append(fail("v6_offset_workflow_import", f"{type(exc).__name__}: {exc}", {"traceback": traceback.format_exc()}))

    if lab_module is not None:
        checks.extend(check_vision_lab_algorithms(lab_module))
        if check_tk:
            checks.append(check_tk_available())
    if workflow_module is not None:
        checks.extend(check_v6_workflow_contract(workflow_module))
        checks.extend(check_standard_runtime_assets(root, workflow_module))

    failed = [check for check in checks if check.status == "fail"]
    warned = [check for check in checks if check.status == "warning"]
    return {
        "schema_version": 1,
        "ok": not failed,
        "status": (
            "V6 cleanroom runtime check passed"
            if not failed
            else f"V6 cleanroom runtime check failed: {len(failed)} failure(s)"
        ),
        "runtime_root": str(root),
        "require_tmpython": require_tmpython,
        "check_tk": check_tk,
        "sequencer_ini_path": str(sequencer_path),
        "require_yase_json_statements": require_yase_json_statements,
        "failure_count": len(failed),
        "warning_count": len(warned),
        "checks": [check.payload() for check in checks],
    }


def check_required_files(runtime_root: Path) -> list[CheckResult]:
    results = []
    for relative_path in REQUIRED_RUNTIME_FILES:
        path = runtime_root / Path(relative_path)
        if path.is_file():
            results.append(ok(f"file:{relative_path}", "present"))
        else:
            results.append(fail(f"file:{relative_path}", f"missing required runtime file: {path}"))
    baseline_dir = runtime_root / "standard_positions_v4" / "vision_baselines"
    if baseline_dir.is_dir():
        results.append(ok("directory:standard_positions_v4/vision_baselines", "present"))
    else:
        results.append(fail("directory:standard_positions_v4/vision_baselines", f"missing required baseline directory: {baseline_dir}"))
    return results


def check_public_imports() -> list[CheckResult]:
    results = []
    for module_name in REQUIRED_PUBLIC_IMPORTS:
        try:
            module = importlib.import_module(module_name)
            version = getattr(module, "__version__", None)
            data = {"version": str(version)} if version is not None else None
            results.append(ok(f"import:{module_name}", "imported", data))
        except Exception as exc:  # pragma: no cover - exercised by machine failures
            results.append(fail(f"import:{module_name}", f"{type(exc).__name__}: {exc}"))
    return results


def check_tmpython(*, require_tmpython: bool) -> CheckResult:
    try:
        tmpython_available = importlib.util.find_spec("tmpython.statement") is not None
    except ModuleNotFoundError:
        tmpython_available = False
    if tmpython_available:
        return ok("import:tmpython.statement", "TMPython vendor package is importable")
    detail = (
        "TMPython vendor package is not importable. The local developer venv may omit it, "
        "but the cleanroom machine venv must include testmaster_pyexec for YASE TMPython calls."
    )
    if require_tmpython:
        return fail("import:tmpython.statement", detail)
    return warning("import:tmpython.statement", detail)


def check_tmpython_stdout_pipe_risk() -> CheckResult:
    try:
        package_spec = importlib.util.find_spec("tmpython")
    except (ImportError, ModuleNotFoundError):
        package_spec = None
    package_roots = tuple(package_spec.submodule_search_locations or ()) if package_spec else ()
    if not package_roots:
        return warning(
            "tmpython_stdout_pipe_risk",
            "TMPython package source is unavailable, so the worker stdout-print hypothesis was not inspected.",
        )

    risky_sources = []
    pattern = re.compile(
        r"print\s*\(\s*pformat\s*\(\s*self\.input_params\s*\)\s*\)",
        flags=re.MULTILINE,
    )
    for package_root in package_roots:
        for source_path in Path(package_root).rglob("*.py"):
            try:
                source = source_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if pattern.search(source):
                risky_sources.append(str(source_path))
    if risky_sources:
        return warning(
            "tmpython_stdout_pipe_risk",
            (
                "TMPython worker source prints the complete input dictionary to stdout. "
                "Cleanroom evidence suggests, but does not yet prove, that repeated calls can "
                "block if the parent does not drain that pipe. Preserve input/result logs and "
                "restart TestMaster if a call stops before statement execution starts."
            ),
            {"matching_sources": risky_sources},
        )
    return ok(
        "tmpython_stdout_pipe_risk",
        (
            "The known print(pformat(self.input_params)) worker pattern was not found. "
            "This source check does not independently prove that stdout is continuously drained."
        ),
    )


def check_yase_json_statement_registration(
    sequencer_ini_path: str | Path,
    *,
    required: bool,
) -> CheckResult:
    path = Path(sequencer_ini_path)
    if not path.is_file():
        result = fail if required else warning
        return result(
            "yase_json_statement_registration",
            (
                f"Sequencer.ini was not found at {path}; cannot verify that "
                "JSON_GetFieldValueBoolean/Numeric/String are registered."
            ),
        )
    try:
        text = path.read_text(encoding="utf-8-sig", errors="ignore")
    except OSError as exc:
        result = fail if required else warning
        return result(
            "yase_json_statement_registration",
            f"could not read {path}: {type(exc).__name__}: {exc}",
        )

    normalized = text.replace("/", "\\")
    while "\\\\" in normalized:
        normalized = normalized.replace("\\\\", "\\")
    if YASE_JSON_STATEMENT_PATH_MARKER.casefold() in normalized.casefold():
        return ok(
            "yase_json_statement_registration",
            "Sequencer.ini registers the installed TestMaster JSON statement folder.",
            {
                "sequencer_ini_path": str(path),
                "required_path": YASE_JSON_STATEMENT_PATH_MARKER,
            },
        )
    result = fail if required else warning
    return result(
        "yase_json_statement_registration",
        (
            "Sequencer.ini does not register "
            f"{YASE_JSON_STATEMENT_PATH_MARKER}; V6 sequences using "
            "JSON_GetFieldValueBoolean/Numeric/String can fail to parse."
        ),
        {"sequencer_ini_path": str(path)},
    )


def check_vision_lab_algorithms(lab_module: Any) -> list[CheckResult]:
    results = []
    recognizers = getattr(lab_module, "RECOGNIZERS", ())
    names = {str(getattr(recognizer, "name", "")) for recognizer in recognizers}
    missing = sorted(REQUIRED_RECOGNIZERS - names)
    if missing:
        results.append(fail("vision_recognizers", "missing reviewed vision recognizers", {"missing": missing, "found": sorted(names)}))
    else:
        results.append(ok("vision_recognizers", f"all {len(REQUIRED_RECOGNIZERS)} expected recognizers are available", {"recognizers": sorted(names)}))

    default_geometry = getattr(lab_module, "DEFAULT_GEOMETRY_RECOGNIZER_NAME", None)
    geometry_names = tuple(getattr(lab_module, "GEOMETRY_RECOGNIZER_NAMES", ()))
    if default_geometry == "skimage_hough_sized" and default_geometry in geometry_names:
        results.append(ok("default_geometry_recognizer", "skimage_hough_sized is the default geometry recognizer"))
    else:
        results.append(fail("default_geometry_recognizer", "unexpected default geometry recognizer", {"default": default_geometry, "geometry_names": list(geometry_names)}))

    if callable(getattr(lab_module, "detect_side_trench_ruler_lines", None)):
        results.append(ok("side_trench_ruler_algorithm", "two-line side trench ruler function is available"))
    else:
        results.append(fail("side_trench_ruler_algorithm", "detect_side_trench_ruler_lines is not callable"))

    for callable_name in ("VisionRecognitionLab", "run_vision_recognition_lab_session", "run_vision_recognition_lab_from_params"):
        if callable(getattr(lab_module, callable_name, None)):
            results.append(ok(f"vision_lab_callable:{callable_name}", "available"))
        else:
            results.append(fail(f"vision_lab_callable:{callable_name}", "missing or not callable"))
    return results


def check_tk_available() -> CheckResult:
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return ok("tkinter_windowing", "Tk root can be created for the review UI")
    except Exception as exc:  # pragma: no cover - depends on machine UI state
        return fail("tkinter_windowing", f"{type(exc).__name__}: {exc}", {"traceback": traceback.format_exc()})


def check_v6_workflow_contract(workflow_module: Any) -> list[CheckResult]:
    results = []
    for callable_name in ("V6VisionReviewRecordStep", "V6VisionWorkflowStep", "run_v6_vision_workflow", "validate_reviewed_capture_session"):
        if callable(getattr(workflow_module, callable_name, None)):
            results.append(ok(f"v6_workflow_callable:{callable_name}", "available"))
        else:
            results.append(fail(f"v6_workflow_callable:{callable_name}", "missing or not callable"))

    capture_ids = tuple(getattr(workflow_module, "CAPTURE_SPECS", {}).keys())
    if capture_ids == EXPECTED_CAPTURE_IDS:
        results.append(ok("v6_capture_specs", "capture ID set and order match V6 workflow", {"capture_ids": list(capture_ids)}))
    else:
        results.append(fail("v6_capture_specs", "unexpected capture specs", {"expected": list(EXPECTED_CAPTURE_IDS), "found": list(capture_ids)}))

    offset_ids = tuple(getattr(workflow_module, "OFFSET_SPECS", {}).keys())
    if set(offset_ids) == set(EXPECTED_OFFSET_CAPTURE_IDS):
        results.append(ok("v6_offset_specs", "offset capture ID set matches V6 workflow", {"offset_capture_ids": list(offset_ids)}))
    else:
        results.append(fail("v6_offset_specs", "unexpected offset specs", {"expected": list(EXPECTED_OFFSET_CAPTURE_IDS), "found": list(offset_ids)}))

    transition_ids = tuple(getattr(workflow_module, "TRANSITION_SPECS", {}).keys())
    if transition_ids == EXPECTED_TRANSITION_IDS:
        results.append(ok("v6_transition_specs", "transition ID set and order match V6 workflow", {"transition_ids": list(transition_ids)}))
    else:
        results.append(fail("v6_transition_specs", "unexpected transition specs", {"expected": list(EXPECTED_TRANSITION_IDS), "found": list(transition_ids)}))

    try:
        init_result = workflow_module.run_v6_vision_workflow({"schema_version": 2, "command": "init"})
        if init_result.get("ok") is True and init_result.get("schema_version") == 2:
            results.append(ok("v6_init_smoke", "run_v6_vision_workflow init command returns schema-2 ok"))
        else:
            results.append(fail("v6_init_smoke", "init command returned unexpected result", {"result": init_result}))
    except Exception as exc:  # pragma: no cover - exercised by machine failures
        results.append(fail("v6_init_smoke", f"{type(exc).__name__}: {exc}", {"traceback": traceback.format_exc()}))
    return results


def check_standard_runtime_assets(runtime_root: Path, workflow_module: Any) -> list[CheckResult]:
    results = []
    standard_positions_path = runtime_root / "standard_positions_v4" / "standard_positions.json"
    baseline_dir = runtime_root / "standard_positions_v4" / "vision_baselines"
    try:
        standard_payload = json.loads(standard_positions_path.read_text(encoding="utf-8"))
        position_ids = {str(position.get("id")) for position in standard_payload.get("positions", [])}
        required_position_ids = {
            str(spec["position_id"])
            for spec in getattr(workflow_module, "CAPTURE_SPECS", {}).values()
        }
        missing_positions = sorted(required_position_ids - position_ids)
        if missing_positions:
            results.append(fail("standard_positions_v4", "standard positions are missing required V6 capture positions", {"missing": missing_positions}))
        else:
            results.append(ok("standard_positions_v4", "all V6 capture positions exist", {"required_position_ids": sorted(required_position_ids)}))
    except Exception as exc:
        results.append(fail("standard_positions_v4", f"{type(exc).__name__}: {exc}", {"traceback": traceback.format_exc()}))

    validator = getattr(workflow_module, "validate_reviewed_capture_session", None)
    for capture_id in EXPECTED_CAPTURE_IDS:
        baseline_path = baseline_dir / f"{capture_id}.json"
        if not baseline_path.is_file():
            results.append(fail(f"baseline:{capture_id}", f"missing reviewed baseline: {baseline_path}"))
            continue
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            if callable(validator):
                validator(capture_id, baseline, {})
            results.append(ok(f"baseline:{capture_id}", "reviewed baseline is present and validates"))
        except Exception as exc:
            results.append(fail(f"baseline:{capture_id}", f"{type(exc).__name__}: {exc}", {"traceback": traceback.format_exc()}))
    return results


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check the copied V6 cleanroom Python runtime.")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=None,
        help="Runtime root to check. Defaults to the parent of this python_vision_geometry folder.",
    )
    parser.add_argument(
        "--require-tmpython",
        action="store_true",
        help="Fail if the TestMaster TMPython vendor package is not importable.",
    )
    parser.add_argument(
        "--skip-tk",
        action="store_true",
        help="Skip creating a Tk root. Do not use this for final cleanroom UI readiness.",
    )
    parser.add_argument(
        "--sequencer-ini",
        type=Path,
        default=DEFAULT_SEQUENCER_INI_PATH,
        help=(
            "Global TestMaster Sequencer.ini to inspect for JSON statement registration. "
            f"Defaults to {DEFAULT_SEQUENCER_INI_PATH}."
        ),
    )
    parser.add_argument(
        "--require-yase-json-statements",
        action="store_true",
        help="Fail unless Sequencer.ini registers the installed JSON statement folder.",
    )
    parser.add_argument("--json-output", type=Path, default=None, help="Optional JSON report path.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    payload = run_checks(
        args.runtime_root,
        require_tmpython=args.require_tmpython,
        check_tk=not args.skip_tk,
        sequencer_ini_path=args.sequencer_ini,
        require_yase_json_statements=args.require_yase_json_statements,
    )
    text = json.dumps(payload, indent=2, sort_keys=True)
    print(text)
    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(text + "\n", encoding="utf-8")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
