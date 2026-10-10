#!/usr/bin/env python3
"""Exercise the model downloader through loopback HTTPS and an authenticated CONNECT proxy.

No public service is contacted and no certificate is installed in the OS trust store.
Fixture bytes test transport, hashes and extraction; they are not speech models.
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import hashlib
import http.client
import http.server
import io
import json
import os
import select
import socket
import socketserver
import ssl
import subprocess
import sys
import tarfile
import threading
import uuid
from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TLS_FILES = ROOT / "tests" / "fixtures" / "tls"
REVISION = "0" * 40
FILE_BYTES = b"VoicePilot loopback download fixture\n"
PROXY_AUTH = "Basic " + base64.b64encode(b"fixture:loopback-only").decode("ascii")


def require(condition: object, message: str) -> None:
    """Keep evidence checks active under python -O."""
    if not condition:
        raise AssertionError(message)


def relay(client: socket.socket, upstream: socket.socket) -> None:
    """Forward a tunnel until EOF; TLS rejection may reset a Windows socket."""
    streams = (client, upstream)
    try:
        while True:
            readable, _, _ = select.select(streams, [], [], 10)
            if not readable:
                return
            for stream in readable:
                data = stream.recv(65536)
                if not data:
                    return
                (upstream if stream is client else client).sendall(data)
    except (ConnectionResetError, BrokenPipeError):
        return


def archive_bytes() -> bytes:
    """Build a minimal voice-shaped archive with a deterministic payload."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as archive:
        for name in ("voice.onnx", "tokens.txt", "espeak-ng-data/phontab"):
            entry = tarfile.TarInfo("fixture/" + name)
            entry.size = len(FILE_BYTES)
            archive.addfile(entry, io.BytesIO(FILE_BYTES))
    return buffer.getvalue()


