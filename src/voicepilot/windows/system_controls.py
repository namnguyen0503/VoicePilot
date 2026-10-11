"""Windows power, display, volume and global media-session controls.

Imports of WMI, WinRT and native DLLs are lazy. Effects are guarded by the executor
and cancellation is checked again immediately before each OS operation.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from voicepilot.contracts import ActionResult, Intent, ResultStatus

POWER_SAVER_GUID = "a1841308-3541-4fab-bc81-f71556f20b4a"
MEDIA_METHODS = {
    "play": "try_play_async",
    "pause": "try_pause_async",
    "next": "try_skip_next_async",
    "previous": "try_skip_previous_async",
}
MEDIA_MESSAGES = {
    "play": "Đã phát nhạc.",
    "pause": "Đã tạm dừng nhạc.",
    "next": "Đã chuyển bài tiếp theo.",
    "previous": "Đã chuyển bài trước.",
}


def cancelled(cancel: threading.Event | None) -> ActionResult | None:
    if cancel is not None and cancel.is_set():
        return ActionResult(ResultStatus.NEEDS_RETRY, "Đã hủy lệnh.", "cancelled")
    return None


async def _wait_operation(operation, cancel, timeout_s=3.0):
    """Bound WinRT awaits and propagate cancellation without abandoning live operations."""
    task = asyncio.ensure_future(operation)
    try:
        async with asyncio.timeout(timeout_s):
            while not task.done():
                if cancelled(cancel):
                    task.cancel()
                    raise asyncio.CancelledError
                await asyncio.wait({task}, timeout=0.025)
            return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


async def media_action_async(action: str, cancel=None, *, request: Callable | None = None) -> ActionResult:
    """Control the current Windows SMTC session, with explicit Play and Pause APIs."""
    if request is None:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager

        request = GlobalSystemMediaTransportControlsSessionManager.request_async
    if result := cancelled(cancel):
        return result
    try:
        manager = await _wait_operation(request(), cancel)
        if result := cancelled(cancel):
            return result
        session = manager.get_current_session()
        if session is None:
            return ActionResult(
                ResultStatus.NOT_AVAILABLE, "Không có ứng dụng đang phát nhạc.", "no_media_session"
            )
        method = getattr(session, MEDIA_METHODS[action])
        if result := cancelled(cancel):
            return result
        success = await _wait_operation(method(), cancel)
        if result := cancelled(cancel):
            return result
        if not success:
            return ActionResult(
                ResultStatus.NOT_AVAILABLE, "Ứng dụng không hỗ trợ thao tác nhạc này.", "media_rejected"
            )
        return ActionResult(ResultStatus.OK, MEDIA_MESSAGES[action])
    except asyncio.CancelledError:
        return ActionResult(ResultStatus.NEEDS_RETRY, "Đã hủy lệnh.", "cancelled")
    except TimeoutError:
        return ActionResult(ResultStatus.ERROR, "Ứng dụng nhạc không phản hồi.", "media_timeout")


def adjust_brightness(delta: int, *, services=None, cancel=None) -> ActionResult:
    """Adjust the active internal display by ten percentage points, clamped to 0..100."""
    if result := cancelled(cancel):
        return result
    owns_com = services is None
    if owns_com:
        import pythoncom
        from win32com.client import GetObject

        pythoncom.CoInitialize()
    try:
        if owns_com:
            services = GetObject(r"winmgmts:{impersonationLevel=impersonate}!\\.\root\WMI")
        monitors = list(services.ExecQuery("SELECT * FROM WmiMonitorBrightness WHERE Active = True"))
        methods = list(services.ExecQuery("SELECT * FROM WmiMonitorBrightnessMethods WHERE Active = True"))
        if not monitors:
            return ActionResult(
                ResultStatus.NOT_AVAILABLE, "Màn hình không hỗ trợ chỉnh độ sáng.", "brightness_unsupported"
            )
        monitor = monitors[0]
        method = next((item for item in methods if item.InstanceName == monitor.InstanceName), None)
        if method is None:
            return ActionResult(
                ResultStatus.NOT_AVAILABLE, "Màn hình không hỗ trợ chỉnh độ sáng.", "brightness_unsupported"
            )
        value = max(0, min(100, int(monitor.CurrentBrightness) + delta))
        levels = getattr(monitor, "Level", None)
        if isinstance(levels, (list, tuple)) and levels:
            value = min((int(level) for level in levels), key=lambda level: abs(level - value))
        if result := cancelled(cancel):
            return result
        parameters = method.Methods_("WmiSetBrightness").InParameters.SpawnInstance_()
        parameters.Timeout = 0
        parameters.Brightness = value
        if result := cancelled(cancel):
            return result
        output = services.ExecMethod(method.Path_.Path, "WmiSetBrightness", parameters)
        code = 0 if output is None else int(output.ReturnValue)
        if output is None:
            # Some driver/dispatch combinations omit the output object despite applying the change.
            # Read the actual brightness rather than announcing success from a missing return value.
            deadline = time.monotonic() + 1.0
            while True:
                observed = list(services.ExecQuery("SELECT * FROM WmiMonitorBrightness WHERE Active = True"))
                updated = next((item for item in observed if item.InstanceName == monitor.InstanceName), None)
                if updated is not None and abs(int(updated.CurrentBrightness) - value) <= 2:
                    break
                if result := cancelled(cancel):
                    return result
                if time.monotonic() >= deadline:
                    return ActionResult(
                        ResultStatus.ERROR, "Không xác nhận được độ sáng.", "brightness_unverified"
                    )
                time.sleep(0.05)
            value = int(updated.CurrentBrightness)
        if code != 0:
            return ActionResult(ResultStatus.ERROR, "Không đổi được độ sáng.", "brightness_failed")
        return ActionResult(ResultStatus.OK, f"Độ sáng {value}%.", data={"brightness": value})
    except Exception:
        return ActionResult(
            ResultStatus.NOT_AVAILABLE,
            "Không điều chỉnh được độ sáng màn hình này.",
            "brightness_unavailable",
        )
    finally:
        parameters = output = observed = updated = None
        # Release WMI proxies while the apartment is still alive.
        services = monitors = methods = monitor = method = None
        if owns_com:
            pythoncom.CoUninitialize()


class _Guid(ctypes.Structure):
    _fields_ = [
        ("data1", ctypes.c_uint32),
        ("data2", ctypes.c_uint16),
        ("data3", ctypes.c_uint16),
        ("data4", ctypes.c_ubyte * 8),
    ]


class NativePowerPlans:
    """Read and activate a power scheme through the public PowrProf API."""

    def __init__(self):
        self._api = ctypes.WinDLL("powrprof", use_last_error=True)
        self._api.PowerSetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(_Guid)]
        self._api.PowerSetActiveScheme.restype = ctypes.c_uint32
        self._api.PowerGetActiveScheme.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(_Guid))]
        self._api.PowerGetActiveScheme.restype = ctypes.c_uint32

    def get_active(self) -> str:
        active = ctypes.POINTER(_Guid)()
        error = self._api.PowerGetActiveScheme(None, ctypes.byref(active))
        if error:
            raise OSError(error, "PowerGetActiveScheme failed")
        try:
            return str(uuid.UUID(bytes_le=ctypes.string_at(active, 16)))
        finally:
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.LocalFree.argtypes = [ctypes.c_void_p]
            kernel.LocalFree.restype = ctypes.c_void_p
            kernel.LocalFree(active)

    def set_active(self, value: str) -> None:
        guid = _Guid.from_buffer_copy(uuid.UUID(value).bytes_le)
        error = self._api.PowerSetActiveScheme(None, ctypes.byref(guid))
        if error:
            raise OSError(error, "PowerSetActiveScheme failed")


_POWER_LOCK = threading.RLock()


class PowerPlanManager:
    """Persist the previous scheme before activating Power saver; restore across restarts."""

    def __init__(self, *, api=None, state_path: Path | None = None):
        from voicepilot.config import config_dir

        self._api = api
        self._state_path = state_path if state_path is not None else config_dir() / "power-state.json"

    def _backend(self):
        if self._api is None:
            self._api = NativePowerPlans()
        return self._api

    def _previous(self) -> str | None:
        if not self._state_path.exists():
            return None
        raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        previous = str(uuid.UUID(raw["previous"]))
        if previous == POWER_SAVER_GUID:
            raise ValueError("Previous plan must differ from Power saver")
        return previous

    def _save_previous(self, previous: str):
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False,
                                             dir=self._state_path.parent, suffix=".tmp") as file:
                temporary = Path(file.name)
                json.dump({"previous": previous}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self._state_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def enable(self, cancel=None) -> ActionResult:
        with _POWER_LOCK:
            try:
                if result := cancelled(cancel):
                    return result
                api = self._backend()
                current = api.get_active()
                if current == POWER_SAVER_GUID:
                    return ActionResult(ResultStatus.OK, "Power saver đang bật.")
                self._save_previous(current)
                if result := cancelled(cancel):
                    return result
                api.set_active(POWER_SAVER_GUID)
                if api.get_active() != POWER_SAVER_GUID:
                    return ActionResult(ResultStatus.ERROR, "Không xác nhận được Power saver.",
                                        "power_verify_failed")
                return ActionResult(ResultStatus.OK, "Đã bật Power saver.")
            except (OSError, ValueError, KeyError, TypeError):
                return ActionResult(ResultStatus.NOT_AVAILABLE,
                                    "Không bật được Power saver; chưa mất cấu hình nguồn trước đó.",
                                    "power_saver_unavailable")

    def restore(self, cancel=None) -> ActionResult:
        with _POWER_LOCK:
            try:
                if result := cancelled(cancel):
                    return result
                previous = self._previous()
                api = self._backend()
                current = api.get_active()
                if current != POWER_SAVER_GUID:
                    self._state_path.unlink(missing_ok=True)
                    return ActionResult(ResultStatus.OK, "Power saver đã tắt.")
                if previous is None:
                    return ActionResult(ResultStatus.NOT_AVAILABLE,
                                        "Chưa có power plan cũ để khôi phục. Hãy chọn trong Power Options.",
                                        "power_previous_missing")
                if result := cancelled(cancel):
                    return result
                api.set_active(previous)
                if api.get_active() != previous:
                    return ActionResult(ResultStatus.ERROR, "Chưa khôi phục được power plan cũ.",
                                        "power_verify_failed")
                self._state_path.unlink(missing_ok=True)
                return ActionResult(ResultStatus.OK, "Đã tắt Power saver và khôi phục power plan cũ.")
            except (OSError, ValueError, KeyError, TypeError):
                return ActionResult(ResultStatus.ERROR,
                                    "Không khôi phục được power plan; thông tin cũ vẫn được giữ.",
                                    "power_restore_failed")


def power_saver(cancel=None) -> ActionResult:
    """Compatibility entry point for activating Power saver."""
    return PowerPlanManager().enable(cancel)


def init_media_apartment() -> None:
    """Initialize WinRT using an existing STA when COM already selected that model."""
    import winrt.runtime

    try:
        winrt.runtime.init_apartment(winrt.runtime.ApartmentType.MULTI_THREADED)
    except OSError as exc:
        if getattr(exc, "winerror", None) != -2147417850:  # RPC_E_CHANGED_MODE
            raise
        winrt.runtime.init_apartment(winrt.runtime.ApartmentType.SINGLE_THREADED)


class SystemControls:
    """Synchronous executor adapter. WinRT stays on the calling worker's apartment."""

    def execute(self, intent: Intent, cancel=None) -> ActionResult:
        if result := cancelled(cancel):
            return result
        if intent is Intent.WIN_POWER_SAVER:
            return power_saver(cancel)
        if intent is Intent.WIN_POWER_RESTORE:
            return PowerPlanManager().restore(cancel)
        if intent in {Intent.WIN_BRIGHTNESS_UP, Intent.WIN_BRIGHTNESS_DOWN}:
            return adjust_brightness(10 if intent is Intent.WIN_BRIGHTNESS_UP else -10, cancel=cancel)
        if intent in {Intent.WIN_VOLUME_UP, Intent.WIN_VOLUME_DOWN}:
            from voicepilot.windows.input import send_hotkey

            if result := cancelled(cancel):
                return result
            up = intent is Intent.WIN_VOLUME_UP
            send_hotkey("volumeup" if up else "volumedown")
            return ActionResult(ResultStatus.OK, "Đã tăng âm lượng." if up else "Đã giảm âm lượng.")
        import winrt.runtime

        init_media_apartment()
        try:
            action = {
                Intent.MEDIA_PLAY: "play",
                Intent.MEDIA_PAUSE: "pause",
                Intent.MEDIA_NEXT: "next",
                Intent.MEDIA_PREVIOUS: "previous",
            }[intent]
            return asyncio.run(media_action_async(action, cancel))
        finally:
            winrt.runtime.uninit_apartment()
