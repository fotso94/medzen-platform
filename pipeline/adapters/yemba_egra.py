"""Yemba EGRA adapter — owner-supplied Cameroonian word-level speech.

Owner-supplied archive extracted at $MEDZEN_YEMBA_EGRA_DIR (YembaEGRA/):
8,033 single-word wavs by ~70 child speakers (EGRA reading assessments),
word transcripts in metadata/words_corpus .csv (Yemba column), speaker
gender/age in metadata/speakers_description .csv.

Provenance: supplied directly by the platform owner 2026-08-11 with a
directive to ingest for training; no licence document ships inside the
archive — licence/consent documentation responsibility rests with the owner
(recorded as license_policy=commercial_ok on owner authority; see
ingest_results.yaml note).

Filename grammar: spkr_<S>_word_<W>_occ_<O>_ci_<C>_l_<L>.wav ->
speaker S, word id W. All rows -> train (use --no-eval-split); SOREVA
soreva-v1 remains the frozen Yemba eval.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from pathlib import Path
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, SourceSpec, build_record, usable

LICENSE_POLICY = "commercial_ok"          # owner authority; see module docstring
RELEASE = "owner-supplied-yemba-egra-2026-08-11"
_FNAME = re.compile(r"spkr_(\d+)_word_(\d+)_occ_(\d+)_ci_(\d+)")


class YembaEGRAAdapter:
    name = "yemba_egra"

    def __init__(self, language: str, task: str | None = None,
                 version: str = "gb1"):
        if language != "yemba":
            raise ValueError("this adapter is Yemba-only")
        if task not in (None, "asr"):
            raise ValueError("ASR-only")
        self.language = "yemba"
        self.task = "asr"
        self.version = version
        self.config = "egra"
        self.root = Path(os.environ["MEDZEN_YEMBA_EGRA_DIR"])
        self.spec = SourceSpec(
            source_id="yemba_egra",
            dataset_release=RELEASE,
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train"],
            consent_id="owner-supplied:yemba_egra_2026_08_11",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)

    def _words(self) -> dict[str, str]:
        path = next(self.root.glob("metadata/words_corpus*.csv"))
        out: dict[str, str] = {}
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                wid = (row.get("id_word") or "").strip()
                text = (row.get("Yemba") or "").strip()
                if wid and text:
                    out[wid] = text
        return out

    def _speakers(self) -> dict[str, str]:
        path = next(self.root.glob("metadata/speakers_description*.csv"))
        out: dict[str, str] = {}
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                sid = (row.get("SpeakerId") or "").strip()
                if sid:
                    out[sid] = (row.get("Gender") or "").strip().lower()
        return out

    def items(self, limit: int | None = None) -> Iterator[dict]:
        import librosa
        import soundfile as sf

        words = self._words()
        genders = self._speakers()
        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = Path(sd.name)
        self._spill_dir = sd
        produced = 0
        for src in sorted(self.root.rglob("audio/**/*.wav")):
            if limit and produced >= limit:
                return
            m = _FNAME.search(src.name)
            if not m:
                continue
            spk, wid = m.group(1), m.group(2)
            text = words.get(wid)
            if not text:
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
            # word-level domain: the A3 floor is 0.3 s for domain=asr_words
            # (validator change 2026-08-11); anything shorter is a truncated
            # or empty take and is dropped.
            if not (0.3 <= dur <= 30.0 and text):
                continue
            buf = io.BytesIO()
            sf.write(buf, arr, TARGET_SR, format="WAV", subtype="PCM_16")
            wav = buf.getvalue()
            raw = src.read_bytes()
            stem = (src.stem + "_" + hashlib.sha256(wav).hexdigest()[:12])
            rp = spill / f"{stem}.raw"; wp = spill / f"{stem}.wav"
            rp.write_bytes(raw); wp.write_bytes(wav)
            rec = build_record(
                audio_uri=f"s3://medzen-speech/curated/{base}/{self.version}/audio/{stem}.wav",
                audio_sha256=hashlib.sha256(wav).hexdigest(),
                duration_s=dur, sample_rate=TARGET_SR, channels=1,
                text_verbatim=text, language=self.language,
                speaker_id=f"egra_{spk}", session_id=f"egra_{spk}",
                split="train", spec=self.spec, split_strategy="speaker_disjoint",
                gender=gc.norm_gender(genders.get(spk)), domain="asr_words",
                license_tier=self.tier, dialect="yemba-egra-child",
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
