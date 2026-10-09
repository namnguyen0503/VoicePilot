"""The hybrid transcriber logs why a backend failed (fixed texts only, never speech)."""

from __future__ import annotations

import logging
from concurrent.futures import Future

import pytest

from voicepilot.contracts import SttError
from voicepilot.stt.hybrid import _settle


def test_settle_logs_error_type_cause_and_fixed_reason(caplog: pytest.LogCaptureFixture) -> None:
    future: Future = Future()
    error = SttError("Không kết nối được Google.")
    error.__cause__ = TimeoutError()
    future.set_exception(error)
    with caplog.at_level(logging.WARNING):
        assert _settle(future) is None
    assert "SttError" in caplog.text
    assert "cause=TimeoutError" in caplog.text
    assert "Không kết nối được Google." in caplog.text
