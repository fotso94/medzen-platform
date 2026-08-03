"""Deterministic ordering, gating and selection for the Option B run.

Pure decision logic, deliberately free of AWS and torch so every rule here is
testable without spending anything. The launcher calls into it; it never calls
out to a cloud.
"""
from __future__ import annotations

import hashlib
import json

from pipeline import language_scope, scope_deviation

# VAL-2026-001 preserves all nine frozen sets.  B4-SCOPE-2026-001 authorises
# six of them for the current campaign and defers Acholi, Amharic and Ewe
# without deleting any set.
ALL_VALIDATION_LANGUAGES = language_scope.ALL_VALIDATION_LANGUAGES
VALIDATION_LANGUAGES = language_scope.VALIDATION_LANGUAGES

# Gates. FOUR of them -- an earlier draft said "both gates" and named two.
MAX_LANGUAGE_REGRESSION = 0.05      # absolute WER, vs the in-run base arm
MAX_TERMINATION_FAILURES = int(
    scope_deviation.TERMINATION_GATE[
        "max_unique_failures_per_language_per_checkpoint"])

# The corrected 12-language campaign already performed the declared
# 1e-4/3e-4/5e-4 comparison under the final run's 600-step scheduler horizon.
# After Acholi is deferred, 1e-4 is the only arm that passed every unchanged
# gate for all six retained validation languages.  The new 11-language mix
# still needs a fresh 100-step confirmation, but repeating two configurations
# already known to fail retained languages would spend money without answering
# a new question.  This is a predeclared targeted continuation, not a relaxed
# selection rule.
LR_CANDIDATES = (1e-4,)
SWEEP_STEPS = 100
SWEEP_COMPARISON_CHECKPOINT = 100
# A sweep executes only SWEEP_STEPS, but its scheduler must be constructed for
# the same horizon as the final run.  Attempt b4-scoped-8350784791a6 proved
# that max_steps=100 in the sweep and max_steps=600 in the final produce
# different first-100-step learning-rate histories, so the selected sweep was
# not predictive of final checkpoint-100.
TRAINING_SCHEDULE_HORIZON = 600
FINAL_RUN_RESUMES_SWEEP = False      # decided in advance; see plan section 5
SEED = 0


def validate_metric_map(m: dict, name: str, lo: float | None = 0.0,
                        hi: float | None = None) -> dict[str, float]:
    """Every metric map must be exactly the active frozen languages, all finite.

    A gate that silently accepts NaN, or an absent language, is a gate that
    passes whatever it cannot see. NaN comparisons are always False, so a NaN
    WER would slip past `> cap` and be reported as no regression at all.
    """
    import math

    if not isinstance(m, dict):
        raise SystemExit(f"REFUSING: {name} is {type(m).__name__}, not a mapping")
    keys = set(m)
    want = set(VALIDATION_LANGUAGES)
    missing, extra = sorted(want - keys), sorted(keys - want)
    problems = []
    if missing:
        problems.append(f"missing {missing}")
    if extra:
        problems.append(f"unexpected {extra}")
    for lang in sorted(want & keys):
        v = m[lang]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            problems.append(f"{lang}={v!r} is not numeric")
            continue
        if math.isnan(v):
            problems.append(f"{lang} is NaN")
        elif math.isinf(v):
            problems.append(f"{lang} is infinite")
        elif lo is not None and v < lo:
            problems.append(f"{lang}={v} is below {lo}")
        elif hi is not None and v > hi:
            problems.append(f"{lang}={v} is above {hi}")
    if problems:
        raise SystemExit(f"REFUSING: {name} is unusable — " + "; ".join(problems))
    return {l: float(m[l]) for l in VALIDATION_LANGUAGES}


def macro_wer(per_language: dict[str, float]) -> float:
    """Unweighted mean across the active validation languages.

    Unweighted so shona's 15 rows count as much as akan's 72. Weighting by rows
    would let the two largest sets decide the verdict.
    """
    missing = set(VALIDATION_LANGUAGES) - set(per_language)
    if missing:
        raise SystemExit(f"REFUSING: no result for {sorted(missing)}; a macro "
                         "average over a subset is not the macro average")
    return sum(per_language[l] for l in VALIDATION_LANGUAGES) / len(VALIDATION_LANGUAGES)


