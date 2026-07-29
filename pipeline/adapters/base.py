"""B2.1 — dataset adapter contract.

Every source (HF stream, AfricanVoices download, own studio recordings) gets
ONE adapter. Every adapter emits the SAME A3 manifest record. That is the whole
design: sources vary wildly, the corpus does not.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Iterator, Protocol

from ..normalizers import for_language

TARGET_SR = 16_000
MIN_S, MAX_S = 1.0, 30.0


@dataclass(frozen=True)
class SourceSpec:
    """Provenance that must travel with every row it produces."""
    source_id: str          # e.g. waxalnlp/media_trust
    dataset_release: str    # exact upstream release — reproducibility
    license_policy: str     # commercial_ok | research_only_nc | sharealike_review | ...
    allowed_use: list[str]  # asr_train | tts_train | asr_eval | ...
    consent_id: str         # dataset-level consent ref, or per-speaker for own data


class Adapter(Protocol):
    name: str
    spec: SourceSpec
    def rows(self, language: str, limit: int | None = None) -> Iterator[dict]: ...


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def build_record(
    *, audio_uri: str, audio_sha256: str, duration_s: float,
    sample_rate: int, channels: int, text_verbatim: str, language: str,
    speaker_id: str, session_id: str, split: str, spec: SourceSpec,
    split_strategy: str = "speaker_disjoint",
    segments: list[dict] | None = None, **extra,
) -> dict:
    """Assemble one A3 manifest record. The normaliser is chosen by language
    and its version is recorded, because changing it invalidates cached scores."""
    norm = for_language(language)
    rec = {
        "audio_filepath": audio_uri,
        "audio_checksum_sha256": audio_sha256,
        "duration_s": round(float(duration_s), 3),
        "sample_rate": int(sample_rate),
        "channels": int(channels),
        "text_verbatim": text_verbatim.strip(),
        "text_normalized": norm(text_verbatim),
        "normalization_version": norm.version,
        "primary_language": language,
        "speaker_id": speaker_id,
        "session_id": session_id,
        "split": split,
        "split_strategy": split_strategy,
        "consent_id": spec.consent_id,
        "source_id": spec.source_id,
        "dataset_release": spec.dataset_release,
        "license_policy": spec.license_policy,
        "allowed_use": list(spec.allowed_use),
    }
    if segments:
        rec["segments"] = segments
    for k, v in extra.items():
        if v is not None:
            rec[k] = v
    return rec


def usable(duration_s: float, text: str) -> bool:
    """Cheap pre-filter. The validator enforces this again — this just avoids
    writing rows we already know will be rejected."""
    return MIN_S <= duration_s <= MAX_S and len((text or "").strip()) > 3
