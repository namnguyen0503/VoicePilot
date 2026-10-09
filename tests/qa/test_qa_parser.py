"""Black-box QA tests for the rule parser, written from docs/COMMANDS.vi.md and docs/PLAN.md."""

from __future__ import annotations

import shutil
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from voicepilot.config import load_config
from voicepilot.contracts import AppKind, Intent, Mode, WindowContext
from voicepilot.intent.parser import RuleParser

COMMANDS_TOML = Path(__file__).resolve().parents[2] / "src" / "voicepilot" / "resources" / "commands.toml"

KINDS = {
    "ppt": AppKind.POWERPOINT,
    "browser": AppKind.BROWSER,
    "other": AppKind.OTHER,
    "none": AppKind.NONE,
}


def window(kind: str | None) -> WindowContext | None:
    """Window context for a short kind name; None means no context at all."""
    if kind is None:
        return None
    browser = "edge" if kind == "browser" else None
    return WindowContext(
        hwnd=100, pid=200, process_name="x.exe", title="t", app_kind=KINDS[kind], browser=browser
    )


@pytest.fixture
def cfg(config_home):
    return load_config()  # config_home points at an empty dir, so this is the shipped defaults


@pytest.fixture
def parser(cfg):
    return RuleParser(cfg.website_aliases, cfg.app_aliases)


def parse(parser: RuleParser, text: str, kind: str | None = "none"):
    return parser.parse(text, window(kind), Mode.COMMAND)


# --- every example phrase in COMMANDS.vi.md reaches its intent -------------------------

