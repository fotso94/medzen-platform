"""Kallaama adapter — CC-BY-4.0 (permissive tier). Wolof + Fula(Pulaar).

Kallaama is NOT on Hugging Face — it is a Zenodo/OpenSLR archive of spontaneous
agricultural radio speech with TIMESTAMPED transcriptions (long recordings, not
pre-cut utterances). It is downloaded and extracted OUT OF BAND (Phase 2), and
this adapter reads from the extracted tree on local disk.

IMPORTANT — layout confirmed AT FETCH. The exact transcription format (TextGrid
vs CSV) is finalised when the archive is first extracted. To keep segmentation
explicit and reviewable, Phase 2's extract step normalises Kallaama into a single
segments TSV per language:

    <root>/<lang>/segments.tsv   with columns:
        wav_path  start_s  end_s  text  speaker_id  session_id

This adapter reads that TSV, cuts each segment from its wav, resamples to 16 kHz
mono, and emits A3 rows. If the TSV is absent it raises with the expected layout
rather than guessing. Set the extract root via MEDZEN_KALLAAMA_DIR.

SMOKE TEST (after extract): `python -m pipeline.ingest --source kallaama
--language wolof --dry-run --limit 20`.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import pathlib
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, MIN_S, MAX_S, SourceSpec, build_record, usable

SOURCE = "OpenSLR-151 / Zenodo-10892569"
LICENSE_POLICY = "cc_by_4_0"               # CC-BY-4.0 -> permissive
LANGS = {"wolof": ("wol", None), "fula": ("fuf", "pulaar"),
         # 2026-08-11 owner-supplied full release: serer is NEW; pulaar was
         # dropped in the campaign for space and now enters (fuc, distinct
         # from the WAXAL "fula" fuf material).
         "serer": ("srr", None), "pulaar": ("fuc", None)}
ENV_ROOT = "MEDZEN_KALLAAMA_DIR"


class KallaamaAdapter:
    name = "kallaama"

    def __init__(self, language: str, task: str | None = None,
                 revision: str = "zenodo_10892569", version: str = "v1"):
        if language not in LANGS:
            raise ValueError(
                f"Kallaama has no data for '{language}'. Available: {sorted(LANGS)}")
        if task not in (None, "asr"):
            raise ValueError("Kallaama is ASR-only")
        self.language = language
        self.task = "asr"
        self.code, self.dialect = LANGS[language]
        self.revision = revision
        self.version = version
        self.config = f"kallaama_{self.code}"
        self.spec = SourceSpec(
            source_id="kallaama",
            dataset_release=f"{SOURCE}@{revision}#{self.code}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train", "asr_eval"],
            consent_id="dataset-level:kallaama",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)    # -> permissive

    def _segments_tsv(self) -> pathlib.Path:
        root = os.environ.get(ENV_ROOT)
        if not root:
            raise RuntimeError(
                f"Set {ENV_ROOT} to the extracted Kallaama root "
                f"(expected <root>/{self.language}/segments.tsv).")
        tsv = pathlib.Path(root) / self.language / "segments.tsv"
        if not tsv.is_file():
            raise RuntimeError(
                f"Expected Kallaama segments TSV at {tsv} (columns: "
                "wav_path start_s end_s text speaker_id session_id). "
                "Run the Phase-2 extract step first.")
        return tsv

    def items(self, limit: int | None = None) -> Iterator[dict]:
        import librosa
        import soundfile as sf

        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = pathlib.Path(sd.name)
        self._spill_dir = sd
        tsv = self._segments_tsv()

        # Group segments by source recording. Kallaama recordings are long radio
        # files (20+ min), so each is read from disk ONCE and all its segments are
        # cut from the in-memory array — never re-read per segment.
        by_wav: dict[str, list[dict]] = {}
        with tsv.open() as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                by_wav.setdefault(r["wav_path"], []).append(r)

        produced = 0
        for wav_path in sorted(by_wav):
            if limit and produced >= limit:
                return
            wav_src = pathlib.Path(wav_path)
            if not wav_src.is_absolute():
                wav_src = tsv.parent / wav_src
            try:
                full, sr = sf.read(str(wav_src), dtype="float32", always_2d=False)
            except Exception:
                continue
            if getattr(full, "ndim", 1) > 1:
                full = full.mean(axis=1)
            for r in by_wav[wav_path]:
                if limit and produced >= limit:
                    return
                text = (r.get("text") or "").strip()
                try:
                    start, end = float(r["start_s"]), float(r["end_s"])
                except (KeyError, ValueError):
                    continue
                if not (MIN_S <= (end - start) <= MAX_S) or len(text) <= 3:
                    continue
                seg = full[int(start * sr):int(end * sr)]
                if sr != TARGET_SR:
                    seg = librosa.resample(seg, orig_sr=sr, target_sr=TARGET_SR)
                dur = len(seg) / TARGET_SR
                if not usable(dur, text):
                    continue
                buf = io.BytesIO()
                sf.write(buf, seg, TARGET_SR, format="WAV", subtype="PCM_16")
                wav = buf.getvalue()
                stem = f"{wav_src.stem}_{int(start*1000):08d}"
                spk = str(r.get("speaker_id") or f"{self.code}_unknown").strip()
                sess = str(r.get("session_id") or wav_src.stem)
                wp = spill / f"{stem}.wav"
                wp.write_bytes(wav)
                rec = build_record(
                    audio_uri=f"s3://medzen-speech/curated/{base}/{self.version}/audio/{stem}.wav",
                    audio_sha256=hashlib.sha256(wav).hexdigest(),
                    duration_s=dur, sample_rate=TARGET_SR, channels=1,
                    text_verbatim=text, language=self.language,
                    speaker_id=spk, session_id=sess, split="train",
                    spec=self.spec, split_strategy="speaker_disjoint",
                    gender="unknown", domain="asr",
                    license_tier=self.tier,
                    dialect=(self.dialect or self.code),
                    raw_filepath=f"s3://medzen-speech/raw/{base}/{wav_src.name}",
                    raw_checksum_sha256=hashlib.sha256(wav).hexdigest(),
                )
                yield {"record": rec, "raw_path": wp, "wav_path": wp,
                       "raw_ext": "wav", "stem": stem}
                produced += 1

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
