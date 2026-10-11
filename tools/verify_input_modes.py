"""Run the voice/text acceptance harness with no microphone or desktop effects."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return subprocess.call(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_input_modes.py",
            "-q",
            "-p",
            "no:cacheprovider",
            "--basetemp",
            str(ROOT / "build" / ("input-tests-" + uuid.uuid4().hex)),
        ],
        cwd=ROOT,
    )


if __name__ == "__main__":
    raise SystemExit(main())
