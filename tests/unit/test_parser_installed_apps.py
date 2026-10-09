"""Unit tests for the installed-app fallback of the rule parser, using a fake app finder."""

from __future__ import annotations

import pytest

from voicepilot.config import AppConfig, load_config
from voicepilot.contracts import Command, Intent, Mode
from voicepilot.intent.parser import RuleParser

SPOTIFY_TARGET = "shell:AppsFolder\\Spotify"


class FakeFinder:
    """Case-insensitive stand-in for InstalledApps.find that records the names it was asked for."""

    def __init__(self, apps: dict[str, str]) -> None:
        self._apps = {name.lower(): target for name, target in apps.items()}
        self.calls: list[str] = []

    def __call__(self, spoken: str) -> str | None:
        self.calls.append(spoken)
        return self._apps.get(spoken.lower())


class RaisingFinder:
    def __call__(self, spoken: str) -> str | None:
        raise RuntimeError(f"search failed for {spoken}")


@pytest.fixture(scope="module")
def config(tmp_path_factory: pytest.TempPathFactory) -> AppConfig:
    """Bundled defaults.toml, loaded with the user config directory pointed at an empty temp dir."""
    home = tmp_path_factory.mktemp("voicepilot_home")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("VOICEPILOT_CONFIG_DIR", str(home))
        return load_config()


def _parser(config: AppConfig, finder: object = None) -> RuleParser:
    return RuleParser(config.website_aliases, config.app_aliases, app_finder=finder)


def _open(parser: RuleParser, text: str) -> Command:
    return parser.parse(text, None, Mode.COMMAND)


def test_open_falls_back_to_installed_app(config: AppConfig) -> None:
    finder = FakeFinder({"spotify": SPOTIFY_TARGET})
    command = _open(_parser(config, finder), "mở spotify")

    assert command.intent is Intent.WIN_OPEN_APP
    assert command.is_executable
    assert command.params == {"app": SPOTIFY_TARGET, "app_name": "spotify"}
    assert finder.calls == ["spotify"]


def test_open_installed_app_is_case_insensitive(config: AppConfig) -> None:
    finder = FakeFinder({"spotify": SPOTIFY_TARGET})
    command = _open(_parser(config, finder), "MỞ SPOTIFY")

    assert command.intent is Intent.WIN_OPEN_APP
    assert command.params == {"app": SPOTIFY_TARGET, "app_name": "SPOTIFY"}
    assert finder.calls == ["SPOTIFY"]


def test_explicit_open_app_rule_uses_installed_app(config: AppConfig) -> None:
    finder = FakeFinder({"spotify": SPOTIFY_TARGET})
    command = _open(_parser(config, finder), "mở ứng dụng spotify")

    assert command.intent is Intent.WIN_OPEN_APP
    assert command.params == {"app": SPOTIFY_TARGET, "app_name": "spotify"}
    assert finder.calls == ["spotify"]


def test_alias_wins_over_installed_app(config: AppConfig) -> None:
    finder = FakeFinder({"word": "C:\\Other\\word.exe"})
    command = _open(_parser(config, finder), "mở word")

    assert command.intent is Intent.WIN_OPEN_APP
    assert command.params == {"app": "WINWORD.EXE", "app_name": "word"}
    assert finder.calls == []


def test_explicit_open_app_alias_wins_over_installed_app(config: AppConfig) -> None:
    finder = FakeFinder({"word": "C:\\Other\\word.exe"})
    command = _open(_parser(config, finder), "mở ứng dụng word")

    assert command.params == {"app": "WINWORD.EXE", "app_name": "word"}
    assert finder.calls == []


def test_website_alias_wins_over_installed_app(config: AppConfig) -> None:
    finder = FakeFinder({"youtube": SPOTIFY_TARGET})
    command = _open(_parser(config, finder), "mở youtube")

    assert command.intent is Intent.BROWSER_OPEN_SITE
    assert command.params["url"] == "https://www.youtube.com"
    assert finder.calls == []


def test_finder_returning_none_is_unknown(config: AppConfig) -> None:
    command = _open(_parser(config, FakeFinder({})), "mở spotify")

    assert command.intent is Intent.UNKNOWN
    assert not command.is_executable


def test_no_finder_is_unknown(config: AppConfig) -> None:
    command = _open(_parser(config), "mở spotify")

    assert command.intent is Intent.UNKNOWN


def test_no_finder_explicit_rule_is_unknown(config: AppConfig) -> None:
    command = _open(_parser(config), "mở ứng dụng spotify")

    assert command.intent is Intent.UNKNOWN


def test_raising_finder_is_unknown(config: AppConfig) -> None:
    command = _open(_parser(config, RaisingFinder()), "mở spotify")

    assert command.intent is Intent.UNKNOWN
    assert not command.is_executable


def test_raising_finder_explicit_rule_is_unknown(config: AppConfig) -> None:
    command = _open(_parser(config, RaisingFinder()), "mở ứng dụng spotify")

    assert command.intent is Intent.UNKNOWN


def test_finder_is_not_used_in_dictation(config: AppConfig) -> None:
    finder = FakeFinder({"spotify": SPOTIFY_TARGET})
    command = _parser(config, finder).parse("mở spotify", None, Mode.DICTATION)

    assert command.intent is Intent.DICTATE_TEXT
    assert finder.calls == []
