#!/usr/bin/env python3
"""Download the speech models listed in the VoicePilot manifest.

Reads src/voicepilot/resources/models.toml and installs each entry into <models-dir>/<dest>/.

Two sources are supported:
- "huggingface": each file is downloaded from https://huggingface.co/<repo>/resolve/<revision>/<path>/<name>.
  A file whose sha256 is set in the manifest is verified; a file that already exists with the
  expected hash is skipped unless --force is given. Entries whose sha256 is "TODO" are not
  verified: the computed hash is printed so it can be copied into the manifest.
- "github-release": one .tar.bz2 archive is downloaded from a GitHub release, verified against
  archive_sha256, and extracted into a temporary folder. Its single top-level folder is dropped,
  and the folder is moved into place only when every expected file is present. Links, absolute
  paths and ".." segments in the archive are refused.

Standard library only. urllib honours the HTTPS_PROXY environment variable.

Exit codes: 0 all files present, 1 a download, extraction or hash failure, 2 bad manifest or arguments.
"""

from __future__ import annotations

import argparse
import dataclasses
import enum
import hashlib
import os
import re
import shutil
import sys
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import IO, Any

HF_BASE_URL = "https://huggingface.co"
SOURCE_HF = "huggingface"
SOURCE_RELEASE = "github-release"
RELEASE_URL_PREFIX = "https://github.com/"
EXTRACT_TAR_BZ2 = "tar.bz2"
TODO_MARKER = "TODO"
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DRIVE_RE = re.compile(r"^[A-Za-z]:")
CHUNK_SIZE = 1 << 20
TIMEOUT_S = 60
PROGRESS_STEP_BYTES = 10 << 20
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "src" / "voicepilot" / "resources" / "models.toml"
MODELS_ENV_VAR = "VOICEPILOT_MODELS_DIR"


class ManifestError(ValueError):
    """The manifest is unreadable or contains a missing, unsafe or malformed value."""


class DownloadError(RuntimeError):
    """A file could not be downloaded or did not match its expected sha256."""


class ArchiveError(RuntimeError):
    """An archive could not be extracted safely or lacks an expected file."""


class HashStatus(enum.Enum):
    """Result of comparing a local file with the manifest."""

    MISSING = "missing"
    UNVERIFIED = "unverified"  # the manifest has no sha256 yet
    MATCH = "match"
    MISMATCH = "mismatch"


@dataclasses.dataclass(frozen=True)
class FileSpec:
    """One file of a model. sha256 is None when the manifest says TODO or when an archive covers it."""

    name: str
    sha256: str | None


@dataclasses.dataclass(frozen=True)
class ModelSpec:
    """One [[model]] entry of the manifest.

    Hugging Face entries use repo, revision and path. GitHub release entries use url,
    archive_sha256 and extract; their files are the paths expected after extraction.
    """

    id: str
    dest: str
    files: tuple[FileSpec, ...]
    source: str = SOURCE_HF
    repo: str = ""
    revision: str = ""
    path: str = ""
    url: str = ""
    archive_sha256: str = ""
    extract: str = ""


def load_manifest(path: Path) -> list[ModelSpec]:
    """Parse and validate every [[model]] entry in the manifest at path."""
    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ManifestError(f"cannot read manifest {path}: {exc}") from exc
    entries = data.get("model")
    if not isinstance(entries, list) or not entries:
        raise ManifestError("manifest has no [[model]] entries")
    return [_parse_model(entry) for entry in entries]


def _parse_model(entry: dict[str, Any]) -> ModelSpec:
    """Validate one model table and convert it to ModelSpec."""
    model_id = _require_str(entry, "id")
    dest = _require_str(entry, "dest")
    _check_relative(dest, f"{model_id}: dest", allow_empty=False)
    source = _require_str(entry, "source")
    raw_files = entry.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise ManifestError(f"{model_id}: files must be a non-empty list")
    if source == SOURCE_HF:
        return _parse_huggingface(entry, model_id, dest, raw_files)
    if source == SOURCE_RELEASE:
        return _parse_release(entry, model_id, dest, raw_files)
    raise ManifestError(f"{model_id}: source must be {SOURCE_HF!r} or {SOURCE_RELEASE!r}, not {source!r}")


