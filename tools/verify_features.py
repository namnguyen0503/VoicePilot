"""Repeatable feature verification, before and after a product change.

The default suite exercises the parser and executor with observable fakes. --windows
adds native checks in disposable documents/windows. Each native group is isolated in
a child process with a timeout. A blocked prerequisite is never reported as a pass.
Reports contain only the harness's synthetic text, never the user's debug log.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import os
import platform
import struct
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

from voicepilot.config import load_config
from voicepilot.contracts import (
    ActionResult,
    AppKind,
    Intent,
    Mode,
    ResultStatus,
    SttBackendMode,
    SttError,
    Transcript,
    WindowContext,
)
from voicepilot.intent.parser import RuleParser
from voicepilot.windows import browser, desktop
from voicepilot.windows.context import Win32ContextProvider
from voicepilot.windows.executor import WindowsExecutor

ROOT = Path(__file__).resolve().parents[1]
CASES = (
    (Intent.PPT_SAVE, "lưu lại", AppKind.POWERPOINT, {}),
    (Intent.PPT_SAVE_AS, "lưu thành báo cáo kiểm thử", AppKind.POWERPOINT,
     {"filename": "báo cáo kiểm thử"}),
    (Intent.PPT_NEXT_SLIDE, "slide tiếp theo", AppKind.POWERPOINT, {}),
    (Intent.PPT_PREV_SLIDE, "slide trước", AppKind.POWERPOINT, {}),
    (Intent.PPT_START_SHOW, "bắt đầu trình chiếu", AppKind.POWERPOINT, {}),
    (Intent.PPT_STOP_SHOW, "dừng trình chiếu", AppKind.POWERPOINT, {}),
    (Intent.BROWSER_OPEN_SITE, "mở youtube", AppKind.BROWSER,
     {"url": "https://www.youtube.com", "site": "youtube"}),
    (Intent.BROWSER_SEARCH, "tìm đường đi Việt Nam", AppKind.BROWSER,
     {"query": "đường đi Việt Nam"}),
    (Intent.BROWSER_NEXT_TAB, "tab tiếp theo", AppKind.BROWSER, {}),
    (Intent.BROWSER_PREV_TAB, "tab trước", AppKind.BROWSER, {}),
    (Intent.BROWSER_NEW_TAB, "tab mới", AppKind.BROWSER, {}),
    (Intent.BROWSER_CLOSE_TAB, "đóng tab", AppKind.BROWSER, {}),
    (Intent.WIN_OPEN_APP, "mở notepad", AppKind.OTHER,
     {"app": "notepad.exe", "app_name": "notepad"}),
    (Intent.WIN_SCREENSHOT, "chụp màn hình", AppKind.OTHER, {}),
    (Intent.DICTATE_TEXT, "Xin chào Việt Nam 👋", AppKind.OTHER,
     {"text": "Xin chào Việt Nam 👋"}),
)
PPT_METHODS = {
    Intent.PPT_SAVE: "save", Intent.PPT_SAVE_AS: "save_as",
    Intent.PPT_NEXT_SLIDE: "next_slide", Intent.PPT_PREV_SLIDE: "previous_slide",
    Intent.PPT_START_SHOW: "start_show", Intent.PPT_STOP_SHOW: "stop_show",
}


class Blocked(RuntimeError):
    """A missing machine prerequisite prevents observation."""


@dataclass
class Check:
    name: str
    layer: str
    status: str
    elapsed_ms: float
    detail: str = ""


class Runner:
    """Continue independent checks and retain failures as evidence."""

    def __init__(self, layer: str) -> None:
        self.layer = layer
        self.checks: list[Check] = []

    def check(self, name: str, fn: Callable[[], object]) -> None:
        start = time.perf_counter()
        status, detail = "pass", ""
        try:
            value = fn()
            detail = str(value) if value is not None else "Observed expected behavior"
        except Blocked as exc:
            status, detail = "blocked", str(exc)
        except Exception as exc:
            status, detail = "fail", f"{type(exc).__name__}: {exc}"
        self.checks.append(Check(name, self.layer, status, (time.perf_counter() - start) * 1000, detail))


def require(condition: object, message: str) -> None:
    """Assertions remain active even when Python runs with -O."""
    if not condition:
        raise AssertionError(message)


def wait_for(fn: Callable[[], object], timeout: float = 8.0) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Expected observable state did not arrive before timeout")


def contract_suite() -> list[Check]:
    config = load_config(ROOT / "verification-unused-config.toml")
    parser = RuleParser(config.website_aliases, config.app_aliases)
    runner = Runner("fake")
    for intent, text, kind, params in CASES:
        def verify(intent=intent, text=text, kind=kind, params=params):
            ctx = WindowContext(100, 200, "test.exe", "Synthetic target", kind,
                                "edge" if kind is AppKind.BROWSER else None)
            command = parser.parse(text, ctx, Mode.DICTATION if intent is Intent.DICTATE_TEXT
                                   else Mode.COMMAND)
            require(command.intent is intent and command.params == params and command.is_executable,
                    f"Unexpected parse: {command.intent.value} {command.params}")
            provider = Mock(is_still_target=Mock(return_value=True))
            ppt = Mock()
            keys = Mock()
            executor = WindowsExecutor(config, provider, powerpoint=ppt, keys=keys)
            with (patch.object(desktop, "open_app") as launch,
                  patch.object(desktop, "screenshot", return_value=Path("synthetic.png")) as screenshot,
                  patch.object(browser, "launch_url") as url):
                if intent in PPT_METHODS:
                    getattr(ppt, PPT_METHODS[intent]).return_value = ActionResult(ResultStatus.OK, "OK")
                result = executor.execute(command, ctx)
                require(result.status is ResultStatus.OK, f"Execution returned {result.status.value}")
                if intent in PPT_METHODS:
                    expected = (ctx, params["filename"]) if intent is Intent.PPT_SAVE_AS else (ctx,)
                    getattr(ppt, PPT_METHODS[intent]).assert_called_once_with(*expected, cancel=None)
                elif intent in browser.TAB_HOTKEYS:
                    keys.send_hotkey.assert_called_once_with(*browser.TAB_HOTKEYS[intent])
                elif intent is Intent.BROWSER_OPEN_SITE:
                    url.assert_called_once_with(params["url"], ctx)
                elif intent is Intent.BROWSER_SEARCH:
                    url.assert_called_once_with(browser.build_search_url(params["query"]), ctx)
                elif intent is Intent.WIN_OPEN_APP:
                    launch.assert_called_once_with(params["app"])
                elif intent is Intent.WIN_SCREENSHOT:
                    screenshot.assert_called_once_with(ctx, hide_overlay=None)
                    require(result.data["path"] == "synthetic.png", "Screenshot path missing")
                else:
                    keys.type_unicode.assert_called_once_with(params["text"])
        runner.check(intent.value, verify)
    return runner.checks


def pipeline_case(intent, text, kind, params, backend_mode, *, google_text=None,
                  changed_target=False, cancel_during_com=False):
    """Exercise the real recorder/controller/hybrid/parser/executor with observable boundaries.

    PCM comes from a fake device callback. Recognizer text and Windows effects are controlled;
    this checks turn wiring and backend selection, not microphone or recognition accuracy.
    """
    import numpy as np
    from PySide6.QtCore import QCoreApplication

    from voicepilot.audio.capture import Recorder
    from voicepilot.controller import Controller
    from voicepilot.stt.hybrid import HybridTranscriber

    app = QCoreApplication.instance() or QCoreApplication([])
    config = replace(load_config(ROOT / "verification-unused-config.toml"), vad_enabled=True)
    parser = RuleParser(config.website_aliases, config.app_aliases)
    ctx = WindowContext(100, 200, "test.exe", "Synthetic target", kind,
                        "edge" if kind is AppKind.BROWSER else None)
    provider = Mock(capture=Mock(return_value=ctx), is_still_target=Mock(return_value=True))
    # A 48 kHz int16 (frames, 1) callback covers the real Windows scaling regression.
    tone = (6000 * np.sin(2 * np.pi * 440 * np.arange(14400) / 48000)).astype(np.int16)
    pcm = np.concatenate((tone, np.zeros(48000, dtype=np.int16))).reshape(-1, 1)
    stream = Mock()
    def stream_factory(**kwargs):
        stream.start.side_effect = lambda: kwargs["callback"](pcm, len(pcm), None, None)
        return stream
    recorder = Recorder(config, stream_factory=stream_factory, input_rate=48000,
                        is_speech=lambda frame: np.max(np.abs(np.frombuffer(frame, dtype="<i2"))) > 500)
    received = []
    selected_modes = []
    class Backend:
        def __init__(self, response, source):
            self.response, self.source = response, source
        def transcribe(self, clip):
            require(clip.sample_rate == 16000 and clip.turn_id > 0, "Incorrect normalized clip")
            samples = np.frombuffer(clip.samples, dtype="<i2")
            require(5000 < np.max(np.abs(samples)) < 6500, "PCM scaled or clipped")
            received.append(self.source)
            if changed_target:
                provider.is_still_target.return_value = False
            if self.response is None:
                raise SttError("Synthetic backend failure")
            return Transcript(self.response, self.source, 1.0, clip.turn_id)
        def without_hotwords(self):
            selected_modes.append(Mode.DICTATION)
            return self
    google = Backend(text if google_text is None else google_text, "google")
    if backend_mode == "fallback":
        google.response = None
    offline = Backend(text, "gipformer")
    mode = SttBackendMode.HYBRID if backend_mode == "fallback" else SttBackendMode(backend_mode)
    transcriber = HybridTranscriber(google, offline, mode, google_timeout_s=0.05)
    ppt, keys, speaker = Mock(), Mock(), Mock()
    for method in PPT_METHODS.values():
        getattr(ppt, method).return_value = ActionResult(ResultStatus.OK, "Đã thực hiện.")
    gate, entered = threading.Event(), threading.Event()
    save_effect = Mock()
    if cancel_during_com:
        from voicepilot.windows.powerpoint import PowerPointController

        presentation = Mock(Name=ctx.title, Path="C:/synthetic", FullName="C:/synthetic/Test.pptx",
                            Windows=Mock(Count=0), SlideShowWindow=None, Save=save_effect)
        collection = Mock(Count=1)
        collection.Item.return_value = presentation
        def source():
            entered.set()
            require(gate.wait(2), "Harness did not release COM after cancellation")
            return Mock(Presentations=collection)
        ppt = PowerPointController(app_source=source, apartment=contextlib.nullcontext,
                                   context_provider=provider, timeout_s=2)
    executor = WindowsExecutor(config, provider, powerpoint=ppt, keys=keys)
    controller = Controller(config, recorder, transcriber, parser, executor, speaker, provider)
    results, transcripts = [], []
    controller.result_ready.connect(results.append)
    controller.transcript_changed.connect(transcripts.append)
    try:
        with (patch.object(desktop, "open_app") as launch,
              patch.object(desktop, "screenshot", return_value=Path("synthetic.png")) as screenshot,
              patch.object(browser, "launch_url") as url):
            if intent is Intent.DICTATE_TEXT:
                controller.toggle_mode()
            controller.toggle_listen()
            cancel_token = controller._turn.cancel_event
            if cancel_during_com:
                def waiting_for_com():
                    app.processEvents()
                    return entered.is_set()
                wait_for(waiting_for_com, timeout=1)
                turn_future = controller._last_future
                controller.cancel()
                gate.set()
                ppt._jobs.put(None)
                ppt._thread.join(timeout=1)
                wait_for(lambda: turn_future.done(), timeout=1)
                app.processEvents()
                require(save_effect.call_count == 0, "Cancelled turn still saved after COM returned")
                require(not results and not controller.history and speaker.speak.call_count == 0,
                        "Cancelled turn produced a late response/history/TTS")
                return "Controller cancellation propagated to COM; no save or late response"
            def finished():
                app.processEvents()
                return results
            wait_for(finished)
            require(len(results) == 1, "Turn produced more than one outcome")
            if changed_target:
                require(results[0].status is ResultStatus.NEEDS_RETRY, "Changed target was accepted")
                require(not any(mock.mock_calls for mock in (ppt, keys, launch, screenshot, url)),
                        "Changed target received an effect")
                return "Foreground change rejected before any Windows effect"
            require(results[0].status is ResultStatus.OK,
                    f"Execution returned {results[0].status.value}: {results[0].error_code}")
            require(transcripts[-1] == text, "Wrong backend transcript selected")
            require(controller.history[-1][3] == intent.value, "Wrong intent reached execution")
            require(stream.stop.call_count == stream.close.call_count == 1, "Device not released")
            if intent in PPT_METHODS:
                expected = (ctx, params["filename"]) if intent is Intent.PPT_SAVE_AS else (ctx,)
                getattr(ppt, PPT_METHODS[intent]).assert_called_once_with(*expected, cancel=cancel_token)
            elif intent in browser.TAB_HOTKEYS:
                keys.send_hotkey.assert_called_once_with(*browser.TAB_HOTKEYS[intent])
            elif intent is Intent.BROWSER_OPEN_SITE:
                url.assert_called_once_with(params["url"], ctx)
            elif intent is Intent.BROWSER_SEARCH:
                url.assert_called_once_with(browser.build_search_url(params["query"]), ctx)
            elif intent is Intent.WIN_OPEN_APP:
                launch.assert_called_once_with(params["app"])
            elif intent is Intent.WIN_SCREENSHOT:
                screenshot.assert_called_once_with(ctx, hide_overlay=None)
            else:
                keys.type_unicode.assert_called_once_with(params["text"])
                require(speaker.speak.call_count == 0, "Dictation spoke a command response")
                if mode is not SttBackendMode.GOOGLE:
                    require(selected_modes == [Mode.DICTATION], "Dictation decoder was not selected")
            if intent is not Intent.DICTATE_TEXT:
                speaker.speak.assert_called_once_with(results[0].message_vi)
            effects = sum(mock.call_count for mock in (launch, screenshot, url, keys.send_hotkey,
                          keys.type_unicode, *(getattr(ppt, name) for name in PPT_METHODS.values())))
            require(effects == 1, "Turn executed more than one effect")
            require(provider.capture.call_count == 1, "Target was not captured once at turn start")
            if backend_mode == "fallback":
                require(received == ["google", "gipformer"], "Offline fallback did not run")
            return "48 kHz PCM -> recorder -> Controller -> hybrid -> parser -> one fake Windows effect"
    finally:
        gate.set()
        controller.shutdown()


def pipeline_suite() -> list[Check]:
    """All fifteen functions through each STT mode, plus context/focus selection probes."""
    runner = Runner("fake_pipeline")
    for backend_mode in ("google", "offline", "hybrid", "fallback"):
        for intent, text, kind, params in CASES:
            runner.check(f"{backend_mode}:{intent.value}",
                         lambda i=intent, t=text, k=kind, p=params, b=backend_mode:
                         pipeline_case(i, t, k, p, b))
    runner.check("hybrid_browser_context", lambda: pipeline_case(
        Intent.BROWSER_NEXT_TAB, "tab tiếp theo", AppKind.BROWSER, {}, "hybrid",
        google_text="slide tiếp theo"))
    runner.check("hybrid_powerpoint_context", lambda: pipeline_case(
        Intent.PPT_NEXT_SLIDE, "slide tiếp theo", AppKind.POWERPOINT, {}, "hybrid",
        google_text="tab tiếp theo"))
    runner.check("changed_target_no_effect", lambda: pipeline_case(
        Intent.DICTATE_TEXT, "Xin chào", AppKind.OTHER, {"text": "Xin chào"}, "offline",
        changed_target=True))
    runner.check("cancel_during_com_no_effect", lambda: pipeline_case(
        Intent.PPT_SAVE, "lưu lại", AppKind.POWERPOINT, {}, "offline", cancel_during_com=True))
    return runner.checks


def powerpoint_worker_case(intent: Intent, scenario: str) -> str:
    """Observe real worker scheduling with fake COM, including delays before mutation."""
    from tempfile import TemporaryDirectory

    from voicepilot.windows.powerpoint import PowerPointController

    ctx = WindowContext(100, 200, "POWERPNT.EXE", "Synthetic.pptx - PowerPoint",
                        AppKind.POWERPOINT, None)
    provider = Mock(is_still_target=Mock(return_value=True))
    gate = threading.Event()
    entered = threading.Event()
    cancel_token = threading.Event()
    effects = Mock()
    view = Mock(CurrentShowPosition=2, Slide=Mock(SlideIndex=2))
    view.Next = effects.Next
    view.Previous = effects.Previous
    view.GotoSlide = effects.GotoSlide
    view.Exit = effects.Exit
    show = Mock(View=view)

    def delay_or_change(stage):
        if scenario == f"focus_{stage}":
            provider.is_still_target.return_value = False
        elif scenario == f"cancel_{stage}":
            cancel_token.set()
        elif scenario == f"timeout_{stage}" or (scenario == "queued_timeout" and stage == "source"):
            entered.set()
            require(gate.wait(2), "Harness failed to release the synthetic COM call")

    with TemporaryDirectory(prefix="voicepilot-com-") as folder:
        presentation = Mock(Name="Synthetic.pptx", Path=folder, FullName=f"{folder}/Synthetic.pptx",
                            Slides=Mock(Count=3), Windows=Mock(Count=1))
        presentation.Windows.Item.return_value = Mock(View=view)
        presentation.Save = effects.Save
        presentation.SaveAs = effects.SaveAs
        presentation.SlideShowWindow = show if intent is Intent.PPT_STOP_SHOW else None
        presentation.SlideShowSettings.Run = effects.Run
        def run_show():
            if scenario == "show_focus":
                provider.is_still_target.return_value = False
            delay_or_change("run")
            return show
        effects.Run.side_effect = run_show
        collection = Mock(Count=1)
        collection.Item.return_value = presentation

        class App:
            @property
            def Presentations(self):
                delay_or_change("resolve")
                return collection

        source_calls = []
        def source():
            source_calls.append(threading.get_ident())
            delay_or_change("source")
            return App()

        def controller_factory(**kwargs):
            return PowerPointController(app_source=source, apartment=contextlib.nullcontext,
                                        save_dir=Path(folder),
                                        timeout_s=0.05 if "timeout" in scenario else 2, **kwargs)

        config = load_config(ROOT / "verification-unused-config.toml")
        parser = RuleParser(config.website_aliases, config.app_aliases)
        text = next(text for case_intent, text, *_ in CASES if case_intent is intent)
        command = parser.parse(text, ctx, Mode.COMMAND)
        with patch("voicepilot.windows.executor.PowerPointController", side_effect=controller_factory):
            executor = WindowsExecutor(config, provider)
        try:
            result = executor.execute(command, ctx,
                                      cancel=cancel_token if scenario.startswith("cancel_") else None)
            if "timeout" in scenario:
                require(entered.is_set(), "The worker never entered the controlled COM delay")
                require(result.status is ResultStatus.ERROR and result.error_code == "ppt_timeout",
                        f"Expected timeout, observed {result.status.value}/{result.error_code}")
                if scenario == "queued_timeout":
                    queued = executor.execute(command, ctx)
                    require(queued.error_code == "ppt_timeout", "Queued call did not time out")
            elif scenario.startswith("focus_"):
                require(result.status is ResultStatus.NEEDS_RETRY and
                        result.error_code == "target_changed",
                        f"Changed target returned {result.status.value}/{result.error_code}")
            elif scenario.startswith("cancel_"):
                require(result.status is ResultStatus.NEEDS_RETRY and result.error_code == "ppt_cancelled",
                        f"Cancelled action returned {result.status.value}/{result.error_code}")
            else:
                require(result.status is ResultStatus.OK, f"Healthy action returned {result.status.value}")
        finally:
            gate.set()
            # Let queued work finish without calling shutdown's cancellation path;
            # otherwise shutdown could conceal a broken deadline/foreground guard.
            worker = executor._powerpoint._thread
            if worker is not None and worker.is_alive():
                executor._powerpoint._jobs.put(None)
                worker.join(timeout=2)
                require(not worker.is_alive(), "Synthetic COM worker did not finish")
            executor.shutdown()
        if scenario in ("healthy", "show_focus"):
            expected = {Intent.PPT_SAVE: "Save", Intent.PPT_SAVE_AS: "SaveAs",
                        Intent.PPT_NEXT_SLIDE: "GotoSlide", Intent.PPT_PREV_SLIDE: "GotoSlide",
                        Intent.PPT_START_SHOW: "Run", Intent.PPT_STOP_SHOW: "Exit"}[intent]
            getattr(effects, expected).assert_called_once()
            if intent is Intent.PPT_START_SHOW:
                effects.GotoSlide.assert_called_once_with(1)
        elif scenario == "timeout_run":
            effects.Run.assert_called_once()
            require(effects.GotoSlide.call_count == 0,
                    "Timed out Run was followed by a late GotoSlide")
        else:
            require(not effects.mock_calls, f"Abandoned command caused late effects: {effects.mock_calls}")
        if scenario == "queued_timeout":
            require(len(source_calls) == 1, "Expired queued job acquired another COM application")
    return "Observed worker outcome and COM effects; no desktop or Office instance used"


def powerpoint_worker_suite() -> list[Check]:
    runner = Runner("fake_com_worker")
    for scenario in ("healthy", "timeout_source", "timeout_resolve", "focus_source", "focus_resolve",
                     "cancel_source", "cancel_resolve"):
        for intent in PPT_METHODS:
            runner.check(f"{scenario}:{intent.value}",
                         lambda intent=intent, scenario=scenario: powerpoint_worker_case(intent, scenario))
    runner.check("queued_timeout:PPT_SAVE", lambda: powerpoint_worker_case(Intent.PPT_SAVE, "queued_timeout"))
    for scenario in ("timeout_run", "show_focus"):
        runner.check(f"{scenario}:PPT_START_SHOW",
                     lambda scenario=scenario: powerpoint_worker_case(Intent.PPT_START_SHOW, scenario))
    runner.check("shutdown_pending_and_restart", powerpoint_shutdown_probe)
    for failure in (None, "unavailable", "unexpected"):
        runner.check(f"com_release:{failure or 'success'}",
                     lambda failure=failure: powerpoint_lifetime_probe(failure))
    return runner.checks


def powerpoint_shutdown_probe() -> str:
    """A blocked old COM apartment must not overlap a restarted worker or save pending jobs."""
    from concurrent.futures import ThreadPoolExecutor

    from voicepilot.windows.powerpoint import PowerPointController

    ctx = WindowContext(100, 200, "POWERPNT.EXE", "Synthetic.pptx", AppKind.POWERPOINT, None)
    effects = Mock()
    presentation = Mock(Name="Synthetic.pptx", Path="C:/synthetic", FullName="C:/synthetic/Synthetic.pptx",
                        Windows=Mock(Count=0), SlideShowWindow=None)
    presentation.Save = effects.Save
    collection = Mock(Count=1)
    collection.Item.return_value = presentation
    app = Mock(Presentations=collection)
    gate, entered = threading.Event(), threading.Event()
    calls = []
    def source():
        calls.append(threading.get_ident())
        if len(calls) == 1:
            entered.set()
            require(gate.wait(2), "Harness did not release the blocked COM source")
        return app
    controller = PowerPointController(app_source=source, apartment=contextlib.nullcontext, timeout_s=0.5)
    pool = ThreadPoolExecutor(max_workers=2)
    old_worker = None
    try:
        first = pool.submit(controller.save, ctx)
        require(entered.wait(1), "Worker did not enter the controlled COM source")
        old_worker = controller._thread
        second = pool.submit(controller.save, ctx)
        wait_for(lambda: controller._jobs.qsize() == 1, timeout=0.3)
        with patch("voicepilot.windows.powerpoint._SHUTDOWN_TIMEOUT_S", 0.02):
            controller.shutdown()
        restart = controller.save(ctx)
        require(restart.status is ResultStatus.NOT_AVAILABLE and
                restart.error_code == "ppt_shutting_down",
                f"Restart during blocked shutdown returned {restart.status.value}/{restart.error_code}")
        require(len(calls) == 1, "A second COM worker started before the old apartment finished")
        gate.set()
        old_worker.join(timeout=1)
        require(not old_worker.is_alive(), "Old worker did not stop after COM returned")
        require(first.result(timeout=1).error_code == "ppt_cancelled",
                "Active unstarted action was not abandoned")
        require(second.result(timeout=1).error_code == "ppt_cancelled", "Pending action was not abandoned")
        require(not effects.mock_calls, f"Shutdown caused late effects: {effects.mock_calls}")
        healthy = controller.save(ctx)
        require(healthy.status is ResultStatus.OK, "Worker did not restart after shutdown completed")
        effects.Save.assert_called_once()
    finally:
        gate.set()
        controller.shutdown()
        if old_worker is not None:
            old_worker.join(timeout=1)
        pool.shutdown(wait=True)
    return "Abandoned active/pending jobs; prevented overlapping COM apartments; clean restart succeeded"


def powerpoint_lifetime_probe(failure: str | None) -> str:
    """COM references must be released on their worker before apartment teardown, even on errors."""
    import gc

    from voicepilot.windows.powerpoint import PowerPointController

    events = []
    @contextlib.contextmanager
    def apartment():
        events.append(("initialize", threading.get_ident()))
        try:
            yield
        finally:
            events.append(("uninitialize", threading.get_ident()))

    presentation = Mock(Name="Synthetic.pptx", Path="C:/synthetic", FullName="C:/synthetic/Synthetic.pptx",
                        Windows=Mock(Count=0), SlideShowWindow=None)
    if failure == "unexpected":
        def fail_save():
            raise RuntimeError("Synthetic COM failure")
        presentation.Save.side_effect = fail_save
    collection = Mock(Count=0 if failure == "unavailable" else 1)
    collection.Item.return_value = presentation
    class App:
        Presentations = collection
        def __del__(self):
            events.append(("release", threading.get_ident()))
    controller = PowerPointController(app_source=App, apartment=apartment)
    ctx = WindowContext(100, 200, "POWERPNT.EXE", "Synthetic.pptx", AppKind.POWERPOINT, None)
    try:
        result = controller.save(ctx)
        expected = {None: ResultStatus.OK, "unavailable": ResultStatus.NOT_AVAILABLE,
                    "unexpected": ResultStatus.ERROR}[failure]
        require(result.status is expected, "COM error mapping changed")
    finally:
        controller.shutdown()
    gc.collect()
    require([name for name, _ in events] == ["initialize", "release", "uninitialize"],
            f"COM reference outlived its apartment: {events}")
    require(len({ident for _, ident in events}) == 1, f"COM reference escaped its worker: {events}")
    return "COM reference released on its worker before CoUninitialize"


def tts_callback_probe(voice, model_path: Path, *, cancel_after_chunk: bool = False):
    """Use the real speaker and a silent player to verify sherpa's callback contract."""
    from voicepilot.tts.piper import PiperSpeaker

    cancelled = threading.Event()
    decisions, played, generated = [], [], []
    class ObservedVoice:
        sample_rate = voice.sample_rate
        def generate(self, text, **kwargs):
            generated.append(text)
            callback = kwargs.pop("callback")
            def observe(samples, progress):
                decision = callback(samples, progress)
                decisions.append((cancelled.is_set(), decision))
                return decision
            return voice.generate(text, callback=observe, **kwargs)
    def silent_player(pcm, rate, stop):
        require(pcm and len(pcm) % 2 == 0 and rate > 0, "Invalid TTS PCM")
        played.append(len(pcm))
        if cancel_after_chunk:
            stop.set()
    speaker = PiperSpeaker(model_path, player=silent_player, voice_loader=lambda _: ObservedVoice())
    speaker._run("Đã lưu báo cáo tháng 10 năm 2026. Xin chào, Nam! Thử lại nhé.", cancelled)
    require(played and decisions, "Speaker produced no callback audio")
    require(all(decision == (0 if stopped else 1) for stopped, decision in decisions),
            f"Wrong sherpa callback decisions: {decisions}; 1 continues and 0 stops")
    if cancel_after_chunk:
        require(len(played) == len(generated) == 1, "TTS continued after cancellation")
        return "Cancellation returns 0 to native TTS; later sentences were not generated"
    require(len(generated) == 3, "TTS dropped a feedback sentence")
    return f"{len(played)} PCM callbacks; active callbacks return 1; no audio device used"


