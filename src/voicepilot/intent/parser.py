"""Pattern-based IntentParser driven by commands.toml; implements the IntentParser protocol.

Matching runs over two views of the utterance: the diacritic-preserving normalized text
and the diacritic-free form. The folded view is used when it matches a strictly more
specific phrase of the same intent than the plain view does, which lets "tìm kiem giá vàng"
reach "tìm kiếm {query}". Among matching rules the most specific one (the longest literal
text) wins. A tie between different intents is broken by the preferred contexts declared
in commands.toml, and an unresolved tie is UNKNOWN.

Polite fillers are stripped before matching. Free-text captures (query, filename) strip
only polite fillers, so "tìm đường đi" keeps "đi". Other rules also strip the words "đi"
and "với", so "mở youtube đi" and "slide tiếp đi" still work.

Natural question patterns use {question}; they run only after explicit commands
miss and search for the whole question, keeping its interrogative words.

An app name that is not in the [apps] alias table is passed to an optional app_finder
(on Windows: the installed apps from the Start Menu). It runs only after the website and
alias lookups miss, so aliases always win.
"""

from __future__ import annotations

import logging
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from voicepilot.contracts import Command, Intent, Mode, WindowContext
from voicepilot.intent.normalize import Normalized, fold, normalize

log = logging.getLogger(__name__)

COMMANDS_RESOURCE = "commands.toml"
DEFAULT_EXAMPLE = "Slide tiếp theo"
DICTATION_INTENT = Intent.DICTATE_TEXT

_ALLOWED_PLACEHOLDERS = frozenset({"query", "filename", "site", "app_name", "target", "question"})
_FREE_TEXT_PLACEHOLDERS = frozenset({"query", "filename", "question"})
_ALLOWED_CONTEXTS = frozenset({"powerpoint", "browser", "other", "none"})
_ROUTED_PLACEHOLDER = "target"
_CAPTURE = "(.+)"
_PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
_LEADING_FILLERS = (
    "voicepilot ơi",
    "voicepilot",
    "làm ơn",
    "giúp tôi",
    "giúp mình",
    "giùm tôi",
    "giùm mình",
    "giùm",
    "cho tôi",
    "cho mình",
    "hãy",
    "ơi",
)
_POLITE_TRAILING_FILLERS = (
    "voicepilot ơi",
    "làm ơn",
    "giúp tôi",
    "giúp mình",
    "giùm tôi",
    "giùm mình",
    "giùm",
    "nhé",
    "ạ",
    "ơi",
)
_COMMAND_TRAILING_FILLERS = (*_POLITE_TRAILING_FILLERS, "đi", "với")
_FILENAME_SUFFIX_WORDS = ("chấm pptx", "pptx")
_FILE_EXTENSIONS = frozenset(
    {
        "exe",
        "bat",
        "cmd",
        "msi",
        "pptx",
        "ppt",
        "docx",
        "doc",
        "xlsx",
        "xls",
        "pdf",
        "txt",
        "png",
        "jpg",
        "lnk",
    }
)


@dataclass(frozen=True)
class _Entry:
    """One validated [[intent]] table from commands.toml."""

    intent: Intent
    patterns: tuple[str, ...]
    contexts: frozenset[str]
    params: tuple[str, ...]
    example: str | None


@dataclass(frozen=True)
class _Rule:
    """A compiled pattern: one regex for one phrasing of one intent."""

    intent: Intent
    regex: re.Pattern[str]
    captures: tuple[str, ...]
    specificity: int
    contexts: frozenset[str]
    params: tuple[str, ...]
    example: str | None

    @property
    def question(self) -> bool:
        """True for a natural search phrasing that keeps the whole utterance as its query."""
        return "question" in self.captures

    @property
    def free_text(self) -> bool:
        """True when the rule captures free text, which keeps words such as "đi" and "với"."""
        return bool(_FREE_TEXT_PLACEHOLDERS.intersection(self.captures))


@dataclass(frozen=True)
class _View:
    """One matching pass: diacritic or diacritic-free rules with their filler lists."""

    folded: bool
    rules: tuple[_Rule, ...]
    leading: tuple[str, ...]
    polite_trailing: tuple[str, ...]
    command_trailing: tuple[str, ...]


