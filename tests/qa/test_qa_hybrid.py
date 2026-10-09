"""Black-box QA tests for HybridTranscriber, written from docs/PLAN.md "Audio và STT"."""

from __future__ import annotations

import concurrent.futures as cf
import random
import threading
import time
from collections import deque

import pytest

from voicepilot.contracts import AudioClip, SttBackendMode, SttError, Transcript
from voicepilot.stt.hybrid import HybridTranscriber

TIMEOUT = 0.15  # google_timeout_s used by the tests (PLAN says 2 s; shortened for speed)
CLIP = AudioClip(samples=b"\x00\x00" * 1600, turn_id=1)


def is_command(transcript: Transcript) -> bool:
    return transcript.text.startswith("mở")


class Backend:
    """Scripted backend. Each call pops one step; the last step repeats when the script runs out."""

    def __init__(self, name: str, *steps: dict, default: dict | None = None) -> None:
        self._name = name
        self._steps = deque(steps)
        self._default = default or {"text": "mở youtube"}
        self.calls = 0
        self.call_times: list[float] = []
        self.returned = 0
        self._lock = threading.Lock()

    @property
    def name(self) -> str:
        return self._name

    def transcribe(self, clip: AudioClip) -> Transcript:
        with self._lock:
            self.calls += 1
            self.call_times.append(time.monotonic())
            step = self._steps.popleft() if self._steps else self._default
        if step.get("hang") is not None:
            step["hang"].wait(10)
        if step.get("delay"):
            time.sleep(step["delay"])
        if step.get("error") is not None:
            raise step["error"]
        with self._lock:
            self.returned += 1
        return Transcript(
            text=step.get("text", "mở youtube"), source="fake", latency_ms=1.0, turn_id=clip.turn_id
        )


def hybrid(google, offline, mode=SttBackendMode.HYBRID, timeout=TIMEOUT, **kw) -> HybridTranscriber:
    return HybridTranscriber(google, offline, mode, google_timeout_s=timeout, **kw)


def run(h: HybridTranscriber, cancel: threading.Event | None = None) -> Transcript:
    return h.transcribe(CLIP, is_command, cancel or threading.Event())


# --- Google first, Gipformer fallback ------------------------------------------------------


def test_fast_valid_google_result_skips_gipformer():
    g, o = Backend("google", {"text": "mở youtube"}), Backend("gipformer", {"text": "mở facebook"})
    h = hybrid(g, o)
    try:
        result = run(h)
        assert result.text == "mở youtube"
        assert o.calls == 0
    finally:
        h.close()


@pytest.mark.parametrize("error", [SttError("network"), RuntimeError("boom")])
def test_google_error_starts_gipformer_at_once(error):
    # PLAN: "Khi lỗi ... chạy Gipformer" — the fallback must not wait for the timeout
    g = Backend("google", {"error": error})
    o = Backend("gipformer", {"text": "mở facebook"})
    h = hybrid(g, o)
    try:
        start = time.monotonic()
        result = run(h)
        assert result.text == "mở facebook"
        assert time.monotonic() - start < TIMEOUT
    finally:
        h.close()


def test_invalid_google_result_starts_gipformer_at_once():
    # PLAN: "chưa có kết quả hợp lệ" — an invalid Google transcript starts the fallback immediately
    g = Backend("google", {"text": "xin chào"})
    o = Backend("gipformer", {"text": "mở facebook"})
    h = hybrid(g, o)
    try:
        start = time.monotonic()
        assert run(h).text == "mở facebook"
        assert time.monotonic() - start < TIMEOUT
    finally:
        h.close()


def test_google_slower_than_timeout_lets_gipformer_win():
    start = time.monotonic()
    g = Backend("google", {"text": "mở youtube", "delay": 0.6})
    o = Backend("gipformer", {"text": "mở facebook"})
    h = hybrid(g, o)
    try:
        result = run(h)
        elapsed = time.monotonic() - start
        assert result.text == "mở facebook"
        assert o.calls == 1
        assert TIMEOUT * 0.8 <= o.call_times[0] - start <= TIMEOUT + 0.3
        assert elapsed < 0.6
    finally:
        h.close()


