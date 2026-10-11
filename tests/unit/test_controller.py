"""Controller behaviour with fake components: turns, cancellation, errors, history and logging."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QCoreApplication
from pytestqt.qtbot import QtBot

from voicepilot.config import AppConfig, load_config
from voicepilot.contracts import (
    ActionResult,
    AppKind,
    AudioClip,
    AudioDeviceError,
    Command,
    Intent,
    Mode,
    ResultStatus,
    SttError,
    Transcript,
    WindowContext,
)
from voicepilot.controller import Controller, State
from voicepilot.intent.parser import RuleParser
from voicepilot.windows import browser
from voicepilot.windows.executor import WindowsExecutor

NEEDS_RETRY_MSG = "Cửa sổ đích đã thay đổi, hãy thử lại."
POWERPOINT_CTX = WindowContext(
    hwnd=1001,
    pid=42,
    process_name="POWERPNT.EXE",
    title="Bài giảng.pptx",
    app_kind=AppKind.POWERPOINT,
    browser=None,
)
CLIP = AudioClip(samples=b"\x01\x00" * 1600, turn_id=0)


class ManualClock:
    """Clock the test advances by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeContext:
    def __init__(self) -> None:
        self.ctx = POWERPOINT_CTX
        self.still_target = True

    def capture(self) -> WindowContext:
        return self.ctx

    def is_still_target(self, ctx: WindowContext) -> bool:
        return self.still_target


class FakeRecorder:
    def __init__(self) -> None:
        self.clip: AudioClip | None = CLIP
        self.error: Exception | None = None
        self.wait_for_stop = False
        self.started = threading.Event()
        self.stop_seen = threading.Event()
        self.calls = 0

    def record(
        self, turn_id: int, mode: Mode, stop_event: threading.Event, cancel_event: threading.Event
    ) -> AudioClip | None:
        self.calls += 1
        self.started.set()
        if self.wait_for_stop:
            deadline = time.monotonic() + 5.0
            while not (stop_event.is_set() or cancel_event.is_set()):
                if time.monotonic() > deadline:
                    raise TimeoutError("recorder was never stopped")
                stop_event.wait(0.005)
            if stop_event.is_set():
                self.stop_seen.set()
        if self.error is not None:
            raise self.error
        return None if cancel_event.is_set() else self.clip


class FakeTranscriber:
    def __init__(self) -> None:
        self.text = "slide tiếp"
        self.error: Exception | None = None
        self.gate: threading.Event | None = None  # when set, transcribe waits for it and ignores cancel
        self.entered = threading.Event()
        self.valid: list[bool] = []

    def transcribe(
        self, clip: AudioClip, is_valid: Callable[[Transcript], bool], cancel_event: threading.Event
    ) -> Transcript:
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5.0)
        transcript = Transcript(text=self.text, source="fake", latency_ms=1.0, turn_id=clip.turn_id)
        self.valid.append(is_valid(transcript))
        if self.error is not None:
            raise self.error
        return transcript


class FakeParser:
    COMMANDS = {
        "slide tiếp": Command(Intent.PPT_NEXT_SLIDE, "slide tiếp"),
        "tìm": Command(Intent.BROWSER_SEARCH, "tìm", missing=("query",), example="Tìm kiếm thời tiết"),
    }

    def parse(self, text: str, context: WindowContext | None, mode: Mode) -> Command:
        found = self.COMMANDS.get(text)
        return found if found is not None else Command(Intent.UNKNOWN, text, example="Slide tiếp theo")


class FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[Command] = []
        self.result = ActionResult(ResultStatus.OK, "Đã chuyển slide.")
        self.error: Exception | None = None
        self.shutdown_called = False

    def execute(
        self, command: Command, context: WindowContext, *, cancel: threading.Event | None = None
    ) -> ActionResult:
        self.calls.append(command)
        if self.error is not None:
            raise self.error
        return self.result

    def shutdown(self) -> None:
        self.shutdown_called = True


class FakeSpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.stops = 0

    def speak(self, text: str) -> None:
        self.spoken.append(text)

    def stop(self) -> None:
        self.stops += 1


