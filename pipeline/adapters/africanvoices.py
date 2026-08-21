"""AfricanVoices (africanvoices.io) adapter — Nigerian Pidgin (naija) ASR.

Commercial rights come from the owner's PRIVATE signed written terms
(LIC-2026-003-africanvoices-written-terms; the paper stays with the owner,
this repo records the attestation only). Rows carry
license_policy=commercial_ok on that authority — the yemba_egra precedent.
TRAIN-split archives only (owner's ordered split choice 2026-08-19); the
frozen pidgin evaluation remains the SOREVA set and must never be joined
by this material.

The 42 Batch_N.tar.zst archives were downloaded by the owner and staged at
s3://medzen-speech/raw/_incoming/africanvoices/. They are extracted OUT OF
BAND (kallaama pattern); MEDZEN_AV_DIR points at a root containing one or
more extracted batch dirs. Verified layout (Batch_1, 2026-08-19):

    <root>/<batch>/metadata.csv   <- ACTUALLY XLSX (Excel 2007+) despite
                                     the name; README says "a CSV fallback
                                     will be provided" — it was not
    <root>/<batch>/audio/*.flac      48 kHz mono, PCM_16/PCM_24

XLSX columns: speaker_id, audio_id, transcript, audio_path, gender,
age_group, education, duration, language, snr, domain. language is 'naija'
on every row; rows with any other value refuse (fail-closed). Real speaker
ids (741 in batch 1 alone) -> speaker_disjoint. Metadata durations run to
467 s; the usable() bounds (1-30 s, measured post-decode) apply as
everywhere else and the drop count is reported per batch.

MEDZEN_AV_MAX_SECONDS caps cumulative emitted audio (the NV cap pattern).

SMOKE TEST: `MEDZEN_AV_DIR=... python -m pipeline.ingest --source
africanvoices --language pidgin --dry-run --limit 20`.
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
from pathlib import Path
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, SourceSpec, build_record, usable

RELEASE = "africanvoices.io/naija-train@2025-12-25"   # export date in README
LICENSE_POLICY = "commercial_ok"   # LIC-2026-003, owner authority
ENV_ROOT = "MEDZEN_AV_DIR"
COLUMNS = ("speaker_id", "audio_id", "transcript", "audio_path", "gender",
           "age_group", "education", "duration", "language", "snr", "domain")


def _metadata_path(batch: Path) -> Path | None:
    for name in ("metadata.csv", "metadata.xlsx"):
        p = batch / name
        if p.is_file():
            return p
    return None


def batch_dirs(root: Path) -> list[Path]:
    """Extracted batch dirs under root — or root itself if it IS one."""
    if _metadata_path(root):
        return [root]
    found = sorted(
        (d for d in root.iterdir() if d.is_dir() and _metadata_path(d)),
        # Batch_2 before Batch_10: numeric-aware ordering keeps re-runs
        # aligned with the owner's archive numbering
        key=lambda d: (len(d.name), d.name))
    return found


def read_metadata(batch: Path, spill: Path) -> list[dict]:
    """The XLSX-named-.csv quirk: openpyxl validates by extension, so a
    .csv-named workbook is copied to spill as .xlsx before opening."""
    import openpyxl
    src = _metadata_path(batch)
    if src is None:
        raise ValueError(f"no metadata.csv/xlsx in {batch}")
    path = src
    if src.suffix != ".xlsx":
        path = spill / f"{batch.name}_metadata.xlsx"
        shutil.copyfile(src, path)
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        ws = wb.active
        it = ws.iter_rows(values_only=True)
        header = tuple(str(h) for h in next(it))
        if header != COLUMNS:
            raise ValueError(
                f"{src}: unexpected columns {header!r} — layout drifted from "
                f"the verified export, refusing to guess")
        return [dict(zip(header, row)) for row in it]
    finally:
        wb.close()


class AfricanVoicesAdapter:
    name = "africanvoices"

    def __init__(self, language: str, task: str | None = None,
                 version: str = "v1"):
        if language != "pidgin":
            raise ValueError(
                "AfricanVoices is wired for 'pidgin' (naija) only")
        if task not in (None, "asr"):
            raise ValueError("AfricanVoices is ASR-only")
        root = os.environ.get(ENV_ROOT)
        if not root:
            raise ValueError(f"set {ENV_ROOT} to the extracted-batches root")
        self.language = language
        self.task = "asr"
        self.version = version
        self.config = "av_pcm"
        self.root = Path(root)
        self.spec = SourceSpec(
            source_id="africanvoices",
            dataset_release=RELEASE,
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train"],
            consent_id="agreement-level:LIC-2026-003",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)

    def items(self, limit: int | None = None) -> Iterator[dict]:
        import librosa
        import soundfile as sf
        batches = batch_dirs(self.root)
        if not batches:
            raise ValueError(
                f"no extracted batch dirs under {self.root} — expected "
                f"<root>/Batch_N/metadata.csv + audio/*.flac")
        max_seconds = float(os.environ.get("MEDZEN_AV_MAX_SECONDS") or 0) or None
        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = Path(sd.name)
        self._spill_dir = sd
        produced = 0
        emitted_s = 0.0
        for batch in batches:
            dropped = 0
            malformed = 0
            rows = read_metadata(batch, spill)
            for row in rows:
                if limit and produced >= limit:
                    return
                if max_seconds and emitted_s >= max_seconds:
                    print(f"  [av] cap reached at {emitted_s / 3600:.1f} h")
                    return
                if str(row["language"]).strip().lower() != "naija":
                    raise ValueError(
                        f"{batch.name}: row {row['audio_id']} has language "
                        f"{row['language']!r} — export is documented as "
                        f"pure naija, refusing to mislabel")
                flac_path = batch / str(row["audio_path"])
                text = str(row["transcript"] or "")
                if not flac_path.is_file() or not text.strip():
                    dropped += 1
                    continue
                raw = flac_path.read_bytes()
                # 2026-08-21 lesson: a corrupt FLAC (libsndfile psf_fseek
                # failure) killed a full 42-batch run at decode time. The
                # full-corpus run then skipped THREE such files — all
                # verified (2026-08-21, Codex review #18) to be identical
                # 8,288-byte truncated stubs in the ORIGINAL export
                # archives (Batch_13/19/35), i.e. persistent source
                # defects, not transfer corruption. A malformed file is a
                # data defect to count and skip, never a crash.
                try:
                    arr, sr = sf.read(io.BytesIO(raw), dtype="float32")
                    if getattr(arr, "ndim", 1) > 1:
                        arr = arr.mean(axis=1)
                    if sr != TARGET_SR:
                        arr = librosa.resample(arr, orig_sr=sr,
                                               target_sr=TARGET_SR)
                except Exception as exc:                  # noqa: BLE001
                    malformed += 1
                    print(f"  [av] MALFORMED {row['audio_id']}: "
                          f"{type(exc).__name__}: {exc}", flush=True)
                    continue
                dur = len(arr) / TARGET_SR
                if not usable(dur, text):
                    dropped += 1
                    continue
                buf = io.BytesIO()
                sf.write(buf, arr, TARGET_SR, format="WAV", subtype="PCM_16")
                wav = buf.getvalue()
                stem = str(row["audio_id"])
                spk = str(row["speaker_id"] or "unknown")
                rp = spill / f"{stem}.raw"
                wp = spill / f"{stem}.wav"
                rp.write_bytes(raw)
                wp.write_bytes(wav)
                rec = build_record(
                    audio_uri=f"s3://medzen-speech/curated/{base}/{self.version}/audio/{stem}.wav",
                    audio_sha256=hashlib.sha256(wav).hexdigest(),
                    duration_s=dur, sample_rate=TARGET_SR, channels=1,
                    text_verbatim=text, language=self.language,
                    speaker_id=spk, session_id=f"pcm_{spk}",
                    split="train", spec=self.spec,
                    split_strategy="speaker_disjoint",
                    gender=gc.norm_gender(row["gender"]), domain="asr",
                    license_tier=self.tier, dialect="pcm",
                    raw_filepath=f"s3://medzen-speech/raw/{base}/{stem}.flac",
                    raw_checksum_sha256=hashlib.sha256(raw).hexdigest(),
                )
                yield {"record": rec, "raw_path": rp, "wav_path": wp,
                       "raw_ext": "flac", "stem": stem}
                produced += 1
                emitted_s += dur
            print(f"  [av] {batch.name}: {len(rows)} rows, "
                  f"{dropped} dropped (bounds/missing), "
                  f"{malformed} malformed (undecodable), "
                  f"cumulative {emitted_s / 3600:.1f} h", flush=True)

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
