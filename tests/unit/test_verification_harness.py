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

    monkeypatch.setattr(harness.WindowsExecutor, "execute",
                        lambda *_, **__: ActionResult(ResultStatus.ERROR, ""))
    checks = harness.contract_suite()
    assert len(checks) == 15
    assert all(check.status == "fail" for check in checks)


def test_all_fifteen_features_through_controller_and_each_stt_mode(qapp):
    checks = harness.pipeline_suite()
    expected = {f"{mode}:{intent.value}" for mode in ("google", "offline", "hybrid", "fallback")
                for intent in Intent if intent is not Intent.UNKNOWN}
    assert expected <= {check.name for check in checks}
    assert len(checks) == 64
    assert all(check.status == "pass" for check in checks), [c for c in checks if c.status != "pass"]


def test_pipeline_harness_detects_executor_failure(monkeypatch, qapp):
    from voicepilot.contracts import ActionResult, AppKind, ResultStatus

    monkeypatch.setattr(harness.WindowsExecutor, "execute",
                        lambda *_, **__: ActionResult(ResultStatus.ERROR, ""))
    with pytest.raises(AssertionError, match="Execution returned error"):
        harness.pipeline_case(Intent.WIN_SCREENSHOT, "chụp màn hình", AppKind.OTHER, {}, "offline")


def test_powerpoint_worker_drops_expired_jobs_and_rechecks_target():
    checks = harness.powerpoint_worker_suite()
    assert len(checks) == 46
    assert all(check.status == "pass" for check in checks), [c for c in checks if c.status != "pass"]


def test_powerpoint_harness_detects_a_missing_deadline_guard(monkeypatch):
    from voicepilot.windows.powerpoint import PowerPointController

    monkeypatch.setattr(PowerPointController, "_check_alive", staticmethod(lambda *_: None))
    with pytest.raises(AssertionError, match="late effects"):
        harness.powerpoint_worker_case(Intent.PPT_SAVE, "timeout_source")


@pytest.mark.parametrize("cancel_after_chunk", [False, True])
def test_tts_probe_checks_native_callback_contract(tmp_path, cancel_after_chunk):
    import numpy as np

    model = tmp_path / "voice.onnx"
    model.write_bytes(b"synthetic")
    chunks = []
    class NativeContractVoice:
        sample_rate = 16000
        def generate(self, text, callback, **kwargs):
            for _ in range(2):
                chunks.append(text)
                if callback(np.full(160, 0.1, dtype=np.float32), 0.5) == 0:
                    break
    harness.tts_callback_probe(NativeContractVoice(), model, cancel_after_chunk=cancel_after_chunk)
    assert len(chunks) == (1 if cancel_after_chunk else 6)


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


def test_existing_report_directory_is_not_overwritten(tmp_path):
    evidence = tmp_path / "report.json"
    evidence.write_text("previous evidence", encoding="utf-8")
    with pytest.raises(SystemExit):
        harness.main(["--output", str(tmp_path)])
    assert evidence.read_text(encoding="utf-8") == "previous evidence"


def test_runtime_timeout_becomes_a_failed_report_check(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise harness.subprocess.TimeoutExpired("synthetic child", 120)
    monkeypatch.setattr(harness.subprocess, "run", timeout)
    checks = harness.collect_probe("runtime", tmp_path, 120)
    assert checks[0].status == "fail" and checks[0].layer == "runtime"
    assert checks[0].elapsed_ms == 120000


def test_runtime_load_failure_keeps_runtime_layer(monkeypatch, tmp_path):
    def broken(_folder):
        raise OSError("Synthetic voice load failure")
    monkeypatch.setattr(harness, "runtime_suite", broken)
    checks = harness.run_probe("runtime", tmp_path)
    assert checks[0].status == "fail" and checks[0].layer == "runtime"


def test_browser_fixture_identifies_pid_handoff_without_touching_personal_window():
    windows = [(10, 100, "Personal tab - Edge", "msedge.exe"),
               (20, 200, "VoicePilot 4321 /first - Edge", "msedge.exe"),
               (30, 300, "VoicePilot 5432 /first - Edge", "msedge.exe")]
    assert harness.browser_fixture_window(windows, "msedge.exe", 4321) == 20
    assert harness.browser_fixture_window(windows, "chrome.exe", 4321) == 0
