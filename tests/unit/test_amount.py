"""Spoken percentage parsing for "tăng âm lượng 30%" style commands."""

import pytest

from voicepilot.intent.amount import parse_percent


@pytest.mark.parametrize(
    "text,expected",
    [
        ("20", 20),
        ("20%", 20),
        ("20 %", 20),
        ("20 phần trăm", 20),
        ("20 phan tram", 20),
        ("1", 1),
        ("100", 100),
        ("100%", 100),
        ("thêm 15%", 15),
        ("bớt 10 phần trăm", 10),
        ("khoảng 30", 30),
        ("20 phần trăm nữa", 20),
        ("20 nữa", 20),
        ("mười", 10),
        ("mười lăm", 15),
        ("mười lam", 15),
        ("hai mươi", 20),
        ("hai mươi mốt", 21),
        ("hai mươi một", 21),
        ("ba mươi tư", 34),
        ("ba mươi bốn", 34),
        ("ba mươi lăm", 35),
        ("ba mươi lam phan tram", 35),
        ("hai lăm", 25),
        ("năm mươi", 50),
        ("năm mươi phần trăm", 50),
        ("một trăm", 100),
        ("mot tram", 100),
        ("chín mươi chín", 99),
        ("một", 1),
        ("mốt", 1),
        ("tám", 8),
        ("tam", 8),
        ("bảy mươi lẻ hai", 72),
        ("bảy mươi linh hai", 72),
        ("mươi lăm", 15),
        ("mười Lăm Phần Trăm", 15),
    ],
)
def test_parse_percent_accepts_digits_and_words(text, expected):
    assert parse_percent(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "0",
        "00",
        "101",
        "1000",
        "to lên",
        "lên 30",
        "30 đơn vị",
        "không",
        "một trăm lẻ một",
        "trăm",
        "hai mươi ba mươi",
        "mười mười",
        "phần trăm",
        "thêm",
        "%",
        "năm mươi lăm mươi",
        "hai mươi x",
    ],
)
def test_parse_percent_rejects_non_percentages(text):
    assert parse_percent(text) is None


def test_parse_percent_rejects_zero_and_over_one_hundred_in_words():
    assert parse_percent("không") is None
    assert parse_percent("một trăm linh một") is None


def test_parse_percent_folded_and_diacritic_text_agree():
    for diacritic, folded in [("hai mươi mốt", "hai muoi mot"), ("mười lăm phần trăm", "muoi lam phan tram"),
                              ("ba mươi tư", "ba muoi tu")]:
        assert parse_percent(diacritic) == parse_percent(folded)


def test_parse_percent_non_string_is_none():
    assert parse_percent(None) is None  # type: ignore[arg-type]
    assert parse_percent(30) is None  # type: ignore[arg-type]


@pytest.mark.parametrize("text,expected", [
    ("lên", True), ("xuống", True), ("thêm chút nữa", True), ("bớt", True),
    ("một", False), ("lên 30", False), ("to lên", False), ("", False),
])
def test_direction_words_mean_default_amount(text: str, expected: bool) -> None:
    from voicepilot.intent.amount import is_default_amount

    assert is_default_amount(text) is expected
