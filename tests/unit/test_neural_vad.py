"""Regression for noise that keeps a Windows capture open after the user stops."""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
import pytest

from voicepilot.audio import capture, neural_vad
from voicepilot.audio.neural_vad import CONTEXT_SAMPLES, WINDOW_SHIFT, SileroSpeechDetector
from voicepilot.audio.vad import FRAME_BYTES, FRAME_SAMPLES, Decision, Endpointer
from voicepilot.config import AppConfig
from voicepilot.contracts import Mode


class Model:
    def __init__(self):
        self.windows = []
        self.resets = 0
        self.voiced = False

    def reset(self):
        self.resets += 1

    def is_speech(self, window):
        self.windows.append(window.copy())
        return self.voiced


def test_model_receives_contiguous_normalized_windows_with_context():
    model = Model()
    detector = SileroSpeechDetector(Path("unused"), model_loader=lambda _: model)
    pcm = np.arange(FRAME_SAMPLES * 20, dtype="<i2")
    original = pcm.tobytes()
    for offset in range(0, len(original), FRAME_BYTES):
        detector(original[offset:offset + FRAME_BYTES])
    signal = np.concatenate((np.zeros(CONTEXT_SAMPLES), pcm / 32768.0))
    assert len(model.windows) == len(pcm) // WINDOW_SHIFT
    for index, window in enumerate(model.windows):
        start = index * WINDOW_SHIFT
        assert window.dtype == np.float32
        np.testing.assert_array_equal(window, signal[start:start + 576])
    assert pcm.tobytes() == original


def test_cancelled_turn_state_and_partial_window_are_cleared():
    model = Model()
    detector = SileroSpeechDetector(Path("unused"), model_loader=lambda _: model)
    model.voiced = True
    frame = np.full(FRAME_SAMPLES, 5000, dtype="<i2").tobytes()
    detector(frame)
    assert detector(frame)
    detector.reset()
    model.voiced = False
    assert not detector(bytes(FRAME_BYTES))
    assert not detector(bytes(FRAME_BYTES))
    assert model.resets == 2
    np.testing.assert_array_equal(model.windows[-1], np.zeros(576))


def test_bad_frame_never_reaches_native_model():
    model = Model()
    detector = SileroSpeechDetector(Path("unused"), model_loader=lambda _: model)
    with pytest.raises(ValueError, match="frame must"):
        detector(b"short")
    assert not model.windows


@pytest.mark.parametrize("available", [False, True])
def test_model_load_failure_uses_webrtc_without_logging_paths(tmp_path, monkeypatch, caplog, available):
    monkeypatch.setattr(neural_vad, "models_dir", lambda: tmp_path)
    if available:
        path = tmp_path / neural_vad.MODEL_RELATIVE_PATH
        path.parent.mkdir(parents=True)
        path.write_bytes(b"placeholder")
    def fail(*args, **kwargs):
        raise RuntimeError("private-file-name")
    monkeypatch.setattr(neural_vad, "SileroSpeechDetector", fail)
    def fallback(frame):
        return True
    monkeypatch.setattr(neural_vad, "webrtc_is_speech", lambda: fallback)
    predicate, margin = neural_vad.load_speech_detector()
    assert predicate is fallback and margin == 8
    assert "using WebRTC" in caplog.text
    assert "private-file-name" not in caplog.text and str(tmp_path) not in caplog.text


