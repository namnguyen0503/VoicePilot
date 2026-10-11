#!/usr/bin/env python3
"""Benchmark the STT backends and the command parser on a folder of recorded sentences.

Input folder layout:
    <input>/labels.csv   columns: file, expected_intent, expected_params_json, context[, reference]
    <input>/*.wav        the files named in labels.csv (any sample rate, mono or stereo PCM)

Columns:
    file                  WAV file name inside the folder.
    expected_intent       Intent name from contracts.Intent, e.g. PPT_NEXT_SLIDE or DICTATE_TEXT.
    expected_params_json  JSON object of parameters that must match, e.g. {"query": "thời tiết"};
                          "{}" when no parameter is checked.
    context               powerpoint | browser | other | none (the window kind at the time of speaking).
    reference             optional. The sentence as spoken, used for WER and exact match.

For each selected backend (google, offline = Gipformer, hybrid), the clips run through the same
HybridTranscriber the app uses, then through RuleParser. Results are written as Markdown and JSON.
Transcript text is NOT written to the JSON output; only per-clip status, latency and error numbers.

Usage:
    python tools/benchmark_stt.py --input bench/ --backends google,offline,hybrid \\
        --output-md docs/bench-results.md --output-json bench-results.json

Optional dependency: psutil (peak RSS). Without it the RSS column shows n/a.
Run one backend per invocation to get an isolated memory number for each.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
import threading
import time
import unicodedata
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from voicepilot import paths  # noqa: E402
from voicepilot.audio.capture import convert_to_16k  # noqa: E402
from voicepilot.config import load_config  # noqa: E402
from voicepilot.contracts import (  # noqa: E402
    AppKind,
    AudioClip,
    Command,
    Intent,
    Mode,
    SttBackendMode,
    SttError,
    Transcript,
    WindowContext,
)
from voicepilot.intent.parser import RuleParser  # noqa: E402
from voicepilot.intent.validation import is_valid_candidate  # noqa: E402
from voicepilot.stt.gipformer import DEFAULT_MODEL_SUBDIR, GipformerStt  # noqa: E402
from voicepilot.stt.google import GoogleStt  # noqa: E402
from voicepilot.stt.hotwords import command_phrases  # noqa: E402
from voicepilot.stt.hybrid import HybridTranscriber  # noqa: E402

TARGET_RATE = 16000
BACKEND_MODES = {
    "google": SttBackendMode.GOOGLE,
    "offline": SttBackendMode.OFFLINE,
    "hybrid": SttBackendMode.HYBRID,
}
CONTEXT_KINDS = {
    "powerpoint": AppKind.POWERPOINT,
    "browser": AppKind.BROWSER,
    "other": AppKind.OTHER,
    "none": AppKind.NONE,
}
REQUIRED_COLUMNS = ("file", "expected_intent", "expected_params_json", "context")
MB = 1024 * 1024


@dataclass
class Case:
    """One labelled row of labels.csv."""

    file: Path
    expected_intent: Intent
    expected_params: dict[str, str]
    context: WindowContext
    reference: str | None


@dataclass
class Outcome:
    """Result of one clip on one backend."""

    file: str
    latency_ms: float
    source: str | None
    intent_ok: bool
    params_ok: bool
    word_edits: int
    reference_words: int
    exact: bool | None
    error: str | None = None


@dataclass
class BackendReport:
    """Aggregated metrics for one backend."""

    backend: str
    outcomes: list[Outcome] = field(default_factory=list)
    model_load_ms: float | None = None
    peak_rss_mb: float | None = None


def read_wav(path: Path) -> np.ndarray:
    """Read a PCM WAV file and return mono 16 kHz int16 samples."""
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    if width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.int16) - 128) * 256
    elif width == 2:
        data = np.frombuffer(raw, dtype="<i2")
    elif width == 4:
        data = (np.frombuffer(raw, dtype="<i4") >> 16).astype(np.int16)
    else:
        raise ValueError(f"{path.name}: {8 * width}-bit PCM is not supported; convert it to 16-bit first")
    if channels > 1:
        data = np.rint(data.reshape(-1, channels).astype(np.float64).mean(axis=1)).astype(np.int16)
    return convert_to_16k(data, rate)


def load_cases(folder: Path) -> list[Case]:
    """Read labels.csv and validate every row. Raises ValueError with the row number on error."""
    labels = folder / "labels.csv"
    if not labels.is_file():
        raise ValueError(f"missing {labels}")
    with labels.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = [name for name in REQUIRED_COLUMNS if name not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"labels.csv lacks column(s): {', '.join(missing)}")
        cases = []
        for line_no, row in enumerate(reader, start=2):
            cases.append(_parse_case(folder, row, line_no))
    if not cases:
        raise ValueError("labels.csv has no rows")
    return cases


def _parse_case(folder: Path, row: dict[str, str], line_no: int) -> Case:
    """Convert one CSV row to a Case."""
    try:
        intent = Intent(row["expected_intent"].strip())
    except ValueError as exc:
        raise ValueError(f"labels.csv line {line_no}: unknown intent {row['expected_intent']!r}") from exc
    context_name = row["context"].strip().lower()
    if context_name not in CONTEXT_KINDS:
        raise ValueError(f"labels.csv line {line_no}: unknown context {context_name!r}")
    try:
        params = json.loads(row["expected_params_json"] or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"labels.csv line {line_no}: expected_params_json is not JSON") from exc
    if not isinstance(params, dict):
        raise ValueError(f"labels.csv line {line_no}: expected_params_json must be a JSON object")
    audio = folder / row["file"].strip()
    if not audio.is_file():
        raise ValueError(f"labels.csv line {line_no}: missing audio file {audio.name}")
    reference = (row.get("reference") or "").strip() or None
    context = WindowContext(
        hwnd=0,
        pid=0,
        process_name=context_name,
        title="",
        app_kind=CONTEXT_KINDS[context_name],
        browser=None,
    )
    return Case(
        file=audio,
        expected_intent=intent,
        expected_params={str(k): str(v) for k, v in params.items()},
        context=context,
        reference=reference,
    )


def normalize_text(text: str) -> list[str]:
    """Lowercase, strip punctuation (Unicode-aware, keeps Vietnamese letters) and split into words."""
    folded = unicodedata.normalize("NFC", text).lower()
    cleaned = re.sub(r"[^\w\s]", " ", folded)
    return cleaned.split()


def word_edit_distance(hyp: Sequence[str], ref: Sequence[str]) -> int:
    """Levenshtein distance between two word sequences."""
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, start=1):
        current = [i]
        for j, hyp_word in enumerate(hyp, start=1):
            cost = 0 if ref_word == hyp_word else 1
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost))
        previous = current
    return previous[-1]


def percentile(values: Sequence[float], pct: float) -> float | None:
    """Nearest-rank percentile. Returns None for an empty sequence."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def peak_rss_mb() -> float | None:
    """Peak working set (Windows) or resident set size of this process, in MB, if psutil is installed."""
    try:
        import psutil
    except ImportError:
        return None
    info = psutil.Process().memory_info()
    peak = getattr(info, "peak_wset", None) or info.rss
    return round(peak / MB, 1)


