"""NCHLT speech corpora adapter — SADiLaR, CC BY 3.0 (attribution tier).

Licence VERIFIED 2026-08-18 at repo.sadilar.org (Sepedi: handle
20.500.12185/270, "Creative Commons Attribution 3.0 Unported License",
~56 h broadband speech, nchlt.speech.corpus.nso.zip). cc_by_3_0 was
admitted to TRAIN_ATTRIBUTION in the same change — attribution follows
the rows into every consuming manifest.

The zip is downloaded and extracted OUT OF BAND (kallaama pattern); this
adapter reads the extracted tree at $MEDZEN_NCHLT_DIR. Only the TRAIN
transcription set (nchlt_{iso}.trn.xml) is ingested — the 8-speaker test
suite stays out: sepedi's frozen evaluation is the soreva_nso_za set, and
benchmark material must not leak into training.

Layout (verified at first extract, 2026-08-18):
    <root>/nchlt_{iso}/audio/{spk}/*.wav
    <root>/nchlt_{iso}/transcriptions/nchlt_{iso}.trn.xml
XML: <corpus><speaker id=.. gender=..><recording audio=<path relative to
extract root> duration=..><orth>TEXT</orth></recording>...</speaker>...

SMOKE TEST: `MEDZEN_NCHLT_DIR=... python -m pipeline.ingest --source nchlt
--language sepedi --dry-run --limit 20`.
"""
from __future__ import annotations

import hashlib
import io
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, MIN_S, MAX_S, SourceSpec, build_record

LICENSE_POLICY = "cc_by_3_0"
LANGS = {"sepedi": "nso"}
RELEASE = "sadilar/nchlt-speech@20.500.12185"


def usable(duration_s: float, text: str) -> bool:
    return MIN_S <= duration_s <= MAX_S and bool(text.strip())


class NCHLTAdapter:
    name = "nchlt"

    def __init__(self, language: str, task: str | None = None,
                 version: str = "v1"):
        if language not in LANGS:
            raise ValueError(
                f"NCHLT (in-scope) has no corpus wired for '{language}'. "
                f"Available: {sorted(LANGS)}")
        if task not in (None, "asr"):
            raise ValueError("NCHLT is ASR-only here")
        root = os.environ.get("MEDZEN_NCHLT_DIR")
        if not root:
            raise ValueError("set MEDZEN_NCHLT_DIR to the extracted corpus root")
        self.language = language
        self.task = "asr"
        self.iso = LANGS[language]
        self.version = version
        self.config = f"nchlt_{self.iso}"
        self.root = Path(root)
        self.spec = SourceSpec(
            source_id="nchlt",
            dataset_release=f"{RELEASE}#{self.iso}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train"],
            consent_id="dataset-level:nchlt_cc_by_3_0",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)

    def _trn_xml(self) -> Path:
        hits = sorted(self.root.rglob(f"nchlt_{self.iso}.trn.xml"))
        if not hits:
            raise ValueError(
                f"no nchlt_{self.iso}.trn.xml under {self.root} — expected "
                f"<root>/nchlt_{self.iso}/transcriptions/nchlt_{self.iso}.trn.xml; "
                "extract the SADiLaR zip and point MEDZEN_NCHLT_DIR at it")
        return hits[0]

    def items(self, limit: int | None = None) -> Iterator[dict]:
        import librosa
        import soundfile as sf
        xml_path = self._trn_xml()
        corpus_root = xml_path.parent.parent      # .../nchlt_{iso}/
        sd = gc.spill_dir()
        spill = Path(sd.name)
        self._spill_dir = sd
        produced = 0
        # verified layout: <corpus><speaker id= gender=><recording audio=
        # relative-to-extract-root><orth>TEXT</orth></recording>...</speaker>
        tree = ET.parse(str(xml_path))
        pairs = [(speaker, recording)
                 for speaker in tree.getroot().iter("speaker")
                 for recording in speaker.iter("recording")]
        for speaker, element in pairs:
            if limit and produced >= limit:
                return
            rel = element.get("audio") or ""
            orth = element.findtext("orth") or ""
            wav_path = (corpus_root.parent / rel) if rel else None
            if wav_path is None or not wav_path.is_file():
                # try relative to the corpus root as well before refusing row
                wav_path = corpus_root / rel
                if not wav_path.is_file():
                    continue
            raw = wav_path.read_bytes()
            arr, sr = sf.read(io.BytesIO(raw), dtype="float32")
            if getattr(arr, "ndim", 1) > 1:
                arr = arr.mean(axis=1)
            if sr != TARGET_SR:
                arr = librosa.resample(arr, orig_sr=sr, target_sr=TARGET_SR)
            dur = len(arr) / TARGET_SR
            if not usable(dur, orth):
                element.clear()
                continue
            buf = io.BytesIO()
            sf.write(buf, arr, TARGET_SR, format="WAV", subtype="PCM_16")
            wav = buf.getvalue()
            stem = wav_path.stem
            spk = speaker.get("id") or (
                stem.split("_")[2] if len(stem.split("_")) >= 3 else "unknown")
            base = f"{self.language}/{self.task}/{self.config}"
            rp = spill / f"{stem}.raw"; wp = spill / f"{stem}.wav"
            rp.write_bytes(raw); wp.write_bytes(wav)
            rec = build_record(
                audio_uri=f"s3://medzen-speech/curated/{base}/{self.version}/audio/{stem}.wav",
                audio_sha256=hashlib.sha256(wav).hexdigest(),
                duration_s=dur, sample_rate=TARGET_SR, channels=1,
                text_verbatim=orth, language=self.language,
                speaker_id=str(spk), session_id=f"{self.iso}_{spk}",
                split="train", spec=self.spec,
                split_strategy="speaker_disjoint",
                gender=gc.norm_gender(speaker.get("gender")), domain="asr",
                license_tier=self.tier, dialect=self.iso,
                raw_filepath=f"s3://medzen-speech/raw/{base}/{stem}.wav",
                raw_checksum_sha256=hashlib.sha256(raw).hexdigest(),
            )
            yield {"record": rec, "raw_path": rp, "wav_path": wp,
                   "raw_ext": "wav", "stem": stem}
            produced += 1
            element.clear()

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