def native_windows() -> list[tuple[int, int, str, str]]:
    import win32gui
    import win32process

    from voicepilot.windows.context import _image_path

    windows = []
    def visit(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            windows.append((hwnd, pid, win32gui.GetWindowText(hwnd), _image_path(pid).lower()))
    win32gui.EnumWindows(visit, None)
    return windows


def activate(hwnd: int) -> WindowContext:
    import win32con
    import win32gui
    import win32process

    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    user32 = ctypes.WinDLL("user32")
    current = ctypes.WinDLL("kernel32").GetCurrentThreadId()
    foreground = win32gui.GetForegroundWindow()
    thread = win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
    attached = bool(thread and thread != current and user32.AttachThreadInput(current, thread, True))
    try:
        if foreground != hwnd:
            win32gui.SetForegroundWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current, thread, False)
    wait_for(lambda: win32gui.GetForegroundWindow() == hwnd)
    ctx = Win32ContextProvider().capture()
    require(ctx.hwnd == hwnd, "Foreground context does not match the disposable target")
    return ctx


def make_executor():
    config = load_config(ROOT / "verification-unused-config.toml")
    parser = RuleParser(config.website_aliases, config.app_aliases)
    return parser, WindowsExecutor(config, Win32ContextProvider())


def execute(parser, executor, hwnd: int, text: str, mode: Mode = Mode.COMMAND):
    ctx = activate(hwnd)
    command = parser.parse(text, ctx, mode)
    require(command.is_executable, f"Parser did not understand {text}")
    result = executor.execute(command, ctx)
    require(result.status is ResultStatus.OK, f"{result.status.value}: {result.error_code}")
    return result


