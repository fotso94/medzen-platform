"""Meta Omnilingual ASR Corpus adapter — CC-BY-4.0 (permissive tier).

Layout VERIFIED 2026-08-05 against the pinned revision: native parquet at
`data/<code>_<Script>/<split>-NNNNN-of-MMMMM.parquet`, audio embedded as the
HF Audio feature ({bytes, path}). Reuses the bounded local-parquet reader.

In-scope coverage is small but clean: the corpus targets UNDERSERVED languages,
so it omits the higher-resource ones and overlaps our scope only on Akan and
Fula. Fula spans several Fulfulde varieties — all are ingested under one merged
config, with each row's `dialect` set to the actual code (fuh/fuv/fui are the
varieties closest to northern Cameroon).

SMOKE TEST before bulk: `python -m pipeline.ingest --source meta_omnilingual
--language akan --dry-run --limit 20` (confirms text/speaker column names, which
vary across HF Audio datasets and are auto-detected by the shared reader).
"""
from __future__ import annotations

import hashlib
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, SourceSpec, build_record

REPO = "facebook/omnilingual-asr-corpus"
# Pinned 2026-08-05. Bump deliberately, never automatically.
REVISION = "8648ba8946377697b427ae952076e49fc0e5e44d"
LICENSE_POLICY = "commercial_ok"          # CC-BY-4.0

# language -> HF config codes (verified present this session)
LANGS: dict[str, list[str]] = {
    "akan": ["fat_Latn"],
    "fula": ["fuf_Latn", "fuh_Latn", "fuv_Latn", "fui_Latn",
             "fuc_Latn", "fuq_Latn", "fue_Latn"],
}
SPLIT_MAP = {"train": "train", "dev": "validation", "test": "test"}


class MetaOmnilingualAdapter:
    name = "meta_omnilingual"

    def __init__(self, language: str, task: str | None = None,
                 revision: str = REVISION, version: str = "v1"):
        if language not in LANGS:
            raise ValueError(
                f"Meta Omnilingual (in-scope) has no data for '{language}'. "
                f"Available: {sorted(LANGS)}")
        if task not in (None, "asr"):
            raise ValueError("Meta Omnilingual is ASR-only")
        self.language = language
        self.task = "asr"
        self.codes = LANGS[language]
        self.revision = revision
        self.version = version
        self.config = f"{language}_omni"          # merged config -> one manifest
        self.spec = SourceSpec(
            source_id="meta_omnilingual",
            dataset_release=f"{REPO}@{revision[:12]}#{'+'.join(self.codes)}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train", "asr_eval"],
            consent_id="dataset-level:meta_omnilingual",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)   # -> permissive

    def items(self, limit: int | None = None) -> Iterator[dict]:
        from huggingface_hub import get_token
        token = get_token()
        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = __import__("pathlib").Path(sd.name)
        self._spill_dir = sd                       # keep alive until caller done
        produced = 0
        for code in self.codes:
            for raw_split, split in SPLIT_MAP.items():
                if limit and produced >= limit:
                    return
                shards = gc.list_shards(
                    REPO, self.revision,
                    f"data/{code}/{raw_split}-*.parquet", token=token)
                if not shards:
                    continue
                for p in gc.read_parquet_audio_shards(
                        REPO, self.revision, shards, split=split,
                        spill=spill, token=token,
                        limit=(None if limit is None else limit - produced)):
                    spk_raw = p["spk_raw"]
                    spk = (str(spk_raw).strip() if spk_raw not in
                           (None, "", "none", "null") else f"{code}_unknown")
                    rec = build_record(
                        audio_uri=f"s3://medzen-speech/curated/{base}/{self.version}/audio/{p['stem']}.wav",
                        audio_sha256=p["wav_sha256"],
                        duration_s=p["duration_s"], sample_rate=TARGET_SR, channels=1,
                        text_verbatim=p["text"], language=self.language,
                        speaker_id=spk, session_id=f"{code}_{spk}",
                        split=split, spec=self.spec, split_strategy="speaker_disjoint",
                        gender=gc.norm_gender(p["gender_raw"]), domain="asr",
                        license_tier=self.tier, dialect=code.split("_")[0],
                        raw_filepath=f"s3://medzen-speech/raw/{base}/{p['stem']}.{p['raw_ext']}",
                        raw_checksum_sha256=p["raw_sha256"],
                    )
                    yield {"record": rec, "raw_path": p["raw_path"],
                           "wav_path": p["wav_path"], "raw_ext": p["raw_ext"],
                           "stem": p["stem"]}
                    produced += 1

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
