#!/usr/bin/env python3
"""Checkpoint-sweep merge — runs INSIDE the trainer image (fairseq2 + the
trainer's own LoRA machinery; diagnosis B5-DIAG-2026-001 step 1).

For each adapter checkpoint under /inputs/checkpoints/*.pt: reload the
FROZEN base model fresh (merging mutates weights — reuse would compound
deltas), wrap with the campaign's exact LoRA shape (rank 16, alpha 32),
load the adapter state, merge, and save the plain merged state dict to
/outputs/merged/. Same arithmetic as the r2 export, proven correct by the
weight-delta analysis (non-target deltas at rounding level)."""
from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

sys.path.insert(0, "/repo")

import torch  # noqa: E402

from pipeline.omniasr_lora import merge_lora, wrap_lora  # noqa: E402
from pipeline.omniasr_train import CTC_SCOPE_PREFIX  # noqa: E402


def main() -> int:
    from fairseq2.models.hub import load_model
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out_dir = Path("/outputs/merged")
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoints = sorted(glob.glob("/inputs/checkpoints/*.pt"))
    if not checkpoints:
        raise SystemExit("no checkpoints mounted")
    for path in checkpoints:
        state = torch.load(path, map_location="cpu", weights_only=False)
        step = state["step"]
        target = out_dir / f"merged-step-{step:07d}.pt"
        if target.exists():
            print(f"skip step {step} (exists)", flush=True)
            continue
        # FULL-mode checkpoints (self-review catch 2026-08-20: this script
        # was LoRA-only and the v2 sweep would have crashed on
        # state["lora"]). A full checkpoint carries the whole model — no
        # wrap, no merge; just extract the servable state dict.
        if "model" in state and "lora" not in state:
            full_state = state["model"]
            residue = [k for k in full_state
                       if ".lora_a" in k or ".lora_b" in k]
            if residue:
                raise SystemExit(
                    f"step {step}: full checkpoint carries adapter keys "
                    f"{residue[:3]} — refusing an ambiguous artifact")
            # Codex review #4: a diverged run used to leave non-finite
            # weights that COMPLETED — never let one reach evaluation
            poisoned = [k for k, v in full_state.items()
                        if torch.is_tensor(v) and v.is_floating_point()
                        and not bool(torch.isfinite(v).all())]
            if poisoned:
                raise SystemExit(
                    f"step {step}: NON-FINITE tensors {poisoned[:3]} — "
                    "diverged checkpoint, refusing to extract")
            torch.save(full_state, target)
            print(f"extracted FULL step {step}: {len(full_state)} tensors "
                  f"-> {target.name}", flush=True)
            del full_state, state
            continue
        model = load_model("medzen_omniASR_CTC_1B_v2", device=device,
                           dtype=torch.bfloat16)
        rank = int(os.environ.get("MERGE_RANK", "16"))
        alpha = float(os.environ.get("MERGE_ALPHA", "32"))
        wrap_lora(model, rank=rank, alpha=alpha, dropout=0.0,
                  scope_prefix=CTC_SCOPE_PREFIX)
        missing, unexpected = model.load_state_dict(state["lora"], strict=False)
        if unexpected:
            raise SystemExit(f"step {step}: unexpected adapter keys {unexpected[:3]}")
        loaded = sum(1 for k in state["lora"])
        audit = merge_lora(model)
        merged_state = model.state_dict()
        residue = [k for k in merged_state if ".lora_a" in k or ".lora_b" in k]
        if residue:
            raise SystemExit(f"step {step}: adapter residue {residue[:3]}")
        torch.save(merged_state, target)
        print(f"merged step {step}: {loaded} adapter tensors, "
              f"{len(audit['merged_modules'])} modules -> {target.name}",
              flush=True)
        del model, merged_state
        if device.type == "cuda":
            torch.cuda.empty_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
