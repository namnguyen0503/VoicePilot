"""Regressions from Windows speech sessions and unfinished v1 phrasing."""

import threading

import pytest

from voicepilot.config import load_config
from voicepilot.contracts import AudioClip, Intent, Mode, SttBackendMode, Transcript
from voicepilot.intent.parser import RuleParser
from voicepilot.stt.gipformer import GipformerStt
from voicepilot.stt.hybrid import HybridTranscriber
from voicepilot.windows.start_menu import match_app


@pytest.mark.parametrize(("text", "intent"), [
    ("chuyển slide tiếp theo", Intent.PPT_NEXT_SLIDE),
    ("chuyển sang slide tiếp theo", Intent.PPT_NEXT_SLIDE),
    ("chuyển tab tiếp theo", Intent.BROWSER_NEXT_TAB),
    ("chuyển sang tab tiếp theo", Intent.BROWSER_NEXT_TAB),
    ("chuyển về tab trước", Intent.BROWSER_PREV_TAB),
])
def test_missing_v1_navigation_phrases(text, intent, tmp_path):
    config = load_config(tmp_path / "config.toml")
    command = RuleParser(config.website_aliases, config.app_aliases).parse(text, None, Mode.COMMAND)
    assert command.intent is intent
    assert command.is_executable


def test_zalo_desktop_can_be_requested_explicitly(tmp_path):
    config = load_config(tmp_path / "config.toml")
    target = "shell:AppsFolder\\com.vng.zalo"
    parser = RuleParser(config.website_aliases, config.app_aliases,
                        app_finder=lambda text: target if text.lower() == "zalo" else None)
    assert parser.parse("mở ứng dụng zalo", None, Mode.COMMAND).params["app"] == target
    assert parser.parse("mở trang zalo", None, Mode.COMMAND).params["url"] == "https://chat.zalo.me"


def test_photoshop_without_publisher_matches_installed_windows_entry():
    # Latest real Windows session: "Mở Photoshop" missed "Adobe Photoshop 2026".
    assert match_app("photoshop", [("Adobe Photoshop 2026", "photoshop.exe")]) == "photoshop.exe"


def test_publisher_match_does_not_make_arbitrary_inner_tokens_launchable():
    assert match_app("code", [("Visual Studio Code", "code.exe")]) is None


def test_gipformer_dictation_clone_is_unbiased_lazy_and_reused(tmp_path):
    biased = GipformerStt(tmp_path, num_threads=3, hotwords=["mở word"])
    plain = biased.without_hotwords()
    assert plain is biased.without_hotwords()
    assert plain._hotwords == ()
    assert plain._recognizer is None
    assert plain._num_threads == 3
    assert biased._hotwords == ("mở word",)


def test_dictation_uses_unbiased_backend_and_keeps_command_backend():
    class Backend:
        name = "gipformer"
        def __init__(self, text):
            self.text, self.calls = text, 0
        def transcribe(self, clip):
            self.calls += 1
            return Transcript(self.text, "gipformer", 1, clip.turn_id)
        def without_hotwords(self):
            return plain
    biased, plain = Backend("mở word"), Backend("nội dung tự do")
    transcriber = HybridTranscriber(None, biased, SttBackendMode.OFFLINE)
    clip = AudioClip(b"\0\0" * 160, 1)
    try:
        selected = transcriber.for_mode(Mode.DICTATION)
        assert selected.transcribe(clip, lambda _: True, threading.Event()).text == "nội dung tự do"
        assert transcriber.for_mode(Mode.COMMAND) is transcriber
        assert transcriber.transcribe(clip, lambda _: True, threading.Event()).text == "mở word"
        assert biased.calls == plain.calls == 1
    finally:
        transcriber.close()
