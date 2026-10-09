"""Vietnamese text normalization for matching: lowercase, punctuation, whitespace, diacritics folding.

Two views of the same utterance are produced:

* ``normalize`` keeps Vietnamese diacritics. It is the primary matching view.
* ``fold`` additionally removes diacritics and maps "đ" to "d". It is used as a
  second matching pass so that "slide tiep" still matches "slide tiếp".

Both views keep a span map back to the original string. A captured parameter is
therefore sliced from the original text, so its case, diacritics and inner
punctuation (for example "báo cáo Q3.v2") are preserved.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Normalized:
    """Normalized text plus, for every character, its span in the original string.

    Attributes:
        text: Normalized text: casefolded, punctuation removed, single spaces, no
            leading or trailing space.
        spans: For each character of ``text``, the ``(start, end)`` offsets of the
            original characters it came from. Always the same length as ``text``.
    """

    text: str
    spans: tuple[tuple[int, int], ...]

    def original(self, source: str, start: int, end: int) -> str:
        """Return the original substring that produced ``text[start:end]``.

        Surrounding whitespace and trailing sentence punctuation are never part of
        the result, because those characters are not kept in ``text``. When the slice
        reaches the end of ``text``, a closing bracket or quote that balances an opener
        inside the result is kept, so "Báo cáo (bản cuối)" keeps its ")".
        """
        if start >= end:
            return ""
        begin = self.spans[start][0]
        stop = self.spans[end - 1][1]
        if end == len(self.text):
            stop = _balancing_closers(source, begin, stop)
        return source[begin:stop]


def normalize(source: str) -> Normalized:
    """Normalize text for matching while remembering where each character came from.

    Steps: Unicode NFC per base character cluster, casefold, separator punctuation and
    whitespace replaced by one space, runs of spaces collapsed, edges trimmed.
    Clusters keep their original span, so a decomposed "a" + combining mark is
    handled as one unit.
    """
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    pending_space = False

    for cluster_start, cluster_end in _clusters(source):
        composed = unicodedata.normalize("NFC", source[cluster_start:cluster_end])
        for ch in composed.casefold():
            if _is_separator(ch):
                pending_space = bool(chars)
                continue
            if pending_space:
                chars.append(" ")
                spans.append((cluster_start, cluster_start))
                pending_space = False
            chars.append(ch)
            spans.append((cluster_start, cluster_end))
    return Normalized("".join(chars), tuple(spans))


def fold(norm: Normalized) -> Normalized:
    """Return the diacritic-free view of an already normalized text.

    Combining marks are removed after NFD decomposition and "đ" becomes "d".
    Every output character keeps the span of the input character it came from.
    """
    chars: list[str] = []
    spans: list[tuple[int, int]] = []
    for ch, span in zip(norm.text, norm.spans, strict=True):
        if ch == " ":
            chars.append(" ")
            spans.append(span)
            continue
        if ch == "đ":
            chars.append("d")
            spans.append(span)
            continue
        for part in unicodedata.normalize("NFD", ch):
            if unicodedata.category(part) == "Mn":
                continue
            chars.append(part)
            spans.append(span)
    return Normalized("".join(chars), tuple(spans))


def _clusters(source: str) -> list[tuple[int, int]]:
    """Split source into base character clusters, each with its combining marks."""
    if not source:
        return []
    starts: list[int] = []
    for index, ch in enumerate(source):
        if index == 0 or unicodedata.combining(ch) == 0:
            starts.append(index)
    ends = starts[1:] + [len(source)]
    return list(zip(starts, ends, strict=True))


_CLOSER_OPENERS = {")": "(", "]": "[", "”": "“", "»": "«"}


def _balancing_closers(source: str, begin: int, stop: int) -> int:
    """Return the end index after the closers that directly follow source[begin:stop] and balance it.

    A closer is kept only when the text before it still has an unmatched opener. A
    space or sentence punctuation ends the scan, so "(bản cuối)." keeps ")" and drops ".".
    """
    inside = source[begin:stop]
    owed = {closer: inside.count(opener) - inside.count(closer) for closer, opener in _CLOSER_OPENERS.items()}
    owed['"'] = inside.count('"') % 2
    while stop < len(source):
        ch = source[stop]
        if ch == '"' and owed['"']:
            owed['"'] = 0
        elif ch != '"' and owed.get(ch, 0) > 0:
            owed[ch] -= 1
        else:
            break
        stop += 1
    return stop


def _is_separator(ch: str) -> bool:
    """True for characters that never take part in matching: whitespace, punctuation, symbols, controls."""
    if ch.isspace():
        return True
    category = unicodedata.category(ch)
    return category[0] in "PSCZ"
