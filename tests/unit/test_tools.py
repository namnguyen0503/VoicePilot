"""Offline unit tests for tools/download_models.py (no network access)."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import sys
import tarfile
import urllib.error
import urllib.parse
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"
REV = "54fa79b1fb790422fa27b2f1a771161f9521ca48"


def _load_tool(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TOOLS_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dl = _load_tool("download_models")


class FakeResponse:
    """Minimal stand-in for the object urlopen returns."""

    def __init__(self, payload: bytes, headers: dict[str, str] | None = None) -> None:
        self._buffer = io.BytesIO(payload)
        self.headers = headers if headers is not None else {"Content-Length": str(len(payload))}

    def read(self, size: int = -1) -> bytes:
        return self._buffer.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def _manifest_text(**overrides: str) -> str:
    fields = {
        "id": '"m1"',
        "kind": '"asr"',
        "engine": '"sherpa-onnx"',
        "source": '"huggingface"',
        "repo": '"owner/repo"',
        "revision": f'"{REV}"',
        "dest": '"asr/m1"',
    }
    fields.update(overrides)
    body = "\n".join(f"{key} = {value}" for key, value in fields.items())
    return f'[[model]]\n{body}\nfiles = [ {{ name = "a.onnx", sha256 = "TODO" }} ]\n'


def _write_manifest(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "models.toml"
    path.write_text(text, encoding="utf-8")
    return path


# --- URL building -----------------------------------------------------------------------------


def test_build_url_pins_revision_at_repo_root() -> None:
    url = dl.build_url("g-group-ai-lab/gipformer1.5-68M-rnnt", REV, "", "encoder.int8.onnx")
    assert url == (
        "https://huggingface.co/g-group-ai-lab/gipformer1.5-68M-rnnt/resolve/"
        f"{REV}/encoder.int8.onnx"
    )


def test_build_url_keeps_subfolder_slashes_and_quotes_unsafe_characters() -> None:
    url = dl.build_url("rhasspy/piper-voices", REV, "vi/vi_VN/vais1000/medium", "voice file.onnx")
    assert url.endswith(f"/resolve/{REV}/vi/vi_VN/vais1000/medium/voice%20file.onnx")


def test_build_url_quotes_vietnamese_file_names() -> None:
    url = dl.build_url("owner/repo", REV, "", "giọng.json")
    assert url.endswith("/" + urllib.parse.quote("giọng.json", safe=""))
    assert url.endswith("%E1%BB%8Dng.json")


@pytest.mark.parametrize("revision", ["main", "dd9227d", REV.upper(), "x" * 40])
def test_build_url_rejects_branch_names_and_short_hashes(revision: str) -> None:
    with pytest.raises(ValueError):
        dl.build_url("owner/repo", revision, "", "a.onnx")


# --- Hashing and status -----------------------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    payload = b"voicepilot" * 300_000  # spans several read chunks
    target = tmp_path / "blob.bin"
    target.write_bytes(payload)
    assert dl.sha256_file(target) == hashlib.sha256(payload).hexdigest()


def test_hash_status_covers_all_outcomes(tmp_path: Path) -> None:
    target = tmp_path / "a.onnx"
    good = hashlib.sha256(b"abc").hexdigest()
    assert dl.hash_status(target, good) is dl.HashStatus.MISSING
    target.write_bytes(b"abc")
    assert dl.hash_status(target, None) is dl.HashStatus.UNVERIFIED
    assert dl.hash_status(target, good) is dl.HashStatus.MATCH
    assert dl.hash_status(target, "0" * 64) is dl.HashStatus.MISMATCH


@pytest.mark.parametrize(
    ("status", "force", "expected"),
    [
        (dl.HashStatus.MISSING, False, True),
        (dl.HashStatus.MISMATCH, False, True),
        (dl.HashStatus.MATCH, False, False),
        (dl.HashStatus.UNVERIFIED, False, False),
        (dl.HashStatus.MATCH, True, True),
        (dl.HashStatus.UNVERIFIED, True, True),
    ],
)
def test_needs_download_skips_only_present_files(
    status: object, force: bool, expected: bool
) -> None:
    assert dl.needs_download(status, force) is expected  # type: ignore[arg-type]


# --- Manifest parsing -------------------------------------------------------------------------


def test_load_manifest_reads_todo_as_unverified(tmp_path: Path) -> None:
    models = dl.load_manifest(_write_manifest(tmp_path, _manifest_text()))
    assert len(models) == 1
    assert models[0].files[0].sha256 is None
    assert models[0].revision == REV


def test_load_manifest_lowercases_and_checks_sha256(tmp_path: Path) -> None:
    digest = "AB" * 32
    text = _manifest_text().replace('sha256 = "TODO"', f'sha256 = "{digest}"')
    models = dl.load_manifest(_write_manifest(tmp_path, text))
    assert models[0].files[0].sha256 == digest.lower()


@pytest.mark.parametrize(
    "text",
    [
        _manifest_text(revision='"main"'),
        _manifest_text(dest='"../outside"'),
        _manifest_text(dest='"/abs/place"'),
        _manifest_text(repo='""'),
        "# no models here\n",
        '[[model]]\nid = "x"\nrepo = "a/b"\nrevision = "' + REV + '"\ndest = "d"\nfiles = []\n',
        _manifest_text().replace('name = "a.onnx"', 'name = "sub/a.onnx"'),
        _manifest_text().replace('sha256 = "TODO"', 'sha256 = "1234"'),
    ],
)
def test_load_manifest_rejects_invalid_entries(tmp_path: Path, text: str) -> None:
    with pytest.raises(dl.ManifestError):
        dl.load_manifest(_write_manifest(tmp_path, text))


def test_load_manifest_rejects_unreadable_file(tmp_path: Path) -> None:
    with pytest.raises(dl.ManifestError):
        dl.load_manifest(tmp_path / "missing.toml")


def test_shipped_manifest_is_valid_and_pins_both_models() -> None:
    models = {model.id: model for model in dl.load_manifest(dl.DEFAULT_MANIFEST)}
    gipformer = models["gipformer1.5-int8"]
    assert gipformer.repo == "g-group-ai-lab/gipformer1.5-68M-rnnt"
    assert {f.name for f in gipformer.files} == {
        "encoder.int8.onnx",
        "decoder.int8.onnx",
        "joiner.int8.onnx",
        "tokens.txt",
    }
    piper = models["vi_VN-vais1000-medium"]
    assert piper.source == dl.SOURCE_RELEASE
    assert piper.url.endswith("/tts-models/vits-piper-vi_VN-vais1000-medium.tar.bz2")
    assert piper.extract == "tar.bz2"
    assert piper.dest == "tts/vits-piper-vi_VN-vais1000-medium"
    assert [f.name for f in piper.files] == [
        "vi_VN-vais1000-medium.onnx",
        "tokens.txt",
        "espeak-ng-data/phontab",
    ]


# --- Downloading (fake opener) ----------------------------------------------------------------


def test_download_file_writes_target_and_returns_hash(tmp_path: Path) -> None:
    payload = b"model-bytes"
    target = tmp_path / "asr" / "a.onnx"
    seen: list[str] = []

    def opener(url: str, timeout: float) -> FakeResponse:
        seen.append(url)
        return FakeResponse(payload)

    expected = hashlib.sha256(payload).hexdigest()
    digest = dl.download_file("https://example.invalid/a", target, expected, opener=opener)
    assert digest == hashlib.sha256(payload).hexdigest()
    assert target.read_bytes() == payload
    assert not target.with_name("a.onnx.part").exists()
    assert seen == ["https://example.invalid/a"]


def test_download_file_rejects_hash_mismatch_and_cleans_partial(tmp_path: Path) -> None:
    target = tmp_path / "a.onnx"

    def opener(url: str, timeout: float) -> FakeResponse:
        return FakeResponse(b"unexpected")

    with pytest.raises(dl.DownloadError, match="sha256 mismatch"):
        dl.download_file("https://example.invalid/a", target, "0" * 64, opener=opener)
    assert not target.exists()
    assert not target.with_name("a.onnx.part").exists()


def test_download_file_without_expected_hash_accepts_any_content(tmp_path: Path) -> None:
    target = tmp_path / "tokens.txt"
    digest = dl.download_file(
        "https://example.invalid/t", target, None, opener=lambda url, timeout: FakeResponse(b"tok")
    )
    assert digest == hashlib.sha256(b"tok").hexdigest()
    assert target.read_bytes() == b"tok"


def test_download_file_wraps_network_errors(tmp_path: Path) -> None:
    def opener(url: str, timeout: float) -> Any:
        raise urllib.error.URLError("proxy refused")

    with pytest.raises(dl.DownloadError, match="proxy refused"):
        dl.download_file("https://example.invalid/a", tmp_path / "a.onnx", None, opener=opener)
    assert not (tmp_path / "a.onnx").exists()


def test_run_skips_present_files_and_fetches_missing_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    present = b"present"
    models = [
        dl.ModelSpec(
            id="m",
            repo="owner/repo",
            revision=REV,
            path="",
            dest="asr/m",
            files=(
                dl.FileSpec("have.onnx", hashlib.sha256(present).hexdigest()),
                dl.FileSpec("missing.onnx", None),
            ),
        )
    ]
    (tmp_path / "asr" / "m").mkdir(parents=True)
    (tmp_path / "asr" / "m" / "have.onnx").write_bytes(present)
    fetched: list[str] = []

    def fake_download(url: str, target: Path, expected: str | None, **kwargs: Any) -> str:
        fetched.append(target.name)
        target.write_bytes(b"new")
        return hashlib.sha256(b"new").hexdigest()

    monkeypatch.setattr(dl, "download_file", fake_download)
    out, err = io.StringIO(), io.StringIO()
    assert dl.run(models, tmp_path, force=False, out=out, err=err) == dl.EXIT_OK
    assert fetched == ["missing.onnx"]
    assert "[skip] have.onnx" in out.getvalue()
    assert "sha256 not in manifest" in out.getvalue()


def test_run_reports_failures_with_exit_code_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    models = [
        dl.ModelSpec(
            id="m",
            repo="owner/repo",
            revision=REV,
            path="",
            dest="d",
            files=(dl.FileSpec("a.onnx", None),),
        )
    ]

    def failing(url: str, target: Path, expected: str | None, **kwargs: Any) -> str:
        raise dl.DownloadError("boom")

    monkeypatch.setattr(dl, "download_file", failing)
    out, err = io.StringIO(), io.StringIO()
    assert dl.run(models, tmp_path, force=False, out=out, err=err) == dl.EXIT_FAILED
    assert "boom" in err.getvalue()


def test_main_returns_usage_code_for_bad_manifest(tmp_path: Path) -> None:
    manifest = _write_manifest(tmp_path, _manifest_text(revision='"main"'))
    assert dl.main(["--manifest", str(manifest), "--models-dir", str(tmp_path)]) == dl.EXIT_USAGE


# --- GitHub release archives ------------------------------------------------------------------

RELEASE_URL = "https://github.com/owner/repo/releases/download/t/voice.tar.bz2"


def _make_archive(path: Path, members: dict[str, bytes], top: str = "voice") -> bytes:
    """Write a .tar.bz2 with regular files under one top-level folder and return its bytes."""
    with tarfile.open(path, "w:bz2") as tar:
        folder = tarfile.TarInfo(top)
        folder.type = tarfile.DIRTYPE
        tar.addfile(folder)
        for name, payload in members.items():
            info = tarfile.TarInfo(f"{top}/{name}")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return path.read_bytes()


def _release_model(archive: bytes, names: tuple[str, ...] = ("a.onnx", "espeak-ng-data/phontab")) -> Any:
    return dl.ModelSpec(
        id="voice",
        dest="tts/voice",
        source=dl.SOURCE_RELEASE,
        url=RELEASE_URL,
        archive_sha256=hashlib.sha256(archive).hexdigest(),
        extract="tar.bz2",
        files=tuple(dl.FileSpec(name, None) for name in names),
    )


def _archive_bytes(tmp_path: Path, members: dict[str, bytes]) -> bytes:
    return _make_archive(tmp_path / "src.tar.bz2", members)


def test_install_archive_extracts_and_drops_top_folder(tmp_path: Path) -> None:
    archive = _archive_bytes(tmp_path, {"a.onnx": b"model", "espeak-ng-data/phontab": b"ph"})
    models_dir = tmp_path / "models"
    out, err = io.StringIO(), io.StringIO()
    code = dl.run([_release_model(archive)], models_dir, force=False, out=out, err=err,
                  opener=lambda url, timeout: FakeResponse(archive))
    assert code == dl.EXIT_OK
    base = models_dir / "tts" / "voice"
    assert (base / "a.onnx").read_bytes() == b"model"
    assert (base / "espeak-ng-data" / "phontab").read_bytes() == b"ph"
    assert not (base / "voice").exists()
    assert [p.name for p in (models_dir / "tts").iterdir()] == ["voice"]  # no temp folders left behind
    assert "archive sha256 verified" in out.getvalue()


def test_install_archive_skips_when_files_present_unless_forced(tmp_path: Path) -> None:
    archive = _archive_bytes(tmp_path, {"a.onnx": b"new", "espeak-ng-data/phontab": b"ph"})
    base = tmp_path / "models" / "tts" / "voice"
    (base / "espeak-ng-data").mkdir(parents=True)
    (base / "a.onnx").write_bytes(b"old")
    (base / "espeak-ng-data" / "phontab").write_bytes(b"ph")

    def refuse(url: str, timeout: float) -> FakeResponse:
        raise AssertionError("must not download when files are present")

    out, err = io.StringIO(), io.StringIO()
    model = _release_model(archive)
    assert dl.run([model], tmp_path / "models", force=False, out=out, err=err, opener=refuse) == dl.EXIT_OK
    assert "[skip] all files present" in out.getvalue()
    assert (base / "a.onnx").read_bytes() == b"old"

    assert dl.run([model], tmp_path / "models", force=True, out=out, err=err,
                  opener=lambda url, timeout: FakeResponse(archive)) == dl.EXIT_OK
    assert (base / "a.onnx").read_bytes() == b"new"


def test_install_archive_hash_mismatch_keeps_previous_files(tmp_path: Path) -> None:
    good = _archive_bytes(tmp_path, {"a.onnx": b"good", "espeak-ng-data/phontab": b"ph"})
    tampered = _make_archive(tmp_path / "t.tar.bz2", {"a.onnx": b"evil", "espeak-ng-data/phontab": b"ph"})
    models_dir = tmp_path / "models"
    base = models_dir / "tts" / "voice"
    base.mkdir(parents=True)
    (base / "a.onnx").write_bytes(b"previous")
    out, err = io.StringIO(), io.StringIO()
    code = dl.run([_release_model(good)], models_dir, force=True, out=out, err=err,
                  opener=lambda url, timeout: FakeResponse(tampered))
    assert code == dl.EXIT_FAILED
    assert "sha256 mismatch" in err.getvalue()
    assert (base / "a.onnx").read_bytes() == b"previous"
    assert not any(p.name.startswith(".") for p in (models_dir / "tts").iterdir())


def test_install_archive_reports_missing_expected_file(tmp_path: Path) -> None:
    archive = _archive_bytes(tmp_path, {"a.onnx": b"model"})
    models_dir = tmp_path / "models"
    out, err = io.StringIO(), io.StringIO()
    code = dl.run([_release_model(archive)], models_dir, force=False, out=out, err=err,
                  opener=lambda url, timeout: FakeResponse(archive))
    assert code == dl.EXIT_FAILED
    assert "espeak-ng-data/phontab" in err.getvalue()
    assert not (models_dir / "tts" / "voice").exists()


def _tar_with(path: Path, entries: list[tarfile.TarInfo], payloads: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:bz2") as tar:
        for info in entries:
            data = payloads.get(info.name)
            tar.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


@pytest.mark.parametrize(
    "build",
    [
        lambda: [_file_info("/abs/evil.onnx")],
        lambda: [_file_info("voice/../../evil.onnx")],
        lambda: [_file_info("C:/evil.onnx")],
        lambda: [_link_info("voice/link.onnx", "/etc/passwd", tarfile.SYMTYPE)],
        lambda: [_link_info("voice/hard.onnx", "voice/a.onnx", tarfile.LNKTYPE)],
        lambda: [_file_info("loose.onnx")],
        lambda: [_file_info("one/a.onnx"), _file_info("two/b.onnx")],
    ],
)
def test_extract_refuses_unsafe_members(tmp_path: Path, build: Any) -> None:
    archive = _tar_with(tmp_path / "bad.tar.bz2", build(), {})
    target = tmp_path / "out"
    with pytest.raises(dl.ArchiveError):
        dl.extract_tar_bz2(archive, target)
    assert not (tmp_path / "evil.onnx").exists()
    assert not (tmp_path / "abs").exists()


def _file_info(name: str) -> tarfile.TarInfo:
    """A regular, empty file member (no payload is written for it)."""
    return tarfile.TarInfo(name)


def _link_info(name: str, target: str, kind: bytes) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info


def test_manifest_rejects_bad_archive_entries(tmp_path: Path) -> None:
    base = _release_entry()
    bad = [
        base.replace('archive_sha256 = "' + "a" * 64 + '"', 'archive_sha256 = "TODO"'),
        base.replace("https://github.com/", "https://example.invalid/"),
        base.replace('extract = "tar.bz2"', 'extract = "zip"'),
        base.replace('{ name = "a.onnx" }', '{ name = "a.onnx", sha256 = "' + "b" * 64 + '" }'),
        base.replace('{ name = "a.onnx" }', '{ name = "../a.onnx" }'),
        base.replace('source = "github-release"', 'source = "ftp"'),
    ]
    for text in bad:
        with pytest.raises(dl.ManifestError):
            dl.load_manifest(_write_manifest(tmp_path, text))
    models = dl.load_manifest(_write_manifest(tmp_path, base))
    assert models[0].archive_sha256 == "a" * 64


def _release_entry() -> str:
    return (
        '[[model]]\nid = "voice"\nkind = "tts"\nengine = "sherpa-onnx"\nsource = "github-release"\n'
        f'url = "{RELEASE_URL}"\narchive_sha256 = "{"a" * 64}"\nextract = "tar.bz2"\n'
        'dest = "tts/voice"\nfiles = [ { name = "a.onnx" } ]\n'
    )
