"""Acceptance cases specified before implementing custom commands."""

from dataclasses import replace
from threading import Event
from unittest.mock import Mock

import pytest

from voicepilot.config import AppConfig, load_config, save_config
from voicepilot.contracts import AppKind, Intent, Mode, ResultStatus, WindowContext
from voicepilot.custom_commands import ACTIONS, validate_commands
from voicepilot.intent.parser import RuleParser
from voicepilot.windows.executor import WindowsExecutor


def definition(**changes):
    return (
        dict(
            id="gold",
            name="Hỏi Google",
            enabled=True,
            patterns=["hỏi google về {topic}", "tra giá {topic}"],
            intent="BROWSER_SEARCH",
            params={"query": "{topic}"},
            context="any",
            process="",
            **changes,
        )
        if not changes
        else {**definition(), **changes}
    )


def parser(*items):
    return RuleParser({}, {}, custom_commands=list(items))


@pytest.mark.parametrize(
    "text",
    [
        "hỏi google về Giá vàng hôm nay",
        "hoi google ve Giá vàng hôm nay",
        "làm ơn hỏi google về Giá vàng hôm nay nhé",
        "tra giá Giá vàng hôm nay",
    ],
)
def test_alias_and_capture_preserve_original(text):
    command = parser(definition()).parse(text, None, Mode.COMMAND)
    assert command.intent is Intent.BROWSER_SEARCH
    assert command.params == {"query": "Giá vàng hôm nay"}
    assert command.custom_id == "gold"


def test_disabled_context_priority_and_dictation():
    ctx = WindowContext(1, 2, "NOTEPAD.EXE", "test", AppKind.OTHER, None)
    rule = definition(patterns=["lưu"], context="other", process="notepad.exe", params={"query": "giá vàng"})
    assert parser(rule).parse("lưu", ctx, Mode.COMMAND).intent is Intent.BROWSER_SEARCH
    assert parser(rule).parse("lưu", None, Mode.COMMAND).intent is Intent.PPT_SAVE
    assert parser({**rule, "enabled": False}).parse("lưu", ctx, Mode.COMMAND).intent is Intent.PPT_SAVE
    assert parser(rule).parse("lưu", ctx, Mode.DICTATION).params == {"text": "lưu"}


def test_conflict_never_executes_random_command():
    a = definition()
    b = definition(id="other", name="Other", params={"query": "fixed"})
    command = parser(a, b).parse("tra giá vàng", None, Mode.COMMAND)
    assert not command.is_executable


def test_specific_literal_beats_capture_and_url_capture_is_encoded():
    rules = [
        definition(
            patterns=["hỏi {topic}"],
            intent="BROWSER_OPEN_SITE",
            params={"url": "https://example.com/search?q={topic}"},
        ),
        definition(id="exact", patterns=["hỏi vàng"], params={"query": "SJC"}),
    ]
    assert parser(*rules).parse("hỏi vàng", None, Mode.COMMAND).params == {"query": "SJC"}
    url = parser(rules[0]).parse("hỏi vàng & bạc", None, Mode.COMMAND).params["url"]
    assert url == "https://example.com/search?q=v%C3%A0ng%20%26%20b%E1%BA%A1c"


@pytest.mark.parametrize(
    "changes",
    [
        {"patterns": ["hỏi {bad-name}"]},
        {"patterns": ["hỏi {topic}{other}"]},
        {"params": {"query": "{missing}"}},
        {"patterns": []},
        {"intent": "UNKNOWN"},
        {"intent": "bad"},
        {"enabled": "yes"},
        {"context": "unsupported_app"},
        {"process": "C:/notepad.exe"},
        {"intent": "BROWSER_OPEN_SITE", "params": {"url": "javascript:alert(1)"}},
        {"intent": "WIN_HOTKEY", "params": {"keys": "ctrl+badkey"}},
        {"intent": "WIN_HOTKEY", "params": {"keys": "ctrl+{topic}"}},
        {"intent": "WIN_OPEN_APP", "params": {"app": "{topic}"}},
    ],
)
def test_invalid_definition_rejected(changes):
    with pytest.raises(ValueError):
        validate_commands([definition(**changes)])


def test_roundtrip_and_bad_entry_isolated(tmp_path):
    path = tmp_path / "config.toml"
    cfg = replace(AppConfig(), custom_commands=[definition()])
    save_config(cfg, path)
    assert load_config(path).custom_commands == cfg.custom_commands
    with path.open("a", encoding="utf-8") as handle:
        handle.write('\n[[custom_commands]]\nid="broken"\nname="bad"\nintent="UNKNOWN"\n')
    assert load_config(path).custom_commands == cfg.custom_commands