def is_valid_for(parser: RuleParser, case: Case) -> Callable[[Transcript], bool]:
    """Return the validity test the app uses: an executable command, or non-empty dictation text."""
    mode = Mode.DICTATION if case.expected_intent is Intent.DICTATE_TEXT else Mode.COMMAND
    known_sites = frozenset(load_config().website_aliases.values())

    def check(transcript: Transcript) -> bool:
        if mode is Mode.DICTATION:
            return bool(transcript.text.strip())
        command = parser.parse(transcript.text, case.context, mode)
        return is_valid_candidate(command, case.context, known_sites)

    return check


def run_backend(
    name: str,
    cases: list[Case],
    parser: RuleParser,
    args: argparse.Namespace,
    gipformer: GipformerStt | None,
) -> BackendReport:
    """Transcribe every case with one backend selection and score the results."""
    mode = BACKEND_MODES[name]
    uses_google = mode in (SttBackendMode.GOOGLE, SttBackendMode.HYBRID)
    google = GoogleStt(language=args.language) if uses_google else None
    offline = gipformer if mode in (SttBackendMode.OFFLINE, SttBackendMode.HYBRID) else None
    transcriber = HybridTranscriber(
        google=google,
        offline=offline,
        mode=mode,
        google_timeout_s=args.google_timeout,
    )
    report = BackendReport(backend=name)
    cancel_event = threading.Event()
    try:
        for index, case in enumerate(cases):
            outcome = _run_case(transcriber, parser, case, index, cancel_event)
            report.outcomes.append(outcome)
            progress = f"{index + 1}/{len(cases)} {case.file.name}"
            print(f"  [{name}] {progress}: {outcome.latency_ms:.0f} ms", flush=True)
        report.peak_rss_mb = peak_rss_mb()
        return report
    finally:
        transcriber.close()