def test_first_valid_result_wins_even_when_google_finishes_later():
    g = Backend("google", {"text": "mở youtube", "delay": 0.5})
    o = Backend("gipformer", {"text": "mở facebook", "delay": 0.05})
    h = hybrid(g, o)
    try:
        assert run(h).text == "mở facebook"
    finally:
        h.close()


def test_invalid_gipformer_does_not_beat_later_valid_google():
    g = Backend("google", {"text": "mở youtube", "delay": 0.4})
    o = Backend("gipformer", {"text": "xin chào"})
    h = hybrid(g, o)
    try:
        assert run(h).text == "mở youtube"
    finally:
        h.close()


def test_both_invalid_returns_best_non_empty_google_preferred():
    g = Backend("google", {"text": "xin chào"})
    o = Backend("gipformer", {"text": "tìm kiếm"})
    h = hybrid(g, o)
    try:
        assert run(h).text == "xin chào"
    finally:
        h.close()


def test_both_backends_failing_raises_stt_error():
    g = Backend("google", {"error": SttError("x")})
    o = Backend("gipformer", {"error": RuntimeError("y")})
    h = hybrid(g, o)
    try:
        with pytest.raises(SttError) as info:
            run(h)
        assert str(info.value)
    finally:
        h.close()


def test_blank_transcripts_are_no_result():
    g = Backend("google", {"text": "   "})
    o = Backend("gipformer", {"text": ""})
    h = hybrid(g, o)
    try:
        with pytest.raises(SttError):
            run(h)
    finally:
        h.close()


def test_missing_google_backend_uses_gipformer_immediately():
    o = Backend("gipformer", {"text": "mở facebook"})
    h = hybrid(None, o)
    try:
        start = time.monotonic()
        assert run(h).text == "mở facebook"
        assert time.monotonic() - start < TIMEOUT
    finally:
        h.close()


def test_missing_gipformer_and_failing_google_raises():
    g = Backend("google", {"error": SttError("x")})
    h = hybrid(g, None)
    try:
        with pytest.raises(SttError):
            run(h)
    finally:
        h.close()


# --- single-backend modes ------------------------------------------------------------------


def test_google_mode_never_calls_gipformer():
    g = Backend("google", {"error": SttError("x")})
    o = Backend("gipformer")
    h = hybrid(g, o, mode=SttBackendMode.GOOGLE)
    try:
        with pytest.raises(SttError):
            run(h)
        assert o.calls == 0
    finally:
        h.close()


def test_offline_mode_never_calls_google():
    g = Backend("google")
    o = Backend("gipformer", {"text": "mở facebook"})
    h = hybrid(g, o, mode=SttBackendMode.OFFLINE)
    try:
        assert run(h).text == "mở facebook"
        assert g.calls == 0
    finally:
        h.close()


def test_single_mode_without_its_backend_raises_stt_error():
    h = hybrid(None, Backend("gipformer"), mode=SttBackendMode.GOOGLE)
    try:
        with pytest.raises(SttError):
            run(h)
    finally:
        h.close()


# --- cancellation --------------------------------------------------------------------------


def test_cancel_before_call_makes_no_backend_calls():
    g, o = Backend("google"), Backend("gipformer")
    h = hybrid(g, o)
    cancel = threading.Event()
    cancel.set()
    try:
        with pytest.raises(SttError):
            run(h, cancel)
        assert g.calls == 0 and o.calls == 0
    finally:
        h.close()


def test_cancel_during_google_wait_returns_promptly_without_fallback():
    release = threading.Event()
    g = Backend("google", {"hang": release})
    o = Backend("gipformer")
    h = hybrid(g, o)
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()
    try:
        start = time.monotonic()
        with pytest.raises(SttError) as info:
            run(h, cancel)
        assert time.monotonic() - start < 0.5
        assert "cancel" in str(info.value).lower()
        assert o.calls == 0
    finally:
        release.set()
        h.close()


def test_cancel_during_gipformer_wait_returns_promptly():
    release = threading.Event()
    g = Backend("google", {"error": SttError("x")})
    o = Backend("gipformer", {"hang": release})
    h = hybrid(g, o)
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()
    try:
        start = time.monotonic()
        with pytest.raises(SttError):
            run(h, cancel)
        assert time.monotonic() - start < 0.5
    finally:
        release.set()
        h.close()


