"""Tests for HybridTranscriber: fallback rules, cancellation, and single-backend modes."""

from __future__ import annotations

import concurrent.futures as cf
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

import pytest

from voicepilot.contracts import AudioClip, SttBackendMode, SttError, Transcript
from voicepilot.stt.hybrid import HybridTranscriber

VALID_TEXT = "mở word"


def is_valid(transcript: Transcript) -> bool:
    return transcript.text == VALID_TEXT


CLIP = AudioClip(samples=b"\x00\x00" * 1600, turn_id=7)


class FakeBackend:
    """Backend whose outcome is scripted; can be gated on an Event to control timing."""

    def __init__(
        self,
        source: str,
        *,
        text: str = VALID_TEXT,
        error: Exception | None = None,
        gate: threading.Event | None = None,
    ) -> None:
        self.name = source
        self._source = source
        self._text = text
        self._error = error
        self._gate = gate
        self.calls = 0
        self.started_at: float | None = None
        self.started = threading.Event()
        self.finished = threading.Event()

    def transcribe(self, clip: AudioClip) -> Transcript:
        self.calls += 1
        self.started_at = time.monotonic()
        self.started.set()
        try:
            if self._gate is not None:
                assert self._gate.wait(5.0), "gate never released"
            if self._error is not None:
                raise self._error
            return Transcript(text=self._text, source=self._source, latency_ms=1.0, turn_id=clip.turn_id)  # type: ignore[arg-type]
        finally:
            self.finished.set()


class ManualExecutor(cf.Executor):
    """Executor that queues jobs until the test runs them, so completion order is fixed."""

    def __init__(self) -> None:
        self.jobs: list[tuple[Future[Any], Callable[..., Any], tuple[Any, ...]]] = []

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        future: Future[Any] = Future()
        self.jobs.append((future, fn, args))
        return future

    def run(self, index: int) -> None:
        future, fn, args = self.jobs[index]
        if not future.set_running_or_notify_cancel():
            return
        try:
            future.set_result(fn(*args))
        except BaseException as exc:  # forward everything to the future, like a real worker
            future.set_exception(exc)


@pytest.fixture
def pool() -> Any:
    executor = cf.ThreadPoolExecutor(max_workers=4)
    yield executor
    executor.shutdown(wait=False, cancel_futures=True)


def make(
    google: FakeBackend | None,
    offline: FakeBackend | None,
    *,
    mode: SttBackendMode = SttBackendMode.HYBRID,
    timeout_s: float = 0.2,
    executor: cf.Executor | None = None,
) -> HybridTranscriber:
    return HybridTranscriber(google, offline, mode, google_timeout_s=timeout_s, executor=executor)


def test_google_fast_and_valid_returns_google_without_starting_offline(pool: cf.Executor) -> None:
    google = FakeBackend("google")
    offline = FakeBackend("gipformer")
    result = make(google, offline, executor=pool).transcribe(CLIP, is_valid, threading.Event())
    assert result.source == "google"
    assert result.text == VALID_TEXT
    assert offline.calls == 0


def test_google_fast_but_invalid_runs_offline_and_offline_valid_wins(pool: cf.Executor) -> None:
    google = FakeBackend("google", text="mở wod")
    offline = FakeBackend("gipformer")
    result = make(google, offline, executor=pool).transcribe(CLIP, is_valid, threading.Event())
    assert result.source == "gipformer"
    assert offline.calls == 1


def test_google_slow_starts_offline_at_timeout_and_offline_valid_wins(pool: cf.Executor) -> None:
    release_google = threading.Event()
    google = FakeBackend("google", gate=release_google)
    offline = FakeBackend("gipformer")
    timeout = 0.2
    begin = time.monotonic()
    try:
        result = make(google, offline, timeout_s=timeout, executor=pool).transcribe(
            CLIP, is_valid, threading.Event()
        )
    finally:
        release_google.set()
    assert result.source == "gipformer"
    assert offline.started_at is not None
    assert offline.started_at - begin >= timeout * 0.8


