"""Mozilla Common Voice adapter — CC0 (permissive tier).

CV 17.0 is a LOAD-SCRIPT dataset (the HF tree holds only a README + script; audio
is pulled from Mozilla as tar archives). It has no parquet mirror, so unlike the
other green sources this one goes through the `datasets` library rather than the
local-parquet reader.

Access verified 2026-08-05: `common_voice_17_0` is NOT gated. It does NOT contain
Cameroon Pidgin (added in CV 25.0, Mozilla Data Collective only — deferred).

RUNTIME NOTES (read before bulk ingest):
  * needs `datasets` installed and `trust_remote_code=True`.
  * `datasets` streaming hangs on macOS/arm64 (Arrow #45214, see waxalnlp.py), so
    run CV ingest on Linux (the EC2 trainer) OR accept a full per-language
    download. The smoke path uses a bounded split slice.
  * a one-time "Agree and access" click on the HF page may be required per account
    even though the repo is public — confirm at fetch.
SMOKE TEST: `python -m pipeline.ingest --source common_voice --language swahili
--dry-run --limit 20`.
"""
from __future__ import annotations

import hashlib
import io
from typing import Iterator

from . import green_common as gc
from .base import TARGET_SR, SourceSpec, build_record, usable

# The official mozilla-foundation/common_voice_17_0 HF repo is now EMPTY (data
# removed). fsicoli/common_voice_17_0 is a CC0 community mirror carrying the same
# CV 17.0 audio+transcripts (tar+tsv, loaded via its script). Verified 2026-08-06.
REPO = "fsicoli/common_voice_17_0"
REVISION = "8262c16bf297c87a9cd88c51997c4758ed7a8ba2"   # pinned 2026-08-06
LICENSE_POLICY = "cc0"                      # CC0 -> permissive

# language -> CV locale code (present in this mirror; amharic absent here and
# already covered by WAXAL with ~190 h, so it is not sourced from CV)
CODES = {"swahili": "sw", "hausa": "ha", "yoruba": "yo",
         "igbo": "ig", "luganda": "lg",
         # rw verified real in this mirror 2026-08-08 (train.tsv ~1.0M rows,
         # ~2,000 validated hours). Ingest is CAPPED via env (below) — the full
         # locale is far larger than a first campaign needs.
         "kinyarwanda": "rw"}
SPLITS = ("train", "validation")

# Optional env controls (added for the capped kinyarwanda ingest, 2026-08-11):
#   MEDZEN_CV_MAX_SECONDS  stop after this many cumulative emitted seconds
#   MEDZEN_CV_SPLITS       comma list overriding SPLITS (e.g. "test" for an
#                          eval-only pass routed by the normal split field)


class CommonVoiceAdapter:
    name = "common_voice"

    def __init__(self, language: str, task: str | None = None,
                 revision: str = REVISION, version: str = "v1"):
        if language not in CODES:
            raise ValueError(
                f"Common Voice 17.0 (in-scope) has no locale for '{language}'. "
                f"Available: {sorted(CODES)}")
        if task not in (None, "asr"):
            raise ValueError("Common Voice is ASR-only here")
        self.language = language
        self.task = "asr"
        self.code = CODES[language]
        self.revision = revision
        self.version = version
        self.config = f"cv17_{self.code}"
        self.spec = SourceSpec(
            source_id="common_voice",
            dataset_release=f"{REPO}@{revision}#{self.code}",
            license_policy=LICENSE_POLICY,
            allowed_use=["asr_train", "asr_eval"],
            consent_id="dataset-level:common_voice_cc0",
        )
        self.tier = gc.tier_for(LICENSE_POLICY)    # -> permissive

    def items(self, limit: int | None = None) -> Iterator[dict]:
        import os

        import librosa
        import soundfile as sf
        from datasets import load_dataset
        from huggingface_hub import get_token
        token = get_token()

        max_seconds = float(os.environ.get("MEDZEN_CV_MAX_SECONDS") or 0) or None
        splits = tuple(s.strip() for s in
                       (os.environ.get("MEDZEN_CV_SPLITS") or "").split(",")
                       if s.strip()) or SPLITS

        base = f"{self.language}/{self.task}/{self.config}"
        sd = gc.spill_dir()
        spill = __import__("pathlib").Path(sd.name)
        self._spill_dir = sd
        produced = 0
        emitted_s = 0.0
        for split in splits:
            if limit and produced >= limit:
                return
            if max_seconds and emitted_s >= max_seconds:
                return
            sel = split if limit is None else f"{split}[:{max(1, limit)}]"
            ds = load_dataset(REPO, self.code, split=sel, token=token,
                              trust_remote_code=True)
            for row in ds:
                if limit and produced >= limit:
                    return
                if max_seconds and emitted_s >= max_seconds:
                    return
                text = (row.get("sentence") or "").strip()
                audio = row.get("audio") or {}
                arr = audio.get("array")
                sr = audio.get("sampling_rate")
                if arr is None or sr is None or not text:
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
                raw = audio.get("bytes") or wav
                src = str(row.get("path") or f"{self.code}_{produced:07d}")
                stem = (src.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                        + "_" + hashlib.sha256(wav).hexdigest()[:12])
                ext = (src.rsplit(".", 1)[-1] if "." in src else "mp3").lower()
                spk = str(row.get("client_id") or f"{self.code}_unknown").strip()
                rp = spill / f"{stem}.raw"; wp = spill / f"{stem}.wav"
                rp.write_bytes(raw); wp.write_bytes(wav)
                rec = build_record(
                    audio_uri=f"s3://medzen-speech/curated/{base}/{self.version}/audio/{stem}.wav",
                    audio_sha256=hashlib.sha256(wav).hexdigest(),
                    duration_s=dur, sample_rate=TARGET_SR, channels=1,
                    text_verbatim=text, language=self.language,
                    speaker_id=spk, session_id=f"{self.code}_{spk}",
                    split=split, spec=self.spec, split_strategy="speaker_disjoint",
                    gender=gc.norm_gender(row.get("gender")), domain="asr",
                    license_tier=self.tier, dialect=self.code,
                    raw_filepath=f"s3://medzen-speech/raw/{base}/{stem}.{ext}",
                    raw_checksum_sha256=hashlib.sha256(raw).hexdigest(),
                )
                yield {"record": rec, "raw_path": rp, "wav_path": wp,
                       "raw_ext": ext, "stem": stem}
                produced += 1
                emitted_s += dur

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
