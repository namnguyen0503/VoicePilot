"""Hotword phrases for contextual biasing of the Gipformer decoder.

Phrases come from the bundled commands.toml and from the user's alias tables. This module
does not import sherpa-onnx: it only builds the phrase list and writes the two files the
recognizer reads, a hotword file and a BPE vocabulary built from tokens.txt.
"""

from __future__ import annotations

import tomllib
import unicodedata
from collections.abc import Iterable, Mapping
from importlib import resources
from pathlib import Path

COMMANDS_RESOURCE = "commands.toml"
HOTWORDS_FILE = "hotwords.txt"
BPE_VOCAB_FILE = "bpe.vocab"
_PLACEHOLDER_START = "{"
_MIN_LITERAL_WORDS = 2
_OPEN_PREFIX = "mở "


def command_phrases(
    app_aliases: Mapping[str, str], website_aliases: Mapping[str, str], custom_commands: Iterable[dict] = (),
    builtin_patterns: dict[str, list[str]] | None = None,
) -> list[str]:
    """Return the sorted, normalized phrases the recognizer should favour.

    A pattern without a placeholder is kept whole. A pattern with one keeps only the literal
    text before the first placeholder, and only when that text has at least two words, so
    "mở {target}" contributes nothing. Each app and website alias adds "mở <key>" and "<key>".
    """
    phrases: set[str] = set()
    custom_patterns = [
        pattern
        for item in custom_commands
        if item.get("enabled", True)
        for pattern in item.get("patterns", [])
    ]
    if builtin_patterns:
        from voicepilot.builtin_commands import command_table

        patterns = [p for row in command_table(builtin_patterns)["intent"] for p in row.get("patterns", [])]
    else:
        patterns = _command_patterns()
    for pattern in [*patterns, *custom_patterns]:
        if _PLACEHOLDER_START not in pattern:
            _add(phrases, pattern)
            continue
        literal = pattern[: pattern.index(_PLACEHOLDER_START)]
        if len(_normalize(literal).split()) >= _MIN_LITERAL_WORDS:
            _add(phrases, literal)
    for key in [*app_aliases, *website_aliases]:
        _add(phrases, key)
        _add(phrases, _OPEN_PREFIX + key)
    return sorted(phrases)


def write_hotword_files(phrases: Iterable[str], tokens_path: Path, out_dir: Path) -> tuple[Path, Path] | None:
    """Write the hotword file and the BPE vocabulary into out_dir.

    The hotword file holds one uppercase phrase per line, as sherpa-onnx expects plain words.
    The vocabulary has one "<piece>TAB<score>" line per tokens.txt line; the score -i makes
    earlier (lower id) pieces preferred. Lines use LF endings on every platform.

    Returns:
        (hotwords_path, vocab_path), or None when there are no phrases.

    Raises:
        OSError: The files cannot be read or written.
    """
    phrase_list = list(phrases)
    if not phrase_list:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    hotwords_path = out_dir / HOTWORDS_FILE
    vocab_path = out_dir / BPE_VOCAB_FILE
    _write_lines(hotwords_path, [phrase.upper() for phrase in phrase_list])

    vocab_lines: list[str] = []
    for index, line in enumerate(tokens_path.read_text(encoding="utf-8").splitlines()):
        piece = line.rsplit(" ", 1)[0] if line.strip() else ""
        if piece:
            vocab_lines.append(f"{piece}\t{-index}")
    _write_lines(vocab_path, vocab_lines)
    return hotwords_path, vocab_path


def _command_patterns() -> list[str]:
    """Every pattern of every [[intent]] table in the bundled commands.toml."""
    text = resources.files("voicepilot").joinpath("resources", COMMANDS_RESOURCE).read_text(encoding="utf-8")
    patterns: list[str] = []
    for raw in tomllib.loads(text).get("intent", []):
        patterns.extend(str(item) for item in raw.get("patterns", []))
    return patterns


def _normalize(text: str) -> str:
    """NFC-normalize, lowercase and collapse runs of whitespace."""
    return " ".join(unicodedata.normalize("NFC", text).lower().split())


def _add(phrases: set[str], text: str) -> None:
    """Add a normalized phrase unless it is empty."""
    phrase = _normalize(text)
    if phrase:
        phrases.add(phrase)


def _write_lines(path: Path, lines: list[str]) -> None:
    """Write UTF-8 lines with LF endings, so sherpa-onnx never sees a stray carriage return."""
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for line in lines:
            handle.write(line + "\n")
