"""Common Voice 26.0 — Cameroonian languages (CC0, permissive tier).

Cameroon pilot (owner directive 2026-09-03). Source is the public CC0 mirror
`Peacockery/common-voice-scripted-speech-26` of the Mozilla Data Collective
release, pinned by revision. Only the four pilot languages are wired.

The splits are NOT taken from Common Voice. CV's own train/dev/test assign one
clip per sentence and cover 6.7% of validated audio; and a speaker-disjoint
split alone still leaks, because each sentence is recorded 7-12 times. This
adapter therefore consumes a precomputed split file that is disjoint on BOTH
speaker and prompt, and discards every cross cell. The split builder and its
leakage assertions live beside the split files.

Audio audit applied before splitting: clips with >=6 consecutive saturated
samples (real clipping, not mp3 overshoot) and clips whose first or last 30 ms
carry >50% of body RMS (truncated edge) are excluded.
"""
from __future__ import annotations

import glob
import hashlib
import io
import json
import os
from typing import Iterator

from .base import TARGET_SR, SourceSpec, build_record, usable

REPO = "Peacockery/common-voice-scripted-speech-26"
REVISION = "b4d8b94d43831475de59a455345acf6945cfd66e"   # pinned 2026-09-03
LICENSE_POLICY = "cc0"

LANGS = {"ngiemboon": "nnh", "ngombala": "nla", "yangben": "yav", "gbaya": "gya"}

# Where the qualified parquet and the audited split files live. Set by the
# operator; no default, because this adapter must never guess at data location.
DATA_ROOT_ENV = "MEDZEN_CV26_CM_ROOT"


class CommonVoice26CameroonAdapter:
    name = "common_voice_cm"

    def __init__(self, language: str, task: str | None = None,
                 revision: str = REVISION, version: str = "v1"):
        if language not in LANGS:
            raise ValueError(
                f"common_voice_cm has no data for {language!r}. "
                f"Available: {sorted(LANGS)}")
        if task not in (None, "asr"):
            raise ValueError("common_voice_cm is ASR-only")
        self.language = language
        self.code = LANGS[language]
        self.task = "asr"
        self.version = version
        self.config = f"cv26_{LANGS[language]}"
        self.revision = revision
        self.root = os.environ.get(DATA_ROOT_ENV, "").strip()
        if not self.root:
            raise ValueError(f"{DATA_ROOT_ENV} must point at the qualified data root")
        self.spec = SourceSpec(
            source_id=f"common_voice/cv26-cm/{self.code}",
            dataset_release=f"{REPO}@{revision}#{self.code}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train", "asr_eval"],
            consent_id="dataset-level:mozilla_common_voice_cc0",
        )

    def _splits(self) -> dict[str, tuple[str, dict]]:
        wanted: dict[str, tuple[str, dict]] = {}
        # ingest.assign_splits would RE-SPLIT by speaker and destroy the
        # prompt-disjointness, so each split is ingested in its own pass:
        # train with --no-eval-split into curated/, test with
        # MEDZEN_EVAL_ONLY=1 into eval/. This selects the pass.
        only = os.environ.get("MEDZEN_CV26_CM_SPLIT", "").strip()
        for split in ([only] if only else ["train", "dev", "test"]):
            path = f"{self.root}/asplit_{self.code}_{split}.jsonl"
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        row = json.loads(line)
                        wanted[row["path"]] = (split, row)
        if not wanted:
            raise ValueError(f"no split rows for {self.language}")
        return wanted

    def items(self, limit: int | None = None) -> Iterator[dict]:
        import numpy as np
        import pyarrow.parquet as pq
        import soundfile as sf
        try:
            import librosa
        except ImportError as exc:                       # pragma: no cover
            raise ValueError("librosa is required to resample CV26 audio") from exc

        wanted = self._splits()
        matches = glob.glob(
            f"{self.root}/raw/data/validated/*__{self.code}__*.parquet")
        if len(matches) != 1:
            raise ValueError(
                f"expected exactly one validated parquet for {self.code}, "
                f"found {len(matches)}")
        stage = f"{self.root}/stage/{self.code}"
        os.makedirs(stage, exist_ok=True)
        base = f"{self.language}/{self.task}/{self.config}"
        emitted = 0
        reader = pq.ParquetFile(matches[0])
        for batch in reader.iter_batches(batch_size=200):
            data = batch.to_pydict()
            for index in range(len(data["path"])):
                name = data["path"][index]
                if name not in wanted:
                    continue
                split, meta = wanted[name]
                raw = data["audio"][index]["bytes"]
                audio, rate = sf.read(io.BytesIO(raw), dtype="float32",
                                      always_2d=True)
                mono = np.clip(audio.mean(axis=1), -1.0, 1.0)
                if rate != TARGET_SR:
                    mono = librosa.resample(mono, orig_sr=rate,
                                            target_sr=TARGET_SR)
                duration = len(mono) / TARGET_SR
                text = meta["sentence"] or ""
                if not usable(duration, text):
                    continue
                stem = name[:-4] if name.endswith(".mp3") else name
                raw_path = f"{stage}/{stem}.mp3"
                wav_path = f"{stage}/{stem}.wav"
                with open(raw_path, "wb") as handle:
                    handle.write(raw)
                sf.write(wav_path, mono, TARGET_SR, subtype="PCM_16")
                with open(wav_path, "rb") as handle:
                    wav = handle.read()
                speaker = f"{self.code}_{meta['client_id'][:16]}"
                record = build_record(
                    audio_uri=(f"s3://medzen-speech/curated/{base}/"
                               f"{self.version}/audio/{stem}.wav"),
                    audio_sha256=hashlib.sha256(wav).hexdigest(),
                    duration_s=duration, sample_rate=TARGET_SR, channels=1,
                    text_verbatim=text, language=self.language,
                    speaker_id=speaker, session_id=speaker,
                    split=split, spec=self.spec,
                    # the schema enum offers speaker_disjoint or text_disjoint;
                    # these splits are BOTH, enforced by the split builder's
                    # assertions, which is stricter than either label alone
                    split_strategy="speaker_disjoint",
                    dialect=f"{self.code}_cm", domain="asr",
                    license_tier="permissive",
                    raw_filepath=f"s3://medzen-speech/raw/{base}/{stem}.mp3",
                    raw_checksum_sha256=hashlib.sha256(raw).hexdigest(),
                )
                yield {"record": record, "raw_path": raw_path,
                       "wav_path": wav_path, "raw_ext": "mp3", "stem": stem}
                emitted += 1
                if limit is not None and emitted >= limit:
                    return

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
