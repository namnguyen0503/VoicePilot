"""Index of installed applications, so a spoken app name can launch apps outside the alias table.

The index comes from the shell AppsFolder (which includes Microsoft Store apps) through COM. If
that fails, it falls back to the .lnk shortcuts in the Start Menu folders. The scan runs on a
background daemon thread. find() never waits for a scan: it answers from the current snapshot and
starts a refresh when the snapshot is missing or older than the time-to-live.

The matching (match_app) and the fallback walker (walk_start_menu) are pure and tested with fakes.
Windows-only modules are imported inside the functions that need them.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path, PureWindowsPath

log = logging.getLogger(__name__)

AppEntry = tuple[str, str]  # (display name, launch target)

DEFAULT_TTL_S = 300.0
_MIN_QUERY_CHARS = 3
_APPS_FOLDER_TARGET = "shell:AppsFolder"
_START_MENU_SUBPATH = Path("Microsoft", "Windows", "Start Menu", "Programs")
_SHORTCUT_SUFFIX = ".lnk"
# Entries whose normalized name contains one of these phrases are not launchable apps.
_EXCLUDED_PHRASES = (
    "uninstall",
    "uninstaller",
    "readme",
    "help",
    "manual",
    "documentation",
    "license",
    "website",
    "release notes",
    "go cai dat",  # Vietnamese "uninstall"
)


def normalize(text: str) -> str:
    """Return a comparable form of text: lowercase, no diacritics, only single-space separators.

    Args:
        text: Any display name or spoken phrase.

    Returns:
        The normalized text; empty when the input has no letters or digits.
    """
    text = unicodedata.normalize("NFC", text).lower().replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", text)
    kept = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    chars = [ch if ch.isalnum() else " " for ch in unicodedata.normalize("NFC", kept)]
    return " ".join("".join(chars).split())


def _is_excluded(normalized_name: str) -> bool:
    padded = f" {normalized_name} "
    return any(f" {phrase} " in padded for phrase in _EXCLUDED_PHRASES)


def match_app(spoken: str, entries: Sequence[AppEntry]) -> str | None:
    """Find the launch target for a spoken app name.

    Tried in order: exact normalized name, name equal to the spoken text ignoring all spaces, then
    the spoken tokens as the first tokens of the name. Among prefix matches the shortest name wins.

    Args:
        spoken: The app name as recognized from speech.
        entries: (display name, launch target) pairs from the installed-app index.

    Returns:
        The launch target of the best match, or None when nothing matches.
    """
    query = normalize(spoken)
    if len(query) < _MIN_QUERY_CHARS:
        return None

    candidates: list[tuple[str, str]] = []
    for name, target in entries:
        normalized = normalize(name)
        if normalized and not _is_excluded(normalized):
            candidates.append((normalized, target))

    for normalized, target in candidates:
        if normalized == query:
            return target

    squashed = query.replace(" ", "")
    for normalized, target in candidates:
        if normalized.replace(" ", "") == squashed:
            return target

    query_tokens = query.split()
    prefix_hits: list[tuple[int, str, str]] = []
    for normalized, target in candidates:
        tokens = normalized.split()
        if tokens[: len(query_tokens)] == query_tokens:
            prefix_hits.append((len(normalized), normalized, target))
    if prefix_hits:
        return min(prefix_hits)[2]
    return None


def apps_folder_target(path: str) -> str:
    """Return what os.startfile should open for an AppsFolder item's Path.

    Desktop apps report an absolute file path, which is launched directly. Store apps and apps in
    known folders report an AppUserModelID such as "Spotify.Spotify_x!App", which only resolves
    under shell:AppsFolder.
    """
    if PureWindowsPath(path).is_absolute():
        return path
    return f"{_APPS_FOLDER_TARGET}\\{path}"


def _load_from_appsfolder() -> list[AppEntry]:
    """Read the shell AppsFolder over COM. Windows only; imports are lazy."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("Shell.Application")
        folder = shell.NameSpace("shell:AppsFolder")
        entries: list[AppEntry] = []
        for item in folder.Items():
            name = str(item.Name)
            path = str(item.Path)
            if name and path:
                entries.append((name, apps_folder_target(path)))
        return entries
    finally:
        pythoncom.CoUninitialize()


def start_menu_roots() -> list[Path]:
    """Return the per-user and all-users Start Menu program folders that are set in the environment."""
    roots: list[Path] = []
    for variable in ("ProgramData", "APPDATA"):
        base = os.environ.get(variable)
        if base:
            roots.append(Path(base) / _START_MENU_SUBPATH)
    return roots


def walk_start_menu(roots: Iterable[Path]) -> list[AppEntry]:
    """List the .lnk shortcuts under each root folder; the name is the file stem.

    Args:
        roots: Folders to search recursively. Missing folders are skipped.

    Returns:
        (shortcut stem, shortcut path) pairs.
    """
    entries: list[AppEntry] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for path in root.rglob(f"*{_SHORTCUT_SUFFIX}"):
                if path.is_file():
                    entries.append((path.stem, str(path)))
        except OSError as exc:
            log.warning("Start Menu walk failed (%s)", type(exc).__name__)
    return entries


def load_installed_apps() -> list[AppEntry]:
    """Build the installed-app list: AppsFolder first, Start Menu shortcuts if that fails."""
    try:
        return _load_from_appsfolder()
    except Exception as exc:
        log.warning("AppsFolder scan failed (%s); using Start Menu shortcuts", type(exc).__name__)
        return walk_start_menu(start_menu_roots())


class InstalledApps:
    """Snapshot of installed apps, refreshed in the background and read without blocking.

    Attributes:
        ttl_s: Seconds after which the snapshot is considered stale.
    """

    def __init__(
        self,
        loader: Callable[[], list[AppEntry]] | None = None,
        ttl_s: float = DEFAULT_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an empty index; nothing is scanned until find() or start_refresh() is called.

        Args:
            loader: Function that returns the current installed-app list. Defaults to load_installed_apps.
            ttl_s: Seconds a snapshot stays fresh.
            clock: Monotonic time source, injectable for tests.
        """
        self.ttl_s = ttl_s
        self._loader = loader if loader is not None else load_installed_apps
        self._clock = clock
        self._lock = threading.Lock()
        self._apps: list[AppEntry] = []
        self._loaded_at: float | None = None
        self._thread: threading.Thread | None = None

    def start_refresh(self) -> None:
        """Rebuild the index on a background daemon thread (no-op if one is already running)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._refresh, name="installed-apps", daemon=True)
            self._thread.start()

    def find(self, spoken: str) -> str | None:
        """Return the launch target for a spoken app name, or None.

        Never blocks on a scan. Answers from the current snapshot, and starts a refresh first when the
        snapshot has never been loaded or is older than ttl_s.

        Args:
            spoken: The app name as recognized from speech.

        Returns:
            The launch target, or None when no installed app matches.
        """
        with self._lock:
            apps = self._apps
            loaded_at = self._loaded_at
        if loaded_at is None or self._clock() - loaded_at > self.ttl_s:
            self.start_refresh()
        return match_app(spoken, apps)

    def _refresh(self) -> None:
        try:
            entries: list[AppEntry] | None = list(self._loader())
        except Exception as exc:
            log.warning("Installed-app scan failed (%s); keeping previous index", type(exc).__name__)
            entries = None
        with self._lock:
            if entries is not None:
                self._apps = entries
            # A failed scan also counts as loaded, so retries follow the ttl instead of every find().
            self._loaded_at = self._clock()
        if entries is not None:
            log.info("Indexed %d installed apps", len(entries))
