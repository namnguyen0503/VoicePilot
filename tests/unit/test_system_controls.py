"""System-control acceptance harness specified before the backend implementation."""

import asyncio
from threading import Event
from unittest.mock import AsyncMock, Mock

import pytest

from voicepilot.config import AppConfig
from voicepilot.contracts import AppKind, Intent, Mode, ResultStatus, WindowContext
from voicepilot.intent.parser import RuleParser

PHRASES = {
    "bật chế độ tiết kiệm pin": "WIN_POWER_SAVER",
    "tắt tiết kiệm pin": "WIN_POWER_RESTORE",
    "giảm độ sáng": "WIN_BRIGHTNESS_DOWN",
    "tăng độ sáng": "WIN_BRIGHTNESS_UP",
    "giảm âm lượng": "WIN_VOLUME_DOWN",
    "tăng âm lượng": "WIN_VOLUME_UP",
    "tắt tiếng": "WIN_MUTE",
    "bật tiếng": "WIN_UNMUTE",
    "play": "MEDIA_PLAY",
    "pause": "MEDIA_PAUSE",
    "bài tiếp theo": "MEDIA_NEXT",
    "bài trước": "MEDIA_PREVIOUS",
}
CTX = WindowContext(42, 7, "notepad.exe", "Fixture", AppKind.OTHER, None)


@pytest.mark.parametrize("phrase,name", PHRASES.items())
def test_system_controls_are_reachable_from_parser_and_custom_actions(phrase, name):
    from voicepilot.custom_commands import ACTIONS

    parser = RuleParser({}, {})
    command = parser.parse(phrase, CTX, Mode.COMMAND)
    assert command.intent.value == name
    assert command.is_executable
    assert name in ACTIONS
    assert parser.parse(phrase, CTX, Mode.DICTATION).intent is Intent.DICTATE_TEXT


@pytest.mark.parametrize(
    "action,method",
    [
        ("play", "try_play_async"),
        ("pause", "try_pause_async"),
        ("next", "try_skip_next_async"),
        ("previous", "try_skip_previous_async"),
    ],
)
def test_system_media_uses_explicit_windows_session_api(action, method):
    from voicepilot.windows.system_controls import media_action_async

    manager, session = Mock(), Mock()
    manager.get_current_session.return_value = session
    setattr(session, method, AsyncMock(return_value=True))
    request = AsyncMock(return_value=manager)
    result = asyncio.run(media_action_async(action, None, request=request))
    assert result.status is ResultStatus.OK
    getattr(session, method).assert_awaited_once()


def test_system_media_no_session_or_rejected_action_is_not_success():
    from voicepilot.windows.system_controls import media_action_async

    manager = Mock()
    manager.get_current_session.return_value = None
    request = AsyncMock(return_value=manager)
    assert asyncio.run(media_action_async("play", None, request=request)).status is ResultStatus.NOT_AVAILABLE
    session = Mock()
    session.try_play_async = AsyncMock(return_value=False)
    manager.get_current_session.return_value = session
    assert asyncio.run(media_action_async("play", None, request=request)).status is ResultStatus.NOT_AVAILABLE


def test_system_media_cancellation_during_session_lookup_never_plays():
    from voicepilot.windows.system_controls import media_action_async

    cancelled = Event()
    manager, session = Mock(), Mock()
    manager.get_current_session.return_value = session
    session.try_play_async = AsyncMock(return_value=True)

    async def request():
        cancelled.set()
        return manager

    result = asyncio.run(media_action_async("play", cancelled, request=request))
    assert result.error_code == "cancelled"
    session.try_play_async.assert_not_called()


@pytest.mark.parametrize("current,delta,expected", [(95, 10, 100), (3, -10, 0), (50, 10, 60), (50, -10, 40)])
def test_system_brightness_clamps_and_matches_the_monitor(current, delta, expected):
    from voicepilot.windows.system_controls import adjust_brightness

    brightness = Mock(Active=True, InstanceName="screen", CurrentBrightness=current)
    method = Mock(InstanceName="screen")
    method.Path_.Path = "fixture-path"
    services = Mock()
    services.ExecQuery.side_effect = [[brightness], [method]]
    services.ExecMethod.return_value = Mock(ReturnValue=0)
    result = adjust_brightness(delta, services=services)
    assert result.status is ResultStatus.OK
    args = method.Methods_.return_value.InParameters.SpawnInstance_.return_value
    assert args.Timeout == 0
    assert args.Brightness == expected
    services.ExecMethod.assert_called_once_with("fixture-path", "WmiSetBrightness", args)


