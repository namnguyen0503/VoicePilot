"""Black-box QA tests for Controller with fake components (PLAN.md "Hiểu lệnh", "GUI").

Run with QT_QPA_PLATFORM=offscreen. Every test waits on public state, signals or fake calls.
"""

from __future__ import annotations

import logging
import random
import threading
import time

import pytest

from voicepilot.config import AppConfig, load_config
from voicepilot.contracts import (
    ActionResult,
    AppKind,
    AudioClip,
    AudioDeviceError,
    Intent,
    Mode,
    ResultStatus,
    SttError,
    Transcript,
    WindowContext,
)
from voicepilot.controller import MSG_UNKNOWN, Controller, State
from voicepilot.intent.parser import RuleParser
from voicepilot.windows.context import none_context, same_target

POWERPOINT_CTX = WindowContext(
    hwnd=11,
    pid=22,
    process_name="POWERPNT.EXE",
    title="Bai.pptx - PowerPoint",
    app_kind=AppKind.POWERPOINT,
    browser=None,
)


class Recorder:
    def __init__(self, *, clip: bool = True, hold: bool = False, raise_exc: Exception | None = None) -> None:
        self.calls = 0
        self.clip = clip
        self.hold = hold
        self.raise_exc = raise_exc

    def record(self, turn_id, mode, stop_event, cancel_event):
        self.calls += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.hold:
            deadline = time.monotonic() + 5
            while not (stop_event.is_set() or cancel_event.is_set()) and time.monotonic() < deadline:
                time.sleep(0.002)
        if cancel_event.is_set() or not self.clip:
            return None
        return AudioClip(samples=b"\x00\x00" * 1600, turn_id=turn_id)


class Transcriber:
    """Each call pops one scripted step; once the script is empty, default_text is returned."""

    def __init__(self, *steps: dict, default_text: str = "mở youtube") -> None:
        self.steps = list(steps)
        self.default_text = default_text
        self.calls = 0
        self.closed = False

    def transcribe(self, clip, is_valid, cancel_event):
        self.calls += 1
        step = self.steps.pop(0) if self.steps else {}
        if step.get("gate") is not None:
            step["gate"].wait(5)
        if step.get("raise") is not None:
            raise step["raise"]
        return Transcript(
            text=step.get("text", self.default_text), source="fake", latency_ms=1.0, turn_id=clip.turn_id
        )

    def close(self):
        self.closed = True


class Parser:
    def __init__(self, raise_exc: Exception | None = None) -> None:
        self._real = None
        self._raise = raise_exc

    def parse(self, text, context, mode):
        if self._raise is not None:
            raise self._raise
        return self._real.parse(text, context, mode)


class Executor:
    def __init__(self, *, gate: threading.Event | None = None, raise_exc: Exception | None = None) -> None:
        self.calls = []
        self.gate = gate
        self.raise_exc = raise_exc
        self.shut_down = False

    def execute(self, command, context, *, cancel=None):
        self.calls.append(command)
        if self.gate is not None:
            self.gate.wait(5)
        if self.raise_exc is not None:
            raise self.raise_exc
        return ActionResult(ResultStatus.OK, "Đã thực hiện")

    def shutdown(self):
        self.shut_down = True


class Speaker:
    def __init__(self, raise_exc: Exception | None = None) -> None:
        self.spoken: list[str] = []
        self.stops = 0
        self.raise_exc = raise_exc

    def speak(self, text):
        if self.raise_exc is not None:
            raise self.raise_exc
        self.spoken.append(text)

    def stop(self):
        self.stops += 1


class Context:
    def __init__(
        self,
        ctx: WindowContext = POWERPOINT_CTX,
        *,
        still: bool = True,
        capture_exc: Exception | None = None,
        still_exc: Exception | None = None,
    ) -> None:
        self.ctx = ctx
        self.still = still
        self.capture_exc = capture_exc
        self.still_exc = still_exc

    def capture(self):
        if self.capture_exc is not None:
            raise self.capture_exc
        return self.ctx

    def is_still_target(self, ctx):
        if self.still_exc is not None:
            raise self.still_exc
        return self.still


@pytest.fixture
def make(qapp, config_home):
    created: list[Controller] = []
    cfg_defaults = load_config()

    def factory(
        *,
        config=None,
        recorder=None,
        transcriber=None,
        executor=None,
        speaker=None,
        context=None,
        parser=None,
        hold_ms: int = 10,
    ) -> Controller:
        cfg = config or cfg_defaults
        if parser is None:
            real = RuleParser(cfg.website_aliases, cfg.app_aliases)
            parser = Parser()
            parser._real = real
        ctrl = Controller(
            cfg,
            recorder or Recorder(),
            transcriber or Transcriber(),
            parser,
            executor or Executor(),
            speaker or Speaker(),
            context or Context(),
            hold_ms=hold_ms,
        )
        created.append(ctrl)
        return ctrl

    yield factory
    for ctrl in created:
        ctrl.shutdown()


