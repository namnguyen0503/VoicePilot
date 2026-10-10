"""PowerPoint automation over COM: save, save as, slide navigation and slide show control.

Every COM object is created and dropped inside one job, and every job runs on the single
worker thread of PowerPointController, which owns a COM apartment (pythoncom.CoInitialize).
Public calls wait for the job with a timeout.

The job functions (save_presentation, next_slide, ...) take the PowerPoint Application
object as a parameter and use only attribute access, so they are unit-tested with fakes.
Pure helpers (sanitize_filename, match_by_title) are tested directly.

The presentation for a captured window is found by matching the window title against the
open presentations' names, since the official object model does not expose the window handle
of a document or slide show window.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import queue
import threading
import time
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from voicepilot.contracts import ActionResult, ContextProvider, ResultStatus, WindowContext
from voicepilot.windows.desktop import documents_dir, unique_path

log = logging.getLogger(__name__)

PROGID = "PowerPoint.Application"
PP_SAVE_AS_OPEN_XML_PRESENTATION = 24  # ppSaveAsOpenXMLPresentation
DEFAULT_TIMEOUT_S = 8.0
_SHUTDOWN_TIMEOUT_S = 2.0
_MAX_STEM_CHARS = 120
_MAX_PATH_CHARS = 240
_COPY_SUFFIX_CHARS = len(" (1000)")  # room kept for unique_path's " (n)" when a name is taken
_INVALID_CHARS = frozenset('<>:"/\\|?*')
_REJECTED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co"})
# Stored casefolded: names are compared after NFKC, which maps superscript digits such as COM¹ to COM1.
_RESERVED_NAMES = frozenset(
    name.casefold()
    for name in (
        {"CON", "PRN", "AUX", "NUL"}
        | {f"COM{number}" for number in range(0, 10)}
        | {f"LPT{number}" for number in range(0, 10)}
    )
)
_PPTX_SUFFIX = ".pptx"
_SAVE_SUBDIR = "VoicePilot"
BeforeAction = Callable[[], None]


def _unguarded() -> None:
    """Allow direct helper calls without worker scheduling or window checks."""


class PowerPointUnavailable(Exception):
    """PowerPoint cannot serve the request; the message is shown to the user as NOT_AVAILABLE."""

    def __init__(self, message_vi: str, code: str) -> None:
        super().__init__(message_vi)
        self.message_vi = message_vi
        self.code = code


@dataclass(frozen=True)
class PresentationTarget:
    """The presentation a command applies to.

    Attributes:
        presentation: The PowerPoint Presentation object.
        show: The running SlideShowWindow of that presentation, or None.
        window: The DocumentWindow in edit view, or None.
    """

    presentation: Any
    show: Any | None
    window: Any | None


def sanitize_filename(name: str) -> str | None:
    """Return a safe ".pptx" file name for a spoken name, or None when the name is not usable.

    Accepts an optional trailing ".pptx". Keeps Vietnamese letters, normalizes to NFC,
    strips surrounding spaces and trailing dots, and rejects empty names, stems longer than
    120 characters, characters forbidden in Windows file names, and invisible or control
    characters (Unicode categories Cc, Cf, Cs, Co). Reserved device names (CON, NUL, COM1...)
    are rejected too, also when written with compatibility characters such as COM¹, in any
    case, and when followed by an extension or spaces.
    """
    text = unicodedata.normalize("NFC", name).strip()
    if text.lower().endswith(_PPTX_SUFFIX):
        text = text[: -len(_PPTX_SUFFIX)]
    stem = text.strip().rstrip(". ")
    if not stem or len(stem) > _MAX_STEM_CHARS:
        return None
    if any(char in _INVALID_CHARS or unicodedata.category(char) in _REJECTED_CATEGORIES for char in stem):
        return None
    if _is_reserved(stem):
        return None
    return f"{stem}{_PPTX_SUFFIX}"


def _is_reserved(stem: str) -> bool:
    """Return True when Windows reserves the name: the text before the first dot, compared after NFKC."""
    base = unicodedata.normalize("NFKC", stem).split(".", 1)[0].strip(" ")
    return base.casefold() in _RESERVED_NAMES


def _fit_to_path(name: str, folder: Path) -> str | None:
    """Shorten the stem of a sanitized name so folder, name and a " (n)" suffix fit in _MAX_PATH_CHARS.

    Returns None when the folder leaves no room for a usable stem or the shortened stem is reserved.
    """
    room = _MAX_PATH_CHARS - len(str(folder)) - 1 - len(_PPTX_SUFFIX) - _COPY_SUFFIX_CHARS
    stem = name[: -len(_PPTX_SUFFIX)]
    if len(stem) > room:
        stem = _truncate_graphemes(stem, room).rstrip(". ")
    if not stem or _is_reserved(stem):
        return None
    return f"{stem}{_PPTX_SUFFIX}"


def _truncate_graphemes(text: str, limit: int) -> str:
    """Return the longest prefix of text within limit characters that keeps base letters with their marks."""
    kept: list[str] = []
    length = 0
    for cluster in _clusters(text):
        if length + len(cluster) > limit:
            break
        kept.append(cluster)
        length += len(cluster)
    return "".join(kept)


def _clusters(text: str) -> list[str]:
    """Split text into base characters, each with the combining marks (categories M*) that follow it."""
    clusters: list[str] = []
    for char in text:
        if clusters and unicodedata.category(char).startswith("M"):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def match_by_title(title: str, names: list[str]) -> int | None:
    """Return the index of the longest non-empty name that occurs in title, case-insensitively."""
    lowered = title.casefold()
    best_index: int | None = None
    best_length = 0
    for index, name in enumerate(names):
        key = name.strip().casefold()
        if key and key in lowered and len(key) > best_length:
            best_index, best_length = index, len(key)
    return best_index


def resolve_target(app: Any, title: str) -> PresentationTarget:
    """Pick the presentation for a window title.

    Order: the presentation whose name appears in the title; otherwise the first presentation
    with a running slide show; otherwise the active presentation.

    Raises:
        PowerPointUnavailable: If no presentation is open or no window exists for it.
    """
    presentations = _collection(app.Presentations)
    if not presentations:
        raise PowerPointUnavailable("PowerPoint chưa mở tài liệu nào.", "ppt_no_document")
    index = match_by_title(title, [str(p.Name) for p in presentations])
    if index is not None:
        presentation = presentations[index]
    else:
        running = [p for p in presentations if _running_show(p) is not None]
        presentation = running[0] if running else app.ActivePresentation
    windows = _collection(presentation.Windows)
    return PresentationTarget(presentation, _running_show(presentation), windows[0] if windows else None)


def save_presentation(app: Any, title: str, *, before_action: BeforeAction = _unguarded) -> ActionResult:
    """Save the target presentation to its current file, or ask for a name when it was never saved."""
    presentation = resolve_target(app, title).presentation
    if not str(presentation.Path or ""):
        return _result(
            ResultStatus.INVALID_PARAM,
            "Tệp chưa được lưu. Hãy nói: lưu thành tên tệp.",
            "unsaved",
        )
    save = presentation.Save
    before_action()
    save()
    return _result(ResultStatus.OK, "Đã lưu", data={"path": str(presentation.FullName)})


def save_as_presentation(
    app: Any, title: str, filename: str, default_dir: Path, *, before_action: BeforeAction = _unguarded
) -> ActionResult:
    """Save a copy of the target presentation as filename as a .pptx.

    The file goes next to the current file, or into default_dir for a presentation that has
    never been saved. A name that already exists gets " (2)", " (3)" and so on. The stem is
    shortened when needed so the full path stays within 240 characters.
    """
    name = sanitize_filename(filename)
    if name is None:
        return _result(ResultStatus.INVALID_PARAM, "Tên tệp không hợp lệ, hãy nói lại.", "invalid_filename")
    presentation = resolve_target(app, title).presentation
    folder = _save_folder(presentation.Path, default_dir)
    fitted = _fit_to_path(name, folder)
    if fitted is None:
        return _result(ResultStatus.INVALID_PARAM, "Tên tệp không hợp lệ, hãy nói lại.", "invalid_filename")
    before_action()
    folder.mkdir(parents=True, exist_ok=True)
    path = unique_path(folder, fitted)
    save_as = presentation.SaveAs
    before_action()
    save_as(str(path), PP_SAVE_AS_OPEN_XML_PRESENTATION)
    return _result(ResultStatus.OK, f"Đã lưu thành {path.name}", data={"path": str(path)})


def next_slide(app: Any, title: str, *, before_action: BeforeAction = _unguarded) -> ActionResult:
    """Move to the next slide in the slide show or in the edit view."""
    return _step_slide(app, title, +1, before_action)


def previous_slide(app: Any, title: str, *, before_action: BeforeAction = _unguarded) -> ActionResult:
    """Move to the previous slide in the slide show or in the edit view."""
    return _step_slide(app, title, -1, before_action)


def start_show(
    app: Any, title: str, *, before_action: BeforeAction = _unguarded,
    before_followup: BeforeAction = _unguarded,
) -> ActionResult:
    """Start the slide show of the target presentation from its first slide."""
    target = resolve_target(app, title)
    if target.show is not None:
        return _result(ResultStatus.OK, "Trình chiếu đang chạy.")
    run = target.presentation.SlideShowSettings.Run
    before_action()
    show = run()
    goto = show.View.GotoSlide
    # Run can legitimately create and focus a new slide show window. Only recheck
    # cancellation/deadline for this follow-up, not the original foreground HWND.
    before_followup()
    goto(1)
    return _result(ResultStatus.OK, "Đang bắt đầu trình chiếu.")


def stop_show(app: Any, title: str, *, before_action: BeforeAction = _unguarded) -> ActionResult:
    """End the running slide show and return to editing."""
    target = resolve_target(app, title)
    if target.show is None:
        return _result(ResultStatus.OK, "Không có trình chiếu nào đang chạy.")
    exit_show = target.show.View.Exit
    before_action()
    exit_show()
    return _result(ResultStatus.OK, "Đã dừng trình chiếu.")


def _step_slide(app: Any, title: str, step: int, before_action: BeforeAction) -> ActionResult:
    target = resolve_target(app, title)
    count = int(target.presentation.Slides.Count)
    if target.show is not None:
        view = target.show.View
        position = int(view.CurrentShowPosition)
        if step > 0 and position >= count:
            return _boundary("Đã ở slide cuối.", "last_slide")
        if step < 0 and position <= 1:
            return _boundary("Đã ở slide đầu.", "first_slide")
        move = view.Next if step > 0 else view.Previous
        before_action()
        move()
    else:
        view = _edit_view(target)
        index = int(view.Slide.SlideIndex)
        if step > 0 and index >= count:
            return _boundary("Đã ở slide cuối.", "last_slide")
        if step < 0 and index <= 1:
            return _boundary("Đã ở slide đầu.", "first_slide")
        goto = view.GotoSlide
        before_action()
        goto(index + step)
    message = "Đã chuyển slide tiếp." if step > 0 else "Đã chuyển slide trước."
    return _result(ResultStatus.OK, message)


def _edit_view(target: PresentationTarget) -> Any:
    if target.window is None:
        raise PowerPointUnavailable("Không tìm thấy cửa sổ PowerPoint.", "ppt_no_window")
    return target.window.View


def _save_folder(presentation_path: Any, default_dir: Path) -> Path:
    """Return the folder of a saved presentation, or default_dir for unsaved or cloud-hosted ones."""
    text = str(presentation_path or "")
    if text and "://" not in text:
        return Path(text)
    return default_dir


def _running_show(presentation: Any) -> Any | None:
    try:
        return presentation.SlideShowWindow
    except Exception:
        return None


def _collection(items: Any) -> list[Any]:
    """Return the members of an Office collection, which is 1-based."""
    return [items.Item(index) for index in range(1, int(items.Count) + 1)]


def _result(
    status: ResultStatus, message: str, code: str | None = None, data: dict[str, Any] | None = None
) -> ActionResult:
    return ActionResult(status, message, code, data or {})


def _boundary(message: str, code: str) -> ActionResult:
    return _result(ResultStatus.BOUNDARY, message, code)


@contextlib.contextmanager
def _com_apartment() -> Iterator[None]:
    """Initialize COM on the current thread for the duration of the block (Windows only)."""
    import pythoncom

    pythoncom.CoInitialize()
    try:
        yield
    finally:
        pythoncom.CoUninitialize()


def _active_app() -> Any:
    """Return the running PowerPoint Application without starting PowerPoint (Windows only).

    Raises:
        PowerPointUnavailable: If PowerPoint is not running.
    """
    import win32com.client

    try:
        return win32com.client.GetActiveObject(PROGID)
    except Exception as exc:
        raise PowerPointUnavailable("PowerPoint chưa mở.", "ppt_not_running") from exc


Job = Callable[[Any, BeforeAction, BeforeAction], ActionResult]


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


class PowerPointController:
    """Runs PowerPoint jobs on one COM worker thread and maps failures to ActionResult.

    The worker starts on the first call. Jobs receive the Application object, which is
    acquired on the worker thread for each job and never leaves it.
    """

    def __init__(
        self,
        *,
        app_source: Callable[[], Any] | None = None,
        apartment: Callable[[], contextlib.AbstractContextManager[Any]] | None = None,
        save_dir: Path | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        context_provider: ContextProvider | None = None,
    ) -> None:
        """Create the controller. Nothing is started until the first call.

        Args:
            app_source: Returns the PowerPoint Application or raises PowerPointUnavailable.
                Defaults to GetActiveObject on the running instance.
            apartment: Context manager that prepares COM on the worker thread. Defaults to CoInitialize.
            save_dir: Folder for presentations that were never saved. Defaults to Documents\\VoicePilot.
            timeout_s: How long a public call waits for PowerPoint before reporting a timeout.
            context_provider: Rechecks the target after COM resolution and before mutation.
        """
        self._app_source = app_source or _active_app
        self._apartment = apartment or _com_apartment
        self._save_dir = save_dir
        self._timeout_s = timeout_s
        self._context = context_provider
        self._jobs: queue.Queue[_Request | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def save(self, ctx: WindowContext) -> ActionResult:
        """Save the presentation of the target window."""
        return self._submit(lambda app, guard, _: save_presentation(app, ctx.title, before_action=guard), ctx)

    def save_as(self, ctx: WindowContext, filename: str) -> ActionResult:
        """Save the presentation of the target window under a new name."""
        save_dir = self._default_save_dir()
        return self._submit(lambda app, guard, _: save_as_presentation(
            app, ctx.title, filename, save_dir, before_action=guard), ctx)

    def next_slide(self, ctx: WindowContext) -> ActionResult:
        """Go to the next slide."""
        return self._submit(lambda app, guard, _: next_slide(app, ctx.title, before_action=guard), ctx)

    def previous_slide(self, ctx: WindowContext) -> ActionResult:
        """Go to the previous slide."""
        return self._submit(lambda app, guard, _: previous_slide(app, ctx.title, before_action=guard), ctx)

    def start_show(self, ctx: WindowContext) -> ActionResult:
        """Start the slide show from slide 1."""
        return self._submit(lambda app, guard, followup: start_show(
            app, ctx.title, before_action=guard, before_followup=followup), ctx)

    def stop_show(self, ctx: WindowContext) -> ActionResult:
        """End the running slide show."""
        return self._submit(lambda app, guard, _: stop_show(app, ctx.title, before_action=guard), ctx)

    def shutdown(self) -> None:
        """Stop the worker thread. Pending jobs are abandoned."""
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is None:
            return
        self._jobs.put(None)
        thread.join(timeout=_SHUTDOWN_TIMEOUT_S)

    def _default_save_dir(self) -> Path:
        return self._save_dir if self._save_dir is not None else documents_dir() / _SAVE_SUBDIR

    def _submit(self, job: Job, ctx: WindowContext) -> ActionResult:
        future: concurrent.futures.Future[ActionResult] = concurrent.futures.Future()
        request = _Request(future, job, ctx, time.monotonic() + self._timeout_s, threading.Event())
        self._ensure_worker()
        self._jobs.put(request)
        try:
            return future.result(timeout=max(0, request.deadline - time.monotonic()))
        except concurrent.futures.TimeoutError:
            request.abandoned.set()
            future.cancel()  # Skip pending jobs; the event also stops a running job after COM returns.
            log.error("PowerPoint did not answer within %.1f s", self._timeout_s)
            return _result(ResultStatus.ERROR, "PowerPoint không phản hồi.", "ppt_timeout")
        except PowerPointUnavailable as exc:
            return _result(ResultStatus.NOT_AVAILABLE, exc.message_vi, exc.code)
        except Exception as exc:
            log.error("PowerPoint command failed: %s", type(exc).__name__)
            return _result(ResultStatus.ERROR, "Lỗi khi điều khiển PowerPoint.", "ppt_error")

    def _ensure_worker(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._work, name="voicepilot-powerpoint", daemon=True)
                self._thread.start()

    def _work(self) -> None:
        with self._apartment():
            while True:
                item = self._jobs.get()
                if item is None:
                    return
                future = item.future
                if not future.set_running_or_notify_cancel():
                    continue
                def guard(request: _Request = item) -> None:
                    self._check_request(request)
                def followup(request: _Request = item) -> None:
                    self._check_request(request, check_target=False)
                try:
                    guard()
                    app = self._app_source()
                    guard()
                    future.set_result(item.job(app, guard, followup))
                except _Abandoned as exc:
                    future.set_result(exc.result)
                except Exception as exc:
                    future.set_exception(exc)

    def _check_request(self, request: _Request, *, check_target: bool = True) -> None:
        if request.abandoned.is_set() or time.monotonic() >= request.deadline:
            raise _Abandoned(_result(ResultStatus.ERROR, "PowerPoint không phản hồi.", "ppt_timeout"))
        if check_target and self._context is not None:
            try:
                still_target = self._context.is_still_target(request.context)
            except Exception as exc:
                log.error("PowerPoint target check failed: %s", type(exc).__name__)
                still_target = False
            if not still_target:
                raise _Abandoned(_result(ResultStatus.NEEDS_RETRY,
                                         "Cửa sổ đích đã thay đổi, hãy thử lại.", "target_changed"))
        # A slow context check must not permit an already expired action.
        if request.abandoned.is_set() or time.monotonic() >= request.deadline:
            raise _Abandoned(_result(ResultStatus.ERROR, "PowerPoint không phản hồi.", "ppt_timeout"))
