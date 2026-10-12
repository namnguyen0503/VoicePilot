"""Acceptance for first/last slide jumps (PowerPoint only) and mute/unmute (any window)."""

from unittest.mock import Mock

import pytest

from voicepilot.config import AppConfig
from voicepilot.contracts import (
    SPOKEN_INTENTS,
    SYSTEM_INTENTS,
    ActionResult,
    AppKind,
    Command,
    Intent,
    Mode,
    ResultStatus,
    WindowContext,
)
from voicepilot.custom_commands import ACTIONS
from voicepilot.intent.parser import RuleParser
from voicepilot.intent.validation import is_valid_candidate
from voicepilot.windows.executor import WindowsExecutor

FIRST_PHRASES = ["slide đầu tiên", "về slide đầu", "về slide đầu tiên", "quay về slide đầu",
                 "trang đầu tiên", "slide đầu"]
LAST_PHRASES = ["slide cuối cùng", "đến slide cuối", "slide cuối", "trang cuối cùng", "tới slide cuối cùng"]
MUTE_PHRASES = ["tắt tiếng", "tắt âm thanh", "tắt loa", "mute", "im lặng", "câm"]
UNMUTE_PHRASES = ["bật tiếng", "bật âm thanh", "bật loa", "mở tiếng", "unmute", "bỏ tắt tiếng"]
POWERPOINT = WindowContext(1, 2, "POWERPNT.EXE", "Deck.pptx", AppKind.POWERPOINT, None)
OTHER = WindowContext(1, 2, "notepad.exe", "Notes", AppKind.OTHER, None)


@pytest.fixture(scope="module")
def parser():
    return RuleParser({}, {})


@pytest.mark.parametrize("phrase", FIRST_PHRASES)
def test_first_slide_phrases(parser, phrase):
    command = parser.parse(phrase, POWERPOINT, Mode.COMMAND)
    assert command.intent is Intent.PPT_FIRST_SLIDE
    assert is_valid_candidate(command, POWERPOINT, [])
    assert not is_valid_candidate(command, OTHER, [])


@pytest.mark.parametrize("phrase", LAST_PHRASES)
def test_last_slide_phrases(parser, phrase):
    command = parser.parse(phrase, POWERPOINT, Mode.COMMAND)
    assert command.intent is Intent.PPT_LAST_SLIDE
    assert is_valid_candidate(command, POWERPOINT, [])
    assert not is_valid_candidate(command, OTHER, [])


@pytest.mark.parametrize("phrase", MUTE_PHRASES)
def test_mute_phrases_work_in_any_window(parser, phrase):
    for context in (POWERPOINT, OTHER, None):
        command = parser.parse(phrase, context, Mode.COMMAND)
        assert command.intent is Intent.WIN_MUTE
        assert command.is_executable
        assert is_valid_candidate(command, context, [])


@pytest.mark.parametrize("phrase", UNMUTE_PHRASES)
def test_unmute_phrases_win_over_app_and_site_rules(parser, phrase):
    command = parser.parse(phrase, OTHER, Mode.COMMAND)
    assert command.intent is Intent.WIN_UNMUTE
    assert command.params == {}


@pytest.mark.parametrize(
    ("phrase", "intent"),
    [
        ("tiếp theo", Intent.PPT_NEXT_SLIDE),
        ("quay lại", Intent.PPT_PREV_SLIDE),
        ("tăng âm lượng", Intent.WIN_VOLUME_UP),
        ("tắt cửa sổ", Intent.WIN_CLOSE_WINDOW),
        ("tắt tiết kiệm pin", Intent.WIN_POWER_RESTORE),
        ("tắt trình chiếu", Intent.PPT_STOP_SHOW),
        ("bật nhạc", Intent.MEDIA_PLAY),
    ],
)
def test_existing_phrases_are_unchanged(parser, phrase, intent):
    assert parser.parse(phrase, POWERPOINT, Mode.COMMAND).intent is intent


def test_new_intents_are_spoken_and_mute_is_a_system_intent():
    new = {Intent.PPT_FIRST_SLIDE, Intent.PPT_LAST_SLIDE, Intent.WIN_MUTE, Intent.WIN_UNMUTE}
    assert new <= SPOKEN_INTENTS
    assert {Intent.WIN_MUTE, Intent.WIN_UNMUTE} <= SYSTEM_INTENTS


def test_custom_action_names_for_slide_jumps():
    assert ACTIONS["PPT_FIRST_SLIDE"][0] == "Slide đầu tiên"
    assert ACTIONS["PPT_LAST_SLIDE"][0] == "Slide cuối cùng"


@pytest.mark.parametrize(
    ("intent", "method"),
    [(Intent.PPT_FIRST_SLIDE, "first_slide"), (Intent.PPT_LAST_SLIDE, "last_slide")],
)
def test_executor_routes_slide_jumps_to_powerpoint(intent, method):
    powerpoint = Mock()
    getattr(powerpoint, method).return_value = ActionResult(ResultStatus.OK, "ok")
    provider = Mock()
    provider.is_still_target.return_value = True
    executor = WindowsExecutor(AppConfig(), provider, powerpoint=powerpoint, keys=Mock())
    result = executor.execute(Command(intent, "slide"), POWERPOINT)
    assert result.status is ResultStatus.OK
    getattr(powerpoint, method).assert_called_once()


@pytest.mark.parametrize("intent", [Intent.PPT_FIRST_SLIDE, Intent.PPT_LAST_SLIDE])
def test_slide_jumps_outside_powerpoint_never_reach_com(intent):
    powerpoint = Mock()
    provider = Mock()
    provider.is_still_target.return_value = True
    executor = WindowsExecutor(AppConfig(), provider, powerpoint=powerpoint, keys=Mock())
    result = executor.execute(Command(intent, "slide"), OTHER)
    assert result.error_code == "no_powerpoint_window"
    assert powerpoint.method_calls == []


@pytest.mark.parametrize(("intent", "muted"), [(Intent.WIN_MUTE, True), (Intent.WIN_UNMUTE, False)])
def test_executor_routes_mute_without_amount(intent, muted):
    backend = Mock()
    backend.execute.return_value = ActionResult(ResultStatus.OK, "ok")
    provider = Mock()
    provider.is_still_target.return_value = True
    executor = WindowsExecutor(AppConfig(), provider, system_controls=backend)
    assert executor.execute(Command(intent, "tắt tiếng"), OTHER).status is ResultStatus.OK
    backend.execute.assert_called_once()
    assert backend.execute.call_args.args[0] is intent
    assert "amount" not in backend.execute.call_args.kwargs