class Watch:
    """Collects controller signals on the main thread."""

    def __init__(self, ctrl: Controller) -> None:
        self.states: list[str] = []
        self.results: list[ActionResult] = []
        self.transcripts: list[str] = []
        self.modes: list[Mode] = []
        ctrl.state_changed.connect(lambda s, _m: self.states.append(s))
        ctrl.result_ready.connect(lambda r: self.results.append(r))
        ctrl.transcript_changed.connect(lambda t: self.transcripts.append(t))
        ctrl.mode_changed.connect(lambda m: self.modes.append(m))


def wait_idle(qtbot, ctrl: Controller, timeout: int = 5000) -> None:
    qtbot.waitUntil(lambda: ctrl.state is State.IDLE, timeout=timeout)


def wait_state(qtbot, ctrl: Controller, state: State, timeout: int = 5000) -> None:
    qtbot.waitUntil(lambda: ctrl.state is state, timeout=timeout)


def settle(qtbot, ms: int = 150) -> None:
    qtbot.wait(ms)


# --- basic turn ------------------------------------------------------------------------------


def test_command_turn_executes_once_and_speaks_result(qtbot, make):
    executor, speaker = Executor(), Speaker()
    ctrl = make(
        executor=executor,
        speaker=speaker,
        transcriber=Transcriber(default_text="mở youtube"),
        context=Context(POWERPOINT_CTX),
    )
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert len(executor.calls) == 1
    assert executor.calls[0].intent is Intent.BROWSER_OPEN_SITE
    assert [r.status for r in watch.results] == [ResultStatus.OK]
    assert speaker.spoken == ["Đã thực hiện"]


def test_unknown_command_is_not_executed_and_suggests_example(qtbot, make):
    executor, speaker = Executor(), Speaker()
    ctrl = make(executor=executor, speaker=speaker, transcriber=Transcriber(default_text="xyzzy plugh"))
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert executor.calls == []
    assert watch.results[0].status is ResultStatus.INVALID_PARAM
    assert watch.results[0].error_code == "unknown_command"
    assert "Thử nói:" in watch.results[0].message_vi
    assert speaker.spoken and speaker.spoken[0].startswith(MSG_UNKNOWN)


def test_missing_parameter_is_reported_not_executed(qtbot, make):
    executor = Executor()
    ctrl = make(executor=executor, transcriber=Transcriber(default_text="tìm"))
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert executor.calls == []
    assert watch.results[0].error_code == "missing_param"


# --- hotkey spam and mode toggling -------------------------------------------------------


def test_hotkey_spam_starts_exactly_one_turn(qtbot, make):
    recorder = Recorder(hold=True)
    executor = Executor()
    ctrl = make(recorder=recorder, executor=executor, context=Context(POWERPOINT_CTX))
    watch = Watch(ctrl)
    for _ in range(20):
        ctrl.toggle_listen()  # all twenty presses land before the worker can finish
    wait_idle(qtbot, ctrl)
    settle(qtbot)
    assert recorder.calls == 1
    assert len(watch.results) == 1
    assert len(executor.calls) <= 1


def test_toggle_mode_mid_turn_is_ignored(qtbot, make):
    recorder = Recorder(hold=True)
    ctrl = make(recorder=recorder)
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_state(qtbot, ctrl, State.LISTENING)
    ctrl.toggle_mode()
    assert ctrl.mode is Mode.COMMAND
    assert watch.modes == []
    ctrl.cancel()
    wait_idle(qtbot, ctrl)


def test_dictation_turn_inserts_text_without_running_commands(qtbot, make):
    executor, speaker = Executor(), Speaker()
    ctrl = make(executor=executor, speaker=speaker, transcriber=Transcriber(default_text="lưu"))
    ctrl.toggle_mode()
    assert ctrl.mode is Mode.DICTATION
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert len(executor.calls) == 1
    assert executor.calls[0].intent is Intent.DICTATE_TEXT
    assert executor.calls[0].params == {"text": "lưu"}
    assert speaker.spoken == []  # dictation only reports status


def test_empty_dictation_result_is_not_typed(qtbot, make):
    executor = Executor()
    ctrl = make(executor=executor, transcriber=Transcriber(default_text="   "))
    ctrl.toggle_mode()
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert executor.calls == []


# --- cancellation at every stage ---------------------------------------------------------


