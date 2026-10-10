"""Failure detection in the network harness; unit tests never open sockets."""

import contextlib
import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

spec = importlib.util.spec_from_file_location(
    "verify_downloads", Path(__file__).resolve().parents[2] / "tools" / "verify_downloads.py"
)
assert spec is not None and spec.loader is not None
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


@pytest.mark.parametrize("failure", [ConnectionResetError, BrokenPipeError])
def test_windows_tls_rejection_closes_proxy_tunnel_quietly(monkeypatch, failure):
    client, upstream = Mock(), Mock()
    client.recv.side_effect = failure("Windows peer closed the rejected TLS connection")
    monkeypatch.setattr(harness.select, "select", lambda *_: ([client], [], []))
    harness.relay(client, upstream)
    upstream.sendall.assert_not_called()


def test_proxy_forwards_received_bytes_until_eof(monkeypatch):
    client, upstream = Mock(), Mock()
    client.recv.side_effect = [b"encrypted fixture", b""]
    monkeypatch.setattr(harness.select, "select", lambda *_: ([client], [], []))
    harness.relay(client, upstream)
    upstream.sendall.assert_called_once_with(b"encrypted fixture")


def test_network_overrides_do_not_change_parent_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://parent-proxy")
    monkeypatch.setenv("all_proxy", "http://other-proxy")
    environment = harness.child_environment("http://loopback", trust_fixture=True)
    assert environment["HTTPS_PROXY"] == "http://loopback"
    assert "all_proxy" not in environment
    assert environment["NO_PROXY"] == ""
    assert environment["SSL_CERT_FILE"] == str(harness.TLS_FILES / "ca.pem")
    assert harness.os.environ["HTTPS_PROXY"] == "http://parent-proxy"


def test_downloader_exit_zero_does_not_hide_corrupted_file(monkeypatch, tmp_path):
    tunnels = []

    @contextlib.contextmanager
    def fixture():
        yield "http://loopback", tunnels

    def broken_download(command, **kwargs):
        target = Path(command[command.index("--models-dir") + 1]) / "fixture"
        target.mkdir(parents=True, exist_ok=True)
        (target / "fixture.onnx").write_bytes(b"wrong bytes")
        tunnels.append("huggingface.co:443")
        return subprocess.CompletedProcess(command, 0, stdout="[ok]", stderr="")

    monkeypatch.setattr(harness, "loopback_transport", fixture)
    monkeypatch.setattr(harness.subprocess, "run", broken_download)
    checks = harness.verify(tmp_path)
    assert all(check["status"] == "fail" for check in checks)
    assert "Installed bytes differ" in checks[0]["detail"]


def test_network_assertions_remain_active():
    with pytest.raises(AssertionError, match="transport state"):
        harness.require(False, "transport state")


def test_network_report_is_not_overwritten(tmp_path):
    report = tmp_path / "report.json"
    report.write_text("previous evidence", encoding="utf-8")
    with pytest.raises(SystemExit):
        harness.main(["--output", str(tmp_path)])
    assert report.read_text(encoding="utf-8") == "previous evidence"
