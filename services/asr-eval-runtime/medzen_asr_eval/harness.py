"""Fail-closed pilot selection, dispatch and durable receipt helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .identity import CANDIDATES


PROSPECTIVE_VERSIONS = frozenset(
    {"aaf-test-v1", "cv17-test-v1", "fleurs-v1", "soreva-v1"}
)
MAX_ROWS_PER_MANIFEST = 10
MAX_PILOT_ROWS = 540


class EvaluationRefusal(ValueError):
    """The pilot cannot proceed without violating a bound control."""


def canonical_json(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def select_rows(manifests: Iterable[tuple[str, Iterable[dict[str, Any]]]]) -> list[dict[str, Any]]:
    """Select the first ten unique checksums per prospective manifest."""
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for manifest_path, rows in sorted(manifests, key=lambda item: item[0]):
        parts = Path(manifest_path).parts
        if len(parts) != 5 or parts[0] != "eval" or parts[2] != "asr":
            raise EvaluationRefusal(f"unexpected manifest path: {manifest_path}")
        language, version = parts[1], parts[3]
        if version not in PROSPECTIVE_VERSIONS:
            continue
        candidates: list[dict[str, Any]] = []
        for row in rows:
            checksum = row.get("audio_checksum_sha256")
            if not isinstance(checksum, str) or len(checksum) != 64:
                raise EvaluationRefusal(f"{manifest_path}: malformed audio checksum")
            if checksum in seen:
                raise EvaluationRefusal(f"duplicate audio checksum: {checksum}")
            seen.add(checksum)
            reference = row.get("text_normalized")
            if not isinstance(reference, str) or not reference:
                raise EvaluationRefusal(f"{manifest_path}: reference absent")
            candidates.append(
                {
                    "manifest": manifest_path,
                    "language": language,
                    "source_id": row.get("source_id"),
                    "audio_filepath": row.get("audio_filepath"),
                    "audio_checksum_sha256": checksum,
                    "duration_s": row.get("duration_s"),
                    "reference_sha256": sha256_bytes(reference.encode("utf-8")),
                }
            )
        ordered = sorted(candidates, key=lambda row: row["audio_checksum_sha256"])
        for ordinal, row in enumerate(ordered[:MAX_ROWS_PER_MANIFEST], 1):
            selected.append({**row, "selection_ordinal": ordinal})
    if len(selected) > MAX_PILOT_ROWS:
        raise EvaluationRefusal("pilot exceeds the 540-row hard maximum")
    return selected


def validate_mode(candidate_name: str, mode: str, language_id: str | None) -> None:
    candidate = CANDIDATES.get(candidate_name)
    if candidate is None:
        raise EvaluationRefusal(f"unknown candidate: {candidate_name}")
    if mode not in {"unconditioned", "conditioned"}:
        raise EvaluationRefusal(f"unknown mode: {mode}")
    if mode == "unconditioned":
        if language_id is not None:
            raise EvaluationRefusal("unconditioned mode forbids a language identifier")
        return
    if not candidate.conditioned:
        raise EvaluationRefusal(f"{candidate_name} conditioned mode is NOT_APPLICABLE")
    if not language_id:
        raise EvaluationRefusal("conditioned mode requires an exact reviewed identifier")


def write_once(path: Path, value: dict[str, Any]) -> str:
    """Create and fsync one immutable receipt; refuse overwrite."""
    payload = canonical_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return sha256_bytes(payload)
