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
from typing import Protocol

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
    """Adjust the active internal display by delta percentage points, clamped to 0..100."""
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


class VolumeEndpoint(Protocol):
    """Master volume of one playback device: level as a fraction 0..1, plus mute state."""

    def get(self) -> float: ...

    def set(self, level: float) -> None: ...

    def get_mute(self) -> bool: ...

    def set_mute(self, muted: bool) -> None: ...


_CLSID_MM_DEVICE_ENUMERATOR = "BCDE0395-E52F-467C-8E3D-C4579291692E"
_IID_MM_DEVICE_ENUMERATOR = "A95664D2-9614-4F35-A746-DE8DB63617E6"
_IID_AUDIO_ENDPOINT_VOLUME = "5CDF2C82-841E-4546-9722-0CF74078229A"
_CLSCTX_ALL = 0x17
_E_RENDER = 0  # EDataFlow: playback device
_E_MULTIMEDIA = 1  # ERole: default multimedia device
# Vtable slots, counted after IUnknown (0 QueryInterface, 1 AddRef, 2 Release).
_SLOT_RELEASE = 2
_SLOT_DEVICE_ACTIVATE = 3
_SLOT_ENUM_GET_DEFAULT_ENDPOINT = 4
_SLOT_VOLUME_SET_SCALAR = 7
_SLOT_VOLUME_GET_SCALAR = 9
_SLOT_VOLUME_SET_MUTE = 14
_SLOT_VOLUME_GET_MUTE = 15
_KEY_PERCENT_PER_PRESS = 2
_MAX_KEY_PRESSES = 50
DEFAULT_STEP_PERCENT = 20


def _com_call(interface: ctypes.c_void_p, slot: int, argtypes: tuple, *args) -> int:
    """Call one COM method by vtable slot and return its HRESULT."""
    address = interface.value
    vtable = ctypes.c_void_p.from_address(address).value
    function = ctypes.c_void_p.from_address(vtable + slot * ctypes.sizeof(ctypes.c_void_p)).value
    prototype = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)
    return prototype(function)(address, *args)


def _com_check(hresult: int, what: str) -> None:
    if hresult < 0:
        raise OSError(f"{what} failed: 0x{hresult & 0xFFFFFFFF:08X}")


def _com_release(interface: ctypes.c_void_p) -> None:
    if interface.value:
        _com_call(interface, _SLOT_RELEASE, ())
        interface.value = None


def _guid(text: str) -> _Guid:
    return _Guid.from_buffer_copy(uuid.UUID(text).bytes_le)


