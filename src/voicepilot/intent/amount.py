"""Spoken percentages such as "30", "30%", "mười lăm phần trăm" or "hai mươi mốt".

Parsing runs on the diacritic-free form, so the same code accepts "muoi lam" and "mười lăm".
Only whole numbers 1..100 are accepted; anything else returns None.
"""

from __future__ import annotations

import re

from voicepilot.intent.normalize import fold, normalize

MIN_PERCENT = 1
MAX_PERCENT = 100

_LEADING_WORDS = frozenset({"them", "bot", "khoang", "len", "xuong"})  # thêm, bớt, khoảng, lên, xuống
_TRAILING_PHRASE = ("phan", "tram")  # phần trăm
_TRAILING_WORD = "nua"  # nữa
_UNITS = {
    "mot": 1,  # một, mốt
    "hai": 2,
    "ba": 3,
    "bon": 4,  # bốn
    "tu": 4,  # tư
    "nam": 5,  # năm
    "lam": 5,  # lăm
    "sau": 6,  # sáu
    "bay": 7,  # bảy
    "tam": 8,  # tám
    "chin": 9,  # chín
}
_TENS_WORD = "muoi"  # mười, mươi
_ZERO_WORDS = frozenset({"linh", "le"})  # linh, lẻ
_DIGITS = re.compile(r"\d{1,3}")
# Direction words said without a number ("tăng âm lượng lên") mean the configured default step.
_DEFAULT_WORDS = frozenset({"len", "xuong", "them", "bot", "nua", "chut", "mot", "ti"})


def is_default_amount(text: str) -> bool:
    """True when text only holds direction fillers such as "lên", "xuống", "thêm chút nữa"."""
    if not isinstance(text, str):
        return False
    tokens = fold(normalize(text)).text.split()
    return bool(tokens) and set(tokens) <= _DEFAULT_WORDS and tokens != ["mot"]


def parse_percent(text: str) -> int | None:
    """Return the whole percentage 1..100 spoken in text, or None when it is not one.

    Accepts digits with an optional "%" or "phần trăm", Vietnamese number words, and the
    optional fillers "thêm", "bớt", "khoảng", "lên", "xuống" at the start and "nữa" or "phần trăm"
    at the end.
    """
    if not isinstance(text, str):
        return None
    tokens = fold(normalize(text)).text.split()
    while tokens and tokens[0] in _LEADING_WORDS:
        tokens.pop(0)
    while tokens:
        if tokens[-1] == _TRAILING_WORD:
            tokens.pop()
        elif tuple(tokens[-2:]) == _TRAILING_PHRASE:
            del tokens[-2:]
        else:
            break
    if not tokens:
        return None
    if len(tokens) == 1 and _DIGITS.fullmatch(tokens[0]):
        value = int(tokens[0])
    else:
        value = _words_value(tokens)
    if value is None or not MIN_PERCENT <= value <= MAX_PERCENT:
        return None
    return value


def _words_value(tokens: list[str]) -> int | None:
    """Value of a Vietnamese number of 1..100 given as folded words, or None."""
    if tokens == ["mot", "tram"]:
        return 100
    first = tokens[0]
    if first == _TENS_WORD:
        tens = 1
        rest = tokens[1:]
    elif first in _UNITS:
        digit = _UNITS[first]
        if len(tokens) == 1:
            return digit
        if tokens[1] == _TENS_WORD:
            tens = digit
            rest = tokens[2:]
        elif len(tokens) == 2 and tokens[1] == "lam" and digit >= 2:
            return digit * 10 + 5  # colloquial "hai lăm" = 25
        else:
            return None
    else:
        return None

    if not rest:
        return tens * 10
    if len(rest) > 1 and rest[0] in _ZERO_WORDS:
        rest = rest[1:]
    if len(rest) == 1 and rest[0] in _UNITS:
        return tens * 10 + _UNITS[rest[0]]
    return None