@dataclass(frozen=True)
class _Hit:
    """A rule that matched, with the stripped text its captures are sliced from."""

    rule: _Rule
    match: re.Match[str]
    view: Normalized

    @property
    def specificity(self) -> int:
        return self.rule.specificity

    @property
    def intent(self) -> Intent:
        return self.rule.intent


class _AliasTable:
    """Spoken name -> target lookup, keyed by normalized and folded forms."""

    def __init__(self, aliases: dict[str, str]) -> None:
        self._exact: dict[str, str] = {}
        self._folded: dict[str, str] = {}
        for spoken, target in aliases.items():
            norm = normalize(spoken)
            if norm.text:
                self._exact[norm.text] = target
                self._folded[fold(norm).text] = target

    def lookup(self, norm: Normalized) -> str | None:
        """Return the target for a spoken name, trying the diacritic form first."""
        if not norm.text:
            return None
        return self._exact.get(norm.text) or self._folded.get(fold(norm).text)


class RuleParser:
    """Maps recognized text to a Command using the phrasings in commands.toml."""

    def __init__(
        self,
        website_aliases: dict[str, str],
        app_aliases: dict[str, str],
        commands_path: Path | None = None,
        app_finder: Callable[[str], str | None] | None = None,
        custom_commands: list[dict] | None = None,
        builtin_patterns: dict[str, list[str]] | None = None,
    ) -> None:
        """Load and validate the command table, then compile every pattern once.

        app_finder maps a spoken app name that is not an alias to a launch target, or None.
        It is called with the original spoken text. An exception from it counts as None.

        Raises:
            ValueError: commands.toml is malformed, names an unknown intent or
                context, uses a bad placeholder, or omits a command intent.
        """
        source = str(commands_path) if commands_path is not None else COMMANDS_RESOURCE
        table = _read_commands(commands_path)
        if builtin_patterns:
            if commands_path is not None:
                raise ValueError("Built-in overrides require the bundled command table")
            import tomli_w

            from voicepilot.builtin_commands import command_table

            table = tomli_w.dumps(command_table(builtin_patterns))
        entries, self._dictation_example = _load_entries(table, source)
        self._sites = _AliasTable(website_aliases)
        self._apps = _AliasTable(app_aliases)
        self._app_finder = app_finder
        from voicepilot.custom_commands import CustomMatcher

        self._custom = CustomMatcher(custom_commands or [])
        self._plain = _build_view(entries, folded=False)
        self._folded = _build_view(entries, folded=True)

    def parse(self, text: str, context: WindowContext | None, mode: Mode) -> Command:
        """Return the Command for text. Dictation mode never matches patterns."""
        if mode is Mode.DICTATION:
            return self._dictation(text)
        kind = context.app_kind.value if context is not None else "none"
        base = normalize(text)
        custom = self._custom.match(
            text,
            _strip_fillers(base, self._plain.leading, self._plain.polite_trailing),
            _strip_fillers(fold(base), self._folded.leading, self._folded.polite_trailing),
            context,
        )
        if custom is not None:
            return custom
        hits = _choose_hits(_match(self._plain, base), _match(self._folded, base))
        if not hits:
            hits = _choose_hits(
                _match(self._plain, base, questions=True), _match(self._folded, base, questions=True)
            )
        if not hits:
            return self._unknown(text)
        return self._decide(text, hits, kind)

    def _decide(self, text: str, hits: list[_Hit], kind: str) -> Command:
        """Pick the winning rule among matches: most specific first, then context."""
        best = max(hit.specificity for hit in hits)
        top = [hit for hit in hits if hit.specificity == best]
        if len({hit.intent for hit in top}) > 1:
            fitting = [hit for hit in top if not hit.rule.contexts or kind in hit.rule.contexts]
            if len({hit.intent for hit in fitting}) != 1:
                return self._unknown(text, example=top[0].rule.example)
            top = fitting
        hit = top[0]
        return self._build(text, hit.view, hit.rule, hit.match)

    def _build(self, text: str, view: Normalized, rule: _Rule, match: re.Match[str]) -> Command:
        """Turn one matched rule into a Command, resolving targets and aliases."""
        values: dict[str, Normalized] = {}
        for group, name in enumerate(rule.captures, start=1):
            start, end = match.span(group)
            values[name] = _slice(view, start, end)
        if rule.question:
            values = {"query": view}
        if _ROUTED_PLACEHOLDER in values:
            return self._route_open(text, values[_ROUTED_PLACEHOLDER])
        if "filename" in values:
            values["filename"] = _strip_filename_suffix(values["filename"])

        spoken = {name: _original(text, norm) for name, norm in values.items()}
        params = {name: value for name, value in spoken.items() if value}
        missing = tuple(name for name in rule.params if name not in params)
        if missing:
            return Command(rule.intent, text, params=params, missing=missing, example=rule.example)

        if rule.intent is Intent.BROWSER_OPEN_SITE:
            url = self._resolve_site(values["site"], text)
            if url is None:
                return self._unknown(text, example="Mở YouTube")
            return Command(rule.intent, text, params={"url": url, "site": spoken["site"]})
        if rule.intent is Intent.WIN_OPEN_APP:
            app = self._apps.lookup(values["app_name"]) or self._installed_app(spoken["app_name"])
            if app is None:
                return self._unknown(text, example="Mở Word")
            return Command(rule.intent, text, params={"app": app, "app_name": spoken["app_name"]})
        return Command(rule.intent, text, params=params)

    def _route_open(self, text: str, target: Normalized) -> Command:
        """Resolve a generic "mở {target}": a website, then an app alias, then an installed app."""
        url = self._resolve_site(target, text)
        if url is not None:
            site = _original(text, target)
            return Command(Intent.BROWSER_OPEN_SITE, text, params={"url": url, "site": site})
        app_name = _original(text, target)
        app = self._apps.lookup(target) or self._installed_app(app_name)
        if app is not None:
            return Command(Intent.WIN_OPEN_APP, text, params={"app": app, "app_name": app_name})
        return self._unknown(text, example="Mở YouTube")

    def _installed_app(self, spoken: str) -> str | None:
        """Ask app_finder for a launch target. A finder that raises counts as no match."""
        if self._app_finder is None:
            return None
        try:
            return self._app_finder(spoken) or None
        except Exception as exc:
            log.warning("installed app lookup failed: %s", type(exc).__name__)
            return None

    def _resolve_site(self, spoken_norm: Normalized, text: str) -> str | None:
        """Return the URL for a website alias, an explicit http(s) URL or a bare domain."""
        hit = self._sites.lookup(spoken_norm)
        if hit is not None:
            return hit
        spoken = _original(text, spoken_norm)
        if spoken.lower().startswith(("http://", "https://")):
            scheme, _, address = spoken.partition("://")
            return _web_url(scheme.lower(), address, need_tld=False)
        return _web_url("https", spoken, need_tld=True)

    def _dictation(self, text: str) -> Command:
        """Dictation mode: the whole utterance is text to insert."""
        stripped = text.strip()
        if not stripped:
            return Command(
                DICTATION_INTENT,
                text,
                params={"text": ""},
                missing=("text",),
                example=self._dictation_example,
            )
        return Command(DICTATION_INTENT, text, params={"text": stripped})

    def _unknown(self, text: str, example: str | None = None) -> Command:
        """Not a command: carry an example phrase to suggest."""
        return Command(Intent.UNKNOWN, text, example=example or DEFAULT_EXAMPLE)


