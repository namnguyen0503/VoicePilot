"""Settings search ranking: diacritic-insensitive, prefix, AND semantics, deterministic order."""

from voicepilot.ui.settings_search import SearchItem, rank


def _items() -> list[SearchItem]:
    return [
        SearchItem("hotkey", "Chung", "Phím bắt đầu nghe", ("phím tắt hotkey shortcut",), "ctrl+alt+space"),
        SearchItem("volume_up", "Lệnh có sẵn", "Windows: tăng âm lượng",
                   ("mức mặc định phần trăm %",), "tăng âm lượng · volume up"),
        SearchItem("volume_down", "Lệnh có sẵn", "Windows: giảm âm lượng",
                   ("mức mặc định phần trăm %",), "giảm âm lượng · volume down"),
        SearchItem("stt", "Chung", "Nhận dạng giọng nói", ("gipformer offline stt",), "Hybrid"),
        SearchItem("tts", "Chung", "Đọc phản hồi bằng giọng nói", ("đọc phản hồi tts",), ""),
    ]


def test_empty_or_blank_query_returns_nothing() -> None:
    assert rank(_items(), "") == []
    assert rank(_items(), "   ") == []


def test_query_ignores_diacritics_on_both_sides() -> None:
    keys = [item.key for item in rank(_items(), "am luong")]
    assert keys == ["volume_up", "volume_down"]
    assert [item.key for item in rank(_items(), "Âm Lượng")] == keys


def test_prefix_match_finds_title_word_by_its_start() -> None:
    assert [item.key for item in rank(_items(), "phim")] == ["hotkey"]


def test_every_query_token_must_match() -> None:
    assert rank(_items(), "tăng gipformer") == []
    assert [item.key for item in rank(_items(), "giọng nói")] == ["stt", "tts"]


def test_title_outranks_detail_and_keywords() -> None:
    items = [
        SearchItem("detail_only", "Chung", "Khác", (), "tăng âm"),
        SearchItem("keyword", "Chung", "Khác", ("tăng âm",), ""),
        SearchItem("title", "Chung", "Tăng âm", (), ""),
    ]
    assert [item.key for item in rank(items, "tang am")] == ["title", "keyword", "detail_only"]


def test_full_query_inside_title_adds_bonus() -> None:
    items = [
        SearchItem("scattered", "Chung", "Âm thanh lượng lớn", (), ""),
        SearchItem("phrase", "Chung", "Âm lượng hệ thống", (), ""),
    ]
    assert rank(items, "am luong")[0].key == "phrase"


def test_ties_keep_original_order() -> None:
    items = [SearchItem(f"n{i}", "Chung", "Mục giống nhau") for i in range(5)]
    assert [item.key for item in rank(items, "muc")] == ["n0", "n1", "n2", "n3", "n4"]


def test_limit_caps_results_in_rank_order() -> None:
    items = [SearchItem(f"n{i}", "Chung", "Lệnh") for i in range(30)]
    assert len(rank(items, "lenh")) == 20
    assert len(rank(items, "lenh", limit=3)) == 3


def test_punctuation_only_query_matches_nothing() -> None:
    assert rank(_items(), "%") == []