DOC_PHRASES = [
    ("lưu", "ppt", Intent.PPT_SAVE, {}),
    ("lưu lại", "ppt", Intent.PPT_SAVE, {}),
    ("lưu file", "ppt", Intent.PPT_SAVE, {}),
    ("save", "ppt", Intent.PPT_SAVE, {}),
    ("lưu thành Báo cáo tháng 10", "ppt", Intent.PPT_SAVE_AS, {"filename": "Báo cáo tháng 10"}),
    ("lưu tên khác là Hợp đồng 2026", "ppt", Intent.PPT_SAVE_AS, {"filename": "Hợp đồng 2026"}),
    ("lưu với tên Kế hoạch 10 năm", "ppt", Intent.PPT_SAVE_AS, {"filename": "Kế hoạch 10 năm"}),
    ("lưu thành Bài thuyết trình, chấm pptx", "ppt", Intent.PPT_SAVE_AS, {"filename": "Bài thuyết trình"}),
    ("slide tiếp theo", "ppt", Intent.PPT_NEXT_SLIDE, {}),
    ("trang tiếp", "ppt", Intent.PPT_NEXT_SLIDE, {}),
    ("chuyển slide", "ppt", Intent.PPT_NEXT_SLIDE, {}),
    ("qua slide", "ppt", Intent.PPT_NEXT_SLIDE, {}),
    ("next slide", "ppt", Intent.PPT_NEXT_SLIDE, {}),
    ("slide trước", "ppt", Intent.PPT_PREV_SLIDE, {}),
    ("trang trước", "ppt", Intent.PPT_PREV_SLIDE, {}),
    ("quay lại slide", "ppt", Intent.PPT_PREV_SLIDE, {}),
    ("lùi slide", "ppt", Intent.PPT_PREV_SLIDE, {}),
    ("previous slide", "ppt", Intent.PPT_PREV_SLIDE, {}),
    ("bắt đầu trình chiếu", "ppt", Intent.PPT_START_SHOW, {}),
    ("trình chiếu", "ppt", Intent.PPT_START_SHOW, {}),
    ("chiếu slide", "ppt", Intent.PPT_START_SHOW, {}),
    ("start show", "ppt", Intent.PPT_START_SHOW, {}),
    ("dừng trình chiếu", "ppt", Intent.PPT_STOP_SHOW, {}),
    ("kết thúc trình chiếu", "ppt", Intent.PPT_STOP_SHOW, {}),
    ("thoát trình chiếu", "ppt", Intent.PPT_STOP_SHOW, {}),
    ("đóng trình chiếu", "ppt", Intent.PPT_STOP_SHOW, {}),
    ("end show", "ppt", Intent.PPT_STOP_SHOW, {}),
    ("mở youtube", "none", Intent.BROWSER_OPEN_SITE, {"url": "https://www.youtube.com"}),
    ("vào facebook", "none", Intent.BROWSER_OPEN_SITE, {"url": "https://www.facebook.com"}),
    ("mở trang vnexpress", "none", Intent.BROWSER_OPEN_SITE, {"url": "https://vnexpress.net"}),
    ("mở vnexpress.net", "none", Intent.BROWSER_OPEN_SITE, {"url": "https://vnexpress.net"}),
    ("mở https://example.com", "none", Intent.BROWSER_OPEN_SITE, {"url": "https://example.com"}),
    ("tìm thời tiết hôm nay", "none", Intent.BROWSER_SEARCH, {"query": "thời tiết hôm nay"}),
    ("tìm kiếm giá vàng", "none", Intent.BROWSER_SEARCH, {"query": "giá vàng"}),
    ("google giá vàng", "none", Intent.BROWSER_SEARCH, {"query": "giá vàng"}),
    ("tra cứu lịch thi học kỳ", "none", Intent.BROWSER_SEARCH, {"query": "lịch thi học kỳ"}),
    ("tab tiếp theo", "browser", Intent.BROWSER_NEXT_TAB, {}),
    ("tab sau", "browser", Intent.BROWSER_NEXT_TAB, {}),
    ("chuyển tab", "browser", Intent.BROWSER_NEXT_TAB, {}),
    ("next tab", "browser", Intent.BROWSER_NEXT_TAB, {}),
    ("tab trước", "browser", Intent.BROWSER_PREV_TAB, {}),
    ("quay lại tab", "browser", Intent.BROWSER_PREV_TAB, {}),
    ("lùi tab", "browser", Intent.BROWSER_PREV_TAB, {}),
    ("previous tab", "browser", Intent.BROWSER_PREV_TAB, {}),
    ("tab mới", "browser", Intent.BROWSER_NEW_TAB, {}),
    ("mở tab mới", "browser", Intent.BROWSER_NEW_TAB, {}),
    ("thêm tab", "browser", Intent.BROWSER_NEW_TAB, {}),
    ("tab trống", "browser", Intent.BROWSER_NEW_TAB, {}),
    ("đóng tab", "browser", Intent.BROWSER_CLOSE_TAB, {}),
    ("đóng tab này", "browser", Intent.BROWSER_CLOSE_TAB, {}),
    ("tắt tab", "browser", Intent.BROWSER_CLOSE_TAB, {}),
    ("close tab", "browser", Intent.BROWSER_CLOSE_TAB, {}),
    ("mở word", "none", Intent.WIN_OPEN_APP, {"app": "WINWORD.EXE"}),
    ("mở excel", "none", Intent.WIN_OPEN_APP, {"app": "EXCEL.EXE"}),
    ("mở powerpoint", "none", Intent.WIN_OPEN_APP, {"app": "POWERPNT.EXE"}),
    ("khởi động notepad", "none", Intent.WIN_OPEN_APP, {"app": "notepad.exe"}),
    ("chạy máy tính", "none", Intent.WIN_OPEN_APP, {"app": "calc.exe"}),
    ("mở ứng dụng Word", "none", Intent.WIN_OPEN_APP, {"app": "WINWORD.EXE"}),
    ("chụp màn hình", "none", Intent.WIN_SCREENSHOT, {}),
    ("chụp ảnh màn hình", "none", Intent.WIN_SCREENSHOT, {}),
    ("chụp màn", "none", Intent.WIN_SCREENSHOT, {}),
]


@pytest.mark.parametrize("text,kind,intent,params", DOC_PHRASES, ids=[p[0] for p in DOC_PHRASES])
def test_documented_phrase_maps_to_intent(parser, text, kind, intent, params):
    # docs/COMMANDS.vi.md sections 1-3
    cmd = parse(parser, text, kind)
    assert cmd.intent is intent, cmd
    assert cmd.is_executable, cmd
    for key, value in params.items():
        assert cmd.params.get(key) == value, cmd


