"""Run isolated restore/power/prompt acceptance checks without touching live user data."""

import os
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = Path(__file__).resolve().parents[1]
    folder = root / "build" / ("restore-harness-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", VOICEPILOT_CONFIG_DIR=str(folder / "data"),
               PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-m", "pytest", "tests/unit/test_restore_features.py",
                             "-q", "-p", "no:cacheprovider", "--basetemp", str(folder / "pytest")],
                            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    (folder / "report.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    print(result.stdout)
    print(f"Report: {folder / 'report.txt'}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
