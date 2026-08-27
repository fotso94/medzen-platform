#!/usr/bin/env python3
"""B5 SageMaker training-job authoring (work item C2): render / validate / launch.

The same discipline as the pilot executor, applied to CreateTrainingJob:

  render    derives the EXACT request from a bindings file — every
            environment-specific value (image digest, subnets, security
            group, data version, cost ceiling) comes from bindings built
            and reviewed at packet time; nothing here is invented;
  validate  re-derives and compares byte-for-byte, then screens the
            request against prohibited scopes and the cost ceiling —
            a drifted request is a refusal, not a warning;
  launch    refuses unless the shared-file review for this job id exists
            (the driver's gate, verbatim in spirit), then submits the
            validated request with the aws CLI and prints the ARN.

Only launch touches AWS. Spot is mandatory: the ceiling arithmetic uses
the ON-DEMAND rate, so the true spend lands at or under ~35% of it.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import re
import subprocess
import sys
from pathlib import Path

ACCOUNT = "558069890522"
REGION = "eu-central-1"
BUCKET = "medzen-speech"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/medzen-trainer-role"
INSTANCE_ALLOWLIST = {"ml.g6.xlarge"}
# g6 carries local NVMe instance storage: hardware-encrypted at rest by AWS,
# and CreateTrainingJob REFUSES VolumeKmsKeyId for it (live refusal at T5
# launch). S3 output KMS is separate and always required.
NVME_LOCAL_STORAGE_TYPES = {"ml.g6.xlarge"}
ON_DEMAND_USD_PER_HOUR = {"ml.g6.xlarge": 1.60}  # DELIBERATELY above any
# published eu-central-1 SageMaker rate (~$1.2-1.3/h at last check; EC2
# g6.xlarge is $0.805 in the B4 design table). This constant only converts
# max_runtime into worst-case dollars for the ceiling refusal, so erring
# high can only refuse too eagerly, never authorize too much.
SHARED_REVIEWS = Path.home() / "Documents/medzen-shared/claude_instructions.txt"
SHA_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
PROHIBITED_SUBSTRINGS = (
    "iam:", "approved/asr", "/medzen/registry/production",
    "model-registration", "mlflow", "eval/",
)
REQUIRED_ENVIRONMENT = (
    "MEDZEN_VARIANT", "MEDZEN_MANIFEST_VERSION", "MEDZEN_LANGUAGES",
    "MEDZEN_SEED", "MEDZEN_MAX_STEPS",
    # Codex review #4: a packet that omitted the mode silently trained
    # LoRA — every packet now DECLARES what kind of training it buys
    "MEDZEN_TRAIN_MODE",
)


class JobRefusal(RuntimeError):
    pass


def _tag_safe(value: str) -> str:
    allowed = set("abcdefghijklmnopqrstuvwxyz"
                  "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 _.:/=+-@")
    return "".join(c for c in str(value) if c in allowed)[:256].strip()


def _require(bindings: dict, key: str):
    value = bindings.get(key)
    if value in (None, "", [], {}):
        raise JobRefusal(f"bindings key {key!r} is required and absent")
    return value


_KD_TRUE = {"1", "true", "yes", "on"}
# Codex review #20 F4: canonical Arm-2 verifier contract, pinned so the packet
# cannot defang the checker. Mirrors scripts/verify_arm2_calibration.py.
ARM2_CANONICAL_VERIFIER_SCRIPT = "scripts/verify_arm2_calibration.py"
ARM2_CANONICAL_METRICS_ARTIFACT = "calibration-metrics.json"
ARM2_L4_PHYSICAL_BYTES = 24 * 1024 * 1024 * 1024  # g6.xlarge single NVIDIA L4
ARM2_MANDATORY_DEV_SENTINELS = frozenset({"lingala", "swahili"})
# The EXACT frozen historical Arm-2 calibration packet — bound by CANONICAL SHA
# (not job_id, which a new packet could reuse). Only this packet, in its
# committed / DRAFT pre-image / launchable forms, may rely on legacy KD-on mode
# inference AND the result_verifier-coverage preservation fallback. Every new
# comparative packet must declare MEDZEN_EXECUTION_MODE and a `comparative`
# block explicitly.
FROZEN_ARM2_CALIBRATION_SHAS = frozenset({
    "3c5024edee3a9df098f1f9e3bdbccc044c963e53f761dc173dbafd1b6a4f9c7e",  # committed
    "3d2dc03e9064371b30c2dc48267a03f8f7814ad26146e60955187ec8bcd5193a",  # DRAFT
    "52eb689b4593a598583861d89032608d536c3ef5afc73ce35925a8f8709500e4",  # launchable
})


def _parse_env_weights(raw: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for pair in (raw or "").split(","):
        if not pair.strip():
            continue
        lang, _, val = pair.partition("=")
        out[lang.strip().lower()] = float(val)
    return out


def arm2_acceptance_criteria(spec: dict) -> list:
    """The CANONICAL human-facing acceptance criteria, derived from the machine
    contract (Codex review #21 F6: the prose criteria were freely mutable —
    `["PASS"]` rendered fine — so the human contract could contradict the
    machine contract; deriving the prose FROM result_verifier and requiring
    byte-equality makes drift impossible).

    Codex stage-1 review (2026-08-25) finding 4a: the KD-OFF comparative
    control declares result_verifier.kd_enabled=false and its verifier requires
    ZERO KD — the criteria must say the same thing, not demand positive KD."""
    steps = int(spec["expected_steps"])
    ceiling = int(spec["gpu_memory_ceiling_bytes"])
    dev = sorted(str(x).strip().lower() for x in spec["dev_sentinel_languages"])
    cov = sorted(str(x).strip().lower()
                 for x in spec["required_preservation_coverage"])
    kd_off = spec.get("kd_enabled") is False
    kd_lines = ([
        "separate CTC / KD / total loss logged per step, steps contiguous 1..N; KD identically ZERO on every step (KD-disabled control); total == ctc",
        "no per-language KD coverage requirement (KD disabled) — the control still records the shared preservation coverage surface: " + ", ".join(cov),
    ] if kd_off else [
        "separate CTC / KD / total loss logged per step, steps contiguous 1..N; KD positive and finite on every step; total == ctc + alpha*kd",
        "per-language KD coverage (rows and valid frames) recorded for every preservation language: " + ", ".join(cov),
    ])
    return [
        f"the run completes exactly {steps} steps or fails closed (no silent success)",
        *kd_lines,
        f"peak GPU memory recorded and <= {ceiling} bytes",
        "throughput recorded: steps/min and samples/s both > 0 (wall time cumulative across resume)",
        "the merged export loads (strict key mapping) and serves (readyz) with no adapter residue and finite weights",
        "dev-sentinel WER recorded on the PREDECLARED frozen slices (path+sha256+rows bound in result_verifier.dev_manifests) for: " + ", ".join(dev) + " (directional read, NOT a promotion signal)",
        "identity bound: run fingerprint, derived job name, export manifest+model shas, predeclared dev-manifest shas, canonical scorer, packet canonical sha, execution-contract sha, verifier bytes sha",
        "per-row dev receipts (audio checksum, normalized hypothesis, edit distance, ref words) recorded and RECOMPUTED against the committed slices",
        "MANDATORY in-run upstream decode parity: the scorer path (resample16k + utterance z-norm + seqlen-truncated CTC-greedy + skip-special decode) matches the pinned omnilingual_asr ASRInferencePipeline on >=1 fetched row per dev language, on the fresh base model, fail-never-skip; receipt in metrics.parity",
        "machine verdict: scripts/verify_arm2_calibration.py PASS in AUTHORITATIVE --live mode (the verifier itself pins account/region, calls DescribeTrainingJob, fetches ModelArtifacts from the KMS-encrypted output with VersionId, extracts and hashes model/manifest/metrics; local files are never authoritative)",
    ]


def validate_arm2_semantics(bindings: dict, environment: dict) -> None:
    """Codex review #19 F4: the top-level `distillation` recipe and the
    `environment` KD variables can silently DISAGREE — the launcher validated
    the environment via parse_config but never cross-checked the human-facing
    recipe against it, and accepted an EMPTY acceptance_criteria. Reproduced:
    top-level alpha 0.9 vs env alpha 0.5 with emptied criteria still PASSED.

    This makes the packet self-consistent or refuses:
      - KD-enabled env <=> a `distillation` block (both, or neither);
      - every duplicated field (alpha, temperature, teacher card/mode,
        preservation languages, per-language weights) is byte-equal across the
        two representations;
      - acceptance_criteria is a NON-EMPTY list;
      - a `result_verifier` block binds the executable checker + schema so the
        result is machine-enforced, not eyeballed."""
    kd_on = str(environment.get("MEDZEN_KD_ENABLE", "0")).strip().lower() in _KD_TRUE
    recipe = bindings.get("distillation")
    if kd_on and not isinstance(recipe, dict):
        raise JobRefusal(
            "MEDZEN_KD_ENABLE is truthy but the packet has no top-level "
            "`distillation` recipe to cross-check — a KD run must declare its "
            "recipe in one canonical, reviewable place")
    if recipe is not None and not kd_on:
        raise JobRefusal(
            "the packet declares a `distillation` recipe but "
            "MEDZEN_KD_ENABLE is not truthy — the recipe and the environment "
            "disagree about whether this is a KD run")
    comparative = is_arm2_comparative(environment)
    if not comparative:
        return                  # plain training carries no Arm-2 semantics

    # Legacy KD-on mode inference AND the coverage fallback below are limited to
    # the EXACT frozen historical calibration packet, bound by CANONICAL SHA.
    frozen_historical = \
        canonical_bindings_sha256(bindings) in FROZEN_ARM2_CALIBRATION_SHAS
    if not str(environment.get("MEDZEN_EXECUTION_MODE", "")).strip() and \
            not frozen_historical:
        raise JobRefusal(
            "a new Arm-2 comparative packet must set MEDZEN_EXECUTION_MODE="
            "'arm2_comparative' explicitly — legacy KD-on inference is limited "
            "to the frozen historical calibration packet (bound by SHA)")

    # KD-on candidates cross-check the human-facing recipe against the env; the
    # KD-off comparative CONTROL has no `distillation` block, so it skips these
    # and carries only the shared acceptance_criteria + result_verifier below.
    if kd_on:
        def _mismatch(field: str, recipe_value, env_value) -> None:
            raise JobRefusal(
                f"Arm-2 recipe/environment disagree on {field}: recipe "
                f"{recipe_value!r} vs environment {env_value!r} — one canonical "
                "recipe, no silent divergence (Codex review #19 F4)")

        if float(recipe.get("kd_alpha")) != \
                float(environment.get("MEDZEN_KD_ALPHA")):
            _mismatch("kd_alpha", recipe.get("kd_alpha"),
                      environment.get("MEDZEN_KD_ALPHA"))
        if float(recipe.get("kd_temperature")) != float(
                environment.get("MEDZEN_KD_TEMPERATURE")):
            _mismatch("kd_temperature", recipe.get("kd_temperature"),
                      environment.get("MEDZEN_KD_TEMPERATURE"))
        if str(recipe.get("teacher_card")) != str(
                environment.get("MEDZEN_KD_TEACHER_CARD")):
            _mismatch("teacher_card", recipe.get("teacher_card"),
                      environment.get("MEDZEN_KD_TEACHER_CARD"))
        if str(recipe.get("teacher_mode")) != str(
                environment.get("MEDZEN_KD_TEACHER_MODE")):
            _mismatch("teacher_mode", recipe.get("teacher_mode"),
                      environment.get("MEDZEN_KD_TEACHER_MODE"))
        # preservation_languages agreement is checked below against the SHARED
        # comparative preservation set (so KD-on and the KD-off control use one
        # source of truth); here only the KD-specific recipe fields.
        recipe_weights = {str(k).strip().lower(): float(v) for k, v in
                          (recipe.get("language_weights") or {}).items()}
        env_weights = _parse_env_weights(
            str(environment.get("MEDZEN_KD_LANGUAGE_WEIGHTS", "")))
        if recipe_weights != env_weights:
            _mismatch("language_weights", recipe_weights, env_weights)
        # Arm-2b (owner decision 2026-08-27): the retention anchor's recipe
        # fields are cross-checked exactly like every other KD field — one
        # canonical recipe, no silent divergence (same F4 discipline).
        retention_recipe = recipe.get("retention")
        if str(recipe.get("teacher_mode")) == "base+arm1_retention":
            if not isinstance(retention_recipe, dict):
                raise JobRefusal(
                    "teacher_mode base+arm1_retention requires a "
                    "distillation.retention recipe block (alpha, languages, "
                    "teacher pins) — no silent anchor")
            if float(retention_recipe.get("alpha")) != float(
                    environment.get("MEDZEN_KD_RETENTION_ALPHA")):
                _mismatch("retention.alpha", retention_recipe.get("alpha"),
                          environment.get("MEDZEN_KD_RETENTION_ALPHA"))
            recipe_ret = {str(x).strip().lower()
                          for x in retention_recipe.get("languages", [])}
            env_ret_langs = {t.strip().lower() for t in str(
                environment.get("MEDZEN_KD_RETENTION_LANGUAGES", "")
                ).split(",") if t.strip()}
            if not recipe_ret or recipe_ret != env_ret_langs:
                _mismatch("retention.languages", sorted(recipe_ret),
                          sorted(env_ret_langs))
            ret_teacher = retention_recipe.get("teacher") or {}
            for field, env_key in (
                    ("s3_uri", "MEDZEN_KD_RETENTION_TEACHER_S3_URI"),
                    ("s3_version_id",
                     "MEDZEN_KD_RETENTION_TEACHER_VERSION_ID"),
                    ("sha256", "MEDZEN_KD_RETENTION_TEACHER_SHA256")):
                if str(ret_teacher.get(field)) != str(
                        environment.get(env_key)):
                    _mismatch(f"retention.teacher.{field}",
                              ret_teacher.get(field),
                              environment.get(env_key))
        elif retention_recipe is not None:
            raise JobRefusal(
                "the packet carries a distillation.retention block but "
                "teacher_mode is not base+arm1_retention — contradictory")

    criteria = bindings.get("acceptance_criteria")
    if not isinstance(criteria, list) or not criteria:
        raise JobRefusal(
            "an Arm-2 KD packet must carry a NON-EMPTY acceptance_criteria "
            "list — emptying it (Codex review #19 F4 reproduction) must refuse")
    spec = bindings.get("result_verifier")
    if not isinstance(spec, dict):
        raise JobRefusal(
            "an Arm-2 KD packet must bind a `result_verifier` block (the "
            "executable checker + metrics schema) so the calibration result "
            "is machine-enforced, not eyeballed (Codex review #19 F3/F4)")
    for key in ("script", "metrics_schema", "metrics_artifact",
                "expected_steps", "gpu_memory_ceiling_bytes",
                "required_preservation_coverage", "dev_sentinel_languages"):
        if key not in spec:
            raise JobRefusal(f"result_verifier lacks {key!r}")
    # Codex review #20 F4: the earlier check validated PRESENCE, not the
    # canonical contract — a nonexistent script, a traversal artifact,
    # expected_steps=1 while training runs 30, an enormous GPU ceiling and an
    # empty dev-language list all passed. Pin every field to its canonical
    # value/bound so the packet cannot smuggle a defanged verifier.
    # Arm-2b: /1 is the single-KD artifact schema; /2 (dual-KD retention
    # anchor) is admitted here and pinned to the retention teacher-mode by
    # the retention-consistency block below — neither layer alone decides.
    if str(spec["metrics_schema"]) not in ("b5-arm2-calibration-metrics/1",
                                           "b5-arm2-calibration-metrics/2"):
        raise JobRefusal(
            "result_verifier.metrics_schema must be "
            "'b5-arm2-calibration-metrics/1' (single-KD) or "
            "'b5-arm2-calibration-metrics/2' (dual-KD retention anchor) — "
            "the trainer's artifact schemas")
    if str(spec["script"]) != ARM2_CANONICAL_VERIFIER_SCRIPT:
        raise JobRefusal(
            f"result_verifier.script must be {ARM2_CANONICAL_VERIFIER_SCRIPT!r}"
            " — a packet cannot substitute its own checker")
    if str(spec["metrics_artifact"]) != ARM2_CANONICAL_METRICS_ARTIFACT:
        raise JobRefusal(
            "result_verifier.metrics_artifact must be "
            f"{ARM2_CANONICAL_METRICS_ARTIFACT!r} (no path traversal)")
    # expected_steps must equal the run's OWN MEDZEN_MAX_STEPS — not an
    # arbitrary tiny number that a 30-step run would trivially satisfy
    env_max_steps = int(environment.get("MEDZEN_MAX_STEPS"))
    if int(spec["expected_steps"]) != env_max_steps:
        raise JobRefusal(
            f"result_verifier.expected_steps {spec['expected_steps']} must "
            f"equal MEDZEN_MAX_STEPS {env_max_steps} — the verifier must "
            "require the full step budget the job actually runs")
    ceiling = spec["gpu_memory_ceiling_bytes"]
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) \
            or not (0 < ceiling <= ARM2_L4_PHYSICAL_BYTES):
        raise JobRefusal(
            f"result_verifier.gpu_memory_ceiling_bytes {ceiling!r} must be in "
            f"1..{ARM2_L4_PHYSICAL_BYTES} (the g6.xlarge L4's physical 24 GiB)")
    dev_langs = spec["dev_sentinel_languages"]
    if not isinstance(dev_langs, list) or not dev_langs:
        raise JobRefusal(
            "result_verifier.dev_sentinel_languages must be a non-empty list")
    dev_set = {str(x).strip().lower() for x in dev_langs}
    if not ARM2_MANDATORY_DEV_SENTINELS.issubset(dev_set):
        raise JobRefusal(
            "result_verifier.dev_sentinel_languages must include the "
            f"regression sentinels {sorted(ARM2_MANDATORY_DEV_SENTINELS)}")
    # SHARED preservation set for BOTH comparative arms (fixes the KD-off
    # UnboundLocalError): a comparative packet may declare it in a top-level
    # `comparative.preservation_languages` block; the frozen historical packet
    # predates that block, so its result_verifier.required_preservation_coverage
    # is the equivalent (backward-compat).
    coverage = {str(x).strip().lower()
                for x in spec["required_preservation_coverage"]}
    comp_block = bindings.get("comparative") or {}
    if comp_block:
        pres = {str(x).strip().lower()
                for x in comp_block.get("preservation_languages", [])}
        if not pres:
            raise JobRefusal(
                "comparative.preservation_languages must be a non-empty list")
        if coverage != pres:
            raise JobRefusal(
                "result_verifier.required_preservation_coverage must equal the "
                "comparative.preservation_languages")
    elif frozen_historical:
        pres = coverage        # coverage fallback ONLY for the frozen packet
    else:
        raise JobRefusal(
            "a new Arm-2 comparative packet must carry a top-level "
            "`comparative` block with preservation_languages — the "
            "result_verifier-coverage fallback is limited to the frozen "
            "historical calibration packet")
    if not pres:
        raise JobRefusal(
            "no preservation languages resolved for the comparative arm")
    # Arm-2b: dev sentinels may also measure RETENTION languages (the
    # trajectory gate reads pidgin-vs-arm1) — the measured set is bounded by
    # the languages the run's objectives anchor (preservation ∪ retention).
    env_retention = {t.strip().lower() for t in str(
        environment.get("MEDZEN_KD_RETENTION_LANGUAGES", "")).split(",")
        if t.strip()}
    if not dev_set.issubset(pres | env_retention):
        raise JobRefusal(
            "result_verifier.dev_sentinel_languages must be a subset of the "
            "preservation_languages plus (Arm-2b) the retention languages")
    # KD-on: the distillation recipe AND the env must agree with the SHARED set;
    # the KD-off control has no distillation block and skips this.
    if kd_on:
        recipe_pres = {str(x).strip().lower()
                       for x in recipe.get("preservation_languages", [])}
        env_pres = {t.strip().lower() for t in
                    str(environment.get("MEDZEN_KD_PRESERVATION_LANGUAGES", "")
                        ).split(",") if t.strip()}
        if recipe_pres != pres or env_pres != pres:
            _mismatch("preservation_languages",
                      sorted(recipe_pres or env_pres), sorted(pres))
    # KD-state must be honest: a KD-off comparative CONTROL declares
    # result_verifier.kd_enabled=false so the verifier skips the KD-only checks;
    # a KD-on packet must not claim otherwise (absent => legacy KD-on).
    spec_kd = spec.get("kd_enabled")
    if kd_on and spec_kd is False:
        raise JobRefusal("result_verifier.kd_enabled is false but the packet "
                         "enables KD — contradictory")
    if not kd_on and spec_kd is not False:
        raise JobRefusal(
            "the KD-off comparative control must declare "
            "result_verifier.kd_enabled=false so the verifier skips the "
            "KD-only checks (kd_positive_finite_steps, kd_coverage)")
    # Arm-2b both-layer pinning (mirror of the /1 discipline): a retention
    # run's spec must pin metrics_schema /2 AND a required_retention_coverage
    # equal to the env retention set; a non-retention packet must pin neither.
    retention_on = kd_on and str(
        environment.get("MEDZEN_KD_TEACHER_MODE", "base")).strip() \
        == "base+arm1_retention"
    spec_ret_cov = spec.get("required_retention_coverage")
    if retention_on:
        if str(spec.get("metrics_schema")) != "b5-arm2-calibration-metrics/2":
            raise JobRefusal(
                "a dual-KD retention packet must pin result_verifier."
                "metrics_schema 'b5-arm2-calibration-metrics/2'")
        spec_ret = {str(x).strip().lower() for x in (spec_ret_cov or [])}
        if not spec_ret or spec_ret != env_retention:
            raise JobRefusal(
                "result_verifier.required_retention_coverage must equal "
                "MEDZEN_KD_RETENTION_LANGUAGES — the retention-coverage "
                "check cannot drift from the anchor's language set")
    else:
        if spec_ret_cov is not None or str(
                spec.get("metrics_schema")) == "b5-arm2-calibration-metrics/2":
            raise JobRefusal(
                "/2 retention pins (metrics_schema or "
                "required_retention_coverage) on a non-retention packet — "
                "contradictory")
    # Arm-2b warm-start parity: MEDZEN_STUDENT_INIT_MODE=arm1 requires a
    # top-level student_init block agreeing field-for-field; a student_init
    # block without the env (or vice versa) is contradictory.
    init_block = bindings.get("student_init")
    env_init = str(environment.get("MEDZEN_STUDENT_INIT_MODE", "base")
                   ).strip() or "base"
    if env_init == "arm1":
        if not isinstance(init_block, dict):
            raise JobRefusal(
                "MEDZEN_STUDENT_INIT_MODE=arm1 requires a top-level "
                "student_init block (mode + s3_uri + s3_version_id + sha256) "
                "— the warm-start identity is reviewed, never env-only")
        for field, env_key in (
                ("mode", "MEDZEN_STUDENT_INIT_MODE"),
                ("s3_uri", "MEDZEN_STUDENT_INIT_S3_URI"),
                ("s3_version_id", "MEDZEN_STUDENT_INIT_VERSION_ID"),
                ("sha256", "MEDZEN_STUDENT_INIT_SHA256")):
            if str(init_block.get(field)) != str(environment.get(env_key)):
                raise JobRefusal(
                    f"student_init.{field} {init_block.get(field)!r} != "
                    f"environment {env_key} {environment.get(env_key)!r} — "
                    "one canonical warm-start identity, no silent divergence")
    elif init_block is not None:
        raise JobRefusal(
            "the packet declares a student_init block but "
            "MEDZEN_STUDENT_INIT_MODE is not 'arm1' — contradictory")

    # Codex review #20 F3: the calibration WRAPPER needs its inputs bound in the
    # environment — the packet path it verifies against, that packet's canonical
    # sha for identity binding, and a dev-sentinel manifest file for EVERY
    # dev-sentinel language. A KD packet missing this refuses.
    if not str(environment.get("MEDZEN_DEV_SENTINEL_MANIFEST_FILES", "")).strip():
        raise JobRefusal(
            "KD calibration requires MEDZEN_DEV_SENTINEL_MANIFEST_FILES in "
            "the environment (the wrapper's inputs — Codex review #20 F3)")
    dev_manifest_langs = {
        pair.partition("=")[0].strip().lower()
        for pair in str(environment["MEDZEN_DEV_SENTINEL_MANIFEST_FILES"]).split(",")
        if pair.strip()}
    if not dev_set.issubset(dev_manifest_langs):
        raise JobRefusal(
            "MEDZEN_DEV_SENTINEL_MANIFEST_FILES must provide a slice for every "
            f"dev-sentinel language {sorted(dev_set)}; got {sorted(dev_manifest_langs)}")

    # Codex review #22 blocker 2: the image/packet lifecycle was CIRCULAR —
    # the image baked the launch packet, but the final packet must contain the
    # image digest, which cannot exist inside an image built before it. The
    # image now bakes a SELF-REFERENCE-FREE EXECUTION CONTRACT (environment +
    # distillation + result_verifier + job_id, NO digest/cost fields), and the
    # launch packet binds that contract's path + sha alongside the digest.
    contract_decl = bindings.get("execution_contract")
    if not isinstance(contract_decl, dict):
        raise JobRefusal(
            "a KD packet must bind `execution_contract` {path, sha256} — the "
            "self-reference-free contract the image bakes (Codex #22 blocker 2)")
    contract_path = str(contract_decl.get("path") or "")
    contract_sha = str(contract_decl.get("sha256") or "")
    if not contract_path or contract_path.startswith("/") or ".." in contract_path:
        raise JobRefusal(
            f"execution_contract.path {contract_path!r} must be repo-relative "
            "without traversal")
    if not re.fullmatch(r"[0-9a-f]{64}", contract_sha):
        raise JobRefusal("execution_contract.sha256 must be 64-hex")
    repo_root_ec = Path(__file__).resolve().parents[1]
    contract_file = repo_root_ec / contract_path
    if not contract_file.exists():
        raise JobRefusal(
            f"execution contract {contract_path} is not committed")
    contract_bytes = contract_file.read_bytes()
    actual_contract_sha = hashlib.sha256(contract_bytes).hexdigest()
    if actual_contract_sha != contract_sha:
        raise JobRefusal(
            f"execution contract {contract_path} hashes to "
            f"{actual_contract_sha[:16]}, the packet declares "
            f"{contract_sha[:16]} — the contract drifted")
    contract = json.loads(contract_bytes)
    # the committed env must not pre-define the launcher-injected identity
    # keys — they are DERIVED at render, never hand-authored (checked FIRST so
    # the error names the real fault, not a downstream block mismatch)
    for injected in ("MEDZEN_CALIBRATION_PACKET", "MEDZEN_CALIBRATION_PACKET_SHA256",
                     "MEDZEN_TRAINING_JOB_NAME", "MEDZEN_EXECUTION_CONTRACT",
                     "MEDZEN_EXECUTION_CONTRACT_SHA256"):
        if injected in (bindings.get("environment") or {}):
            raise JobRefusal(
                f"the committed environment must not pre-define {injected} — "
                "the launcher derives and injects it at render")
    # the contract's shared blocks must BYTE-EQUAL the packet's — one truth
    for block in ("environment", "distillation", "result_verifier", "job_id"):
        if contract.get(block) != bindings.get(block):
            raise JobRefusal(
                f"execution contract {block!r} differs from the launch "
                "packet's — the image would run a different recipe than the "
                "one reviewed (Codex #22 blocker 2)")

    # Codex review #21 F6: the prose acceptance_criteria must EQUAL the
    # canonical list derived from the machine contract — a human-facing
    # contract that contradicts the machine contract refuses.
    if bindings.get("acceptance_criteria") != arm2_acceptance_criteria(spec):
        raise JobRefusal(
            "acceptance_criteria does not equal the canonical machine-derived "
            "list (arm2_acceptance_criteria) — the human-facing contract must "
            "not drift from the machine contract (Codex review #21 F6)")

    # Codex review #21 F4: the dev evaluation data must be PREDECLARED and
    # BOUND — path, sha256 and row count per dev-sentinel language — and the
    # committed files must match the declaration byte-for-byte. Without this
    # the wrapper could score any plausible-looking slice.
    dev_decl = spec.get("dev_manifests")
    if not isinstance(dev_decl, dict):
        raise JobRefusal(
            "result_verifier.dev_manifests must predeclare each dev-sentinel "
            "slice (path, sha256, rows, source) — Codex review #21 F4")
    env_dev_paths = {
        pair.partition("=")[0].strip().lower():
        pair.partition("=")[2].strip()
        for pair in str(environment["MEDZEN_DEV_SENTINEL_MANIFEST_FILES"]).split(",")
        if pair.strip()}
    repo_root = Path(__file__).resolve().parents[1]
    for lang in sorted(dev_set):
        decl = dev_decl.get(lang)
        if not isinstance(decl, dict):
            raise JobRefusal(
                f"result_verifier.dev_manifests lacks {lang!r}")
        path = str(decl.get("path") or "")
        sha = str(decl.get("sha256") or "")
        rows = decl.get("rows")
        if not path or path.startswith("/") or ".." in path:
            raise JobRefusal(
                f"dev_manifests[{lang!r}].path {path!r} must be repo-relative "
                "without traversal")
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise JobRefusal(
                f"dev_manifests[{lang!r}].sha256 must be 64-hex")
        if not isinstance(rows, int) or isinstance(rows, bool) or rows < 1:
            raise JobRefusal(
                f"dev_manifests[{lang!r}].rows must be a positive int")
        if env_dev_paths.get(lang) != path:
            raise JobRefusal(
                f"MEDZEN_DEV_SENTINEL_MANIFEST_FILES path for {lang!r} "
                f"({env_dev_paths.get(lang)!r}) != the predeclared "
                f"dev_manifests path ({path!r})")
        local = repo_root / path
        if not local.exists():
            raise JobRefusal(
                f"predeclared dev manifest {path} does not exist in the repo — "
                "author and commit the frozen slice before binding it")
        body = local.read_bytes()
        actual_sha = hashlib.sha256(body).hexdigest()
        if actual_sha != sha:
            raise JobRefusal(
                f"dev manifest {path} hashes to {actual_sha[:16]}, the packet "
                f"declares {sha[:16]} — the slice drifted from its declaration")
        actual_rows = sum(1 for line in body.splitlines() if line.strip())
        if actual_rows != rows:
            raise JobRefusal(
                f"dev manifest {path} has {actual_rows} rows, the packet "
                f"declares {rows}")


EXECUTION_MODES = ("plain", "arm2_comparative", "arm2_scoring")


def resolve_execution_mode(environment: dict) -> str:
    """The STRICT execution-mode enum (owner-directed): MEDZEN_EXECUTION_MODE is
    'plain' (bare trainer) or 'arm2_comparative' (the shared KD-on/KD-off
    calibration wrapper). It does NOT overload MEDZEN_TRAIN_MODE (lora|full) or
    MEDZEN_VARIANT (ctc|llm). Fail-closed:
      - an unknown value refuses;
      - 'plain' with KD enabled is contradictory and refuses;
      - when the key is ABSENT, KD-on implies 'arm2_comparative' (so the frozen
        pre-enum Arm-2 calibration packet renders byte-identically) and KD-off
        is 'plain' (so every existing plain-training packet is unchanged)."""
    raw = str(environment.get("MEDZEN_EXECUTION_MODE", "")).strip()
    kd_on = str(environment.get("MEDZEN_KD_ENABLE", "0")).strip().lower() \
        in _KD_TRUE
    if raw == "":
        return "arm2_comparative" if kd_on else "plain"
    if raw not in EXECUTION_MODES:
        raise JobRefusal(
            f"MEDZEN_EXECUTION_MODE={raw!r} is not one of {EXECUTION_MODES} — "
            "unknown execution modes fail closed")
    if raw == "plain" and kd_on:
        raise JobRefusal(
            "MEDZEN_EXECUTION_MODE=plain with MEDZEN_KD_ENABLE truthy is "
            "contradictory — plain training runs no KD (fail closed)")
    if raw == "arm2_scoring" and kd_on:
        raise JobRefusal(
            "MEDZEN_EXECUTION_MODE=arm2_scoring with MEDZEN_KD_ENABLE truthy "
            "is contradictory — the evaluator decodes, it never trains")
    return raw


def is_arm2_comparative(environment: dict) -> bool:
    """True iff this packet runs the shared Arm-2 comparative calibration
    wrapper (KD-on candidate OR the KD-off control)."""
    return resolve_execution_mode(environment) == "arm2_comparative"


def inject_launcher_provenance(environment: dict, bindings: dict) -> dict:
    """Reproduce, in ONE place, the self-reference-free provenance the launcher
    stamps into a KD packet's rendered Environment (Codex review #20 F5, #22
    blocker 2): the packet's own canonical sha, the real TrainingJobName, and
    the in-image execution-contract path+sha derived from the packet's
    execution_contract block. It is deterministic from `bindings`, so BOTH the
    renderer (render_request) and the online receipt check
    (verify_receipt_against_aws) reconstruct the SAME environment — a single
    source of truth keeps them from drifting. The committed packet env carries
    none of these keys (it cannot: the sha is a self-reference); they exist only
    in the rendered/live environment. Returns a NEW dict; the input is untouched.
    """
    env = dict(environment)
    # Injected for every Arm-2 comparative packet — the KD-on candidates AND
    # the KD-off control — since both run the wrapper and both need the
    # execution contract + packet-sha + job-name provenance. (For KD-on with no
    # explicit mode this is unchanged from the prior KD-enable gate, so the
    # frozen calibration packet renders byte-identically.)
    if is_arm2_comparative(env):
        env["MEDZEN_CALIBRATION_PACKET_SHA256"] = \
            canonical_bindings_sha256(bindings)
        env["MEDZEN_TRAINING_JOB_NAME"] = f"medzen-b5-{bindings['job_id']}"
        contract_decl = bindings.get("execution_contract") or {}
        if str(contract_decl.get("path") or "").strip():
            env["MEDZEN_EXECUTION_CONTRACT"] = \
                "/opt/medzen/" + str(contract_decl["path"])
            env["MEDZEN_EXECUTION_CONTRACT_SHA256"] = \
                str(contract_decl.get("sha256") or "")
    return env


def render_request(bindings: dict) -> dict:
    job_id = _require(bindings, "job_id")
    if re.fullmatch(r"[a-z0-9-]{1,40}", job_id) is None:
        raise JobRefusal("job_id must be lowercase kebab, <=40 chars")
    image = _require(bindings, "image_uri_with_digest")
    if "@sha256:" not in image or not image.startswith(
            f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/"):
        raise JobRefusal(
            "image must be this account's ECR URI pinned by digest — "
            "a floating tag can train on unreviewed code")
    instance_type = _require(bindings, "instance_type")
    if instance_type not in INSTANCE_ALLOWLIST:
        raise JobRefusal(f"instance {instance_type!r} is outside the allowlist "
                         f"{sorted(INSTANCE_ALLOWLIST)}")
    kms = _require(bindings, "kms_key_arn")
    if not kms.startswith(f"arn:aws:kms:{REGION}:{ACCOUNT}:key/"):
        raise JobRefusal("kms_key_arn is not this account's key in-region")
    subnets = _require(bindings, "subnets")
    security_groups = _require(bindings, "security_group_ids")
    max_runtime_s = int(_require(bindings, "max_runtime_seconds"))
    # Spot is the default and the campaign posture. Opting out requires BOTH
    # an explicit false AND a written reason in bindings — added when the
    # account's spot-training quota was 0 at T5 launch (increase filed);
    # the ceiling arithmetic below is on-demand-based either way, so an
    # on-demand run can never cost more than the ceiling contemplated.
    managed_spot = bindings.get("managed_spot", True)
    if not isinstance(managed_spot, bool):
        raise JobRefusal("managed_spot must be a boolean when present")
    if not managed_spot and not str(bindings.get("managed_spot_reason", "")).strip():
        raise JobRefusal("opting out of spot requires managed_spot_reason")
    if managed_spot:
        max_wait_s = int(_require(bindings, "max_wait_seconds"))
        if max_wait_s < max_runtime_s:
            raise JobRefusal("max_wait must cover max_runtime (spot contract)")
    elif bindings.get("max_wait_seconds") is not None:
        raise JobRefusal("max_wait_seconds is a spot-only setting")
    ceiling_usd = float(_require(bindings, "cost_ceiling_usd"))
    worst_case = max_runtime_s / 3600.0 * ON_DEMAND_USD_PER_HOUR[instance_type]
    if worst_case > ceiling_usd:
        raise JobRefusal(
            f"max_runtime {max_runtime_s}s costs up to ${worst_case:.2f} "
            f"on-demand, above the ${ceiling_usd:.2f} ceiling — shrink the "
            "runtime or raise the ceiling in review, never here")
    # The launcher stamps self-reference-free provenance (packet sha, real
    # TrainingJobName, in-image execution-contract path+sha) into a KD packet's
    # rendered environment. It is deterministic from `bindings`, so
    # validate_request's exact-render comparison and verify_receipt_against_aws's
    # live-Environment comparison both reconstruct it identically — see
    # inject_launcher_provenance (the ONE definition of this injection).
    environment = inject_launcher_provenance(
        _require(bindings, "environment"), bindings)
    missing = [k for k in REQUIRED_ENVIRONMENT if not environment.get(k)]
    if missing:
        raise JobRefusal(f"environment lacks {missing}")
    if environment["MEDZEN_VARIANT"] != "ctc":
        raise JobRefusal("only the calibrated ctc variant is launchable")
    # Codex review #4 (reproduced: LR=nan passed): the trainer's OWN parser
    # is the single source of truth for environment semantics — run it at
    # packet time so a bad packet dies here, not after instance spin-up.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from pipeline.omniasr_train import TrainerRefusal, parse_config
    try:
        parse_config(environment)
    except TrainerRefusal as exc:
        raise JobRefusal(
            f"the trainer would refuse this environment at container "
            f"start: {exc}") from exc
    # Codex review #19 F4: the trainer parser validates the environment in
    # isolation; this cross-checks the human-facing `distillation` recipe and
    # the acceptance/result-verifier bindings against it so the packet cannot
    # be internally self-contradictory.
    validate_arm2_semantics(bindings, environment)
    # Codex review #8 step 6: a multilingual-full packet must bind the
    # EXACT approved pilot profile, not merely satisfy generic limits.
    if environment.get("MEDZEN_MULTILINGUAL_FULL_ACK"):
        protocol = load_protocol(
            Path(__file__).resolve().parents[1])
        mandatory = set(protocol["mandatory_languages"])
        requested = {t.strip() for t in
                     environment.get("MEDZEN_LANGUAGES", "").split(",")
                     if t.strip()}
        if requested != mandatory:
            raise JobRefusal(
                f"multilingual-full packets bind the frozen pilot set "
                f"{sorted(mandatory)} exactly; got {sorted(requested)}")
        if environment.get("MEDZEN_MANIFEST_VERSION") != "gb9":
            raise JobRefusal(
                "multilingual-full packets bind dataset version gb9 "
                "(B5-GB9: gb8 minus the cross-language CV contributor "
                "whose voice sits in the kinyarwanda dev-selection "
                "surface — Codex review #19 finding 4)")
        if environment.get("MEDZEN_TEMPERATURE") != "0.5":
            raise JobRefusal(
                "multilingual-full packets bind temperature 0.5 exactly "
                "(the approved pilot profile)")
        expected_ref = ("s3://medzen-speech/curated/_versions/gb3/"
                        "DQ-2026-006-gb3-pulaar-question-mark-deferral.json")
        if environment.get("MEDZEN_EXCLUSIONS_REF") != expected_ref:
            raise JobRefusal(
                "multilingual-full packets bind the exact DQ-2026-006 "
                "exclusions reference gb6 adoption was granted on")
    registry_line = _require(bindings, "cost_registry_line")
    volume_gb = int(bindings.get("volume_gb", 100))
    if not 1 <= volume_gb <= 500:
        raise JobRefusal(f"volume_gb {volume_gb} is outside 1..500")

    prefix = f"research/b5-training/{job_id}"
    return {
        "TrainingJobName": f"medzen-b5-{job_id}",
        "RoleArn": ROLE_ARN,
        "AlgorithmSpecification": {
            "TrainingImage": image,
            "TrainingInputMode": "File",
            # Without an explicit entrypoint SageMaker appends its default
            # 'train' argument to the image ENTRYPOINT — the first T5 attempt
            # died in seconds on python trying to open a file named 'train'.
            "ContainerEntrypoint": ["/opt/venv/bin/python"],
            # Codex review #20 F3: a KD calibration runs the WRAPPER
            # (train -> export -> readyz -> dev-WER -> finalize -> verify ->
            # exit nonzero on failure), NOT the bare trainer that wrote
            # null serve/dev-WER and could never pass its own verifier.
            # arm2_comparative (KD-on candidate OR KD-off control) runs the
            # shared wrapper; plain training runs the bare trainer. Resolved by
            # the strict MEDZEN_EXECUTION_MODE enum (fail-closed).
            "ContainerArguments": (
                ["-m", "pipeline.omniasr_score"]
                if resolve_execution_mode(environment) == "arm2_scoring"
                else ["-m", "pipeline.omniasr_calibrate"]
                if is_arm2_comparative(environment)
                else ["-m", "pipeline.omniasr_train"]),
        },
        "OutputDataConfig": {
            "S3OutputPath": f"s3://{BUCKET}/{prefix}/output",
            "KmsKeyId": kms,
        },
        "CheckpointConfig": {
            "S3Uri": f"s3://{BUCKET}/{prefix}/checkpoints",
            "LocalPath": "/opt/ml/checkpoints",
        },
        "ResourceConfig": (
            {"InstanceType": instance_type, "InstanceCount": 1,
             "VolumeSizeInGB": volume_gb}
            if instance_type in NVME_LOCAL_STORAGE_TYPES else
            {"InstanceType": instance_type, "InstanceCount": 1,
             "VolumeSizeInGB": volume_gb, "VolumeKmsKeyId": kms}
        ),
        "VpcConfig": {
            "SecurityGroupIds": list(security_groups),
            "Subnets": list(subnets),
        },
        "StoppingCondition": (
            {"MaxRuntimeInSeconds": max_runtime_s,
             "MaxWaitTimeInSeconds": max_wait_s}
            if managed_spot else
            {"MaxRuntimeInSeconds": max_runtime_s}
        ),
        "EnableManagedSpotTraining": managed_spot,
        "EnableNetworkIsolation": False,
        # Codex review #24 finding 1: render these EXPLICITLY-OFF so the
        # receipt comparison covers them by exact equality — remote debugging
        # is shell access to the container, the profiler writes an unreviewed
        # output stream, and inter-container encryption is meaningless on one
        # instance; none may ever be silently enabled.
        "EnableInterContainerTrafficEncryption": False,
        "ProfilerConfig": {"DisableProfiler": True},
        "RemoteDebugConfig": {"EnableRemoteDebug": False},
        "Environment": dict(sorted(environment.items())),
        # AWS tag values allow only [letters spaces digits _.:/=+-@]
        # (live ValidationException at the first real arm launch: the
        # bookkeeping label's parentheses were refused). Sanitize DERIVED
        # tag values; identity fields are already charset-safe.
        "Tags": [
            {"Key": "medzen:cost-registry",
             "Value": _tag_safe(registry_line)},
            {"Key": "medzen:job", "Value": job_id},
            {"Key": "medzen:classification",
             "Value": "OFFLINE_TRAINING_PUBLIC_RESEARCH_NO_PHI"},
            # Codex review #24: the OWNER-APPLIED local IAM boundary
            # (platform/iam/medzen-local-boundary-policy.json) denies
            # local CreateTrainingJob unless medzen-tier=calibration —
            # arm-tier jobs are only creatable by the arm-launch role.
            # Codex stage-1 review (2026-08-25) finding 1: tier is decided by
            # JOB CLASS, not price alone — a full comparative training arm
            # (steps > the calibration boundary) is a CAMPAIGN job even at
            # $5.60, so it can never ride the below-tier local path.
            {"Key": "medzen-tier",
             "Value": ("arm"
                        if is_campaign_arm_job(environment, worst_case)
                        else "calibration")},
        ],
    }


def validate_request(request: dict, bindings: dict) -> dict:
    expected = render_request(bindings)
    if request != expected:
        raise JobRefusal("request differs from the exact rendered form")
    if request["RoleArn"] != ROLE_ARN:
        raise JobRefusal("RoleArn is not the pinned trainer role")
    # RoleArn is pinned to the exact constant above, so it is excluded from
    # the substring screen — any OTHER field smuggling an iam: scope still trips.
    screened = {k: v for k, v in request.items() if k != "RoleArn"}
    flattened = json.dumps(screened, sort_keys=True).casefold()
    for prohibited in PROHIBITED_SUBSTRINGS:
        if prohibited.casefold() in flattened:
            raise JobRefusal(f"request contains prohibited scope: {prohibited}")
    return {
        "status": "PASS_EXACT_TRAINING_REQUEST",
        "job": request["TrainingJobName"],
        "worst_case_on_demand_usd": round(
            request["StoppingCondition"]["MaxRuntimeInSeconds"] / 3600.0
            * ON_DEMAND_USD_PER_HOUR[request["ResourceConfig"]["InstanceType"]], 2),
        "spot": request["EnableManagedSpotTraining"],
    }


def canonical_bindings_sha256(bindings: dict) -> str:
    """The packet identity the authorization must cite (Codex review #9:
    the phrase bound only the job id, so a mutated packet — different
    seed, LR, batch, image — launched under an old approval)."""
    return hashlib.sha256(json.dumps(
        bindings, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


CALIBRATION_TIER_USD = 10.0
# Codex stage-1 review (2026-08-25) finding 1: calibration-SHAPED jobs
# (mechanics receipts, the throughput benchmark) run <= this many steps; a
# comparative job above it is a full training ARM regardless of its price.
STAGE1_STEP_BOUNDARY = 30


def is_campaign_arm_job(environment: dict, worst_case: float) -> bool:
    """ARM (campaign) tier iff the worst case exceeds the calibration tier OR
    the job is an Arm-2 comparative run training past the calibration step
    boundary (Codex stage-1 review finding 1: the six 2000-step arms cost
    $5.60 — below $10 — and would otherwise ride the below-tier local path,
    bypassing the protected workflow, owner click, campaign reservation and
    the atomic-$70 controls). Fail-closed: an unparseable MEDZEN_MAX_STEPS on
    a comparative packet refuses upstream in parse/validate paths; here it is
    treated as arm tier."""
    if worst_case > CALIBRATION_TIER_USD:
        return True
    # Codex final-gap correction (2026-08-26): evaluator/scoring jobs decode
    # candidate models on the frozen split — ALWAYS campaign tier, at any
    # price, so hypotheses can only originate from the protected workflow.
    if str(environment.get("MEDZEN_EXECUTION_MODE", "")).strip() \
            == "arm2_scoring":
        return True
    if is_arm2_comparative(environment):
        try:
            steps = int(str(environment.get("MEDZEN_MAX_STEPS", "")).strip())
        except ValueError:
            return True
        return steps > STAGE1_STEP_BOUNDARY
    return False
AUTH_DIR = "platform/decisions/launch-authorizations"
REVIEWS_DIR = "platform/decisions/reviews"
INTENTS_DIR = "platform/decisions/launch-intents"
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
# Codex review #22: the calibration proves the RECIPE; an arm may differ
# from it ONLY on these declared scale dimensions — every other
# environment key must be byte-identical to the committed calibration
# packet (LR/batch/accum/seed/schedule drift refuses).
SCALE_KEYS = frozenset({"MEDZEN_MAX_STEPS", "MEDZEN_CHECKPOINT_EVERY",
                        "MEDZEN_WARMUP_STEPS", "MEDZEN_AUDIO_CAP_HOURS"})


def repo_head_oid(root: Path) -> str:
    """ONE captured commit OID for EVERY governed input in a launch
    decision (Codex reviews #21-#22: separate HEAD resolutions and
    working-tree reads could disagree mid-decision)."""
    completed = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                               capture_output=True, text=True)
    oid = (completed.stdout or "").strip()
    if completed.returncode != 0 or not _HEX40.fullmatch(oid):
        raise JobRefusal("cannot resolve a git HEAD commit — committed-"
                         "evidence gates need a repository")
    return oid


def _show_at(root: Path, oid: str, rel: str) -> bytes | None:
    if rel.startswith(("/", "..")) or ":" in rel or "\\" in rel \
            or "/../" in rel:
        return None
    completed = subprocess.run(["git", "-C", str(root), "show",
                                f"{oid}:{rel}"],
                               capture_output=True)
    return completed.stdout if completed.returncode == 0 else None


def load_protocol(root: Path, oid: str | None = None) -> dict:
    """Protocol via the hash-binding pointer. With `oid`, BOTH files come
    from that captured commit (Codex review #22: working-tree bytes and
    an uncontained pointer path were accepted); without it (authoring
    time: render/validate) the working tree is read with the same
    containment and hash rules."""
    pointer_rel = "platform/decisions/CURRENT-PROMOTION-PROTOCOL.json"
    if oid is None:
        pointer = json.loads((root / pointer_rel).read_bytes())
    else:
        body = _show_at(root, oid, pointer_rel)
        if body is None:
            raise JobRefusal("protocol pointer is not committed")
        pointer = json.loads(body)
    rel = str(pointer.get("file") or "")
    if not rel.startswith("platform/decisions/") or rel.startswith(("/",
            "..")) or "/../" in rel or ":" in rel:
        raise JobRefusal(f"protocol pointer path {rel!r} escapes "
                         "platform/decisions/ — refusing")
    if oid is None:
        protocol_body = (root / rel).read_bytes()
    else:
        protocol_body = _show_at(root, oid, rel)
        if protocol_body is None:
            raise JobRefusal(f"protocol file {rel} is not committed")
    if hashlib.sha256(protocol_body).hexdigest() != pointer.get("sha256"):
        raise JobRefusal("protocol file does not match the pointer hash")
    protocol = json.loads(protocol_body)
    if protocol.get("record") != pointer.get("record"):
        raise JobRefusal("protocol record id does not match the pointer")
    return protocol


def assert_medzen_account(runner=subprocess.run) -> None:
    """CLI-based account assertion (tooling/tests). The launch path uses
    ONE boto3 session for the STS check AND the mutation."""
    completed = runner(["aws", "sts", "get-caller-identity",
                        "--query", "Account", "--output", "text"],
                       capture_output=True, text=True)
    account = (completed.stdout or "").strip()
    if completed.returncode != 0 or account != ACCOUNT:
        raise JobRefusal(
            f"effective AWS account is {account or 'unknown'!r}, not the "
            f"MedZen account {ACCOUNT} — refusing to mutate anything "
            "under ambient credentials (Codex review #20)")


def review_record_approves(job_id: str, bindings: dict, root: Path,
                            oid: str) -> dict:
    """Codex review #22 finding 1 (reproduced: HOLD_BEFORE_LAST_MARKER_
    ACCEPTED): free-text window parsing is GONE. The review decision is a
    structured committed record at {REVIEWS_DIR}/<job_id>.json binding
    the exact packet sha; its `decision` field is an enum and ONLY
    "APPROVED" launches. A HOLD or CHANGES_REQUIRED decision is that
    record's current state until a NEW commit replaces it — nothing
    "nearby" can revive a held packet."""
    rel = f"{REVIEWS_DIR}/{job_id}.json"
    body = _show_at(root, oid, rel)
    if body is None:
        raise JobRefusal(f"no committed review record at {rel} — "
                         "free-text log entries no longer authorize "
                         "(Codex review #22)")
    record = json.loads(body)
    if record.get("job_id") != job_id:
        raise JobRefusal(f"review record {rel} names a different job")
    if record.get("bindings_sha256") != canonical_bindings_sha256(bindings):
        raise JobRefusal("review record binds a DIFFERENT packet sha — "
                         "a mutated packet cannot ride an old review")
    if record.get("decision") != "APPROVED":
        raise JobRefusal(f"review decision is {record.get('decision')!r}, "
                         "not APPROVED — the packet is held")
    if not str(record.get("basis") or "").strip():
        raise JobRefusal("review record lacks a basis")
    return record


def verify_calibration_receipt(bindings: dict,
                                repo_root: Path | None = None,
                                head_oid: str | None = None) -> dict:
    """Codex reviews #20-#22. The COMMITTED CALIBRATION PACKET is the
    recipe authority (a fabricated receipt cannot invent invariants):
    the arm environment must equal the calibration packet environment on
    EVERY key except the declared SCALE_KEYS. Receipt facts that only
    AWS knows (terminal status, billable time, image, artifact identity)
    are re-verified ONLINE at launch via verify_receipt_against_aws.
    Returns the parsed receipt record for that online step."""
    root = repo_root or Path(__file__).resolve().parents[1]
    oid = head_oid or repo_head_oid(root)
    receipt = bindings.get("calibration_receipt")
    if not isinstance(receipt, dict):
        raise JobRefusal(
            "above-tier multilingual packets must bind calibration_receipt "
            "{record, record_sha256} — an arm may never launch on an "
            "unproven chain (Codex reviews #20-#22)")
    rel = str(receipt.get("record") or "")
    if not rel.startswith("platform/evidence/"):
        raise JobRefusal(f"calibration_receipt.record {rel!r} must be a "
                         "repo-relative platform/evidence/ path")
    body = _show_at(root, oid, rel)
    if body is None:
        raise JobRefusal(f"calibration receipt {rel} is not committed at "
                         f"{oid[:12]}")
    if hashlib.sha256(body).hexdigest() != receipt.get("record_sha256"):
        raise JobRefusal("calibration_receipt.record_sha256 does not match "
                         f"the committed bytes of {rel}")
    record = json.loads(body)

    def field(name):
        value = record.get(name)
        if value in (None, "", [], {}):
            raise JobRefusal(f"calibration receipt lacks {name!r}")
        return value

    if field("terminal_status") != "Completed":
        raise JobRefusal("calibration terminal_status is not Completed")
    billable = record.get("billable_seconds")
    # bool is an int subclass — Codex review #22 reproduced True passing
    if type(billable) is not int or billable <= 0:
        raise JobRefusal("billable_seconds must be a positive integer")
    verdict = str(field("verdict"))
    # Codex round 30 finding 1 (reproduced: a committed receipt with
    # verdict='PASS — CALIBRATION FAILED' passed): the prefix branch admitted
    # any 'PASS …' phrase, including a contradictory one. There is no
    # legitimate PASS verdict that is not exactly "PASS".
    if verdict != "PASS":
        raise JobRefusal(f"verdict {verdict[:24]!r} is not exactly 'PASS' — "
                         "a 'PASS …' phrase (reproduced: 'PASS — CALIBRATION "
                         "FAILED', 'PASSWORD') is not a PASS")
    if sorted(record.get("declared_scale_keys") or []) != \
            sorted(SCALE_KEYS):
        raise JobRefusal("receipt must declare EXACTLY the permitted "
                         "calibration-to-pilot scale keys")
    env = bindings["environment"]
    # recipe authority: the COMMITTED calibration packet
    packet_rel = str(field("calibration_packet"))
    if not packet_rel.startswith("platform/manifests/"):
        raise JobRefusal("calibration_packet must live under "
                         "platform/manifests/")
    packet_body = _show_at(root, oid, packet_rel)
    if packet_body is None:
        raise JobRefusal(f"calibration packet {packet_rel} is not committed")
    cal = json.loads(packet_body)
    if canonical_bindings_sha256(cal) != \
            field("calibration_bindings_sha256"):
        raise JobRefusal("receipt calibration_bindings_sha256 does not "
                         "match the committed calibration packet")
    cal_env = cal.get("environment") or {}
    for key in sorted(set(cal_env) | set(env)):
        if key in SCALE_KEYS:
            continue
        if cal_env.get(key) != env.get(key):
            raise JobRefusal(
                f"recipe drift on {key}: calibration proved "
                f"{cal_env.get(key)!r}, the arm binds {env.get(key)!r} — "
                "only the declared scale keys may differ (Codex #22)")
    if cal.get("image_uri_with_digest") != \
            bindings.get("image_uri_with_digest") or \
            record.get("image_uri_with_digest") != \
            bindings.get("image_uri_with_digest"):
        raise JobRefusal("the arm binds a different image digest than the "
                         "calibrated chain")
    version = env.get("MEDZEN_MANIFEST_VERSION", "")
    if field("dataset_version") != version:
        raise JobRefusal(
            f"receipt proves dataset {record.get('dataset_version')!r} but "
            f"the arm binds {version!r} — the wrong calibration cannot "
            "justify this arm")
    adoption_rel = (f"platform/evidence/"
                    f"B5-{version.upper()}-ADOPTION-2026-001.json")
    adoption_body = _show_at(root, oid, adoption_rel)
    if adoption_body is None:
        raise JobRefusal(f"no committed adoption evidence {adoption_rel}")
    if field("dataset_complete_raw_sha256") != \
            json.loads(adoption_body)["complete_raw_sha256"]:
        raise JobRefusal("receipt dataset adoption sha does not match the "
                         f"committed {version} adoption")
    export = field("export")
    if export.get("status") != "PASS_MERGED_EXPORT" or not (
            _hex(str(export.get("model_sha256", "")), 64)
            and _hex(str(export.get("manifest_sha256", "")), 64)):
        raise JobRefusal("receipt export block is not a hash-complete "
                         "PASS_MERGED_EXPORT")
    artifact = field("artifact")
    if not artifact.get("s3_version_id"):
        raise JobRefusal("receipt artifact block lacks an S3 VersionId")
    if not str(artifact.get("kms_key", "")).startswith(
            f"arn:aws:kms:{REGION}:{ACCOUNT}:key/"):
        raise JobRefusal("receipt artifact KMS key is not this account's "
                         "in-region key (Codex review #22: wrong-account "
                         "ARN was accepted)")
    if type(artifact.get("s3_bytes")) is not int or artifact["s3_bytes"] <= 0:
        raise JobRefusal("receipt artifact block lacks a positive integer "
                         "s3_bytes (Codex round 30 finding 1: a false size "
                         "was accepted) — the live gate binds it to "
                         "head_object ContentLength")
    # Codex round 30 finding 1 (reproduced: a receipt whose
    # authoritative_verification block said verdict='FAIL' still PASSED,
    # because the gate never read that block). The gate now REQUIRES the
    # receipt to carry the authoritative verifier's own attestation and
    # requires that attestation to be an unambiguous PASS produced by the
    # CURRENTLY COMMITTED authoritative verifier. A fabricated receipt can no
    # longer self-report a failed verification and pass; and because the
    # attested verifier sha is bound to the committed verify_arm2_calibration
    # script, the online step (and the launch-time full re-hash) re-derive
    # every content fact this attestation claims.
    auth = field("authoritative_verification")
    if not isinstance(auth, dict):
        raise JobRefusal("receipt lacks an authoritative_verification block")
    if auth.get("authoritative") is not True:
        raise JobRefusal("authoritative_verification.authoritative is not "
                         "True — only the --live authoritative verdict counts")
    if auth.get("verdict") != "PASS":
        raise JobRefusal("authoritative_verification.verdict is not 'PASS' — "
                         "a receipt cannot ride a non-PASS verification")
    if auth.get("creation_event_verified") is not True:
        raise JobRefusal("authoritative_verification.creation_event_verified "
                         "is not True — the CloudTrail creator was not proven")
    if auth.get("failures") != []:
        raise JobRefusal("authoritative_verification.failures is non-empty — "
                         f"the verification reported {auth.get('failures')!r}")
    if auth.get("mode") != "live":
        raise JobRefusal("authoritative_verification.mode is not 'live'")
    # bind the attestation to the CURRENTLY COMMITTED authoritative verifier —
    # a stale/tampered verifier's attestation cannot authorize this chain
    verifier_rel = "scripts/verify_arm2_calibration.py"
    verifier_body = _show_at(root, oid, verifier_rel)
    if verifier_body is None:
        raise JobRefusal(f"the authoritative verifier {verifier_rel} is not "
                         f"committed at {oid[:12]}")
    if auth.get("verifier_script_sha256") != \
            hashlib.sha256(verifier_body).hexdigest():
        raise JobRefusal("authoritative_verification.verifier_script_sha256 "
                         "does not match the committed authoritative verifier "
                         "— the attestation was not produced by this verifier")
    if not _hex(str(auth.get("metrics_sha256", "")), 64):
        raise JobRefusal("authoritative_verification.metrics_sha256 is not a "
                         "64-hex digest")
    # Second-review bundle (2026-08-25): a receipt MAY declare the run's own
    # reviewed commit + the verifier sha BAKED AT THAT COMMIT (the in-image
    # verifier that wrote the metrics identity). When declared, both are
    # verified against git — the sha must equal the verifier's committed
    # bytes AT THAT COMMIT — and the launch-time re-verification compares the
    # metrics identity against it instead of the current verifier (which
    # legitimately evolves between the calibration run and the launch).
    # Absent => the metrics identity must match the CURRENT verifier (the
    # original, stricter behavior; correct when nothing changed in between).
    run_commit = record.get("run_commit")
    run_verifier = record.get("run_verifier_sha256")
    if (run_commit is None) != (run_verifier is None):
        raise JobRefusal("run_commit and run_verifier_sha256 must be "
                         "declared together or not at all")
    if run_commit is not None:
        if not _HEX40.fullmatch(str(run_commit)):
            raise JobRefusal("run_commit must be an exact 40-hex commit")
        if not _hex(str(run_verifier), 64):
            raise JobRefusal("run_verifier_sha256 must be 64-hex")
        run_verifier_body = _show_at(root, str(run_commit),
                                     "scripts/verify_arm2_calibration.py")
        if run_verifier_body is None:
            raise JobRefusal(f"run_commit {str(run_commit)[:12]} does not "
                             "carry the authoritative verifier")
        if hashlib.sha256(run_verifier_body).hexdigest() != str(run_verifier):
            raise JobRefusal(
                "run_verifier_sha256 does not match the verifier committed "
                "at the receipt's own run_commit — a receipt cannot invent "
                "the in-image verifier identity")
    # structural consistency: the receipt cannot claim PASS at the top while
    # its terminal status or its own attestation disagree
    if record.get("terminal_status") != "Completed" or \
            auth.get("verdict") != verdict:
        raise JobRefusal("receipt verdict / terminal_status / authoritative "
                         "verdict are not all a consistent PASS/Completed")
    return record


def verify_receipt_against_aws(record: dict, cal_packet: dict,
                                sagemaker_client, s3_client) -> None:
    """Fast online IDENTITY + COST + SIZE + Environment pre-check against the
    committed calibration packet (Codex reviews #22-#23: the genuine gb8 job
    passed as a gb9 calibration). This checks the receipt's own claims and the
    reconstructed Environment cheaply, WITHOUT downloading the artifact.

    The AUTHORITATIVE, complete verification — the full rendered request
    (entrypoint, arguments, role, VPC, runtime, remote-debug, tags), the
    CloudTrail creator, and a re-hash of the actual KMS-encrypted artifact —
    is derive_live_artifact_facts() + cross_check_receipt_content() (Codex
    round 30 finding 2), which the launcher runs immediately after this and
    which reuses verify_arm2_calibration end-to-end."""
    expected_job = f"medzen-b5-{cal_packet['job_id']}"
    if record.get("job") != expected_job:
        raise JobRefusal(
            f"receipt names job {record.get('job')!r} but the committed "
            f"calibration packet derives {expected_job!r} — a different "
            "job's evidence cannot justify this chain (Codex review #23)")
    desc = sagemaker_client.describe_training_job(
        TrainingJobName=expected_job)
    if desc.get("TrainingJobStatus") != "Completed":
        raise JobRefusal(f"AWS says {expected_job} is "
                         f"{desc.get('TrainingJobStatus')!r}, not Completed")
    if desc.get("BillableTimeInSeconds") != record["billable_seconds"]:
        raise JobRefusal("AWS billable seconds do not match the receipt")
    if desc.get("AlgorithmSpecification", {}).get("TrainingImage") != \
            cal_packet["image_uri_with_digest"]:
        raise JobRefusal("AWS training image does not match the committed "
                         "calibration packet")
    live_env = desc.get("Environment") or {}
    # The launcher deterministically injects self-reference-free provenance
    # into a KD packet's Environment, so the live env is the packet env PLUS
    # those keys — reconstruct and require byte-for-byte equality (Codex #23:
    # gb8 wearing a gb9 receipt fails; a forged provenance value fails).
    expected_env = inject_launcher_provenance(
        cal_packet.get("environment") or {}, cal_packet)
    if live_env != expected_env:
        drift = sorted(set(live_env.items()) ^ set(expected_env.items()))
        raise JobRefusal(
            f"the LIVE SageMaker Environment differs from the committed "
            f"calibration packet's rendered environment ({drift[:4]}) — the "
            "job that actually ran is not the calibration this packet claims "
            "(Codex review #23)")
    artifact = record["artifact"]
    live_artifact = desc.get("ModelArtifacts", {}).get("S3ModelArtifacts")
    if live_artifact != artifact["s3_uri"]:
        raise JobRefusal(f"AWS model artifact {live_artifact!r} does not "
                         "match the receipt's s3_uri")
    if desc.get("OutputDataConfig", {}).get("KmsKeyId") not in (
            artifact["kms_key"],
            artifact["kms_key"].rsplit("/", 1)[-1]):
        raise JobRefusal("AWS output KMS configuration does not match "
                         "the receipt")
    uri = artifact["s3_uri"]
    bucket, key = uri.replace("s3://", "", 1).split("/", 1)
    head = s3_client.head_object(Bucket=bucket, Key=key)
    if head.get("VersionId") != artifact["s3_version_id"]:
        raise JobRefusal("artifact S3 VersionId does not match AWS")
    if head.get("SSEKMSKeyId") != artifact["kms_key"]:
        raise JobRefusal("artifact KMS identity does not match AWS")
    if head.get("ContentLength") != artifact["s3_bytes"]:
        raise JobRefusal("artifact byte size does not match head_object "
                         "ContentLength (Codex round 30 finding 1: a false "
                         "s3_bytes was accepted)")


def cross_check_receipt_content(record: dict, facts: dict) -> None:
    """Codex round 30 finding 1: the receipt's self-reported CONTENT (export
    model/manifest shas, metrics sha, artifact size/version/KMS) was never
    re-derived, so a well-formed-but-fabricated 64-hex or a false size passed.
    This PURE function requires every content fact the receipt claims to equal
    the value the AUTHORITATIVE verifier re-derived by re-hashing the actual
    KMS-encrypted artifact it fetched by explicit VersionId (`facts` is the
    report from verify_arm2_calibration.verify_live_bundle). A fabricated
    content value cannot survive a re-hash of the real bytes."""
    auth = record.get("authoritative_verification") or {}
    checks = [
        ("export.model_sha256", (record.get("export") or {}).get("model_sha256"),
         facts.get("export_model_sha256")),
        ("export.manifest_sha256",
         (record.get("export") or {}).get("manifest_sha256"),
         facts.get("export_manifest_sha256")),
        ("authoritative_verification.metrics_sha256", auth.get("metrics_sha256"),
         facts.get("metrics_sha256")),
        ("artifact.s3_uri", (record.get("artifact") or {}).get("s3_uri"),
         facts.get("model_artifacts_uri")),
        ("artifact.s3_version_id",
         (record.get("artifact") or {}).get("s3_version_id"),
         facts.get("s3_version_id")),
        ("artifact.kms_key", (record.get("artifact") or {}).get("kms_key"),
         facts.get("s3_kms_key")),
        ("artifact.s3_bytes", (record.get("artifact") or {}).get("s3_bytes"),
         facts.get("s3_bytes")),
    ]
    for name, claimed, derived in checks:
        if derived in (None, "") or claimed != derived:
            raise JobRefusal(
                f"receipt {name} = {claimed!r} does not match the value "
                f"{derived!r} re-derived from the live artifact — the receipt "
                "misreports its own content (Codex round 30 finding 1)")


def derive_live_artifact_facts(cal_packet: dict, workdir, session=None,
                               metrics_verifier_sha: str | None = None
                               ) -> dict:
    """The AWS side of the launch-time full re-verification: reuse the
    authoritative verifier IN-PROCESS (single source of truth) — fetch the
    real job + its exact-VersionId KMS-encrypted artifact, re-hash it, and
    re-run the CloudTrail creation-event + request + metrics checks. Returns
    the re-derived content facts for cross_check_receipt_content; raises
    JobRefusal if the authoritative verification itself fails."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import verify_arm2_calibration as v2
    receipt, extracted, s3_meta, creation_event = v2.live_fetch(
        cal_packet, workdir, session=session)
    verifier_sha = hashlib.sha256(
        Path(v2.__file__).read_bytes()).hexdigest()
    # the metrics identity was written by the RUN's in-image verifier; when
    # the receipt declares (and the gate verified) that commit's verifier
    # sha, compare against IT — else against this current verifier.
    failures, facts = v2.verify_live_bundle(
        packet=cal_packet, receipt=receipt, extracted=extracted,
        s3_meta=s3_meta,
        verifier_script_sha=metrics_verifier_sha or verifier_sha)
    # Codex second review (2026-08-25) finding 5 / item 5: the calibration
    # chain being re-verified may have been launched via the DOCUMENTED
    # below-tier local route (the owner's exact IAM user) or the calibration
    # workflow role — scope the expectation by the cal packet's own tier
    # instead of hardcoding the workflow role.
    _cal_wc = (float(cal_packet.get("max_runtime_seconds")) / 3600.0
               * ON_DEMAND_USD_PER_HOUR[str(cal_packet.get("instance_type"))])
    _cal_above = is_campaign_arm_job(cal_packet.get("environment") or {},
                                     _cal_wc)
    _expected = ((f"arn:aws:iam::{ACCOUNT}:role/"
                  + expected_arm_launch_role(cal_packet.get("environment")
                                             or {}),)
                 if _cal_above else
                 (v2.CALIBRATION_LAUNCH_ROLE_ARN,
                  v2.OWNER_LOCAL_PRINCIPAL_ARN))
    failures = list(failures) + v2.verify_creation_event(
        creation_event, render_request(cal_packet),
        expected_job_name=f"medzen-b5-{cal_packet['job_id']}",
        expected_job_arn=str(receipt.get("TrainingJobArn") or ""),
        expected_principal_role_arn=_expected)
    if failures:
        raise JobRefusal(
            "the authoritative live re-verification of the calibration "
            f"artifact FAILED ({failures[0]}) — refusing to ride this chain")
    facts["verifier_script_sha256"] = verifier_sha
    return facts


