"""Create one blank document in the captured Office window, including its Home page."""

from __future__ import annotations

import ctypes
import threading
import uuid
from collections.abc import Callable

from voicepilot.contracts import ActionResult, AppKind, Intent, ResultStatus
from voicepilot.windows.powerpoint import PowerPointController

OFFICE_ACTIONS = {
    Intent.WORD_NEW_DOCUMENT: (AppKind.WORD, "winword.exe", "Đã tạo tài liệu Word trắng."),
    Intent.EXCEL_NEW_WORKBOOK: (AppKind.EXCEL, "excel.exe", "Đã tạo bảng tính Excel trắng."),
    Intent.PPT_NEW_PRESENTATION: (AppKind.POWERPOINT, "powerpnt.exe", "Đã tạo bài trình chiếu trắng."),
}
BLANK_NAMES = {
    "winword.exe": {"blank document", "new blank document", "tài liệu trống", "tài liệu trắng"},
    "excel.exe": {"blank workbook", "new blank workbook", "sổ làm việc trống", "sổ làm việc trắng"},
    "powerpnt.exe": {
        "blank presentation",
        "new blank presentation",
        "bản trình bày trống",
        "bài trình chiếu trống",
        "bản trình bày trắng",
    },
}
_IID_ACCESSIBLE = uuid.UUID("618736e0-3c3d-11cf-810c-00aa00389b71").bytes_le
_INVISIBLE = 0x8000
_OFFSCREEN = 0x10000


class OfficeController(PowerPointController):
    """Reuse the guarded COM queue; accessibility objects stay inside its apartment."""

    def __init__(self, *, blank_source=None, **kwargs):
        super().__init__(app_source=lambda: None, app_label="Office", code_prefix="office", **kwargs)
        self._blank_source = blank_source or _blank_action

    def new_blank(self, ctx, keys, *, cancel: threading.Event | None = None):
        def job(_app, guard, _followup):
            blank = self._blank_source(ctx)
            guard()
            if blank is not None:
                blank()
            else:
                keys.send_hotkey("ctrl", "n")
            message = next(value[2] for value in OFFICE_ACTIONS.values() if value[0] is ctx.app_kind)
            return ActionResult(ResultStatus.OK, message)

        return self._submit(job, ctx, cancel)


def _find_blank(node, names, *, budget=None, depth=0) -> Callable[[], None] | None:
    """Walk visible MSAA objects only; never invoke templates merely containing 'blank'."""
    budget = [2500] if budget is None else budget
    budget[0] -= 1
    if depth > 18 or budget[0] < 0:
        raise OSError("Office accessibility traversal limit")
    if node.state & (_INVISIBLE | _OFFSCREEN):
        return None
    if node.name.strip().casefold() in names:
        return node.invoke
    for child in node.children():
        action = _find_blank(child, names, budget=budget, depth=depth + 1)
        if action is not None:
            return action
    return None


def _blank_action(ctx):
    """Read the native window's accessibility model, without attaching to another Office instance."""
    with _Accessible.root(ctx.hwnd) as root:
        return _find_blank(root, BLANK_NAMES[ctx.process_name.casefold()])


class _Value(ctypes.Union):
    _fields_ = [
        ("pointer", ctypes.c_void_p),
        ("number", ctypes.c_int32),
        ("padding", ctypes.c_byte * (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 8)),
    ]


class _Variant(ctypes.Structure):
    _fields_ = [("vt", ctypes.c_uint16), ("reserved", ctypes.c_uint16 * 3), ("value", _Value)]


def _release(pointer):
    table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(table[2])(pointer)


class _Accessible:
    """MSAA adapter: child dispatches must be queried for IAccessible before AccessibleChildren."""

    def __init__(self, pointer, *, child_id=0, owner=None):
        import pythoncom
        import win32com.client

        self._pointer = pointer
        self._owner = owner
        self._child_id = child_id
        self._dispatch = win32com.client.Dispatch(
            pythoncom.ObjectFromAddress(pointer, pythoncom.IID_IDispatch)
        )

    @staticmethod
    def root(hwnd):
        import contextlib

        @contextlib.contextmanager
        def acquire():
            pointer = ctypes.c_void_p()
            lib = ctypes.OleDLL("oleacc")
            lib.AccessibleObjectFromWindow.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_void_p),
            ]
            lib.AccessibleObjectFromWindow.restype = ctypes.c_long
            lib.AccessibleObjectFromWindow(hwnd, 0xFFFFFFFC, _IID_ACCESSIBLE, ctypes.byref(pointer))
            if not pointer.value:
                raise OSError("Office accessibility unavailable")
            try:
                node = _Accessible(pointer.value)
            finally:
                _release(pointer.value)  # ObjectFromAddress owns a separate reference.
            try:
                yield node
            finally:
                node = None

        return acquire()

    def _get(self, dispid):
        return self._dispatch._oleobj_.Invoke(dispid, 0, 2, True, self._child_id)

    @property
    def name(self):
        return str(self._get(-5003) or "")

    @property
    def state(self):
        return int(self._get(-5007) or 0)

    def invoke(self):
        self._dispatch._oleobj_.Invoke(-5018, 0, 1, False, self._child_id)

    def children(self):
        if self._child_id:
            return
        count = min(int(self._dispatch.accChildCount), 2500)
        if not count:
            return
        lib = ctypes.OleDLL("oleacc")
        lib.AccessibleChildren.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.POINTER(_Variant),
            ctypes.POINTER(ctypes.c_int32),
        ]
        lib.AccessibleChildren.restype = ctypes.c_long
        values = (_Variant * count)()
        obtained = ctypes.c_int32()
        clear = ctypes.OleDLL("oleaut32").VariantClear
        clear.argtypes = [ctypes.POINTER(_Variant)]
        clear.restype = ctypes.c_long
        try:
            lib.AccessibleChildren(self._pointer, 0, count, values, ctypes.byref(obtained))
            for value in values[: obtained.value]:
                if value.vt == 9 and value.value.pointer:
                    # AccessibleChildren returns IDispatch, whose vtable is not IAccessible's.
                    pointer = ctypes.c_void_p()
                    table = ctypes.cast(
                        value.value.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
                    ).contents
                    query = ctypes.WINFUNCTYPE(
                        ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
                    )(table[0])
                    if query(value.value.pointer, _IID_ACCESSIBLE, ctypes.byref(pointer)) < 0:
                        raise OSError("Office child does not expose IAccessible")
                    try:
                        child = _Accessible(pointer.value)
                    finally:
                        _release(pointer.value)
                    yield child
                elif value.vt == 3:
                    yield _Accessible(self._pointer, child_id=value.value.number, owner=self)
        finally:
            for value in values:
                clear(ctypes.byref(value))
