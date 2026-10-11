"""Run close-window acceptance before and after implementation."""

import os
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / "build" / ("close-window-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8",
               VOICEPILOT_CONFIG_DIR=str(folder / "VoicePilot"))
    result = subprocess.run([sys.executable, "-m", "pytest", "tests/unit/test_close_window.py",
                             "-q", "-p", "no:cacheprovider", "--basetemp", str(folder / "pytest")],
                            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    (folder / "report.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(result.stdout[-2500:])
    print(f"Report: {folder / 'report.txt'}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
