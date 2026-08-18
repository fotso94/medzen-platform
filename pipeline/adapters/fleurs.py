"""FLEURS adapter — frozen EVALUATION only (eval_only tier).

FLEURS is CC-BY-4.0 (commercially clean), but we deliberately RESERVE it as a
held-out benchmark so every language has an evaluation set independent of its
training data. Licence stays truthful (`commercial_ok`); use is pinned to eval
via allowed_use=[asr_eval] and license_tier=eval_only. Ingest with --force-eval
so it lands under eval/ (IAM write-denied to trainers).

FLEURS's raw repo is tar.gz+tsv, so we read the datasets-server PARQUET MIRROR
(refs/convert/parquet), layout `{config}/{split}/*.parquet` — verified 2026-08-05.
No speaker ids in FLEURS; a per-config fallback speaker is used (irrelevant for
eval). Default splits: test + validation.

SMOKE TEST: `python -m pipeline.ingest --source fleurs --language hausa
--force-eval --dry-run --limit 20`.
"""
from __future__ import annotations

from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, SourceSpec, build_record

REPO = "google/fleurs"
# The parquet mirror is what we actually read. Pinned 2026-08-05.
PARQUET_REVISION = "168de341b3db6859a9bac1c50a2ef5e3b47647e0"   # refs/convert/parquet
SOURCE_REVISION = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"    # main (provenance)
LICENSE_POLICY = "commercial_ok"          # CC-BY-4.0 (reserved for eval)

# language -> FLEURS locale config (verified present this session)
CONFIGS = {
    "swahili": "sw_ke", "hausa": "ha_ng", "yoruba": "yo_ng", "amharic": "am_et",
    "oromo": "om_et", "igbo": "ig_ng", "lingala": "ln_cd", "shona": "sn_zw",
    "wolof": "wo_sn", "fula": "ff_sn", "luganda": "lg_ug",
    "english": "en_us", "french": "fr_fr",
}
SPLITS = ("test", "validation")


class FleursAdapter:
    name = "fleurs"

    def __init__(self, language: str, task: str | None = None,
                 revision: str = PARQUET_REVISION, version: str = "v1"):
        if language not in CONFIGS:
            raise ValueError(
                f"FLEURS (in-scope) has no config for '{language}'. "
                f"Available: {sorted(CONFIGS)}")
        if task not in (None, "asr"):
            raise ValueError("FLEURS is used for ASR eval only")
        self.language = language
        self.task = "asr"
        self.code = CONFIGS[language]
        self.revision = revision
        self.version = version
        self.config = f"{self.code}_fleurs"
        self.spec = SourceSpec(
            source_id="fleurs",
            dataset_release=f"{REPO}@{SOURCE_REVISION[:12]}#{self.code}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_eval"],           # eval only — never training
            consent_id="dataset-level:fleurs",
        )
        self.tier = "eval_only"

    def items(self, limit: int | None = None) -> Iterator[dict]:
        from huggingface_hub import get_token
        token = get_token()
        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = __import__("pathlib").Path(sd.name)
        self._spill_dir = sd
        produced = 0
        for split in SPLITS:
            if limit and produced >= limit:
                return
            shards = gc.list_shards(
                REPO, self.revision, f"{self.code}/{split}/*.parquet", token=token)
            if not shards:
                continue
            for p in gc.read_parquet_audio_shards(
                    REPO, self.revision, shards, split=split, spill=spill,
                    token=token,
                    limit=(None if limit is None else limit - produced)):
                spk = f"{self.code}_fleurs"          # FLEURS has no speaker ids
                rec = build_record(
                    audio_uri=f"s3://medzen-speech/eval/{self.language}/{self.task}/{self.version}/audio/{p['stem']}.wav",
                    audio_sha256=p["wav_sha256"],
                    duration_s=p["duration_s"], sample_rate=TARGET_SR, channels=1,
                    text_verbatim=p["text"], language=self.language,
                    speaker_id=spk, session_id=f"{self.code}_{p['stem']}",
                    split=split, spec=self.spec, split_strategy="frozen_eval",
                    gender=gc.norm_gender(p["gender_raw"]), domain="asr",
                    license_tier=self.tier, dialect=self.code,
                    raw_filepath=f"s3://medzen-speech/eval/{self.language}/{self.task}/{self.version}/raw/{p['stem']}.{p['raw_ext']}",
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


class FleursTrainAdapter(FleursAdapter):
    """FLEURS TRAIN split for training (Tier A, owner-ordered 2026-08-18).

    The eval reservation above stays intact: the frozen fleurs-v1 eval sets
    are built from test+validation, and this adapter touches ONLY the train
    split — disjoint sentences by FLEURS's own construction, and the gb
    curation eval-leak byte check verifies that independently at assembly
    (it caught 128 leaked rows in gb4). Licence truth is unchanged
    (CC-BY-4.0 -> commercial_ok, attribution carried in the manifest).
    Ingest with --no-eval-split: all rows land as train in
    curated/{language}/asr/{code}_fleurs_train/.
    """

    name = "fleurs_train"

    def __init__(self, language: str, task: str | None = None,
                 revision: str = PARQUET_REVISION, version: str = "v1"):
        super().__init__(language, task=task, revision=revision,
                         version=version)
        self.config = f"{self.code}_fleurs_train"
        self.spec = SourceSpec(
            source_id="fleurs_train",
            dataset_release=f"{REPO}@{SOURCE_REVISION[:12]}#{self.code}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train"],
            consent_id="dataset-level:fleurs",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)

    def items(self, limit: int | None = None) -> Iterator[dict]:
        from huggingface_hub import get_token
        token = get_token()
        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = __import__("pathlib").Path(sd.name)
        self._spill_dir = sd
        produced = 0
        shards = gc.list_shards(
            REPO, self.revision, f"{self.code}/train/*.parquet", token=token)
        if not shards:
            raise ValueError(f"FLEURS has no train shards for {self.code}")
        for p in gc.read_parquet_audio_shards(
                REPO, self.revision, shards, split="train", spill=spill,
                token=token, limit=limit):
            spk = f"{self.code}_fleurs"          # FLEURS has no speaker ids
            rec = build_record(
                audio_uri=f"s3://medzen-speech/curated/{base}/audio/{p['stem']}.wav",
                audio_sha256=p["wav_sha256"],
                duration_s=p["duration_s"], sample_rate=TARGET_SR, channels=1,
                text_verbatim=p["text"], language=self.language,
                speaker_id=spk, session_id=f"{self.code}_{p['stem']}",
                split="train", spec=self.spec, split_strategy="text_disjoint",
                gender=gc.norm_gender(p["gender_raw"]), domain="asr",
                license_tier=self.tier, dialect=self.code,
                raw_filepath=f"s3://medzen-speech/raw/{base}/{p['stem']}.{p['raw_ext']}",
                raw_checksum_sha256=p["raw_sha256"],
            )
            yield {"record": rec, "raw_path": p["raw_path"],
                   "wav_path": p["wav_path"], "raw_ext": p["raw_ext"],
                   "stem": p["stem"]}
            produced += 1
