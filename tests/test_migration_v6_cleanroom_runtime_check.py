from pathlib import Path

from migrations.migration_v6.python_vision_geometry.cleanroom_runtime_check import (
    EXPECTED_CAPTURE_IDS,
    run_checks,
)


ROOT = Path(__file__).resolve().parents[1]
V6 = ROOT / "migrations" / "migration_v6"


def _check_by_name(payload):
    return {check["name"]: check for check in payload["checks"]}


def test_v6_cleanroom_runtime_check_passes_for_repo_copy_without_tmpython_requirement():
    payload = run_checks(V6, require_tmpython=False, check_tk=False)
    checks = _check_by_name(payload)

    failed = [check for check in payload["checks"] if check["status"] == "fail"]
    assert failed == []
    assert payload["ok"] is True
    assert checks["import:tmpython.statement"]["status"] in {"ok", "warning"}
    assert checks["vision_recognizers"]["status"] == "ok"
    assert checks["v6_capture_specs"]["data"]["capture_ids"] == list(EXPECTED_CAPTURE_IDS)
    for capture_id in EXPECTED_CAPTURE_IDS:
        assert checks[f"baseline:{capture_id}"]["status"] == "ok"


def test_v6_cleanroom_runtime_check_can_require_tmpython_for_machine_runtime():
    payload = run_checks(V6, require_tmpython=True, check_tk=False)
    checks = _check_by_name(payload)

    if checks["import:tmpython.statement"]["status"] == "fail":
        assert payload["ok"] is False
        assert "cleanroom machine venv must include testmaster_pyexec" in checks[
            "import:tmpython.statement"
        ]["detail"]
    else:
        assert payload["ok"] is True
