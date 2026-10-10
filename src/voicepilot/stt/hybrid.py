"""Hybrid STT: Google first, Gipformer fallback on error or timeout; honors SttBackendMode.

Rules (plan section "Audio và STT"):
- Google runs first. If it errors, or has no valid result after google_timeout_s,
  Gipformer runs on the same clip. An invalid Google result starts Gipformer at once.
- Once both run, the first VALID result wins. Google is checked before Gipformer
  in every poll, so when both are valid in the same snapshot Google wins.
- If both finish without a valid result, the best non-empty transcript is returned
  (Google's preferred). If nothing usable exists, SttError is raised.

Google and Gipformer run on separate executors. A Google request hung on a flaky
network must not occupy the worker that the next turn's Gipformer fallback needs.
"""

from __future__ import annotations

import concurrent.futures as cf
import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future

from voicepilot.contracts import AudioClip, Mode, SttBackend, SttBackendMode, SttError, Transcript
from voicepilot.debuglog import debug_log

log = logging.getLogger(__name__)

CANCELLED_MESSAGE = "cancelled"
_POLL_S = 0.05  # upper bound on how long a cancel request waits to be noticed
_GOOGLE_WORKERS = 4
_OFFLINE_WORKERS = 1  # Gipformer decoding is CPU-bound; never decode two clips at once


def _settle(future: Future[Transcript]) -> Transcript | None:
    """Return the transcript of a finished future, or None on error or empty text."""
    try:
        result = future.result()
    except Exception as exc:
        cause = type(exc.__cause__).__name__ if exc.__cause__ is not None else "-"
        # SttError texts are fixed strings, never speech.
        reason = str(exc) if isinstance(exc, SttError) else "-"
        log.warning("STT backend failed with %s (cause=%s): %s", type(exc).__name__, cause, reason)
        return None
    debug_log.info("stt %s %.0f ms text=%r", result.source, result.latency_ms, result.text)
    if not result.text.strip():
        return None
    log.debug("STT %s finished in %.0f ms", result.source, result.latency_ms)
    return result


