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