def worst_regression(candidate: dict[str, float], base: dict[str, float]
                     ) -> tuple[str, float]:
    """The language that fared worst against the in-run base arm."""
    worst, val = None, float("-inf")
    for l in VALIDATION_LANGUAGES:
        d = candidate[l] - base[l]
        if d > val:
            worst, val = l, d
    return worst, val


def validate_termination_failures(
        evidence: dict, eos_rate: dict[str, float], cap_hit_rate: dict[str, float],
        termination_gate: dict) -> dict:
    """Validate checksum-only row evidence before applying the count gate."""
    if termination_gate != scope_deviation.TERMINATION_GATE:
        raise SystemExit(
            "REFUSING: termination gate differs from the owner-approved rule")
    if not isinstance(evidence, dict) or set(evidence) != set(VALIDATION_LANGUAGES):
        raise SystemExit(
            "REFUSING: termination evidence must name exactly the active "
            "validation languages")
    cleaned = {}
    for language in VALIDATION_LANGUAGES:
        item = evidence.get(language)
        if not isinstance(item, dict):
            raise SystemExit(
                f"REFUSING: termination evidence for {language} is not a mapping")
        rows = item.get("rows")
        if isinstance(rows, bool) or not isinstance(rows, int) or rows <= 0:
            raise SystemExit(
                f"REFUSING: termination evidence for {language} has invalid rows")

        lists = {}
        for field in ("checksums", "eos_missing_checksums", "cap_hit_checksums"):
            value = item.get(field)
            if (not isinstance(value, list) or value != sorted(set(value))
                    or any(not isinstance(v, str) or len(v) != 64
                           or any(c not in "0123456789abcdef" for c in v)
                           for v in value)):
                raise SystemExit(
                    f"REFUSING: {language} {field} is not a sorted unique "
                    "lowercase SHA-256 list")
            lists[field] = value
        union = sorted(set(lists["eos_missing_checksums"])
                       | set(lists["cap_hit_checksums"]))
        if lists["checksums"] != union:
            raise SystemExit(
                f"REFUSING: {language} termination checksums are not the "
                "deduplicated EOS/cap union")
        expected_counts = {
            "count": len(union),
            "eos_missing_count": len(lists["eos_missing_checksums"]),
            "cap_hit_count": len(lists["cap_hit_checksums"]),
        }
        for field, want in expected_counts.items():
            if item.get(field) != want:
                raise SystemExit(
                    f"REFUSING: {language} {field} differs from checksum evidence")
        expected_eos = round((rows - expected_counts["eos_missing_count"]) / rows, 4)
        expected_cap = round(expected_counts["cap_hit_count"] / rows, 4)
        if eos_rate[language] != expected_eos or cap_hit_rate[language] != expected_cap:
            raise SystemExit(
                f"REFUSING: {language} termination rates do not reconcile "
                "with checksum counts")
        cleaned[language] = {**item}
    return cleaned


