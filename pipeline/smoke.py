"""Bounded pre-training checks. Each has a numeric pass condition.

These exist because the failed 600-step run could have been stopped in about
ninety seconds. Its loss fell 22.53 -> 4.00 -- an 82% decrease -- while it
learned an objective that could not terminate. Every check here is designed to
fail on that run.
"""
from __future__ import annotations

# ---- one-batch overfit ----------------------------------------------------
# Two conditions, both required. The RATIO alone would have passed the failed
# run (82% decrease). The ABSOLUTE alone would fail a hard-but-correct task
# early in training. Together they say: the objective is learnable AND the
# model can actually fit it.
OVERFIT_MAX_STEPS = 200
OVERFIT_RATIO = 0.05            # L_final <= 5% of L0
OVERFIT_ABSOLUTE = 0.5          # ... and below 0.5 in absolute terms


def overfit_verdict(l0: float, l_final: float, steps: int,
                    all_grads_finite: bool) -> dict:
    """Decide whether a single batch was actually learned."""
    import math
    finite = all(math.isfinite(x) for x in (l0, l_final))
    ratio = (l_final / l0) if (finite and l0 > 0) else float("inf")
    reasons = []
    if not finite:
        reasons.append(f"non-finite loss (L0={l0}, L_final={l_final})")
    if not all_grads_finite:
        reasons.append("a gradient was non-finite")
    if steps > OVERFIT_MAX_STEPS:
        reasons.append(f"took {steps} steps, budget is {OVERFIT_MAX_STEPS}")
    if finite and ratio > OVERFIT_RATIO:
        reasons.append(f"L_final/L0 = {ratio:.4f} > {OVERFIT_RATIO}")
    if finite and l_final >= OVERFIT_ABSOLUTE:
        reasons.append(f"L_final = {l_final:.4f} >= {OVERFIT_ABSOLUTE}")
    return {
        "l0": l0, "l_final": l_final,
        "ratio": None if not finite else round(ratio, 6),
        "steps": steps, "max_steps": OVERFIT_MAX_STEPS,
        "required_ratio": OVERFIT_RATIO, "required_absolute": OVERFIT_ABSOLUTE,
        "all_grads_finite": all_grads_finite,
        "passed": not reasons,
        "reasons": reasons,
    }


# ---- the adapter must actually do something -------------------------------
# A LoRA whose B matrix is still zero is structurally present, correctly
# targeted, and has requires_grad=True -- and changes nothing. It would produce
# base-quality numbers that look exactly like a successful fix.
ADAPTER_MIN_LOGIT_DELTA = 1e-3
ADAPTER_MIN_WEIGHT_NORM = 1e-6