def _match(view: _View, base: Normalized, *, questions: bool = False) -> list[_Hit]:
    """Every rule of one view that matches the utterance, each with its own stripped text."""
    source = fold(base) if view.folded else base
    polite = _strip_fillers(source, view.leading, view.polite_trailing)
    full = _strip_fillers(source, view.leading, view.command_trailing)
    hits: list[_Hit] = []
    for rule in view.rules:
        if rule.question != questions:
            continue
        stripped = polite if rule.free_text else full
        if not stripped.text:
            continue
        match = rule.regex.match(stripped.text)
        if match is not None:
            hits.append(_Hit(rule, match, stripped))
    return hits


def _choose_hits(plain: list[_Hit], folded: list[_Hit]) -> list[_Hit]:
    """Prefer the folded matches only when they are strictly more specific for the same intent."""
    if not plain:
        return folded
    if not folded:
        return plain
    top_plain = _top(plain)
    top_folded = _top(folded)
    if top_folded[0].specificity > top_plain[0].specificity and _intents(top_folded) == _intents(top_plain):
        return folded
    return plain


def _top(hits: list[_Hit]) -> list[_Hit]:
    """The hits with the greatest specificity."""
    best = max(hit.specificity for hit in hits)
    return [hit for hit in hits if hit.specificity == best]


