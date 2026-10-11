"""Atomic download of the versioned VAD model; no network or checksums in tests."""
from __future__ import annotations

import io

import pytest

from tools import download_models as dl


class Response(io.BytesIO):
    def __init__(self, data, size=None):
        super().__init__(data)
        self.headers = {} if size is None else {"Content-Length": str(size)}


def test_manifest_includes_offline_vad_and_its_license():
    model = next(m for m in dl.load_manifest(dl.DEFAULT_MANIFEST) if m.id == "silero-vad-v6")
    assert model.dest == "vad/silero-v6"
    assert {f.name for f in model.files} == {"silero_vad.onnx", "LICENSE"}
    assert all("/v6.0/" in f.url for f in model.files)


@pytest.mark.parametrize("size,data", [(10, b"short"), (None, b"")])
def test_failed_raw_download_keeps_previous_model_and_removes_partial(tmp_path, size, data):
    target = tmp_path / "model.onnx"
    target.write_bytes(b"previous")
    with pytest.raises(dl.DownloadError):
        dl.download_raw_file("fixture", target, opener=lambda *a, **kw: Response(data, size))
    assert target.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [target]


def test_raw_download_does_not_invoke_checksum_code(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Raw VAD downloads do not use checksum verification")
    monkeypatch.setattr(dl, "sha256_file", forbidden)
    monkeypatch.setattr(dl.hashlib, "sha256", forbidden)
    target = tmp_path / "model.onnx"
    dl.download_raw_file("fixture", target, opener=lambda *a, **kw: Response(b"new model", 9))
    assert target.read_bytes() == b"new model"


def test_raw_model_run_installs_then_skips_existing_files(tmp_path):
    file = dl.FileSpec(name="model.onnx", sha256=None, url="fixture")
    model = dl.ModelSpec(id="fixture", dest="vad", source=dl.SOURCE_RAW, files=(file,))
    calls = []
    def opener(url, **kwargs):
        calls.append(url)
        return Response(b"model")
    for _ in range(2):
        assert dl.run([model], tmp_path, force=False, out=io.StringIO(), err=io.StringIO(),
                      opener=opener) == dl.EXIT_OK
    assert calls == ["fixture"]