def test_cancel_while_listening_skips_stt_and_execution(qtbot, make):
    recorder = Recorder(hold=True)
    transcriber, executor = Transcriber(), Executor()
    ctrl = make(recorder=recorder, transcriber=transcriber, executor=executor)
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_state(qtbot, ctrl, State.LISTENING)
    ctrl.cancel()
    assert ctrl.state is State.IDLE
    settle(qtbot)
    assert transcriber.calls == 0
    assert executor.calls == []
    assert watch.results == []


def test_cancel_while_recognizing_drops_late_result(qtbot, make):
    gate = threading.Event()
    transcriber, executor = Transcriber({"gate": gate, "text": "mở youtube"}), Executor()
    ctrl = make(transcriber=transcriber, executor=executor)
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_state(qtbot, ctrl, State.RECOGNIZING)
    ctrl.cancel()
    gate.set()
    settle(qtbot)
    assert executor.calls == []
    assert watch.results == []
    assert ctrl.state is State.IDLE


def test_cancel_while_executing_drops_result_and_speech(qtbot, make):
    gate = threading.Event()
    executor, speaker = Executor(gate=gate), Speaker()
    ctrl = make(executor=executor, speaker=speaker, transcriber=Transcriber(default_text="mở youtube"))
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_state(qtbot, ctrl, State.EXECUTING)
    ctrl.cancel()
    gate.set()
    settle(qtbot)
    assert len(executor.calls) <= 1
    assert watch.results == []
    assert speaker.spoken == []
    assert ctrl.state is State.IDLE


def test_cancel_when_idle_is_a_no_op(qtbot, make):
    ctrl = make()
    ctrl.cancel()
    ctrl.cancel()
    assert ctrl.state is State.IDLE


def test_late_result_of_cancelled_turn_never_reaches_next_turn(qtbot, make):
    gate = threading.Event()
    transcriber = Transcriber({"gate": gate, "text": "mở facebook"}, default_text="mở youtube")
    executor = Executor()
    ctrl = make(transcriber=transcriber, executor=executor)
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_state(qtbot, ctrl, State.RECOGNIZING)
    ctrl.cancel()
    ctrl.toggle_listen()  # turn 2 starts while turn 1 is still inside the transcriber
    wait_idle(qtbot, ctrl)
    gate.set()  # turn 1 now produces "mở facebook"
    settle(qtbot, 200)
    assert [c.params.get("url") for c in executor.calls] == ["https://www.youtube.com"]
    assert len(watch.results) == 1


# --- failure isolation ---------------------------------------------------------------------


def test_executor_exception_becomes_error_result(qtbot, make):
    ctrl = make(executor=Executor(raise_exc=RuntimeError("COM exploded")))
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert watch.results[0].status is ResultStatus.ERROR
    assert watch.results[0].error_code == "executor_error"


def test_unexpected_transcriber_exception_is_internal_error(qtbot, make):
    executor = Executor()
    ctrl = make(transcriber=Transcriber({"raise": RuntimeError("bad")}), executor=executor)
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert executor.calls == []
    assert watch.results[0].error_code == "internal"


def test_stt_error_is_reported(qtbot, make):
    ctrl = make(transcriber=Transcriber({"raise": SttError("nothing usable")}))
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert watch.results[0].error_code == "stt_failed"


def test_silence_reports_no_speech(qtbot, make):
    ctrl = make(recorder=Recorder(clip=False))
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert watch.results[0].error_code == "no_speech"


def test_missing_microphone_reports_audio_device(qtbot, make):
    transcriber = Transcriber()
    ctrl = make(recorder=Recorder(raise_exc=AudioDeviceError("no mic")), transcriber=transcriber)
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert watch.results[0].error_code == "audio_device"
    assert transcriber.calls == 0


def test_parser_exception_is_treated_as_unknown(qtbot, make):
    executor = Executor()
    ctrl = make(parser=Parser(raise_exc=ValueError("parser bug")), executor=executor)
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert executor.calls == []
    assert watch.results[0].error_code == "unknown_command"


def test_speaker_exception_does_not_break_the_turn(qtbot, make):
    ctrl = make(speaker=Speaker(raise_exc=RuntimeError("no audio device")))
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert [r.status for r in watch.results] == [ResultStatus.OK]


def test_context_capture_exception_still_ends_turn_idle(qtbot, make):
    ctrl = make(context=Context(capture_exc=OSError("win32 failed")))
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert len(watch.results) == 1


def test_target_check_exception_fails_closed(qtbot, make):
    executor = Executor()
    ctrl = make(executor=executor, context=Context(still_exc=OSError("gone")))
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert executor.calls == []
    assert watch.results[0].status is ResultStatus.NEEDS_RETRY


