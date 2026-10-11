"""Verify automatic endpointing with real WebRTC VAD and synthetic Vietnamese speech.

No microphone recording, recognition, playback, or desktop action is performed.
Reports are retained before and after a change. Synthetic audio is not a voice benchmark.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import uuid
from dataclasses import replace
from pathlib import Path

import numpy as np

from voicepilot.audio.capture import Recorder, convert_to_16k
from voicepilot.audio.vad import FRAME_SAMPLES, Decision, Endpointer
from voicepilot.config import AppConfig
from voicepilot.contracts import Mode
from voicepilot.tts.piper import _load_sherpa_voice

ROOT = Path(__file__).resolve().parents[1]


def evaluate(samples: np.ndarray, *, margin: float | None = 8.0) -> tuple[Decision, Endpointer]:
    ep = Endpointer(silence_end_ms=800, max_duration_ms=15000,
                    no_speech_timeout_ms=5000, noise_margin_db=margin)
    decision = Decision.CONTINUE
    for offset in range(0, len(samples) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        decision = ep.feed(samples[offset:offset + FRAME_SAMPLES].astype("<i2").tobytes())
        if decision is not Decision.CONTINUE:
            break
    return decision, ep


def suite(speech: np.ndarray) -> list[dict]:
    results = []
    rng = np.random.default_rng(42)

    def check(name, fn):
        try:
            detail = fn()
            results.append(dict(name=name, status="pass", detail=detail))
        except Exception as exc:
            results.append(dict(name=name, status="fail", detail=str(exc)))

    def utterance(prefix_ms, gain, noise_rms, paused=False):
        utterance = np.rint(speech * gain).astype(np.int16)
        if paused:
            utterance = np.concatenate((utterance, np.zeros(4800, dtype=np.int16), utterance))
        signal = np.concatenate((np.zeros(prefix_ms * 16), utterance, np.zeros(32000)))
        signal += rng.normal(0, noise_rms, len(signal))
        samples = np.clip(np.rint(signal), -32768, 32767).astype(np.int16)
        end_ms = prefix_ms + len(utterance) / 16
        decision, ep = evaluate(samples)
        if decision is not Decision.END_SILENCE or not end_ms <= ep.total_duration_ms <= end_ms + 1300:
            raise AssertionError(
                f"{decision.value}: ended at {ep.total_duration_ms} ms; speech ends {end_ms:.0f} ms")
        start_ms = ep.total_duration_ms - len(ep.audio()) / 32
        if start_ms > prefix_ms + 30:
            raise AssertionError(
                f"First syllable lost: clip starts at {start_ms:.0f} ms; onset {prefix_ms} ms")
        # Exercise the production recorder with real VAD, including mode and 48 kHz resampling.
        for mode, rate in ((Mode.COMMAND, 16000), (Mode.DICTATION, 48000)):
            pcm = samples if rate == 16000 else np.repeat(samples, 3)
            class Stream:
                def __init__(self, callback, audio):
                    self.callback = callback
                    self.audio = audio
                    self.closed = False
                def start(self):
                    for offset in range(0, len(self.audio), 960):
                        chunk = self.audio[offset:offset + 960].reshape(-1, 1)
                        self.callback(chunk, len(chunk), None, None)
                def stop(self):
                    pass
                def close(self):
                    self.closed = True
            streams = []
            def factory(audio=pcm, created=streams, **kwargs):
                stream = Stream(kwargs["callback"], audio)
                created.append(stream)
                return stream
            cfg = replace(AppConfig(), vad_enabled=True)
            clip = Recorder(cfg, stream_factory=factory, input_rate=rate).record(
                1, mode, threading.Event(), threading.Event())
            expected_s = len(ep.audio()) / 32000
            if (clip is None or not streams[0].closed
                    or abs(clip.duration_s - expected_s) > 0.1):
                duration = "no clip" if clip is None else f"{clip.duration_s:.3f} s"
                raise AssertionError(
                    f"Recorder {mode.value}/{rate}: {duration}; expected {expected_s:.3f} s")
        return f"speech ends {end_ms:.0f} ms; endpoint {ep.total_duration_ms} ms"

    for name, prefix, gain, noise, paused in (
        ("normal", 300, 1.0, 0, False),
        ("immediate", 0, 1.0, 0, False),
        ("quiet-immediate", 0, 0.15, 0, False),
        ("light-noise", 300, 0.7, 120, False),
        ("short-pause", 300, 1.0, 0, True),
    ):
        check(name, lambda p=prefix, g=gain, r=noise, b=paused: utterance(p, g, r, b))

    def silence():
        decision, ep = evaluate(np.zeros(96000, dtype=np.int16))
        if decision is not Decision.END_NO_SPEECH or ep.speech_started:
            raise AssertionError("Silence was treated as speech")
        return f"no-speech timeout {ep.total_duration_ms} ms"
    check("silence", silence)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="endpoint")
    parser.add_argument("--reuse", type=Path, help="Replay synthetic PCM from an earlier report folder")
    args = parser.parse_args()
    folder = Path(os.environ.get("LOCALAPPDATA", str(ROOT / "build"))) / "VoicePilot/verification"
    folder = folder / f"{args.label}-{uuid.uuid4().hex}"
    folder.mkdir(parents=True)
    model = ROOT / "models/tts/vits-piper-vi_VN-vais1000-medium/vi_VN-vais1000-medium.onnx"
    voice = None if args.reuse else _load_sherpa_voice(model)
    results = []
    phrases = ("lưu", "chụp màn hình", "mở youtube", "Xin chào Việt Nam, tôi đang đọc chính tả.")
    for index, phrase in enumerate(phrases):
        if args.reuse:
            speech = np.load(args.reuse / f"synthetic-{index}.npy", allow_pickle=False)
        else:
            audio = voice.generate(phrase, sid=0, speed=1.0)
            speech = convert_to_16k(np.asarray(audio.samples, dtype=np.float32), audio.sample_rate)
            # Remove generated padding so immediate speech really begins at the first frame.
            active = np.flatnonzero(np.abs(speech.astype(np.int32)) > 100)
            speech = speech[active[0]:active[-1] + 1]
        np.save(folder / f"synthetic-{index}.npy", speech, allow_pickle=False)
        checks = suite(speech)
        for check in checks:
            check["name"] = phrase + "/" + check["name"]
        results.extend(checks)
    (folder / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    lines = ["# Endpoint verification", "", "Real WebRTC VAD; synthetic speech; no live microphone.", ""]
    lines += [f"- {r['status']}: {r['name']} — {r['detail']}" for r in results]
    (folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(folder / "report.md")
    for result in results:
        print(json.dumps(result, ensure_ascii=True))
    return int(any(r["status"] != "pass" for r in results))


if __name__ == "__main__":
    raise SystemExit(main())
