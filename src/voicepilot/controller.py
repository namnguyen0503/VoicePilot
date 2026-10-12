"""Turn orchestration: hotkey, capture, STT, parse, execute, speak, with one-shot turn IDs.

The controller lives on the Qt main thread. Each turn runs its blocking pipeline
(record, transcribe, parse, execute) on a worker thread. The worker reports back
only through queued Qt signals, so widgets are never touched off the GUI thread.
Results carry the turn ID they belong to; anything that arrives for a turn that
was cancelled or already decided is dropped on the main thread.
"""

from __future__ import annotations

import collections
import concurrent.futures
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from voicepilot import debuglog
from voicepilot.config import AppConfig
from voicepilot.contracts import (
    ActionResult,
    AppKind,
    AudioClip,
    AudioDeviceError,
    Command,
    ContextProvider,
    Executor,
    Intent,
    IntentParser,
    Mode,
    ResultStatus,
    Speaker,
    SttError,
    Transcript,
    WindowContext,
)
from voicepilot.intent.validation import is_valid_candidate

log = logging.getLogger("voicepilot.controller")

LEVEL_MIN_INTERVAL_S = 0.05  # about 20 level updates per second
DEFAULT_HOLD_MS = 1500  # how long DONE/ERROR stays visible before returning to IDLE

MSG_LISTENING = "Đang nghe…"
MSG_RECOGNIZING = "Đang nhận dạng…"
MSG_EXECUTING = "Đang thực hiện…"
MSG_CANCELLED = "Đã hủy"
MSG_NO_MIC = "Không tìm thấy micro"
MSG_NO_SPEECH = "Không nghe thấy gì"
MSG_STT_FAILED = "Không nhận dạng được, hãy thử lại"
MSG_EXEC_FAILED = "Không thực hiện được lệnh"
MSG_INTERNAL = "Có lỗi nội bộ, hãy thử lại"
MSG_UNKNOWN = "Chưa hiểu lệnh"
MSG_TARGET_CHANGED = "Cửa sổ đích đã thay đổi, hãy thử lại."


class State(Enum):
    """Controller state. DONE and ERROR are transient and return to IDLE after a short hold."""

    IDLE = "IDLE"
    LISTENING = "LISTENING"
    RECOGNIZING = "RECOGNIZING"
    EXECUTING = "EXECUTING"
    DONE = "DONE"
    ERROR = "ERROR"


_STAGE_MESSAGES = {
    State.LISTENING: MSG_LISTENING,
    State.RECOGNIZING: MSG_RECOGNIZING,
    State.EXECUTING: MSG_EXECUTING,
}


class Recorder(Protocol):
    """Captures one utterance. Implemented by audio.capture.Recorder."""

    def record(
        self,
        turn_id: int,
        mode: Mode,
        stop_event: threading.Event,
        cancel_event: threading.Event,
    ) -> AudioClip | None:
        """Blocking capture; None when cancelled or when no speech was detected."""
        ...


class Transcriber(Protocol):
    """Turns a clip into text. Implemented by stt.hybrid.HybridTranscriber."""

    def transcribe(
        self,
        clip: AudioClip,
        is_valid: Callable[[Transcript], bool],
        cancel_event: threading.Event,
    ) -> Transcript:
        """Return a transcript; raises SttError when nothing usable is produced."""
        ...


def target_label(ctx: WindowContext) -> str:
    """Short name of the target application shown in the overlay."""
    if ctx.app_kind is AppKind.POWERPOINT:
        return "PowerPoint"
    if ctx.app_kind is AppKind.WORD:
        return "Word"
    if ctx.app_kind is AppKind.EXCEL:
        return "Excel"
    if ctx.app_kind is AppKind.BROWSER:
        return {"edge": "Edge", "chrome": "Chrome"}.get(ctx.browser or "", "Trình duyệt")
    return "Windows"


def _empty_context() -> WindowContext:
    return WindowContext(hwnd=0, pid=0, process_name="", title="", app_kind=AppKind.NONE, browser=None)


def _fmt_ms(value: float | None) -> str:
    return "-" if value is None else str(round(value))


