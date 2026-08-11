"""LRSC adapter — Lingala Read Speech Corpus (owner-supplied archive).

Fairseq-style layout at $MEDZEN_LRSC_DIR (LRSC/lingala/):
  manifest/{train,valid}.tsv   line 1 = audio root (ignored; we resolve
                               locally), then `<wav>\t<frames>` per row
  manifest/{train,valid}.wrd   word transcripts, line-aligned with tsv rows
  {train,valid}/audio/*.wav    the audio

Provenance: the CdWav2Vec Congolese ASR work (public research corpus);
archive supplied by the platform owner 2026-08-11 with no licence file
inside — recorded as commercial_ok on owner authority (see
ingest_results.yaml note). Both splits ingest as train (--no-eval-split);
lingala eval stays fleurs-v1 + soreva-v1.

Speaker ids: filenames look like `gina_220716-..._lin_9f7_elicit_80.wav` —
token before the first '_' plus the `lin_<id>` token give a stable
speaker key.
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

LICENSE_POLICY = "commercial_ok"    # owner authority; provenance CdWav2Vec/LRSC
RELEASE = "owner-supplied-lrsc-cdwav2vec-2026-08-11"
SPLITS = ("train", "valid")


def _speaker(fname: str) -> str:
    m = re.search(r"_lin_([0-9a-z]+)_", fname)
    tok = fname.split("_", 1)[0]
    return f"{tok}_{m.group(1)}" if m else tok


class LRSCAdapter:
    name = "lrsc"

    def __init__(self, language: str, task: str | None = None,
                 version: str = "gb1"):
        if language != "lingala":
            raise ValueError("LRSC is Lingala-only")
        if task not in (None, "asr"):
            raise ValueError("ASR-only")
        self.language = "lingala"
        self.task = "asr"
        self.version = version
        self.config = "lrsc"
        self.root = Path(os.environ["MEDZEN_LRSC_DIR"])
        self.spec = SourceSpec(
            source_id="lrsc",
            dataset_release=RELEASE,
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train"],
            consent_id="owner-supplied:lrsc_2026_08_11",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)

    def items(self, limit: int | None = None) -> Iterator[dict]:
        import librosa
        import soundfile as sf

        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = Path(sd.name)
        self._spill_dir = sd
        produced = 0
        for split in SPLITS:
            tsv = (self.root / "manifest" / f"{split}.tsv").read_text(
                encoding="utf-8", errors="replace").splitlines()
            wrd = (self.root / "manifest" / f"{split}.wrd").read_text(
                encoding="utf-8", errors="replace").splitlines()
            rows = tsv[1:]                      # line 1 is the audio root
            audio_dir = self.root / split / "audio"
            for i, line in enumerate(rows):
                if limit and produced >= limit:
                    return
                parts = line.strip().split("\t")
                if not parts or not parts[0]:
                    continue
                fname = parts[0]
                text = wrd[i].strip() if i < len(wrd) else ""
                src = audio_dir / fname
                if not text or not src.exists():
                    continue
                try:
                    arr, sr = sf.read(str(src), dtype="float32",
                                      always_2d=False)
                except Exception:
                    continue
                if getattr(arr, "ndim", 1) > 1:
                    arr = arr.mean(axis=1)
                if sr != TARGET_SR:
                    arr = librosa.resample(arr, orig_sr=sr,
                                           target_sr=TARGET_SR)
                dur = len(arr) / TARGET_SR
                if not usable(dur, text):
                    continue
                buf = io.BytesIO()
                sf.write(buf, arr, TARGET_SR, format="WAV", subtype="PCM_16")
                wav = buf.getvalue()
                raw = src.read_bytes()
                stem = (src.stem + "_" + hashlib.sha256(wav).hexdigest()[:12])
                spk = _speaker(src.stem)
                rp = spill / f"{stem}.raw"; wp = spill / f"{stem}.wav"
                rp.write_bytes(raw); wp.write_bytes(wav)
                rec = build_record(
                    audio_uri=f"s3://medzen-speech/curated/{base}/{self.version}/audio/{stem}.wav",
                    audio_sha256=hashlib.sha256(wav).hexdigest(),
                    duration_s=dur, sample_rate=TARGET_SR, channels=1,
                    text_verbatim=text, language=self.language,
                    speaker_id=f"lrsc_{spk}", session_id=f"lrsc_{split}_{spk}",
                    split="train", spec=self.spec,
                    split_strategy="speaker_disjoint",
                    domain="asr", license_tier=self.tier, dialect="ln-cd",
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
