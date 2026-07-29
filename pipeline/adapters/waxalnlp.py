"""WaxalNLP adapter. Licence varies BY PROVIDER, not by dataset — the spec is
built per provider so a row's licence_policy is always the correct one."""
from __future__ import annotations

import hashlib
from typing import Iterator

from .base import TARGET_SR, Adapter, SourceSpec, build_record, usable

# provider -> commercial status, from registry/sources.yaml (verified)
PROVIDER_LICENCE = {
    "makerere": "sharealike_review",
    "university_ghana": "commercial_ok",      # CC-BY-4.0, the cleanest here
    "digital_umuganda": "sharealike_review",
    "media_trust": "sharealike_review",
    "loud_and_clear": "sharealike_review",
    "aims_senegal": "sharealike_review",
}

# language alias -> (hf config, provider, task)
CONFIGS = {
    "pidgin": ("pcm_tts", "media_trust", "tts"),
    "igbo":   ("ibo_tts", "media_trust", "tts"),
    "hausa":  ("hau_tts", "media_trust", "tts"),
    "yoruba": ("yor_tts", "media_trust", "tts"),
    "swahili": ("swa_tts", "loud_and_clear", "tts"),
    "wolof":  ("wol_tts", "aims_senegal", "tts"),
    "akan":   ("aka_asr", "university_ghana", "asr"),
    "ewe":    ("ewe_asr", "university_ghana", "asr"),
    "luganda": ("lug_asr", "makerere", "asr"),
    "acholi": ("ach_asr", "makerere", "asr"),
    "amharic": ("amh_asr", "digital_umuganda", "asr"),
    "oromo":  ("orm_asr", "digital_umuganda", "asr"),
    "lingala": ("lin_asr", "digital_umuganda", "asr"),
    "shona":  ("sna_asr", "digital_umuganda", "asr"),
    "fula":   ("ful_asr", "digital_umuganda", "asr"),
}


class WaxalNLPAdapter:
    name = "waxalnlp"

    def __init__(self, language: str, release: str = "google/WaxalNLP@main"):
        if language not in CONFIGS:
            raise ValueError(f"WaxalNLP has no config for '{language}'. "
                             f"Known: {sorted(CONFIGS)}")
        self.language = language
        self.config, self.provider, self.task = CONFIGS[language]
        self.spec = SourceSpec(
            source_id=f"waxalnlp/{self.provider}",
            dataset_release=f"{release}#{self.config}",
            license_policy=PROVIDER_LICENCE[self.provider],
            allowed_use=["tts_train", "tts_eval"] if self.task == "tts"
                        else ["asr_train", "asr_eval"],
            consent_id=f"dataset-level:waxalnlp/{self.provider}",
        )

    def rows(self, language: str | None = None, limit: int | None = None) -> Iterator[dict]:
        """Decode with soundfile rather than the datasets Audio feature.

        datasets>=5 routes Audio decoding through torchcodec, which drags in
        torch. We only need PCM samples, so we take the raw bytes
        (Audio(decode=False)) and decode them ourselves — lighter, and it makes
        the 16 kHz resample explicit rather than implicit.
        """
        import io

        import librosa
        import numpy as np
        import soundfile as sf
        from datasets import Audio, load_dataset

        ds = load_dataset("google/WaxalNLP", self.config, split="train", streaming=True)
        ds = ds.cast_column("audio", Audio(decode=False))

        n = 0
        for i, row in enumerate(ds):
            if limit and n >= limit:
                break
            blob = (row.get("audio") or {}).get("bytes")
            text = (row.get("text") or row.get("sentence")
                    or row.get("transcription") or "")
            if not blob or not text:
                continue
            try:
                arr, sr = sf.read(io.BytesIO(blob), dtype="float32", always_2d=False)
            except Exception:
                continue
            if arr.ndim > 1:                       # downmix to mono
                arr = arr.mean(axis=1)
            if sr != TARGET_SR:                    # A3: resample BEFORE anything else
                arr = librosa.resample(arr, orig_sr=sr, target_sr=TARGET_SR)
            dur = len(arr) / TARGET_SR
            if not usable(dur, text):
                continue

            # Hash the SOURCE bytes, not the resampled samples: the checksum
            # then identifies the artifact we actually ingested.
            sha = hashlib.sha256(blob).hexdigest()
            spk = str(row.get("speaker_id") or row.get("client_id") or f"{self.config}_spk0")
            stem = (row.get("id") or f"{i:07d}")
            yield build_record(
                audio_uri=f"s3://medzen-speech/raw/{self.language}/{self.config}/{stem}.wav",
                audio_sha256=sha, duration_s=dur, sample_rate=TARGET_SR, channels=1,
                text_verbatim=text, language=self.language,
                speaker_id=spk, session_id=f"{self.config}_{spk}",
                split="train", spec=self.spec,
                split_strategy=("text_disjoint" if self.task == "tts" else "speaker_disjoint"),
                gender=(row.get("gender") or "").lower()[:1].replace("m", "m").replace("f", "f") or None,
                domain=self.task,
            )
            n += 1
