"""Bootstrap tests: --version and --selftest without a window, and settings changes that reuse Gipformer."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import replace
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

import voicepilot.audio.capture as capture_module
import voicepilot.stt.gipformer as gipformer_module
import voicepilot.stt.google as google_module
import voicepilot.stt.hybrid as hybrid_module
from voicepilot import app as bootstrap
from voicepilot.config import AppConfig
from voicepilot.contracts import Command, Intent, Mode, SttBackendMode, WindowContext
from voicepilot.paths import MODELS_ENV_VAR


def _fail(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("must not run on this path")


def _manifest_model_files() -> list[str]:
    """Every model file the manifest lists, as paths relative to models/."""
    text = resources.files("voicepilot").joinpath("resources", "models.toml").read_text(encoding="utf-8")
    manifest = tomllib.loads(text)
    return [f"{model['dest']}/{item['name']}" for model in manifest["model"] for item in model["files"]]


def _report(config_home: Path) -> str:
    return (config_home / "logs" / "selftest.txt").read_text(encoding="utf-8")


@pytest.fixture
def models_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A models folder with an empty placeholder for every file in the manifest."""
    home = tmp_path / "models"
    for relative in _manifest_model_files():
        target = home / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
    monkeypatch.setenv(MODELS_ENV_VAR, str(home))
    return home