def _hex(value: str, length: int) -> bool:
    return len(value) == length and all(
        c in "0123456789abcdef" for c in value.lower())


def _finite_nonneg(value, what: str) -> float:
    import math
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise JobRefusal(f"{what} is not a number")
    if not math.isfinite(number) or number < 0:
        raise JobRefusal(f"{what} is {value!r} — non-finite or negative "
                         "financial values refuse (Codex review #23: NaN "
                         "passed every comparison)")
    return number


def recompute_registry_totals(registry: dict) -> dict:
    """The registry's own arithmetic, re-derived from EFFECTIVE rows
    (last line per allocation_id). Codex review #23: the summary said
    $584.43 while the rows totalled $514.43 — summaries must be a pure
    function of rows, never hand-maintained."""
    effective: dict[str, dict] = {}
    for line in registry.get("allocations", []):
        if line.get("allocation_id"):
            effective[line["allocation_id"]] = line
    recognized = 0.0
    active_sum = 0.0
    accrual_sum = 0.0
    for aid, line in effective.items():
        value = line.get("recognized_committed_usd")
        if value is not None:
            recognized += _finite_nonneg(
                value, f"allocation {aid} recognized_committed_usd")
        if line.get("financial_state") == "ACTIVE_RESERVED":
            active_sum += _finite_nonneg(
                line.get("reservation_usd", 0),
                f"allocation {aid} reservation_usd")
        # Codex reviews #14-#16: spend must be visible but HONESTLY
        # labelled. CALCULATED_ESTIMATE rows carry a conservative list-price
        # estimate_usd (a deliberate OVER-estimate; Cost Explorer is still
        # Estimated:true — these are NOT billed actuals). Legacy
        # CALCULATED_ACCRUAL/actual_usd rows are read the same way.
        if line.get("financial_state") in ("CALCULATED_ESTIMATE",
                                            "CALCULATED_ACCRUAL"):
            accrual_sum += _finite_nonneg(
                line.get("estimate_usd", line.get("actual_usd", 0)),
                f"allocation {aid} estimate_usd")
    return {"effective": effective, "recognized": round(recognized, 10),
            "active": round(active_sum, 10),
            "calculated_estimate": round(accrual_sum, 10)}


