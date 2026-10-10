"""Verify that the harness detects failures and covers all fifteen contracts."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from voicepilot.contracts import Intent

spec = importlib.util.spec_from_file_location(
    "verify_features", Path(__file__).resolve().parents[2] / "tools" / "verify_features.py"
)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = harness
spec.loader.exec_module(harness)


def test_all_feature_contracts_have_observable_checks():
    checks = harness.contract_suite()
    expected = {intent.value for intent in Intent if intent is not Intent.UNKNOWN}
    assert {check.name for check in checks} == expected
    assert all(check.status == "pass" for check in checks)


def test_executor_failure_is_not_reported_as_success(monkeypatch):
    from voicepilot.contracts import ActionResult, ResultStatus

    monkeypatch.setattr(harness.WindowsExecutor, "execute", lambda *_: ActionResult(ResultStatus.ERROR, ""))
    checks = harness.contract_suite()
    assert len(checks) == 15
    assert all(check.status == "fail" for check in checks)


def test_missing_prerequisite_is_distinct_from_failure():
    runner = harness.Runner("windows")
    def unavailable():
        raise harness.Blocked("Office unavailable")
    runner.check("PowerPoint", unavailable)
    runner.check("Independent check", lambda: "observed")
    assert [check.status for check in runner.checks] == ["blocked", "pass"]


def test_assertions_cannot_be_disabled():
    with pytest.raises(AssertionError, match="actual state"):
        harness.require(False, "actual state")


def test_report_preserves_layer_failure_and_unicode(tmp_path):
    checks = [harness.Check("DICTATE_TEXT", "windows", "fail", 2, "Việt Nam | mismatch\nobserved")]
    harness.write_report(tmp_path, checks)
    data = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert data["checks"][0]["status"] == "fail"
    assert data["checks"][0]["layer"] == "windows"
    assert "Việt Nam \\| mismatch observed" in (tmp_path / "report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("process_name", ["LockApp.exe", "LogonUI.exe", "WINLOGON.EXE"])
def test_locked_windows_desktop_blocks_native_checks(process_name):
    from voicepilot.contracts import AppKind, WindowContext

    context = WindowContext(1, 2, process_name, "", AppKind.OTHER, None)
    reason = harness.desktop_block_reason(context)
    assert reason is not None
    checks = harness.blocked_native_checks(reason)
    assert all(check.status == "blocked" for check in checks)
    assert {intent.value for intent in Intent if intent is not Intent.UNKNOWN} <= {c.name for c in checks}