def test_google_slow_but_valid_before_offline_returns_google(pool: cf.Executor) -> None:
    release_google = threading.Event()
    release_offline = threading.Event()
    google = FakeBackend("google", gate=release_google)
    offline = FakeBackend("gipformer", gate=release_offline)

    def finish_google_first() -> None:
        assert offline.started.wait(2.0)  # fallback is running because Google is slow
        release_google.set()
        assert google.finished.wait(2.0)  # Google completes valid while Gipformer is still blocked
        release_offline.set()

    driver = threading.Thread(target=finish_google_first)
    driver.start()
    try:
        result = make(google, offline, timeout_s=0.05, executor=pool).transcribe(
            CLIP, is_valid, threading.Event()
        )
    finally:
        release_google.set()
        release_offline.set()
        driver.join(2.0)
    assert result.source == "google"


def test_google_error_starts_offline_immediately(pool: cf.Executor) -> None:
    timeout = 1.0
    google = FakeBackend("google", error=SttError("boom"))
    offline = FakeBackend("gipformer")
    begin = time.monotonic()
    result = make(google, offline, timeout_s=timeout, executor=pool).transcribe(
        CLIP, is_valid, threading.Event()
    )
    assert result.source == "gipformer"
    assert offline.started_at is not None
    assert offline.started_at - begin < timeout / 2


def test_offline_error_with_invalid_google_returns_google_transcript(pool: cf.Executor) -> None:
    google = FakeBackend("google", text="mở wod")
    offline = FakeBackend("gipformer", error=SttError("boom"))
    result = make(google, offline, executor=pool).transcribe(CLIP, is_valid, threading.Event())
    assert result.source == "google"
    assert result.text == "mở wod"
    assert is_valid(result) is False


def test_both_backends_fail_raises_stt_error(pool: cf.Executor) -> None:
    google = FakeBackend("google", error=SttError("g"))
    offline = FakeBackend("gipformer", error=SttError("o"))
    with pytest.raises(SttError):
        make(google, offline, executor=pool).transcribe(CLIP, is_valid, threading.Event())


def test_cancel_during_wait_raises_promptly(pool: cf.Executor) -> None:
    release = threading.Event()
    google = FakeBackend("google", gate=release)
    offline = FakeBackend("gipformer", gate=release)
    cancel = threading.Event()
    timer = threading.Timer(0.1, cancel.set)
    timer.start()
    begin = time.monotonic()
    try:
        with pytest.raises(SttError, match="cancelled"):
            make(google, offline, timeout_s=0.05, executor=pool).transcribe(CLIP, is_valid, cancel)
        elapsed = time.monotonic() - begin
    finally:
        release.set()
        timer.cancel()
    assert elapsed < 0.4


def test_cancel_before_start_raises_without_calling_backends(pool: cf.Executor) -> None:
    google = FakeBackend("google")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(SttError, match="cancelled"):
        make(google, None, executor=pool).transcribe(CLIP, is_valid, cancel)
    assert google.calls == 0


def test_both_valid_simultaneously_prefers_google() -> None:
    executor = ManualExecutor()
    google = FakeBackend("google")
    offline = FakeBackend("gipformer")

    def drive() -> None:
        deadline = time.monotonic() + 2.0
        while len(executor.jobs) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        executor.run(0)
        executor.run(1)

    driver = threading.Thread(target=drive)
    driver.start()
    result = make(google, offline, timeout_s=0.0, executor=executor).transcribe(
        CLIP, is_valid, threading.Event()
    )
    driver.join(2.0)
    assert result.source == "google"


def test_late_result_after_return_does_not_change_outcome(pool: cf.Executor) -> None:
    release_google = threading.Event()
    google = FakeBackend("google", gate=release_google)
    offline = FakeBackend("gipformer")
    try:
        result = make(google, offline, timeout_s=0.05, executor=pool).transcribe(
            CLIP, is_valid, threading.Event()
        )
        assert result.source == "gipformer"
        release_google.set()  # Google now finishes valid, after the turn was decided
        assert google.finished.wait(2.0)
    finally:
        release_google.set()
    assert result.source == "gipformer"
    assert google.calls == 1