def test_system_brightness_unsupported_or_cancelled_never_writes():
    from voicepilot.windows.system_controls import adjust_brightness

    services = Mock()
    services.ExecQuery.return_value = []
    assert adjust_brightness(10, services=services).status is ResultStatus.NOT_AVAILABLE
    cancel = Event()
    cancel.set()
    assert adjust_brightness(10, services=services, cancel=cancel).error_code == "cancelled"


@pytest.mark.parametrize("name", list(PHRASES.values()))
def test_system_executor_blocks_cancel_and_focus_changes(name):
    from voicepilot.contracts import Command
    from voicepilot.windows.executor import WindowsExecutor

    backend = Mock()
    provider = Mock()
    provider.is_still_target.return_value = False
    executor = WindowsExecutor(AppConfig(), provider, system_controls=backend)
    command = Command(Intent(name), "fixture")
    assert executor.execute(command, CTX).status is ResultStatus.NEEDS_RETRY
    backend.execute.assert_not_called()
    cancelled = Event()
    cancelled.set()
    provider.is_still_target.return_value = True
    assert executor.execute(command, CTX, cancel=cancelled).error_code == "cancelled"
    backend.execute.assert_not_called()


def test_system_brightness_uses_wmi_execmethod_when_dispatch_treats_method_as_property():
    from voicepilot.windows.system_controls import adjust_brightness

    class MonitorMethods:
        InstanceName = "screen"
        Path_ = Mock(Path="fixture-path")
        Methods_ = Mock()

        @property
        def WmiSetBrightness(self):
            raise RuntimeError("SWbemObjectEx rejects property invocation on real Windows")

    brightness = Mock(Active=True, InstanceName="screen", CurrentBrightness=50)
    method = MonitorMethods()
    services = Mock()
    services.ExecQuery.side_effect = [[brightness], [method]]
    services.ExecMethod.return_value = Mock(ReturnValue=0)
    result = adjust_brightness(10, services=services)
    assert result.status is ResultStatus.OK
    args = method.Methods_.return_value.InParameters.SpawnInstance_.return_value
    assert args.Brightness == 60
    assert args.Timeout == 0
    services.ExecMethod.assert_called_once_with("fixture-path", "WmiSetBrightness", args)


def test_system_wmi_services_uses_execmethod_without_object_suffix():
    from voicepilot.windows.system_controls import adjust_brightness

    class Services:
        ExecQuery = Mock(
            side_effect=[[Mock(InstanceName="screen", CurrentBrightness=50)], [Mock(InstanceName="screen")]]
        )
        ExecMethod = Mock(return_value=Mock(ReturnValue=0))

        @property
        def ExecMethod_(self):
            raise AttributeError("SWbemServices has ExecMethod, not ExecMethod_")

    services = Services()
    assert adjust_brightness(10, services=services).status is ResultStatus.OK
    services.ExecMethod.assert_called_once()


def test_system_winrt_apartment_type_is_explicit(monkeypatch):
    import sys
    import types

    from voicepilot.windows import system_controls

    runtime = types.ModuleType("winrt.runtime")
    runtime.ApartmentType = Mock(MULTI_THREADED=1)
    runtime.init_apartment = Mock(side_effect=lambda kind: None)
    runtime.uninit_apartment = Mock()
    package = types.ModuleType("winrt")
    package.runtime = runtime
    monkeypatch.setitem(sys.modules, "winrt", package)
    monkeypatch.setitem(sys.modules, "winrt.runtime", runtime)
    monkeypatch.setattr(system_controls, "media_action_async", AsyncMock(return_value=Mock()))
    system_controls.SystemControls().execute(Intent.MEDIA_PAUSE)
    runtime.init_apartment.assert_called_once_with(1)
    runtime.uninit_apartment.assert_called_once()