def test_hung_google_does_not_block_gipformer_fallback():
    # The hung Google call must not hold the Gipformer worker (module docstring, separate executors)
    release = threading.Event()
    g = Backend("google", {"hang": release}, default={"hang": release})
    o = Backend("gipformer", {"text": "mở facebook"})
    h = hybrid(g, o)
    try:
        start = time.monotonic()
        assert run(h).text == "mở facebook"
        assert time.monotonic() - start < TIMEOUT + 0.5
    finally:
        release.set()
        h.close()


def test_hung_google_in_one_turn_does_not_break_next_turn():
    release = threading.Event()
    g = Backend("google", {"hang": release}, {"text": "mở youtube"})
    o = Backend("gipformer", {"text": "mở facebook"})
    h = hybrid(g, o)
    try:
        assert run(h).text == "mở facebook"  # turn 1: google hangs, fallback wins
        start = time.monotonic()
        assert run(h).text == "mở youtube"  # turn 2: google answers on another worker
        assert time.monotonic() - start < TIMEOUT
    finally:
        release.set()
        h.close()


def test_late_result_of_cancelled_turn_is_not_returned_to_next_turn():
    release = threading.Event()
    g = Backend("google", {"hang": release, "text": "mở youtube"}, {"text": "mở facebook"})
    o = Backend("gipformer", {"error": SttError("x")})
    h = hybrid(g, o)
    cancel = threading.Event()
    threading.Timer(0.05, cancel.set).start()
    try:
        with pytest.raises(SttError):
            run(h, cancel)
        release.set()  # the cancelled turn's Google answer arrives now
        time.sleep(0.05)
        assert run(h).text == "mở facebook"
    finally:
        release.set()
        h.close()


# --- close() and injected executors -----------------------------------------------------


def test_transcribe_after_close_raises_stt_error():
    h = hybrid(Backend("google"), Backend("gipformer"))
    h.close()
    with pytest.raises(SttError):
        run(h)


def test_close_leaves_injected_executor_running():
    pool = cf.ThreadPoolExecutor(max_workers=2)
    try:
        h = hybrid(Backend("google"), Backend("gipformer"), executor=pool)
        h.close()
        assert pool.submit(lambda: 42).result(timeout=2) == 42
    finally:
        pool.shutdown(wait=True)


def test_close_is_idempotent():
    h = hybrid(Backend("google"), Backend("gipformer"))
    h.close()
    h.close()


# --- stress and resource bounds ----------------------------------------------------------


def test_fifty_random_turns_finish_and_threads_stay_bounded():
    rng = random.Random(20261009)
    baseline = threading.active_count()
    peak = baseline
    outcomes = {"ok": 0, "stt_error": 0}
    for _ in range(50):
        g = Backend(
            "google",
            {
                "delay": rng.uniform(0, 0.12),
                "error": RuntimeError("flaky") if rng.random() < 0.2 else None,
                "text": rng.choice(["mở youtube", "xin chào", "", "tìm giá vàng"]),
            },
        )
        o = Backend(
            "gipformer",
            {
                "delay": rng.uniform(0, 0.12),
                "error": SttError("offline") if rng.random() < 0.2 else None,
                "text": rng.choice(["mở facebook", "xin chào", "lưu"]),
            },
        )
        h = hybrid(g, o, timeout=0.05)
        try:
            peak = max(peak, threading.active_count())
            try:
                result = run(h)
            except SttError:
                outcomes["stt_error"] += 1
            else:
                assert isinstance(result, Transcript)
                outcomes["ok"] += 1
        finally:
            h.close()
        assert g.returned <= g.calls and o.returned <= o.calls
    assert outcomes["ok"] + outcomes["stt_error"] == 50
    # per transcriber: at most 4 Google + 1 Gipformer worker threads; only one transcriber is alive at a time
    assert peak - baseline <= 5
    time.sleep(0.3)
    assert threading.active_count() <= baseline + 1


def test_repeated_construct_and_close_does_not_leak_threads():
    baseline = threading.active_count()
    for _ in range(20):
        h = hybrid(Backend("google"), Backend("gipformer"))
        run(h)
        h.close()
    time.sleep(0.3)
    assert threading.active_count() <= baseline + 1
