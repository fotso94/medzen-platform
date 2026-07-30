"""WaxalNLP adapter — local-shard reader, not a remote stream.

WHY NOT streaming=True: breaking out of an active remote Parquet iterator
hangs on macOS/arm64 (Apache Arrow #45214). Observed exactly: 300 records
built, validator rejected them, then `Bad file descriptor` (Errno 9) and the
process never exited. HF_HUB_DOWNLOAD_TIMEOUT cannot fix that — it is an Arrow
teardown bug, not a transport timeout.

Instead: resolve the exact shard for a pinned revision, hf_hub_download it
(cached, resumable), then read row groups from the LOCAL file. Nothing remote
is iterated, so there is no iterator to abandon.

Licence varies BY PROVIDER, so the spec is built per provider.
"""
from __future__ import annotations

import fnmatch
import hashlib
import io
import pathlib
from typing import Iterator

from .base import TARGET_SR, SourceSpec, build_record, usable

REPO = "google/WaxalNLP"
# Pinned 2026-07-29. Bump deliberately, never automatically.
REVISION = "e0a62aaebc61bd5bb8cac17a08d1b42c65551dd2"

PROVIDER_LICENCE = {
    "makerere": "sharealike_review",
    "university_ghana": "commercial_ok",      # CC-BY-4.0 — cleanest here
    "digital_umuganda": "sharealike_review",
    "media_trust": "sharealike_review",
    "loud_and_clear": "sharealike_review",
    "aims_senegal": "sharealike_review",
}

# VERIFIED against get_dataset_config_names on 2026-07-29. The dataset CARD's
# provider table disagrees with the configs (it lists Wolof/Bambara/Pular TTS,
# none of which exist). Trust the configs.
CONFIGS: dict[str, dict[str, tuple[str, str]]] = {
    "acholi":  {"asr": ("ach_asr", "makerere"),         "tts": ("ach_tts", "makerere")},
    "luganda": {"asr": ("lug_asr", "makerere"),         "tts": ("lug_tts", "makerere")},
    "ewe":     {"asr": ("ewe_asr", "university_ghana"), "tts": ("ewe_tts", "university_ghana")},
    "fula":    {"asr": ("ful_asr", "digital_umuganda"), "tts": ("ful_tts", "digital_umuganda")},
    "akan":    {"asr": ("aka_asr", "university_ghana")},
    "amharic": {"asr": ("amh_asr", "digital_umuganda")},
    "oromo":   {"asr": ("orm_asr", "digital_umuganda")},
    "lingala": {"asr": ("lin_asr", "digital_umuganda")},
    "shona":   {"asr": ("sna_asr", "digital_umuganda")},
    "pidgin":  {"tts": ("pcm_tts", "media_trust")},
    "igbo":    {"tts": ("ibo_tts", "media_trust")},
    "hausa":   {"tts": ("hau_tts", "media_trust")},
    "yoruba":  {"tts": ("yor_tts", "media_trust")},
    "swahili": {"tts": ("swa_tts", "loud_and_clear")},
    # wolof: NO WaxalNLP data of any kind. Absent so ingest fails loudly.
}

BATCH_ROWS = 64        # bounded materialisation; audio blobs are large

# MEMORY, measured 2026-07-29 (macOS/arm64, pyarrow 25, mimalloc):
#   python heap  flat  ~150 MB   (no accumulation)
#   arrow live   bounded ~400 MB (peak 545 MB; nothing retained)
#   RSS high-water ~1.8 GB @ --limit 300
# RSS is dominated by transient decode+resample float arrays and by the
# allocator not returning freed pages; ru_maxrss only ever rises. Live
# memory does NOT grow with --limit, but RSS does, so:
#   * safe at --limit <= 600 on a host with >= 4 GB free
#   * in a container, allow >= 3 GB or lower --limit
# Changing the Arrow allocator does not help (system/mimalloc/purge all ~1.8-2.0 GB).
TEXT_KEYS = ("text", "sentence", "transcription")
SPK_KEYS = ("speaker_id", "client_id")