def verify_active_reservation(registry_binding: dict, bindings: dict,
                               worst_case_usd: float,
                               repo_root: Path | None = None,
                               head_oid: str | None = None) -> None:
    """Codex reviews #21-#23. The committed registry must show THIS
    packet's allocation as the single unexpired ACTIVE_RESERVED line,
    sized for the worst case, and the summary must EQUAL the recompute
    from effective rows with finite non-negative values under the
    aggregate ceiling."""
    root = repo_root or Path(__file__).resolve().parents[1]
    oid = head_oid or repo_head_oid(root)
    if not isinstance(registry_binding, dict):
        raise JobRefusal("launch intent must bind the cost registry "
                         "{file, sha256, allocation_id}")
    rel = str(registry_binding.get("file") or "")
    if not rel.startswith("platform/finance/"):
        raise JobRefusal("registry binding must live under "
                         "platform/finance/")
    body = _show_at(root, oid, rel)
    if body is None:
        raise JobRefusal(f"registry {rel} is not committed at {oid[:12]}")
    if hashlib.sha256(body).hexdigest() != registry_binding.get("sha256"):
        raise JobRefusal("registry binding sha256 does not match the "
                         f"committed bytes of {rel}")
    registry = json.loads(body)
    totals = recompute_registry_totals(registry)
    allocation_id = registry_binding.get("allocation_id")
    ours = totals["effective"].get(allocation_id)
    if ours is None:
        raise JobRefusal(f"allocation {allocation_id!r} does not exist")
    if ours.get("financial_state") != "ACTIVE_RESERVED":
        raise JobRefusal(
            f"allocation {allocation_id!r} is "
            f"{ours.get('financial_state')!r}, not ACTIVE_RESERVED")
    if _finite_nonneg(ours.get("reservation_usd", 0),
                      "reservation_usd") < worst_case_usd:
        raise JobRefusal("active reservation does not cover the worst case")
    if ours.get("packet_bindings_sha256") != \
            canonical_bindings_sha256(bindings):
        raise JobRefusal(
            "the ACTIVE reservation is bound to a DIFFERENT packet sha "
            "(Codex review #22)")
    expiry = str(ours.get("reservation_expires_utc") or "")
    import datetime
    try:
        expires = datetime.datetime.fromisoformat(
            expiry.replace("Z", "+00:00"))
    except ValueError:
        raise JobRefusal("ACTIVE_RESERVED lines must carry a valid "
                         "reservation_expires_utc (Codex review #23: no "
                         "reservation expiry existed)")
    if datetime.datetime.now(datetime.timezone.utc) >= expires:
        raise JobRefusal(f"the reservation expired at {expiry} — cut a "
                         "fresh registry revision to re-reserve")
    active = [aid for aid, line in totals["effective"].items()
              if line.get("financial_state") == "ACTIVE_RESERVED"]
    if active != [allocation_id]:
        raise JobRefusal(
            f"one-active-reservation rule violated: {sorted(active)}")
    summary = registry.get("guardrail_summary") or {}
    ceiling = _finite_nonneg(summary.get("aggregate_ceiling_usd", 0),
                             "aggregate_ceiling_usd")
    for key, expected in (
            ("recognized_committed_guardrail_usd", totals["recognized"]),
            ("active_reservations_usd", totals["active"]),
            # Codex review #24: the DERIVED fields were not recomputed —
            # corrupted committed_plus_reserved/headroom passed
            ("committed_plus_reserved_usd",
             totals["recognized"] + totals["active"]),
            ("guardrail_headroom_after_reservations_usd",
             ceiling - totals["recognized"] - totals["active"])):
        declared = _finite_nonneg(summary.get(key, float("nan")), key)
        if abs(declared - expected) > 0.01:
            raise JobRefusal(
                f"registry summary {key}=${declared:.2f} does not match "
                f"the recompute from effective rows ${expected:.2f} — "
                "summaries must be pure functions of rows "
                "(Codex reviews #23-#24)")
    declared_count = str(registry.get("controls", {}).get(
        "current_active_billable_reservations", ""))
    active_count = sum(
        1 for line in totals["effective"].values()
        if line.get("financial_state") == "ACTIVE_RESERVED")
    if declared_count != str(active_count):
        raise JobRefusal(
            f"controls.current_active_billable_reservations="
            f"{declared_count!r} but the rows hold {active_count} — "
            "the count is a derived field too (Codex review #24)")
    if totals["recognized"] + totals["active"] > ceiling + 1e-9:
        raise JobRefusal("registry arithmetic breaches the aggregate "
                         "ceiling")


