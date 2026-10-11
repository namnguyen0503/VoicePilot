"""Verify native Windows controls and restore display, volume and power settings."""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from voicepilot.contracts import Intent, ResultStatus
from voicepilot.windows.system_controls import SystemControls, _Guid, adjust_brightness, init_media_apartment


def volume_endpoint():
    import pythoncom

    pythoncom.CoInitialize()
    ole = ctypes.OleDLL("ole32")
    ole.CoCreateInstance.argtypes = [
        ctypes.POINTER(_Guid),
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(_Guid),
        ctypes.POINTER(ctypes.c_void_p),
    ]

    def guid(value):
        return _Guid.from_buffer_copy(uuid.UUID(value).bytes_le)

    def call(pointer, index, args, *values):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        method = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *args)(table[index])
        code = method(pointer, *values)
        if code < 0:
            raise OSError(f"CoreAudio HRESULT {code}")

    enum = ctypes.c_void_p()
    device = ctypes.c_void_p()
    endpoint = ctypes.c_void_p()
    clsid = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
    iid = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
    code = ole.CoCreateInstance(ctypes.byref(clsid), None, 23, ctypes.byref(iid), ctypes.byref(enum))
    if code < 0:
        pythoncom.CoUninitialize()
        raise OSError(f"CoreAudio HRESULT {code}")
    call(enum, 4, [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)], 0, 1, ctypes.byref(device))
    iid = guid("5CDF2C82-841E-4546-9722-0CF74078229A")
    call(
        device,
        3,
        [ctypes.POINTER(_Guid), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)],
        ctypes.byref(iid),
        23,
        None,
        ctypes.byref(endpoint),
    )
    return pythoncom, call, (enum, device, endpoint)


def verify(report):
    import winrt.runtime
    from win32com.client import GetObject
    from winrt.windows.media import MediaPlaybackStatus, MediaPlaybackType
    from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
    from winrt.windows.media.playback import MediaPlayer

    controls = SystemControls()

    def check(name, result):
        report[name] = {"status": result.status.value, "error": result.error_code, "data": result.data}
        assert result.status is ResultStatus.OK, (name, result)

    cancel = threading.Event()
    cancel.set()
    for intent in (
        Intent.WIN_POWER_SAVER,
        Intent.WIN_BRIGHTNESS_DOWN,
        Intent.WIN_VOLUME_DOWN,
        Intent.MEDIA_PLAY,
    ):
        result = controls.execute(intent, cancel)
        assert result.error_code == "cancelled"
    report["cancelled_operations"] = 4
    scheme = re.search(
        r"[0-9a-f-]{36}", subprocess.check_output(["powercfg", "/getactivescheme"], text=True)
    ).group()
    try:
        check("power_saver", controls.execute(Intent.WIN_POWER_SAVER))
    finally:
        subprocess.run(["powercfg", "/setactive", scheme], check=True)
    report["power_restored"] = scheme

    pythoncom, call, pointers = volume_endpoint()
    endpoint = pointers[-1]

    def read_volume():
        value = ctypes.c_float()
        call(endpoint, 9, [ctypes.POINTER(ctypes.c_float)], ctypes.byref(value))
        return value.value

    def set_volume(value):
        call(endpoint, 7, [ctypes.c_float, ctypes.c_void_p], value, None)

    original = read_volume()
    try:
        set_volume(0.5)
        before = read_volume()
        check("volume_up", controls.execute(Intent.WIN_VOLUME_UP))
        time.sleep(0.15)
        raised = read_volume()
        assert raised > before, (before, raised)
        check("volume_down", controls.execute(Intent.WIN_VOLUME_DOWN))
        time.sleep(0.15)
        lowered = read_volume()
        assert lowered < raised, (raised, lowered)
        report["volume_observed"] = [before, raised, lowered]
    finally:
        set_volume(original)
        for pointer in reversed(pointers):
            call(pointer, 2, [])
        pythoncom.CoUninitialize()

    pythoncom.CoInitialize()
    services = GetObject(r"winmgmts:{impersonationLevel=impersonate}!\\.\root\WMI")
    original = int(
        list(services.ExecQuery("SELECT * FROM WmiMonitorBrightness WHERE Active=True"))[0].CurrentBrightness
    )
    try:
        check("brightness_down", adjust_brightness(-10, services=services))
        check("brightness_up", adjust_brightness(10, services=services))
    finally:
        current = int(
            list(services.ExecQuery("SELECT * FROM WmiMonitorBrightness WHERE Active=True"))[
                0
            ].CurrentBrightness
        )
        check("brightness_restore", adjust_brightness(original - current, services=services))
        services = None
        pythoncom.CoUninitialize()

    init_media_apartment()
    player = MediaPlayer()
    player.command_manager.is_enabled = False
    media = player.system_media_transport_controls
    media.is_enabled = True
    media.is_play_enabled = media.is_pause_enabled = True
    media.is_next_enabled = media.is_previous_enabled = True
    media.display_updater.type = MediaPlaybackType.MUSIC
    media.display_updater.music_properties.title = "VoicePilot verification fixture"
    media.display_updater.update()
    media.playback_status = MediaPlaybackStatus.PLAYING
    buttons = []
    token = media.add_button_pressed(lambda _, event: buttons.append(int(event.button)))

    async def fixture_session():
        manager = await Manager.request_async()
        for _ in range(30):
            session = manager.get_current_session()
            if session is not None and "python" in session.source_app_user_model_id.lower():
                return session.source_app_user_model_id
            await asyncio.sleep(0.1)
        raise AssertionError("Windows did not select our Python media fixture")

    try:
        report["media_fixture"] = asyncio.run(fixture_session())
        for name, intent, status in (
            ("pause", Intent.MEDIA_PAUSE, MediaPlaybackStatus.PLAYING),
            ("play", Intent.MEDIA_PLAY, MediaPlaybackStatus.PAUSED),
            ("next", Intent.MEDIA_NEXT, MediaPlaybackStatus.PLAYING),
            ("previous", Intent.MEDIA_PREVIOUS, MediaPlaybackStatus.PLAYING),
        ):
            media.playback_status = status
            time.sleep(0.1)
            check(name, controls.execute(intent))
            time.sleep(0.15)
        report["media_buttons"] = buttons
    finally:
        media.remove_button_pressed(token)
        media.is_enabled = False
        player.close()
        player = media = None
        winrt.runtime.uninit_apartment()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--windows", action="store_true", required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = {}
    try:
        verify(report)
    except Exception as exc:
        report["failure"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