def edit_text(hwnd: int) -> str:
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_void_p]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    length = user32.SendMessageW(hwnd, 0x000E, 0, None)  # WM_GETTEXTLENGTH
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.SendMessageW(hwnd, 0x000D, length + 1, ctypes.cast(buffer, ctypes.c_void_p))
    return buffer.value


def keys_suite(folder: Path) -> list[Check]:
    import win32con
    import win32gui
    from PySide6.QtWidgets import QApplication

    from voicepilot.ui.overlay import Overlay
    from voicepilot.windows.start_menu import load_installed_apps, match_app

    app = QApplication.instance() or QApplication([])
    runner = Runner("windows")
    parser, executor = make_executor()
    document = folder / "VoicePilot verification.txt"
    document.write_text("", encoding="utf-8")
    process = subprocess.Popen(["notepad.exe", str(document)])
    hwnd, editor = 0, 0
    overlay = Overlay()
    try:
        hwnd = int(wait_for(lambda: next((w[0] for w in native_windows()
                                         if w[1] == process.pid and "notepad" in w[3]), 0)))
        editor = win32gui.FindWindowEx(hwnd, 0, "Edit", None)
        if not editor:
            raise Blocked("Classic Notepad Edit control is unavailable on this machine")
        def overlay_focus():
            ctx = activate(hwnd)
            overlay.show_bar()
            app.processEvents()
            require(Win32ContextProvider().is_still_target(ctx), "Overlay took foreground focus")
        runner.check("overlay_preserves_focus", overlay_focus)
        def dictate():
            text = "Xin chào Việt Nam 👋 — kiểm thử chính tả."
            execute(parser, executor, hwnd, text, Mode.DICTATION)
            wait_for(lambda: edit_text(editor) == text)
            return "Notepad text equals Vietnamese and emoji input"
        runner.check("DICTATE_TEXT", dictate)
        def changed_focus():
            ctx = activate(hwnd)
            changed = WindowContext(ctx.hwnd + 1, ctx.pid, ctx.process_name, ctx.title, ctx.app_kind, None)
            command = parser.parse("must never be inserted", changed, Mode.DICTATION)
            before = edit_text(editor)
            result = executor.execute(command, changed)
            require(result.status is ResultStatus.NEEDS_RETRY and edit_text(editor) == before,
                    "Changed target was not rejected without typing")
        runner.check("changed_target_rejected", changed_focus)
        def screenshot():
            overlay.show_bar()
            app.processEvents()
            @contextlib.contextmanager
            def hidden_overlay():
                with overlay.hidden_temporarily():
                    app.processEvents()
                    require(not overlay.isVisible(), "Overlay was visible during capture")
                    yield
            executor._hide_overlay = hidden_overlay
            result = execute(parser, executor, hwnd, "chụp màn hình")
            data = Path(result.data["path"]).read_bytes()
            require(data[:8] == b"\x89PNG\r\n\x1a\n", "Screenshot is not a PNG")
            width, height = struct.unpack(">II", data[16:24])
            import mss
            with mss.mss() as grabber:
                require((width, height) in [(m["width"], m["height"]) for m in grabber.monitors[1:]],
                        "Screenshot dimensions do not match a physical monitor")
            require(overlay.isVisible(), "Overlay was not restored after capture")
            return f"PNG {width}x{height}; overlay restored={overlay.isVisible()}"
        runner.check("WIN_SCREENSHOT", screenshot)
        def launch():
            before = {w[0] for w in native_windows() if "notepad" in w[3]}
            execute(parser, executor, hwnd, "mở notepad")
            opened = int(wait_for(lambda: next((w[0] for w in native_windows()
                                               if "notepad" in w[3] and w[0] not in before), 0)))
            win32gui.PostMessage(opened, win32con.WM_CLOSE, 0, 0)
            return "A new Notepad window appeared"
        runner.check("WIN_OPEN_APP", launch)
        def indexed_launch():
            target = match_app("notepad", load_installed_apps())
            require(target, "Installed Notepad was not indexed")
            before = {w[0] for w in native_windows() if "notepad" in w[3]}
            desktop.open_app(target)
            opened = int(wait_for(lambda: next((w[0] for w in native_windows()
                                               if "notepad" in w[3] and w[0] not in before), 0)))
            win32gui.PostMessage(opened, win32con.WM_CLOSE, 0, 0)
            return f"Indexed launch type: {'AppsFolder' if target.startswith('shell:') else 'exe'}"
        runner.check("installed_app_launch", indexed_launch)
    finally:
        overlay.close()
        executor.shutdown()
        if editor:
            win32gui.SendMessage(editor, 0x00B9, 0, 0)  # EM_SETMODIFY: avoid a save prompt
        if hwnd:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()
    return runner.checks


