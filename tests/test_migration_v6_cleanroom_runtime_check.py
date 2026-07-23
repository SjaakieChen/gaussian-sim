from pathlib import Path

from migrations.migration_v6.python_vision_geometry.cleanroom_runtime_check import (
    EXPECTED_CAPTURE_IDS,
    YASE_JSON_STATEMENT_PATH_MARKER,
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


def test_v6_cleanroom_runtime_check_requires_registered_yase_json_statements(tmp_path):
    sequencer_ini = tmp_path / "Sequencer.ini"
    sequencer_ini.write_text(
        (
            "[Statements]\n"
            "Path=\"existing,"
            + YASE_JSON_STATEMENT_PATH_MARKER.replace("\\", "\\\\")
            + "\"\n"
        ),
        encoding="utf-8",
    )

    registered = run_checks(
        V6,
        require_tmpython=False,
        check_tk=False,
        sequencer_ini_path=sequencer_ini,
        require_yase_json_statements=True,
    )
    registered_check = _check_by_name(registered)["yase_json_statement_registration"]
    assert registered["ok"] is True
    assert registered_check["status"] == "ok"

    sequencer_ini.write_text(
        '[Statements]\nPath="#SM_ROOT#\\\\core\\\\Statements"\n',
        encoding="utf-8",
    )
    missing = run_checks(
        V6,
        require_tmpython=False,
        check_tk=False,
        sequencer_ini_path=sequencer_ini,
        require_yase_json_statements=True,
    )
    missing_check = _check_by_name(missing)["yase_json_statement_registration"]
    assert missing["ok"] is False
    assert missing_check["status"] == "fail"
    assert "can fail to parse" in missing_check["detail"]
