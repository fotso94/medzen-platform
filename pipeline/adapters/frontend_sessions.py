"""Opt-in recordings from the MylestechSpeechVoice frontend (Phase 4).

Source of truth: a local mirror of s3://medzen-speech/curated/frontend-sessions-
reviewed/ (`aws s3 sync`), pointed to by MEDZEN_FRONTEND_SESSIONS_ROOT. Each
session folder holds audio.wav (16 kHz mono PCM as recorded by the browser),
meta.json (consent + retention + ASR hypothesis) and, after review,
review.json (reviewer decision + final text).

Rules that cannot be bypassed here:
  * only sessions with an explicit consent record (granted, version) are read;
  * only REVIEWED sessions become rows, and the text is the reviewer-approved
    correction (or the ASR hypothesis when the reviewer confirmed it) — an
    uncorrected, unconfirmed hypothesis is never emitted (it would reinforce
    the model's own mistakes);
  * rows are de-identified: speaker_id is the session pseudonym, never a
    device or network identity (none is stored to begin with).
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import wave
from pathlib import Path
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, SourceSpec, build_record, usable

ENV_ROOT = "MEDZEN_FRONTEND_SESSIONS_ROOT"
RELEASE = "frontend-sessions-reviewed-2026-09"
LICENSE_POLICY = "own_data_consented"
CONSENT_VERSION = "2026-09-02-v1"
LANGUAGES = {"en": "english", "fr": "french", "kin": "kinyarwanda",
             "pcm": "pidgin", "swa": "swahili"}


def session_dirs(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.rglob("meta.json"))


def load_session(folder: Path) -> dict | None:
    meta = json.loads((folder / "meta.json").read_text())
    consent = meta.get("consent") or {}
    if consent.get("granted") is not True or consent.get("version") != CONSENT_VERSION:
        return None                      # never read unconsented material
    review_path = folder / "review.json"
    if not review_path.is_file():
        return None                      # unreviewed: not a row yet
    review = json.loads(review_path.read_text())
    if review.get("approved") is not True:
        return None
    text = str(review.get("text") or "").strip()
    if not text:
        return None
    return {"meta": meta, "review": review, "text": text, "folder": folder}


class FrontendSessionsAdapter:
    name = "frontend_sessions"

    def __init__(self, language: str, task: str | None = None, version: str = "v1"):
        if language not in LANGUAGES.values():
            raise ValueError(f"frontend_sessions supports {sorted(LANGUAGES.values())}")
        if task not in (None, "asr"):
            raise ValueError("frontend_sessions is ASR-only")
        root = os.environ.get(ENV_ROOT)
        if not root:
            raise ValueError(f"set {ENV_ROOT} to the reviewed-sessions mirror root")
        self.language, self.task, self.version = language, "asr", version
        self.config = "frontend_sessions"
        self.root = Path(root)
        self.spec = SourceSpec(
            source_id="frontend_sessions",
            dataset_release=RELEASE,
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train", "asr_eval"],
            consent_id=f"frontend-consent:{CONSENT_VERSION}",
        )
        self.tier = gc.tier_for(LICENSE_POLICY) if hasattr(gc, "tier_for") else None

    def items(self, limit: int | None = None) -> Iterator[dict]:
        sd = gc.spill_dir()
        spill = Path(sd.name)
        self._spill_dir = sd
        produced = 0
        base = f"{self.language}/{self.task}/{self.config}"
        for folder in session_dirs(self.root):
            if limit and produced >= limit:
                return
            session = load_session(folder)
            if session is None:
                continue
            meta = session["meta"]
            if LANGUAGES.get(str(meta.get("language"))) != self.language:
                continue
            raw = (folder / "audio.wav").read_bytes()
            with wave.open(io.BytesIO(raw)) as w:
                sr, ch, frames = w.getframerate(), w.getnchannels(), w.getnframes()
            if sr != TARGET_SR or ch != 1:
                raise ValueError(f"{folder.name}: expected {TARGET_SR} Hz mono WAV, got {sr} Hz x{ch}")
            dur = frames / float(sr)
            text = session["text"]
            if not usable(dur, text):
                continue
            stem = meta["request_id"]
            rp = spill / f"{stem}.wav"
            wp = spill / f"{stem}.16k.wav"
            rp.write_bytes(raw)
            shutil.copyfile(rp, wp)
            rec = build_record(
                audio_uri=f"s3://medzen-speech/curated/{base}/{stem}.wav",
                audio_sha256=hashlib.sha256(raw).hexdigest(),
                duration_s=dur, sample_rate=sr, channels=1,
                text_verbatim=text, language=self.language,
                speaker_id=f"fe-{meta.get('session_pseudonym', 'unknown')[:16]}",
                session_id=stem, split="train", spec=self.spec,
                split_strategy="speaker_disjoint", domain="asr",
                license_tier=self.tier,
                review_kind=str(session["review"].get("kind", "corrected")),
                asr_hypothesis=str(meta.get("asr_hypothesis") or ""),
                consent_version=CONSENT_VERSION,
                raw_filepath=f"s3://medzen-speech/raw/{base}/{stem}.wav",
                raw_checksum_sha256=hashlib.sha256(raw).hexdigest(),
            )
            yield {"record": rec, "raw_path": rp, "wav_path": wp,
                   "raw_ext": "wav", "stem": stem}
            produced += 1

    def rows(self, language: str | None = None, limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