def _close_quietly(transcriber: object) -> None:
    """Call close() on a transcriber when it has one. Objects without close() are left alone."""
    close = getattr(transcriber, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception as exc:
        log.warning("closing transcriber failed: %s", type(exc).__name__)


@dataclass
class _Turn:
    """Main-thread bookkeeping for the active turn. Components are bound at turn start."""

    turn_id: int
    mode: Mode
    context: WindowContext
    recorder: Recorder
    transcriber: Transcriber
    started: float
    parser: IntentParser | None = None
    config: AppConfig | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    cancel_event: threading.Event = field(default_factory=threading.Event)
    state: State = State.LISTENING
    cancelled: bool = False
    mode_lock: threading.Lock = field(default_factory=threading.Lock)
    input_frozen: bool = False
    text: str | None = None


@dataclass(frozen=True)
class _Heard:
    """Speech-side facts about a turn, carried into its outcome."""

    speech_end: float
    record_ms: float | None = None
    stt_ms: float | None = None
    transcript: str = ""
    source: str = "none"


@dataclass(frozen=True)
class _Outcome:
    """What the worker reports when a turn reaches a result."""

    turn_id: int
    mode: Mode
    result: ActionResult
    speech: str | None
    transcript: str
    source: str
    intent: str
    speech_end: float
    record_ms: float | None
    stt_ms: float | None
    exec_ms: float | None


@dataclass(frozen=True)
class _Components:
    """Settings-dependent components waiting to be applied at the next turn start."""

    config: AppConfig
    recorder: Recorder
    transcriber: Transcriber
    parser: IntentParser | None = None


class Controller(QObject):
    """Runs voice turns and publishes their progress to the GUI.

    Signals are always emitted on the main thread. Components are injected so
    tests can pass fakes; no hardware is touched by this class directly.
    """

    state_changed = Signal(str, str)  # state name, Vietnamese message
    level_changed = Signal(float)
    transcript_changed = Signal(str)
    mode_changed = Signal(object)  # Mode
    target_changed = Signal(str)
    result_ready = Signal(object)  # ActionResult

    # Worker-to-main-thread channels. Emitted from worker threads, delivered queued.
    _stage = Signal(int, str)
    _transcript_ready = Signal(int, str)
    _finished = Signal(object)

    def __init__(
        self,
        config: AppConfig,
        recorder: Recorder,
        transcriber: Transcriber,
        parser: IntentParser,
        executor: Executor,
        speaker: Speaker,
        context_provider: ContextProvider,
        *,
        pool: concurrent.futures.Executor | None = None,
        clock: Callable[[], float] = time.monotonic,
        hold_ms: int = DEFAULT_HOLD_MS,
        parent: QObject | None = None,
    ) -> None:
        """Wire the components together. The controller takes ownership of pool when given."""
        super().__init__(parent)
        self._config = config
        self._recorder = recorder
        self._transcriber = transcriber
        self._parser = parser
        self._executor = executor
        self._speaker = speaker
        self._context_provider = context_provider
        self._clock = clock
        self._pool: concurrent.futures.Executor = (
            pool
            if pool is not None
            else concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="voicepilot-turn")
        )
        self._input_channel = "voice"
        self._record_lock = threading.Lock()
        self._mode = Mode.COMMAND
        self._state = State.IDLE
        self._turn: _Turn | None = None
        self._turn_seq = 0
        self._pending: _Components | None = None
        self._closed = False
        self._last_future: concurrent.futures.Future[None] | None = None
        self._last_level_at = float("-inf")
        self._history: collections.deque[tuple[float, Mode, str, str, str]] = collections.deque(
            maxlen=config.history_size
        )

        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.setInterval(hold_ms)
        self._hold_timer.timeout.connect(self._on_hold_elapsed)

        self._stage.connect(self._on_stage, Qt.ConnectionType.QueuedConnection)
        self._transcript_ready.connect(self._on_transcript_ready, Qt.ConnectionType.QueuedConnection)
        self._finished.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)

    # ----- public API (main thread) -------------------------------------------------

    @property
    def state(self) -> State:
        """Current controller state."""
        return self._state

    @property
    def mode(self) -> Mode:
        """Current interaction mode."""
        return self._mode

    @property
    def history(self) -> tuple[tuple[float, Mode, str, str, str], ...]:
        """Recent turns as (clock time, mode, text, intent, status). Kept in RAM only."""
        return tuple(self._history)

    def toggle_listen(self) -> None:
        """Start a turn when idle, or end the current listening phase early."""
        if self._closed or self._input_channel != "voice":
            return
        turn = self._turn
        if turn is None:
            self._start_turn()
        elif turn.state is State.LISTENING:
            turn.stop_event.set()
        # RECOGNIZING and EXECUTING ignore the hotkey; the turn finishes on its own.

    def cancel(self) -> None:
        """Abandon the active turn. Whatever its worker produces later is dropped."""
        turn = self._turn
        if turn is None:
            return
        turn.cancelled = True
        turn.cancel_event.set()
        turn.stop_event.set()
        self._turn = None
        self._hold_timer.stop()
        self._log_turn(
            turn.turn_id,
            turn.mode,
            source="none",
            status="cancelled",
            error_code=None,
            record_ms=None,
            stt_ms=None,
            exec_ms=None,
            total_ms=(self._clock() - turn.started) * 1000,
        )
        self._set_state(State.IDLE, MSG_CANCELLED)

    @property
    def input_channel(self) -> str:
        """Selected input channel; text selection blocks microphone starts."""
        return self._input_channel

    def set_input_channel(self, channel: str) -> None:
        """Cancel the old channel before enabling another one."""
        if channel not in {"voice", "text"}:
            raise ValueError("Unknown input channel")
        if self._closed or channel == self._input_channel:
            return
        self.cancel()
        self._safe_speaker_call(self._speaker.stop, "stop")
        self._input_channel = channel

    def toggle_mode(self) -> None:
        """Keep captured audio when switching during capture; cancel frozen work."""
        if self._closed:
            return
        new_mode = Mode.DICTATION if self._mode is Mode.COMMAND else Mode.COMMAND
        turn = self._turn
        if turn is not None:
            with turn.mode_lock:
                frozen = turn.input_frozen
                if not frozen:
                    turn.mode = new_mode
            if frozen:
                self.cancel()
        self._mode = new_mode
        self.mode_changed.emit(self._mode)

    def submit_text(self, text: str, context: WindowContext) -> bool:
        """Submit one typed turn. Never opens the microphone or a speech backend."""
        if self._closed or self._input_channel != "text" or self._turn is not None or not text.strip():
            return False
        self._start_turn(text=text, context=context)
        return True

    def on_audio_level(self, level: float) -> None:
        """Show a microphone level while listening, throttled to about 20 Hz.

        Connect the recorder's level relay to this slot with a queued connection so
        it always runs on the main thread.
        """
        turn = self._turn
        if turn is None or turn.state is not State.LISTENING:
            return
        now = self._clock()
        if now - self._last_level_at < LEVEL_MIN_INTERVAL_S:
            return
        self._last_level_at = now
        self.level_changed.emit(min(1.0, max(0.0, float(level))))

    def apply_settings(
        self,
        config: AppConfig,
        recorder: Recorder,
        transcriber: Transcriber,
        *,
        parser: IntentParser | None = None,
    ) -> None:
        """Use new settings and components. Applied now when idle, else at the next turn start.

        A transcriber that is swapped out is closed at the swap. One that is superseded
        before it was ever applied is closed here.
        """
        if self._closed:
            _close_quietly(transcriber)
            return
        superseded = self._pending.transcriber if self._pending is not None else None
        self._pending = _Components(config, recorder, transcriber, parser)
        if superseded is not None and superseded is not transcriber and superseded is not self._transcriber:
            _close_quietly(superseded)
        if self._turn is None:
            self._apply_pending()

    def shutdown(self) -> None:
        """Cancel the active turn, stop speech, release executor, transcriber and worker threads."""
        if self._closed:
            return
        self._closed = True
        self.cancel()
        self._hold_timer.stop()
        self._safe_speaker_call(self._speaker.stop, "stop")
        self._pool.shutdown(wait=False, cancel_futures=True)
        if self._last_future is not None:
            concurrent.futures.wait([self._last_future], timeout=2.0)
        shutdown_executor = getattr(self._executor, "shutdown", None)
        if callable(shutdown_executor):
            shutdown_executor()
        if self._pending is not None:
            _close_quietly(self._pending.transcriber)
            self._pending = None
        _close_quietly(self._transcriber)

    # ----- turn start ---------------------------------------------------------------

    def _start_turn(self, *, text: str | None = None, context: WindowContext | None = None) -> None:
        self._hold_timer.stop()
        self._apply_pending()
        self._safe_speaker_call(self._speaker.stop, "stop")
        ctx = context if context is not None else self._capture_context()
        self._turn_seq += 1
        turn = _Turn(
            turn_id=self._turn_seq,
            mode=self._mode,
            context=ctx,
            recorder=self._recorder,
            transcriber=self._transcriber,
            started=self._clock(),
            parser=self._parser,
            config=self._config,
            text=text,
            input_frozen=text is not None,
        )
        self._turn = turn
        self.transcript_changed.emit("")
        self.target_changed.emit(target_label(ctx))
        self._set_state(State.LISTENING if text is None else State.EXECUTING,
                        MSG_LISTENING if text is None else MSG_EXECUTING)
        self._last_future = self._pool.submit(self._run_turn, turn)

    def _apply_pending(self) -> None:
        pending = self._pending
        if pending is None:
            return
        self._pending = None
        previous = self._transcriber
        self._config = pending.config
        if self._history.maxlen != pending.config.history_size:
            self._history = collections.deque(self._history, maxlen=pending.config.history_size)
        self._recorder = pending.recorder
        self._transcriber = pending.transcriber
        if pending.parser is not None:
            self._parser = pending.parser
        if previous is not pending.transcriber:
            # A cancelled turn may still be inside the old transcriber; close() lets that call finish alone.
            _close_quietly(previous)

    def _capture_context(self) -> WindowContext:
        try:
            return self._context_provider.capture()
        except Exception as exc:
            log.warning("context capture failed: %s", type(exc).__name__)
            return _empty_context()

    # ----- worker thread ------------------------------------------------------------

    def _run_turn(self, turn: _Turn) -> None:
        """Worker entry point. Reports through signals only and never raises."""
        try:
            outcome = self._pipeline(turn)
        except Exception as exc:  # a worker must never let an exception escape
            log.error("turn id=%d internal error=%s", turn.turn_id, type(exc).__name__)
            outcome = self._outcome(
                turn,
                ActionResult(ResultStatus.ERROR, MSG_INTERNAL, "internal"),
                _Heard(speech_end=self._clock()),
            )
        if outcome is not None:
            self._finished.emit(outcome)

    def _pipeline(self, turn: _Turn) -> _Outcome | None:
        """Run one turn. Returns None when the turn was cancelled and nothing should be reported."""
        now = self._clock
        if turn.text is not None:
            heard = _Heard(speech_end=now(), transcript=turn.text, source="text")
            self._transcript_ready.emit(turn.turn_id, turn.text)
            return self._execute_text(turn, heard)
        t_start = now()
        try:
            # A cancelled capture may still be closing its stream when another turn starts.
            with self._record_lock:
                if turn.cancel_event.is_set():
                    return None
                live_record = getattr(turn.recorder, "record_live", None)
                if callable(live_record):
                    clip = live_record(turn.turn_id, lambda: turn.mode, turn.stop_event, turn.cancel_event)
                else:
                    clip = turn.recorder.record(turn.turn_id, turn.mode, turn.stop_event, turn.cancel_event)
        except AudioDeviceError:
            return self._outcome(
                turn, ActionResult(ResultStatus.ERROR, MSG_NO_MIC, "audio_device"), _Heard(speech_end=now())
            )
        speech_end = now()
        record_ms = (speech_end - t_start) * 1000
        if turn.cancel_event.is_set():
            return None
        if clip is None:
            return self._outcome(
                turn,
                ActionResult(ResultStatus.ERROR, MSG_NO_SPEECH, "no_speech"),
                _Heard(speech_end=speech_end, record_ms=record_ms),
            )
        debuglog.save_clip(turn.turn_id, clip.samples, clip.sample_rate)

        with turn.mode_lock:
            if turn.cancel_event.is_set():
                return None
            turn.input_frozen = True
        self._stage.emit(turn.turn_id, State.RECOGNIZING.value)
        t_stt = now()
        try:
            transcriber = turn.transcriber
            select_mode = getattr(transcriber, "for_mode", None)
            if callable(select_mode):
                transcriber = select_mode(turn.mode)
            transcript = transcriber.transcribe(clip, self._validator(turn), turn.cancel_event)
        except SttError:
            if turn.cancel_event.is_set():
                return None
            return self._outcome(
                turn,
                ActionResult(ResultStatus.ERROR, MSG_STT_FAILED, "stt_failed"),
                _Heard(speech_end=speech_end, record_ms=record_ms, stt_ms=(now() - t_stt) * 1000),
            )
        heard = _Heard(
            speech_end=speech_end,
            record_ms=record_ms,
            stt_ms=(now() - t_stt) * 1000,
            transcript=transcript.text,
            source=transcript.source,
        )
        text = transcript.text
        self._transcript_ready.emit(turn.turn_id, text)

        return self._execute_text(turn, heard)

    def _execute_text(self, turn: _Turn, heard: _Heard) -> _Outcome | None:
        """Shared typed/recognized-text path with identical parse, cancel and focus guards."""
        now = self._clock
        text = heard.transcript
        if turn.cancel_event.is_set():
            return None
        if turn.mode is Mode.DICTATION:
            if not text.strip():
                no_speech = ActionResult(ResultStatus.ERROR, MSG_NO_SPEECH, "no_speech")
                return self._outcome(turn, no_speech, heard)
            command = Command(Intent.DICTATE_TEXT, text, params={"text": text})
        else:
            command = self._parse(text, turn.context, turn.parser)
            debuglog.debug_log.info(
                "turn id=%d heard=%r source=%s app=%s -> intent=%s params=%r missing=%r",
                turn.turn_id,
                text,
                heard.source,
                turn.context.app_kind.value,
                command.intent.value,
                dict(command.params),
                command.missing,
            )
            if not command.is_executable:
                code = "missing_param" if command.missing else "unknown_command"
                hint = command.example
                message = f"{MSG_UNKNOWN}. Thử nói: {hint}" if hint else MSG_UNKNOWN
                return self._outcome(
                    turn,
                    ActionResult(ResultStatus.INVALID_PARAM, message, code),
                    heard,
                    intent=command.intent.value,
                    spoken=f"{MSG_UNKNOWN}.",
                )

        if turn.cancel_event.is_set():
            return None
        if turn.context.hwnd != 0 and not self._still_target(turn.context):
            # The transcript stays visible so the user can repeat the command in the right window.
            # With no captured window (hwnd=0) there is nothing to re-check: the executor decides,
            # running lenient commands and refusing strict ones such as dictation.
            return self._outcome(
                turn,
                ActionResult(ResultStatus.NEEDS_RETRY, MSG_TARGET_CHANGED, "target_changed"),
                heard,
                intent=command.intent.value,
            )

        self._stage.emit(turn.turn_id, State.EXECUTING.value)
        t_exec = now()
        try:
            result = self._executor.execute(command, turn.context, cancel=turn.cancel_event)
        except Exception as exc:
            log.error("turn id=%d executor error=%s", turn.turn_id, type(exc).__name__)
            result = ActionResult(ResultStatus.ERROR, MSG_EXEC_FAILED, "executor_error")
        return self._outcome(
            turn,
            result,
            heard,
            intent=command.intent.value,
            exec_ms=(now() - t_exec) * 1000,
        )

    def _outcome(
        self,
        turn: _Turn,
        result: ActionResult,
        heard: _Heard,
        *,
        intent: str = "-",
        exec_ms: float | None = None,
        spoken: str | None = None,
    ) -> _Outcome:
        """Build an outcome. Command mode speaks the result unless an explicit spoken text is given."""
        if turn.mode is Mode.DICTATION:
            spoken = None
        elif spoken is None:
            spoken = result.message_vi
        return _Outcome(
            turn_id=turn.turn_id,
            mode=turn.mode,
            result=result,
            speech=spoken,
            transcript=heard.transcript,
            source=heard.source,
            intent=intent,
            speech_end=heard.speech_end,
            record_ms=heard.record_ms,
            stt_ms=heard.stt_ms,
            exec_ms=exec_ms,
        )

    def _validator(self, turn: _Turn) -> Callable[[Transcript], bool]:
        """Per-mode validity rule the transcriber uses to pick a winning backend result."""
        if turn.mode is Mode.DICTATION:
            return lambda transcript: bool(transcript.text.strip())

        known_sites = frozenset((turn.config or self._config).website_aliases.values())

        def is_valid(transcript: Transcript) -> bool:
            command = self._parse(transcript.text, turn.context, turn.parser)
            return is_valid_candidate(command, turn.context, known_sites)

        return is_valid

    def _parse(self, text: str, ctx: WindowContext, parser: IntentParser | None = None) -> Command:
        try:
            return (parser or self._parser).parse(text, ctx, Mode.COMMAND)
        except Exception as exc:
            log.error("parser error=%s", type(exc).__name__)
            return Command(Intent.UNKNOWN, text)

    def _still_target(self, ctx: WindowContext) -> bool:
        try:
            return bool(self._context_provider.is_still_target(ctx))
        except Exception as exc:
            log.warning("target check failed: %s", type(exc).__name__)
            return False  # fail closed: do not act on a window we cannot verify

    # ----- main thread: results and state -------------------------------------------

    def _active_turn(self, turn_id: int) -> _Turn | None:
        turn = self._turn
        if turn is None or turn.cancelled or turn.turn_id != turn_id:
            return None
        return turn

    def _on_stage(self, turn_id: int, state_value: str) -> None:
        turn = self._active_turn(turn_id)
        if turn is None:
            return
        turn.state = State(state_value)
        self._set_state(turn.state, _STAGE_MESSAGES[turn.state])

    def _on_transcript_ready(self, turn_id: int, text: str) -> None:
        if self._active_turn(turn_id) is not None:
            self.transcript_changed.emit(text)

    def _on_finished(self, outcome: _Outcome) -> None:
        turn = self._active_turn(outcome.turn_id)
        if turn is None:
            return  # late result for a cancelled or already decided turn
        self._turn = None
        result = outcome.result
        state = State.ERROR if result.status is ResultStatus.ERROR else State.DONE
        self._history.append(
            (self._clock(), outcome.mode, outcome.transcript, outcome.intent, result.status.value)
        )
        if outcome.speech and self._config.tts_enabled:
            spoken = outcome.speech
            self._safe_speaker_call(lambda: self._speaker.speak(spoken), "speak")
        self._log_turn(
            outcome.turn_id,
            outcome.mode,
            source=outcome.source,
            status=result.status.value,
            error_code=result.error_code,
            record_ms=outcome.record_ms,
            stt_ms=outcome.stt_ms,
            exec_ms=outcome.exec_ms,
            total_ms=(self._clock() - outcome.speech_end) * 1000,
        )
        self.result_ready.emit(result)
        self._set_state(state, result.message_vi)
        self._hold_timer.start()

    def _on_hold_elapsed(self) -> None:
        if self._turn is None and self._state in (State.DONE, State.ERROR):
            self._set_state(State.IDLE, "")

    def _set_state(self, state: State, message: str) -> None:
        self._state = state
        self.state_changed.emit(state.value, message)

    # ----- helpers ------------------------------------------------------------------

    def _safe_speaker_call(self, call: Callable[[], None], action: str) -> None:
        try:
            call()
        except Exception as exc:
            log.warning("speaker %s failed: %s", action, type(exc).__name__)

    def _log_turn(
        self,
        turn_id: int,
        mode: Mode,
        *,
        source: str,
        status: str,
        error_code: str | None,
        record_ms: float | None,
        stt_ms: float | None,
        exec_ms: float | None,
        total_ms: float,
    ) -> None:
        """One log line per turn. Never includes transcripts, file names or queries."""
        log.info(
            "turn id=%d mode=%s stt=%s record_ms=%s stt_ms=%s exec_ms=%s total_ms=%s status=%s error=%s",
            turn_id,
            mode.value,
            source,
            _fmt_ms(record_ms),
            _fmt_ms(stt_ms),
            _fmt_ms(exec_ms),
            _fmt_ms(total_ms),
            status,
            error_code or "-",
        )