def test_every_command_intent_is_reachable(parser):
    # PLAN.md "Hiểu lệnh": each v1 function must be reachable from speech
    reached = {parse(parser, text, kind).intent for text, kind, _, _ in DOC_PHRASES}
    expected = {i for i in Intent if i not in (Intent.UNKNOWN, Intent.DICTATE_TEXT)}
    assert expected <= reached, sorted(i.value for i in expected - reached)


# --- window context decides ambiguous phrases (COMMANDS.vi.md "Từ mơ hồ") ----------------


@pytest.mark.parametrize(
    "text,kind,intent",
    [
        ("tiếp theo", "ppt", Intent.PPT_NEXT_SLIDE),
        ("tiếp theo", "browser", Intent.BROWSER_NEXT_TAB),
        ("quay lại", "ppt", Intent.PPT_PREV_SLIDE),
        ("quay lại", "browser", Intent.BROWSER_PREV_TAB),
        # "đóng" and "mở mới" are tab commands in every window; the executor decides whether to act
        ("đóng", "ppt", Intent.BROWSER_CLOSE_TAB),
        ("đóng", "other", Intent.BROWSER_CLOSE_TAB),
        ("đóng", "browser", Intent.BROWSER_CLOSE_TAB),
        ("mở mới", "ppt", Intent.BROWSER_NEW_TAB),
        ("mở mới", "browser", Intent.BROWSER_NEW_TAB),
    ],
)
def test_context_resolves_shared_phrases(parser, text, kind, intent):
    assert parse(parser, text, kind).intent is intent


@pytest.mark.parametrize("kind", ["other", "none", None])
@pytest.mark.parametrize("text", ["tiếp theo", "quay lại"])
def test_shared_phrase_without_fitting_window_is_unknown_with_example(parser, text, kind):
    cmd = parse(parser, text, kind)
    assert cmd.intent is Intent.UNKNOWN
    assert cmd.example


# --- dictation mode never runs a command --------------------------------------------------


@pytest.mark.parametrize(
    "text", ["lưu", "mở Word", "tiếp theo", "save", "đóng", "mở mới", "tìm giá vàng", "chụp màn hình"]
)
@pytest.mark.parametrize("kind", [None, "ppt", "browser", "other", "none"])
def test_dictation_mode_is_text_only(parser, text, kind):
    # PLAN.md "Chính tả": every utterance is text, the command table is not consulted
    cmd = parser.parse(text, window(kind), Mode.DICTATION)
    assert cmd.intent is Intent.DICTATE_TEXT
    assert cmd.params == {"text": text}
    assert cmd.is_executable


def test_dictation_keeps_punctuation_and_diacritics(parser):
    cmd = parser.parse("Xin chào, bạn khỏe không?", window("other"), Mode.DICTATION)
    assert cmd.params["text"] == "Xin chào, bạn khỏe không?"


@pytest.mark.parametrize("text", ["", "   ", "\t\n"])
def test_dictation_blank_is_not_executable(parser, text):
    cmd = parser.parse(text, window("other"), Mode.DICTATION)
    assert cmd.intent is Intent.DICTATE_TEXT
    assert not cmd.is_executable
    assert cmd.missing == ("text",)


# --- polite fillers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,kind,intent",
    [
        ("làm ơn mở youtube ạ", "none", Intent.BROWSER_OPEN_SITE),
        ("giúp tôi tìm giá vàng ạ", "none", Intent.BROWSER_SEARCH),
        ("giúp tôi mở youtube", "none", Intent.BROWSER_OPEN_SITE),
        ("Slide tiếp theo nhé", "ppt", Intent.PPT_NEXT_SLIDE),
        ("slide tiếp theo với", "ppt", Intent.PPT_NEXT_SLIDE),
        ("tiếp theo ơi", "ppt", Intent.PPT_NEXT_SLIDE),
        ("VoicePilot ơi, mở youtube nhé", "none", Intent.BROWSER_OPEN_SITE),
        ("tìm giá vàng nhé", "none", Intent.BROWSER_SEARCH),
    ],
)
def test_documented_fillers_are_ignored(parser, text, kind, intent):
    # COMMANDS.vi.md: "làm ơn", "giúp tôi", "nhé", "ạ", "với", "ơi" at start or end are dropped
    assert parse(parser, text, kind).intent is intent