def ppt_suite(folder: Path) -> list[Check]:
    import pythoncom
    import win32com.client

    runner = Runner("windows")
    parser, executor = make_executor()
    pythoncom.CoInitialize()
    app, deck = None, None
    try:
        try:
            app = win32com.client.DispatchEx("PowerPoint.Application")
        except Exception as exc:
            raise Blocked(f"PowerPoint desktop COM unavailable: {type(exc).__name__}") from exc
        app.Visible = True
        deck = app.Presentations.Add()
        for index in range(1, 4):
            deck.Slides.Add(index, 12)
        deck.SaveAs(str(folder / "VoicePilot verification.pptx"), 24)
        deck.Windows.Item(1).View.GotoSlide(1)
        hwnd = int(wait_for(lambda: next((w[0] for w in native_windows()
                                         if "powerpnt.exe" in w[3] and str(deck.Name) in w[2]), 0)))
        activate(hwnd)
        time.sleep(1)  # Office registers its running object after it loses focus.
        def save_as():
            result = execute(parser, executor, hwnd, "lưu thành báo cáo kiểm thử")
            path = Path(result.data["path"])
            require(path.is_file() and path.name == "báo cáo kiểm thử.pptx", "Unicode SaveAs failed")
        runner.check("PPT_SAVE_AS", save_as)
        def save():
            deck.Slides.Item(1).Shapes.AddTextbox(1, 10, 10, 200, 50).TextFrame.TextRange.Text = "Synthetic"
            execute(parser, executor, hwnd, "lưu lại")
            require(bool(deck.Saved), "COM reported success but the deck is still modified")
        runner.check("PPT_SAVE", save)
        def next_slide():
            execute(parser, executor, hwnd, "slide tiếp theo")
            require(int(deck.Windows.Item(1).View.Slide.SlideIndex) == 2, "Edit view did not reach slide 2")
        runner.check("PPT_NEXT_SLIDE", next_slide)
        def previous():
            execute(parser, executor, hwnd, "slide trước")
            require(int(deck.Windows.Item(1).View.Slide.SlideIndex) == 1, "Edit view did not reach slide 1")
        runner.check("PPT_PREV_SLIDE", previous)
        def start():
            execute(parser, executor, hwnd, "bắt đầu trình chiếu")
            require(int(deck.SlideShowWindow.View.CurrentShowPosition) == 1, "Show did not start at slide 1")
        runner.check("PPT_START_SHOW", start)
        def show_steps():
            raw_hwnd = deck.SlideShowWindow.HWND
            show_hwnd = int(raw_hwnd() if callable(raw_hwnd) else raw_hwnd)
            execute(parser, executor, show_hwnd, "slide tiếp theo")
            require(int(deck.SlideShowWindow.View.CurrentShowPosition) == 2, "Show next failed")
            execute(parser, executor, show_hwnd, "slide trước")
            require(int(deck.SlideShowWindow.View.CurrentShowPosition) == 1, "Show previous failed")
        runner.check("presentation_navigation", show_steps)
        def stop():
            raw_hwnd = deck.SlideShowWindow.HWND
            show_hwnd = int(raw_hwnd() if callable(raw_hwnd) else raw_hwnd)
            execute(parser, executor, show_hwnd, "dừng trình chiếu")
            require(int(app.SlideShowWindows.Count) == 0, "Show window still exists")
        runner.check("PPT_STOP_SHOW", stop)
    finally:
        executor.shutdown()
        if deck is not None:
            with contextlib.suppress(Exception):
                deck.Saved = True
                deck.Close()
        if app is not None:
            with contextlib.suppress(Exception):
                app.Quit()
        pythoncom.CoUninitialize()
    return runner.checks


