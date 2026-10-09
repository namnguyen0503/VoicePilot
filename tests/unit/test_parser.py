"""Unit tests for the rule-based intent parser: normalization, span mapping, fixtures and validation."""

from __future__ import annotations

import json
import re
import time
import tomllib
import unicodedata
from importlib import resources
from pathlib import Path

import pytest

from voicepilot.config import AppConfig, load_config
from voicepilot.contracts import AppKind, Intent, Mode, WindowContext
from voicepilot.intent.normalize import fold, normalize
from voicepilot.intent.parser import RuleParser

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "commands.json"
COMMANDS_DOC = Path(__file__).resolve().parents[2] / "docs" / "COMMANDS.vi.md"
RESOURCE_TEXT = (
    resources.files("voicepilot").joinpath("resources", "commands.toml").read_text(encoding="utf-8")
)
COMMAND = Mode.COMMAND
DICTATION = Mode.DICTATION


def _context(kind: str) -> WindowContext | None:
    """Build a WindowContext for a fixture context name; "none" means no window context."""
    if kind == "none":
        return None
    return WindowContext(
        hwnd=1,
        pid=1,
        process_name="test.exe",
        title="test",
        app_kind=AppKind(kind),
        browser="edge" if kind == "browser" else None,
    )


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "commands.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _cases() -> list[dict[str, object]]:
    return json.loads(FIXTURES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def config(tmp_path_factory: pytest.TempPathFactory) -> AppConfig:
    """Bundled defaults.toml, loaded with the user config directory pointed at an empty temp dir."""
    home = tmp_path_factory.mktemp("voicepilot_home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("VOICEPILOT_CONFIG_DIR", str(home))
        return load_config()


@pytest.fixture(scope="module")
def parser(config: AppConfig) -> RuleParser:
    return RuleParser(config.website_aliases, config.app_aliases)


@pytest.mark.parametrize(
    "case",
    _cases(),
    ids=lambda case: f"{case['mode']}-{case['context']}-{case['text'][:32]}",
)
def test_fixture_case(parser: RuleParser, case: dict[str, object]) -> None:
    command = parser.parse(str(case["text"]), _context(str(case["context"])), Mode(case["mode"]))

    assert command.intent.value == case["intent"]
    assert command.raw_text == case["text"]
    for key, expected in dict(case.get("params", {})).items():
        assert command.params.get(key) == expected, key
    assert command.missing == tuple(case.get("missing", []))
    if command.intent is Intent.UNKNOWN:
        assert command.example


def test_normalize_ignores_case_punctuation_and_spacing() -> None:
    assert normalize("  Lưu  thành,  Báo-cáo!  ").text == "lưu thành báo cáo"


def test_normalize_composes_decomposed_vietnamese() -> None:
    decomposed = unicodedata.normalize("NFD", "Lưu thành")
    assert normalize(decomposed).text == "lưu thành"


def test_fold_removes_diacritics_and_maps_d_stroke() -> None:
    assert fold(normalize("Đường dẫn tiếp")).text == "duong dan tiep"


def test_span_returns_original_case_diacritics_and_inner_punctuation() -> None:
    source = "tìm kiếm Báo cáo Q3.v2!"
    norm = normalize(source)
    start = norm.text.index("báo")
    assert norm.original(source, start, len(norm.text)) == "Báo cáo Q3.v2"


def test_span_never_includes_trailing_sentence_punctuation() -> None:
    source = "Mở Word?!  "
    norm = normalize(source)
    assert norm.original(source, 0, len(norm.text)) == "Mở Word"


def test_span_keeps_closing_bracket_that_balances_an_inner_opener() -> None:
    source = "Báo cáo (bản cuối)."
    norm = normalize(source)
    assert norm.original(source, 0, len(norm.text)) == "Báo cáo (bản cuối)"


def test_span_drops_closing_bracket_without_an_opener() -> None:
    source = "Báo cáo bản cuối)"
    norm = normalize(source)
    assert norm.original(source, 0, len(norm.text)) == "Báo cáo bản cuối"


def test_span_keeps_closing_quote_only_when_the_quote_is_open() -> None:
    assert normalize('"Báo cáo"').text == "báo cáo"
    source = 'Báo "cáo'
    norm = normalize(source)
    assert norm.original(source + '"', 0, len(norm.text)) == 'Báo "cáo"'


def test_span_of_decomposed_input_is_the_original_decomposed_text() -> None:
    source = unicodedata.normalize("NFD", "Lưu thành Báo cáo.")
    norm = normalize(source)
    start = norm.text.index("báo")
    assert norm.original(source, start, len(norm.text)) == unicodedata.normalize("NFD", "Báo cáo")


def test_fold_keeps_spans_aligned_with_the_original() -> None:
    source = "Đường Dẫn Q3.v2"
    folded = fold(normalize(source))
    assert folded.text == "duong dan q3 v2"
    assert folded.original(source, folded.text.index("q3"), len(folded.text)) == "Q3.v2"


def test_filename_parameter_is_the_original_substring(parser: RuleParser) -> None:
    text = "Lưu thành Báo cáo tháng 10."
    command = parser.parse(text, _context("powerpoint"), COMMAND)
    assert command.intent is Intent.PPT_SAVE_AS
    assert command.params["filename"] == "Báo cáo tháng 10"


def test_filename_keeps_decomposed_form_from_input(parser: RuleParser) -> None:
    text = unicodedata.normalize("NFD", "lưu thành Báo cáo")
    command = parser.parse(text, _context("powerpoint"), COMMAND)
    assert command.params["filename"] == unicodedata.normalize("NFD", "Báo cáo")


def test_folded_pass_matches_when_diacritics_are_missing(parser: RuleParser) -> None:
    assert parser.parse("slide tiep", _context("powerpoint"), COMMAND).intent is Intent.PPT_NEXT_SLIDE


def test_diacritic_alias_wins_over_folded_alias() -> None:
    parser = RuleParser({"ghi chu": "https://plain.example", "ghi chú": "https://accent.example"}, {})
    assert parser.parse("mở ghi chú", None, COMMAND).params["url"] == "https://accent.example"
    assert parser.parse("mở ghi chu", None, COMMAND).params["url"] == "https://plain.example"


def test_polite_fillers_are_removed_at_both_ends(parser: RuleParser) -> None:
    command = parser.parse("làm ơn giúp tôi mở word nhé ạ", None, COMMAND)
    assert command.intent is Intent.WIN_OPEN_APP
    assert command.params["app"] == "WINWORD.EXE"


def test_most_specific_phrase_wins(parser: RuleParser) -> None:
    save_as = parser.parse("lưu thành báo cáo", _context("powerpoint"), COMMAND)
    assert save_as.intent is Intent.PPT_SAVE_AS
    assert save_as.params["filename"] == "báo cáo"

    search = parser.parse("tìm kiếm thời tiết", _context("browser"), COMMAND)
    assert search.intent is Intent.BROWSER_SEARCH
    assert search.params["query"] == "thời tiết"


def test_ambiguous_phrase_uses_window_context(parser: RuleParser) -> None:
    assert parser.parse("tiếp theo", _context("powerpoint"), COMMAND).intent is Intent.PPT_NEXT_SLIDE
    assert parser.parse("tiếp theo", _context("browser"), COMMAND).intent is Intent.BROWSER_NEXT_TAB


def test_ambiguous_phrase_without_fitting_context_is_unknown_with_example(parser: RuleParser) -> None:
    command = parser.parse("tiếp theo", None, COMMAND)
    assert command.intent is Intent.UNKNOWN
    assert command.example


def test_open_target_routes_bare_domain_and_app(parser: RuleParser) -> None:
    assert parser.parse("mở youtube.com", None, COMMAND).params["url"] == "https://youtube.com"
    assert parser.parse("mở powerpoint", None, COMMAND).params["app"] == "POWERPNT.EXE"


def test_unknown_open_target_is_not_guessed(parser: RuleParser) -> None:
    command = parser.parse("mở xyzabc", None, COMMAND)
    assert command.intent is Intent.UNKNOWN
    assert command.example


def test_missing_parameter_is_reported_and_not_executable(parser: RuleParser) -> None:
    command = parser.parse("lưu tên khác", _context("powerpoint"), COMMAND)
    assert command.intent is Intent.PPT_SAVE_AS
    assert command.missing == ("filename",)
    assert not command.is_executable


def test_dictation_keeps_original_text_and_never_matches_commands(parser: RuleParser) -> None:
    command = parser.parse("  Mở Word, rồi lưu.  ", _context("powerpoint"), DICTATION)
    assert command.intent is Intent.DICTATE_TEXT
    assert command.params == {"text": "Mở Word, rồi lưu."}


def test_empty_dictation_is_missing_text(parser: RuleParser) -> None:
    command = parser.parse("   ", None, DICTATION)
    assert command.intent is Intent.DICTATE_TEXT
    assert command.missing == ("text",)


def test_parse_is_fast_for_a_short_command(parser: RuleParser) -> None:
    context = _context("powerpoint")
    start = time.perf_counter()
    for _ in range(100):
        parser.parse("lưu thành báo cáo tháng 10", context, COMMAND)
    per_call_ms = (time.perf_counter() - start) / 100 * 1000
    assert per_call_ms < 5


def test_long_input_does_not_backtrack(parser: RuleParser) -> None:
    text = "tìm " + "a " * 2000 + "!"
    start = time.perf_counter()
    command = parser.parse(text, None, COMMAND)
    assert command.intent is Intent.BROWSER_SEARCH
    assert time.perf_counter() - start < 0.5


def test_loader_rejects_unknown_intent_name(tmp_path: Path) -> None:
    text = RESOURCE_TEXT.replace('name = "PPT_NEXT_SLIDE"', 'name = "PPT_NEXT"', 1)
    with pytest.raises(ValueError, match="unknown intent"):
        RuleParser({}, {}, _write(tmp_path, text))


def test_loader_rejects_bad_placeholder(tmp_path: Path) -> None:
    text = RESOURCE_TEXT.replace('"tìm {query}"', '"tìm {questions}"', 1)
    with pytest.raises(ValueError, match="bad placeholder"):
        RuleParser({}, {}, _write(tmp_path, text))


def test_loader_rejects_unknown_context(tmp_path: Path) -> None:
    text = RESOURCE_TEXT.replace('context = ["powerpoint"]', 'context = ["excel"]', 1)
    with pytest.raises(ValueError, match="unknown context"):
        RuleParser({}, {}, _write(tmp_path, text))


def test_loader_requires_every_command_intent(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing command intents"):
        RuleParser({}, {}, _write(tmp_path, '[[intent]]\nname = "PPT_SAVE"\npatterns = ["lưu"]\n'))


def _literal_phrases(pattern: str) -> str:
    """A pattern with its {placeholders} removed, e.g. "lưu thành {filename}" -> "lưu thành"."""
    return " ".join(re.sub(r"\{[^{}]*\}", " ", pattern).split())


def test_commands_doc_mentions_every_intent() -> None:
    doc = unicodedata.normalize("NFC", COMMANDS_DOC.read_text(encoding="utf-8")).casefold()
    entries = tomllib.loads(RESOURCE_TEXT)["intent"]
    missing = []
    for entry in entries:
        if entry["name"] == Intent.DICTATE_TEXT.value:
            continue
        phrases = [_literal_phrases(p) for p in entry["patterns"]]
        phrases = [unicodedata.normalize("NFC", p).casefold() for p in phrases if p]
        if not any(phrase in doc for phrase in phrases):
            missing.append(entry["name"])
    assert not missing, f"docs/COMMANDS.vi.md does not mention: {missing}"