def evaluate_gates(candidate_wer: dict[str, float], base_wer: dict[str, float],
                   eos_rate: dict[str, float], cap_hit_rate: dict[str, float],
                   termination_failures: dict | None = None,
                   termination_gate: dict | None = None) -> dict:
    """All FOUR gates. Every one is hard; none is advisory."""
    # Validate BEFORE gating. WER/CER are unbounded above but never negative;
    # rates must lie in [0, 1].
    candidate_wer = validate_metric_map(candidate_wer, "candidate WER")
    base_wer = validate_metric_map(base_wer, "base WER")
    eos_rate = validate_metric_map(eos_rate, "EOS rate", lo=0.0, hi=1.0)
    cap_hit_rate = validate_metric_map(cap_hit_rate, "cap-hit rate",
                                       lo=0.0, hi=1.0)
    termination_failures = validate_termination_failures(
        termination_failures, eos_rate, cap_hit_rate, termination_gate)

    failures = []
    limit = int(termination_gate[
        "max_unique_failures_per_language_per_checkpoint"])
    bad_termination = {
        language: item["count"]
        for language, item in termination_failures.items()
        if item["count"] > limit
    }
    if bad_termination:
        failures.append(
            f"unique termination failures exceed {limit} for "
            f"{bad_termination}")

    lang, reg = worst_regression(candidate_wer, base_wer)
    if reg > MAX_LANGUAGE_REGRESSION:
        failures.append(
            f"{lang} regressed {reg:+.4f} WER against the base arm, over the "
            f"{MAX_LANGUAGE_REGRESSION} cap -- an aggregate improvement must "
            "not hide one language collapsing")

    cand_macro, base_macro = macro_wer(candidate_wer), macro_wer(base_wer)
    if cand_macro > base_macro:
        failures.append(f"macro WER {cand_macro:.4f} is worse than the base "
                        f"macro {base_macro:.4f}")

    return {
        "macro_wer": round(cand_macro, 6),
        "base_macro_wer": round(base_macro, 6),
        "worst_language": lang,
        "worst_language_regression": round(reg, 6),
        "min_eos_rate": min(eos_rate.values()) if eos_rate else None,
        "max_cap_hit_rate": max(cap_hit_rate.values()) if cap_hit_rate else None,
        "max_unique_termination_failures": max(
            (v["count"] for v in termination_failures.values()), default=0),
        "termination_failures": termination_failures,
        "gates": {"termination_count": not bad_termination,
                  "eos_rate": not bad_termination,
                  "cap_hit_rate": not bad_termination,
                  "per_language_regression": reg <= MAX_LANGUAGE_REGRESSION,
                  "macro_not_worse": cand_macro <= base_macro},
        "passed": not failures,
        "failures": failures,
    }


def apply_termination_recurrence(gate: dict, prior: dict[str, set[str]]) -> dict:
    """Fail a later checkpoint if one checksum repeats for that language."""
    out = {
        **gate,
        "gates": dict(gate.get("gates") or {}),
        "failures": list(gate.get("failures") or []),
    }
    recurring = {}
    for language in VALIDATION_LANGUAGES:
        current = set(
            gate["termination_failures"][language]["checksums"])
        repeated = sorted(current & set(prior.get(language, set())))
        if repeated:
            recurring[language] = repeated
    out["recurrent_termination_checksums"] = recurring
    out["gates"]["termination_recurrence"] = not recurring
    if recurring:
        out["failures"].append(
            "termination checksum recurred at an independent checkpoint: "
            + json.dumps(recurring, sort_keys=True))
    out["passed"] = bool(gate.get("passed")) and not recurring
    return out


def apply_checkpoint_controls(gate: dict, smoke_result: dict | None) -> dict:
    """Add the saved-artifact smoke as a hard checkpoint gate.

    WER and EOS can look healthy when a LoRA is inert and the untouched base is
    doing all the work.  This control proves the saved artifact was reloaded,
    changes logits, has finite loss/gradients, and terminates.
    """
    out = {
        **gate,
        "gates": dict(gate.get("gates") or {}),
        "failures": list(gate.get("failures") or []),
    }
    passed = bool(smoke_result and smoke_result.get("passed"))
    out["gates"]["saved_adapter_smoke"] = passed
    if not passed:
        reasons = (smoke_result or {}).get(
            "reasons", ["saved-adapter smoke result is absent"])
        out["failures"].append(
            "saved-adapter smoke failed: " + "; ".join(map(str, reasons)))
    out["passed"] = bool(gate.get("passed")) and passed
    return out