def test_google_mode_uses_only_google(pool: cf.Executor) -> None:
    google = FakeBackend("google", text="mở wod")
    offline = FakeBackend("gipformer")
    result = make(google, offline, mode=SttBackendMode.GOOGLE, executor=pool).transcribe(
        CLIP, is_valid, threading.Event()
    )
    assert result.source == "google"
    assert offline.calls == 0


def test_offline_mode_uses_only_offline(pool: cf.Executor) -> None:
    google = FakeBackend("google")
    offline = FakeBackend("gipformer")
    result = make(google, offline, mode=SttBackendMode.OFFLINE, executor=pool).transcribe(
        CLIP, is_valid, threading.Event()
    )
    assert result.source == "gipformer"
    assert google.calls == 0


def test_mode_with_missing_backend_raises(pool: cf.Executor) -> None:
    with pytest.raises(SttError):
        make(FakeBackend("google"), None, mode=SttBackendMode.OFFLINE, executor=pool).transcribe(
            CLIP, is_valid, threading.Event()
        )
    with pytest.raises(SttError):
        make(None, FakeBackend("gipformer"), mode=SttBackendMode.GOOGLE, executor=pool).transcribe(
            CLIP, is_valid, threading.Event()
        )


def test_hybrid_with_no_google_backend_uses_offline(pool: cf.Executor) -> None:
    offline = FakeBackend("gipformer")
    result = make(None, offline, executor=pool).transcribe(CLIP, is_valid, threading.Event())
    assert result.source == "gipformer"


def test_hung_google_from_previous_turn_does_not_delay_next_turn_offline() -> None:
    hung = threading.Event()
    google = FakeBackend("google", gate=hung)  # every Google call blocks until released
    offline = FakeBackend("gipformer")
    google_pool = cf.ThreadPoolExecutor(max_workers=1)  # one worker: a hung call would starve it
    offline_pool = cf.ThreadPoolExecutor(max_workers=1)
    timeout = 0.2
    transcriber = HybridTranscriber(
        google,
        offline,
        SttBackendMode.HYBRID,
        google_timeout_s=timeout,
        executor=google_pool,
        offline_executor=offline_pool,
    )
    try:
        first = transcriber.transcribe(CLIP, is_valid, threading.Event())
        assert first.source == "gipformer"  # turn 1: Google hangs, Gipformer answers at the timeout
        begin = time.monotonic()
        second = transcriber.transcribe(CLIP, is_valid, threading.Event())  # turn 2: old Google still hung
        assert second.source == "gipformer"
        assert offline.started_at is not None
        assert offline.started_at - begin <= timeout + 0.15
    finally:
        hung.set()
        transcriber.close()
        google_pool.shutdown(wait=True)
        offline_pool.shutdown(wait=True)


def test_close_shuts_down_owned_pools_and_keeps_injected_ones_running() -> None:
    owned = HybridTranscriber(FakeBackend("google"), FakeBackend("gipformer"), SttBackendMode.HYBRID)
    google_pool = owned._google_executor
    offline_pool = owned._offline_executor
    owned.close()
    assert google_pool._shutdown  # type: ignore[attr-defined]
    assert offline_pool._shutdown  # type: ignore[attr-defined]

    injected = cf.ThreadPoolExecutor(max_workers=1)
    shared = HybridTranscriber(FakeBackend("google"), None, SttBackendMode.GOOGLE, executor=injected)
    shared.close()
    assert not injected._shutdown  # type: ignore[attr-defined]
    result = shared.transcribe(CLIP, is_valid, threading.Event())
    assert result.source == "google"
    injected.shutdown(wait=True)


@pytest.mark.parametrize("mode", [SttBackendMode.HYBRID, SttBackendMode.GOOGLE, SttBackendMode.OFFLINE])
def test_transcribe_after_close_raises_stt_error(mode: SttBackendMode) -> None:
    transcriber = HybridTranscriber(FakeBackend("google"), FakeBackend("gipformer"), mode)
    transcriber.close()
    with pytest.raises(SttError, match="đã đóng"):
        transcriber.transcribe(CLIP, is_valid, threading.Event())
