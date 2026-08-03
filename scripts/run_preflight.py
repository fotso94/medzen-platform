#!/usr/bin/env python3
"""The executable training preflight. Runs BEFORE any learning-rate sweep.

One deterministic batch, up to 200 real optimisation steps, then a decode. If
the objective is not learnable, or the adapter is inert, or the model cannot
stop, this exits non-zero and the sweep never starts.

The failed 600-step run would have been caught here in about ninety seconds.

    python scripts/run_preflight.py --out /out/preflight.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import smoke                                        # noqa: E402
from pipeline.generation import (EOT_TOKEN, account, config_fingerprint,  # noqa: E402
                                 expected_prompt, extract_sequence,
                                 generation_kwargs)


def run(model, processor, batch, lang_token: str, device: str,
        checkpoint_sha256: str | None = None,
        tested_artifact_sha256: str | None = None) -> dict:
    """Overfit one batch, then prove the adapter works and the model stops."""
    import torch

    # ---- structure -------------------------------------------------------
    structure = smoke.lora_structure_verdict(model)
    if not structure["passed"]:
        return {"passed": False, "reasons": structure["reasons"],
                "lora_structure": structure}

    # ---- logits with the adapter DISABLED, before any training -----------
    model.eval()
    with torch.no_grad():
        off = model.base_model(**batch).logits.detach().clone() \
            if hasattr(model, "base_model") else None

    # ---- bounded overfit on ONE fixed batch ------------------------------
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad],
                            lr=1e-3)
    model.train()
    l0 = None
    grads_finite = True
    steps = 0
    loss_val = None
    for step in range(smoke.OVERFIT_MAX_STEPS):
        opt.zero_grad(set_to_none=True)
        out = model(**batch)
        loss = out.loss
        loss_val = float(loss.detach())
        if l0 is None:
            l0 = loss_val
        if not math.isfinite(loss_val):
            grads_finite = False
            steps = step + 1
            break
        loss.backward()
        for p in model.parameters():
            if p.requires_grad and p.grad is not None:
                if not bool(torch.isfinite(p.grad).all()):
                    grads_finite = False
        opt.step()
        steps = step + 1
        if loss_val <= smoke.OVERFIT_RATIO * l0 and loss_val < smoke.OVERFIT_ABSOLUTE:
            break

    overfit = smoke.overfit_verdict(l0 if l0 is not None else float("nan"),
                                    loss_val if loss_val is not None else float("nan"),
                                    steps, grads_finite)

    # ---- adapter effect: same input, adapter on vs off -------------------
    model.eval()
    with torch.no_grad():
        on = model(**batch).logits.detach()
        if hasattr(model, "disable_adapter"):
            with model.disable_adapter():
                off = model(**batch).logits.detach()
    norms = {n: float(p.detach().norm())
             for n, p in model.named_parameters() if "lora_B" in n}
    effect = smoke.adapter_effect_verdict(
        on, off, norms, checkpoint_sha256=checkpoint_sha256,
        tested_artifact_sha256=tested_artifact_sha256)

    # ---- generation: exact prompt, EOS, no cap ---------------------------
    prompt = expected_prompt(processor, lang_token)
    eot = processor.tokenizer.convert_tokens_to_ids(EOT_TOKEN)
    with torch.no_grad():
        gen = model.generate(batch["input_features"][:1],
                             **generation_kwargs(lang_token))
    ids = extract_sequence(gen)
    acct = account(ids, prompt, eot)
    gsmoke = smoke.generation_smoke_verdict(
        acct, logits_finite=bool(torch.isfinite(on).all()),
        loss_finite=math.isfinite(loss_val if loss_val is not None else float("nan")))

    reasons = (overfit["reasons"] + effect["reasons"] + gsmoke["reasons"])
    return {
        "passed": not reasons, "reasons": reasons,
        "lora_structure": structure, "overfit": overfit,
        "adapter_effect": effect, "generation_smoke": gsmoke,
        "generation_config_fingerprint": config_fingerprint(),
        "expected_prompt_ids": prompt,
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--lang-token", default="en")
    a = ap.parse_args()
    raise SystemExit(
        "run_preflight is invoked by pipeline/campaign.py with a constructed "
        "model, processor and fixed batch; it has no standalone launch path "
        f"(requested --out {a.out}). This message exists so the module cannot "
        "be run as an alternate entrypoint that bypasses the campaign's "
        "ordering.")


if __name__ == "__main__":
    sys.exit(main())