def _run_case(
    transcriber: HybridTranscriber,
    parser: RuleParser,
    case: Case,
    index: int,
    cancel_event: threading.Event,
) -> Outcome:
    """Run one clip through the transcriber and the parser and score it."""
    try:
        clip = AudioClip(samples=read_wav(case.file).tobytes(), turn_id=index)
    except (OSError, ValueError, wave.Error) as exc:
        return _score(case, 0.0, None, None, parser, error=str(exc))
    started = time.perf_counter()
    try:
        mode = Mode.DICTATION if case.expected_intent is Intent.DICTATE_TEXT else Mode.COMMAND
        transcript = transcriber.for_mode(mode).transcribe(clip, is_valid_for(parser, case), cancel_event)
    except SttError as exc:
        latency = (time.perf_counter() - started) * 1000
        return _score(case, latency, None, None, parser, error=str(exc))
    latency = (time.perf_counter() - started) * 1000
    return _score(case, latency, transcript.text, transcript.source, parser)


def _score(
    case: Case,
    latency_ms: float,
    text: str | None,
    source: str | None,
    parser: RuleParser,
    error: str | None = None,
) -> Outcome:
    """Compare one result against the labels."""
    command: Command | None = None
    if text is not None:
        mode = Mode.DICTATION if case.expected_intent is Intent.DICTATE_TEXT else Mode.COMMAND
        command = parser.parse(text, case.context, mode)
    intent_ok = command is not None and command.intent is case.expected_intent
    params_ok = intent_ok and all(
        _same_value(command.params.get(key), value) for key, value in case.expected_params.items()
    )
    if case.reference is None:
        edits, ref_len, exact = 0, 0, None
    else:
        ref_words = normalize_text(case.reference)
        hyp_words = normalize_text(text) if text is not None else []
        edits = word_edit_distance(hyp_words, ref_words)
        ref_len = len(ref_words)
        exact = hyp_words == ref_words
    return Outcome(
        file=case.file.name,
        latency_ms=latency_ms,
        source=source,
        intent_ok=intent_ok,
        params_ok=params_ok,
        word_edits=edits,
        reference_words=ref_len,
        exact=exact,
        error=error,
    )


def _same_value(actual: str | None, expected: str) -> bool:
    """Compare parameter values after Unicode and whitespace normalization."""
    if actual is None:
        return False
    return normalize_text(actual) == normalize_text(expected)


def summarize(report: BackendReport) -> dict[str, Any]:
    """Compute the metrics written to the Markdown table and JSON."""
    outcomes = report.outcomes
    total = len(outcomes)
    with_ref = [o for o in outcomes if o.reference_words > 0]
    ref_words = sum(o.reference_words for o in with_ref)
    edits = sum(o.word_edits for o in with_ref)
    exact_flags = [o.exact for o in outcomes if o.exact is not None]
    latencies = [o.latency_ms for o in outcomes]
    sources: dict[str, int] = {}
    for o in outcomes:
        if o.source:
            sources[o.source] = sources.get(o.source, 0) + 1
    return {
        "backend": report.backend,
        "clips": total,
        "no_result": sum(1 for o in outcomes if o.error is not None),
        "intent_accuracy": _ratio(sum(o.intent_ok for o in outcomes), total),
        "param_accuracy": _ratio(sum(o.params_ok for o in outcomes), total),
        "wer": _ratio(edits, ref_words) if ref_words else None,
        "exact_match": _ratio(sum(exact_flags), len(exact_flags)) if exact_flags else None,
        "latency_p50_ms": _round(percentile(latencies, 50)),
        "latency_p95_ms": _round(percentile(latencies, 95)),
        "model_load_ms": _round(report.model_load_ms),
        "peak_rss_mb": report.peak_rss_mb,
        "sources": sources,
        "per_clip": [
            {
                "file": o.file,
                "latency_ms": round(o.latency_ms, 1),
                "source": o.source,
                "intent_ok": o.intent_ok,
                "params_ok": o.params_ok,
                "word_edits": o.word_edits,
                "reference_words": o.reference_words,
                "error": o.error,
            }
            for o in outcomes
        ],
    }


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _round(value: float | None) -> float | None:
    return round(value, 1) if value is not None else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _num(value: float | None, unit: str = "") -> str:
    return "n/a" if value is None else f"{value:g}{unit}"