@pytest.fixture
def no_module_imports(monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave out the import group, so the file and parser checks can be asserted exactly on any OS."""
    monkeypatch.setattr(bootstrap, "_import_targets", lambda: ())


# ----- --version -----------------------------------------------------------------


def test_version_flag_prints_the_installed_version_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bootstrap.metadata, "version", lambda name: "9.8.7")
    monkeypatch.setattr(bootstrap, "setup_logging", _fail)  # no logging and no Qt on this path

    assert bootstrap.main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "VoicePilot 9.8.7"


def test_version_falls_back_when_package_metadata_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing(name: str) -> str:
        raise bootstrap.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(bootstrap.metadata, "version", missing)
    assert bootstrap._app_version() == "0.0.0+dev"


# ----- --selftest ----------------------------------------------------------------


def test_selftest_passes_with_every_model_file_present(
    config_home: Path,
    models_home: Path,
    no_module_imports: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(bootstrap, "setup_logging", _fail)
    monkeypatch.setattr(bootstrap, "VoicePilot", _fail)  # no window, no hotkeys

    assert bootstrap.main(["--selftest"]) == 0

    printed = capsys.readouterr().out.splitlines()
    assert printed[-1] == "SELFTEST OK"
    report = _report(config_home)
    assert report.splitlines() == printed
    assert "[FAIL]" not in report
    for relative in _manifest_model_files():
        assert f"[OK] model file {relative}: present" in report
    assert '[OK] rules: "slide tiếp theo" -> PPT_NEXT_SLIDE, executable=True' in report
    assert '[OK] rules: "tìm kiếm thời tiết Hà Nội" -> BROWSER_SEARCH, executable=True' in report
    assert '[OK] rules: "mở youtube" -> BROWSER_OPEN_SITE, executable=True' in report


def test_selftest_fails_and_names_a_missing_model_file(
    config_home: Path,
    models_home: Path,
    no_module_imports: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = models_home / "asr" / "gipformer1.5-int8" / "encoder.int8.onnx"
    missing.unlink()

    assert bootstrap.main(["--selftest"]) == 1

    printed = capsys.readouterr().out
    assert printed.splitlines()[-1] == "SELFTEST FAILED"
    assert "[FAIL] model file asr/gipformer1.5-int8/encoder.int8.onnx: MISSING" in printed
    assert str(missing) in printed
    assert "[OK] rules:" in printed  # the other checks still run and report
    assert "encoder.int8.onnx" in _report(config_home)


def test_selftest_fails_when_a_required_module_cannot_be_imported(
    config_home: Path,
    models_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(bootstrap, "_import_targets", lambda: ("voicepilot_missing_module",))

    assert bootstrap.main(["--selftest"]) == 1
    assert "[FAIL] import voicepilot_missing_module: ModuleNotFoundError" in capsys.readouterr().out


def test_selftest_import_group_passes_for_an_installed_module(config_home: Path, models_home: Path) -> None:
    assert bootstrap.run_selftest(("json",)) == 0
    assert "[OK] import json" in _report(config_home)


def test_runtime_selftest_is_opt_in(config_home, models_home, no_module_imports, monkeypatch):
    monkeypatch.setattr(bootstrap, "_check_speech_runtime", _fail)
    assert bootstrap.main(["--selftest"]) == 0


def test_runtime_selftest_reports_failure_without_starting_gui(
    config_home, models_home, no_module_imports, monkeypatch,
):
    monkeypatch.setattr(bootstrap, "VoicePilot", _fail)
    monkeypatch.setattr(bootstrap, "_check_speech_runtime",
                        lambda: [(False, "runtime: fake inference failed")])
    assert bootstrap.main(["--selftest-runtime"]) == 1
    assert "[FAIL] runtime: fake inference failed" in _report(config_home)


def test_runtime_selftest_guards_native_load_errors(config_home, models_home, monkeypatch):
    def broken():
        raise RuntimeError("synthetic DLL failure")
    monkeypatch.setattr(bootstrap, "_check_speech_runtime", broken)
    assert bootstrap.run_selftest((), runtime=True) == 1
    assert "[FAIL] runtime: RuntimeError" in _report(config_home)


def test_speech_runtime_observes_command_and_unbiased_dictation(monkeypatch):
    import types

    import voicepilot.tts.piper as piper
    from voicepilot.contracts import Transcript

    calls = []
    class FakeAsr:
        def __init__(self, *args, **kwargs):
            assert kwargs["hotwords"]
        def warmup(self):
            calls.append("load_asr")
        def without_hotwords(self):
            calls.append("unbiased")
            return self
        def transcribe(self, clip):
            assert clip.samples
            text = "chụp màn hình" if clip.turn_id == 0 else "xin chào"
            return Transcript(text, "gipformer", 1, clip.turn_id)
    class FakeVoice:
        def generate(self, text, **kwargs):
            calls.append("synthesize")
            return types.SimpleNamespace(samples=[0.1] * 160, sample_rate=16000)
    def load_voice(path):
        calls.append("load_tts")
        return FakeVoice()
    monkeypatch.setattr(gipformer_module, "GipformerStt", FakeAsr)
    monkeypatch.setattr(piper, "_load_sherpa_voice", load_voice)
    checks = bootstrap._check_speech_runtime()
    assert all(ok for ok, _ in checks)
    assert calls == ["load_asr", "load_tts", "synthesize", "synthesize", "unbiased"]


def test_selftest_fails_when_the_report_folder_is_unavailable(
    config_home: Path,
    models_home: Path,
    no_module_imports: None,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_home.write_text("a file where the config folder should be", encoding="utf-8")

    assert bootstrap.main(["--selftest"]) == 1
    assert "cannot write the selftest report" in capsys.readouterr().out


def test_import_targets_add_pywin32_only_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bootstrap.sys, "platform", "linux")
    assert bootstrap._import_targets() == bootstrap.SELFTEST_IMPORTS

    monkeypatch.setattr(bootstrap.sys, "platform", "win32")
    assert bootstrap._import_targets() == bootstrap.SELFTEST_IMPORTS + bootstrap.SELFTEST_WINDOWS_IMPORTS


# ----- settings change and shutdown -----------------------------------------------


class _FakeGipformer:
    def __init__(self, model_dir: Path, num_threads: int = 2) -> None:
        self.model_dir = model_dir
        self.num_threads = num_threads
        self.warmups = 0

    def warmup(self) -> None:
        self.warmups += 1


class _FakeHybrid:
    def __init__(
        self, google: object, offline: object, mode: SttBackendMode, google_timeout_s: float = 2.0
    ) -> None:
        self.offline = offline
        self.mode = mode
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


class _FakeRecorder:
    def __init__(self, config: AppConfig, on_level: Callable[[float], None] | None = None) -> None:
        self.config = config


class _StubParser:
    def parse(self, text: str, context: WindowContext | None, mode: Mode) -> Command:
        return Command(Intent.UNKNOWN, text)


class _StubSpeaker:
    def speak(self, text: str) -> None:
        return None

    def stop(self) -> None:
        return None


class _Engines:
    """Records every fake speech component the application builds."""

    def __init__(self) -> None:
        self.gipformers: list[_FakeGipformer] = []
        self.hybrids: list[_FakeHybrid] = []


@pytest.fixture
def engines(monkeypatch: pytest.MonkeyPatch) -> _Engines:
    fakes = _Engines()

    def make_gipformer(model_dir: Path, num_threads: int = 2, hotwords: object = ()) -> _FakeGipformer:
        gipformer = _FakeGipformer(model_dir, num_threads)
        fakes.gipformers.append(gipformer)
        return gipformer

    def make_hybrid(
        google: object, offline: object, mode: SttBackendMode, google_timeout_s: float = 2.0
    ) -> _FakeHybrid:
        hybrid = _FakeHybrid(google, offline, mode, google_timeout_s)
        fakes.hybrids.append(hybrid)
        return hybrid

    monkeypatch.setattr(gipformer_module, "GipformerStt", make_gipformer)
    monkeypatch.setattr(google_module, "GoogleStt", lambda: object())
    monkeypatch.setattr(hybrid_module, "HybridTranscriber", make_hybrid)
    monkeypatch.setattr(capture_module, "Recorder", _FakeRecorder)
    monkeypatch.setattr(bootstrap, "_make_parser", lambda config: _StubParser())
    monkeypatch.setattr(bootstrap, "_make_speaker", lambda config: _StubSpeaker())
    return fakes


@pytest.fixture
def make_app(
    qapp: QApplication, config_home: Path, engines: _Engines
) -> Iterator[Callable[[AppConfig], bootstrap.VoicePilot]]:
    built: list[bootstrap.VoicePilot] = []

    def build(config: AppConfig) -> bootstrap.VoicePilot:
        app = bootstrap.VoicePilot(qapp, config, windows=False, icon=QIcon(), instance_server=QLocalServer())
        built.append(app)
        return app

    yield build
    for app in built:
        app.shutdown()


def test_settings_change_reuses_gipformer_and_closes_the_replaced_transcriber(
    make_app: Callable[[AppConfig], bootstrap.VoicePilot], engines: _Engines
) -> None:
    app = make_app(AppConfig())
    first_gipformer, first_hybrid = engines.gipformers[0], engines.hybrids[0]

    app._apply_settings(replace(AppConfig(), tts_enabled=False))

    assert len(engines.gipformers) == 1
    assert engines.hybrids[1].offline is first_gipformer
    assert first_hybrid.closed == 1
    assert engines.hybrids[1].closed == 0


def test_thread_count_change_builds_and_warms_a_new_gipformer(
    make_app: Callable[[AppConfig], bootstrap.VoicePilot], engines: _Engines, qtbot: QtBot
) -> None:
    app = make_app(AppConfig())
    first_hybrid = engines.hybrids[0]

    app._apply_settings(replace(AppConfig(), gipformer_threads=4))

    assert len(engines.gipformers) == 2
    new_gipformer = engines.gipformers[1]
    assert new_gipformer.num_threads == 4
    assert engines.hybrids[1].offline is new_gipformer
    assert first_hybrid.closed == 1
    qtbot.waitUntil(lambda: new_gipformer.warmups == 1, timeout=5000)

    app._apply_settings(replace(AppConfig(), gipformer_threads=4, tts_enabled=False))
    assert len(engines.gipformers) == 2
    assert engines.hybrids[2].offline is new_gipformer


def test_google_only_mode_never_warms_up_gipformer(
    make_app: Callable[[AppConfig], bootstrap.VoicePilot], engines: _Engines
) -> None:
    make_app(AppConfig(stt_mode=SttBackendMode.GOOGLE))
    assert engines.gipformers[0].warmups == 0


def test_shutdown_closes_the_transcriber_once(
    make_app: Callable[[AppConfig], bootstrap.VoicePilot], engines: _Engines
) -> None:
    app = make_app(AppConfig())
    app.shutdown()
    app.shutdown()
    assert engines.hybrids[0].closed == 1


def test_make_parser_reuses_one_installed_app_index(monkeypatch: pytest.MonkeyPatch) -> None:
    from voicepilot.windows import start_menu

    created: list[object] = []

    class FakeIndex:
        def __init__(self) -> None:
            created.append(self)

        def start_refresh(self) -> None:
            pass

        def find(self, spoken: str) -> str | None:
            return "shell:AppsFolder\\Zalo" if spoken.lower() == "zalo" else None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(bootstrap, "_INSTALLED_APPS", None)
    monkeypatch.setattr(start_menu, "InstalledApps", FakeIndex)

    first = bootstrap._make_parser(AppConfig())
    bootstrap._make_parser(AppConfig())
    command = first.parse("mở zalo", None, Mode.COMMAND)

    assert len(created) == 1
    assert command.intent is Intent.WIN_OPEN_APP
    assert command.params["app"] == "shell:AppsFolder\\Zalo"



def test_custom_command_change_rebuilds_hotword_model_and_parser(make_app, engines):
    app = make_app(AppConfig())
    custom = dict(id="test", name="Search", patterns=["câu riêng"], intent="BROWSER_SEARCH",
                  params={"query": "gold"}, enabled=True, context="any", process="")
    app._apply_settings(replace(AppConfig(), custom_commands=[custom]))
    assert len(engines.gipformers) == 2
    assert app._controller._config.custom_commands == [custom]
