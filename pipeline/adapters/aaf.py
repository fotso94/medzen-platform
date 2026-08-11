"""African Accented French (OpenSLR #57) adapter — Apache-2.0 (permissive).

Owner-supplied archive extracted at $MEDZEN_AAF_DIR. ~13.5k transcribed
utterances of African-accented French: yaounde (Cameroon, 8.4k — the project's
Cameroon-French seed), ca16 read+conv (Cameroon/Gabon/...), niger dev set,
ca16 test prompts. Licence verified at openslr.org/57 on 2026-08-11.

Config = sub-corpus. Splits: train/dev/devtest sub-corpora ingest as train
(--no-eval-split -> curated/french/asr/aaf_<cfg>/gb1/); the ca16 test prompts
ingest separately with MEDZEN_EVAL_ONLY=1 -> eval/french/asr/aaf-test-v1/.
Transcript lines are `<id-or-path> <text>`; audio matched by basename against
a one-time index of speech/.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
from pathlib import Path
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, SourceSpec, build_record, usable

LICENSE_POLICY = "commercial_ok"        # Apache-2.0 (openslr-57); canonical tier key
RELEASE = "openslr-57-african-accented-french"

# config -> (transcript relpath, id column has .wav suffix?, default split)
CONFIGS = {
    "yaounde":    ("transcripts/train/yaounde/fn_text.txt",              "train"),
    "ca16_read":  ("transcripts/train/ca16_read/conditioned.txt",        "train"),
    "ca16_conv":  ("transcripts/train/ca16_conv/transcripts.txt",        "train"),
    "niger_dev":  ("transcripts/dev/niger_west_african_fr/transcripts.txt", "train"),
    "ca16_devtest": ("transcripts/devtest/ca16_read/conditioned.txt",    "train"),
    "ca16_test":  ("transcripts/test/ca16/prompts.txt",                  "test"),
}

_COUNTRY = re.compile(r"(cameroon|gabon|niger|chad|congo|benin|burkina|mali|senegal)", re.I)


def _country_of(basename: str, cfg: str) -> str:
    m = _COUNTRY.search(basename)
    if m:
        return m.group(1).lower()
    if cfg == "yaounde":
        return "cameroon"
    if cfg.startswith("niger"):
        return "niger"
    return "african"


class AAFAdapter:
    name = "aaf"

    def __init__(self, language: str, task: str | None = None,
                 version: str = "gb1", config: str | None = None):
        if language != "french":
            raise ValueError("AAF is French-only")
        if task not in (None, "asr"):
            raise ValueError("AAF is ASR-only")
        cfg = config or os.environ.get("MEDZEN_AAF_CONFIG")
        if cfg not in CONFIGS:
            raise ValueError(f"MEDZEN_AAF_CONFIG must be one of {sorted(CONFIGS)}")
        self.language = "french"
        self.task = "asr"
        self.cfg = cfg
        self.version = version
        self.config = f"aaf_{cfg}"
        self.root = Path(os.environ["MEDZEN_AAF_DIR"])
        self.spec = SourceSpec(
            source_id="aaf",
            dataset_release=f"{RELEASE}#{cfg}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train", "asr_eval"],
            consent_id="dataset-level:openslr57_apache_2_0",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)

    def items(self, limit: int | None = None) -> Iterator[dict]:
        import librosa
        import soundfile as sf

        tpath, split = CONFIGS[self.cfg]
        # one-time basename -> path index of all audio
        index: dict[str, Path] = {}
        for p in (self.root / "speech").rglob("*.wav"):
            index[p.name] = p

        base = f"{self.language}/{self.task}/{self.config}"
        prefix = "eval" if split == "test" else "curated"
        sd = gc.spill_dir()
        spill = Path(sd.name)
        self._spill_dir = sd
        produced = 0
        for line in (self.root / tpath).read_text(encoding="utf-8",
                                                  errors="replace").splitlines():
            if limit and produced >= limit:
                return
            parts = line.strip().split(None, 1)
            if len(parts) != 2:
                continue
            fid, text = parts[0], parts[1].strip()
            bname = fid.rsplit("/", 1)[-1]
            bname = re.sub(r"\.(tdf|wav)$", "", bname) + ".wav"
            src = index.get(bname)
            if src is None or not text:
                continue
            try:
                arr, sr = sf.read(str(src), dtype="float32", always_2d=False)
            except Exception:
                continue
            if getattr(arr, "ndim", 1) > 1:
                arr = arr.mean(axis=1)
            if sr != TARGET_SR:
                arr = librosa.resample(arr, orig_sr=sr, target_sr=TARGET_SR)
            dur = len(arr) / TARGET_SR
            if not usable(dur, text):
                continue
            buf = io.BytesIO()
            sf.write(buf, arr, TARGET_SR, format="WAV", subtype="PCM_16")
            wav = buf.getvalue()
            raw = src.read_bytes()
            stem = (bname[:-4] + "_" + hashlib.sha256(wav).hexdigest()[:12])
            spk_m = re.match(r"(.+?)_\d+$", bname[:-4])
            spk = spk_m.group(1) if spk_m else bname[:-4]
            rp = spill / f"{stem}.raw"; wp = spill / f"{stem}.wav"
            rp.write_bytes(raw); wp.write_bytes(wav)
            rec = build_record(
                audio_uri=f"s3://medzen-speech/{prefix}/{base}/{self.version}/audio/{stem}.wav",
                audio_sha256=hashlib.sha256(wav).hexdigest(),
                duration_s=dur, sample_rate=TARGET_SR, channels=1,
                text_verbatim=text, language=self.language,
                speaker_id=spk, session_id=f"{self.cfg}_{spk}",
                split=split, spec=self.spec, split_strategy="speaker_disjoint",
                domain="asr", license_tier=self.tier,
                dialect=f"fr-{_country_of(bname, self.cfg)}",
                raw_filepath=f"s3://medzen-speech/raw/{base}/{stem}.wav",
                raw_checksum_sha256=hashlib.sha256(raw).hexdigest(),
            )
            yield {"record": rec, "raw_path": rp, "wav_path": wp,
                   "raw_ext": "wav", "stem": stem}
            produced += 1

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