class HybridTranscriber:
    """Chooses and combines the Google and Gipformer backends for one turn."""

    def __init__(
        self,
        google: SttBackend | None,
        offline: SttBackend | None,
        mode: SttBackendMode,
        google_timeout_s: float = 2.0,
        executor: cf.Executor | None = None,
        offline_executor: cf.Executor | None = None,
    ) -> None:
        """Create the transcriber.

        Args:
            google: Online backend, or None when unavailable.
            offline: Gipformer backend, or None when unavailable.
            mode: Which backends a turn may use.
            google_timeout_s: How long Google may run before Gipformer starts.
            executor: Runs Google calls. Defaults to an owned pool of 4 workers.
                If only this is given, it also runs Gipformer calls (test convenience).
            offline_executor: Runs Gipformer calls. Defaults to an owned pool of 1 worker.

        Executors this object creates are shut down by close(); injected ones are not.
        """
        self._google = google
        self._offline = offline
        self._mode = mode
        self._timeout_s = google_timeout_s
        self._owned: list[cf.ThreadPoolExecutor] = []

        if executor is None:
            executor = self._own(
                cf.ThreadPoolExecutor(max_workers=_GOOGLE_WORKERS, thread_name_prefix="stt-google")
            )
            if offline_executor is None:
                offline_executor = self._own(
                    cf.ThreadPoolExecutor(max_workers=_OFFLINE_WORKERS, thread_name_prefix="stt-offline")
                )
        elif offline_executor is None:
            offline_executor = executor
        self._google_executor: cf.Executor = executor
        self._offline_executor: cf.Executor = offline_executor

    def _own(self, pool: cf.ThreadPoolExecutor) -> cf.ThreadPoolExecutor:
        self._owned.append(pool)
        return pool

    def close(self) -> None:
        """Stop the pools this object created. Pending calls are cancelled; running ones finish alone.

        Injected executors are left running; their owner shuts them down.
        """
        for pool in self._owned:
            pool.shutdown(wait=False, cancel_futures=True)
        self._owned.clear()

    def for_mode(self, mode: Mode) -> HybridTranscriber:
        """Select unbiased offline recognition for dictation, sharing the existing pools."""
        if mode is not Mode.DICTATION or self._mode is SttBackendMode.GOOGLE:
            return self
        unbiased = getattr(self._offline, "without_hotwords", None)
        if not callable(unbiased):
            return self
        return HybridTranscriber(
            self._google, unbiased(), self._mode, self._timeout_s,
            executor=self._google_executor, offline_executor=self._offline_executor,
        )

    def transcribe(
        self,
        clip: AudioClip,
        is_valid: Callable[[Transcript], bool],
        cancel_event: threading.Event,
    ) -> Transcript:
        """Transcribe one clip according to the configured mode.

        Raises:
            SttError: When no usable transcript exists, a required backend is missing,
                or the turn was cancelled (message "cancelled").
        """
        if cancel_event.is_set():
            raise SttError(CANCELLED_MESSAGE)
        if self._mode is SttBackendMode.GOOGLE:
            return self._single(self._google, self._google_executor, "Google", clip, cancel_event)
        if self._mode is SttBackendMode.OFFLINE:
            return self._single(self._offline, self._offline_executor, "Gipformer", clip, cancel_event)
        return self._race(clip, is_valid, cancel_event)

    def _single(
        self,
        backend: SttBackend | None,
        executor: cf.Executor,
        label: str,
        clip: AudioClip,
        cancel_event: threading.Event,
    ) -> Transcript:
        if backend is None:
            raise SttError(f"Chưa cấu hình bộ nhận dạng {label}.")
        return self._run(
            first=backend,
            first_executor=executor,
            second=None,
            second_executor=None,
            clip=clip,
            is_valid=None,
            cancel_event=cancel_event,
            timeout_s=0.0,
        )

    def _race(
        self,
        clip: AudioClip,
        is_valid: Callable[[Transcript], bool],
        cancel_event: threading.Event,
    ) -> Transcript:
        return self._run(
            first=self._google,
            first_executor=self._google_executor,
            second=self._offline,
            second_executor=self._offline_executor,
            clip=clip,
            is_valid=is_valid,
            cancel_event=cancel_event,
            timeout_s=self._timeout_s,
        )

    def _run(
        self,
        *,
        first: SttBackend | None,
        first_executor: cf.Executor,
        second: SttBackend | None,
        second_executor: cf.Executor | None,
        clip: AudioClip,
        is_valid: Callable[[Transcript], bool] | None,
        cancel_event: threading.Event,
        timeout_s: float,
    ) -> Transcript:
        """Run first, and second as a fallback, returning per the rules in the module docstring.

        A None backend counts as already finished with no result. If first is None,
        second starts immediately.
        """
        wake = threading.Event()
        submitted: list[Future[Transcript]] = []

        def submit(backend: SttBackend, executor: cf.Executor) -> Future[Transcript]:
            try:
                future = executor.submit(backend.transcribe, clip)
            except RuntimeError as exc:  # the pool was shut down by close()
                raise SttError("Bộ nhận dạng đã đóng.") from exc
            future.add_done_callback(lambda _done: wake.set())
            submitted.append(future)
            return future

        first_future = submit(first, first_executor) if first is not None else None
        first_done = first_future is None
        first_result: Transcript | None = None
        second_future: Future[Transcript] | None = None
        second_done = second is None
        second_result: Transcript | None = None
        deadline = time.monotonic() + timeout_s

        def valid(result: Transcript) -> bool:
            return is_valid is None or is_valid(result)

        try:
            while True:
                if cancel_event.is_set():
                    raise SttError(CANCELLED_MESSAGE)
                wake.clear()

                if first_future is not None and not first_done and first_future.done():
                    first_done = True
                    first_result = _settle(first_future)
                    if first_result is not None and valid(first_result):
                        return first_result

                if second_future is not None and not second_done and second_future.done():
                    second_done = True
                    second_result = _settle(second_future)
                    if second_result is not None and valid(second_result):
                        return second_result

                if (
                    second is not None
                    and second_executor is not None
                    and second_future is None
                    and (first_done or time.monotonic() >= deadline)
                ):
                    second_future = submit(second, second_executor)

                if first_done and second_done:
                    break
                wake.wait(_POLL_S)
        finally:
            for future in submitted:
                future.cancel()

        best = first_result if first_result is not None else second_result
        if best is None:
            raise SttError("Không nhận dạng được giọng nói.")
        return best