def load_intent(job_id: str, root: Path, oid: str) -> dict:
    """The launch-intent record: the unsigned COMMITTED document binding
    packet, registry line, receipt and the review's exact bytes. Codex
    review #24: the in-repo SSH allowed-signers file was forgeable by
    any repository writer, so the signature layer is REMOVED rather than
    kept as theater — the OWNER's authorization is their click on the
    protected GitHub environment (github.com identity, outside every
    local trust surface), and the workflow verifies this intent chain
    before anything spends."""
    if not job_id or not all(c.islower() or c.isdigit() or c == "-"
                             for c in job_id):
        raise JobRefusal("malformed job id")
    intent_rel = f"{INTENTS_DIR}/{job_id}.json"
    intent_body = _show_at(root, oid, intent_rel)
    if intent_body is None:
        raise JobRefusal(f"no committed launch intent at {intent_rel}")
    intent = json.loads(intent_body)
    if intent.get("job_id") != job_id:
        raise JobRefusal("launch intent names a different job")
    return intent


def verify_intent_chain(intent: dict, bindings: dict, worst_case: float,
                         root: Path, oid: str) -> None:
    """Codex review #23 finding 2 (reproduced: owner signed while the
    review was PENDING; the review was flipped to APPROVED afterwards
    and both passed). The signed intent must bind the review's EXACT
    committed bytes (sha) and decision, the packet, the receipt, an
    integer ceiling covering the worst case, and its own expiry."""
    packet_rel = str(intent.get("packet", {}).get("file") or "")
    packet_body = _show_at(root, oid, packet_rel)
    packet_sha = canonical_bindings_sha256(bindings)
    if packet_body is None or canonical_bindings_sha256(
            json.loads(packet_body)) != packet_sha or \
            intent["packet"].get("canonical_sha256") != packet_sha:
        raise JobRefusal("the signed intent binds a DIFFERENT packet "
                         "than the one launching")
    review_ref = intent.get("review") or {}
    review_rel = str(review_ref.get("file") or "")
    review_body = _show_at(root, oid, review_rel)
    if review_body is None:
        raise JobRefusal("the signed intent's review record is not "
                         "committed")
    if hashlib.sha256(review_body).hexdigest() != \
            review_ref.get("sha256"):
        raise JobRefusal(
            "the committed review record differs from the bytes the "
            "owner signed over — a review changed AFTER signing cannot "
            "ride the old signature (Codex review #23)")
    review = json.loads(review_body)
    if review.get("decision") != "APPROVED" or \
            review_ref.get("decision") != "APPROVED":
        raise JobRefusal("the owner may only sign an APPROVED review — "
                         f"this one is {review.get('decision')!r}")
    if review.get("bindings_sha256") != packet_sha:
        raise JobRefusal("the reviewed packet differs from the launching "
                         "packet")
    if not str(review_ref.get("reviewer") or "").strip() or \
            review.get("reviewer") != review_ref.get("reviewer"):
        raise JobRefusal("the intent must name the reviewer it relied on")
    if intent.get("receipt", {}).get("record_sha256") != \
            bindings["calibration_receipt"]["record_sha256"]:
        raise JobRefusal("the signed intent binds a different calibration "
                         "receipt")
    ceiling = intent.get("ceiling_usd")
    if type(ceiling) is not int:
        raise JobRefusal("intent ceiling_usd must be a strict integer")
    if not (worst_case <= ceiling <= float(
            bindings.get("cost_ceiling_usd", 0))):
        raise JobRefusal(
            f"intent ceiling ${ceiling} must cover the ${worst_case:.2f} "
            "worst case and stay within the packet ceiling")
    import datetime
    expiry = str(intent.get("expires_utc") or "")
    try:
        expires = datetime.datetime.fromisoformat(
            expiry.replace("Z", "+00:00"))
    except ValueError:
        raise JobRefusal("the intent must carry a valid expires_utc")
    if datetime.datetime.now(datetime.timezone.utc) >= expires:
        raise JobRefusal(f"the signed intent expired at {expiry}")