def _parse_huggingface(entry: dict[str, Any], model_id: str, dest: str, raw_files: list[Any]) -> ModelSpec:
    revision = _require_str(entry, "revision")
    if not REVISION_RE.fullmatch(revision):
        raise ManifestError(f"{model_id}: revision must be a full 40-character commit hash")
    path = str(entry.get("path", ""))
    _check_relative(path, f"{model_id}: path", allow_empty=True)
    return ModelSpec(
        id=model_id,
        source=SOURCE_HF,
        repo=_require_str(entry, "repo"),
        revision=revision,
        path=path,
        dest=dest,
        files=tuple(_parse_file(model_id, item) for item in raw_files),
    )


def _parse_release(entry: dict[str, Any], model_id: str, dest: str, raw_files: list[Any]) -> ModelSpec:
    url = _require_str(entry, "url")
    if not url.startswith(RELEASE_URL_PREFIX):
        raise ManifestError(f"{model_id}: url must start with {RELEASE_URL_PREFIX}")
    archive_sha256 = _require_str(entry, "archive_sha256").lower()
    if not SHA256_RE.fullmatch(archive_sha256):
        raise ManifestError(f"{model_id}: archive_sha256 must be 64 hex characters (no TODO for archives)")
    extract = _require_str(entry, "extract")
    if extract != EXTRACT_TAR_BZ2:
        raise ManifestError(f"{model_id}: extract must be {EXTRACT_TAR_BZ2!r}, not {extract!r}")
    files = tuple(_parse_archive_file(model_id, item) for item in raw_files)
    return ModelSpec(
        id=model_id,
        source=SOURCE_RELEASE,
        url=url,
        archive_sha256=archive_sha256,
        extract=extract,
        dest=dest,
        files=files,
    )


def _parse_file(model_id: str, item: Any) -> FileSpec:
    """Validate one Hugging Face { name, sha256 } table."""
    if not isinstance(item, dict):
        raise ManifestError(f"{model_id}: each file must be a table with name and sha256")
    name = _require_str(item, "name")
    if "/" in name or "\\" in name or name in (".", ".."):
        raise ManifestError(f"{model_id}: file name must be a plain file name: {name!r}")
    raw_hash = _require_str(item, "sha256")
    if raw_hash == TODO_MARKER:
        return FileSpec(name=name, sha256=None)
    digest = raw_hash.lower()
    if not SHA256_RE.fullmatch(digest):
        raise ManifestError(f"{model_id}: sha256 for {name} must be 64 hex characters or TODO")
    return FileSpec(name=name, sha256=digest)


def _parse_archive_file(model_id: str, item: Any) -> FileSpec:
    """Validate one expected path inside an archive model: { name } only, relative to dest."""
    if not isinstance(item, dict):
        raise ManifestError(f"{model_id}: each file must be a table with name")
    if "sha256" in item:
        raise ManifestError(f"{model_id}: archive files take no sha256; archive_sha256 covers them")
    name = _require_str(item, "name")
    _check_relative(name, f"{model_id}: file name", allow_empty=False)
    return FileSpec(name=name, sha256=None)


def _require_str(table: dict[str, Any], key: str) -> str:
    """Return a non-empty string value or raise ManifestError."""
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"missing or empty required key: {key!r} in {table.get('id', '?')}")
    return value


def _check_relative(value: str, label: str, *, allow_empty: bool) -> None:
    """Reject absolute paths and parent-directory segments so downloads stay inside models/."""
    if not value:
        if allow_empty:
            return
        raise ManifestError(f"{label} must not be empty")
    pure = Path(value.replace("\\", "/"))
    if pure.is_absolute() or value.startswith("/") or ".." in pure.parts:
        raise ManifestError(f"{label} must be a relative path inside the models folder: {value!r}")


def build_url(repo: str, revision: str, path: str, name: str) -> str:
    """Return the Hugging Face resolve URL for one file at a pinned revision."""
    if not REVISION_RE.fullmatch(revision):
        raise ValueError(f"revision must be a full 40-character commit hash: {revision!r}")
    repo_part = "/".join(urllib.parse.quote(seg, safe="") for seg in repo.split("/"))
    segments = [*path.split("/"), name] if path else [name]
    file_part = "/".join(urllib.parse.quote(seg, safe="") for seg in segments)
    return f"{HF_BASE_URL}/{repo_part}/resolve/{revision}/{file_part}"