def test_filler_only_is_unknown(parser):
    assert parse(parser, "làm ơn nhé ạ").intent is Intent.UNKNOWN


@pytest.mark.parametrize(
    "text,intent,params",
    [
        ("mở YouTube giúp tôi", Intent.BROWSER_OPEN_SITE, {"url": "https://www.youtube.com"}),
        ("mở youtube giúp mình", Intent.BROWSER_OPEN_SITE, {"url": "https://www.youtube.com"}),
        ("mở youtube giùm", Intent.BROWSER_OPEN_SITE, {"url": "https://www.youtube.com"}),
        ("mở youtube giùm tôi", Intent.BROWSER_OPEN_SITE, {"url": "https://www.youtube.com"}),
        ("mở youtube giùm mình", Intent.BROWSER_OPEN_SITE, {"url": "https://www.youtube.com"}),
        ("tìm giá vàng giúp tôi", Intent.BROWSER_SEARCH, {"query": "giá vàng"}),
        ("tìm giá vàng giúp mình", Intent.BROWSER_SEARCH, {"query": "giá vàng"}),
        ("tìm giá vàng giùm mình nhé", Intent.BROWSER_SEARCH, {"query": "giá vàng"}),
        ("giùm tôi tìm giá vàng", Intent.BROWSER_SEARCH, {"query": "giá vàng"}),
        ("cho mình mở word", Intent.WIN_OPEN_APP, {"app": "WINWORD.EXE"}),
        ("cho mình tìm giá vàng", Intent.BROWSER_SEARCH, {"query": "giá vàng"}),
    ],
)
def test_helper_fillers_are_stripped_at_the_documented_ends(parser, text, intent, params):
    # COMMANDS.vi.md section "Cách dùng nhanh": giúp tôi / giúp mình / giùm / cho mình
    cmd = parse(parser, text)
    assert cmd.intent is intent, cmd
    for key, value in params.items():
        assert cmd.params.get(key) == value, cmd


def test_search_query_not_truncated_by_trailing_di(parser):
    cmd = parse(parser, "tìm đường đi")
    assert cmd.intent is Intent.BROWSER_SEARCH
    assert cmd.params["query"] == "đường đi"


def test_search_query_with_inner_di_is_kept(parser):
    assert parse(parser, "tìm đường đi Hà Nội").params["query"] == "đường đi Hà Nội"


def test_free_text_keeps_trailing_di_and_voi_but_drops_polite_fillers(parser):
    assert parse(parser, "tìm đường đi nhé").params["query"] == "đường đi"
    assert parse(parser, "tìm đồ ăn với").params["query"] == "đồ ăn với"
    assert parse(parser, "lưu thành Kế hoạch đi ạ", "ppt").params["filename"] == "Kế hoạch đi"


@pytest.mark.parametrize(
    "text,kind,intent,params",
    [
        ("mở youtube đi", "none", Intent.BROWSER_OPEN_SITE, {"url": "https://www.youtube.com"}),
        ("mở word với", "none", Intent.WIN_OPEN_APP, {"app": "WINWORD.EXE"}),
        ("slide tiếp đi", "ppt", Intent.PPT_NEXT_SLIDE, {}),
        ("tab tiếp với", "browser", Intent.BROWSER_NEXT_TAB, {}),
        ("chụp màn hình đi", "none", Intent.WIN_SCREENSHOT, {}),
    ],
)
def test_short_commands_and_names_still_strip_di_and_voi(parser, text, kind, intent, params):
    cmd = parse(parser, text, kind)
    assert cmd.intent is intent, cmd
    for key, value in params.items():
        assert cmd.params.get(key) == value, cmd


# --- parameter fidelity -----------------------------------------------------------------


def test_search_keeps_case_and_diacritics(parser):
    assert parse(parser, "Tìm Giá Vàng Hôm Nay!").params["query"] == "Giá Vàng Hôm Nay"


