"""Endpoint harness failure detection and Windows console regression."""

import importlib.util
import io
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from voicepilot.audio.vad import Decision

spec = importlib.util.spec_from_file_location(
    "verify_endpoint", Path(__file__).resolve().parents[2] / "tools/verify_endpoint.py"
)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def test_endpoint_harness_reports_missing_speech_as_failure(monkeypatch):
    ep = SimpleNamespace(total_duration_ms=5010, speech_started=False)
    monkeypatch.setattr(harness, "evaluate", lambda *_, **__: (Decision.END_NO_SPEECH, ep))
    results = harness.suite(np.ones(16000, dtype=np.int16))
    assert len(results) == 6
    assert all(r["status"] == "fail" for r in results if r["name"] != "silence")
    assert results[-1]["status"] == "pass"


def test_endpoint_harness_writes_unicode_report_with_ansi_console(monkeypatch, tmp_path):
    voice = SimpleNamespace(generate=lambda *_, **__: SimpleNamespace(
        samples=np.full(16000, 0.2, dtype=np.float32), sample_rate=16000))
    monkeypatch.setattr(harness, "_load_sherpa_voice", lambda _: voice)
    monkeypatch.setattr(harness, "suite", lambda _: [dict(name="immediate", status="pass", detail="ok")])
    monkeypatch.setattr(sys, "argv", ["verify_endpoint.py"])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    assert harness.main() == 0
    reports = list(tmp_path.rglob("report.md"))
    assert len(reports) == 1
    assert "lưu/immediate" in reports[0].read_text(encoding="utf-8")