@pytest.mark.parametrize("mode,rate", [(Mode.COMMAND, 16000), (Mode.DICTATION, 48000)])
def test_noisy_tail_ends_and_quiet_speech_survives_without_rms_gate(monkeypatch, mode, rate):
    # A fake classifier isolates the integration bug: the acoustic background is
    # as loud as the voice, and gets louder after speech; only its speech votes
    # may reset the silence timer. The old RMS gate either rejected voice or hung.
    class Detector:
        def reset(self):
            self.index = 0
        def __call__(self, frame):
            self.index += 1
            return 20 < self.index <= 40
    detector = Detector()
    loads = []
    def load():
        loads.append(True)
        return detector, None
    monkeypatch.setattr(capture, "load_speech_detector", load)
    chunks = np.concatenate((np.full(20 * FRAME_SAMPLES, 2000),
                             np.full(20 * FRAME_SAMPLES, 2100),
                             np.full(50 * FRAME_SAMPLES, 8000))).astype(np.int16)
    chunks = np.repeat(chunks, rate // 16000)
    streams = []
    class Stream:
        def start(self):
            self.callback(chunks.reshape(-1, 1), len(chunks), None, None)
        def stop(self):
            self.stopped = True
        def close(self):
            self.closed = True
    def factory(**kwargs):
        stream = Stream()
        stream.callback = kwargs["callback"]
        streams.append(stream)
        return stream
    recorder = capture.Recorder(AppConfig(), stream_factory=factory, input_rate=rate)
    clips = [recorder.record(i, mode, threading.Event(), threading.Event()) for i in range(2)]
    assert len(loads) == 1
    for clip in clips:
        assert clip is not None and 1.60 <= clip.duration_s <= 1.64
        # Recognition gets the original noisy audio, rather than normalized VAD input.
        assert np.frombuffer(clip.samples, dtype="<i2").max() >= 7900
    assert all(s.closed and s.stopped for s in streams)


def test_vad_disabled_does_not_load_a_model(monkeypatch):
    def forbidden():
        raise AssertionError("VAD must remain disabled")
    monkeypatch.setattr(capture, "load_speech_detector", forbidden)
    stop = threading.Event()
    class Stream:
        def start(self):
            self.callback(np.full((16000, 1), 500, dtype=np.int16), 16000, None, None)
            stop.set()
        def stop(self):
            pass
        def close(self):
            pass
    def factory(**kwargs):
        stream = Stream()
        stream.callback = kwargs["callback"]
        return stream
    recorder = capture.Recorder(AppConfig(vad_enabled=False), stream_factory=factory, input_rate=16000)
    clip = recorder.record(1, Mode.COMMAND, stop, threading.Event())
    assert clip is not None and clip.duration_s >= 0.99


def test_isolated_noise_votes_cannot_keep_resetting_end_of_speech():
    votes = iter([True] * 10 + [False, False, True, True, False] * 15)
    endpoint = Endpointer(silence_end_ms=800, max_duration_ms=15000,
                          no_speech_timeout_ms=5000, is_speech=lambda _: next(votes),
                          resume_speech_frames=3)
    result = Decision.CONTINUE
    while result is Decision.CONTINUE:
        result = endpoint.feed(bytes(FRAME_BYTES))
    assert result is Decision.END_SILENCE
    assert 1110 <= endpoint.total_duration_ms <= 1170


def test_resumed_word_at_silence_boundary_is_confirmed_before_ending():
    votes = iter([True] * 10 + [False] * 26 + [True] * 10 + [False] * 30)
    endpoint = Endpointer(silence_end_ms=800, max_duration_ms=15000,
                          no_speech_timeout_ms=5000, is_speech=lambda _: next(votes),
                          resume_speech_frames=3)
    for _ in range(46):
        assert endpoint.feed(bytes(FRAME_BYTES)) is Decision.CONTINUE
    assert endpoint.trailing_silence_ms == 0
    result = Decision.CONTINUE
    while result is Decision.CONTINUE:
        result = endpoint.feed(bytes(FRAME_BYTES))
    assert result is Decision.END_SILENCE
    assert endpoint.total_duration_ms == 2190


def test_windows_cue_tone_does_not_create_a_recorded_utterance(monkeypatch):
    # The real 20 s Windows probe showed WebRTC treating the short cue as a turn.
    # Reproduce its speech vote with a fake, while the neural classifier rejects it.
    pcm = np.full(16000, 460, dtype=np.int16)
    t = np.arange(1920) / 16000
    pcm[2400:4320] = np.rint(12000 * np.sin(2 * np.pi * 900 * t)).astype(np.int16)
    old = Endpointer(silence_end_ms=800, max_duration_ms=15000, no_speech_timeout_ms=5000,
                     is_speech=lambda _: True, noise_margin_db=8)
    for offset in range(0, len(pcm) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        old.feed(pcm[offset:offset + FRAME_SAMPLES].tobytes())
    assert old.speech_started
    monkeypatch.setattr(capture, "load_speech_detector", lambda: (lambda _: False, None))
    stop = threading.Event()
    class Stream:
        def start(self):
            self.callback(pcm.reshape(-1, 1), len(pcm), None, None)
            stop.set()
        def stop(self):
            pass
        def close(self):
            pass
    def factory(**kwargs):
        stream = Stream()
        stream.callback = kwargs["callback"]
        return stream
    recorder = capture.Recorder(AppConfig(), stream_factory=factory, input_rate=16000)
    assert recorder.record(1, Mode.COMMAND, stop, threading.Event()) is None