def test_search_keeps_symbols_and_digits(parser):
    assert parse(parser, "tìm C++ & Java 100% #1").params["query"] == "C++ & Java 100% #1"


def test_filename_keeps_inner_punctuation_and_digits(parser):
    assert parse(parser, "lưu thành Báo cáo Q3.v2", "ppt").params["filename"] == "Báo cáo Q3.v2"


def test_filename_keeps_english_mix(parser):
    cmd = parse(parser, "lưu thành Kế hoạch Marketing 2026 v2", "ppt")
    assert cmd.params["filename"] == "Kế hoạch Marketing 2026 v2"


def test_filename_trailing_pptx_is_removed(parser):
    assert parse(parser, "lưu thành Báo cáo.pptx", "ppt").params["filename"] == "Báo cáo"


def test_filename_only_suffix_is_missing(parser):
    cmd = parse(parser, "lưu thành chấm pptx", "ppt")
    assert cmd.intent is Intent.PPT_SAVE_AS
    assert cmd.missing == ("filename",)


def test_nfd_speech_keeps_vietnamese_content(parser):
    text = unicodedata.normalize("NFD", "lưu thành Báo cáo tháng 10")
    filename = parse(parser, text, "ppt").params["filename"]
    assert unicodedata.normalize("NFC", filename) == "Báo cáo tháng 10"


def test_gipformer_lowercase_no_punctuation(parser):
    cmd = parse(parser, "lưu thành báo cáo tháng mười", "ppt")
    assert cmd.params["filename"] == "báo cáo tháng mười"


def test_diacritic_less_speech_keeps_spoken_query(parser):
    assert parse(parser, "tim kiem gia vang").params["query"] == "gia vang"


def test_extra_spaces_and_tabs_do_not_leak_into_intent(parser):
    cmd = parse(parser, "   \t tìm   giá   vàng  \n")
    assert cmd.intent is Intent.BROWSER_SEARCH
    assert " ".join(cmd.params["query"].split()) == "giá vàng"


def test_mixed_diacritics_do_not_leak_command_word_into_query(parser):
    assert parse(parser, "tìm kiem giá vàng").params["query"] == "giá vàng"
    assert parse(parser, "tìm kiếm giá vàng").params["query"] == "giá vàng"


def test_mixed_diacritics_prefer_the_more_specific_phrase_of_the_same_intent(parser):
    # "tìm kiem" matches "tìm {query}" with diacritics but "tìm kiếm {query}" when folded
    cmd = parse(parser, "tìm kiem slide mẫu")
    assert cmd.intent is Intent.BROWSER_SEARCH
    assert cmd.params["query"] == "slide mẫu"
    # a same-length folded match must not replace the plain match
    assert parse(parser, "tiếp theo", "ppt").intent is Intent.PPT_NEXT_SLIDE


@pytest.mark.parametrize(
    "text,filename",
    [
        ("lưu thành Báo cáo Q3 (bản cuối)", "Báo cáo Q3 (bản cuối)"),
        ("lưu thành Báo cáo Q3 (bản cuối).", "Báo cáo Q3 (bản cuối)"),
        ("lưu thành Báo cáo (bản cuối))", "Báo cáo (bản cuối)"),
        ("lưu thành “Báo cáo”", "Báo cáo"),
        ('lưu thành "Báo cáo"', "Báo cáo"),
        ("lưu thành (Báo cáo)", "Báo cáo"),
        ("lưu thành Báo cáo (2024", "Báo cáo (2024"),
        ("lưu thành Báo cáo)", "Báo cáo"),
        ("lưu thành Báo cáo (bản cuối) nhé", "Báo cáo (bản cuối)"),
    ],
)
def test_filename_keeps_balanced_brackets_and_drops_unmatched_ones(parser, text, filename):
    assert parse(parser, text, "ppt").params["filename"] == filename


# --- every alias in the shipped config resolves --------------------------------------------


def test_every_website_alias_resolves(parser, cfg):
    for spoken, url in cfg.website_aliases.items():
        cmd = parse(parser, f"mở {spoken}")
        assert cmd.intent is Intent.BROWSER_OPEN_SITE, spoken
        assert cmd.params["url"] == url, spoken


