"""Complete one-load-per-candidate offline pilot loop with deterministic resume."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import threading
import time
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .backends import Backend, Transcript
from .conditioning import language_id, load_conditioning
from .harness import EvaluationRefusal, canonical_json, write_once
from .identity import CANDIDATES
from .metrics import POLICY_LABELS, TONE_SENSITIVE, aggregate, error_counts, normalize_text


SHA256 = set("0123456789abcdef")
META_FILES = {
    "omniASR-CTC-1B-v2.pt": (3902956068, "354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c"),
    "omniASR-LLM-1B-v2.pt": (9118733852, "cceb4d9ebac3d168a6af6b26c62ce11bafc562b38976c6bfa87e7d60422c6da5"),
    "omniASR_tokenizer_written_v2.model": (None, "8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e"),
}
WHISPER_TREE = "5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e"


def sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def verify_file(path: Path, expected_sha256: str, expected_bytes: int | None = None) -> dict[str, Any]:
    if len(expected_sha256) != 64 or any(char not in SHA256 for char in expected_sha256):
        raise EvaluationRefusal("expected file SHA-256 is malformed")
    digest, size = sha256_file(path)
    if digest != expected_sha256 or (expected_bytes is not None and size != expected_bytes):
        raise EvaluationRefusal(f"file identity differs: {path.name}")
    return {"path": str(path), "sha256": digest, "bytes": size}


def verify_model_root(model_root: Path, binding_path: Path) -> dict[str, Any]:
    try:
        binding = json.loads(binding_path.read_bytes())
    except Exception as exc:
        raise EvaluationRefusal("model binding is absent or malformed") from exc
    if binding.get("schema_version") != 1 or binding.get("whisper_tree_sha256") != WHISPER_TREE:
        raise EvaluationRefusal("model binding identity differs")
    files = binding.get("whisper_files")
    if not isinstance(files, dict) or not files:
        raise EvaluationRefusal("Whisper file bindings are absent")
    normalized: dict[str, dict[str, Any]] = {}
    for raw, expected in sorted(files.items()):
        relative = PurePosixPath(raw)
        if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != raw:
            raise EvaluationRefusal("unsafe Whisper binding path")
        actual = verify_file(
            model_root / "whisper-large-v3-ct2" / Path(*relative.parts),
            expected["sha256"], expected["bytes"],
        )
        normalized[raw] = {"sha256": actual["sha256"], "bytes": actual["bytes"]}
    tree = hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if tree != WHISPER_TREE:
        raise EvaluationRefusal("Whisper artifact tree differs")
    meta = {}
    for name, (expected_bytes, expected_sha) in META_FILES.items():
        meta[name] = verify_file(model_root / name, expected_sha, expected_bytes)
    return {"whisper_tree_sha256": tree, "whisper_files": len(files), "meta": meta}


def load_runtime_rows(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise EvaluationRefusal("runtime row bundle is absent or malformed") from exc
    if value.get("schema_version") != 1 or value.get("classification") != "PUBLIC_RESEARCH_NO_PHI":
        raise EvaluationRefusal("runtime row bundle classification differs")
    rows = value.get("rows")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 540:
        raise EvaluationRefusal("runtime row count is outside the pilot bound")
    checksums: set[str] = set()
    normalized = []
    for row in rows:
        required = {
            "manifest", "language", "source_id", "audio_local_path",
            "audio_checksum_sha256", "duration_s", "reference",
            "reference_sha256", "selection_ordinal",
        }
        if not isinstance(row, dict) or set(row) != required:
            raise EvaluationRefusal("runtime row fields differ")
        checksum = row["audio_checksum_sha256"]
        if not isinstance(checksum, str) or len(checksum) != 64 or any(char not in SHA256 for char in checksum):
            raise EvaluationRefusal("runtime audio checksum is malformed")
        if checksum in checksums:
            raise EvaluationRefusal("runtime row checksums overlap")
        checksums.add(checksum)
        if hashlib.sha256(row["reference"].encode()).hexdigest() != row["reference_sha256"]:
            raise EvaluationRefusal("runtime reference hash differs")
        audio = Path(row["audio_local_path"])
        if not audio.is_absolute() or ".." in audio.parts:
            raise EvaluationRefusal("runtime audio path is not an absolute normalized path")
        if not isinstance(row["duration_s"], (int, float)) or isinstance(row["duration_s"], bool) or not 0 < row["duration_s"] <= 30:
            raise EvaluationRefusal("runtime row duration differs")
        normalized.append(row)
    return sorted(normalized, key=lambda row: (row["manifest"], row["selection_ordinal"]))


class GpuMemorySampler:
    def __init__(self, command: tuple[str, ...] = (
        "nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"
    ), interval_seconds: float = 1.0):
        self.command = command
        self.interval_seconds = interval_seconds
        self.samples: list[float] = []
        self.errors: list[str] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _sample(self) -> None:
        while not self._stop.is_set():
            try:
                completed = subprocess.run(self.command, check=False, text=True, capture_output=True, timeout=5)
                if completed.returncode != 0:
                    raise ValueError(f"nvidia-smi exit {completed.returncode}")
                values = [float(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
                if len(values) != 1 or not math.isfinite(values[0]) or values[0] < 0:
                    raise ValueError("nvidia-smi output is not one numeric sample")
                self.samples.append(values[0])
            except Exception as exc:
                self.errors.append(type(exc).__name__)
            self._stop.wait(self.interval_seconds)

    def start(self) -> None:
        self._sample_once()
        self._thread = threading.Thread(target=self._sample, daemon=True)
        self._thread.start()

    def _sample_once(self) -> None:
        try:
            completed = subprocess.run(self.command, check=False, text=True, capture_output=True, timeout=5)
            if completed.returncode != 0:
                raise ValueError(f"nvidia-smi exit {completed.returncode}")
            values = [float(line.strip()) for line in completed.stdout.splitlines() if line.strip()]
            if len(values) != 1 or not math.isfinite(values[0]) or values[0] < 0:
                raise ValueError("nvidia-smi output is not one numeric sample")
            self.samples.append(values[0])
        except Exception as exc:
            self.errors.append(type(exc).__name__)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)


def _receipt_name(candidate: str, mode: str, checksum: str) -> str:
    identity = f"{candidate}|{mode}|{checksum}".encode()
    return hashlib.sha256(identity).hexdigest() + ".json"


def _existing_receipt(
    path: Path, candidate: str, mode: str, row: dict[str, Any], normalization_policy: str
) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_bytes())
    except Exception as exc:
        raise EvaluationRefusal("existing row receipt is malformed") from exc
    expected = (candidate, mode, row["audio_checksum_sha256"])
    if (value.get("candidate"), value.get("mode"), value.get("audio_sha256")) != expected:
        raise EvaluationRefusal("existing row receipt identity differs")
    if value.get("status") not in {"PASS_ROW_INFERENCE", "NOT_APPLICABLE"}:
        raise EvaluationRefusal("existing row receipt is not resumable")
    if value["status"] == "PASS_ROW_INFERENCE":
        # A resumed row was scored under whatever policy that run declared.
        # Reusing it under a different one would pool a tone-sensitive and a
        # tone-blind count into one rate.
        scored_under = value.get("errors", {}).get("normalization_policy")
        if scored_under != normalization_policy:
            raise EvaluationRefusal(
                "existing row receipt was scored under normalization policy "
                f"{scored_under!r}, not {normalization_policy!r}")
    return value


def run_pilot(
    *,
    rows_path: Path,
    model_root: Path,
    model_binding_path: Path,
    receipt_root: Path,
    aggregate_path: Path,
    conditioning_path: Path | None = None,
    normalization_policy: str = TONE_SENSITIVE,
    backend_loader: Callable[[str, str, str | None, Path], Backend] | None = None,
    model_verifier: Callable[[Path, Path], dict[str, Any]] = verify_model_root,
    sampler: GpuMemorySampler | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    if normalization_policy not in POLICY_LABELS:
        raise EvaluationRefusal(
            f"unknown normalization policy {normalization_policy!r}; "
            f"name one of {sorted(POLICY_LABELS)}")
    if backend_loader is None:
        from .backends import load_backend

        backend_loader = load_backend
    rows = load_runtime_rows(rows_path)
    model_identity = model_verifier(model_root, model_binding_path)
    conditioning = load_conditioning(conditioning_path)
    unknown = sorted({row["language"] for row in rows} - set(conditioning))
    if unknown:
        raise EvaluationRefusal(f"runtime rows lack conditioning decisions: {unknown}")
    receipt_root.mkdir(parents=True, exist_ok=True)
    sampler = sampler or GpuMemorySampler()
    sampler.start()
    load_times: dict[str, float] = {}
    receipts: list[dict[str, Any]] = []
    not_applicable = 0
    try:
        for candidate in sorted(CANDIDATES):
            load_started = clock()
            load_receipt = receipt_root / f"backend-load-{candidate}.json"
            try:
                backend = backend_loader(candidate, "unconditioned", None, model_root)
            except Exception as exc:
                write_once(load_receipt, {
                    "status": "REFUSED_BACKEND_LOAD",
                    "candidate": candidate,
                    "exception_class": type(exc).__name__,
                })
                raise
            load_times[candidate] = round(clock() - load_started, 6)
            write_once(load_receipt, {
                "status": "PASS_BACKEND_LOAD",
                "candidate": candidate,
                "load_seconds": load_times[candidate],
            })
            modes = ("unconditioned",) if candidate == "omniASR_CTC_1B_v2" else ("unconditioned", "conditioned")
            for mode in modes:
                for row in rows:
                    path = receipt_root / _receipt_name(candidate, mode, row["audio_checksum_sha256"])
                    existing = _existing_receipt(path, candidate, mode, row, normalization_policy)
                    if existing is not None:
                        receipts.append(existing)
                        not_applicable += existing["status"] == "NOT_APPLICABLE"
                        continue
                    requested_language = None
                    if mode == "conditioned":
                        requested_language = language_id(candidate, row["language"], conditioning)
                        if requested_language is None:
                            value = {
                                "status": "NOT_APPLICABLE",
                                "candidate": candidate,
                                "mode": mode,
                                "language": row["language"],
                                "source_id": row["source_id"],
                                "audio_sha256": row["audio_checksum_sha256"],
                                "reason": "No exact supported language identifier; proxy identifiers are prohibited.",
                            }
                            write_once(path, value)
                            receipts.append(value)
                            not_applicable += 1
                            continue
                    audio = Path(row["audio_local_path"])
                    verify_file(audio, row["audio_checksum_sha256"])
                    started = clock()
                    try:
                        transcript: Transcript = backend.transcribe(audio, requested_language)
                    except Exception as exc:
                        write_once(path, {
                            "status": "REFUSED_ROW_INFERENCE",
                            "candidate": candidate,
                            "mode": mode,
                            "language": row["language"],
                            "source_id": row["source_id"],
                            "audio_sha256": row["audio_checksum_sha256"],
                            "reason_code": "BACKEND_INFERENCE_EXCEPTION",
                            "exception_class": type(exc).__name__,
                        })
                        raise
                    latency = clock() - started
                    prediction = normalize_text(transcript.text, policy=normalization_policy)
                    value = {
                        "status": "PASS_ROW_INFERENCE",
                        "candidate": candidate,
                        "mode": mode,
                        "language_id": requested_language,
                        "language": row["language"],
                        "source_id": row["source_id"],
                        "manifest": row["manifest"],
                        "audio_sha256": row["audio_checksum_sha256"],
                        "reference_sha256": row["reference_sha256"],
                        "prediction": prediction,
                        "prediction_sha256": hashlib.sha256(prediction.encode()).hexdigest(),
                        "errors": error_counts(
                            row["reference"], prediction, policy=normalization_policy),
                        "duration_seconds": row["duration_s"],
                        "latency_seconds": round(latency, 6),
                        "rtf": round(latency / row["duration_s"], 6),
                        "eos_failure": not transcript.eos_observed,
                        "cap_hit": transcript.cap_hit,
                        "termination_evidence": transcript.termination_evidence,
                    }
                    if value["eos_failure"] or value["cap_hit"]:
                        # A capped or EOS-less decode on properly sized token
                        # bounds is the model's measured failure mode, not a
                        # harness fault: score the truncated output, keep the
                        # flags for the aggregate's cap_hits/eos_failures
                        # counters, and bound the tolerated fraction below.
                        value["reason_code"] = "TERMINATION_FLAGGED_ROW_SCORED"
                    write_once(path, value)
                    receipts.append(value)
    finally:
        sampler.stop()
    completed = [value for value in receipts if value["status"] == "PASS_ROW_INFERENCE"]
    # Misconfiguration guard: flagged terminations are tolerated as scored
    # model failures only up to a bounded fraction of each (candidate, mode)
    # pass; beyond it the cause is overwhelmingly a harness or token-bound
    # fault and the run must fail closed rather than publish garbage.
    flagged_groups: dict[tuple[str, str], list[int]] = {}
    for value in completed:
        group = flagged_groups.setdefault((value["candidate"], value["mode"]), [0, 0])
        group[0] += 1
        group[1] += int(bool(value["eos_failure"]) or bool(value["cap_hit"]))
    for (flagged_candidate, flagged_mode), (group_rows, group_flagged) in sorted(flagged_groups.items()):
        if group_rows >= 5 and group_flagged * 5 > group_rows:
            raise EvaluationRefusal(
                "flagged termination fraction exceeds the misconfiguration bound: "
                f"{flagged_candidate}/{flagged_mode} {group_flagged}/{group_rows}"
            )
    summary = aggregate(completed, sampler.samples)
    result = {
        "schema_version": 1,
        "status": summary["status"],
        "normalization_policy": normalization_policy,
        "normalization_policy_label": POLICY_LABELS[normalization_policy],
        "model_identity": model_identity,
        "runtime_rows": len(rows),
        "completed_inferences": len(completed),
        "not_applicable": not_applicable,
        "load_seconds": load_times,
        "sampler_errors": sampler.errors,
        "aggregate": summary,
    }
    write_once(aggregate_path, result)
    return result