def test_system_wmi_missing_output_is_verified_by_readback_instead_of_reported_failure():
    from voicepilot.windows.system_controls import adjust_brightness

    method = Mock(InstanceName="screen")
    services = Mock()
    services.ExecQuery.side_effect = [
        [Mock(InstanceName="screen", CurrentBrightness=100)],
        [method],
        [Mock(InstanceName="screen", CurrentBrightness=89)],
    ]
    services.ExecMethod.return_value = None
    result = adjust_brightness(-10, services=services)
    assert result.status is ResultStatus.OK
    assert result.data["brightness"] == 89


def test_system_brightness_uses_driver_supported_levels():
    from voicepilot.windows.system_controls import adjust_brightness

    monitor = Mock(InstanceName="screen", CurrentBrightness=89, Level=(5, 89, 92, 95, 97, 100))
    method = Mock(InstanceName="screen")
    services = Mock()
    services.ExecQuery.side_effect = [[monitor], [method]]
    services.ExecMethod.return_value = Mock(ReturnValue=0)
    assert adjust_brightness(10, services=services).status is ResultStatus.OK
    params = method.Methods_.return_value.InParameters.SpawnInstance_.return_value
    assert params.Brightness == 100


def test_system_brightness_waits_for_driver_readback(monkeypatch):
    from voicepilot.windows import system_controls

    monitor = Mock(InstanceName="screen", CurrentBrightness=89)
    updated = Mock(InstanceName="screen", CurrentBrightness=99)
    services = Mock()
    services.ExecQuery.side_effect = [[monitor], [Mock(InstanceName="screen")], [monitor], [updated]]
    services.ExecMethod.return_value = None
    assert system_controls.adjust_brightness(10, services=services).status is ResultStatus.OK


def test_system_media_reuses_existing_sta_apartment(monkeypatch):
    import sys
    import types

    from voicepilot.windows import system_controls

    runtime = types.ModuleType("winrt.runtime")
    runtime.ApartmentType = Mock(MULTI_THREADED=1, SINGLE_THREADED=0)
    changed = OSError("Cannot change thread mode after it is set")
    changed.winerror = -2147417850
    runtime.init_apartment = Mock(side_effect=[changed, None])
    runtime.uninit_apartment = Mock()
    package = types.ModuleType("winrt")
    package.runtime = runtime
    monkeypatch.setitem(sys.modules, "winrt", package)
    monkeypatch.setitem(sys.modules, "winrt.runtime", runtime)
    monkeypatch.setattr(system_controls, "media_action_async", AsyncMock(return_value=Mock()))
    system_controls.SystemControls().execute(Intent.MEDIA_PLAY)
    assert runtime.init_apartment.call_args_list == [((1,), {}), ((0,), {})]
    runtime.uninit_apartment.assert_called_once()


class FakeVolumeEndpoint:
    """In-memory VolumeEndpoint: level is a fraction 0..1, like IAudioEndpointVolume."""

    def __init__(self, level: float = 0.5, muted: bool = False) -> None:
        self.level = level
        self.muted = muted
        self.sets: list[float] = []
        self.mute_calls: list[bool] = []

    def get(self) -> float:
        return self.level

    def set(self, level: float) -> None:
        self.sets.append(level)
        self.level = level

    def get_mute(self) -> bool:
        return self.muted

    def set_mute(self, muted: bool) -> None:
        self.mute_calls.append(muted)
        self.muted = muted


@pytest.mark.parametrize(
    "start,delta,expected",
    [(0.5, 30, 80), (0.5, -10, 40), (0.9, 30, 100), (0.1, -30, 0), (0.0, -20, 0), (1.0, 20, 100)],
)
def test_volume_adjusts_by_points_and_clamps(start, delta, expected):
    from voicepilot.windows.system_controls import adjust_volume

    endpoint = FakeVolumeEndpoint(start)
    result = adjust_volume(delta, endpoint=endpoint)
    assert result.status is ResultStatus.OK
    assert endpoint.sets == [expected / 100]
    assert result.data == {"volume": expected}
    assert result.message_vi == f"Âm lượng {expected}%."


