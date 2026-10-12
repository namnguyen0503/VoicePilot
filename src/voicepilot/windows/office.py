"""Create one blank document in the captured Office window, including its Home page.

Route 1 presses the single visible blank tile on the Home page, found through MSAA.
Route 2 sends Ctrl+N and checks that a new window of the same process came to the foreground.
MSAA pointers live only inside one _Session, which releases them in the COM apartment of the worker.
"""

from __future__ import annotations

import contextlib
import ctypes
import functools
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any, ClassVar

from voicepilot.contracts import ActionResult, AppKind, Intent, ResultStatus, WindowContext
from voicepilot.windows.com_worker import ComWorker

OFFICE_MESSAGES = {
    Intent.WORD_NEW_DOCUMENT: "Đã tạo tài liệu Word trắng.",
    Intent.EXCEL_NEW_WORKBOOK: "Đã tạo bảng tính Excel trắng.",
    Intent.PPT_NEW_PRESENTATION: "Đã tạo bài trình chiếu trắng.",
}
BLANK_NAMES = {
    AppKind.WORD: {"blank document", "new blank document", "tài liệu trống", "tài liệu trắng"},
    AppKind.EXCEL: {"blank workbook", "new blank workbook", "sổ làm việc trống", "sổ làm việc trắng"},
    AppKind.POWERPOINT: {
        "blank presentation",
        "new blank presentation",
        "bản trình bày trống",
        "bài trình chiếu trống",
        "bản trình bày trắng",
    },
}
_IID_ACCESSIBLE = uuid.UUID("618736e0-3c3d-11cf-810c-00aa00389b71").bytes_le
_OBJID_CLIENT = 0xFFFFFFFC
_INVISIBLE = 0x8000
_OFFSCREEN = 0x10000
_VT_I4 = 3
_VT_DISPATCH = 9
# MSAA roles that can be pressed to start a document: list item, push button, menu item.
_TILE_ROLES = frozenset({0x22, 0x2B, 0x0C})
_WALK_DEPTH = 18
_WALK_NODE_BUDGET = 800
_WALK_SECONDS = 2.0
_MAX_CHILDREN = 800
_NEW_WINDOW_SECONDS = 2.5
_POLL_SECONDS = 0.1

BlankSource = Callable[[WindowContext, Callable[[], None]], bool]


class OfficeController(ComWorker):
    """Blank document creation on the guarded COM worker; accessibility objects stay in its apartment."""

    app_label: ClassVar[str] = "Office"
    code_prefix: ClassVar[str] = "office"

    def __init__(
        self,
        *,
        blank_source: BlankSource | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        foreground: Callable[[], tuple[int, int]] | None = None,
        **kwargs: Any,
    ) -> None:
        """Create the controller.

        Args:
            blank_source: Presses the Home-page blank tile. Returns True when a tile was invoked,
                False when no unambiguous tile exists. Defaults to the MSAA walk.
            clock: Monotonic clock for the tile-walk and new-window deadlines.
            sleep: Pause between foreground polls.
            foreground: Returns (hwnd, pid) of the foreground window. Defaults to user32.
        """
        super().__init__(app_source=lambda: None, **kwargs)
        self._clock = clock
        self._sleep = sleep
        self._foreground = foreground or _foreground_window
        self._blank_source = blank_source or functools.partial(blank_from_accessibility, clock=clock)

    def new_blank(
        self, ctx: WindowContext, keys: Any, intent: Intent, *, cancel: Any = None
    ) -> ActionResult:
        """Create a blank document for intent in the window described by ctx."""

        def job(_app: Any, guard: Callable[[], None], _followup: Callable[[], None]) -> ActionResult:
            if self._blank_source(ctx, guard):
                return ActionResult(ResultStatus.OK, OFFICE_MESSAGES[intent])
            guard()  # cancel, deadline and target are checked again before any keystroke
            keys.send_hotkey("ctrl", "n")
            if self._new_window_opened(ctx):
                return ActionResult(ResultStatus.OK, OFFICE_MESSAGES[intent])
            return ActionResult(
                ResultStatus.ERROR, "Office không tạo được tài liệu mới.", "office_new_unverified"
            )

        return self._submit(job, ctx, cancel)

    def _new_window_opened(self, ctx: WindowContext) -> bool:
        """Poll until a different window of the same process is in the foreground, or give up."""
        deadline = self._clock() + _NEW_WINDOW_SECONDS
        while True:
            hwnd, pid = self._foreground()
            if hwnd and hwnd != ctx.hwnd and pid == ctx.pid:
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(_POLL_SECONDS)