def browser_suite(folder: Path, name: str) -> list[Check]:
    import http.server
    import urllib.request
    import winreg

    import win32con
    import win32gui

    runner = Runner(f"windows-{name}")
    executable = browser.BROWSER_EXECUTABLES[name]
    path = None
    registry_path = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{executable}"
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        with contextlib.suppress(OSError):
            with winreg.OpenKey(root, registry_path) as key:
                path = winreg.QueryValue(key, None)
    if not path or not Path(path).is_file():
        raise Blocked(f"{name} executable is not installed in App Paths")
    class Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            title = f"VoicePilot {self.server.server_port} {self.path}"
            body = f"<title>{title}</title><textarea id='text'></textarea>".encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    address = f"http://127.0.0.1:{server.server_port}"
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen([path, f"--user-data-dir={folder / name}", "--no-first-run",
                                "--no-default-browser-check", f"--remote-debugging-port={port}",
                                address + "/first", address + "/second"])
    hwnd = 0
    parser, executor = make_executor()
    def tabs():
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2) as response:
            return [tab for tab in json.load(response) if tab.get("type") == "page"]
    try:
        # Chromium may hand the launch off to another PID. Match the unique local fixture title.
        hwnd = int(wait_for(lambda: browser_fixture_window(native_windows(), executable,
                                                           server.server_port), 15))
        wait_for(lambda: len(tabs()) == 2)
        def previous():
            execute(parser, executor, hwnd, "tab trước")
            wait_for(lambda: "/first" in win32gui.GetWindowText(hwnd))
        runner.check("BROWSER_PREV_TAB", previous)
        def next_tab():
            execute(parser, executor, hwnd, "tab tiếp theo")
            wait_for(lambda: "/second" in win32gui.GetWindowText(hwnd))
        runner.check("BROWSER_NEXT_TAB", next_tab)
        def new():
            before = len(tabs())
            execute(parser, executor, hwnd, "tab mới")
            wait_for(lambda: len(tabs()) == before + 1)
        runner.check("BROWSER_NEW_TAB", new)
        def close():
            before = len(tabs())
            execute(parser, executor, hwnd, "đóng tab")
            wait_for(lambda: len(tabs()) == before - 1)
        runner.check("BROWSER_CLOSE_TAB", close)
        # Keep launch_url real, but route its executable into this disposable profile.
        # This verifies ShellExecute and tab creation without touching a personal profile.
        real_shell = browser._shell_open
        def isolated_shell(file, parameters):
            return real_shell(path, f'--user-data-dir="{folder / name}" "{parameters}"')
        with patch.object(browser, "_shell_open", side_effect=isolated_shell):
            def open_site():
                execute(parser, executor, hwnd, "mở " + address + "/opened")
                wait_for(lambda: any(t["url"] == address + "/opened" for t in tabs()))
            runner.check("BROWSER_OPEN_SITE", open_site)
            def search():
                execute(parser, executor, hwnd, "tìm VoicePilot verification")
                wait_for(lambda: any("google.com/search?q=VoicePilot+verification" in t["url"]
                                    for t in tabs()))
            runner.check("BROWSER_SEARCH", search)
    finally:
        executor.shutdown()
        if hwnd:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
        server.shutdown()
        server.server_close()
    return runner.checks