def sha256_file(path: Path) -> str:
    """Return the lowercase hex sha256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_status(path: Path, expected: str | None) -> HashStatus:
    """Compare a local file with the expected sha256 (None means not verified)."""
    if not path.is_file():
        return HashStatus.MISSING
    if expected is None:
        return HashStatus.UNVERIFIED
    return HashStatus.MATCH if sha256_file(path) == expected else HashStatus.MISMATCH


def needs_download(status: HashStatus, force: bool) -> bool:
    """Decide whether a file must be fetched.

    Missing and mismatched files are fetched; matching or unverified files are kept.
    --force always fetches.
    """
    if force:
        return True
    return status in (HashStatus.MISSING, HashStatus.MISMATCH)


def download_file(
    url: str,
    target: Path,
    expected: str | None,
    *,
    opener: Callable[..., Any] = urllib.request.urlopen,
    progress: Callable[[int, int | None], None] | None = None,
) -> str:
    """Stream url into target through a .part file and return the computed sha256.

    The file replaces target only after it completes and, when expected is set, matches it.
    Raises DownloadError on network failure or a hash mismatch; the partial file is removed.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    try:
        with opener(url, timeout=TIMEOUT_S) as response, partial.open("wb") as out:
            total = _content_length(response)
            done = 0
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except (urllib.error.URLError, OSError) as exc:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"{target.name}: {exc}") from exc
    actual = digest.hexdigest()
    if expected is not None and actual != expected:
        partial.unlink(missing_ok=True)
        raise DownloadError(f"sha256 mismatch for {target.name}: expected {expected}, got {actual}")
    os.replace(partial, target)
    return actual


def extract_tar_bz2(archive: Path, target: Path) -> None:
    """Extract a .tar.bz2 whose members all sit under one top-level folder, which is dropped.

    Only regular files and folders are accepted. Absolute paths, drive letters, ".." segments,
    links and special files are refused before anything is written. target must not exist yet.
    """
    try:
        with tarfile.open(archive, "r:bz2") as tar:
            members = tar.getmembers()
            plan = _plan_extraction(members, target)
            target.mkdir(parents=True)
            for member, dest in plan:
                if dest is None:
                    continue
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                    continue
                source = tar.extractfile(member)
                if source is None:
                    raise ArchiveError(f"cannot read archive member {member.name!r}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with source, dest.open("wb") as out:
                    shutil.copyfileobj(source, out)
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise ArchiveError(f"cannot extract {archive.name}: {exc}") from exc


def _plan_extraction(
    members: Sequence[tarfile.TarInfo], target: Path
) -> list[tuple[tarfile.TarInfo, Path | None]]:
    """Check every member and return (member, destination) pairs; destination None is the top folder."""
    tops: set[str] = set()
    plan: list[tuple[tarfile.TarInfo, Path | None]] = []
    for member in members:
        name = member.name.replace("\\", "/")
        if name.startswith("/") or DRIVE_RE.match(name):
            raise ArchiveError(f"absolute path in archive: {member.name!r}")
        parts = [part for part in PurePosixPath(name).parts if part != "."]
        if ".." in parts:
            raise ArchiveError(f"parent reference in archive: {member.name!r}")
        if not (member.isfile() or member.isdir()):
            raise ArchiveError(f"link or special file in archive: {member.name!r}")
        if len(parts) <= 1:
            if member.isdir():
                plan.append((member, None))
                continue
            raise ArchiveError(f"file outside the top-level folder: {member.name!r}")
        tops.add(parts[0])
        plan.append((member, target.joinpath(*parts[1:])))
    if len(tops) != 1:
        raise ArchiveError(f"archive must have exactly one top-level folder, found {sorted(tops)}")
    return plan


def _content_length(response: Any) -> int | None:
    """Return the Content-Length header as an int, or None when absent or malformed."""
    headers = getattr(response, "headers", None)
    raw = headers.get("Content-Length") if headers is not None else None
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _progress_printer(out: IO[str]) -> Callable[[int, int | None], None]:
    """Return a callback that prints progress every PROGRESS_STEP_BYTES (or each 10 percent)."""
    last_reported = [0]

    def report(done: int, total: int | None) -> None:
        if done - last_reported[0] < PROGRESS_STEP_BYTES and done != total:
            return
        last_reported[0] = done
        if total:
            done_mib, total_mib = done // (1 << 20), total // (1 << 20)
            print(f"       {done * 100 // total:3d}% ({done_mib} / {total_mib} MiB)", file=out)
        else:
            print(f"       {done // (1 << 20)} MiB", file=out)

    return report


def install_archive(
    model: ModelSpec,
    models_dir: Path,
    *,
    force: bool,
    out: IO[str],
    err: IO[str],
    opener: Callable[..., Any],
) -> bool:
    """Download, verify and extract a github-release model into models_dir/dest. Returns True on success.

    The folder is replaced only after the new copy is complete, so a failed download leaves the
    previous files in place. Without --force, a folder that already holds every expected file is kept.
    """
    base = models_dir / model.dest
    if not force and all((base / spec.name).is_file() for spec in model.files):
        print("[skip] all files present (archive was verified when it was downloaded)", file=out)
        return True
    print(f"[get ] {model.url}", file=out)
    base.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=base.parent, prefix=f".{base.name}-") as work:
            archive = Path(work) / "archive.tar.bz2"
            download_file(
                model.url, archive, model.archive_sha256, opener=opener, progress=_progress_printer(out)
            )
            staged = Path(work) / "staged"
            extract_tar_bz2(archive, staged)
            missing = [spec.name for spec in model.files if not (staged / spec.name).is_file()]
            if missing:
                raise ArchiveError(f"archive lacks expected file(s): {', '.join(missing)}")
            if base.exists():
                shutil.rmtree(base)
            os.replace(staged, base)
    except (DownloadError, ArchiveError, OSError) as exc:
        print(f"[fail] {exc}", file=err)
        return False
    print("[ok  ] archive sha256 verified and extracted", file=out)
    return True