def blank_from_accessibility(
    ctx: WindowContext, guard: Callable[[], None], *, clock: Callable[[], float] = time.monotonic
) -> bool:
    """Invoke the one visible blank tile of the Home page; True when a tile was invoked.

    The guard runs after the tile is found and before it is pressed, inside the same session.
    """
    with _Accessible.root(ctx.hwnd) as root:
        tiles = _find_blank_tiles(root, BLANK_NAMES[ctx.app_kind], clock=clock)
        if len(tiles) != 1:
            return False
        guard()
        tiles[0].invoke()
        return True


def _find_blank_tiles(
    root: _Accessible,
    names: set[str],
    *,
    clock: Callable[[], float] = time.monotonic,
    seconds: float = _WALK_SECONDS,
    budget: int = _WALK_NODE_BUDGET,
) -> list[_Accessible]:
    """Return every visible tile named like a blank template, or [] when the walk hit a limit.

    A walk that reaches the node budget, the depth limit or the deadline is treated as not found,
    so an ambiguous or unfinished search never presses anything.
    """
    deadline = clock() + seconds
    remaining = [budget]
    tiles: list[_Accessible] = []

    def visit(node: _Accessible, depth: int) -> bool:
        if remaining[0] <= 0 or depth > _WALK_DEPTH or clock() >= deadline:
            return False
        remaining[0] -= 1
        if node.state & (_INVISIBLE | _OFFSCREEN):
            return True
        if node.name.strip().casefold() in names and node.role in _TILE_ROLES:
            tiles.append(node)  # a tile's own children are not candidates
            return True
        for child in node.children():
            if not visit(child, depth + 1):
                return False
        return True

    complete = visit(root, 0)
    return tiles if complete else []


class _Value(ctypes.Union):
    _fields_ = [
        ("pointer", ctypes.c_void_p),
        ("number", ctypes.c_int32),
        ("padding", ctypes.c_byte * (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 8)),
    ]


class _Variant(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("reserved", ctypes.c_uint16 * 3), ("value", _Value)]


@functools.cache
def _oleacc() -> Any:
    """Load oleacc once with fixed signatures (Windows only)."""
    lib = ctypes.OleDLL("oleacc")
    lib.AccessibleObjectFromWindow.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.AccessibleObjectFromWindow.restype = ctypes.c_long
    lib.AccessibleChildren.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int32,
        ctypes.c_int32,
        ctypes.POINTER(_Variant),
        ctypes.POINTER(ctypes.c_int32),
    ]
    lib.AccessibleChildren.restype = ctypes.c_long
    return lib


@functools.cache
def _variant_clear() -> Any:
    """Load VariantClear from oleaut32 once (Windows only)."""
    clear = ctypes.OleDLL("oleaut32").VariantClear
    clear.argtypes = [ctypes.POINTER(_Variant)]
    clear.restype = ctypes.c_long
    return clear


@functools.cache
def _user32() -> Any:
    """Load the user32 foreground queries once (Windows only)."""
    lib = ctypes.WinDLL("user32")
    lib.GetForegroundWindow.restype = ctypes.c_void_p
    lib.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    lib.GetWindowThreadProcessId.restype = ctypes.c_uint32
    return lib


def _foreground_window() -> tuple[int, int]:
    """Return (hwnd, pid) of the foreground window, or (0, 0) when there is none (Windows only)."""
    user32 = _user32()
    hwnd = user32.GetForegroundWindow() or 0
    if not hwnd:
        return 0, 0
    pid = ctypes.c_uint32()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(hwnd), int(pid.value)


def _open_root(hwnd: int) -> int:
    """Return a new reference to the client IAccessible of hwnd; the caller releases it."""
    pointer = ctypes.c_void_p()
    status = _oleacc().AccessibleObjectFromWindow(
        hwnd, _OBJID_CLIENT, _IID_ACCESSIBLE, ctypes.byref(pointer)
    )
    if status < 0 or not pointer.value:
        raise OSError("Office accessibility unavailable")
    return pointer.value


