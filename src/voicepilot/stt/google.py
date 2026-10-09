"""Google Web Speech backend (SpeechRecognition, vi-VN) implementing SttBackend."""

from __future__ import annotations

import logging
import time

from voicepilot.contracts import SAMPLE_WIDTH_BYTES, AudioClip, SttError, Transcript

log = logging.getLogger(__name__)

GOOGLE_OPERATION_TIMEOUT_S = 10.0  # upper bound on a request; the hybrid applies its own shorter wait


class GoogleStt:
    """Recognizes Vietnamese speech with Google's public web endpoint."""

    name = "google"

    def __init__(self, language: str = "vi-VN") -> None:
        """Create the backend. No network access happens until transcribe()."""
        self._language = language

    def transcribe(self, clip: AudioClip) -> Transcript:
        """Recognize one clip.

        Raises:
            SttError: On network or API failure, no speech, or empty text.
        """
        try:
            import speech_recognition as sr  # heavy dependency, imported lazily
        except ImportError as exc:
            raise SttError("Thiếu thư viện nhận dạng Google.") from exc

        start = time.perf_counter()
        try:
            recognizer = sr.Recognizer()
            recognizer.operation_timeout = GOOGLE_OPERATION_TIMEOUT_S
            audio = sr.AudioData(clip.samples, clip.sample_rate, SAMPLE_WIDTH_BYTES)
            text = recognizer.recognize_google(audio, language=self._language)
        except sr.UnknownValueError as exc:
            raise SttError("Google không nhận ra giọng nói.") from exc
        except sr.RequestError as exc:
            raise SttError("Không kết nối được Google.") from exc
        except Exception as exc:
            raise SttError("Google lỗi khi nhận dạng.") from exc

        text = " ".join(str(text).split())
        latency_ms = (time.perf_counter() - start) * 1000.0
        if not text:
            raise SttError("Google trả về kết quả rỗng.")
        return Transcript(
            text=text,
            source="google",
            latency_ms=latency_ms,
            turn_id=clip.turn_id,
        )
