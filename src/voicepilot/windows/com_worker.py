"""Generic guarded COM worker shared by the PowerPoint and blank-document controllers.

Every job runs on the single worker thread of a ComWorker, which owns one COM apartment
(pythoncom.CoInitialize). A job is dropped when its deadline passes, its cancel token is set,
the worker is shutting down, or the captured window is no longer the target. Public calls wait
for the job with a timeout and map every failure to an ActionResult; nothing is raised.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from voicepilot.contracts import ActionResult, ContextProvider, ResultStatus, WindowContext

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 8.0
_SHUTDOWN_TIMEOUT_S = 2.0
BeforeAction = Callable[[], None]
Job = Callable[[Any, BeforeAction, BeforeAction], ActionResult]


class PowerPointUnavailable(Exception):
    """A COM application cannot serve the request; the message is shown to the user as NOT_AVAILABLE."""

    def __init__(self, message_vi: str, code: str) -> None:
        super().__init__(message_vi)
        self.message_vi = message_vi
        self.code = code


def _unguarded() -> None:
    """Allow direct helper calls without worker scheduling or window checks."""


@contextlib.contextmanager
def _com_apartment() -> Iterator[None]:
    """Initialize COM on the current thread for the duration of the block (Windows only)."""
    import pythoncom

    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def _result(
    status: ResultStatus, message: str, code: str | None = None, data: dict[str, Any] | None = None
) -> ActionResult:
    return ActionResult(status, message, code, data or {})


class _Abandoned(Exception):
    def __init__(self, result: ActionResult) -> None:
        self.result = result


@dataclass
class _Request:
    future: concurrent.futures.Future[ActionResult]
    job: Job
    context: WindowContext
    deadline: float
    abandoned: threading.Event
    cancel: threading.Event | None


class ComWorker:
    """Runs jobs on one COM worker thread and maps failures to ActionResult.

    Subclasses set app_label (user-facing name) and code_prefix (stable error-code prefix).
    The worker starts on the first call. Each job receives the object returned by app_source,
    acquired on the worker thread for that job, and never leaves the thread.
    """

    app_label: ClassVar[str] = "Office"
    code_prefix: ClassVar[str] = "office"

    def __init__(
        self,
        *,
        app_source: Callable[[], Any],
        apartment: Callable[[], contextlib.AbstractContextManager[Any]] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        context_provider: ContextProvider | None = None,
    ) -> None:
        """Create the worker. Nothing is started until the first call.

        Args:
            app_source: Returns the per-job COM object, or raises PowerPointUnavailable.
            apartment: Context manager that prepares COM on the worker thread. Defaults to CoInitialize.
            timeout_s: How long a public call waits before reporting a timeout.
            context_provider: Rechecks the target after COM resolution and before mutation.
        """
        self._app_source = app_source
        self._apartment = apartment or _com_apartment
        self._timeout_s = timeout_s
        self._context = context_provider
        self._jobs: queue.Queue[_Request | None] = queue.Queue()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def shutdown(self) -> None:
        """Abandon unstarted actions and stop the worker; already entered COM calls may finish.

        A restart is allowed once the old apartment has finished, never while it is blocked.
        """
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                return
            self._stopping.set()
            while True:
                try:
                    request = self._jobs.get_nowait()
                except queue.Empty:
                    break
                if request is not None:
                    request.abandoned.set()
                    request.future.cancel()
            self._jobs.put(None)
        thread.join(timeout=_SHUTDOWN_TIMEOUT_S)

    def _submit(self, job: Job, ctx: WindowContext, cancel: threading.Event | None) -> ActionResult:
        future: concurrent.futures.Future[ActionResult] = concurrent.futures.Future()
        request = _Request(future, job, ctx, time.monotonic() + self._timeout_s, threading.Event(), cancel)
        try:
            if cancel is not None and cancel.is_set():
                return self._cancelled_result()
            self._ensure_worker(request)
            while True:
                if cancel is not None and cancel.is_set():
                    future.cancel()
                    return self._cancelled_result()
                remaining = max(0, request.deadline - time.monotonic())
                try:
                    return future.result(timeout=min(remaining, 0.05) if cancel is not None else remaining)
                except concurrent.futures.TimeoutError:
                    if future.done() or remaining <= 0 or time.monotonic() >= request.deadline:
                        raise
        except concurrent.futures.TimeoutError:
            request.abandoned.set()
            future.cancel()  # Skip pending jobs; the event also stops a running job after COM returns.
            log.error("%s did not answer within %.1f s", self.app_label, self._timeout_s)
            return _result(
                ResultStatus.ERROR, f"{self.app_label} không phản hồi.", f"{self.code_prefix}_timeout"
            )
        except concurrent.futures.CancelledError:
            return self._cancelled_result()
        except PowerPointUnavailable as exc:
            return _result(ResultStatus.NOT_AVAILABLE, exc.message_vi, exc.code)
        except Exception as exc:
            log.error("%s command failed: %s", self.app_label, type(exc).__name__)
            return _result(
                ResultStatus.ERROR, f"Lỗi khi điều khiển {self.app_label}.", f"{self.code_prefix}_error"
            )

    def _cancelled_result(self) -> ActionResult:
        return _result(
            ResultStatus.NEEDS_RETRY, f"Đã hủy lệnh {self.app_label}.", f"{self.code_prefix}_cancelled"
        )

    def _ensure_worker(self, request: _Request) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                if self._stopping.is_set():
                    raise PowerPointUnavailable(
                        f"{self.app_label} đang dừng, hãy thử lại.", f"{self.code_prefix}_shutting_down"
                    )
            else:
                # Each apartment owns its queue and stop token. A stale sentinel cannot
                # stop a later generation, and submissions cannot race past shutdown.
                self._jobs = queue.Queue()
                self._stopping = threading.Event()
                self._thread = threading.Thread(
                    target=self._work,
                    args=(self._jobs, self._stopping),
                    name="voicepilot-" + self.app_label.casefold(),
                    daemon=True,
                )
                self._thread.start()
            self._jobs.put(request)

    def _work(self, jobs: queue.Queue[_Request | None], stopping: threading.Event) -> None:
        with self._apartment():
            while True:
                item = jobs.get()
                if item is None:
                    return
                future = item.future
                if stopping.is_set():
                    future.cancel()
                    continue
                if not future.set_running_or_notify_cancel():
                    continue

                def guard(request: _Request = item) -> None:
                    self._check_request(request, stopping)

                def followup(request: _Request = item) -> None:
                    self._check_request(request, stopping, check_target=False)

                app = None
                try:
                    guard()
                    app = self._app_source()
                    guard()
                    future.set_result(item.job(app, guard, followup))
                except _Abandoned as exc:
                    future.set_result(exc.result)
                except PowerPointUnavailable as exc:
                    future.set_result(_result(ResultStatus.NOT_AVAILABLE, exc.message_vi, exc.code))
                except Exception as exc:
                    # A transported traceback can retain COM wrappers in helper frames
                    # and release them on the caller's thread after this apartment closes.
                    log.error("%s command failed: %s", self.app_label, type(exc).__name__)
                    future.set_result(
                        _result(
                            ResultStatus.ERROR,
                            f"Lỗi khi điều khiển {self.app_label}.",
                            f"{self.code_prefix}_error",
                        )
                    )
                finally:
                    app = None  # Drop the last application reference before CoUninitialize.

    def _check_request(
        self, request: _Request, stopping: threading.Event, *, check_target: bool = True
    ) -> None:
        self._check_alive(request, stopping)
        if check_target and self._context is not None:
            try:
                still_target = self._context.is_still_target(request.context)
            except Exception as exc:
                log.error("%s target check failed: %s", self.app_label, type(exc).__name__)
                still_target = False
            if not still_target:
                raise _Abandoned(
                    _result(
                        ResultStatus.NEEDS_RETRY, "Cửa sổ đích đã thay đổi, hãy thử lại.", "target_changed"
                    )
                )
        # A slow context check must not permit an already expired action.
        self._check_alive(request, stopping)

    def _check_alive(self, request: _Request, stopping: threading.Event) -> None:
        if stopping.is_set() or (request.cancel is not None and request.cancel.is_set()):
            raise _Abandoned(self._cancelled_result())
        if request.abandoned.is_set() or time.monotonic() >= request.deadline:
            raise _Abandoned(
                _result(
                    ResultStatus.ERROR, f"{self.app_label} không phản hồi.", f"{self.code_prefix}_timeout"
                )
            )