class ClosableTranscriber(FakeTranscriber):
    """A transcriber that counts close() calls, as HybridTranscriber does."""

    def __init__(self, close_error: Exception | None = None) -> None:
        super().__init__()
        self.closed = 0
        self.close_error = close_error

    def close(self) -> None:
        self.closed += 1
        if self.close_error is not None:
            raise self.close_error


def test_controller_selects_dictation_backend_before_recognition(build_rig, qtbot):
    plain = FakeTranscriber()
    plain.text = "nội dung tự do"
    class ModeAwareTranscriber(FakeTranscriber):
        def for_mode(self, mode):
            assert mode is Mode.DICTATION
            return plain
    command = ModeAwareTranscriber()
    rig = build_rig(transcriber=command)
    rig.controller.toggle_mode()
    rig.controller.toggle_listen()
    qtbot.waitUntil(lambda: bool(rig.results))
    assert not command.entered.is_set()
    assert plain.entered.is_set()
    assert rig.executor.calls[0].params["text"] == "nội dung tự do"
    assert rig.speaker.spoken == []


@dataclass
class Rig:
    """A controller wired to fakes, plus the lists its signals fill in."""

    controller: Controller
    pool: ThreadPoolExecutor
    recorder: FakeRecorder
    transcriber: FakeTranscriber
    parser: FakeParser
    executor: FakeExecutor
    speaker: FakeSpeaker
    context: FakeContext
    states: list[tuple[str, str]] = field(default_factory=list)
    results: list[ActionResult] = field(default_factory=list)
    transcripts: list[str] = field(default_factory=list)
    modes: list[Mode] = field(default_factory=list)
    targets: list[str] = field(default_factory=list)
    levels: list[float] = field(default_factory=list)

    def idle_count(self) -> int:
        return sum(1 for state, _ in self.states if state == "IDLE")

    def state_names(self) -> list[str]:
        return [state for state, _ in self.states]


@pytest.fixture
def build_rig() -> Iterator[Callable[..., Rig]]:
    rigs: list[Rig] = []

    def build(
        config: AppConfig | None = None,
        clock: Callable[[], float] | None = None,
        transcriber: FakeTranscriber | None = None,
    ) -> Rig:
        pool = ThreadPoolExecutor(max_workers=2)
        recorder = FakeRecorder()
        transcriber = transcriber if transcriber is not None else FakeTranscriber()
        parser, executor, speaker, context = FakeParser(), FakeExecutor(), FakeSpeaker(), FakeContext()
        controller = Controller(
            config or AppConfig(),
            recorder,
            transcriber,
            parser,
            executor,
            speaker,
            context,
            pool=pool,
            clock=clock or time.monotonic,
            hold_ms=5,
        )
        rig = Rig(controller, pool, recorder, transcriber, parser, executor, speaker, context)
        controller.state_changed.connect(lambda state, message: rig.states.append((state, message)))
        controller.result_ready.connect(rig.results.append)
        controller.transcript_changed.connect(rig.transcripts.append)
        controller.mode_changed.connect(rig.modes.append)
        controller.target_changed.connect(rig.targets.append)
        controller.level_changed.connect(rig.levels.append)
        rigs.append(rig)
        return rig

    yield build
    for rig in rigs:
        rig.controller.shutdown()


def wait_for_idle(qtbot: QtBot, rig: Rig, count: int = 1) -> None:
    qtbot.waitUntil(lambda: rig.idle_count() >= count, timeout=5000)


def drain(rig: Rig) -> None:
    """Wait for every worker to finish, then deliver the queued signals they posted."""
    rig.pool.shutdown(wait=True)
    QCoreApplication.processEvents()


