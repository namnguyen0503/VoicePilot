"""Record an explicitly authorized microphone probe and replay endpoint candidates.

Retains private local WAVs and frame votes; never performs commands or recognition.
The report is evidence of endpoint decisions, not labelled voice accuracy.
"""
from __future__ import annotations

import argparse
import json
import time
import wave
from pathlib import Path

import numpy as np

from voicepilot.audio.capture import convert_to_16k
from voicepilot.audio.neural_vad import load_speech_detector
from voicepilot.audio.vad import FRAME_SAMPLES, Decision, Endpointer, frame_db


def replay(samples: np.ndarray, neural: bool) -> list[dict]:
    results = []
    start_ms = 0
    index = 0
    while index + FRAME_SAMPLES <= len(samples):
        predicate, margin = load_speech_detector() if neural else (None, 8.0)
        endpoint = Endpointer(silence_end_ms=800, max_duration_ms=15000,
                              no_speech_timeout_ms=5000, is_speech=predicate,
                              noise_margin_db=margin, resume_speech_frames=3 if neural else 1)
        decision = Decision.CONTINUE
        levels = []
        while index + FRAME_SAMPLES <= len(samples) and decision is Decision.CONTINUE:
            frame = samples[index:index + FRAME_SAMPLES].astype("<i2").tobytes()
            decision = endpoint.feed(frame)
            levels.append(frame_db(frame))
            index += FRAME_SAMPLES
        results.append(dict(start_ms=start_ms, end_ms=start_ms + endpoint.total_duration_ms,
                            decision=decision.value, speech=endpoint.speech_started,
                            clip_start_ms=start_ms + endpoint.total_duration_ms
                            - len(endpoint.audio()) / 32,
                            trailing_ms=endpoint.trailing_silence_ms, levels_db=levels))
        start_ms += endpoint.total_duration_ms
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reuse", type=Path, help="Replay a retained 16 kHz WAV without recording")
    parser.add_argument("--seconds", type=int, default=20, choices=range(5, 61))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    if args.reuse:
        with wave.open(str(args.reuse), "rb") as source:
            if source.getframerate() != 16000 or source.getnchannels() != 1 or source.getsampwidth() != 2:
                raise ValueError("Expected 16 kHz mono int16 WAV")
            pcm = np.frombuffer(source.readframes(source.getnframes()), dtype="<i2")
    else:
        import winsound

        import sounddevice as sd

        device = sd.query_devices(kind="input")
        rate = round(device["default_samplerate"])
        print(f"Microphone: {device['name']}; recording {args.seconds}s", flush=True)
        # Initialize native detection before the cue so model loading cannot lose onset.
        load_speech_detector()
        recording = sd.rec(args.seconds * rate, samplerate=rate, channels=1, dtype="int16")
        winsound.Beep(900, 100)
        begun = time.monotonic()
        sd.wait()
        print(f"Recorded {time.monotonic() - begun:.1f}s", flush=True)
        pcm = convert_to_16k(recording, rate)
    with wave.open(str(args.output / "microphone.wav"), "wb") as target:
        target.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        target.writeframes(pcm.astype("<i2").tobytes())
    report = {"webrtc": replay(pcm, False), "silero": replay(pcm, True)}
    (args.output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for engine, turns in report.items():
        print(engine, [{k: v for k, v in turn.items() if k != "levels_db"} for turn in turns])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