def assert_committed_profile(bindings: dict, root: Path, oid: str) -> None:
    """Codex review #23 finding 4 (reproduced: an english-only request
    passed after coordinated WORKING-TREE protocol edits). At launch the
    multilingual profile is re-asserted against the COMMITTED protocol
    bytes — the working tree has no vote."""
    environment = bindings.get("environment") or {}
    if not environment.get("MEDZEN_MULTILINGUAL_FULL_ACK"):
        return
    protocol = load_protocol(root, oid=oid)
    mandatory = set(protocol["mandatory_languages"])
    requested = {t.strip() for t in
                 environment.get("MEDZEN_LANGUAGES", "").split(",")
                 if t.strip()}
    if requested != mandatory:
        raise JobRefusal(
            f"COMMITTED protocol requires {sorted(mandatory)} exactly; "
            f"the packet binds {sorted(requested)} — working-tree "
            "protocol edits cannot widen or narrow the set "
            "(Codex review #23)")


EXECUTOR_ENV = "MEDZEN_EXECUTOR"
PROTECTED_EXECUTOR = "github-protected-workflow"
ARM_LAUNCH_ROLE = "medzen-arm-launch-role"
# Codex second review (2026-08-25) finding 2: Arm-2 comparative campaign jobs
# launch as the DEDICATED stage-1 role (arm2-stage1-launch-exec.yml); the
# legacy arm-launch role remains only for non-comparative (Arm-1) arm jobs.
STAGE1_LAUNCH_ROLE = "medzen-arm2-stage1-launch-role"
SCORING_LAUNCH_ROLE = "medzen-arm2-scoring-launch-role"


