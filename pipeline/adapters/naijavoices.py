"""NaijaVoices adapter — hausa/igbo/yoruba ASR at scale (Tier B).

Commercial rights come from the owner's PRIVATE signed agreement
(LIC-2026-004-naijavoices-commercial-agreement; the public repo licence is
CC-BY-NC-SA and is NOT what this ingest relies on). Rows are recorded as
license_policy=commercial_ok on that owner authority — the yemba_egra
precedent. Access is HF-gated: the machine needs the owner's logged-in
token (huggingface-cli login), accepted on the owner's account 2026-08-19.

Repo layout (verified 2026-08-19): {language}-batch-{N}/train-*.parquet on
MAIN, columns: audio{bytes,path}, speaker_id, text, language, gender,
age_range, phase. Real speaker ids -> speaker_disjoint.

MEDZEN_NV_MAX_SECONDS caps cumulative emitted audio (the CV-cap pattern);
ALWAYS set it for local runs — the full corpus is ~1,800 h.

SMOKE TEST: `python -m pipeline.ingest --source naijavoices --language
hausa --dry-run --limit 20`.
"""
from __future__ import annotations

import os
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, SourceSpec, build_record

REPO = "naijavoices/naijavoices-dataset"
REVISION = "main"   # pinned at first full ingest; batches are append-only
LICENSE_POLICY = "commercial_ok"   # LIC-2026-004, owner authority
CONFIGS = {"hausa": "hausa", "igbo": "igbo", "yoruba": "yoruba"}


class NaijaVoicesAdapter:
    name = "naijavoices"

    def __init__(self, language: str, task: str | None = None,
                 version: str = "v1"):
        if language not in CONFIGS:
            raise ValueError(
                f"NaijaVoices has no config for '{language}'. "
                f"Available: {sorted(CONFIGS)}")
        if task not in (None, "asr"):
            raise ValueError("NaijaVoices is ASR-only")
        self.language = language
        self.task = "asr"
        self.code = CONFIGS[language]
        self.version = version
        self.config = f"nv_{self.code}"
        self.spec = SourceSpec(
            source_id="naijavoices",
            dataset_release=f"{REPO}@{REVISION}#{self.code}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train"],
            consent_id="agreement-level:LIC-2026-004",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)

    def items(self, limit: int | None = None) -> Iterator[dict]:
        from huggingface_hub import get_token
        token = get_token()
        if not token:
            raise ValueError("NaijaVoices is gated — run huggingface-cli login "
                             "with the owner's accepted account first")
        max_seconds = float(os.environ.get("MEDZEN_NV_MAX_SECONDS") or 0) or None
        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = __import__("pathlib").Path(sd.name)
        self._spill_dir = sd
        shards = gc.list_shards(
            REPO, REVISION, f"{self.code}-batch-*/train-*.parquet", token=token)
        if not shards:
            raise ValueError(f"no shards match {self.code}-batch-*/train-*")
        produced = 0
        emitted_s = 0.0
        for p in gc.read_parquet_audio_shards(
                REPO, REVISION, shards, split="train", spill=spill,
                token=token,
                limit=limit):
            if limit and produced >= limit:
                return
            if max_seconds and emitted_s >= max_seconds:
                return
            rec = build_record(
                audio_uri=f"s3://medzen-speech/curated/{base}/{self.version}/audio/{p['stem']}.wav",
                audio_sha256=p["wav_sha256"],
                duration_s=p["duration_s"], sample_rate=TARGET_SR, channels=1,
                text_verbatim=p["text"], language=self.language,
                speaker_id=str(p["spk_raw"] or "unknown"),
                session_id=f"{self.code}_{p['spk_raw'] or 'unknown'}",
                split="train", spec=self.spec, split_strategy="speaker_disjoint",
                gender=gc.norm_gender(p["gender_raw"]), domain="asr",
                license_tier=self.tier, dialect=self.code,
                raw_filepath=f"s3://medzen-speech/raw/{base}/{p['stem']}.{p['raw_ext']}",
                raw_checksum_sha256=p["raw_sha256"],
            )
            yield {"record": rec, "raw_path": p["raw_path"],
                   "wav_path": p["wav_path"], "raw_ext": p["raw_ext"],
                   "stem": p["stem"]}
            produced += 1
            emitted_s += p["duration_s"]

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
