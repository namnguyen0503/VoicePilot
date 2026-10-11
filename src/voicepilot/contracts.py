"""Shared data types, enums, exceptions and interface protocols for VoicePilot.

This module is the stable contract between the parallel implementation modules
(audio, STT, intent parsing, Windows execution, UI). It must stay free of
third-party and Windows-only imports so that it can be imported on any platform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from threading import Event
from typing import Any, Literal, Protocol

SAMPLE_WIDTH_BYTES = 2  # PCM int16 mono little-endian


class Mode(Enum):
    """Interaction mode: run voice commands or dictate text at the caret."""

    COMMAND = "command"
    DICTATION = "dictation"


class SttBackendMode(Enum):
    """Which speech-to-text backends are used for a turn."""

    HYBRID = "hybrid"
    GOOGLE = "google"
    OFFLINE = "offline"


@dataclass(frozen=True)
class AudioClip:
    """One utterance captured for a single turn.

    Attributes:
        samples: Raw PCM int16 mono little-endian bytes.
        turn_id: Identifier of the turn this clip belongs to.
        sample_rate: Sample rate in Hz (keyword-only, defaults to 16 kHz).
    """

    samples: bytes
    turn_id: int
    sample_rate: int = field(default=16000, kw_only=True)

    @property
    def duration_s(self) -> float:
        """Duration of the clip in seconds."""
        return len(self.samples) / (SAMPLE_WIDTH_BYTES * self.sample_rate)


@dataclass(frozen=True)
class Transcript:
    """Result of a speech-to-text call."""

    text: str
    source: Literal["google", "gipformer", "fake"]
    latency_ms: float
    turn_id: int
    confidence: float | None = None


class AppKind(Enum):
    """Category of the foreground application that the user is working in."""

    POWERPOINT = "powerpoint"
    BROWSER = "browser"
    OTHER = "other"
    NONE = "none"


@dataclass(frozen=True)
class WindowContext:
    """Snapshot of the foreground window taken before the overlay is shown.

    Attributes:
        hwnd: Native window handle.
        pid: Owning process id.
        process_name: Executable name, e.g. "POWERPNT.EXE".
        title: Window title at capture time.
        app_kind: Category derived from the process.
        browser: "edge", "chrome" or None when the window is not a browser.
    """

    hwnd: int
    pid: int
    process_name: str
    title: str
    app_kind: AppKind
    browser: str | None


class Intent(Enum):
    """Every command function supported in v1. Values equal the names used in commands.toml."""

    PPT_SAVE = "PPT_SAVE"
    PPT_SAVE_AS = "PPT_SAVE_AS"
    PPT_NEXT_SLIDE = "PPT_NEXT_SLIDE"
    PPT_PREV_SLIDE = "PPT_PREV_SLIDE"
    PPT_START_SHOW = "PPT_START_SHOW"
    PPT_STOP_SHOW = "PPT_STOP_SHOW"
    BROWSER_OPEN_SITE = "BROWSER_OPEN_SITE"
    BROWSER_SEARCH = "BROWSER_SEARCH"
    BROWSER_NEXT_TAB = "BROWSER_NEXT_TAB"
    BROWSER_PREV_TAB = "BROWSER_PREV_TAB"
    BROWSER_NEW_TAB = "BROWSER_NEW_TAB"
    BROWSER_CLOSE_TAB = "BROWSER_CLOSE_TAB"
    WIN_OPEN_APP = "WIN_OPEN_APP"
    WIN_SCREENSHOT = "WIN_SCREENSHOT"
    DICTATE_TEXT = "DICTATE_TEXT"
    WIN_HOTKEY = "WIN_HOTKEY"  # custom commands only, no bundled spoken pattern
    WIN_POWER_SAVER = "WIN_POWER_SAVER"
    WIN_BRIGHTNESS_DOWN = "WIN_BRIGHTNESS_DOWN"
    WIN_BRIGHTNESS_UP = "WIN_BRIGHTNESS_UP"
    WIN_VOLUME_DOWN = "WIN_VOLUME_DOWN"
    WIN_VOLUME_UP = "WIN_VOLUME_UP"
    MEDIA_PLAY = "MEDIA_PLAY"
    MEDIA_PAUSE = "MEDIA_PAUSE"
    MEDIA_NEXT = "MEDIA_NEXT"
    MEDIA_PREVIOUS = "MEDIA_PREVIOUS"
    UNKNOWN = "UNKNOWN"


# The original fifteen v1 features; user-only actions are tested separately.
SYSTEM_INTENTS = frozenset({
    Intent.WIN_POWER_SAVER,
    Intent.WIN_BRIGHTNESS_DOWN,
    Intent.WIN_BRIGHTNESS_UP,
    Intent.WIN_VOLUME_DOWN,
    Intent.WIN_VOLUME_UP,
    Intent.MEDIA_PLAY,
    Intent.MEDIA_PAUSE,
    Intent.MEDIA_NEXT,
    Intent.MEDIA_PREVIOUS,
})
BUILTIN_INTENTS = frozenset(Intent) - {Intent.UNKNOWN, Intent.WIN_HOTKEY} - SYSTEM_INTENTS


@dataclass(frozen=True)
class Command:
    """A parsed user utterance.

    Attributes:
        intent: The matched function, or Intent.UNKNOWN.
        raw_text: The recognized text exactly as received from STT.
        params: Extracted parameters such as file name or search query (keyword-only).
        missing: Names of required parameters that were not found (keyword-only).
        example: Phrase to suggest saying again when the command is unknown or incomplete.
        custom_id: Trusted parser metadata for a user-defined command, otherwise None.
    """

    intent: Intent
    raw_text: str
    params: dict[str, str] = field(default_factory=dict, kw_only=True)
    missing: tuple[str, ...] = field(default=(), kw_only=True)
    example: str | None = field(default=None, kw_only=True)
    custom_id: str | None = field(default=None, kw_only=True)

    @property
    def is_executable(self) -> bool:
        """True when the intent is known and no required parameter is missing."""
        return self.intent is not Intent.UNKNOWN and not self.missing


class ResultStatus(Enum):
    """Outcome category of an executed command."""

    OK = "ok"
    ERROR = "error"
    NEEDS_RETRY = "needs_retry"  # target window changed before execution
    INVALID_PARAM = "invalid_param"
    NOT_AVAILABLE = "not_available"  # e.g. PowerPoint is not running
    BOUNDARY = "boundary"  # first or last slide


@dataclass(frozen=True)
class ActionResult:
    """Result of executing a command.

    Attributes:
        status: Outcome category.
        message_vi: Short Vietnamese feedback, shown in the overlay and spoken by TTS.
        error_code: Stable machine-readable code for logs, None on success.
        data: Extra values such as the saved file path.
    """

    status: ResultStatus
    message_vi: str
    error_code: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class SttBackend(Protocol):
    """A speech-to-text engine. Implementations raise SttError on failure."""

    @property
    def name(self) -> str:
        """Short backend name used in logs and the overlay."""
        ...

    def transcribe(self, clip: AudioClip) -> Transcript:
        """Transcribe one clip. Raises SttError when no usable result is produced."""
        ...


class IntentParser(Protocol):
    """Maps recognized text to a Command."""

    def parse(self, text: str, context: WindowContext | None, mode: Mode) -> Command:
        """Return the Command for text, using the target window context and mode."""
        ...


class Executor(Protocol):
    """Runs a Command against the target application."""

    def execute(
        self, command: Command, context: WindowContext, *, cancel: Event | None = None
    ) -> ActionResult:
        """Execute against context; cancel prevents effects that have not started yet.

        An already entered OS/COM call cannot be undone. The caller drops its late result.
        """
        ...


class Speaker(Protocol):
    """Text-to-speech output."""

    def speak(self, text: str) -> None:
        """Speak text. Returns once playback has started."""
        ...

    def stop(self) -> None:
        """Stop any playback in progress."""
        ...


class ContextProvider(Protocol):
    """Captures and re-validates the foreground window."""

    def capture(self) -> WindowContext:
        """Return a snapshot of the current foreground window."""
        ...

    def is_still_target(self, ctx: WindowContext) -> bool:
        """True when the foreground window still matches ctx (same hwnd and process)."""
        ...


class VoicePilotError(Exception):
    """Base class for all VoicePilot errors."""


class SttError(VoicePilotError):
    """A speech-to-text backend failed or produced no usable result."""


class AudioDeviceError(VoicePilotError):
    """The microphone or audio output device is unavailable."""


class ExecutionError(VoicePilotError):
    """A command could not be executed against the target application."""