def test_volume_raise_unmutes_but_lower_keeps_mute_state():
    from voicepilot.windows.system_controls import adjust_volume

    muted = FakeVolumeEndpoint(0.4, muted=True)
    assert adjust_volume(10, endpoint=muted).status is ResultStatus.OK
    assert muted.mute_calls == [False]
    assert muted.muted is False

    lowered = FakeVolumeEndpoint(0.4, muted=True)
    adjust_volume(-10, endpoint=lowered)
    assert lowered.mute_calls == []


def test_volume_cancelled_before_set_never_writes():
    from voicepilot.windows.system_controls import adjust_volume

    endpoint = FakeVolumeEndpoint(0.5, muted=True)
    cancel = Event()
    cancel.set()
    result = adjust_volume(20, endpoint=endpoint, cancel=cancel)
    assert result.error_code == "cancelled"
    assert endpoint.sets == []
    assert endpoint.mute_calls == []


def test_volume_cancelled_between_unmute_and_set_never_sets_level():
    from voicepilot.windows.system_controls import adjust_volume

    class CancelAfterUnmute(FakeVolumeEndpoint):
        def set_mute(self, muted: bool) -> None:
            super().set_mute(muted)
            cancel.set()

    cancel = Event()
    endpoint = CancelAfterUnmute(0.5, muted=True)
    assert adjust_volume(20, endpoint=endpoint, cancel=cancel).error_code == "cancelled"
    assert endpoint.sets == []


def test_volume_com_failure_during_set_is_reported_not_raised():
    from voicepilot.windows.system_controls import adjust_volume

    class Broken(FakeVolumeEndpoint):
        def set(self, level: float) -> None:
            raise OSError("SetMasterVolumeLevelScalar failed")

    result = adjust_volume(10, endpoint=Broken(0.5))
    assert result.status is ResultStatus.ERROR
    assert result.error_code == "volume_failed"


@pytest.fixture
def fake_ole32(monkeypatch):
    """ole32 stand-in for the Core Audio apartment; CoreAudioEndpoint is faked by each test."""
    import ctypes

    ole32 = Mock()
    ole32.CoInitializeEx.return_value = 0  # S_OK
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, *args, **kwargs: ole32, raising=False)
    return ole32


@pytest.fixture
def fake_input(monkeypatch):
    import sys
    import types

    module = types.ModuleType("voicepilot.windows.input")
    module.send_hotkey = Mock()
    monkeypatch.setitem(sys.modules, "voicepilot.windows.input", module)
    return module


def test_volume_falls_back_to_media_keys_without_core_audio(monkeypatch, fake_ole32, fake_input):
    from voicepilot.windows import system_controls

    def unavailable():
        raise OSError("CoCreateInstance failed")

    monkeypatch.setattr(system_controls, "CoreAudioEndpoint", unavailable)
    result = system_controls.adjust_volume(20)
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã tăng âm lượng."
    assert fake_input.send_hotkey.call_count == 10
    fake_input.send_hotkey.assert_called_with("volumeup")
    fake_ole32.CoUninitialize.assert_called_once()


def test_volume_key_fallback_presses_each_two_percent_and_caps_at_fifty(fake_input):
    from voicepilot.windows.system_controls import _adjust_volume_with_keys

    assert _adjust_volume_with_keys(-30, None).message_vi == "Đã giảm âm lượng."
    assert fake_input.send_hotkey.call_count == 15
    fake_input.send_hotkey.assert_called_with("volumedown")

    fake_input.send_hotkey.reset_mock()
    _adjust_volume_with_keys(100, None)
    assert fake_input.send_hotkey.call_count == 50


def test_volume_key_fallback_stops_when_cancelled(fake_input):
    from voicepilot.windows.system_controls import _adjust_volume_with_keys

    cancel = Event()
    cancel.set()
    assert _adjust_volume_with_keys(20, cancel).error_code == "cancelled"
    fake_input.send_hotkey.assert_not_called()