class CoreAudioEndpoint:
    """IAudioEndpointVolume of the default playback device, called through raw COM vtables.

    Creation raises when Core Audio is unavailable (OSError, or another exception on hosts
    without ole32). COM must be initialized on this thread; call close() before CoUninitialize.
    """

    def __init__(self) -> None:
        ole32 = ctypes.WinDLL("ole32")
        create = ole32.CoCreateInstance
        create.argtypes = [ctypes.POINTER(_Guid), ctypes.c_void_p, ctypes.c_uint32,
                           ctypes.POINTER(_Guid), ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_long
        enumerator = ctypes.c_void_p()
        device = ctypes.c_void_p()
        self._volume = ctypes.c_void_p()
        try:
            _com_check(create(ctypes.byref(_guid(_CLSID_MM_DEVICE_ENUMERATOR)), None, _CLSCTX_ALL,
                              ctypes.byref(_guid(_IID_MM_DEVICE_ENUMERATOR)), ctypes.byref(enumerator)),
                       "CoCreateInstance")
            _com_check(_com_call(enumerator, _SLOT_ENUM_GET_DEFAULT_ENDPOINT,
                                 (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
                                 _E_RENDER, _E_MULTIMEDIA, ctypes.byref(device)),
                       "GetDefaultAudioEndpoint")
            _com_check(_com_call(device, _SLOT_DEVICE_ACTIVATE,
                                 (ctypes.POINTER(_Guid), ctypes.c_uint32, ctypes.c_void_p,
                                  ctypes.POINTER(ctypes.c_void_p)),
                                 ctypes.byref(_guid(_IID_AUDIO_ENDPOINT_VOLUME)), _CLSCTX_ALL, None,
                                 ctypes.byref(self._volume)),
                       "IMMDevice::Activate")
        except BaseException:
            _com_release(self._volume)
            raise
        finally:
            _com_release(device)
            _com_release(enumerator)

    def get(self) -> float:
        level = ctypes.c_float()
        _com_check(_com_call(self._volume, _SLOT_VOLUME_GET_SCALAR,
                             (ctypes.POINTER(ctypes.c_float),), ctypes.byref(level)),
                   "GetMasterVolumeLevelScalar")
        return float(level.value)

    def set(self, level: float) -> None:
        _com_check(_com_call(self._volume, _SLOT_VOLUME_SET_SCALAR, (ctypes.c_float, ctypes.c_void_p),
                             ctypes.c_float(level), None),
                   "SetMasterVolumeLevelScalar")

    def get_mute(self) -> bool:
        muted = ctypes.c_int()
        _com_check(_com_call(self._volume, _SLOT_VOLUME_GET_MUTE, (ctypes.POINTER(ctypes.c_int),),
                             ctypes.byref(muted)), "GetMute")
        return bool(muted.value)

    def set_mute(self, muted: bool) -> None:
        _com_check(_com_call(self._volume, _SLOT_VOLUME_SET_MUTE, (ctypes.c_int, ctypes.c_void_p),
                             int(muted), None), "SetMute")

    def close(self) -> None:
        _com_release(self._volume)


def _percent(level: float) -> int:
    return max(0, min(100, round(level * 100)))


def adjust_volume(delta: int, *, endpoint: VolumeEndpoint | None = None, cancel=None) -> ActionResult:
    """Change the master volume by delta percentage points, clamped to 0..100.

    Raising the volume also unmutes, like the media keys do. Without an endpoint, the default
    playback device is opened through Core Audio; if that is unavailable, the media volume keys
    are pressed instead, two percent per press.
    """
    if endpoint is not None:
        return _set_volume(endpoint, delta, cancel)
    if result := cancelled(cancel):
        return result
    try:
        import pythoncom
    except ImportError:
        return _adjust_volume_with_keys(delta, cancel)
    owns_com = False
    try:
        try:
            pythoncom.CoInitialize()
            owns_com = True
        except Exception:
            # Already initialized on this thread in another mode: use that apartment as it is.
            pass
        try:
            native = CoreAudioEndpoint()
        except Exception:
            return _adjust_volume_with_keys(delta, cancel)
        try:
            return _set_volume(native, delta, cancel)
        finally:
            native.close()
    finally:
        if owns_com:
            pythoncom.CoUninitialize()


def _set_volume(endpoint: VolumeEndpoint, delta: int, cancel) -> ActionResult:
    """Apply a relative change to an endpoint and report the level read back from it."""
    try:
        if result := cancelled(cancel):
            return result
        target = max(0, min(100, _percent(endpoint.get()) + delta))
        if delta > 0 and endpoint.get_mute():
            if result := cancelled(cancel):
                return result
            endpoint.set_mute(False)
        if result := cancelled(cancel):
            return result
        endpoint.set(target / 100)
        level = _percent(endpoint.get())
    except OSError:
        return ActionResult(ResultStatus.ERROR, "Không đổi được âm lượng.", "volume_failed")
    return ActionResult(ResultStatus.OK, f"Âm lượng {level}%.", data={"volume": level})


def _adjust_volume_with_keys(delta: int, cancel) -> ActionResult:
    """Fallback when Core Audio is unavailable: press the media volume keys, one per 2 percent."""
    from voicepilot.windows.input import send_hotkey

    up = delta > 0
    presses = min(_MAX_KEY_PRESSES, max(1, round(abs(delta) / _KEY_PERCENT_PER_PRESS)))
    try:
        for _ in range(presses):
            if result := cancelled(cancel):
                return result
            send_hotkey("volumeup" if up else "volumedown")
    except Exception:
        return ActionResult(ResultStatus.ERROR, "Không đổi được âm lượng.", "volume_failed")
    return ActionResult(ResultStatus.OK, "Đã tăng âm lượng." if up else "Đã giảm âm lượng.")


class SystemControls:
    """Synchronous executor adapter. WinRT stays on the calling worker's apartment."""

    def execute(self, intent: Intent, cancel=None, *, amount: int | None = None) -> ActionResult:
        """Run a system intent. amount is the spoken percentage; None means DEFAULT_STEP_PERCENT."""
        if result := cancelled(cancel):
            return result
        if intent is Intent.WIN_POWER_SAVER:
            return power_saver(cancel)
        if intent is Intent.WIN_POWER_RESTORE:
            return PowerPlanManager().restore(cancel)
        if intent in {Intent.WIN_BRIGHTNESS_UP, Intent.WIN_BRIGHTNESS_DOWN, Intent.WIN_VOLUME_UP,
                      Intent.WIN_VOLUME_DOWN}:
            step = DEFAULT_STEP_PERCENT if amount is None else amount
            delta = step if intent in {Intent.WIN_BRIGHTNESS_UP, Intent.WIN_VOLUME_UP} else -step
            if intent in {Intent.WIN_BRIGHTNESS_UP, Intent.WIN_BRIGHTNESS_DOWN}:
                return adjust_brightness(delta, cancel=cancel)
            return adjust_volume(delta, cancel=cancel)
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
