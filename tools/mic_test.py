"""Record a few seconds from the microphone and show levels plus what each STT backend hears.

Usage: python tools/mic_test.py [--device N] [--seconds 4] [--list]
Prints transcripts to the console only; nothing is written to the VoicePilot log. The clip is saved
as mic_test.wav in the current folder so it can be played back.
"""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

from voicepilot.audio.capture import convert_to_16k
from voicepilot.contracts import AudioClip
from voicepilot.stt.gipformer import GipformerStt
from voicepilot.stt.google import GoogleStt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", type=int, default=None)
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    import sounddevice as sd

    if args.list:
        for index, dev in enumerate(sd.query_devices()):
            if dev["max_input_channels"] > 0:
                print(index, dev["name"], dev["default_samplerate"])
        return 0
    info = sd.query_devices(args.device, kind="input")
    rate = round(info["default_samplerate"])
    print(f"Device: {info['name']} @ {rate} Hz. Speak after the beep line, recording {args.seconds}s...")
    data = sd.rec(round(args.seconds * rate), samplerate=rate, channels=1, dtype="int16", device=args.device)
    print(">>> NOI BAY GIO <<<", flush=True)
    sd.wait()
    pcm = convert_to_16k(data[:, 0], rate)
    x = pcm.astype(np.float64)
    rms_db = 20 * np.log10(x.std() / 32768 + 1e-9)
    print(f"rms {rms_db:.1f} dBFS, peak {abs(x).max() / 32768:.2f} (1.0 = clipping)")
    with wave.open("mic_test.wav", "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm.tobytes())
    clip = AudioClip(samples=pcm.tobytes(), turn_id=0)
    try:
        print("Google    :", GoogleStt().transcribe(clip).text)
    except Exception as exc:
        print("Google    : FAIL", exc)
    try:
        print("Gipformer :", GipformerStt(Path("gipformer1.5-int8")).transcribe(clip).text)
    except Exception as exc:
        print("Gipformer : FAIL", exc)
    print("Saved", Path("mic_test.wav").resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