def test_execute_passes_spoken_amount_to_brightness_and_volume(monkeypatch):
    from voicepilot.windows import system_controls

    brightness = Mock(return_value=Mock())
    volume = Mock(return_value=Mock())
    monkeypatch.setattr(system_controls, "adjust_brightness", brightness)
    monkeypatch.setattr(system_controls, "adjust_volume", volume)
    controls = system_controls.SystemControls()

    controls.execute(Intent.WIN_BRIGHTNESS_DOWN, amount=15)
    brightness.assert_called_with(-15, cancel=None)
    controls.execute(Intent.WIN_VOLUME_UP, amount=30)
    volume.assert_called_with(30, cancel=None)


def test_execute_without_amount_uses_default_step(monkeypatch):
    from voicepilot.windows import system_controls

    brightness = Mock(return_value=Mock())
    volume = Mock(return_value=Mock())
    monkeypatch.setattr(system_controls, "adjust_brightness", brightness)
    monkeypatch.setattr(system_controls, "adjust_volume", volume)
    controls = system_controls.SystemControls()

    controls.execute(Intent.WIN_BRIGHTNESS_UP)
    brightness.assert_called_with(20, cancel=None)
    controls.execute(Intent.WIN_VOLUME_DOWN)
    volume.assert_called_with(-20, cancel=None)


@pytest.mark.parametrize(
    "name,params,expected",
    [
        ("WIN_VOLUME_UP", {"amount": "35"}, 35),
        ("WIN_VOLUME_DOWN", {}, 7),
        ("WIN_BRIGHTNESS_DOWN", {}, 12),
        ("WIN_BRIGHTNESS_UP", {"amount": "5"}, 5),
        ("MEDIA_PLAY", {}, None),
        ("WIN_MUTE", {}, None),
        ("WIN_UNMUTE", {}, None),
    ],
)
def test_executor_passes_spoken_amount_or_configured_step(name, params, expected):
    from voicepilot.contracts import ActionResult, Command
    from voicepilot.windows.executor import WindowsExecutor

    backend = Mock()
    backend.execute.return_value = ActionResult(ResultStatus.OK, "ok")
    provider = Mock()
    provider.is_still_target.return_value = True
    executor = WindowsExecutor(AppConfig(volume_step_percent=50, brightness_step_percent=50),
                               provider, system_controls=backend)
    executor.update_config(AppConfig(volume_step_percent=7, brightness_step_percent=12))
    executor.execute(Command(Intent(name), "fixture", params=params), CTX)
    assert backend.execute.call_args.kwargs.get("amount") == expected


# ---------------------------------------------------------------- mute / unmute


def test_mute_sets_state_explicitly_and_reads_it_back():
    from voicepilot.windows.system_controls import set_muted

    endpoint = FakeVolumeEndpoint(muted=False)
    result = set_muted(True, endpoint=endpoint)
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã tắt tiếng."
    assert endpoint.mute_calls == [True]
    assert endpoint.muted is True


def test_unmute_sets_state_explicitly_and_reads_it_back():
    from voicepilot.windows.system_controls import set_muted

    endpoint = FakeVolumeEndpoint(muted=True)
    result = set_muted(False, endpoint=endpoint)
    assert result.status is ResultStatus.OK
    assert result.message_vi == "Đã bật tiếng."
    assert endpoint.mute_calls == [False]


@pytest.mark.parametrize(("muted", "message"), [(True, "Đã tắt tiếng."), (False, "Đã bật tiếng.")])
def test_mute_already_in_requested_state_is_ok_without_write(muted, message):
    from voicepilot.windows.system_controls import set_muted

    endpoint = FakeVolumeEndpoint(muted=muted)
    result = set_muted(muted, endpoint=endpoint)
    assert result.status is ResultStatus.OK
    assert result.message_vi == message
    assert endpoint.mute_calls == []


def test_mute_cancelled_before_set_never_writes():
    from voicepilot.windows.system_controls import set_muted

    cancel = Event()
    cancel.set()
    endpoint = FakeVolumeEndpoint(muted=False)
    assert set_muted(True, endpoint=endpoint, cancel=cancel).error_code == "cancelled"
    assert endpoint.mute_calls == []


