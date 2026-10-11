"""Windows power round trip and real process factory reset using isolated app data."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from voicepilot.config import AppConfig, default_config, load_config, save_config
from voicepilot.contracts import ResultStatus, SttBackendMode


def child(report: Path, arguments: list[str]) -> int:
    from PySide6.QtCore import QTimer

    from voicepilot import app as bootstrap

    root = Path(os.environ["VOICEPILOT_CONFIG_DIR"])
    bootstrap._single_instance_name = lambda: "VoicePilot.restore.fixture." + report.parent.name
    bootstrap._restart_command = lambda debug: [
        sys.executable, str(Path(__file__).resolve()), "--child", str(report),
        "--wait-for-exit", str(os.getpid()),
    ]
    original_start = bootstrap.VoicePilot.start

    def start(app):
        original_start(app)
        if app._config.custom_commands:
            # Exercise the signal used after the destructive UI confirmation.
            app._open_settings()
            from PySide6.QtWidgets import QTabWidget

            tabs = app._settings_dialog.findChild(QTabWidget)
            for index, name in ((0, "settings"), (1, "prompts"), (3, "restore")):
                tabs.setCurrentIndex(index)
                app._settings_dialog.grab().save(str(report.parent / (name + ".png")))
            QTimer.singleShot(100, app._settings_dialog.factory_reset_requested.emit)
        else:
            config = load_config(root / "config.toml")
            assert config == default_config()
            assert config.builtin_patterns == {} and config.input_policy == "voice"
            assert not (root / "private-fixture.txt").exists()
            assert not (root / "logs" / "private-fixture.log").exists()
            assert not (root / "stt" / "private-fixture.cache").exists()
            report.write_text(json.dumps({"factory_restart": "pass", "pid": os.getpid(),
                                          "custom_removed": True, "default_hotkey": config.hotkey_listen}),
                              encoding="utf-8")
            QTimer.singleShot(800, bootstrap.QApplication.instance().quit)

    bootstrap.VoicePilot.start = start
    return bootstrap.main(arguments)


def verify(folder: Path):
    from voicepilot.windows.system_controls import POWER_SAVER_GUID, NativePowerPlans, PowerPlanManager

    api = NativePowerPlans()
    original = api.get_active()
    baseline = original
    if baseline == POWER_SAVER_GUID:
        baseline = "381b4222-f694-41f0-9685-ff5bb260df2e"
        api.set_active(baseline)
    state = folder / "power-state.json"
    try:
        manager = PowerPlanManager(api=api, state_path=state)
        assert manager.enable().status is ResultStatus.OK
        assert api.get_active() == POWER_SAVER_GUID
        assert manager.enable().status is ResultStatus.OK
        restarted = PowerPlanManager(api=NativePowerPlans(), state_path=state)
        assert restarted.restore().status is ResultStatus.OK
        assert api.get_active() == baseline
        assert restarted.restore().status is ResultStatus.OK
        report = {"power_enable_repeat_restore": "pass", "original_plan": original}
    finally:
        api.set_active(original)
    root = folder / "VoicePilot"
    root.mkdir()
    config = AppConfig(input_policy="text", stt_mode=SttBackendMode.GOOGLE,
                       tts_enabled=False, hotkey_listen="ctrl+alt+f11", hotkey_mode="ctrl+alt+f12",
                       builtin_patterns={"WIN_SCREENSHOT": ["ghi ảnh màn hình"]},
                       custom_commands=[dict(id="fixture", name="Fixture", patterns=["ký tên"],
                                             intent="DICTATE_TEXT", params={"text": "Nam"})])
    save_config(config, root / "config.toml")
    (root / "logs").mkdir()
    (root / "logs" / "private-fixture.log").write_text("synthetic private data")
    (root / "private-fixture.txt").write_text("synthetic private data")
    (root / "stt").mkdir()
    (root / "stt" / "private-fixture.cache").write_text("synthetic private data")
    child_report = folder / "child-report.json"
    env = dict(os.environ, VOICEPILOT_CONFIG_DIR=str(root), QT_QPA_PLATFORM="windows",
               PYTHONIOENCODING="utf-8")
    with (folder / "process-output.txt").open("w", encoding="utf-8") as output:
        command = [sys.executable, str(Path(__file__).resolve()), "--child", str(child_report)]
        first = subprocess.Popen(command, env=env, stdout=output, stderr=output,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            assert first.wait(timeout=30) == 0
            deadline = time.monotonic() + 35
            while not child_report.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            assert child_report.exists(), "Factory child did not restart"
            report.update(json.loads(child_report.read_text(encoding="utf-8")))
            from voicepilot.app import _wait_for_previous_process

            _wait_for_previous_process(report["pid"])
        finally:
            if first.poll() is None:
                first.terminate()
                first.wait(timeout=10)
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", type=Path)
    args, extra = parser.parse_known_args()
    if args.child:
        return child(args.child, extra)
    folder = Path(__file__).resolve().parents[1] / "build" / ("restore-native-" + uuid.uuid4().hex)
    folder.mkdir(parents=True)
    print(f"Evidence: {folder}", flush=True)
    verify(folder)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