def test_happy_path_command_runs_once_and_speaks_the_result(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    rig = build_rig()
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    assert rig.state_names() == ["LISTENING", "RECOGNIZING", "EXECUTING", "DONE", "IDLE"]
    assert rig.states[3] == ("DONE", "Đã chuyển slide.")
    assert [call.intent for call in rig.executor.calls] == [Intent.PPT_NEXT_SLIDE]
    assert rig.speaker.spoken == ["Đã chuyển slide."]
    assert rig.speaker.stops >= 1
    assert rig.transcripts == ["", "slide tiếp"]
    assert rig.targets == ["PowerPoint"]
    assert [result.status for result in rig.results] == [ResultStatus.OK]
    assert rig.transcriber.valid == [True]


def test_dictation_types_text_without_speech_output(build_rig: Callable[..., Rig], qtbot: QtBot) -> None:
    rig = build_rig()
    rig.controller.toggle_mode()
    rig.transcriber.text = "Xin chào thế giới"
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    assert rig.modes == [Mode.DICTATION]
    assert [call.intent for call in rig.executor.calls] == [Intent.DICTATE_TEXT]
    assert rig.executor.calls[0].params == {"text": "Xin chào thế giới"}
    assert rig.speaker.spoken == []
    assert rig.states[-2] == ("DONE", "Đã chuyển slide.")


def test_cancel_during_stt_never_executes_and_drops_late_result(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    rig = build_rig()
    rig.transcriber.gate = threading.Event()
    rig.controller.toggle_listen()
    qtbot.waitUntil(rig.transcriber.entered.is_set, timeout=5000)

    rig.controller.cancel()
    assert rig.states[-1] == ("IDLE", "Đã hủy")
    states_before_late_result = len(rig.states)

    rig.transcriber.gate.set()
    drain(rig)

    assert rig.executor.calls == []
    assert rig.results == []
    assert len(rig.states) == states_before_late_result
    assert rig.controller.state is State.IDLE


def test_second_toggle_while_listening_ends_recording_early(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    rig = build_rig()
    rig.recorder.wait_for_stop = True
    rig.controller.toggle_listen()
    qtbot.waitUntil(rig.recorder.started.is_set, timeout=5000)

    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    assert rig.recorder.stop_seen.is_set()
    assert len(rig.executor.calls) == 1


def test_target_change_needs_retry_and_keeps_transcript(build_rig: Callable[..., Rig], qtbot: QtBot) -> None:
    rig = build_rig()
    rig.context.still_target = False
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    assert rig.executor.calls == []
    assert rig.results[-1].status is ResultStatus.NEEDS_RETRY
    assert rig.results[-1].message_vi == NEEDS_RETRY_MSG
    assert rig.transcripts[-1] == "slide tiếp"
    assert rig.speaker.spoken == [NEEDS_RETRY_MSG]


@pytest.mark.parametrize(
    ("text", "expected_message"),
    [
        ("blah", "Chưa hiểu lệnh. Thử nói: Slide tiếp theo"),
        ("tìm", "Chưa hiểu lệnh. Thử nói: Tìm kiếm thời tiết"),
    ],
)
def test_unknown_or_incomplete_command_shows_example(
    build_rig: Callable[..., Rig], qtbot: QtBot, text: str, expected_message: str
) -> None:
    rig = build_rig()
    rig.transcriber.text = text
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    assert rig.executor.calls == []
    assert rig.results[-1].status is ResultStatus.INVALID_PARAM
    assert rig.results[-1].message_vi == expected_message
    assert rig.speaker.spoken == ["Chưa hiểu lệnh."]


@pytest.mark.parametrize(
    ("setup", "expected_status", "expected_message", "executes"),
    [
        (
            lambda rig: setattr(rig.recorder, "error", AudioDeviceError("no device")),
            ResultStatus.ERROR,
            "Không tìm thấy micro",
            False,
        ),
        (lambda rig: setattr(rig.recorder, "clip", None), ResultStatus.ERROR, "Không nghe thấy gì", False),
        (
            lambda rig: setattr(rig.transcriber, "error", SttError("empty")),
            ResultStatus.ERROR,
            "Không nhận dạng được, hãy thử lại",
            False,
        ),
        (
            lambda rig: setattr(rig.recorder, "error", RuntimeError("bug")),
            ResultStatus.ERROR,
            "Có lỗi nội bộ, hãy thử lại",
            False,
        ),
        (
            lambda rig: setattr(rig.executor, "error", RuntimeError("boom")),
            ResultStatus.ERROR,
            "Không thực hiện được lệnh",
            True,
        ),
    ],
    ids=["no-mic", "no-speech", "stt-failed", "internal", "executor-raised"],
)
def test_failures_report_a_message_and_return_to_idle(
    build_rig: Callable[..., Rig],
    qtbot: QtBot,
    setup: Callable[[Rig], Any],
    expected_status: ResultStatus,
    expected_message: str,
    executes: bool,
) -> None:
    rig = build_rig()
    setup(rig)
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    assert rig.results[-1].status is expected_status
    assert rig.results[-1].message_vi == expected_message
    assert ("ERROR", expected_message) in rig.states
    assert rig.states[-1] == ("IDLE", "")
    assert rig.controller.state is State.IDLE
    assert (len(rig.executor.calls) == 1) is executes


def test_mode_toggle_is_ignored_during_a_turn(build_rig: Callable[..., Rig], qtbot: QtBot) -> None:
    rig = build_rig()
    rig.recorder.wait_for_stop = True
    rig.controller.toggle_listen()
    qtbot.waitUntil(rig.recorder.started.is_set, timeout=5000)

    rig.controller.toggle_mode()
    assert rig.controller.mode is Mode.COMMAND
    assert rig.modes == []

    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)
    rig.controller.toggle_mode()
    assert rig.controller.mode is Mode.DICTATION
    assert rig.modes == [Mode.DICTATION]


def test_history_keeps_only_the_last_entries(build_rig: Callable[..., Rig], qtbot: QtBot) -> None:
    rig = build_rig(AppConfig(history_size=3))
    rig.controller.toggle_mode()
    for turn, text in enumerate(["một", "hai", "ba", "bốn", "năm"], start=1):
        rig.transcriber.text = text
        rig.controller.toggle_listen()
        wait_for_idle(qtbot, rig, count=turn)

    history = rig.controller.history
    assert [entry[2] for entry in history] == ["ba", "bốn", "năm"]
    assert all(entry[1] is Mode.DICTATION for entry in history)
    assert all(entry[4] == "ok" for entry in history)


def test_log_has_one_line_per_turn_and_never_the_spoken_text(
    build_rig: Callable[..., Rig], qtbot: QtBot, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="voicepilot.controller")
    rig = build_rig()
    rig.controller.toggle_mode()
    rig.transcriber.text = "bí mật tuyệt đối"
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    lines = [record.getMessage() for record in caplog.records if record.name == "voicepilot.controller"]
    assert len(lines) == 1
    assert "turn id=1" in lines[0]
    assert "mode=dictation" in lines[0]
    assert "stt=fake" in lines[0]
    assert "status=ok" in lines[0]
    assert "total_ms=" in lines[0]
    assert "bí mật" not in caplog.text


def test_audio_level_is_ignored_when_idle_and_throttled_while_listening(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    clock = ManualClock()
    rig = build_rig(clock=clock)
    rig.controller.on_audio_level(0.3)
    assert rig.levels == []

    rig.recorder.wait_for_stop = True
    rig.controller.toggle_listen()
    qtbot.waitUntil(rig.recorder.started.is_set, timeout=5000)
    for _ in range(10):
        rig.controller.on_audio_level(0.5)
    assert rig.levels == [0.5]

    clock.now += 0.02
    rig.controller.on_audio_level(0.9)
    assert rig.levels == [0.5]

    clock.now += 0.05
    rig.controller.on_audio_level(0.9)
    assert rig.levels == [0.5, 0.9]

    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)


def test_disabled_tts_keeps_command_feedback_silent(build_rig: Callable[..., Rig], qtbot: QtBot) -> None:
    rig = build_rig(AppConfig(tts_enabled=False))
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    assert rig.results[-1].status is ResultStatus.OK
    assert rig.speaker.spoken == []


def test_shutdown_cancels_the_active_turn_and_releases_the_executor(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    rig = build_rig()
    rig.recorder.wait_for_stop = True
    rig.controller.toggle_listen()
    qtbot.waitUntil(rig.recorder.started.is_set, timeout=5000)

    rig.controller.shutdown()

    assert rig.states[-1] == ("IDLE", "Đã hủy")
    assert rig.executor.shutdown_called
    assert rig.speaker.stops >= 1
    rig.controller.toggle_listen()
    assert rig.recorder.calls == 1


def test_replaced_transcriber_is_closed_at_the_swap_when_idle(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    old = ClosableTranscriber()
    rig = build_rig(transcriber=old)
    new = ClosableTranscriber()
    rig.controller.apply_settings(AppConfig(), rig.recorder, new)

    assert old.closed == 1
    assert new.closed == 0
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)
    assert new.valid == [True]
    assert old.valid == []
    assert new.closed == 0


def test_transcriber_swap_waits_for_the_next_turn_while_a_turn_is_active(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    old = ClosableTranscriber()
    rig = build_rig(transcriber=old)
    rig.recorder.wait_for_stop = True
    rig.controller.toggle_listen()
    qtbot.waitUntil(rig.recorder.started.is_set, timeout=5000)

    new = ClosableTranscriber()
    rig.controller.apply_settings(AppConfig(), rig.recorder, new)
    assert old.closed == 0

    rig.recorder.wait_for_stop = False
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)
    assert old.closed == 0
    assert old.valid == [True]

    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig, count=2)
    assert old.closed == 1
    assert new.valid == [True]
    assert new.closed == 0


def test_settings_superseded_before_they_applied_close_their_transcriber(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    rig = build_rig()
    rig.recorder.wait_for_stop = True
    rig.controller.toggle_listen()
    qtbot.waitUntil(rig.recorder.started.is_set, timeout=5000)

    first, second = ClosableTranscriber(), ClosableTranscriber()
    rig.controller.apply_settings(AppConfig(), rig.recorder, first)
    rig.controller.apply_settings(AppConfig(), rig.recorder, second)

    assert first.closed == 1
    assert second.closed == 0


def test_shutdown_closes_the_active_and_the_pending_transcriber(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    old = ClosableTranscriber()
    rig = build_rig(transcriber=old)
    rig.recorder.wait_for_stop = True
    rig.controller.toggle_listen()
    qtbot.waitUntil(rig.recorder.started.is_set, timeout=5000)
    pending = ClosableTranscriber()
    rig.controller.apply_settings(AppConfig(), rig.recorder, pending)

    rig.controller.shutdown()

    assert pending.closed == 1
    assert old.closed == 1


def test_transcriber_without_close_is_swapped_without_error(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    rig = build_rig()  # FakeTranscriber has no close()
    new = ClosableTranscriber()
    rig.controller.apply_settings(AppConfig(), rig.recorder, new)
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)
    assert new.valid == [True]


def test_failing_close_is_logged_and_does_not_break_the_swap(
    build_rig: Callable[..., Rig], qtbot: QtBot, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="voicepilot.controller")
    old = ClosableTranscriber(close_error=RuntimeError("pool busy"))
    rig = build_rig(transcriber=old)
    new = ClosableTranscriber()
    rig.controller.apply_settings(AppConfig(), rig.recorder, new)

    assert "closing transcriber failed: RuntimeError" in caplog.text
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)
    assert new.valid == [True]


# --- no captured window (hwnd=0) ------------------------------------------------------------

NO_WINDOW_CTX = WindowContext(hwnd=0, pid=0, process_name="", title="", app_kind=AppKind.NONE, browser=None)


def test_no_captured_window_leaves_the_decision_to_the_executor(
    build_rig: Callable[..., Rig], qtbot: QtBot
) -> None:
    rig = build_rig()
    rig.context.ctx = NO_WINDOW_CTX
    rig.context.still_target = False  # a target re-check would refuse; the executor must be reached
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    assert len(rig.executor.calls) == 1
    assert rig.executor.calls[0].intent is Intent.PPT_NEXT_SLIDE
    assert rig.results[-1].status is ResultStatus.OK


class _RecordingKeys:
    def __init__(self) -> None:
        self.hotkeys: list[tuple[str, ...]] = []
        self.typed: list[str] = []

    def send_hotkey(self, *keys: str) -> None:
        self.hotkeys.append(keys)

    def type_unicode(self, text: str, *, word: bool = False) -> None:
        self.typed.append(text)


class _FakePowerPoint:
    def shutdown(self) -> None:
        """Nothing to stop."""


def _run_with_windows_executor(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str, mode: Mode
) -> tuple[list[ActionResult], _RecordingKeys, list[str]]:
    """Run one turn through the real WindowsExecutor, with fake keys and browser launch."""
    keys = _RecordingKeys()
    launched: list[str] = []
    monkeypatch.setattr(browser, "launch_url", lambda url, ctx: launched.append(url))
    context = FakeContext()
    context.ctx = NO_WINDOW_CTX
    context.still_target = False
    config = load_config(tmp_path / "missing.toml")  # bundled defaults, including the aliases
    transcriber = FakeTranscriber()
    transcriber.text = text
    controller = Controller(
        config,
        FakeRecorder(),
        transcriber,
        RuleParser(config.website_aliases, config.app_aliases),
        WindowsExecutor(config, context, powerpoint=_FakePowerPoint(), keys=keys),
        FakeSpeaker(),
        context,
        pool=ThreadPoolExecutor(max_workers=2),
        hold_ms=5,
    )
    results: list[ActionResult] = []
    controller.result_ready.connect(results.append)
    try:
        if mode is Mode.DICTATION:
            controller.toggle_mode()
        controller.toggle_listen()
        qtbot.waitUntil(lambda: len(results) == 1, timeout=5000)
    finally:
        controller.shutdown()
    return results, keys, launched


def test_lenient_command_runs_through_the_real_executor_without_a_window(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results, _, launched = _run_with_windows_executor(
        qtbot, monkeypatch, tmp_path, "mở youtube", Mode.COMMAND
    )

    assert results[0].status is ResultStatus.OK
    assert launched == ["https://www.youtube.com"]


def test_dictation_without_a_window_types_nothing(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    results, keys, _ = _run_with_windows_executor(qtbot, monkeypatch, tmp_path, "xin chào", Mode.DICTATION)

    assert results[0].status is ResultStatus.NOT_AVAILABLE
    assert results[0].error_code == "no_target"
    assert keys.typed == []


@pytest.mark.parametrize(
    ("url", "valid"),
    [("https://claude.ai", True), ("https://club.vn", False)],
)
def test_bare_domain_site_is_a_weak_match_so_an_alias_from_the_other_backend_wins(
    build_rig: Callable[..., Rig], qtbot: QtBot, url: str, valid: bool
) -> None:
    # Real Windows: Google heard "mở cờ lốt" as "mở club.vn" and won over Gipformer's alias match.
    rig = build_rig(config=AppConfig(website_aliases={"cờ lốt": "https://claude.ai"}))
    rig.parser.COMMANDS = {
        **FakeParser.COMMANDS,
        "mở site": Command(Intent.BROWSER_OPEN_SITE, "mở site", params={"url": url, "site": "x"}),
    }
    rig.transcriber.text = "mở site"
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)

    assert rig.transcriber.valid == [valid]
    assert [call.params["url"] for call in rig.executor.calls] == [url]



def test_parser_update_waits_for_next_turn_and_old_turn_keeps_snapshot(build_rig, qtbot):
    rig = build_rig()
    rig.recorder.wait_for_stop = True
    rig.controller.toggle_listen()
    qtbot.waitUntil(rig.recorder.started.is_set, timeout=5000)
    from unittest.mock import Mock
    parser = Mock()
    parser.parse.return_value = Command(Intent.BROWSER_SEARCH, "slide tiếp", params={"query": "custom"})
    rig.controller.apply_settings(AppConfig(), rig.recorder, rig.transcriber, parser=parser)
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig)
    assert rig.executor.calls[0].intent is Intent.PPT_NEXT_SLIDE
    parser.parse.assert_not_called()
    rig.recorder.wait_for_stop = False
    rig.controller.toggle_listen()
    wait_for_idle(qtbot, rig, count=2)
    assert rig.executor.calls[1].intent is Intent.BROWSER_SEARCH