def browser_fixture_window(windows: list[tuple[int, int, str, str]], executable: str, port: int) -> int:
    prefix = f"VoicePilot {port} "
    return next((hwnd for hwnd, _pid, title, image in windows
                 if title.startswith(prefix) and Path(image).name == executable), 0)


def run_probe(name: str, folder: Path) -> list[Check]:
    runner = Runner("runtime" if name == "runtime" else "windows")
    try:
        if name == "runtime":
            return runtime_suite(folder)
        if name == "keys":
            return keys_suite(folder)
        if name == "ppt":
            return ppt_suite(folder)
        return browser_suite(folder, name)
    except Blocked as exc:
        runner.checks.append(Check(name, runner.layer, "blocked", 0, str(exc)))
    except Exception as exc:
        runner.checks.append(Check(name, runner.layer, "fail", 0, f"{type(exc).__name__}: {exc}"))
    return runner.checks


def runtime_suite(folder: Path) -> list[Check]:
    """Native ASR and TTS share one process; synthetic speech is smoke evidence only."""
    import numpy as np

    from voicepilot.audio.capture import convert_to_16k
    from voicepilot.contracts import AudioClip
    from voicepilot.paths import models_dir
    from voicepilot.stt.gipformer import GipformerStt
    from voicepilot.stt.hotwords import command_phrases
    from voicepilot.tts.piper import _load_sherpa_voice, _to_pcm

    os.environ["VOICEPILOT_CONFIG_DIR"] = str(folder / "config")
    config = load_config()
    runner = Runner("runtime")
    asr = GipformerStt(models_dir() / "asr" / "gipformer1.5-int8",
                       hotwords=command_phrases(config.app_aliases, config.website_aliases))
    runner.check("gipformer_model_load", asr.warmup)
    voice_path = models_dir() / "tts" / "vits-piper-vi_VN-vais1000-medium" / "vi_VN-vais1000-medium.onnx"
    voice = _load_sherpa_voice(voice_path)
    def roundtrip():
        audio = voice.generate("Chụp màn hình.", sid=0, speed=1.0)
        require(len(audio.samples) > 0 and audio.sample_rate > 0, "TTS did not generate PCM")
        samples = np.frombuffer(_to_pcm(audio.samples), dtype="<i2")
        normalized = convert_to_16k(samples, audio.sample_rate)
        result = asr.transcribe(AudioClip(normalized.tobytes(), 1))
        parser = RuleParser(config.website_aliases, config.app_aliases)
        require(parser.parse(result.text, None, Mode.COMMAND).intent is Intent.WIN_SCREENSHOT,
                "Synthesized command did not reach the expected intent")
        return f"Synthetic TTS -> ASR -> parser; ASR {result.latency_ms:.0f} ms"
    runner.check("asr_tts_same_process", roundtrip)
    def dictation():
        audio = voice.generate("Xin chào, tôi đang kiểm thử chức năng đọc chính tả.", sid=0, speed=1.0)
        samples = convert_to_16k(np.frombuffer(_to_pcm(audio.samples), dtype="<i2"), audio.sample_rate)
        result = asr.without_hotwords().transcribe(AudioClip(samples.tobytes(), 2))
        require(result.text.strip(), "Unbiased dictation returned no text")
        return "Unbiased decoder returned text from synthetic PCM"
    runner.check("dictation_unbiased_runtime", dictation)
    runner.check("tts_feedback_callback", lambda: tts_callback_probe(voice, voice_path))
    runner.check("tts_cancel_callback",
                 lambda: tts_callback_probe(voice, voice_path, cancel_after_chunk=True))
    return runner.checks