def _query_accessible(pointer: int) -> int:
    """Return a new IAccessible reference for an IDispatch from AccessibleChildren."""
    # AccessibleChildren returns IDispatch, whose vtable is not IAccessible's.
    out = ctypes.c_void_p()
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    query = ctypes.WINFUNCTYPE(
        ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
    )(table[0])
    if query(pointer, _IID_ACCESSIBLE, ctypes.byref(out)) < 0 or not out.value:
        raise OSError("Office child does not expose IAccessible")
    return out.value


def _accessible_children(pointer: int, count: int, session: _Session) -> list[tuple[str, int]]:
    """Return (kind, value) per child: ("accessible", owned pointer) or ("child", child id)."""
    values = (_Variant * count)()
    obtained = ctypes.c_int32()
    clear = _variant_clear()
    try:
        _oleacc().AccessibleChildren(pointer, 0, count, values, ctypes.byref(obtained))
        children: list[tuple[str, int]] = []
        for value in values[: obtained.value]:
            if value.vt == _VT_DISPATCH and value.value.pointer:
                accessible = _query_accessible(value.value.pointer)
                session.own(accessible)  # owned before the VARIANT that held the IDispatch is cleared
                children.append(("accessible", accessible))
            elif value.vt == _VT_I4:
                children.append(("child", value.value.number))
        return children
    finally:
        for value in values:
            clear(ctypes.byref(value))


def _release(pointer: int) -> None:
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(pointer)


def _dispatch_for(pointer: int) -> Any:
    """Wrap an IAccessible pointer for Invoke-based property access (Windows only)."""
    import pythoncom
    import win32com.client

    return win32com.client.Dispatch(pythoncom.ObjectFromAddress(pointer, pythoncom.IID_IDispatch))


class _Session:
    """Owns every raw IAccessible reference taken during one walk and releases each exactly once."""

    def __init__(self) -> None:
        self._pointers: list[int] = []
        self.closed = False

    def own(self, pointer: int) -> None:
        self._pointers.append(pointer)

    def close(self) -> None:
        self.closed = True
        pointers, self._pointers = self._pointers, []
        for pointer in reversed(pointers):
            _release(pointer)


class _Accessible:
    """MSAA node. A node built from a pointer must be given a pointer its session already owns.

    Child-id nodes hold their owner, so the owner's pointer and dispatch stay alive while they are used.
    """

    def __init__(self, pointer: int, session: _Session) -> None:
        self._pointer = pointer
        self._session = session
        self._owner: _Accessible | None = None
        self._child_id = 0
        self._dispatch = _dispatch_for(pointer)

    @classmethod
    def _child(cls, owner: _Accessible, child_id: int) -> _Accessible:
        node = cls.__new__(cls)
        node._pointer = owner._pointer
        node._session = owner._session
        node._owner = owner
        node._child_id = child_id
        node._dispatch = owner._dispatch
        return node

    @staticmethod
    @contextlib.contextmanager
    def root(hwnd: int) -> Iterator[_Accessible]:
        """Yield the window's root node; every reference taken inside is released on exit."""
        session = _Session()
        try:
            pointer = _open_root(hwnd)
            session.own(pointer)
            yield _Accessible(pointer, session)
        finally:
            session.close()

    def _check_open(self) -> None:
        if self._session.closed:
            raise OSError("Office accessibility session is closed")

    def _get(self, dispid: int) -> Any:
        self._check_open()
        return self._dispatch._oleobj_.Invoke(dispid, 0, 2, True, self._child_id)

    @property
    def name(self) -> str:
        return str(self._get(-5003) or "")

    @property
    def state(self) -> int:
        return int(self._get(-5007) or 0)

    @property
    def role(self) -> int:
        try:
            return int(self._get(-5006) or 0)
        except (TypeError, ValueError):
            return 0  # string roles are custom roles, never tiles

    def invoke(self) -> None:
        self._check_open()
        self._dispatch._oleobj_.Invoke(-5018, 0, 1, False, self._child_id)

    def children(self) -> Iterator[_Accessible]:
        self._check_open()
        if self._child_id:
            return
        count = min(int(self._dispatch.accChildCount), _MAX_CHILDREN)
        if count <= 0:
            return
        for kind, value in _accessible_children(self._pointer, count, self._session):
            if kind == "accessible":
                yield _Accessible(value, self._session)
            else:
                yield _Accessible._child(self, value)
