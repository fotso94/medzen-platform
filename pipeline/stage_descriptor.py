"""The immutable description of one EC2 stage, and its hash.

Everything a stage is allowed to do is written down once, hashed, and carried
into user-data. The container echoes the hash back in its result; a mismatch
means the instance ran something other than what was authorised, and the
campaign refuses the result rather than recording it.

This is what makes "one reservation, one instance, one stage" checkable after
the fact instead of asserted in a plan.
"""
from __future__ import annotations

import hashlib
import json

STAGES = ("base_and_preflight", "sweep", "final")

# Every field is required. A descriptor missing one is not a weaker
# descriptor -- it is an unanswered question about what the instance will do.
REQUIRED = (
    "campaign_run", "attempt", "stage",
    "git_sha", "bundle_tar_sha256", "image_digest",
    "policy_sha256", "adoption_key", "dataset_fingerprint",
    "base_manifest_sha256", "validation_manifest_sha256",
    "generation_config_fingerprint", "evaluator_sha256",
    "lr", "seed", "max_steps", "checkpoint_steps",
    "reservation_id", "watchdog_s",
    "input_prefix", "output_prefix",
    "mlflow_parent_run_id", "mlflow_child_run_id",
    "purpose", "promotable",
)

HEX64 = ("git_sha_len_40",)          # handled explicitly below


def build(**kw) -> dict:
    """Assemble and validate. Refuses anything incomplete or contradictory."""
    missing = [k for k in REQUIRED if k not in kw]
    if missing:
        raise SystemExit(f"REFUSING: stage descriptor is missing {missing}")
    extra = [k for k in kw if k not in REQUIRED]
    if extra:
        raise SystemExit(f"REFUSING: unexpected descriptor field(s) {extra}; "
                         "the schema is closed so a typo cannot become a "
                         "silently-ignored setting")

    d = dict(kw)
    problems = []
    if d["stage"] not in STAGES:
        problems.append(f"stage {d['stage']!r} is not one of {STAGES}")
    if len(str(d["git_sha"])) != 40:
        problems.append("git_sha must be the full 40 characters")
    if not str(d["image_digest"]).startswith("sha256:"):
        problems.append("image_digest must be sha256:<64 hex>")
    for f in ("bundle_tar_sha256", "policy_sha256", "dataset_fingerprint",
              "base_manifest_sha256", "generation_config_fingerprint",
              "evaluator_sha256"):
        if len(str(d[f])) != 64:
            problems.append(f"{f} must be 64 hex characters")
    if d["purpose"] != "training_system_validation":
        problems.append("purpose must be training_system_validation")
    if d["promotable"] is not False:
        problems.append("promotable must be False; Option B is non-promotable")
    if d["stage"] == "sweep" and d["max_steps"] != 100:
        problems.append("a sweep stage trains exactly 100 steps")
    if d["stage"] == "base_and_preflight" and d["lr"] is not None:
        problems.append("base_and_preflight has no learning rate")
    if not str(d["output_prefix"]).startswith("candidates/"):
        problems.append("output_prefix must be under candidates/")
    if problems:
        raise SystemExit("REFUSING: invalid stage descriptor —\n  "
                         + "\n  ".join(problems))
    return d


def descriptor_hash(d: dict) -> str:
    """Stable hash of the descriptor. Carried in user-data and every result."""
    return hashlib.sha256(
        json.dumps(d, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_result(descriptor: dict, result: dict) -> dict:
    """The instance must prove it ran THIS descriptor, and nothing else.

    Checks the echoed hash, the identity fields a result carries anyway, and
    the lifecycle evidence that distinguishes a finished stage from one that is
    merely quiet.
    """
    want = descriptor_hash(descriptor)
    problems = []
    if result.get("stage_descriptor_sha256") != want:
        problems.append(
            f"result echoes descriptor {str(result.get('stage_descriptor_sha256'))[:16]}, "
            f"authorised {want[:16]}; the instance ran something else")
    for f in ("campaign_run", "attempt", "stage"):
        if result.get(f) != descriptor[f]:
            problems.append(f"{f}: result {result.get(f)!r} != "
                            f"descriptor {descriptor[f]!r}")
    for f in ("instance_id", "launched_utc", "terminated_utc",
              "actual_seconds", "exit_status", "root_volume_deleted"):
        if result.get(f) in (None, ""):
            problems.append(f"result is missing {f}")
    if result.get("root_volume_deleted") is not True:
        problems.append("root volume deletion is not confirmed")
    if result.get("exit_status") != 0:
        problems.append(f"exit status {result.get('exit_status')}")
    if problems:
        raise SystemExit("REFUSING: stage result does not match what was "
                         "authorised —\n  " + "\n  ".join(problems))
    return result