def desktop_block_reason(context: WindowContext) -> str | None:
    """A locked or inaccessible desktop cannot supply native feature evidence."""
    if not context.hwnd or context.process_name.lower() in {"lockapp.exe", "logonui.exe", "winlogon.exe"}:
        return "Windows desktop is locked or unavailable; unlock it before native verification"
    return None


def blocked_native_checks(reason: str) -> list[Check]:
    checks = [Check(intent.value, "windows", "blocked", 0, reason) for intent, *_ in CASES]
    checks.extend(Check(name, "windows", "blocked", 0, reason) for name in
                  ("overlay_preserves_focus", "changed_target_rejected", "installed_app_launch"))
    return checks


def write_report(folder: Path, checks: list[Check]) -> None:
    metadata = {"timestamp": datetime.now(UTC).isoformat(), "python": sys.version,
                "platform": platform.platform(), "root": str(ROOT)}
    payload = {**metadata, "checks": [asdict(check) for check in checks]}
    (folder / "report.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# VoicePilot verification", "", f"UTC: {metadata['timestamp']}", "",
             "Fake checks validate contracts. Windows checks observe native results.",
             "Blocked checks need a prerequisite and do not count as passed.", "",
             "| Check | Layer | Status | Time (ms) | Evidence |", "|---|---|---|---:|---|"]
    for check in checks:
        detail = check.detail.replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {check.name} | {check.layer} | {check.status} | {check.elapsed_ms:.0f} | {detail} |")
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def collect_probe(name: str, folder: Path, timeout: int, *, environment: dict[str, str] | None = None):
    """Retain a failing report even when a native child crashes or times out."""
    target = folder / name
    layer = "runtime" if name == "runtime" else "windows"
    try:
        result = subprocess.run([sys.executable, __file__, "--probe", name, "--output", str(target)],
                                env=environment, cwd=ROOT, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return [Check(name, layer, "fail", timeout * 1000, "Probe process timed out")]
    (folder / f"{name}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    report = target / "report.json"
    if report.is_file():
        return [Check(**raw) for raw in json.loads(report.read_text(encoding="utf-8"))["checks"]]
    return [Check(name, layer, "fail", 0, f"Probe exited {result.returncode}; see {name}.txt")]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", action="store_true", help="Exercise disposable native app windows")
    parser.add_argument("--checks", action="store_true", help="Also run pytest, ruff and selftest")
    parser.add_argument("--runtime", action="store_true", help="Check native ASR/TTS with synthetic speech")
    parser.add_argument("--output", type=Path, help="New report directory; defaults to local app data")
    parser.add_argument("--probe", choices=("keys", "ppt", "edge", "chrome", "runtime"),
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VoicePilot" / "verification"
    folder = args.output or base / (datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
    if folder.exists():
        parser.error("--output must be a new directory; previous evidence will not be overwritten")
    folder.mkdir(parents=True)
    if args.probe:
        checks = run_probe(args.probe, folder)
        write_report(folder, checks)
        return int(any(check.status == "fail" for check in checks))
    checks = contract_suite() + pipeline_suite() + powerpoint_worker_suite()
    if args.checks:
        commands = {
            "pytest": [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                       "--basetemp", str(folder / "pytest")],
            "ruff": [sys.executable, "-m", "ruff", "check", "src", "tests", "tools"],
            "selftest": [sys.executable, "-m", "voicepilot", "--selftest"],
        }
        runner = Runner("tool")
        for name, command in commands.items():
            def invoke(name=name, command=command):
                environment = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
                               "VOICEPILOT_CONFIG_DIR": str(folder / "config")}
                result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                                        text=True, encoding="utf-8", errors="replace", timeout=120)
                (folder / f"{name}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
                require(result.returncode == 0, f"Exit {result.returncode}; see {name}.txt")
                return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else "Exit 0"
            runner.check(name, invoke)
        checks.extend(runner.checks)
    if args.runtime:
        checks.extend(collect_probe("runtime", folder, 120))
    if args.windows:
        require(sys.platform == "win32", "--windows requires an interactive Windows desktop")
        reason = desktop_block_reason(Win32ContextProvider().capture())
        if reason:
            checks.extend(blocked_native_checks(reason))
            write_report(folder, checks)
            print(f"blocked windows: {reason}\nReport: {folder / 'report.md'}")
            return 1
        for name in ("keys", "ppt", "edge", "chrome"):
            environment = {**os.environ, "QT_QPA_PLATFORM": "windows"}
            checks.extend(collect_probe(name, folder, 90, environment=environment))
            write_report(folder, checks)
    write_report(folder, checks)
    for check in checks:
        print(f"{check.status:7} {check.layer:14} {check.name}: {check.detail}")
    print(f"Report: {folder / 'report.md'}")
    return int(any(check.status != "pass" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