def test_mute_cancelled_during_read_never_writes():
    from voicepilot.windows.system_controls import set_muted

    cancel = Event()

    class CancelOnRead(FakeVolumeEndpoint):
        def get_mute(self) -> bool:
            cancel.set()  # the user cancels while the state is being read
            return self.muted

    endpoint = CancelOnRead(muted=False)
    assert set_muted(True, endpoint=endpoint, cancel=cancel).error_code == "cancelled"
    assert endpoint.mute_calls == []


def test_mute_endpoint_failure_is_mute_unavailable_and_presses_no_key(fake_input):
    from voicepilot.windows.system_controls import set_muted

    class Broken(FakeVolumeEndpoint):
        def set_mute(self, muted: bool) -> None:
            raise OSError("SetMute failed")

    result = set_muted(True, endpoint=Broken(muted=False))
    assert result.status is ResultStatus.NOT_AVAILABLE
    assert result.message_vi == "Không điều khiển được âm thanh."
    assert result.error_code == "mute_unavailable"
    fake_input.send_hotkey.assert_not_called()


def test_mute_readback_mismatch_is_mute_unavailable():
    from voicepilot.windows.system_controls import set_muted

    class Ignores(FakeVolumeEndpoint):
        def set_mute(self, muted: bool) -> None:
            self.mute_calls.append(muted)  # accepted but not applied

    result = set_muted(True, endpoint=Ignores(muted=False))
    assert result.error_code == "mute_unavailable"


def test_mute_without_core_audio_is_unavailable_and_never_uses_toggle_key(
    monkeypatch, fake_ole32, fake_input
):
    from voicepilot.windows import system_controls

    def unavailable():
        raise OSError("CoCreateInstance failed")

    monkeypatch.setattr(system_controls, "CoreAudioEndpoint", unavailable)
    for muted in (True, False):
        result = system_controls.set_muted(muted)
        assert result.status is ResultStatus.NOT_AVAILABLE
        assert result.error_code == "mute_unavailable"
    fake_input.send_hotkey.assert_not_called()
    assert fake_ole32.CoUninitialize.call_count == 2


def test_mute_with_core_audio_uses_the_default_endpoint(monkeypatch, fake_ole32):
    from voicepilot.windows import system_controls

    endpoint = FakeVolumeEndpoint(muted=False)
    endpoint.close = Mock()
    monkeypatch.setattr(system_controls, "CoreAudioEndpoint", lambda: endpoint)
    assert system_controls.set_muted(True).message_vi == "Đã tắt tiếng."
    assert endpoint.mute_calls == [True]
    endpoint.close.assert_called_once()
    fake_ole32.CoUninitialize.assert_called_once()


def test_execute_routes_mute_and_unmute_without_amount(monkeypatch):
    from voicepilot.windows import system_controls

    set_muted = Mock(return_value=Mock())
    monkeypatch.setattr(system_controls, "set_muted", set_muted)
    controls = system_controls.SystemControls()

    controls.execute(Intent.WIN_MUTE)
    set_muted.assert_called_with(True, cancel=None)
    controls.execute(Intent.WIN_UNMUTE)
    set_muted.assert_called_with(False, cancel=None)


@pytest.mark.parametrize("hresult,owns", [(0, True), (1, True), (-2147417850, False)])
def test_core_audio_apartment_is_balanced_with_the_callers(monkeypatch, fake_ole32, hresult, owns):
    # Regression: pythoncom.CoInitialize() adds no reference after "import pythoncom" initialized
    # the thread, so its CoUninitialize() dropped the caller's apartment (CO_E_NOTINITIALIZED next).
    from voicepilot.windows import system_controls

    fake_ole32.CoInitializeEx.return_value = hresult  # S_OK, S_FALSE, RPC_E_CHANGED_MODE
    endpoint = FakeVolumeEndpoint(muted=False)
    endpoint.close = Mock()
    monkeypatch.setattr(system_controls, "CoreAudioEndpoint", lambda: endpoint)
    assert system_controls.set_muted(True).status is ResultStatus.OK
    assert fake_ole32.CoUninitialize.call_count == (1 if owns else 0)