class WaxalNLPAdapter:
    name = "waxalnlp"

    def __init__(self, language: str, task: str | None = None,
                 revision: str = REVISION, version: str = "v1"):
        if language not in CONFIGS:
            raise ValueError(f"WaxalNLP has no data for '{language}'. "
                             f"Available: {sorted(CONFIGS)}")
        tasks = CONFIGS[language]
        if task is None:
            task = "asr" if "asr" in tasks else "tts"
        if task not in tasks:
            raise ValueError(f"WaxalNLP has no {task} data for '{language}' "
                             f"(has: {sorted(tasks)})")
        self.language, self.task = language, task
        self.config, self.provider = tasks[task]
        self.revision = revision
        self.version = version      # must match ingest --version or audio URIs lie
        code, kind = self.config.split("_")
        # Layout confirmed from the README config block: data/TTS/ach/ach-train-*
        self.shard_glob = f"data/{kind.upper()}/{code}/{code}-train-*"
        self.spec = SourceSpec(
            source_id=f"waxalnlp/{self.provider}",
            dataset_release=f"{REPO}@{revision[:12]}#{self.config}",
            license_policy=PROVIDER_LICENCE[self.provider],
            allowed_use=(["tts_train", "tts_eval"] if self.task == "tts"
                         else ["asr_train", "asr_eval"]),
            consent_id=f"dataset-level:waxalnlp/{self.provider}",
        )

    # ------------------------------------------------------------------ #
    def shards(self) -> list[str]:
        """Shard paths for this config at the pinned revision, in order."""
        from huggingface_hub import list_repo_files
        files = list_repo_files(REPO, repo_type="dataset", revision=self.revision)
        return sorted(f for f in files if fnmatch.fnmatch(f, self.shard_glob))

    def _fetch(self, path: str) -> str:
        from huggingface_hub import hf_hub_download
        return hf_hub_download(REPO, path, repo_type="dataset",
                               revision=self.revision)

    # ------------------------------------------------------------------ #
    def items(self, limit: int | None = None) -> Iterator[dict]:
        """Yield {record, raw_bytes, raw_ext, wav_bytes} from LOCAL shards.

        Row groups are fully materialised before extraction, and the Parquet
        file is closed explicitly. No remote iterator is created or abandoned.
        """
        import librosa
        import pyarrow as pa
        import pyarrow.parquet as pq
        import soundfile as sf

        pool = pa.default_memory_pool()

        shard_paths = self.shards()
        if not shard_paths:
            raise RuntimeError(f"no shards matched {self.shard_glob} at "
                               f"{self.revision[:12]}")

        import tempfile
        self._spill_dir = tempfile.TemporaryDirectory(prefix="medzen_ingest_")
        self._spill = pathlib.Path(self._spill_dir.name)

        produced = 0
        for shard in shard_paths:
            if limit and produced >= limit:
                break
            local = self._fetch(shard)
            pf = pq.ParquetFile(local)
            try:
                names = set(pf.schema_arrow.names)
                text_key = next((k for k in TEXT_KEYS if k in names), None)
                spk_key = next((k for k in SPK_KEYS if k in names), None)
                if text_key is None or "audio" not in names:
                    continue

                # Project to the columns we actually use. A full row group
                # to_pylist() can be several GB; audio+text+ids is a fraction.
                cols = [c for c in ("audio", text_key, spk_key, "id", "gender")
                        if c and c in names]
                # iter_batches on a LOCAL file with a bounded batch_size keeps
                # peak RSS flat. read_row_group() materialises a whole group —
                # measured 1.9 GB peak even with column projection, because a
                # group holds thousands of audio blobs. Arrow #45214 concerns
                # REMOTE fragment iterators; this file is on local disk and is
                # closed explicitly in the finally block.
                for bi, rb in enumerate(pf.iter_batches(batch_size=BATCH_ROWS,
                                                        columns=cols)):
                    if limit and produced >= limit:
                        break
                    batch = rb.to_pylist()
                    del rb                  # drop the Arrow buffers immediately
                    for i, row in enumerate(batch):
                        if limit and produced >= limit:
                            break
                        audio = row.get("audio") or {}
                        blob = audio.get("bytes")
                        text = (row.get(text_key) or "").strip()
                        if not blob or not text:
                            continue
                        src = audio.get("path") or f"{bi:04d}_{i:06d}"
                        ext = (src.rsplit(".", 1)[-1] if "." in src else "bin").lower()
                        try:
                            arr, sr = sf.read(io.BytesIO(blob), dtype="float32",
                                              always_2d=False)
                        except Exception:
                            continue
                        if arr.ndim > 1:
                            arr = arr.mean(axis=1)          # downmix to mono
                        if sr != TARGET_SR:
                            arr = librosa.resample(arr, orig_sr=sr,
                                                   target_sr=TARGET_SR)
                        dur = len(arr) / TARGET_SR
                        if not usable(dur, text):
                            continue

                        # serialise the EXACT bytes we upload, then hash those
                        buf = io.BytesIO()
                        sf.write(buf, arr, TARGET_SR, format="WAV",
                                 subtype="PCM_16")
                        wav = buf.getvalue()

                        stem = str(row.get("id") or src.rsplit(".", 1)[0])
                        raw_spk = row.get(spk_key) if spk_key else None
                        if raw_spk is None or str(raw_spk).strip().lower() in ("", "none", "null"):
                            # A literal "None" speaker silently merges distinct
                            # speakers into one id and breaks speaker-disjoint
                            # splitting. Derive a stable per-shard fallback.
                            spk = f"{self.config}_unknown_{bi:04d}"
                        else:
                            spk = str(raw_spk).strip()
                        base = f"{self.language}/{self.task}/{self.config}"
                        g = (row.get("gender") or "").strip().lower()
                        gender = {"male": "m", "female": "f"}.get(
                            g, "unknown" if not g else "other")

                        rec = build_record(
                            audio_uri=f"s3://medzen-speech/curated/{base}/{self.version}/audio/{stem}.wav",
                            audio_sha256=hashlib.sha256(wav).hexdigest(),
                            duration_s=dur, sample_rate=TARGET_SR, channels=1,
                            text_verbatim=text, language=self.language,
                            speaker_id=spk, session_id=f"{self.config}_{spk}",
                            split="train", spec=self.spec,
                            split_strategy=("text_disjoint" if self.task == "tts"
                                            else "speaker_disjoint"),
                            gender=gender, domain=self.task,
                            raw_filepath=f"s3://medzen-speech/raw/{base}/{stem}.{ext}",
                            raw_checksum_sha256=hashlib.sha256(blob).hexdigest(),
                        )
                        # Spill to disk rather than retaining bytes: 300 rows
                        # of (raw + wav) plus Arrow's row-group pool drove peak
                        # RSS to ~1.9 GB. Paths keep RSS flat; the caller
                        # uploads from disk and the temp dir is cleaned up.
                        rp = self._spill / f"{stem}.raw"
                        wp = self._spill / f"{stem}.wav"
                        rp.write_bytes(blob)
                        wp.write_bytes(wav)
                        yield {"record": rec, "raw_path": rp, "wav_path": wp,
                               "raw_ext": ext, "stem": stem}
                        produced += 1

                    # Arrow does not return decompressed buffers to the OS on
                    # its own; without this RSS grew linearly with --limit
                    # (measured 17 MB @5 rows -> 1896 MB @300 rows).
                    batch.clear()
                    del batch
                    pool.release_unused()
            finally:
                pf.close()          # explicit: no dangling Arrow file handle

    def rows(self, language: str | None = None,
             limit: int | None = None) -> Iterator[dict]:
        for item in self.items(limit=limit):
            yield item["record"]
