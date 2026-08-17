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


def make_batch_source(mix: list[dict], tokenizer, config,
                      cli, cache: Path) -> Callable[[int], dict[str, Any]]:
    """Collate micro-batch `index` into the tensors _batch_loss consumes.

    fairseq2 contact surface — kept to one closure, verified in-container.
    """
    import soundfile as sf
    import torch
    from fairseq2.nn.padding import pad_seqs

    encoder = tokenizer.create_encoder()

    def batches(index: int) -> dict[str, Any]:
        rows = batch_rows(mix, config.batch_size, index)
        waves, targets = [], []
        for row in rows:
            audio, _ = sf.read(fetch_audio(cli, row, cache),
                               dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            waves.append(torch.from_numpy(audio))
            targets.append(encoder(row["text_normalized"]))
        seqs, padding_mask = pad_seqs(waves)
        target_seqs, target_padding_mask = pad_seqs(targets)
        return {"seqs": seqs, "padding_mask": padding_mask,
                "targets": target_seqs,
                "target_padding_mask": target_padding_mask}

    return batches
