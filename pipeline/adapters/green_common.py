"""Green-bucket shared adapter machinery.

Two things every green-bucket adapter needs and should NOT re-implement:

1. `tier_for()` — the licence -> isolation-tier mapping. Every row carries the
   resulting `license_tier` so a training-manifest builder can select a tier and
   never merge permissive with sharealike, and never pull eval_only into
   training.

2. `read_parquet_audio_shards()` — the memory-bounded, no-remote-iterator reader
   proven in pipeline/adapters/waxalnlp.py. It resolves LOCAL parquet shards for
   a pinned revision, reads them in bounded batches, decodes+resamples audio to
   16 kHz mono, applies the cheap `usable` pre-filter, spills bytes to disk (so
   RSS stays flat), and yields decoded payloads. Adapters wrap each payload with
   base.build_record + their own SourceSpec.

   The Arrow #45214 teardown hang is about REMOTE streaming iterators; here every
   file is downloaded first (hf_hub_download, cached/resumable) and read locally,
   and every ParquetFile is closed explicitly. Same guarantees as the WAXAL path.
"""
from __future__ import annotations

import fnmatch
import hashlib
import io
import pathlib
import tempfile
from typing import Iterator

from .base import TARGET_SR, usable

# --------------------------------------------------------------------------- #
# licence -> tier
# --------------------------------------------------------------------------- #
# tier drives storage/manifest isolation. NOT a licence rename: it records how a
# row may be COMBINED, not what the licence text says.
TIER_BY_POLICY = {
    "commercial_ok": "permissive",     # CC0 / CC-BY-4.0 / Apache-2.0
    "cc0": "permissive",
    "cc_by_4_0": "permissive",
    "sharealike_review": "sharealike",  # CC-BY-SA-4.0 (owner-legal-approved, isolated)
    "research_only_nc": "eval_only",    # non-commercial -> eval/benchmark only
    "eval_reserved": "eval_only",       # cleanly licensed but deliberately held out
}


def tier_for(license_policy: str) -> str:
    """Map a licence policy to its isolation tier. Unknown -> eval_only (safe:
    an unclassified licence must never silently enter a commercial training set)."""
    return TIER_BY_POLICY.get(license_policy, "eval_only")


# --------------------------------------------------------------------------- #
# shard resolution
# --------------------------------------------------------------------------- #
def list_shards(repo: str, revision: str, glob: str,
                token: str | None = None) -> list[str]:
    """Parquet shard paths for a repo at a pinned revision, matching `glob`."""
    from huggingface_hub import list_repo_files
    files = list_repo_files(repo, repo_type="dataset", revision=revision,
                            token=token)
    return sorted(f for f in files if fnmatch.fnmatch(f, glob))


# --------------------------------------------------------------------------- #
# bounded local-parquet audio reader
# --------------------------------------------------------------------------- #
BATCH_ROWS = 64
TEXT_KEYS = ("text", "sentence", "transcription", "raw_transcription",
             "raw_text", "transcript", "normalized_text")
SPK_KEYS = ("speaker_id", "client_id", "speaker", "spk_id")
ID_KEYS = ("segment_id", "id", "path", "audio_id", "file", "filename")
GENDER_KEYS = ("gender", "sex")


def _pick(names: set[str], candidates) -> str | None:
    return next((k for k in candidates if k in names), None)


def read_parquet_audio_shards(
    repo: str, revision: str, shard_paths: list[str], *,
    split: str, spill: pathlib.Path, token: str | None = None,
    text_keys=TEXT_KEYS, spk_keys=SPK_KEYS, id_keys=ID_KEYS,
    gender_keys=GENDER_KEYS, limit: int | None = None,
) -> Iterator[dict]:
    """Yield decoded, resampled, usable rows from LOCAL parquet shards.

    Yields dicts:
      {wav_path, raw_path, raw_ext, stem, duration_s, text, spk_raw,
       gender_raw, raw_sha256, wav_sha256, split}
    The caller assembles the A3 record (it owns language, spec, tier, ids).
    """
    import librosa
    import pyarrow as pa
    import pyarrow.parquet as pq
    import soundfile as sf
    from huggingface_hub import hf_hub_download

    pool = pa.default_memory_pool()
    if not shard_paths:
        raise RuntimeError(f"no shards to read for {repo}@{revision[:12]}")

    produced = 0
    for shard in shard_paths:
        if limit and produced >= limit:
            break
        local = hf_hub_download(repo, shard, repo_type="dataset",
                                revision=revision, token=token)
        pf = pq.ParquetFile(local)
        try:
            names = set(pf.schema_arrow.names)
            if "audio" not in names:
                continue
            tkey = _pick(names, text_keys)
            skey = _pick(names, spk_keys)
            ikey = _pick(names, id_keys)
            gkey = _pick(names, gender_keys)
            if tkey is None:
                continue
            cols = [c for c in ("audio", tkey, skey, ikey, gkey) if c and c in names]

            for bi, rb in enumerate(pf.iter_batches(batch_size=BATCH_ROWS,
                                                    columns=cols)):
                if limit and produced >= limit:
                    break
                batch = rb.to_pylist()
                del rb
                for i, row in enumerate(batch):
                    if limit and produced >= limit:
                        break
                    audio = row.get("audio") or {}
                    blob = audio.get("bytes")
                    text = (row.get(tkey) or "").strip()
                    if not blob or not text:
                        continue
                    src = audio.get("path") or (row.get(ikey) if ikey else None) \
                        or f"{bi:04d}_{i:06d}"
                    src = str(src)
                    ext = (src.rsplit(".", 1)[-1] if "." in src else "bin").lower()
                    try:
                        arr, sr = sf.read(io.BytesIO(blob), dtype="float32",
                                          always_2d=False)
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

                    # Natural ids repeat across speakers/splits (Meta segment_id
                    # is "s01","s02".. per speaker; FLEURS id restarts per split),
                    # which would silently overwrite audio in S3. Append a content
                    # hash so the stem is globally unique per distinct audio (true
                    # byte-duplicates collapse to one key, which is correct).
                    natural = str(row.get(ikey) or src.rsplit(".", 1)[0]).replace("/", "_")
                    wav_sha = hashlib.sha256(wav).hexdigest()
                    stem = f"{natural}_{wav_sha[:12]}"
                    rp = spill / f"{stem}.raw"
                    wp = spill / f"{stem}.wav"
                    rp.write_bytes(blob)
                    wp.write_bytes(wav)
                    yield {
                        "wav_path": wp, "raw_path": rp, "raw_ext": ext,
                        "stem": stem, "duration_s": dur, "text": text,
                        "spk_raw": (row.get(skey) if skey else None),
                        "gender_raw": (row.get(gkey) if gkey else None),
                        "raw_sha256": hashlib.sha256(blob).hexdigest(),
                        "wav_sha256": wav_sha,
                        "split": split,
                    }
                    produced += 1
                batch.clear()
                del batch
                pool.release_unused()
        finally:
            pf.close()


def norm_gender(raw) -> str:
    g = (str(raw or "")).strip().lower()
    return {"male": "m", "female": "f", "m": "m", "f": "f"}.get(
        g, "unknown" if not g else "other")


def spill_dir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="medzen_green_")