def adapter_effect_verdict(logits_on, logits_off, weight_norms: dict,
                           checkpoint_sha256: str | None = None,
                           tested_artifact_sha256: str | None = None) -> dict:
    """Prove the adapter changes inference, not just the object graph.

    Also refuses non-finite logits or norms: a NaN would make every comparison
    below False and report the adapter as inert, or -- worse -- make `abs().max()`
    itself NaN and slip past the threshold check.
    """
    import math

    import torch

    reasons = []
    if not bool(torch.isfinite(logits_on).all()) or \
            not bool(torch.isfinite(logits_off).all()):
        reasons.append("non-finite logits; the comparison below is meaningless")
    nonfinite_norms = {k: v for k, v in weight_norms.items()
                       if not math.isfinite(float(v))}
    if nonfinite_norms:
        reasons.append(f"non-finite adapter tensor norms: {sorted(nonfinite_norms)}")
    if reasons:
        return {"max_abs_logit_delta": None,
                "required_delta": ADAPTER_MIN_LOGIT_DELTA,
                "adapter_tensors_checked": len(weight_norms),
                "adapter_tensors_nonzero": None,
                "passed": False, "reasons": reasons}

    # The adapter under test must be the SAVED checkpoint, not the in-memory
    # model before serialization -- those can differ, and the artifact is what
    # a later run would load.
    if checkpoint_sha256 is not None and tested_artifact_sha256 is not None \
            and checkpoint_sha256 != tested_artifact_sha256:
        return {"passed": False, "reasons": [
            f"tested artifact {str(tested_artifact_sha256)[:16]} is not the "
            f"saved checkpoint {checkpoint_sha256[:16]}; the effect was "
            "measured on something other than what will be loaded later"],
            "max_abs_logit_delta": None,
            "required_delta": ADAPTER_MIN_LOGIT_DELTA,
            "adapter_tensors_checked": len(weight_norms),
            "adapter_tensors_nonzero": None}

    delta = float((logits_on - logits_off).abs().max().item())
    nonzero = {k: float(v) for k, v in weight_norms.items()
               if float(v) > ADAPTER_MIN_WEIGHT_NORM}
    if delta < ADAPTER_MIN_LOGIT_DELTA:
        reasons.append(
            f"enabling the adapter changed logits by at most {delta:.2e}, "
            f"below {ADAPTER_MIN_LOGIT_DELTA:.0e} -- it is inert")
    if not nonzero:
        reasons.append(
            "every adapter tensor norm is effectively zero; a LoRA with a zero "
            "B matrix is present, targeted, trainable and does nothing")
    if torch.equal(logits_on, logits_off):
        reasons.append("logits are bit-identical with the adapter on and off")
    return {
        "max_abs_logit_delta": delta,
        "required_delta": ADAPTER_MIN_LOGIT_DELTA,
        "adapter_tensors_checked": len(weight_norms),
        "adapter_tensors_nonzero": len(nonzero),
        "checkpoint_sha256": checkpoint_sha256,
        "tested_artifact_sha256": tested_artifact_sha256,
        "tested_the_saved_checkpoint": (
            checkpoint_sha256 is not None
            and checkpoint_sha256 == tested_artifact_sha256),
        "passed": not reasons,
        "reasons": reasons,
    }


def lora_structure_verdict(model, expect_modules=("q_proj", "v_proj")) -> dict:
    """Structural checks: wrapped, targeted, trainable."""
    named = [n for n, _ in model.named_parameters() if "lora_" in n]
    trainable = [n for n, p in model.named_parameters()
                 if "lora_" in n and p.requires_grad]
    targeted = {m for m in expect_modules if any(m in n for n in named)}
    reasons = []
    if type(model).__name__ not in ("PeftModel", "PeftModelForSeq2SeqLM"):
        reasons.append(f"model is {type(model).__name__}, not a PeftModel")
    if not named:
        reasons.append("no lora_ parameters found")
    if not trainable:
        reasons.append("no lora_ parameter has requires_grad=True")
    missing = set(expect_modules) - targeted
    if missing:
        reasons.append(f"expected target modules absent: {sorted(missing)}")
    return {
        "model_type": type(model).__name__,
        "lora_parameters": len(named),
        "trainable_lora_parameters": len(trainable),
        "targeted_modules": sorted(targeted),
        "passed": not reasons,
        "reasons": reasons,
    }


# ---- generation smoke ------------------------------------------------------
def generation_smoke_verdict(acct: dict, logits_finite: bool,
                             loss_finite: bool) -> dict:
    """One decode, five conditions. Run post-overfit AND at every checkpoint."""
    reasons = []
    if not acct["eos_emitted"]:
        reasons.append("no <|endoftext|> emitted")
    if acct["hit_length_cap"]:
        reasons.append(f"hit the {acct['generated_tokens']}-token cap")
    if not logits_finite:
        reasons.append("non-finite logits")
    if not loss_finite:
        reasons.append("non-finite loss")
    return {
        **{k: acct[k] for k in ("prompt_tokens", "generated_tokens",
                                "eos_emitted", "eos_position",
                                "hit_length_cap", "stop_reason")},
        "logits_finite": logits_finite, "loss_finite": loss_finite,
        "passed": not reasons, "reasons": reasons,
    }


def require(verdict: dict, what: str) -> dict:
    """Stop unless the verdict passed. Reports the numbers, not just a word."""
    if not verdict.get("passed"):
        raise SystemExit(
            f"REFUSING: {what} failed:\n  "
            + "\n  ".join(verdict.get("reasons", ["(no reason recorded)"]))
            + f"\n  verdict: {verdict}")
    return verdict