def test_every_app_alias_resolves(parser, cfg):
    for spoken, target in cfg.app_aliases.items():
        cmd = parse(parser, f"mở {spoken}")
        assert cmd.intent is Intent.WIN_OPEN_APP, spoken
        assert cmd.params["app"] == target, spoken


def test_alias_matches_without_diacritics(parser):
    assert parse(parser, "mo yu tup").params["url"] == "https://www.youtube.com"
    assert parse(parser, "mo may tinh").params["app"] == "calc.exe"


def test_custom_alias_from_config_is_used(config_home):
    parser = RuleParser({"ghi chú": "https://example.org/notes"}, {})
    assert parse(parser, "mở trang ghi chú").params["url"] == "https://example.org/notes"


def test_unknown_names_are_not_opened(parser):
    for text in ["mở trang xyzzy", "vào xyzzy", "mở tài liệu", "mở bài thuyết trình"]:
        assert parse(parser, text).intent is Intent.UNKNOWN, text


# --- missing parameters ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text,kind,missing",
    [
        ("tìm", "none", ("query",)),
        ("tìm kiếm", "none", ("query",)),
        ("lưu thành", "ppt", ("filename",)),
        ("mở", "none", ("app_name",)),
        ("mở trang", "none", ("site",)),
        ("mở ứng dụng", "none", ("app_name",)),
    ],
)
def test_missing_parameter_is_reported_with_example(parser, text, kind, missing):
    cmd = parse(parser, text, kind)
    assert not cmd.is_executable
    assert cmd.missing == missing
    assert cmd.example


# --- unknown and junk input -------------------------------------------------------------


@pytest.mark.parametrize("text", ["", "   ", "🎉", "...", "!!!", "???", "ừm", "abc xyz", "🎉 🎉"])
def test_junk_is_unknown_with_example(parser, text):
    cmd = parse(parser, text)
    assert cmd.intent is Intent.UNKNOWN
    assert cmd.example


def test_raw_text_is_kept_on_command(parser):
    assert parse(parser, "Slide tiếp theo.", "ppt").raw_text == "Slide tiếp theo."


# --- hostile input ---------------------------------------------------------------------

HOSTILE_OPEN = [
    "mở javascript:alert(1)",
    "mở JaVaScRiPt:alert(1)",
    "mở file:///C:/",
    "mở file:///C:/Windows",
    "mở http://",
    "mở https://",
    "mở data:text/html,hi",
    "mở vbscript:msgbox",
    "mở \\\\server\\share",
    "mở ..",
    "mở https:// bad host.com",
]


@pytest.mark.parametrize("text", HOSTILE_OPEN)
def test_hostile_open_never_yields_non_http_url(parser, text):
    cmd = parse(parser, text)
    if cmd.intent is Intent.BROWSER_OPEN_SITE:
        assert urlsplit(cmd.params["url"]).scheme in ("http", "https"), cmd.params
    else:
        assert cmd.intent in (Intent.UNKNOWN, Intent.WIN_OPEN_APP)


HOSTILE_FILENAMES = [
    "lưu thành ../../Windows/system32/x",
    "lưu thành CON",
    "lưu thành a/b",
    "lưu thành C:\\temp\\x",
    "lưu thành a|b",
]


@pytest.mark.parametrize("text", HOSTILE_FILENAMES)
def test_hostile_filename_is_passed_through_for_sanitizer(parser, text):
    # The parser keeps the spoken name; rejection is the job of sanitize_filename (tested in test_qa_windows).
    cmd = parse(parser, text, "ppt")
    assert cmd.intent is Intent.PPT_SAVE_AS
    assert cmd.params.get("filename"), cmd