@contextlib.contextmanager
def loopback_transport() -> Iterator[tuple[str, list[str]]]:
    """Yield the proxy URL and observed tunnels; only loopback sockets are permitted."""
    payload = archive_bytes()
    tunnels: list[str] = []

    class Origin(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/fixture/resolve/"):
                self.send_response(302)
                self.send_header("Location", "https://huggingface.co/fixture-bytes")
                self.end_headers()
                return
            body = payload if self.path == "/fixture/releases/voice.tar.bz2" else FILE_BYTES
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Origin)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(TLS_FILES / "server.pem", TLS_FILES / "server-key.pem")
    origin.socket = context.wrap_socket(origin.socket, server_side=True)

    class Proxy(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(10)
            request = self.rfile.readline(8192).decode("ascii").strip().split()
            headers = http.client.parse_headers(self.rfile)
            allowed = {"huggingface.co:443", "github.com:443"}
            if len(request) != 3 or request[0] != "CONNECT" or request[1] not in allowed:
                self.wfile.write(b"HTTP/1.0 403 Forbidden\r\n\r\n")
                return
            if headers.get("Proxy-Authorization") != PROXY_AUTH:
                self.wfile.write(b"HTTP/1.0 407 Proxy Authentication Required\r\n\r\n")
                return
            tunnels.append(request[1])
            with socket.create_connection(origin.server_address, timeout=10) as upstream:
                self.wfile.write(b"HTTP/1.0 200 Connection established\r\n\r\n")
                self.wfile.flush()
                relay(self.connection, upstream)

    class ProxyServer(socketserver.ThreadingTCPServer):
        daemon_threads = True

    proxy = ProxyServer(("127.0.0.1", 0), Proxy)
    threads = [threading.Thread(target=server.serve_forever, daemon=True)
               for server in (origin, proxy)]
    try:
        for thread in threads:
            thread.start()
        yield f"http://fixture:loopback-only@127.0.0.1:{proxy.server_address[1]}", tunnels
    finally:
        for server in (proxy, origin):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def child_environment(proxy_url: str, *, trust_fixture: bool) -> dict[str, str]:
    """Restrict proxy and CA overrides to the downloader subprocess."""
    environment = {key: value for key, value in os.environ.items()
                   if not key.lower().endswith("_proxy")
                   and key not in {"SSL_CERT_FILE", "SSL_CERT_DIR"}}
    environment.update(HTTPS_PROXY=proxy_url, HTTP_PROXY=proxy_url, NO_PROXY="",
                       SSL_CERT_DIR=str(TLS_FILES / "no-system-cert-directory"),
                       SSL_CERT_FILE=str(TLS_FILES / ("ca.pem" if trust_fixture else "server.pem")),
                       PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return environment


def write_manifest(path: Path, *, archive: bool = False, bad_hash: bool = False) -> None:
    """Write a valid downloader manifest with test URLs covered by the local certificate."""
    digest = "0" * 64 if bad_hash else hashlib.sha256(FILE_BYTES).hexdigest()
    if archive:
        digest = hashlib.sha256(archive_bytes()).hexdigest()
        model = (f'source = "github-release"\n'
                 f'url = "https://github.com/fixture/releases/voice.tar.bz2"\n'
                 f'archive_sha256 = "{digest}"\nextract = "tar.bz2"\n'
                 'files = [{name="voice.onnx"}, {name="tokens.txt"}, '
                 '{name="espeak-ng-data/phontab"}]\n')
    else:
        model = (f'source = "huggingface"\nrepo = "fixture"\nrevision = "{REVISION}"\n'
                 f'files = [{{name="fixture.onnx", sha256="{digest}"}}]\n')
    path.write_text('[[model]]\nid = "loopback-fixture"\ndest = "fixture"\n' + model,
                    encoding="utf-8")


def verify(folder: Path) -> list[dict[str, object]]:
    """Run six observable scenarios without changing real model/configuration files."""
    checks: list[dict[str, object]] = []
    with loopback_transport() as (proxy_url, tunnels):
        def invoke(name, *, archive=False, bad_hash=False, trust=True, force=False,
                   expect_success=True, preserve=None, destination=None, expect_network=True):
            target = destination or folder / name / "models"
            manifest = folder / f"{name}.toml"
            write_manifest(manifest, archive=archive, bad_hash=bad_hash)
            original = target / "fixture" / "fixture.onnx"
            if preserve is not None:
                original.parent.mkdir(parents=True, exist_ok=True)
                original.write_bytes(preserve)
            before = len(tunnels)
            command = [sys.executable, str(ROOT / "tools" / "download_models.py"),
                       "--manifest", str(manifest), "--models-dir", str(target)]
            if force:
                command.append("--force")
            try:
                result = subprocess.run(command, cwd=folder,
                                        env=child_environment(proxy_url, trust_fixture=trust),
                                        capture_output=True, text=True, encoding="utf-8", timeout=25)
                (folder / f"{name}.txt").write_text(result.stdout + result.stderr, encoding="utf-8")
                require(result.returncode == (0 if expect_success else 1), result.stderr)
                count = len(tunnels) - before
                require(bool(count) is expect_network, f"Unexpected number of proxy tunnels: {count}")
                require(not list(target.rglob("*.part")), "Partial download was retained")
                if expect_success:
                    names = ("voice.onnx", "tokens.txt", "espeak-ng-data/phontab") if archive else (
                        "fixture.onnx",)
                    for filename in names:
                        require((target / "fixture" / filename).read_bytes() == FILE_BYTES,
                                "Installed bytes differ from the fixture")
                elif preserve is not None:
                    require(original.read_bytes() == preserve, "Failed download replaced existing file")
                if not trust:
                    require("CERTIFICATE_VERIFY_FAILED" in result.stderr, "Expected certificate failure")
                if bad_hash:
                    require("sha256 mismatch" in result.stderr, "Expected hash mismatch")
                checks.append({"name": name, "status": "pass", "proxy_tunnels": count})
            except (AssertionError, OSError, subprocess.TimeoutExpired) as exc:
                checks.append({"name": name, "status": "fail", "detail": str(exc)})
            return target

        invoke("https_proxy_ca_redirect")
        invoke("https_proxy_archive_extract", archive=True)
        invoke("untrusted_ca_rejected", trust=False, expect_success=False, preserve=b"old model")
        repair = invoke("hash_mismatch_preserves_file", bad_hash=True, expect_success=False,
                        preserve=b"old model")
        invoke("retry_installs_verified_file", destination=repair)
        invoke("verified_file_skips_network", destination=repair, expect_network=False)
    return checks


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="new evidence directory")
    args = parser.parse_args(argv)
    base = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "VoicePilot" / "verification"
    folder = args.output or base / ("downloads-" + datetime.now().strftime("%Y%m%d-%H%M%S-")
                                   + uuid.uuid4().hex[:8])
    if folder.exists():
        parser.error("--output must be a new directory")
    folder.mkdir(parents=True)
    checks = verify(folder)
    report = {"timestamp": datetime.now(UTC).isoformat(), "python": sys.version,
              "scope": "loopback TLS/proxy and fixture bytes; not public model servers", "checks": checks}
    (folder / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for check in checks:
        print(f"{check['status']:5} {check['name']}")
    print(f"Report: {folder / 'report.json'}")
    return int(any(check["status"] != "pass" for check in checks))


if __name__ == "__main__":
    raise SystemExit(main())
