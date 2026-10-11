"""Natural web questions retain their query without taking over explicit commands."""

import importlib.util
import sys
from pathlib import Path

import pytest

from voicepilot.config import load_config
from voicepilot.contracts import AppKind, Intent, Mode, WindowContext
from voicepilot.intent.parser import RuleParser

spec = importlib.util.spec_from_file_location(
    "search_pipeline_harness", Path(__file__).resolve().parents[2] / "tools/verify_features.py"
)
assert spec is not None and spec.loader is not None
# Dataclasses in the shared harness need their module registered during loading.
harness = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = harness
spec.loader.exec_module(harness)


@pytest.fixture
def parser(tmp_path):
    cfg = load_config(tmp_path / "absent.toml")
    return RuleParser(cfg.website_aliases, cfg.app_aliases)


@pytest.mark.parametrize("kind", [None, AppKind.POWERPOINT, AppKind.BROWSER, AppKind.OTHER])
@pytest.mark.parametrize(("text", "query"), [
    ("giá vàng hôm nay bao nhiêu", "giá vàng hôm nay bao nhiêu"),
    ("Giá vàng SJC hôm nay bao nhiêu?", "Giá vàng SJC hôm nay bao nhiêu"),
    ("gia vang hom nay bao nhieu", "gia vang hom nay bao nhieu"),
    ("giá vàng hôm nay", "giá vàng hôm nay"),
    ("thời tiết Hà Nội hôm nay", "thời tiết Hà Nội hôm nay"),
    ("Python 3.12 là gì", "Python 3.12 là gì"),
    ("Bảo tàng ở đâu", "Bảo tàng ở đâu"),
    ("World Cup diễn ra khi nào", "World Cup diễn ra khi nào"),
    ("tại sao bầu trời xanh", "tại sao bầu trời xanh"),
    ("vì sao trời mưa", "vì sao trời mưa"),
    ("cách cài Python trên Windows", "cách cài Python trên Windows"),
    ("làm thế nào để nấu cơm", "làm thế nào để nấu cơm"),
    ("làm ơn giá vàng hôm nay bao nhiêu nhé", "giá vàng hôm nay bao nhiêu"),
    ("Báo cáo Q3.v2 (bản cuối) là gì", "Báo cáo Q3.v2 (bản cuối) là gì"),
])
def test_natural_question_opens_search_with_full_query(parser, kind, text, query):
    ctx = None if kind is None else WindowContext(
        1, 2, "test.exe", "test", kind, "edge" if kind is AppKind.BROWSER else None)
    command = parser.parse(text, ctx, Mode.COMMAND)
    assert command.intent is Intent.BROWSER_SEARCH
    assert command.is_executable
    assert command.params == {"query": query}
    assert command.raw_text == text


@pytest.mark.parametrize(("text", "intent", "params"), [
    ("tìm giá vàng hôm nay bao nhiêu", Intent.BROWSER_SEARCH, {"query": "giá vàng hôm nay bao nhiêu"}),
    ("tim gia vang hom nay bao nhieu", Intent.BROWSER_SEARCH, {"query": "gia vang hom nay bao nhieu"}),
    ("lưu thành Giá vàng hôm nay bao nhiêu", Intent.PPT_SAVE_AS,
     {"filename": "Giá vàng hôm nay bao nhiêu"}),
    ("mở word", Intent.WIN_OPEN_APP, {"app": "WINWORD.EXE", "app_name": "word"}),
    ("chụp màn hình", Intent.WIN_SCREENSHOT, {}),
])
def test_explicit_command_precedes_question_search(parser, text, intent, params):
    command = parser.parse(text, None, Mode.COMMAND)
    assert command.intent is intent
    assert command.params == params


@pytest.mark.parametrize("text", ["", "ừm", "abc xyz", "bao nhiêu", "là gì", "giá", "thời tiết", "mở app lạ"])
def test_junk_and_incomplete_commands_do_not_become_search(parser, text):
    assert parser.parse(text, None, Mode.COMMAND).intent is Intent.UNKNOWN


def test_question_in_dictation_is_only_inserted_text(parser):
    text = "giá vàng hôm nay bao nhiêu"
    command = parser.parse(text, None, Mode.DICTATION)
    assert command.intent is Intent.DICTATE_TEXT
    assert command.params == {"text": text}


@pytest.mark.parametrize("backend", ["google", "offline", "hybrid", "fallback"])
def test_gold_question_runs_one_search_through_controller(qapp, backend):
    text = "giá vàng hôm nay bao nhiêu"
    harness.pipeline_case(Intent.BROWSER_SEARCH, text, AppKind.OTHER, {"query": text}, backend)


@pytest.mark.parametrize("pattern", ["lưu {question}", "{question} {query}"])
def test_question_placeholder_requires_a_search_only_capture(tmp_path, pattern):
    from importlib import resources

    text = resources.files("voicepilot").joinpath("resources/commands.toml").read_text(encoding="utf-8")
    if pattern.startswith("lưu"):
        text = text.replace('patterns = ["lưu",', 'patterns = ["lưu {question}",', 1)
    else:
        text = text.replace('"tìm {query}"', '"{question} {query}"', 1)
    path = tmp_path / "commands.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        RuleParser({}, {}, path)