@pytest.mark.parametrize(
    "text,kind",
    [
        ("mở ftp://x.y", "none"),
        ("mở C:\\Windows\\System32\\calc.exe", "none"),
        ("mở notepad.exe", "none"),
        ("mở report.pdf", "none"),
        ("mở setup.msi", "none"),
        ("mở a..b", "none"),
        ("mở x.y", "none"),
        ("mở 192.168.1", "none"),
        ("mở http://", "none"),
    ],
)
def test_scheme_path_or_file_name_is_not_turned_into_web_url(parser, text, kind):
    cmd = parse(parser, text, kind)
    assert cmd.intent is not Intent.BROWSER_OPEN_SITE, cmd
    assert cmd.intent is Intent.UNKNOWN or cmd.intent is Intent.WIN_OPEN_APP, cmd


@pytest.mark.parametrize(
    "text,url",
    [
        ("mở vnexpress.net/abc", "https://vnexpress.net/abc"),
        ("mở github.com/Foo", "https://github.com/Foo"),
        ("mở localhost.dev:8080", "https://localhost.dev:8080"),
        ("mở https://example.com:8080/Path", "https://example.com:8080/Path"),
        ("mở HTTP://Example.com", "http://example.com"),
        ("mở báo.vn", "https://báo.vn"),
    ],
)
def test_valid_web_addresses_are_opened(parser, text, url):
    cmd = parse(parser, text)
    assert cmd.intent is Intent.BROWSER_OPEN_SITE, cmd
    assert cmd.params["url"] == url


# --- performance ----------------------------------------------------------------------


def test_very_long_utterance_is_fast_and_intact(parser):
    long_query = "giá vàng " * 5000
    start = time.perf_counter()
    cmd = parse(parser, "tìm " + long_query)
    elapsed = time.perf_counter() - start
    assert cmd.intent is Intent.BROWSER_SEARCH
    assert len(cmd.params["query"]) == len(long_query.strip())
    assert elapsed < 2.0


def test_long_noise_is_fast(parser):
    start = time.perf_counter()
    cmd = parse(parser, "a " * 20000)
    assert time.perf_counter() - start < 2.0
    assert cmd.intent is Intent.UNKNOWN


# --- commands.toml validation ---------------------------------------------------------


def _write_commands(tmp_path: Path, mutate) -> Path:
    text = COMMANDS_TOML.read_text(encoding="utf-8")
    target = tmp_path / "commands.toml"
    target.write_text(mutate(text), encoding="utf-8")
    return target


@pytest.mark.parametrize(
    "mutate,fragment",
    [
        (lambda t: t.replace('name = "PPT_SAVE"', 'name = "PPT_SAVE_X"'), "unknown intent"),
        (lambda t: t.replace('name = "WIN_SCREENSHOT"', 'name = "UNKNOWN"'), "UNKNOWN"),
        (lambda t: t.replace('context = ["powerpoint"]', 'context = ["desktop"]', 1), "unknown context"),
        (lambda t: t.replace('"lưu thành {filename}"', '"lưu thành {file}"'), "bad placeholder"),
        (lambda t: t.replace('"lưu thành {filename}"', '"lưu thành {filename"'), "unbalanced"),
        (lambda t: t.replace('"lưu thành {filename}"', '"{filename}{query}"'), "adjacent"),
        (lambda t: t.replace('patterns = ["lưu", "lưu lại"', 'patterns = [3, "lưu lại"'), "list of strings"),
    ],
)
def test_malformed_command_table_is_rejected(tmp_path, mutate, fragment):
    path = _write_commands(tmp_path, mutate)
    with pytest.raises(ValueError) as info:
        RuleParser({}, {}, commands_path=path)
    assert fragment in str(info.value)


def test_command_table_without_an_intent_is_rejected(tmp_path):
    path = _write_commands(
        tmp_path, lambda t: t.replace('name = "WIN_SCREENSHOT"', 'name = "WIN_SCREENSHOT_OLD"')
    )
    with pytest.raises(ValueError):
        RuleParser({}, {}, commands_path=path)


def test_shipped_command_table_loads_from_custom_path(tmp_path):
    copy = tmp_path / "commands.toml"
    shutil.copy(COMMANDS_TOML, copy)
    parser = RuleParser({"youtube": "https://www.youtube.com"}, {}, commands_path=copy)
    assert parse(parser, "mở youtube").intent is Intent.BROWSER_OPEN_SITE
