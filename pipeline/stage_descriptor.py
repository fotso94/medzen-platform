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
import re

from pipeline import language_scope

STAGES = ("base_and_preflight", "sweep", "final", "diagnostic",
          "decode_compatibility")

# Every field is required. A descriptor missing one is not a weaker
# descriptor -- it is an unanswered question about what the instance will do.
REQUIRED = (
    "campaign_run", "attempt", "stage",
    "git_sha", "bundle_tar_sha256", "image_digest",
    "policy_sha256", "adoption_key", "dataset_fingerprint",
    "language_scope_sha256", "training_languages", "validation_languages",
    "base_manifest_sha256", "validation_manifest_sha256",
    "base_arm_key", "base_artifact_key", "base_artifact_sha256",
    "generation_config_fingerprint", "evaluator_sha256",
    "lr", "seed", "max_steps", "checkpoint_steps",
    "reservation_id", "watchdog_s",
    "input_prefix", "input_artifact_sha256", "output_prefix",
    "mlflow_parent_run_id", "mlflow_child_run_id",
    "purpose", "promotable",
)

HEX64 = ("git_sha_len_40",)          # handled explicitly below


def _lower_hex(value, size: int) -> bool:
    text = str(value)
    return len(text) == size and all(c in "0123456789abcdef" for c in text)


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
    for field, size in (("campaign_run", 80), ("attempt", 64)):
        value = str(d[field])
        if (len(value) > size
                or re.fullmatch(r"[a-z0-9][a-z0-9-]*", value) is None):
            problems.append(
                f"{field} must be a lowercase path-safe identifier")
    if d["stage"] not in STAGES:
        problems.append(f"stage {d['stage']!r} is not one of {STAGES}")
    if not _lower_hex(d["git_sha"], 40):
        problems.append("git_sha must be 40 lowercase hex characters")
    image = str(d["image_digest"])
    if not image.startswith("sha256:") or not _lower_hex(image[7:], 64):
        problems.append("image_digest must be sha256:<64 hex>")
    for f in ("bundle_tar_sha256", "policy_sha256", "dataset_fingerprint",
              "language_scope_sha256",
              "base_manifest_sha256", "validation_manifest_sha256",
              "generation_config_fingerprint", "evaluator_sha256"):
        if not _lower_hex(d[f], 64):
            problems.append(f"{f} must be 64 lowercase hex characters")
    if d["language_scope_sha256"] != language_scope.LANGUAGE_SCOPE_SHA256:
        problems.append(
            "language_scope_sha256 differs from the approved B4 scope record")
    for field in ("training_languages", "validation_languages"):
        value = d[field]
        if (not isinstance(value, list)
                or len(set(value)) != len(value)
                or any(not isinstance(language, str) or not language
                       for language in value)):
            problems.append(f"{field} must be a unique string list")
    deferred = set(language_scope.DEFERRED_LANGUAGES)
    for field in ("training_languages", "validation_languages"):
        present = sorted(deferred & set(d[field]))
        if present:
            problems.append(
                f"{field} contains deferred B4 language(s) {present}")
    if d["stage"] in ("base_and_preflight", "sweep", "final"):
        if tuple(d["training_languages"]) != language_scope.TRAINING_LANGUAGES:
            problems.append(
                "training_languages differ from the approved B4 active scope")
        if tuple(d["validation_languages"]) != language_scope.VALIDATION_LANGUAGES:
            problems.append(
                "validation_languages differ from the approved B4 active scope")
    elif d["training_languages"]:
        problems.append("a zero-training diagnostic cannot name training languages")
    if d["purpose"] != "training_system_validation":
        problems.append("purpose must be training_system_validation")
    if d["promotable"] is not False:
        problems.append("promotable must be False; Option B is non-promotable")
    if d["stage"] == "sweep" and d["max_steps"] != 100:
        problems.append("a sweep stage trains exactly 100 steps")
    if d["stage"] == "base_and_preflight" and d["lr"] is not None:
        problems.append("base_and_preflight has no learning rate")
    if d["stage"] == "base_and_preflight":
        if any(d[f] is not None for f in (
                "base_arm_key", "base_artifact_key",
                "base_artifact_sha256")):
            problems.append(
                "base_and_preflight cannot consume a base artifact it is "
                "responsible for producing")
    else:
        for f in ("base_arm_key", "base_artifact_sha256"):
            if not _lower_hex(d[f], 64):
                problems.append(f"{f} must be 64 lowercase hex characters")
        if not str(d["base_artifact_key"]).startswith("candidates/"):
            problems.append("base_artifact_key must be under candidates/")
    if d["stage"] in ("diagnostic", "decode_compatibility"):
        if d["max_steps"] != 0 or d["checkpoint_steps"] != []:
            problems.append(
                f"{d['stage']} performs zero training steps")
        if d["lr"] != 1e-4:
            problems.append(
                f"{d['stage']} is bound to the retained 1e-4 adapter")
        if not _lower_hex(d["input_artifact_sha256"], 64):
            problems.append(
                f"{d['stage']} input_artifact_sha256 must pin the adapter tree")
        input_prefix = str(d["input_prefix"])
        if (not input_prefix.startswith("candidates/evaluations/")
                or "/asr/checkpoint-100" not in input_prefix
                or ".." in input_prefix or "//" in input_prefix):
            problems.append(
                f"{d['stage']} input_prefix must be a confined checkpoint-100 "
                "artifact prefix")
    elif d["input_artifact_sha256"] is not None:
        problems.append(
            "input_artifact_sha256 is only valid for a no-training "
            "diagnostic stage")
    if not str(d["output_prefix"]).startswith("candidates/"):
        problems.append("output_prefix must be under candidates/")
    authorised_root = (
        f"candidates/evaluations/{d['campaign_run']}/"
        f"attempt-{d['attempt']}/")
    if (not str(d["output_prefix"]).startswith(authorised_root)
            or ".." in str(d["output_prefix"])
            or "//" in str(d["output_prefix"])):
        problems.append(
            f"output_prefix must be confined under {authorised_root}")
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
