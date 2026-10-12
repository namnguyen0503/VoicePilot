"""Deterministic search over settings items. No Qt dependency, so it is unit-testable.

The corpus is small (about 80 short items), so a plain rule-based ranker is enough:
every query token must prefix-match a token of the item, and the best matching field
(title, keywords, detail) gives the weight. Diacritics are ignored on both sides.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from voicepilot.intent.normalize import fold, normalize

TITLE_WEIGHT = 3
KEYWORD_WEIGHT = 2
DETAIL_WEIGHT = 1
FULL_TITLE_BONUS = 2


@dataclass(frozen=True)
class SearchItem:
    """One searchable settings row. key is a stable id used to navigate to the target."""

    key: str
    tab: str
    title: str
    keywords: tuple[str, ...] = ()
    detail: str = ""


def _fold(text: str) -> str:
    """Casefold, collapse separators and drop diacritics, e.g. "Âm lượng" -> "am luong"."""
    return fold(normalize(text)).text


def _matches(token: str, words: Iterable[str]) -> bool:
    return any(word.startswith(token) for word in words)


def rank(items: Iterable[SearchItem], query: str, limit: int = 20) -> list[SearchItem]:
    """Return the items matching every query token, best first.

    Score per query token is the best weight among the fields that prefix-match it
    (title 3, keywords 2, detail 1). The full query found inside the title adds 2.
    Items with equal score keep their original order. An empty query returns [].
    """
    folded_query = _fold(query)
    tokens = folded_query.split()
    if not tokens:
        return []
    scored: list[tuple[int, int, SearchItem]] = []
    for index, item in enumerate(items):
        title_words = _fold(item.title).split()
        keyword_words = _fold(" ".join(item.keywords)).split()
        detail_words = _fold(item.detail).split()
        score = 0
        for token in tokens:
            if _matches(token, title_words):
                score += TITLE_WEIGHT
            elif _matches(token, keyword_words):
                score += KEYWORD_WEIGHT
            elif _matches(token, detail_words):
                score += DETAIL_WEIGHT
            else:
                break
        else:
            if folded_query in " ".join(title_words):
                score += FULL_TITLE_BONUS
            scored.append((score, index, item))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return [item for _, _, item in scored[:limit]]