def expected_arm_launch_role(environment: dict) -> str:
    """The above-tier launch role, by job class: evaluator/scoring jobs use
    the dedicated scoring role; comparative campaign arms the stage-1 role;
    legacy (Arm-1) jobs the original arm-launch role."""
    if str(environment.get("MEDZEN_EXECUTION_MODE", "")).strip() \
            == "arm2_scoring":
        return SCORING_LAUNCH_ROLE
    return (STAGE1_LAUNCH_ROLE if is_arm2_comparative(environment)
            else ARM_LAUNCH_ROLE)


def assert_launch_identity(arn: str, above_tier: bool,
                           environment: dict | None = None) -> None:
    """Codex review #25 finding 5: the launcher only checked the account
    number, so forged executor variables under LOCAL credentials would
    have sailed past the identity check. Above-tier launches must run as
    an assumed session of the DEDICATED launch role for the job class
    (Codex second review 2026-08-25 finding 2: Arm-2 comparative campaign
    jobs use the stage-1 role; legacy arm jobs keep the arm-launch role) —
    local users and the general CI role refuse."""
    if not above_tier:
        return
    role = expected_arm_launch_role(environment or {})
    prefix = f"arn:aws:sts::{ACCOUNT}:assumed-role/{role}/"
    if not str(arn or "").startswith(prefix):
        raise JobRefusal(
            f"above-tier launches must run as {role} (the protected "
            f"workflow's dedicated role for this job class); caller is "
            f"{arn!r} — forged executor variables under other credentials "
            "refuse (Codex review #25)")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("render", "validate", "launch"))
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--request", type=Path)
    args = parser.parse_args()
    bindings = json.loads(args.bindings.read_bytes())

    try:
        if args.mode == "render":
            print(json.dumps(render_request(bindings), indent=1, sort_keys=True))
            return 0
        if args.request is None:
            raise JobRefusal(f"--request is required for {args.mode}")
        request = json.loads(args.request.read_bytes())
        result = validate_request(request, bindings)
        if args.mode == "validate":
            print(json.dumps(result, sort_keys=True))
            return 0
        # Codex review #21 rec 6: a DRAFT packet may be rendered and validated
        # during authoring, but may NEVER launch — the launchable packet is the
        # committed, reviewed, digest-pinned one.
        if "DRAFT_STATUS" in bindings or ".DRAFT." in args.bindings.name:
            raise JobRefusal(
                "this packet is a DRAFT (DRAFT_STATUS present or .DRAFT. in "
                "the filename) — launch requires the committed, reviewed, "
                "digest-pinned packet")
        job_id = bindings["job_id"]
        worst_case = result["worst_case_on_demand_usd"]
        root = Path(__file__).resolve().parents[1]
        head = repo_head_oid(root)
        receipt_record = None
        cal_packet = None
        # Codex stage-1 review finding 1: tier by JOB CLASS, not price — a
        # 2000-step comparative arm is a campaign job at any worst case.
        above_tier = is_campaign_arm_job(bindings.get("environment") or {},
                                         worst_case)
        scoring_job = (str((bindings.get("environment") or {}).get(
            "MEDZEN_EXECUTION_MODE", "")).strip() == "arm2_scoring")
        if above_tier and scoring_job:
            # owner narrow functional correction (2026-08-26): evaluator jobs
            # are READ-ONLY decode jobs — they launch ONLY from the protected
            # workflow with a committed APPROVED review record; the
            # training-campaign chain (intent, calibration receipt, campaign
            # reservation, committed profile) is training machinery and does
            # not apply to a decode job.
            import os
            if not (os.environ.get(EXECUTOR_ENV) == PROTECTED_EXECUTOR
                    and os.environ.get("GITHUB_ACTIONS") == "true"):
                raise JobRefusal(
                    "scoring launches go through the protected workflow "
                    "(.github/workflows/arm2-scoring-eval.yml) — its "
                    "environment requires the OWNER's approval and its "
                    "dedicated role is the only intended creator")
            review_record_approves(job_id, bindings, root, head)
        elif above_tier:
            # the OUTERMOST boundary first: locally, above-tier launches
            # refuse no matter what the other gates say
            import os
            if not (os.environ.get(EXECUTOR_ENV) == PROTECTED_EXECUTOR
                    and os.environ.get("GITHUB_ACTIONS") == "true"):
                raise JobRefusal(
                    "above-tier launches go through the protected "
                    "workflow (.github/workflows/arm-launch.yml): its "
                    "GitHub environment requires the OWNER's approval "
                    "and its dedicated AWS role is the only identity "
                    "meant to create arm-tier jobs. HONEST LIMIT (Codex "
                    "review #24): this refusal is PROCESS enforcement — "
                    "env vars can be forged and local credentials can "
                    "call SageMaker directly until the owner applies "
                    "platform/iam/medzen-local-boundary-policy.json, "
                    "which is what actually closes the local path.")
            review_record_approves(job_id, bindings, root, head)
            intent = load_intent(job_id, root, head)
            verify_intent_chain(intent, bindings, worst_case, root, head)
            receipt_record = verify_calibration_receipt(bindings,
                                                        head_oid=head)
            cal_packet = json.loads(_show_at(
                root, head, receipt_record["calibration_packet"]))
            verify_active_reservation(intent.get("registry"), bindings,
                                      worst_case, head_oid=head)
            assert_committed_profile(bindings, root, head)
        else:
            review_record_approves(job_id, bindings, root, head)
        # ONE boto3 session: STS pin + AWS receipt facts + the mutation
        import boto3
        session = boto3.session.Session(region_name=REGION)
        try:
            identity = session.client("sts").get_caller_identity()
        except Exception as exc:
            raise JobRefusal(f"cannot establish an AWS identity "
                             f"({type(exc).__name__}) — refusing to "
                             "launch") from exc
        if identity.get("Account") != ACCOUNT:
            raise JobRefusal(
                f"effective AWS account is {identity.get('Account')!r}, "
                f"not the MedZen account {ACCOUNT} — refusing to launch")
        assert_launch_identity(identity.get("Arn"), above_tier,
                               bindings.get("environment") or {})
        if receipt_record is not None:
            verify_receipt_against_aws(receipt_record, cal_packet,
                                       session.client("sagemaker"),
                                       session.client("s3"))
            # Codex round 30 findings 1-2: the request+identity check above is
            # not enough — re-derive the artifact CONTENT (re-hash the real
            # KMS-encrypted model by explicit VersionId + re-run the CloudTrail
            # creation-event/metrics checks via the authoritative verifier) and
            # require the committed receipt's content to match byte-for-byte, so
            # a fabricated export/metrics/size cannot ride an above-tier launch.
            import tempfile
            _workdir = Path(tempfile.mkdtemp(prefix="arm2-launch-verify-"))
            # Codex round 31: reuse the launcher's ONE role-asserted session so
            # the re-verification runs under the SAME assumed launch role, not a
            # second credential path.
            _facts = derive_live_artifact_facts(cal_packet, _workdir,
                session=session,
                metrics_verifier_sha=(receipt_record or {}).get(
                    "run_verifier_sha256"))
            cross_check_receipt_content(receipt_record, _facts)
        response = session.client("sagemaker").create_training_job(**request)
        print(json.dumps({"TrainingJobArn": response["TrainingJobArn"]},
                         indent=4))
        return 0
    except JobRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
