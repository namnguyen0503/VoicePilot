"""Benchmark inference must follow the app's command and dictation policies."""

import csv
import importlib.util
import json
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

from voicepilot.config import AppConfig, load_config
from voicepilot.contracts import AppKind, Intent, Mode, Transcript, WindowContext
from voicepilot.intent.parser import RuleParser

spec = importlib.util.spec_from_file_location(
    "benchmark_stt", Path(__file__).resolve().parents[2] / "tools" / "benchmark_stt.py"
)
benchmark = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = benchmark
spec.loader.exec_module(benchmark)


def case(intent):
    return benchmark.Case(Path("synthetic.wav"), intent, {}, None, None)


def test_benchmark_rejects_unlisted_domain_like_controller(monkeypatch):
    config = AppConfig(website_aliases={"claude": "https://claude.ai"})
    monkeypatch.setattr(benchmark, "load_config", lambda: config)
    parser = RuleParser(config.website_aliases, config.app_aliases)
    valid = benchmark.is_valid_for(parser, case(Intent.BROWSER_OPEN_SITE))
    assert not valid(Transcript("mở close.vn", "google", 1, 0))
    assert valid(Transcript("mở claude", "gipformer", 1, 0))


@pytest.mark.parametrize("kind, wrong, correct", [
    (AppKind.BROWSER, "slide tiếp theo", "tab tiếp theo"),
    (AppKind.POWERPOINT, "tab tiếp theo", "slide tiếp theo"),
    (AppKind.OTHER, "tab tiếp theo", "chụp màn hình"),
    (AppKind.NONE, "lưu lại", "mở word"),
])
def test_benchmark_prefers_candidate_compatible_with_target(kind, wrong, correct, monkeypatch):
    config = load_config(Path("unused-benchmark-config.toml"))
    monkeypatch.setattr(benchmark, "load_config", lambda: config)
    context = WindowContext(0, 0, "", "", kind, None)
    sample = benchmark.Case(Path("synthetic.wav"), Intent.WIN_SCREENSHOT, {}, context, None)
    valid = benchmark.is_valid_for(RuleParser(config.website_aliases, config.app_aliases), sample)
    assert not valid(Transcript(wrong, "google", 1, 0))
    assert valid(Transcript(correct, "gipformer", 1, 0))


def test_benchmark_dictation_uses_unbiased_mode(monkeypatch):
    calls = []
    class FakeHybrid:
        def for_mode(self, mode):
            calls.append(mode)
            return self
        def transcribe(self, clip, validator, cancel):
            transcript = Transcript("xin chào", "gipformer", 1, clip.turn_id)
            assert validator(transcript)
            return transcript
    monkeypatch.setattr(benchmark, "read_wav", lambda _: np.zeros(160, dtype=np.int16))
    outcome = benchmark._run_case(FakeHybrid(), RuleParser({}, {}),
                                  case(Intent.DICTATE_TEXT), 0, threading.Event())
    assert outcome.intent_ok and calls == [Mode.DICTATION]


def test_benchmark_offline_command_model_has_app_hotwords(monkeypatch):
    received = {}
    class FakeAsr:
        def __init__(self, model_dir, **kwargs):
            received.update(kwargs)
        def warmup(self):
            received["warmed"] = True
    monkeypatch.setattr(benchmark, "GipformerStt", FakeAsr)
    monkeypatch.setattr(benchmark, "load_config", lambda: AppConfig(app_aliases={"custom app": "fake.exe"}))
    benchmark.build_gipformer(types.SimpleNamespace(model_dir="synthetic", threads=2))
    assert "custom app" in received["hotwords"]
    assert received["warmed"]


def test_benchmark_closes_worker_pools_when_case_fails(monkeypatch):
    closed = []
    class FakeHybrid:
        def __init__(self, **kwargs):
            pass
        def close(self):
            closed.append(True)
    def fail(*args):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(benchmark, "HybridTranscriber", FakeHybrid)
    monkeypatch.setattr(benchmark, "_run_case", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        benchmark.run_backend("offline", [case(Intent.PPT_SAVE)], RuleParser({}, {}),
                              types.SimpleNamespace(google_timeout=2), None)
    assert closed == [True]


def test_thirty_prompt_labels_cover_all_features_and_match_parser():
    root = Path(__file__).resolve().parents[2]
    config = load_config(root / "unused-benchmark-config.toml")
    parser = RuleParser(config.website_aliases, config.app_aliases)
    with (root / "docs" / "benchmark-labels.csv").open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert len(rows) == 30
    assert {row["expected_intent"] for row in rows} == {i.value for i in Intent if i is not Intent.UNKNOWN}
    for row in rows:
        intent = Intent(row["expected_intent"])
        context = WindowContext(0, 0, "", "", benchmark.CONTEXT_KINDS[row["context"]], None)
        mode = Mode.DICTATION if intent is Intent.DICTATE_TEXT else Mode.COMMAND
        command = parser.parse(row["reference"], context, mode)
        assert command.intent is intent, row["reference"]
        assert all(command.params[key] == value
                   for key, value in json.loads(row["expected_params_json"]).items())