def run(
    models: Sequence[ModelSpec],
    models_dir: Path,
    *,
    force: bool,
    out: IO[str],
    err: IO[str],
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> int:
    """Fetch every missing or mismatched file, or install each archive. Returns an exit code."""
    failures = 0
    for model in models:
        if model.source == SOURCE_RELEASE:
            print(f"== {model.id} ({model.source})", file=out)
            if not install_archive(model, models_dir, force=force, out=out, err=err, opener=opener):
                failures += 1
            continue
        print(f"== {model.id} ({model.repo} @ {model.revision[:7]})", file=out)
        base = models_dir / model.dest
        for spec in model.files:
            target = base / spec.name
            status = hash_status(target, spec.sha256)
            if not needs_download(status, force):
                suffix = " (sha256 not verified: TODO)" if status is HashStatus.UNVERIFIED else ""
                print(f"[skip] {spec.name}{suffix}", file=out)
                continue
            url = build_url(model.repo, model.revision, model.path, spec.name)
            print(f"[get ] {spec.name}", file=out)
            try:
                actual = download_file(
                    url, target, spec.sha256, opener=opener, progress=_progress_printer(out)
                )
            except DownloadError as exc:
                failures += 1
                print(f"[fail] {exc}", file=err)
                continue
            if spec.sha256 is None:
                print(f"[warn] sha256 not in manifest; computed {actual} for {spec.name}", file=out)
            else:
                print(f"[ok  ] {spec.name} verified", file=out)
    if failures:
        print(f"{failures} file(s) failed", file=err)
        return EXIT_FAILED
    return EXIT_OK


def default_models_dir() -> Path:
    """Return VOICEPILOT_MODELS_DIR when set, otherwise <repo>/models (same rule as paths.models_dir)."""
    override = os.environ.get(MODELS_ENV_VAR)
    return Path(override) if override else REPO_ROOT / "models"


def main(argv: Sequence[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description="Download VoicePilot speech models listed in the manifest.")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST, help="model manifest (TOML)")
    parser.add_argument("--models-dir", type=Path, default=None, help="destination folder for models")
    parser.add_argument("--force", action="store_true", help="download again even if files are present")
    args = parser.parse_args(argv)
    try:
        models = load_manifest(args.manifest)
    except ManifestError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    models_dir = args.models_dir if args.models_dir is not None else default_models_dir()
    return run(models, models_dir, force=args.force, out=sys.stdout, err=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
