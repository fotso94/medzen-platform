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


class ConflictingDuplicateAudioError(ValueError):
    """Byte-identical audio listed under different transcripts.

    The same bytes cannot carry two truths: at least one label is wrong, and
    keeping either side silently poisons training or eval. Ingest must refuse
    and the source must be repaired. `pairs` holds every offending
    (audio_sha256, first_ref, first_text, second_ref, second_text) so the
    error message doubles as the repair list.
    """

    def __init__(self, pairs: list[tuple[str, str, str, str, str]]):
        self.pairs = list(pairs)
        lines = [f"sha {sha[:16]}…: {ra} {ta!r}  vs  {rb} {tb!r}"
                 for sha, ra, ta, rb, tb in self.pairs]
        super().__init__(
            f"{len(self.pairs)} byte-identical audio pair(s) carry conflicting "
            "transcripts — repair the source:\n  " + "\n  ".join(lines))


def dedupe_byte_duplicates(items: list[dict]) -> list[dict]:
    """Byte-duplicate guard over adapter items, shared by every source.

    Two rows with the same audio_checksum_sha256 are one recording listed
    twice. Same normalized text -> a re-listing: keep the first, drop the
    rest. Different text -> conflicting labels for identical bytes: raise
    ConflictingDuplicateAudioError naming EVERY pair, so one failed run
    yields the complete repair list (yemba_egra gb1 shipped 4 re-listings
    AND 4 conflicting pairs; the hand-built gb2 had to drop the conflicts).
    """
    first: dict[str, dict] = {}
    kept: list[dict] = []
    conflicts: list[tuple[str, str, str, str, str]] = []
    for it in items:
        rec = it["record"]
        prev = first.get(rec["audio_checksum_sha256"])
        if prev is None:
            first[rec["audio_checksum_sha256"]] = rec
            kept.append(it)
        elif rec["text_normalized"] != prev["text_normalized"]:
            conflicts.append((rec["audio_checksum_sha256"],
                              prev["audio_filepath"], prev["text_verbatim"],
                              rec["audio_filepath"], rec["text_verbatim"]))
    if conflicts:
        raise ConflictingDuplicateAudioError(conflicts)
    return kept


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
