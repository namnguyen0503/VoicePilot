"""Close-window acceptance: never send Alt+F4 to the Windows desktop."""

import threading
from unittest.mock import Mock

import pytest

from voicepilot.config import AppConfig
from voicepilot.contracts import AppKind, Intent, Mode, ResultStatus, WindowContext
from voicepilot.intent.parser import RuleParser
from voicepilot.windows.executor import WindowsExecutor


def target():
    return WindowContext(10, 20, "explorer.exe", "Folder", AppKind.OTHER, None)


@pytest.mark.parametrize("phrase", ["đóng cửa sổ", "đóng cửa sổ này", "tắt cửa sổ", "close window"])
def test_close_window_phrase_and_dictation(phrase):
    parser = RuleParser({}, {})
    assert parser.parse(phrase, target(), Mode.COMMAND).intent.value == "WIN_CLOSE_WINDOW"
    assert parser.parse(phrase, target(), Mode.DICTATION).params == {"text": phrase}


def test_custom_and_builtin_override_keep_the_close_action():
    from voicepilot.custom_commands import validate_commands

    custom = validate_commands([dict(id="close", name="Close", intent="WIN_CLOSE_WINDOW",
                                    patterns=["kết thúc cửa sổ"], params={})])
    parser = RuleParser({}, {}, custom_commands=custom,
                        builtin_patterns={"WIN_CLOSE_WINDOW": ["đóng khung này"]})
    for phrase in ("kết thúc cửa sổ", "đóng khung này"):
        assert parser.parse(phrase, target(), Mode.COMMAND).intent.value == "WIN_CLOSE_WINDOW"


def test_close_candidate_requires_a_target():
    from voicepilot.intent.validation import is_valid_candidate

    command = RuleParser({}, {}).parse("đóng cửa sổ", target(), Mode.COMMAND)
    assert is_valid_candidate(command, target(), [])
    assert not is_valid_candidate(command, None, [])


@pytest.mark.parametrize("blocked", ["desktop", "no_target", "changed", "changed_during_probe",
                                    "cancel", "cancel_during_probe", "probe_error", "allowed"])
def test_close_checks_desktop_focus_and_cancel_before_keys(blocked):
    ctx = target()
    provider = Mock(is_still_target=Mock(return_value=True))
    keys, probe = Mock(), Mock(return_value=blocked != "desktop")
    cancel = threading.Event()
    if blocked == "no_target":
        ctx = WindowContext(0, 0, "", "", AppKind.NONE, None)
    if blocked == "changed":
        provider.is_still_target.return_value = False
    if blocked == "cancel":
        cancel.set()
    if blocked == "changed_during_probe":
        probe.side_effect = lambda hwnd: setattr(provider.is_still_target, "return_value", False) or True
    if blocked == "cancel_during_probe":
        probe.side_effect = lambda hwnd: cancel.set() or True
    if blocked == "probe_error":
        probe.side_effect = OSError("Unavailable window information")
    executor = WindowsExecutor(AppConfig(), provider, keys=keys, close_window_probe=probe)
    try:
        result = executor.execute(RuleParser({}, {}).parse("đóng cửa sổ", ctx, Mode.COMMAND),
                                  ctx, cancel=cancel)
        if blocked == "allowed":
            assert result.status is ResultStatus.OK
            keys.send_hotkey.assert_called_once_with("alt", "f4")
            assert "gửi" in result.message_vi.lower()
        else:
            assert result.status is not ResultStatus.OK
            assert not keys.mock_calls
    finally:
        executor.shutdown()


@pytest.mark.parametrize("window_class,root,shell,desktop,exists,allowed", [
    ("Progman", 10, 10, 1, True, False),
    ("WorkerW", 10, 5, 1, True, False),
    ("Shell_TrayWnd", 10, 5, 1, True, False),
    ("Shell_SecondaryTrayWnd", 10, 5, 1, True, False),
    ("SHELLDLL_DefView", 10, 5, 1, True, False),
    ("#32769", 10, 5, 1, True, False),
    ("CabinetWClass", 10, 5, 1, True, True),
    ("Notepad", 10, 5, 1, True, True),
    ("Chrome_WidgetWin_1", 10, 5, 1, True, True),
    ("OpusApp", 10, 5, 1, True, True),
    ("Unlisted", 10, 10, 1, True, False),
    ("Unlisted", 10, 5, 10, True, False),
    ("Unlisted", 0, 5, 1, True, False),
    ("", 10, 5, 1, True, False),
    ("Notepad", 10, 0, 1, True, False),
    ("Notepad", 10, 5, 0, True, False),
    ("Notepad", 10, 5, 1, False, False),
])
def test_native_window_identity_rules(window_class, root, shell, desktop, exists, allowed):
    from voicepilot.windows.window_close import is_closable_window

    user = Mock()
    user.IsWindow.return_value = exists
    user.GetAncestor.return_value = root
    user.GetShellWindow.return_value = shell
    user.GetDesktopWindow.return_value = desktop

    def read_class(hwnd, buffer, size):
        buffer.value = window_class
        return len(window_class)

    user.GetClassNameW.side_effect = read_class
    assert is_closable_window(10, user32=user) is allowed


def test_child_of_desktop_is_blocked():
    from voicepilot.windows.window_close import is_closable_window

    user = Mock(IsWindow=Mock(return_value=True), GetAncestor=Mock(return_value=5),
                GetShellWindow=Mock(return_value=5), GetDesktopWindow=Mock(return_value=1))
    assert not is_closable_window(10, user32=user)


def test_unreadable_window_information_is_blocked():
    from voicepilot.windows.window_close import is_closable_window

    assert not is_closable_window(10, user32=Mock(IsWindow=Mock(side_effect=OSError())))


@pytest.mark.parametrize("backend", ["google", "offline", "hybrid", "fallback"])
def test_close_window_runs_once_through_voice_pipeline(qapp, backend):
    from tools.verify_features import pipeline_case

    pipeline_case(Intent.WIN_CLOSE_WINDOW, "đóng cửa sổ", AppKind.OTHER, {}, backend)
