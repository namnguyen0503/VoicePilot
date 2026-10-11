"""Run blank document/browser acceptance before and after implementation."""

import os
import subprocess
import sys
import uuid
from pathlib import Path


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    root = Path(__file__).resolve().parents[1]
    folder = root / "build" / ("new-documents-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONIOENCODING="utf-8",
               VOICEPILOT_CONFIG_DIR=str(folder / "VoicePilot"))
    result = subprocess.run([sys.executable, "-m", "pytest", "tests/unit/test_new_documents.py",
                             "-q", "-p", "no:cacheprovider", "--basetemp", str(folder / "pytest")],
                            cwd=root, env=env, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    (folder / "report.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
    print(result.stdout[-12000:])
    print(f"Report: {folder / 'report.txt'}")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
