"""Replay noisy PCM through the production recorder without a microphone or desktop action.

Creates retained before/after reports from identical deterministic noise and saved
Vietnamese TTS PCM. Synthetic acceptance is not a benchmark of the user's voice.
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from dataclasses import replace
from pathlib import Path

import numpy as np

from voicepilot.audio.capture import Recorder
from voicepilot.audio.vad import SAMPLE_RATE
from voicepilot.config import AppConfig
from voicepilot.contracts import Mode

RATE = SAMPLE_RATE


def noisy_cases(speech: np.ndarray) -> list[tuple[str, np.ndarray, float | None, float]]:
    """Return PCM, expected speech end and expected first syllable, in milliseconds."""
    rng = np.random.default_rng(731)
    voice = speech.astype(np.float64)
    voice *= 4000 / max(1, np.sqrt(np.mean(voice * voice)))
    results = []
    for name, prefix, gain, kind, paused in (
        ("clean-immediate", 0, 1, "clean", False),
        ("quiet-immediate", 0, 0.15, "clean", False),
        ("fan-6db-snr", 600, 1, "fan", False),
        ("broadband-6db-snr", 600, 1, "broadband", False),
        ("fan-gets-louder-after-speech", 600, 1, "step", False),
        ("modulated-machinery", 600, 1, "modulated", False),
        ("keyboard-after-speech", 600, 1, "keyboard", False),
        ("pause-then-continue", 600, 1, "fan", True),
        ("fan-0db-snr", 600, 1, "fan-loud", False),
        ("broadband-0db-snr", 600, 1, "broadband-loud", False),
    ):
        utterance = voice * gain
        if paused:
            utterance = np.concatenate((utterance, np.zeros(4800), utterance))
        signal = np.concatenate((np.zeros(prefix * 16), utterance, np.zeros(RATE * 6)))
        t = np.arange(len(signal)) / RATE
        white = rng.normal(0, 1, len(signal))
        fan = np.convolve(white, np.ones(12) / np.sqrt(12), mode="same")
        fan += 0.5 * np.sin(2 * np.pi * 120 * t)
        fan *= 2000 / np.sqrt(np.mean(fan * fan))
        end = prefix * 16 + len(utterance)
        if kind in ("fan", "fan-loud"):
            signal += fan * (2 if kind == "fan-loud" else 1)
        elif kind in ("broadband", "broadband-loud"):
            signal += white * (4000 if kind == "broadband-loud" else 2000)
        elif kind == "step":
            signal += fan * np.where(np.arange(len(signal)) >= end, 2.5, 0.3)
        elif kind == "modulated":
            signal += fan * (1 + 0.8 * np.sin(2 * np.pi * 3 * t))
        elif kind == "keyboard":
            signal += fan * 0.3
            for offset in range(end + 1600, len(signal) - 320, 3200):
                signal[offset:offset + 320] += white[offset:offset + 320] * 7000
        results.append((name, np.clip(np.rint(signal), -32768, 32767).astype("<i2"),
                        end / 16, float(prefix)))
    for kind in ("fan", "broadband"):
        noise = rng.normal(0, 3000, RATE * 6)
        if kind == "fan":
            noise = np.convolve(noise, np.ones(12) / np.sqrt(12), mode="same")
        results.append((kind + "-only", np.rint(noise).astype("<i2"), None, 0))
    return results


def evaluate(samples: np.ndarray, mode: Mode, rate: int) -> dict:
    """Expose consumption while exercising the unchanged production frame pump."""
    class ObservedRecorder(Recorder):
        def _pump(self, chunks, source, endpointer, *args):
            super()._pump(chunks, source, endpointer, *args)
            self.endpoint = endpointer

    stopped = threading.Event()
    audio = samples if rate == RATE else np.repeat(samples, rate // RATE)
    class Stream:
        def start(self):
            for offset in range(0, len(audio), 1021):
                chunk = audio[offset:offset + 1021].reshape(-1, 1)
                self.callback(chunk, len(chunk), None, None)
            # No stall waits when replaying: drain queued PCM using the real stop path.
            stopped.set()
        def stop(self):
            pass
        def close(self):
            self.closed = True
    stream = Stream()
    def factory(**kwargs):
        stream.callback = kwargs["callback"]
        return stream
    recorder = ObservedRecorder(replace(AppConfig(), vad_enabled=True),
                                stream_factory=factory, input_rate=rate)
    started = time.perf_counter()
    clip = recorder.record(1, mode, stopped, threading.Event())
    ep = recorder.endpoint
    return dict(end_ms=ep.total_duration_ms, started=ep.speech_started,
                silence_ms=ep.trailing_silence_ms,
                clip_start_ms=ep.total_duration_ms - (clip.duration_s * 1000 if clip else 0),
                closed=stream.closed, processing_s=time.perf_counter() - started)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--speech-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    results = []
    for index in range(4):
        speech = np.load(args.speech_dir / f"synthetic-{index}.npy", allow_pickle=False)
        for name, pcm, end_ms, onset_ms in noisy_cases(speech):
            np.save(args.output / f"{index}-{name}.npy", pcm, allow_pickle=False)
            for mode, rate in ((Mode.COMMAND, RATE), (Mode.DICTATION, 48000)):
                detail = evaluate(pcm, mode, rate)
                if end_ms is None:
                    ok = not detail["started"] and 5000 <= detail["end_ms"] <= 5100
                else:
                    ok = (end_ms - 60 <= detail["end_ms"] <= end_ms + 1400
                          and detail["silence_ms"] >= 800
                          and detail["clip_start_ms"] <= onset_ms + 100)
                results.append(dict(name=f"{index}/{name}/{mode.value}/{rate}",
                                    status="pass" if ok and detail["closed"] else "fail",
                                    speech_end_ms=end_ms, **detail))
    (args.output / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    failures = [r for r in results if r["status"] == "fail"]
    print(json.dumps(dict(total=len(results), passed=len(results) - len(failures),
                          failures=failures), indent=2))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
