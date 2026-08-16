"""Adapter-merge export with a signed manifest (design record T2).

The exported artifact is a plain checkpoint with every LoRA adapter folded
into the base weights — the serving pipeline stays byte-for-byte the one the
evaluation suite live-proved, with no adapter code at inference. The
manifest carries the §B5 identity set (model SHA-256, tokenizer reference,
decode config, gate report reference) and both files are write-once.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import torch
from torch import nn

from pipeline.omniasr_lora import merge_lora


class ExportRefusal(RuntimeError):
    pass


def _write_exclusive(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(body)


def export_merged_checkpoint(
    model: nn.Module,
    *,
    output_dir: Path,
    base_model_card: str,
    tokenizer_reference: str,
    decode_config: dict[str, Any],
    gate_report_reference: str | None,
    training_run_identity: dict[str, Any],
) -> dict[str, Any]:
    """Merge adapters, save the checkpoint, and bind its manifest.

    The write-once check precedes the merge so a refused export never
    mutates the model it was handed."""
    checkpoint_path = output_dir / "model.pt"
    manifest_path = output_dir / "manifest.json"
    if checkpoint_path.exists() or manifest_path.exists():
        raise ExportRefusal(f"{output_dir} already holds an exported artifact")
    merge_audit = merge_lora(model)

    state = model.state_dict()
    residue = [name for name in state if ".lora_a" in name or ".lora_b" in name]
    if residue:
        raise ExportRefusal(f"adapter residue survived the merge: {residue[:3]}")

    output_dir.mkdir(parents=True, exist_ok=True)
    with checkpoint_path.open("xb") as stream:
        torch.save(state, stream)
    model_sha256 = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()

    manifest = {
        "record": "OMNIASR_MERGED_CHECKPOINT_MANIFEST",
        "schema_version": 1,
        "base_model_card": base_model_card,
        "model_sha256": model_sha256,
        "model_bytes": checkpoint_path.stat().st_size,
        "tokenizer_reference": tokenizer_reference,
        "decode_config": decode_config,
        "gate_report_reference": gate_report_reference,
        "training_run_identity": training_run_identity,
        "merged_modules": merge_audit["merged_modules"],
        "serving_contract": (
            "plain fairseq2 checkpoint; no adapter code at inference; serve on the "
            "ASRInferencePipeline path live-proven by the evaluation suite"
        ),
    }
    body = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
    _write_exclusive(manifest_path, body)
    return {
        "status": "PASS_MERGED_EXPORT",
        "checkpoint": str(checkpoint_path),
        "manifest": str(manifest_path),
        "model_sha256": model_sha256,
        "manifest_sha256": hashlib.sha256(body).hexdigest(),
    }
