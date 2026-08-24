"""Batch source for the omniASR CTC trainer (work item C1).

Deterministic by construction: batch i is a pure function of (mix order,
batch_size, i), so a resumed run replays exactly the batches an
uninterrupted run would have seen — the kill-and-resume equivalence test
depends on this, and so does the honesty of any loss curve that spans a
spot reclaim.

Audio is fetched once into a content-addressed cache and its SHA-256 is
VERIFIED on first fetch (the B4 dataset trusted the object store; a
trainer that signs its export manifest should not). fairseq2 tensor
assembly is confined to make_batch_source, exercised in-container (C3).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Callable


class DataRefusal(RuntimeError):
    pass


BUCKET = "medzen-speech"


def fetch_audio(cli, row: dict[str, Any], cache: Path) -> Path:
    """Download-once into the cache, verifying content against the manifest."""
    sha = row["audio_checksum_sha256"]
    local = cache / f"{sha}.audio"
    if local.exists():
        return local
    filepath = row["audio_filepath"]
    marker = f"{BUCKET}/"
    if marker not in filepath:
        raise DataRefusal(f"audio_filepath {filepath!r} is not in {BUCKET}")
    key = filepath.split(marker, 1)[1]
    body = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    actual = hashlib.sha256(body).hexdigest()
    if actual != sha:
        raise DataRefusal(
            f"{key} hashes to {actual[:16]}, manifest says {sha[:16]} — "
            "the object changed after ingest; refusing to train on it")
    cache.mkdir(parents=True, exist_ok=True)
    tmp = cache / f"{sha}.tmp"
    tmp.write_bytes(body)
    tmp.replace(local)
    return local


def batch_rows(mix: list[dict], batch_size: int, index: int) -> list[dict]:
    """Rows for micro-batch `index`; wraps deterministically over the mix."""
    if not mix:
        raise DataRefusal("empty mix has no batches")
    start = (index * batch_size) % len(mix)
    return [mix[(start + offset) % len(mix)] for offset in range(batch_size)]


def authoritative_language(row: dict[str, Any]) -> str:
    """The trusted per-row language is `_lang`, set by load_mix from the
    manifest PARTITION path (train_asr.py) — not the optional, free-text
    `primary_language`/`language` metadata a row may also carry. Codex review
    #19 (F2): KD masking must key off the authoritative tag, refuse a row that
    has none, and refuse a row whose optional metadata CONFLICTS with `_lang`
    (a data-integrity fault, not a thing to silently ignore). Host-safe (no
    torch) so the invariant is unit-tested off the GPU."""
    lang = str(row.get("_lang") or "").strip().lower()
    if not lang:
        raise DataRefusal(
            "a mix row has no authoritative `_lang` (set by load_mix from the "
            "manifest partition) — KD cannot trust unlabelled data")
    for key in ("primary_language", "language"):
        meta = row.get(key)
        if meta is not None and str(meta).strip().lower() not in ("", lang):
            raise DataRefusal(
                f"row {key}={meta!r} conflicts with authoritative "
                f"_lang={lang!r}; refusing rather than trusting metadata "
                "that disagrees with the manifest partition")
    return lang


def make_batch_source(mix: list[dict], tokenizer, config, cli, cache: Path,
                      *, device=None) -> Callable[[int], dict[str, Any]]:
    """Collate micro-batch `index` into the tensors _batch_loss consumes:
    zero-padded (B, T) tensors plus fairseq2 BatchLayout objects carrying
    the true lengths (the layout, not the padding value, is what the CTC
    loss reads — fairseq2 v0.6.0). Contact surface verified in-container.
    """
    import soundfile as sf
    import torch
    from fairseq2.nn import BatchLayout

    encoder = tokenizer.create_encoder()

    def _pad(tensors: list, dtype) -> tuple[Any, list[int]]:
        lens = [int(t.shape[0]) for t in tensors]
        out = torch.zeros(len(tensors), max(lens), dtype=dtype)
        for i, t in enumerate(tensors):
            out[i, : t.shape[0]] = t
        if device is not None:
            out = out.to(device)
        return out, lens

    def batches(index: int) -> dict[str, Any]:
        rows = batch_rows(mix, config.batch_size, index)
        waves, targets, languages = [], [], []
        for row in rows:
            audio, _ = sf.read(fetch_audio(cli, row, cache),
                               dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            waves.append(torch.from_numpy(audio))
            targets.append(encoder(row["text_normalized"]))
            languages.append(authoritative_language(row))
        # The model loads in bf16; float32 audio dies in its first conv
        # ("Input type (float) and bias type (c10::BFloat16)", run r5).
        seqs, seq_lens = _pad(waves, torch.float32)
        seqs = seqs.to(torch.bfloat16)
        target_seqs, target_lens = _pad(targets, torch.int64)
        return {
            "seqs": seqs,
            "seqs_layout": BatchLayout(
                tuple(seqs.shape), seq_lens=seq_lens, device=seqs.device),
            "targets": target_seqs,
            "targets_layout": BatchLayout(
                tuple(target_seqs.shape), seq_lens=target_lens,
                device=target_seqs.device),
            # Arm-2 (Codex #14-#19): AUTHORITATIVE per-row language tag
            # (manifest-partition `_lang`, conflicts refused) so the KD closure
            # masks the distillation term to the preservation languages.
            "languages": languages,
        }

    return batches