def _intents(hits: list[_Hit]) -> set[Intent]:
    return {hit.intent for hit in hits}


def _read_commands(path: Path | None) -> str:
    """Read commands.toml from the given path, or from the package resources by default."""
    if path is not None:
        return path.read_text(encoding="utf-8")
    return resources.files("voicepilot").joinpath("resources", COMMANDS_RESOURCE).read_text(encoding="utf-8")


def _load_entries(text: str, source: str) -> tuple[list[_Entry], str]:
    """Validate the TOML table and return the command entries plus the dictation example."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{source}: {exc}") from exc
    raw_entries = data.get("intent")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError(f"{source}: no [[intent]] tables")

    entries: list[_Entry] = []
    seen: set[Intent] = set()
    dictation_example = DEFAULT_EXAMPLE
    for raw in raw_entries:
        intent = _parse_intent_name(raw.get("name"), source)
        if intent in seen:
            raise ValueError(f"{source}: duplicate intent {intent.value}")
        seen.add(intent)
        patterns = _string_list(raw, "patterns", source)
        contexts = frozenset(_string_list(raw, "context", source))
        params = tuple(_string_list(raw, "params", source))
        example = raw.get("example")
        if example is not None and not isinstance(example, str):
            raise ValueError(f"{source}: example of {intent.value} must be a string")
        if not contexts <= _ALLOWED_CONTEXTS:
            unknown = sorted(contexts - _ALLOWED_CONTEXTS)
            raise ValueError(f"{source}: unknown context in {intent.value}: {unknown}")
        if not set(params) <= _ALLOWED_PLACEHOLDERS - {"question"}:
            raise ValueError(f"{source}: unknown params in {intent.value}: {list(params)}")

        if intent is DICTATION_INTENT:
            if patterns:
                raise ValueError(f"{source}: {intent.value} is mode-driven and takes no patterns")
            dictation_example = example or DEFAULT_EXAMPLE
            continue
        if not patterns:
            raise ValueError(f"{source}: {intent.value} has no patterns")
        entries.append(_Entry(intent, tuple(patterns), contexts, params, example))

    required = {
        intent for intent in Intent if intent not in (Intent.UNKNOWN, DICTATION_INTENT, Intent.WIN_HOTKEY)
    }
    absent = sorted(intent.value for intent in required - seen)
    if absent:
        raise ValueError(f"{source}: missing command intents: {absent}")
    return entries, dictation_example


def _parse_intent_name(name: object, source: str) -> Intent:
    """Map a name from commands.toml to an Intent, rejecting UNKNOWN and unknown names."""
    if not isinstance(name, str) or name not in Intent.__members__:
        raise ValueError(f"{source}: unknown intent {name!r}")
    intent = Intent[name]
    if intent is Intent.UNKNOWN:
        raise ValueError(f"{source}: UNKNOWN cannot be declared in commands.toml")
    return intent


def _string_list(raw: dict[str, object], key: str, source: str) -> list[str]:
    """Read an optional list of strings from a [[intent]] table."""
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{source}: {key} must be a list of strings")
    return list(value)


def _build_view(entries: list[_Entry], folded: bool) -> _View:
    """Compile every pattern of one matching pass."""
    rules = [_compile_rule(entry, pattern, folded) for entry in entries for pattern in entry.patterns]
    return _View(
        folded=folded,
        rules=tuple(rules),
        leading=_fillers(_LEADING_FILLERS, folded),
        polite_trailing=_fillers(_POLITE_TRAILING_FILLERS, folded),
        command_trailing=_fillers(_COMMAND_TRAILING_FILLERS, folded),
    )


def _compile_rule(entry: _Entry, pattern: str, folded: bool) -> _Rule:
    """Compile one phrasing such as "lưu thành {filename}" into an anchored regex."""
    pieces: list[str] = []
    captures: list[str] = []
    literal_len = 0
    for index, part in enumerate(_PLACEHOLDER.split(pattern)):
        if index % 2:
            if part not in _ALLOWED_PLACEHOLDERS or part in captures:
                raise ValueError(f"bad placeholder {{{part}}} in pattern {pattern!r}")
            if pieces and pieces[-1] == _CAPTURE:
                raise ValueError(f"adjacent placeholders in pattern {pattern!r}")
            pieces.append(_CAPTURE)
            captures.append(part)
            continue
        if "{" in part or "}" in part:
            raise ValueError(f"unbalanced braces in pattern {pattern!r}")
        norm = normalize(part)
        literal = fold(norm).text if folded else norm.text
        if literal:
            pieces.append(re.escape(literal))
            literal_len += len(literal)
    if "question" in captures and (entry.intent is not Intent.BROWSER_SEARCH or captures != ["question"]):
        raise ValueError(f"question placeholder is only valid alone in BROWSER_SEARCH: {pattern!r}")
    return _Rule(
        intent=entry.intent,
        regex=re.compile("^" + " ".join(pieces) + "$"),
        captures=tuple(captures),
        specificity=literal_len,
        contexts=entry.contexts,
        params=entry.params,
        example=entry.example,
    )


def _fillers(words: tuple[str, ...], folded: bool) -> tuple[str, ...]:
    """Normalized filler phrases, longest first so that the longest one is stripped."""
    forms = {fold(normalize(word)).text if folded else normalize(word).text for word in words}
    return tuple(sorted(forms, key=len, reverse=True))


_FILENAME_SUFFIXES = tuple(
    sorted(
        {normalize(word).text for word in _FILENAME_SUFFIX_WORDS}
        | {fold(normalize(word)).text for word in _FILENAME_SUFFIX_WORDS},
        key=len,
        reverse=True,
    )
)


def _slice(view: Normalized, start: int, end: int) -> Normalized:
    """Return the part of a normalized text between two indices, keeping span links."""
    if start >= end:
        return Normalized("", ())
    return Normalized(view.text[start:end], view.spans[start:end])


def _strip_fillers(view: Normalized, leading: tuple[str, ...], trailing: tuple[str, ...]) -> Normalized:
    """Remove polite filler words at the start and end of the utterance, repeatedly."""
    text = view.text
    start, end = 0, len(text)
    changed = True
    while changed and start < end:
        changed = False
        current = text[start:end]
        for filler in leading:
            if current == filler:
                start = end
                changed = True
                break
            if current.startswith(filler + " "):
                start += len(filler) + 1
                changed = True
                break
        current = text[start:end]
        for filler in trailing:
            if current == filler:
                end = start
                changed = True
                break
            if current.endswith(" " + filler):
                end -= len(filler) + 1
                changed = True
                break
    return _slice(view, start, end)


def _strip_filename_suffix(norm: Normalized) -> Normalized:
    """Drop a spoken trailing extension such as "chấm pptx" or ".pptx" from a filename."""
    text = norm.text
    for suffix in _FILENAME_SUFFIXES:
        if text == suffix:
            return Normalized("", ())
        if text.endswith(" " + suffix):
            return _slice(norm, 0, len(text) - len(suffix) - 1)
    return norm


def _web_url(scheme: str, address: str, need_tld: bool) -> str | None:
    """Return "scheme://address" if address is a plain host with optional port and path, else None.

    Hosts are labels of letters, digits and hyphens (Unicode letters allowed). A bare domain
    (need_tld) needs at least one dot, an alphabetic TLD of two or more letters, and a TLD
    that is not a common file extension, so "notepad.exe" is not a website. Backslashes,
    whitespace and other schemes are rejected.
    """
    if "\\" in address or any(ch.isspace() for ch in address):
        return None
    host_port, slash, path = address.partition("/")
    host, colon, port = host_port.partition(":")
    if colon and not (port.isascii() and port.isdigit() and len(port) <= 5):
        return None
    labels = host.split(".")
    if not all(_valid_label(label) for label in labels):
        return None
    if need_tld:
        tld = labels[-1]
        if len(labels) < 2 or len(tld) < 2 or not tld.isalpha() or tld.lower() in _FILE_EXTENSIONS:
            return None
    return f"{scheme}://{host.lower()}{colon}{port}{slash}{path}"


def _valid_label(label: str) -> bool:
    """A non-empty host label of letters, digits and hyphens that does not start or end with a hyphen."""
    if not label or label.startswith("-") or label.endswith("-"):
        return False
    return all(ch.isalpha() or ch.isdecimal() or ch == "-" for ch in label)


def _original(source: str, norm: Normalized) -> str:
    """Original substring covered by a normalized text, trimmed of surrounding punctuation."""
    return norm.original(source, 0, len(norm.text))