def markdown_table(summaries: list[dict[str, Any]]) -> str:
    """Render the summary rows as a Markdown table."""
    header = (
        "| Backend | Clips | No result | Intent acc. | Param acc. | WER | Exact match "
        "| p50 latency | p95 latency | Model load | Peak RSS | Sources |"
    )
    lines = [header, "|" + "---|" * 12]
    for s in summaries:
        sources = ", ".join(f"{k}: {v}" for k, v in sorted(s["sources"].items())) or "-"
        lines.append(
            "| {backend} | {clips} | {no_result} | {intent} | {param} | {wer} | {exact} "
            "| {p50} | {p95} | {load} | {rss} | {sources} |".format(
                backend=s["backend"],
                clips=s["clips"],
                no_result=s["no_result"],
                intent=_pct(s["intent_accuracy"]),
                param=_pct(s["param_accuracy"]),
                wer=_pct(s["wer"]),
                exact=_pct(s["exact_match"]),
                p50=_num(s["latency_p50_ms"], " ms"),
                p95=_num(s["latency_p95_ms"], " ms"),
                load=_num(s["model_load_ms"], " ms"),
                rss=_num(s["peak_rss_mb"], " MB"),
                sources=sources,
            )
        )
    return "\n".join(lines) + "\n"


def build_gipformer(args: argparse.Namespace) -> tuple[GipformerStt, float]:
    """Create the Gipformer backend and warm it up. Returns the backend and the load time in ms."""
    model_dir = Path(args.model_dir) if args.model_dir else paths.models_dir() / "asr" / DEFAULT_MODEL_SUBDIR
    config = load_config()
    backend = GipformerStt(model_dir, num_threads=args.threads,
                          hotwords=command_phrases(config.app_aliases, config.website_aliases,
                                                   config.custom_commands))
    started = time.perf_counter()
    backend.warmup()
    return backend, (time.perf_counter() - started) * 1000


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Command-line options."""
    parser = argparse.ArgumentParser(description="Benchmark VoicePilot STT backends and the parser.")
    parser.add_argument("--input", type=Path, required=True, help="folder with labels.csv and WAV files")
    parser.add_argument(
        "--backends", default="google,offline,hybrid", help="comma list: google, offline, hybrid"
    )
    parser.add_argument("--model-dir", default=None, help="Gipformer model folder (default: models/asr/...)")
    parser.add_argument("--threads", type=int, default=2, help="Gipformer CPU threads (default 2)")
    parser.add_argument("--google-timeout", type=float, default=2.0, help="seconds before Gipformer starts")
    parser.add_argument("--language", default="vi-VN", help="Google recognition language")
    parser.add_argument("--output-md", type=Path, default=None, help="write the Markdown table here")
    parser.add_argument("--output-json", type=Path, default=None, help="write the JSON report here")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the benchmark. Returns the process exit code."""
    args = parse_args(argv)
    names = [name.strip() for name in args.backends.split(",") if name.strip()]
    unknown = [name for name in names if name not in BACKEND_MODES]
    if unknown:
        print(f"error: unknown backend(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    try:
        cases = load_cases(args.input)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    config = load_config()
    parser = RuleParser(config.website_aliases, config.app_aliases,
                        custom_commands=config.custom_commands)
    summaries = []
    gipformer: GipformerStt | None = None
    load_ms: float | None = None
    if any(name in ("offline", "hybrid") for name in names):
        gipformer, load_ms = build_gipformer(args)
    for name in names:
        print(f"Running backend: {name} ({len(cases)} clips)")
        report = run_backend(name, cases, parser, args, gipformer)
        report.model_load_ms = load_ms if name in ("offline", "hybrid") else None
        summaries.append(summarize(report))

    table = markdown_table(summaries)
    print()
    print(table)
    if args.output_md:
        args.output_md.write_text(table, encoding="utf-8")
    if args.output_json:
        payload = {"input": str(args.input), "clips": len(cases), "results": summaries}
        args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