def test_changed_window_keeps_transcript_and_does_not_execute(qtbot, make):
    executor = Executor()
    ctrl = make(
        executor=executor, context=Context(still=False), transcriber=Transcriber(default_text="mở youtube")
    )
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert executor.calls == []
    assert watch.results[0].error_code == "target_changed"
    assert "mở youtube" in watch.transcripts


def test_commands_run_from_any_window_including_no_context(qtbot, make):
    class NoWindowContext:
        def capture(self):
            return none_context()

        def is_still_target(self, ctx):
            # what Win32ContextProvider does with the current foreground window
            return same_target(ctx, 0, 0)

    executor = Executor()
    ctrl = make(
        executor=executor, context=NoWindowContext(), transcriber=Transcriber(default_text="mở youtube")
    )
    watch = Watch(ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    assert len(executor.calls) == 1
    assert watch.results[0].status is ResultStatus.OK


# --- privacy of logs -----------------------------------------------------------------------


def test_logs_never_contain_transcript_or_query_or_filename(qtbot, make, caplog):
    secret_query = "mật khẩu ngân hàng 4321"
    secret_file = "Hợp đồng bí mật"
    caplog.set_level(logging.DEBUG)
    ctrl = make(
        transcriber=Transcriber(
            {"text": f"tìm {secret_query}"},
            {"text": f"lưu thành {secret_file}"},
        ),
        context=Context(POWERPOINT_CTX),
        executor=Executor(raise_exc=RuntimeError("x")),
    )
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    ctrl.toggle_listen()
    wait_idle(qtbot, ctrl)
    text = caplog.text
    for secret in (secret_query, "4321", secret_file, "bí mật", "Hợp đồng"):
        assert secret not in text, secret
    assert "turn id=" in text  # the per-turn line does exist, just without content


# --- lifecycle and bounds --------------------------------------------------------------------


def test_history_is_bounded_to_twenty_entries(qtbot, make):
    ctrl = make(config=load_config(), transcriber=Transcriber(default_text="xin chào"))
    for _ in range(25):
        ctrl.toggle_listen()
        wait_idle(qtbot, ctrl)
    assert len(ctrl.history) == 20


def test_history_respects_configured_size(qtbot, make, config_home):
    cfg = AppConfig(history_size=3)
    ctrl = make(config=cfg, transcriber=Transcriber(default_text="xin chào"))
    for _ in range(5):
        ctrl.toggle_listen()
        wait_idle(qtbot, ctrl)
    assert len(ctrl.history) == 3


def test_shutdown_releases_components_and_stops_further_turns(qtbot, make):
    speaker, executor = Speaker(), Executor()
    transcriber = Transcriber()
    ctrl = make(speaker=speaker, executor=executor, transcriber=transcriber)
    ctrl.shutdown()
    ctrl.shutdown()  # idempotent
    assert speaker.stops >= 1
    assert transcriber.closed
    assert executor.shut_down
    ctrl.toggle_listen()
    settle(qtbot, 50)
    assert transcriber.calls == 0


def test_applying_new_transcriber_closes_the_old_one(qtbot, make):
    old = Transcriber()
    ctrl = make(transcriber=old)
    new = Transcriber()
    ctrl.apply_settings(load_config(), Recorder(), new)
    assert old.closed
    assert not new.closed


def test_random_fifty_turns_with_faults_and_cancels(qtbot, make):
    rng = random.Random(20261009)
    executor = Executor()
    ctrl = make(executor=executor, context=Context(POWERPOINT_CTX))
    baseline_threads = threading.active_count()
    for turn in range(50):
        recorder = Recorder(
            clip=rng.random() > 0.1, raise_exc=AudioDeviceError("x") if rng.random() < 0.05 else None
        )
        steps = {"text": f"tìm giá vàng số {turn}"}
        if rng.random() < 0.15:
            steps = {"raise": SttError("x")}
        if rng.random() < 0.1:
            steps = {"raise": RuntimeError("y")}
        ctrl.apply_settings(
            load_config(), recorder, Transcriber(steps, default_text=f"tìm giá vàng số {turn}")
        )
        ctrl.toggle_listen()
        if rng.random() < 0.3:
            qtbot.wait(int(rng.uniform(0, 4)))
            ctrl.cancel()
        wait_idle(qtbot, ctrl)
    settle(qtbot, 100)
    queries = [c.params.get("query") for c in executor.calls]
    assert len(queries) == len(set(queries)), "a turn was executed more than once"
    assert len(ctrl.history) <= 20
    assert ctrl.state is State.IDLE
    assert threading.active_count() <= baseline_threads + 4