def select_lr(results: list[dict]) -> dict:
    """Pick the learning rate. Deterministic, including under ties.

    `results`: [{"lr": float, "gate": <evaluate_gates output>}, ...]

    Ties are not hypothetical -- macro WER is stored rounded, so two candidates
    can genuinely present the same value. Without a stated rule the winner
    would depend on dict ordering, which is not a decision anyone made.
    """
    eligible = [r for r in results if r["gate"]["passed"]]
    if not eligible:
        raise SystemExit(
            "REFUSING: no learning rate passed all four gates. Selecting the "
            "least-bad of a failing set would produce a candidate that no gate "
            "endorsed.\n  " + "\n  ".join(
                f"lr={r['lr']:.0e}: {'; '.join(r['gate']['failures'])}"
                for r in results))
    # 1) lowest macro WER  2) smallest worst-language regression  3) lower LR
    ranked = sorted(eligible, key=lambda r: (r["gate"]["macro_wer"],
                                             r["gate"]["worst_language_regression"],
                                             r["lr"]))
    best = ranked[0]
    tied = [r for r in ranked
            if r["gate"]["macro_wer"] == best["gate"]["macro_wer"]]
    return {
        "selected_lr": best["lr"],
        "macro_wer": best["gate"]["macro_wer"],
        "worst_language_regression": best["gate"]["worst_language_regression"],
        "candidates_evaluated": len(results),
        "candidates_passing_all_four_gates": len(eligible),
        "tie_broken": len(tied) > 1,
        "tie_break_rule": ("lowest macro WER, then smallest worst-language "
                           "regression, then lower learning rate"),
        "ranking": [{"lr": r["lr"], "macro_wer": r["gate"]["macro_wer"],
                     "worst_regression": r["gate"]["worst_language_regression"]}
                    for r in ranked],
        "final_run_resumes_this_checkpoint": FINAL_RUN_RESUMES_SWEEP,
        "final_run_note": ("the final run starts FROM SCRATCH at the selected "
                           "learning rate; resuming would give the winner a "
                           f"{SWEEP_STEPS}-step head start no other candidate "
                           "had"),
    }


# --------------------------------------------------------------------------- #
# base arm reuse
# --------------------------------------------------------------------------- #
def base_arm_key(image_digest: str, gen_fingerprint: str, evaluator_sha: str,
                 manifest_hashes: dict[str, str], normalization: dict[str, str]
                 ) -> str:
    """Identity of a base evaluation. Reuse is valid only for an exact match.

    The base arm is evaluated ONCE, before the sweep, and reused across all
    four runs -- but only while every input that could move its numbers is
    identical. Anything else and it is a different measurement wearing the same
    name.
    """
    payload = {"image_digest": image_digest, "generation": gen_fingerprint,
               "evaluator_sha256": evaluator_sha,
               "manifests": dict(sorted(manifest_hashes.items())),
               "normalization": dict(sorted(normalization.items()))}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def may_reuse_base(stored: dict, current_key: str) -> bool:
    if stored.get("base_arm_key") != current_key:
        raise SystemExit(
            "REFUSING to reuse the base evaluation: its identity key does not "
            "match the current configuration. Image digest, generation config, "
            "evaluator hash, manifests or normalization changed, so the stored "
            "numbers describe a different measurement.")
    return True


# --------------------------------------------------------------------------- #
# artifact prefixes
# --------------------------------------------------------------------------- #
def evaluation_prefix(training_run_id: str, step: int | str) -> str:
    if not training_run_id or "/" in training_run_id:
        raise ValueError(f"bad training_run_id {training_run_id!r}")
    return f"candidates/evaluations/{training_run_id}/checkpoint-{step}/"


def require_absent(cli, bucket: str, prefix: str) -> None:
    """Fail closed: an occupied prefix is never written to.

    An empty listing is required, and a listing that ERRORS is not an empty
    one -- treating an API failure as absence is how the first evaluation
    launch nearly overwrote a record it could not read.
    """
    from botocore.exceptions import ClientError
    try:
        page = cli.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=1)
    except ClientError as e:
        raise SystemExit(
            f"REFUSING: cannot determine whether {prefix} is empty "
            f"({e.response.get('Error', {}).get('Code')}). An error is not an "
            "absence.")
    if page.get("KeyCount", 0) > 0:
        raise SystemExit(
            f"REFUSING: s3://{bucket}/{prefix} already contains objects. "
            "Checkpoint and evaluation prefixes are write-once; overwriting "
            "would destroy the evidence for a run that already happened.")