def test_hotkey_executor_focus_and_cancel_guards():
    ctx = WindowContext(1, 2, "notepad.exe", "test", AppKind.OTHER, None)
    command = parser(
        definition(patterns=["tout sélectionner"], intent="WIN_HOTKEY", params={"keys": "ctrl+a"})
    ).parse("tout sélectionner", ctx, Mode.COMMAND)
    keys, provider = Mock(), Mock()
    provider.is_still_target.return_value = True
    executor = WindowsExecutor(AppConfig(), provider, keys=keys)
    assert executor.execute(command, ctx).status is ResultStatus.OK
    keys.send_hotkey.assert_called_once_with("ctrl", "a")
    keys.reset_mock()
    provider.is_still_target.return_value = False
    assert executor.execute(command, ctx).error_code == "target_changed"
    provider.is_still_target.return_value = True
    cancel = Event()
    cancel.set()
    assert executor.execute(command, ctx, cancel=cancel).error_code == "cancelled"
    keys.send_hotkey.assert_not_called()


def test_multiple_captures_and_missing_parameter():
    rule = definition(patterns=["so sánh {first} với {second}"], params={"query": "{first} vs {second}"})
    assert parser(rule).parse("so sánh Vàng với Bạc", None, Mode.COMMAND).params == {"query": "Vàng vs Bạc"}
    assert not parser(rule).parse("so sánh Vàng với", None, Mode.COMMAND).is_executable


def test_custom_hotwords_ignore_disabled():
    from voicepilot.stt.hotwords import command_phrases

    phrases = command_phrases(
        {}, {}, [definition(), definition(id="off", enabled=False, patterns=["không thêm câu này"])]
    )
    assert "hỏi google về" in phrases
    assert "không thêm câu này" not in phrases


@pytest.mark.parametrize("backend", ["google", "offline", "hybrid", "fallback"])
@pytest.mark.parametrize("action", list(ACTIONS))
def test_all_custom_actions_through_production_pipeline(qapp, backend, action):
    import importlib.util
    import sys
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "custom_pipeline_harness", Path(__file__).resolve().parents[2] / "tools/verify_features.py"
    )
    harness = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = harness
    spec.loader.exec_module(harness)
    intent = Intent[action]
    values = {
        "BROWSER_SEARCH": {"query": "Giá vàng"},
        "BROWSER_OPEN_SITE": {"url": "https://example.com"},
        "WIN_OPEN_APP": {"app": "notepad.exe"},
        "DICTATE_TEXT": {"text": "Chào Nam 👋"},
        "WIN_HOTKEY": {"keys": "ctrl+a"},
        "PPT_SAVE_AS": {"filename": "Báo cáo tháng 10"},
    }.get(action, {})
    kind = (
        AppKind.POWERPOINT
        if action.startswith("PPT_")
        else AppKind.WORD
        if action == "WORD_NEW_DOCUMENT"
        else AppKind.EXCEL
        if action == "EXCEL_NEW_WORKBOOK"
        else AppKind.BROWSER
        if "TAB" in action or action in ("BROWSER_NEW_WINDOW", "BROWSER_PRIVATE_WINDOW")
        else AppKind.OTHER
    )
    item = definition(patterns=["lệnh riêng của tôi"], intent=action, params=values)
    harness.pipeline_case(intent, "lệnh riêng của tôi", kind, values, backend, custom_commands=[item])


def test_log_never_contains_custom_contents(caplog):
    import logging

    from voicepilot.custom_commands import load_commands

    caplog.set_level(logging.DEBUG)
    secret = "Private query and user paths"
    assert load_commands([definition(intent="UNKNOWN", name=secret)]) == []
    assert secret not in caplog.text


def test_url_cannot_change_host_with_capture():
    with pytest.raises(ValueError):
        validate_commands(
            [definition(intent="BROWSER_OPEN_SITE", params={"url": "https://{topic}.example.com"})]
        )


def test_custom_text_only_runs_in_command_mode():
    item = definition(patterns=["ký tên"], intent="DICTATE_TEXT", params={"text": "Nam\nVoicePilot"})
    assert parser(item).parse("ký tên", None, Mode.COMMAND).params == {"text": "Nam\nVoicePilot"}
    assert parser(item).parse("ký tên", None, Mode.DICTATION).params == {"text": "ký tên"}


def test_capture_templates_do_not_backtrack_on_long_misses():
    import time

    custom = parser(definition(patterns=["so {a} với {b} và {c} kết thúc"], params={"query": "{a} {b} {c}"}))
    text = "so " + "với và " * 170
    started = time.monotonic()
    assert custom.parse(text, None, Mode.COMMAND).intent is Intent.UNKNOWN
    assert time.monotonic() - started < 0.5


def test_bundled_custom_examples_validate_and_execute_as_documented():
    import json
    from pathlib import Path

    raw = json.loads(
        (Path(__file__).resolve().parents[2] / "docs/custom-commands.example.json").read_text(
            encoding="utf-8"
        )
    )
    items = validate_commands(raw["commands"])
    command = parser(*items).parse("xem vàng", None, Mode.COMMAND)
    assert command.params == {"query": "giá vàng hôm nay"}
