#!/usr/bin/env python3
"""Machine-enforced Arm-2 calibration acceptance checker (Codex review #19 F3).

The calibration DRAFT enumerates acceptance criteria in prose; nothing
executable enforced them, so an empty or malformed result would have passed
review by eye. This script turns those criteria into a hard gate: it reads the
metrics artifact the trainer writes (pipeline.omniasr_train.CalibrationMetrics
-> calibration-metrics.json) plus the calibration packet's `result_verifier`
block, and EXITS NON-ZERO if any criterion is unmet. It is host-safe (stdlib
only) and its core (`verify_calibration`) is unit-tested.

Contract (the metrics artifact schema `b5-arm2-calibration-metrics/1`):
  - the TRAINER writes the training-side fields (status, per_step CTC/KD/total,
    per-language kd_coverage, peak_gpu_bytes, throughput);
  - the in-image calibration WRAPPER, after training, runs export -> readyz ->
    dev-sentinel WER and patches `serve` and `dev_sentinel_wer`.
The verifier requires BOTH halves, so a run that skips serve/dev-WER — or
silently produces no KD — fails closed rather than reading as "passed".

Usage:
    python -m scripts.verify_arm2_calibration \
        --metrics /opt/ml/model/calibration-metrics.json \
        --packet  platform/manifests/B5-UNIVERSAL-ARM2-FTCAL-SAGEMAKER-BINDINGS-2026-001.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

METRICS_SCHEMA = "b5-arm2-calibration-metrics/1"
# Arm-2b: dual-KD runs (base preservation teacher + Arm-1 retention anchor)
# emit /2, adding the kd_retention decomposition + coverage. A packet pins
# exactly one schema; the artifact must match the PACKET's pin, so a dual-KD
# run cannot masquerade as single-KD or vice versa.
METRICS_SCHEMA_V2 = "b5-arm2-calibration-metrics/2"
CANONICAL_SCRIPT = "scripts/verify_arm2_calibration.py"
CANONICAL_ARTIFACT = "calibration-metrics.json"
# g6.xlarge carries a single NVIDIA L4 = 24 GiB. No reviewed ceiling may exceed
# the physical device (Codex review #20 F4: an "enormous" ceiling was accepted).
L4_PHYSICAL_BYTES = 24 * 1024 * 1024 * 1024
# the regression sentinels Arm-2 exists to protect — a dev-language list that
# omits them is not a valid Arm-2 calibration (Codex review #20 F4)
MANDATORY_DEV_SENTINELS = frozenset({"lingala", "swahili"})
IDENTITY_FIELDS = ("run_fingerprint", "training_job_name",
                   "export_manifest_sha256", "export_model_sha256",
                   "dev_manifest_shas", "scorer", "packet_sha256",
                   "execution_contract_sha256", "verifier_script_sha256")
# the pinned MedZen account/region (Codex #22 blocker 1: live mode must never
# read another account's artifacts)
MEDZEN_ACCOUNT = "558069890522"
MEDZEN_REGION = "eu-central-1"
# the dedicated calibration-launch role the Arm-2 calibration MUST run under
# (Codex #26 finding 6; created by infra/arm2_calibration_role.tf). The live
# verifier binds the CloudTrail principal to exactly this role.
CALIBRATION_LAUNCH_ROLE_ARN = (
    "arn:aws:iam::558069890522:role/medzen-arm2-calibration-role")
# Codex stage-1 review (2026-08-25) finding 4c — tier-scoped principals:
# campaign/arm jobs are created ONLY by the protected arm-launch role; the
# documented below-tier LOCAL route (Codex #24) runs under the owner's exact
# IAM user (never an arbitrary principal).
ARM_LAUNCH_ROLE_ARN = (
    "arn:aws:iam::558069890522:role/medzen-arm-launch-role")
OWNER_LOCAL_PRINCIPAL_ARN = "arn:aws:iam::558069890522:user/s.fotso"
# the ONLY members live mode extracts from model.tar.gz (path-traversal-safe)
# with per-member size caps (Codex #23: unbounded extraction was a
# disk-exhaustion path)
BUNDLE_MEMBERS = ("calibration-metrics.json", "export/manifest.json",
                  "export/model.pt")
# Codex #25 finding 3: caps sized to the REAL ~2.6 GB model (not 8-9 GB
# ceilings that could still overwhelm a runner) — archive + extraction peak
# is ~8 GB under these bounds, and live_fetch ALSO preflights free disk.
BUNDLE_MEMBER_MAX_BYTES = {
    "calibration-metrics.json": 64 * 1024 * 1024,       # generous for receipts
    "export/manifest.json": 4 * 1024 * 1024,
    "export/model.pt": 4 * 1024 * 1024 * 1024,          # ~2.6 GB real model
}
ARCHIVE_MAX_BYTES = 4 * 1024 * 1024 * 1024
ARCHIVE_MAX_MEMBERS = 64
ARCHIVE_MAX_AGGREGATE_BYTES = 4_800_000_000
DISK_SAFETY_MARGIN_BYTES = 2 * 1024 * 1024 * 1024

# ---- the GOVERNED field sets (Codex #25 finding 1) ----
# Every member of the pinned botocore CreateTrainingJob∩DescribeTrainingJob
# model must be RENDERED (exact-compared), UNRENDERED-INERT (must be absent
# or provably inert in the receipt), or CREATE-ONLY-GOVERNED (not echoed by
# Describe — verified against the CloudTrail creation record + the exclusive
# workflow/IAM boundary). A botocore upgrade that adds a field FAILS the
# model-coverage test until the field is governed here.
RENDERED_TOP_KEYS = frozenset({
    "TrainingJobName", "RoleArn", "AlgorithmSpecification",
    "OutputDataConfig", "CheckpointConfig", "ResourceConfig", "VpcConfig",
    "StoppingCondition", "EnableManagedSpotTraining",
    "EnableNetworkIsolation", "EnableInterContainerTrafficEncryption",
    "ProfilerConfig", "RemoteDebugConfig", "Environment",
})
UNRENDERED_INERT_KEYS = frozenset({
    "HyperParameters", "InputDataConfig", "DebugHookConfig",
    "DebugRuleConfigurations", "TensorBoardOutputConfig", "ExperimentConfig",
    "ProfilerRuleConfigurations", "RetryStrategy", "InfraCheckConfig",
    "MlflowConfig", "ModelPackageConfig", "ServerlessJobConfig",
})
CREATE_ONLY_GOVERNED = frozenset({"SessionChainingConfig", "Tags"})


def required_free_bytes(declared_archive_bytes: int) -> int:
    """Codex #26 finding 5: peak disk = the compressed archive on disk PLUS the
    full aggregate extraction cap (extracted bytes are NOT bounded by the
    compressed size) PLUS the safety margin — not archive*2."""
    return (int(declared_archive_bytes) + ARCHIVE_MAX_AGGREGATE_BYTES
            + DISK_SAFETY_MARGIN_BYTES)


def stream_with_cap(body, dest: Path, cap: int, *, label: str) -> int:
    """Stream a response body to disk, refusing past `cap` bytes — a lying
    ContentLength cannot exhaust the disk mid-download."""
    written = 0
    with dest.open("wb") as stream:
        for chunk in iter(lambda: body.read(1 << 22), b""):
            written += len(chunk)
            if written > cap:
                raise SystemExit(
                    f"{label} exceeded the {cap}-byte cap mid-stream — "
                    "refusing disk exhaustion")
            stream.write(chunk)
    return written
# identity fields that must be a 64-hex sha256 (Codex review #20 F5 follow-up:
# presence-only let a fabricated file through — enforce the FORMAT of every
# sha and the value of the derivable ones)
IDENTITY_SHA_FIELDS = ("run_fingerprint", "export_manifest_sha256",
                       "export_model_sha256", "packet_sha256",
                       "execution_contract_sha256", "verifier_script_sha256")
# the canonical scorer string the calibration wrapper stamps; kept in lock-step
# with pipeline.omniasr_calibrate.SCORER_ID (a host test asserts equality).
CANONICAL_SCORER = ("preproc=resample16k+utterance-znorm; "
                    "ctc-greedy/seqlen-truncate+argmax+collapse+blank-strip+"
                    "skip-special-tokens; "
                    "normalizer=pipeline.normalizers.for_language; "
                    "metric=corpus-word-error-rate/3")
# the exact pinned upstream decoder the mandatory in-run parity probe names
# (kept in lock-step with pipeline.omniasr_calibrate.UPSTREAM_PIPELINE_ID)
CANONICAL_UPSTREAM_PIPELINE = ("omnilingual_asr.models.inference.pipeline."
                               "ASRInferencePipeline@145a12a6")
import re as _re

_HEX64 = _re.compile(r"^[0-9a-f]{64}$")


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_calibration(metrics: dict[str, Any],
                       verifier_spec: dict[str, Any],
                       *, packet_canonical_sha: str | None = None,
                       verifier_script_sha: str | None = None,
                       expected_job_name: str | None = None,
                       expected_contract_sha: str | None = None,
                       authenticated_export: dict[str, Any] | None = None) -> list[str]:
    """Return a list of human-readable FAILURE strings — empty means PASS.

    Every acceptance criterion is checked independently so one run surfaces
    ALL the reasons it is not calibration-clean, not just the first.

    ``packet_canonical_sha`` / ``verifier_script_sha`` (when supplied) bind the
    metrics to the EXACT reviewed packet and to this verifier's own bytes
    (Codex review #20 F5): a metrics file produced under a different packet or
    a different verifier is rejected."""
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)

    schema = metrics.get("schema")
    # the artifact schema must equal the PACKET-pinned schema (/1 single-KD,
    # /2 dual-KD with the Arm-2b retention anchor); a mismatch in either
    # direction is a masquerade and nothing else is trustworthy under it
    want_schema = str(verifier_spec.get("metrics_schema") or METRICS_SCHEMA)
    if want_schema not in (METRICS_SCHEMA, METRICS_SCHEMA_V2):
        fail(f"result_verifier.metrics_schema {want_schema!r} is not a known "
             f"schema ({METRICS_SCHEMA!r} or {METRICS_SCHEMA_V2!r})")
        return failures
    if schema != want_schema:
        fail(f"metrics schema {schema!r} != the packet-pinned {want_schema!r}")
        return failures  # nothing else is trustworthy under a wrong schema
    dual_kd = want_schema == METRICS_SCHEMA_V2

    # (1) completed the declared step budget — no silent short/over run
    expected_steps = int(verifier_spec["expected_steps"])
    # The KD-only checks apply only to a KD-on candidate; a KD-off comparative
    # CONTROL declares kd_enabled=false (owner-directed shared wrapper). Absent
    # => legacy KD-on, so existing calibration specs are unchanged. kd_enabled
    # must be an EXACT JSON boolean — never a bool(...) coercion that would let
    # a truthy string/int pass as a flag (Codex round 32).
    _raw_kd = verifier_spec.get("kd_enabled", True)
    if not isinstance(_raw_kd, bool):
        fail(f"result_verifier.kd_enabled must be a JSON boolean, got "
             f"{_raw_kd!r}")
        _raw_kd = True   # after failing, run the KD checks (fail closed)
    kd_enabled = _raw_kd
    if dual_kd and not kd_enabled:
        fail("metrics_schema /2 (dual-KD retention anchor) with "
             "kd_enabled=false is contradictory — the anchor IS a KD term")
    status = metrics.get("status")
    if status != "COMPLETED":
        fail(f"status is {status!r}, not COMPLETED (no silent success)")
    if int(metrics.get("steps_completed", -1)) != expected_steps:
        fail(f"steps_completed={metrics.get('steps_completed')} != "
             f"expected {expected_steps}")
    if int(metrics.get("max_steps", -1)) != expected_steps:
        fail(f"max_steps={metrics.get('max_steps')} != expected {expected_steps}")

    # (2) separate CTC / KD / total per step, KD > 0 and finite every step,
    # steps CONTIGUOUS 1..N (Codex review #20 F5: a resumed/torn accumulator
    # must not pass with a short or out-of-order per_step), and the loss
    # equation total == ctc + alpha*kd must hold each step
    per_step = metrics.get("per_step") or []
    if len(per_step) != expected_steps:
        fail(f"per_step has {len(per_step)} records, expected {expected_steps}")
    for index, record in enumerate(per_step):
        if int(record.get("step", -1)) != index + 1:
            fail(f"per_step[{index}].step={record.get('step')} is not "
                 f"contiguous (expected {index + 1}) — torn/resumed trajectory")
        for key in ("ctc", "kd", "total", "alpha"):
            if not _finite_number(record.get(key)):
                fail(f"step {record.get('step')}: {key} is not a finite number "
                     f"({record.get(key)!r})")
        if kd_enabled:
            if _finite_number(record.get("kd")) and float(record["kd"]) <= 0.0:
                fail(f"step {record.get('step')}: KD={record['kd']} is not > 0 "
                     "(the distillation term is not live on preservation "
                     "batches)")
        else:
            # KD-off CONTROL: the distillation term must be PROVABLY zero on
            # EVERY step, not merely a summary counter (Codex round 32 bypass:
            # kd=0.2, alpha=0.5, total=ctc+alpha*kd passed with the counter=0).
            if _finite_number(record.get("kd")) and float(record["kd"]) != 0.0:
                fail(f"step {record.get('step')}: KD={record['kd']} != 0 for a "
                     "KD-off control — the distillation term must be off")
            if _finite_number(record.get("alpha")) \
                    and float(record["alpha"]) != 0.0:
                fail(f"step {record.get('step')}: alpha={record['alpha']} != 0 "
                     "for a KD-off control")
            if _finite_number(record.get("ctc")) \
                    and _finite_number(record.get("total")) \
                    and not math.isclose(float(record["total"]),
                                         float(record["ctc"]),
                                         rel_tol=1e-4, abs_tol=1e-5):
                fail(f"step {record.get('step')}: total={record['total']} != "
                     f"ctc={record['ctc']} for a KD-off control")
        # Arm-2b retention-term shape rules: a /1 record must NOT carry the
        # retention decomposition (a dual-KD run masquerading as single-KD),
        # and a /2 record must carry it on EVERY step, finite and > 0 (a
        # dead anchor must not pass as live).
        has_retention = ("kd_retention" in record
                         or "retention_alpha" in record)
        if not dual_kd and has_retention:
            fail(f"step {record.get('step')}: retention KD fields present "
                 "under the single-KD /1 schema — dual-KD masquerade")
        if dual_kd:
            if not has_retention:
                fail(f"step {record.get('step')}: no kd_retention record — "
                     "the retention anchor was not live on this step")
            else:
                for key in ("kd_retention", "retention_alpha"):
                    if not _finite_number(record.get(key)):
                        fail(f"step {record.get('step')}: {key} is not a "
                             f"finite number ({record.get(key)!r})")
                if _finite_number(record.get("kd_retention")) \
                        and float(record["kd_retention"]) <= 0.0:
                    fail(f"step {record.get('step')}: "
                         f"kd_retention={record['kd_retention']} is not > 0 "
                         "(the retention anchor is not live on retention "
                         "batches)")
                if _finite_number(record.get("retention_alpha")) \
                        and float(record["retention_alpha"]) <= 0.0:
                    fail(f"step {record.get('step')}: retention_alpha="
                         f"{record['retention_alpha']} is not > 0")
        if all(_finite_number(record.get(k)) for k in ("ctc", "kd", "total",
                                                       "alpha")):
            expected_total = float(record["ctc"]) + float(record["alpha"]) \
                * float(record["kd"])
            equation = "ctc + alpha*kd"
            if dual_kd and _finite_number(record.get("kd_retention")) \
                    and _finite_number(record.get("retention_alpha")):
                expected_total += (float(record["retention_alpha"])
                                   * float(record["kd_retention"]))
                equation = "ctc + alpha*kd + retention_alpha*kd_retention"
            if not math.isclose(float(record["total"]), expected_total,
                                rel_tol=1e-4, abs_tol=1e-5):
                fail(f"step {record.get('step')}: total={record['total']} != "
                     f"{equation} ({expected_total}) — loss equation "
                     "violated")
    # step_sequence must corroborate per_step exactly
    if metrics.get("step_sequence") != [r.get("step") for r in per_step]:
        fail("step_sequence does not match per_step ordering")
    positive = int(metrics.get("kd_positive_finite_steps", 0))
    if kd_enabled:
        if positive != expected_steps:
            fail(f"kd_positive_finite_steps={positive} != {expected_steps} — "
                 "KD was not positive-and-finite on every step")
    elif positive != 0:
        fail(f"kd_positive_finite_steps={positive} != 0 for a KD-off control "
             "— the distillation term must NOT be live in the control")
    if dual_kd:
        # the retention anchor must be provably live on EVERY step, and the
        # summary counter is RECOMPUTED from per_step — never trusted alone
        # (same discipline as the KD-off control recompute, Codex round 32)
        ret_positive = int(metrics.get(
            "kd_retention_positive_finite_steps", 0))
        if ret_positive != expected_steps:
            fail(f"kd_retention_positive_finite_steps={ret_positive} != "
                 f"{expected_steps} — the retention anchor was not "
                 "positive-and-finite on every step")
        ret_recomputed = sum(
            1 for r in per_step
            if _finite_number(r.get("kd_retention"))
            and float(r["kd_retention"]) > 0.0)
        if ret_recomputed != expected_steps:
            fail(f"recomputed positive-retention steps = {ret_recomputed} != "
                 f"{expected_steps} (the summary counter alone is not "
                 "trusted)")
    if not kd_enabled:
        # RECOMPUTE the positive-KD count from per_step — never trust the
        # self-reported summary counter alone (Codex round 32).
        recomputed = sum(1 for r in per_step
                         if _finite_number(r.get("kd")) and float(r["kd"]) > 0.0)
        if recomputed != 0:
            fail(f"recomputed positive-KD steps = {recomputed} != 0 for a "
                 "KD-off control (the summary counter alone is not trusted)")
        cov = metrics.get("kd_coverage") or {}
        for lang, bucket in cov.items():
            b = bucket or {}
            if int(b.get("rows", 0)) != 0 or int(b.get("frames", 0)) != 0:
                fail(f"KD coverage for {lang!r} is nonzero (rows="
                     f"{b.get('rows')}, frames={b.get('frames')}) on a KD-off "
                     "control — the distillation term must not have run")

    # (3) per-language KD coverage: KD-ON only. Every required preservation
    # language must have contributed real rows AND valid frames. Codex review
    # #20 F4 defense-in-depth: an EMPTY required_preservation_coverage must FAIL
    # here (not silently skip) for a KD run. The KD-off control skips this.
    if kd_enabled:
        required_coverage = verifier_spec.get(
            "required_preservation_coverage") or []
        if not required_coverage:
            fail("result_verifier.required_preservation_coverage is empty — "
                 "the KD-coverage check cannot be defanged to a no-op")
        coverage = metrics.get("kd_coverage") or {}
        for language in required_coverage:
            bucket = coverage.get(language)
            if not bucket:
                fail(f"no KD coverage recorded for preservation language "
                     f"{language!r}")
                continue
            if int(bucket.get("rows", 0)) <= 0:
                fail(f"preservation language {language!r} contributed 0 KD rows")
            if int(bucket.get("frames", 0)) <= 0:
                fail(f"preservation language {language!r} contributed 0 KD "
                     "frames")
        if dual_kd:
            # Arm-2b: every required retention language must have contributed
            # real rows AND frames to the anchor term; an empty requirement
            # list is a defanged check and fails (same F4 defense-in-depth as
            # required_preservation_coverage)
            required_retention = verifier_spec.get(
                "required_retention_coverage") or []
            if not required_retention:
                fail("result_verifier.required_retention_coverage is empty "
                     "for a dual-KD run — the retention-coverage check "
                     "cannot be defanged to a no-op")
            retention_cov = metrics.get("kd_retention_coverage") or {}
            for language in required_retention:
                bucket = retention_cov.get(language)
                if not bucket:
                    fail(f"no retention-KD coverage recorded for retention "
                         f"language {language!r}")
                    continue
                if int(bucket.get("rows", 0)) <= 0:
                    fail(f"retention language {language!r} contributed 0 "
                         "retention-KD rows")
                if int(bucket.get("frames", 0)) <= 0:
                    fail(f"retention language {language!r} contributed 0 "
                         "retention-KD frames")

    # (4) peak GPU memory recorded and within the reviewed envelope
    ceiling = int(verifier_spec["gpu_memory_ceiling_bytes"])
    peak = metrics.get("peak_gpu_bytes")
    if not isinstance(peak, int) or isinstance(peak, bool) or peak <= 0:
        fail(f"peak_gpu_bytes not recorded as a positive int ({peak!r})")
    elif peak > ceiling:
        fail(f"peak GPU memory {peak} bytes exceeds the reviewed ceiling "
             f"{ceiling} bytes")

    # (5) throughput recorded — BOTH steps/min and samples/s (Codex #20 F5)
    throughput = metrics.get("throughput") or {}
    for key in ("steps_per_min", "samples_per_sec"):
        if not _finite_number(throughput.get(key)) \
                or float(throughput.get(key, 0)) <= 0:
            fail(f"throughput.{key} not recorded as > 0 "
                 f"({throughput.get(key)!r})")

    # (6) the merged export loads and serves (readyz) with no adapter residue
    serve = metrics.get("serve")
    if not isinstance(serve, dict):
        fail("serve (readyz) block is absent — the calibration wrapper must "
             "export, load and readyz the model before the verifier passes")
    else:
        if serve.get("readyz") is not True:
            fail(f"serve.readyz is not true ({serve.get('readyz')!r})")
        if serve.get("adapter_residue") is not False:
            fail(f"serve.adapter_residue is not false "
                 f"({serve.get('adapter_residue')!r})")
        if serve.get("weights_finite") is not True:
            fail(f"serve.weights_finite is not true "
                 f"({serve.get('weights_finite')!r})")

    # (7) dev-sentinel WER recorded (directional, NOT a promotion signal) for
    # every declared sentinel language
    dev_wer = metrics.get("dev_sentinel_wer")
    if not isinstance(dev_wer, dict):
        fail("dev_sentinel_wer block is absent — a directional read on the "
             "frozen sentinels must be recorded")
    else:
        for language in verifier_spec["dev_sentinel_languages"]:
            if not _finite_number(dev_wer.get(language)):
                fail(f"dev_sentinel_wer[{language!r}] is not a finite number "
                     f"({dev_wer.get(language)!r})")

    # (7c) MANDATORY upstream decode parity (Codex #25 finding 2): the run
    # itself must have proven our scorer path decodes identically to the
    # pinned upstream ASRInferencePipeline on the BASE model, at least one
    # real fetched row per dev-sentinel language — fail, never skip.
    parity = metrics.get("parity")
    if not isinstance(parity, dict):
        fail("parity block is absent — the run did not prove upstream decode "
             "parity (mandatory, not skippable)")
    else:
        if parity.get("upstream_equal") is not True:
            fail(f"parity.upstream_equal is {parity.get('upstream_equal')!r} "
                 "— the scorer does not match the pinned upstream decoder")
        # Codex #26 finding 4: EXACT identities, not a substring
        if str(parity.get("upstream")) != CANONICAL_UPSTREAM_PIPELINE:
            fail(f"parity.upstream {parity.get('upstream')!r} != the pinned "
                 f"{CANONICAL_UPSTREAM_PIPELINE!r}")
        if str(parity.get("scorer")) != CANONICAL_SCORER:
            fail(f"parity.scorer {parity.get('scorer')!r} != the canonical "
                 "scorer")
        rows_checked = parity.get("rows_checked") or {}
        parity_rows = parity.get("rows") or {}
        # the per-language rows the WER was scored on
        results = metrics.get("dev_sentinel_results") or {}
        for language in verifier_spec["dev_sentinel_languages"]:
            if int(rows_checked.get(language) or 0) < 1:
                fail(f"parity checked no rows for {language!r}")
            receipts = parity_rows.get(language) or []
            if len(receipts) != int(rows_checked.get(language) or 0):
                fail(f"parity.rows[{language!r}] count != rows_checked")
            scored = {str(r.get("audio_checksum_sha256"))
                      for r in (results.get(language) or {}).get("rows", [])}
            for receipt in receipts:
                checksum = str(receipt.get("audio_checksum_sha256"))
                ours_hash = str(receipt.get("ours_hyp_sha256") or "")
                up_hash = str(receipt.get("upstream_hyp_sha256") or "")
                ours_text = receipt.get("ours_hyp")
                up_text = receipt.get("upstream_hyp")
                # Codex #28 finding 4: the hashes must be the REAL sha256 of the
                # RECORDED hypotheses, and the two hypotheses must MATCH — so a
                # pair of fabricated (e.g. all-zero) hashes cannot stand in for
                # a real agreement.
                if not isinstance(ours_text, str) or not isinstance(up_text, str):
                    fail(f"parity row {checksum[:12]} ({language}) lacks the "
                         "recorded hypotheses (ours_hyp/upstream_hyp)")
                elif hashlib.sha256(ours_text.encode()).hexdigest() != ours_hash \
                        or hashlib.sha256(up_text.encode()).hexdigest() != up_hash:
                    fail(f"parity row {checksum[:12]} ({language}): a hypothesis "
                         "hash is not sha256 of the recorded hypothesis — "
                         "fabricated hash")
                elif ours_text != up_text:
                    fail(f"parity row {checksum[:12]} ({language}): our "
                         "hypothesis != the upstream hypothesis — the decoders "
                         "did not actually agree")
                # the parity-proven rows must be a subset of the SCORED rows —
                # parity on rows the WER never touched proves nothing
                if scored and checksum not in scored:
                    fail(f"parity row {checksum[:12]} ({language}) is not among "
                         "the scored dev rows")

    # (8) IDENTITY BINDING (Codex review #20 F5, hardened after the adversarial
    # pass found presence-only checks let a FABRICATED file through). Scope,
    # stated honestly: the metrics file is SELF-REPORTED by the job — its
    # AUTHENTICITY (that a real training/export actually produced these bytes)
    # rests on fetching it from the job's KMS-encrypted S3 OUTPUT path plus the
    # AWS training-job receipt, NOT on any field inside the file. What the
    # verifier enforces here is (a) the format of every sha, (b) equality of the
    # DERIVABLE identities — the packet canonical sha, the verifier's own bytes,
    # the declared job name (medzen-b5-<job_id>), the canonical scorer — and (c)
    # coverage of the dev-manifest shas. A fabricated file that names a wrong
    # job, a made-up scorer, a malformed sha, a different packet or a different
    # verifier now fails; establishing that the file is the one the job wrote is
    # the reviewer's S3-provenance/receipt step, documented in the packet.
    identity = metrics.get("identity")
    if not isinstance(identity, dict):
        fail("identity block is absent — the metrics are not bound to a run, "
             "export, scorer, packet or verifier")
    else:
        for field in IDENTITY_FIELDS:
            value = identity.get(field)
            if value in (None, "", {}, []):
                fail(f"identity.{field} is absent — evidence is not bound")
        # every sha field must be a well-formed sha256 (not 'deadbeef')
        for field in IDENTITY_SHA_FIELDS:
            value = str(identity.get(field) or "")
            if value and not _HEX64.fullmatch(value):
                fail(f"identity.{field}={value!r} is not a 64-hex sha256")
        # dev_manifest_shas must EQUAL the packet's predeclared slice shas
        # (Codex review #21 F4: 64-hex-shaped "plausible hashes" passed; now
        # only the exact reviewed slices do)
        dev_shas = identity.get("dev_manifest_shas") or {}
        dev_decl = verifier_spec.get("dev_manifests") or {}
        for language in verifier_spec["dev_sentinel_languages"]:
            sha = str(dev_shas.get(language) or "").strip()
            declared = str((dev_decl.get(language) or {}).get("sha256") or "")
            if not sha:
                fail(f"identity.dev_manifest_shas[{language!r}] absent — the "
                     "dev slice that produced the WER is not bound")
            elif not _HEX64.fullmatch(sha):
                fail(f"identity.dev_manifest_shas[{language!r}]={sha!r} is not "
                     "a 64-hex sha256")
            elif sha != declared:
                fail(f"identity.dev_manifest_shas[{language!r}] != the "
                     "PREDECLARED slice sha in result_verifier.dev_manifests — "
                     "the WER was not scored on the reviewed frozen slice")
        # the scorer must be the CANONICAL one (a made-up scorer is rejected)
        if str(identity.get("scorer")) != CANONICAL_SCORER:
            fail(f"identity.scorer {identity.get('scorer')!r} != the canonical "
                 f"scorer {CANONICAL_SCORER!r}")
        # the training job name must be the one DERIVED from the packet's job_id
        if expected_job_name is not None \
                and str(identity.get("training_job_name")) != expected_job_name:
            fail(f"identity.training_job_name "
                 f"{identity.get('training_job_name')!r} != the name derived "
                 f"from the packet {expected_job_name!r} — the metrics do not "
                 "name the declared job")
        # the metrics must be bound to THIS packet and THIS verifier
        if packet_canonical_sha is not None \
                and str(identity.get("packet_sha256")) != packet_canonical_sha:
            fail(f"identity.packet_sha256 {identity.get('packet_sha256')!r} != "
                 f"the reviewed packet's canonical sha {packet_canonical_sha!r}"
                 " — the metrics were produced under a different packet")
        if verifier_script_sha is not None \
                and str(identity.get("verifier_script_sha256")) != verifier_script_sha:
            fail("identity.verifier_script_sha256 does not match this "
                 "verifier's own bytes — the run used a different verifier")
        # Codex #22 blocker 2: the run must have executed under the EXACT
        # committed execution contract the launch packet binds
        if expected_contract_sha is not None \
                and str(identity.get("execution_contract_sha256")) \
                != expected_contract_sha:
            fail(f"identity.execution_contract_sha256 "
                 f"{identity.get('execution_contract_sha256')!r} != the launch "
                 f"packet's bound contract sha {expected_contract_sha!r} — the "
                 "run executed a different contract")
        # Codex review #20 F5 follow-up (the adversary's residual): bind the
        # metrics' self-reported export shas to the ACTUAL authenticated export
        # artifact the reviewer fetched from the job's KMS-encrypted S3 output.
        # This turns export_manifest_sha256/export_model_sha256 from format-only
        # into a real cross-check, so a competent fabrication that never
        # exported (arbitrary-but-64-hex export shas) is rejected.
        if authenticated_export is not None:
            if str(identity.get("export_manifest_sha256")) \
                    != str(authenticated_export.get("manifest_sha256")):
                fail("identity.export_manifest_sha256 != the sha of the "
                     "authenticated export manifest.json — the metrics do not "
                     "match the real export")
            if str(identity.get("export_model_sha256")) \
                    != str(authenticated_export.get("model_sha256")):
                fail("identity.export_model_sha256 != the model sha the "
                     "authenticated export manifest declares — the metrics do "
                     "not match the real export")

    return failures


def verify_training_receipt(receipt: dict[str, Any], packet: dict[str, Any],
                            *, expected_job_name: str,
                            packet_canonical_sha: str) -> list[str]:
    """Machine-check the SageMaker DescribeTrainingJob receipt against the
    COMPLETE canonical request the launcher renders from the packet (Codex
    review #23 critical: hand-picked field checks missed ContainerEntrypoint,
    TrainingInputMode, CheckpointConfig.LocalPath, VolumeKmsKeyId and Tags —
    a swapped entrypoint could run arbitrary code with the checked image/args/
    environment unchanged). The single source of truth is
    b5_sagemaker_job.render_request(packet): every field of the rendered
    request that DescribeTrainingJob echoes must match exactly; the job's
    Environment must equal the rendered one EXACTLY (no extra keys); a
    VolumeKmsKeyId the request never set must not appear; no input channels;
    and the job Tags (supplied via ListTags in live mode) must equal the
    rendered Tags. Returns failure strings; empty means the receipt matches."""
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)

    # render the canonical request from the packet — the launcher's own code
    # path, so the comparison can never drift from what launch actually sends
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from b5_sagemaker_job import JobRefusal, render_request
    try:
        expected = render_request(packet)
    except JobRefusal as exc:
        return [f"the packet does not render a canonical request ({exc}) — "
                "nothing to verify the receipt against"]

    if str(receipt.get("TrainingJobName")) != expected["TrainingJobName"]:
        fail(f"receipt.TrainingJobName {receipt.get('TrainingJobName')!r} != "
             f"the rendered {expected['TrainingJobName']!r}")
    if expected["TrainingJobName"] != expected_job_name:
        fail("internal: rendered job name != derived expected_job_name")
    if str(receipt.get("TrainingJobStatus")) != "Completed":
        fail(f"receipt.TrainingJobStatus {receipt.get('TrainingJobStatus')!r} "
             "is not Completed — no silent success")

    def compare(exp: Any, act: Any, path: str) -> None:
        """Every key/value of the RENDERED request must appear identically in
        the receipt (AWS may add extra informational keys; it must never
        change or drop a requested one)."""
        if isinstance(exp, dict):
            if not isinstance(act, dict):
                fail(f"receipt {path} is {type(act).__name__}, expected object")
                return
            for key, value in exp.items():
                compare(value, act.get(key), f"{path}.{key}")
        elif isinstance(exp, list):
            if path.endswith(("SecurityGroupIds", "Subnets")):
                if sorted(map(str, exp)) != sorted(map(str, act or [])):
                    fail(f"receipt {path} {act!r} != rendered {exp!r}")
            elif list(exp) != list(act or []):
                fail(f"receipt {path} {act!r} != rendered {exp!r}")
        else:
            if act != exp:
                fail(f"receipt {path} {act!r} != rendered {exp!r}")

    for key in ("RoleArn", "AlgorithmSpecification", "OutputDataConfig",
                "CheckpointConfig", "ResourceConfig", "VpcConfig",
                "StoppingCondition", "EnableManagedSpotTraining",
                "EnableNetworkIsolation",
                "EnableInterContainerTrafficEncryption", "ProfilerConfig",
                "RemoteDebugConfig"):
        compare(expected.get(key), receipt.get(key), key)

    # Codex review #24 finding 1: the forward compare is FAIL-OPEN for
    # anything the rendered request never sets — an injected RemoteDebugConfig
    # (container shell access), HyperParameters, RetryStrategy, debugger/
    # profiler/experiment configs or warm-pool settings all passed. CLOSED
    # ENUMERATION: every CreateTrainingJob field the Describe response echoes
    # that the render does not set must be ABSENT or provably inert.
    def _inert(value: Any) -> bool:
        return value in (None, {}, [], "", False, 0)

    unrendered_top = {
        "HyperParameters": _inert,               # config travels ONLY via env
        "DebugHookConfig": _inert,
        "DebugRuleConfigurations": _inert,
        "TensorBoardOutputConfig": _inert,
        "ExperimentConfig": _inert,
        "ProfilerRuleConfigurations": _inert,
        # Codex #25: MaximumRetryAttempts=1 is NOT inert (it changes
        # execution behavior) — only absent/empty is acceptable
        "RetryStrategy": _inert,
        "InfraCheckConfig": lambda v: _inert(v) or (
            isinstance(v, dict) and v.get("EnableInfraCheck") in (None, False)),
        # Codex #25: the current API's remaining shared fields
        "MlflowConfig": _inert,
        "ModelPackageConfig": _inert,
        "ServerlessJobConfig": _inert,
        # NOTE: SessionChainingConfig is CREATE-ONLY — DescribeTrainingJob
        # never echoes it, so a receipt check would be theater. It is
        # governed by verify_creation_request_parameters (the CloudTrail
        # creation record) + the exclusive workflow/IAM boundary.
    }
    for key, is_ok in unrendered_top.items():
        value = receipt.get(key)
        if not is_ok(value):
            fail(f"receipt carries {key}={value!r} which the rendered request "
                 "never set — the job was not created by this request")
    # nested extras the subset compare cannot see: the effective config dicts
    # must contain NO keys beyond the rendered ones (plus AWS-inert extras)
    algo_extra_ok = {"MetricDefinitions": _inert,
                     "EnableSageMakerMetricsTimeSeries": _inert,
                     "TrainingImageConfig": _inert,
                     "AlgorithmName": _inert}
    algo_actual = dict(receipt.get("AlgorithmSpecification") or {})
    for key in set(algo_actual) - set(expected["AlgorithmSpecification"]):
        if key not in algo_extra_ok or not algo_extra_ok[key](algo_actual[key]):
            fail(f"receipt AlgorithmSpecification.{key}="
                 f"{algo_actual[key]!r} was never rendered — refusing an "
                 "altered algorithm specification")
    resource_extra_ok = {"VolumeKmsKeyId": lambda v: True,  # ruled below
                         "KeepAlivePeriodInSeconds": _inert,  # no warm pools
                         "TrainingPlanArn": _inert,
                         "InstanceGroups": _inert}
    resource_actual = dict(receipt.get("ResourceConfig") or {})
    for key in set(resource_actual) - set(expected["ResourceConfig"]):
        if key not in resource_extra_ok                 or not resource_extra_ok[key](resource_actual[key]):
            fail(f"receipt ResourceConfig.{key}={resource_actual[key]!r} was "
                 "never rendered — refusing an altered resource config")

    # Environment must be EXACT (a subset compare would allow smuggled keys)
    if dict(receipt.get("Environment") or {}) != dict(expected["Environment"]):
        drift = sorted(set(expected["Environment"].items())
                       ^ set((receipt.get("Environment") or {}).items()))
        fail(f"receipt Environment differs from the packet's rendered "
             f"environment ({len(drift)} drifted entries, e.g. {drift[:3]})")
    if expected["Environment"].get("MEDZEN_CALIBRATION_PACKET_SHA256")             != packet_canonical_sha:
        fail("internal: rendered packet sha != this packet's canonical sha")
    # a VolumeKmsKeyId the request never set means the job was not created by
    # this request (NVMe instance types take no volume key)
    if "VolumeKmsKeyId" not in expected["ResourceConfig"] and             str((receipt.get("ResourceConfig") or {}).get("VolumeKmsKeyId")
                or "").strip():
        fail("receipt carries a VolumeKmsKeyId the rendered request never set")
    if receipt.get("InputDataConfig"):
        fail("receipt has InputDataConfig channels — the calibration job "
             "takes NO input channels (its data path is the governed mix + "
             "the baked dev slices); a channel would be ungoverned data")
    # Tags are not echoed by DescribeTrainingJob — live mode injects them from
    # ListTags; their absence is a FAILURE, not a skip
    receipt_tags = receipt.get("Tags")
    if not isinstance(receipt_tags, list):
        fail("receipt.Tags absent — live mode injects ListTags; a receipt "
             "without tags cannot prove the cost/tier labels")
    else:
        expected_tags = {t["Key"]: t["Value"] for t in expected.get("Tags", [])}
        actual_tags = {str(t.get("Key")): str(t.get("Value"))
                       for t in receipt_tags}
        if actual_tags != expected_tags:
            fail(f"receipt Tags {actual_tags!r} != rendered {expected_tags!r}")
    return failures


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_dev_row_receipts(metrics: dict[str, Any],
                            verifier_spec: dict[str, Any],
                            *, read_manifest) -> list[str]:
    """Codex review #22: a scalar WER cannot be recomputed. The wrapper now
    writes per-row receipts (audio checksum, normalized hypothesis, edit
    distance, ref word count); this RECOMPUTES everything against the
    COMMITTED dev manifests: row coverage must equal the manifest exactly,
    each row's edit distance must reproduce from (manifest reference,
    receipt hypothesis), and the corpus WER must equal the reported scalar.
    ``read_manifest(rel_path) -> bytes`` supplies the committed slice (repo
    checkout for the reviewer, /opt/medzen in-image)."""
    from pipeline.normalizers import for_language
    from pipeline.omniasr_calibrate import word_edits

    failures: list[str] = []
    results = metrics.get("dev_sentinel_results")
    if not isinstance(results, dict):
        return ["dev_sentinel_results block is absent — per-row receipts are "
                "required so the WER is recomputable (Codex review #22)"]
    dev_decl = verifier_spec.get("dev_manifests") or {}
    reported = metrics.get("dev_sentinel_wer") or {}
    for language in sorted(str(x).strip().lower()
                           for x in verifier_spec["dev_sentinel_languages"]):
        decl = dev_decl.get(language) or {}
        block = results.get(language)
        if not isinstance(block, dict) or not isinstance(block.get("rows"), list):
            failures.append(
                f"dev_sentinel_results[{language!r}] has no rows list")
            continue
        rows = block["rows"]
        try:
            manifest_body = read_manifest(str(decl.get("path")))
        except Exception as exc:  # noqa: BLE001
            failures.append(
                f"cannot read committed dev manifest for {language!r}: {exc}")
            continue
        if hashlib.sha256(manifest_body).hexdigest() != str(decl.get("sha256")):
            failures.append(
                f"committed dev manifest for {language!r} does not hash to the "
                "predeclared sha — wrong slice on disk")
            continue
        manifest_rows = {
            row["audio_checksum_sha256"]: row
            for row in (json.loads(line)
                        for line in manifest_body.decode().splitlines()
                        if line.strip())}
        receipt_checksums = [str(r.get("audio_checksum_sha256")) for r in rows]
        if sorted(receipt_checksums) != sorted(manifest_rows):
            failures.append(
                f"dev_sentinel_results[{language!r}] rows do not cover the "
                "manifest exactly (missing/extra/duplicated audio checksums)")
            continue
        norm = for_language(language)
        total_edits = 0
        total_ref_words = 0
        for receipt_row in rows:
            checksum = str(receipt_row.get("audio_checksum_sha256"))
            reference = norm(manifest_rows[checksum]["text_normalized"])
            hypothesis = str(receipt_row.get("hyp_normalized") or "")
            edits, ref_words = word_edits(reference, hypothesis)
            if int(receipt_row.get("edit_distance", -1)) != edits or \
                    int(receipt_row.get("ref_words", -1)) != ref_words:
                failures.append(
                    f"dev row {checksum[:12]} ({language}): receipt "
                    f"edits/ref_words ({receipt_row.get('edit_distance')}/"
                    f"{receipt_row.get('ref_words')}) do not reproduce from "
                    f"the committed reference ({edits}/{ref_words})")
                continue
            total_edits += edits
            total_ref_words += ref_words
        if total_ref_words > 0:
            recomputed = round(total_edits / total_ref_words, 4)
            scalar = reported.get(language)
            if not _finite_number(scalar) or \
                    not math.isclose(float(scalar), recomputed,
                                     rel_tol=1e-6, abs_tol=5e-5):
                failures.append(
                    f"dev_sentinel_wer[{language!r}]={scalar!r} does not equal "
                    f"the WER recomputed from the per-row receipts "
                    f"({recomputed})")
    return failures


def _camel(key: str) -> str:
    """CloudTrail requestParameters serialize SageMaker's PascalCase members
    with a lowered first letter; normalize for comparison."""
    return key[:1].lower() + key[1:] if key else key


# Codex #26 finding 2: EVERY real MedZen CreateTrainingJob record carries
# service-added fields the render never sent (confirmed against a live event).
# The comparison is TWO-SIDED — every rendered field present+equal, and the
# ONLY tolerated extras are these empirically-observed inert defaults with
# their exact values (all False; anywhere in the tree, since SageMaker injects
# them at the level their field belongs). `trainingJobArn` is special: an extra
# by that name must equal the job's real ARN, bound from the receipt.
SERVICE_ADDED_INERT_FALSE = frozenset({
    "disableEFA", "withWarmPoolValidationError",
    "enableSageMakerMetricsTimeSeries", "removeJobNameFromS3OutputPath",
    "disableModelUpload", "useReservedCapacity",
})


def verify_creation_request_parameters(
        params: dict[str, Any], expected_request: dict[str, Any],
        *, expected_job_arn: str | None = None) -> list[str]:
    """Codex #26 findings 1-2: TWO-SIDED comparison of the CloudTrail
    CreateTrainingJob requestParameters against the launcher's rendered request.
    Every RENDERED field must be PRESENT and EQUAL (a record with only a correct
    job name no longer passes), and every EXTRA field must be an
    empirically-observed inert SageMaker default with its exact value (a
    genuine record's service defaults no longer refuse). Create-only fields the
    render never sent (e.g. sessionChainingConfig with tag chaining on) are
    caught here at create time."""
    failures: list[str] = []

    def extra_ok(key: str, value: Any) -> bool:
        camel = _camel(key)
        if camel in SERVICE_ADDED_INERT_FALSE:
            return value is False
        if camel == "trainingJobArn":
            return expected_job_arn is not None and value == expected_job_arn
        return False

    def compare(exp: Any, act: Any, path: str) -> None:
        if isinstance(exp, dict):
            if not isinstance(act, dict):
                failures.append(f"creation record {path} is not an object")
                return
            act_by_camel = {_camel(k): (k, v) for k, v in act.items()}
            for ekey, evalue in exp.items():
                camel = _camel(ekey)
                if camel not in act_by_camel:            # RENDERED field missing
                    failures.append(
                        f"creation record {path}.{ekey} is ABSENT — the "
                        "rendered request set it (a partial record must fail)")
                else:
                    compare(evalue, act_by_camel[camel][1], f"{path}.{ekey}")
            rendered_camels = {_camel(k) for k in exp}
            for akey, avalue in act.items():             # EXTRA fields
                if _camel(akey) not in rendered_camels and \
                        not extra_ok(akey, avalue):
                    failures.append(
                        f"creation record {path}.{akey}={avalue!r} was never "
                        "rendered and is not a tolerated inert default")
        elif isinstance(exp, list):
            if not isinstance(act, list):
                failures.append(f"creation record {path} is not a list")
            elif path.lower().endswith(("securitygroupids", "subnets")):
                if sorted(map(str, exp)) != sorted(map(str, act)):
                    failures.append(
                        f"creation record {path} {act!r} != rendered {exp!r}")
            elif path.lower().endswith("tags"):
                # Codex #26 adversarial pass: AWS tags are an UNORDERED SET and
                # CloudTrail neither preserves the request's tag order nor the
                # Key/Value casing (it lowercases to key/value). Comparing
                # positionally rejected every genuine event — compare as
                # {tag key: tag value} maps, mirroring the receipt path.
                def _tag_map(items):
                    out = {}
                    for item in items:
                        if not isinstance(item, dict):
                            return None
                        keyed = {_camel(k): v for k, v in item.items()}
                        out[str(keyed.get("key"))] = keyed.get("value")
                    return out
                exp_map, act_map = _tag_map(exp), _tag_map(act)
                if exp_map is None or act_map is None or exp_map != act_map:
                    failures.append(
                        f"creation record {path} tag set {act_map!r} != "
                        f"rendered {exp_map!r}")
            elif len(exp) != len(act):
                failures.append(
                    f"creation record {path} has {len(act)} entries, "
                    f"rendered {len(exp)}")
            else:
                for index, (e, a) in enumerate(zip(exp, act)):
                    compare(e, a, f"{path}[{index}]")
        else:
            if str(act) != str(exp):
                failures.append(
                    f"creation record {path} {act!r} != rendered {exp!r}")

    if not isinstance(params, dict) or not params:
        return ["CloudTrail creation record has no requestParameters"]
    compare(expected_request, params, "requestParameters")
    return failures


def verify_creation_event(event: dict[str, Any], expected_request: dict[str, Any],
                          *, expected_job_name: str, expected_job_arn: str,
                          expected_principal_role_arn: str) -> list[str]:
    """Codex #26 finding 2: the CloudTrail ENVELOPE proves WHICH role launched
    the job, in the right account/region, successfully — the requestParameters
    alone do not. Checks event source/name, region, account, no error, the
    response + request ARN, and the session-issuer principal, then delegates the
    two-sided requestParameters comparison."""
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)

    if str(event.get("eventName")) != "CreateTrainingJob":
        fail(f"event is {event.get('eventName')!r}, not CreateTrainingJob")
    if str(event.get("eventSource")) != "sagemaker.amazonaws.com":
        fail(f"eventSource {event.get('eventSource')!r} is not sagemaker")
    if str(event.get("awsRegion")) != MEDZEN_REGION:
        fail(f"event awsRegion {event.get('awsRegion')!r} != {MEDZEN_REGION}")
    if str(event.get("recipientAccountId")) != MEDZEN_ACCOUNT:
        fail(f"event recipientAccountId {event.get('recipientAccountId')!r} "
             f"!= {MEDZEN_ACCOUNT}")
    if event.get("errorCode") or event.get("errorMessage"):
        fail(f"the creation event carries an error "
             f"({event.get('errorCode')!r}) — it did not succeed")
    response = event.get("responseElements") or {}
    if str(response.get("trainingJobArn")) != expected_job_arn:
        fail(f"event responseElements.trainingJobArn "
             f"{response.get('trainingJobArn')!r} != {expected_job_arn!r}")
    identity = event.get("userIdentity") or {}
    issuer = (identity.get("sessionContext") or {}).get("sessionIssuer") or {}
    # Codex stage-1 review (2026-08-25) finding 4c: an assumed role shows in
    # sessionContext.sessionIssuer; a plain IAM user (the DOCUMENTED below-tier
    # local route, Codex #24) has no sessionIssuer — its principal is
    # userIdentity.arn. Extract the effective principal, then require it to be
    # in the tier-scoped allowlist the caller supplies (exact ARNs only).
    principal = str(issuer.get("arn") or identity.get("arn") or "")
    allowed = (expected_principal_role_arn
               if isinstance(expected_principal_role_arn, (tuple, list, set))
               else (expected_principal_role_arn,))
    if principal not in {str(a) for a in allowed}:
        fail(f"event was launched by {principal!r}, not one of the expected "
             f"tier-scoped launch principals {sorted(map(str, allowed))!r}")
    params = event.get("requestParameters") or {}
    if str(params.get("trainingJobName")) != expected_job_name:
        fail(f"requestParameters.trainingJobName {params.get('trainingJobName')!r}"
             f" != {expected_job_name!r}")
    failures.extend(verify_creation_request_parameters(
        params, expected_request, expected_job_arn=expected_job_arn))
    return failures


def safe_extract_bundle(tar_path: Path, workdir: Path) -> dict[str, Path]:
    """Extract ONLY the allowlisted bundle members from model.tar.gz,
    refusing absolute paths, traversal, links, or missing members. Returns
    {member_name: extracted_path}."""
    import tarfile

    out: dict[str, Path] = {}
    with tarfile.open(tar_path, "r:*") as archive:
        members = archive.getmembers()
        if len(members) > ARCHIVE_MAX_MEMBERS:
            raise SystemExit(
                f"archive has {len(members)} members, over the "
                f"{ARCHIVE_MAX_MEMBERS} cap — not a calibration bundle")
        aggregate = sum(max(0, int(m.size)) for m in members)
        if aggregate > ARCHIVE_MAX_AGGREGATE_BYTES:
            raise SystemExit(
                f"archive declares {aggregate} aggregate bytes, over the "
                f"{ARCHIVE_MAX_AGGREGATE_BYTES} cap — refusing disk "
                "exhaustion")
        names = {m.name.lstrip("./"): m for m in members}
        for member_name in BUNDLE_MEMBERS:
            member = names.get(member_name)
            if member is None:
                raise SystemExit(
                    f"model.tar.gz lacks {member_name!r} — not a calibration "
                    "output bundle")
            if not member.isreg() or member.name.startswith(("/", "..")) \
                    or ".." in member.name:
                raise SystemExit(
                    f"refusing unsafe tar member {member.name!r}")
            cap = BUNDLE_MEMBER_MAX_BYTES[member_name]
            if member.size > cap:
                raise SystemExit(
                    f"tar member {member_name!r} declares {member.size} bytes, "
                    f"over the {cap}-byte cap — refusing disk exhaustion")
            dest = workdir / member_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with archive.extractfile(member) as src, dest.open("wb") as dst:
                for chunk in iter(lambda: src.read(1 << 22), b""):
                    written += len(chunk)
                    if written > cap:  # header lied about the size
                        raise SystemExit(
                            f"tar member {member_name!r} exceeded its declared "
                            "size mid-stream — refusing disk exhaustion")
                    dst.write(chunk)
            out[member_name] = dest
    return out


def verify_live_bundle(*, packet: dict[str, Any], receipt: dict[str, Any],
                       extracted: dict[str, Path], s3_meta: dict[str, Any],
                       verifier_script_sha: str,
                       repo_root: Path | None = None) -> tuple[list[str], dict[str, Any]]:
    """The AUTHORITATIVE verification core (Codex #22 blocker 1), pure over
    already-fetched inputs so it is unit-testable: the receipt came from
    DescribeTrainingJob, the extracted files from the job's OWN
    ModelArtifacts object (KMS/VersionId in s3_meta) — the verifier read
    everything itself; nothing is caller-suppliable. Returns (failures,
    report_facts)."""
    failures: list[str] = []
    spec = load_verifier_spec(packet)
    packet_sha = _canonical_sha256(packet)
    job_id = str(packet.get("job_id") or "").strip()
    expected_job_name = f"medzen-b5-{job_id}"
    expected_contract_sha = str(
        (packet.get("execution_contract") or {}).get("sha256") or "") or None

    # the fetched object must be KMS-encrypted with the packet's key
    if str(s3_meta.get("SSEKMSKeyId") or "") != str(packet.get("kms_key_arn")):
        failures.append(
            f"model.tar.gz SSEKMSKeyId {s3_meta.get('SSEKMSKeyId')!r} != the "
            f"packet's KMS key — the artifact is not the job's sealed output")
    model_uri = str(s3_meta.get("uri") or "")
    # Codex #23 high: startswith() admitted `output-evil/...`. The EXACT
    # SageMaker artifact path is <S3OutputPath>/<TrainingJobName>/output/
    # model.tar.gz — require full equality, and require an EXPLICIT VersionId
    # (S3 versioning is on; an unpinned fetch is mutable identity).
    expected_uri = (f"s3://medzen-speech/research/b5-training/{job_id}/output/"
                    f"{expected_job_name}/output/model.tar.gz")
    if model_uri != expected_uri:
        failures.append(
            f"ModelArtifacts {model_uri!r} != the exact expected artifact "
            f"path {expected_uri!r}")
    if not str(s3_meta.get("VersionId") or "").strip():
        failures.append(
            "the fetched artifact has no explicit S3 VersionId — the bucket "
            "is versioned and identity must be pinned to one version")

    metrics = json.loads(extracted["calibration-metrics.json"].read_bytes())
    manifest_bytes = extracted["export/manifest.json"].read_bytes()
    manifest = json.loads(manifest_bytes)
    actual_model_sha = _sha256_file(extracted["export/model.pt"])
    if actual_model_sha != str(manifest.get("model_sha256")):
        failures.append(
            f"model.pt hashes to {actual_model_sha[:16]}, the export manifest "
            f"declares {str(manifest.get('model_sha256'))[:16]} — torn export")
    authenticated_export = {
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "model_sha256": actual_model_sha,
    }
    failures.extend(verify_calibration(
        metrics, spec, packet_canonical_sha=packet_sha,
        verifier_script_sha=verifier_script_sha,
        expected_job_name=expected_job_name,
        expected_contract_sha=expected_contract_sha,
        authenticated_export=authenticated_export))
    failures.extend(verify_training_receipt(
        receipt, packet, expected_job_name=expected_job_name,
        packet_canonical_sha=packet_sha))
    root = repo_root or Path(__file__).resolve().parents[1]
    failures.extend(verify_dev_row_receipts(
        metrics, spec, read_manifest=lambda rel: (root / rel).read_bytes()))
    facts = {
        "model_artifacts_uri": model_uri,
        "s3_version_id": s3_meta.get("VersionId"),
        "s3_etag": s3_meta.get("ETag"),
        "s3_kms_key": s3_meta.get("SSEKMSKeyId"),
        "s3_bytes": s3_meta.get("ContentLength"),
        "metrics_sha256": _sha256_file(extracted["calibration-metrics.json"]),
        "export_manifest_sha256": authenticated_export["manifest_sha256"],
        "export_model_sha256": actual_model_sha,
    }
    return failures, facts


def select_execution_window_version(versions: list[dict[str, Any]],
                                    start, end, *, slack_seconds: int = 900):
    """Pick the ONE object version created inside the job's AWS-recorded
    execution window [TrainingStartTime, TrainingEndTime + slack] (SageMaker
    uploads the bundle at job end; slack covers upload/clock skew). Zero or
    multiple in-window versions is a hard refusal — the artifact identity
    would be ambiguous or post-hoc (Codex #23 high)."""
    import datetime as _dt

    def as_dt(value):
        if isinstance(value, _dt.datetime):
            return value
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    lo = as_dt(start)
    hi = as_dt(end) + _dt.timedelta(seconds=slack_seconds)
    in_window = [v for v in versions
                 if lo <= as_dt(v["LastModified"]) <= hi]
    if len(in_window) != 1:
        raise SystemExit(
            f"expected exactly ONE model.tar.gz version created in the job's "
            f"execution window [{lo.isoformat()} .. {hi.isoformat()}], found "
            f"{len(in_window)} of {len(versions)} total — artifact identity "
            "is ambiguous or post-hoc; refusing")
    return in_window[0]


def fetch_creation_event(session, job_name: str, creation_time,
                         *, window_seconds: int = 1800) -> dict[str, Any]:
    """Fetch the SUCCESSFUL CloudTrail CreateTrainingJob event for this job.

    Codex #26 finding 1: a real SageMaker job's CloudTrail record has an EMPTY
    Resources list, so a ResourceName lookup returns ZERO — a genuine job would
    fail verification. Query by EventName within the job's creation window
    instead, decode each event, and filter by requestParameters.trainingJobName.
    Codex #26 finding 4: among matches, keep only SUCCESSFUL events (no
    errorCode) so a failed-then-retried create cannot supply a stale record;
    exactly one successful event is required."""
    import datetime as _dt

    def as_dt(value):
        if isinstance(value, _dt.datetime):
            return value
        return _dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    center = as_dt(creation_time)
    lo = center - _dt.timedelta(seconds=window_seconds)
    hi = center + _dt.timedelta(seconds=window_seconds)
    trail = session.client("cloudtrail")
    matches: list[dict[str, Any]] = []
    token = None
    while True:
        kwargs = {
            "LookupAttributes": [{"AttributeKey": "EventName",
                                  "AttributeValue": "CreateTrainingJob"}],
            "StartTime": lo, "EndTime": hi}
        if token:
            kwargs["NextToken"] = token
        page = trail.lookup_events(**kwargs)
        for entry in page.get("Events") or []:
            detail = json.loads(entry["CloudTrailEvent"])
            params = detail.get("requestParameters") or {}
            if str(params.get("trainingJobName")) == job_name \
                    and not detail.get("errorCode"):
                matches.append(detail)
        token = page.get("NextToken")
        if not token:
            break
    if len(matches) != 1:
        raise SystemExit(
            f"expected exactly ONE SUCCESSFUL CloudTrail CreateTrainingJob "
            f"event for {job_name} in [{lo.isoformat()} .. {hi.isoformat()}], "
            f"found {len(matches)} — management-event delivery can lag ~15 min "
            "(re-run once it lands), or the job name was created more than once")
    return matches[0]


def live_fetch(packet: dict[str, Any], workdir: Path,
               session=None) -> tuple[
        dict[str, Any], dict[str, Path], dict[str, Any], dict[str, Any]]:
    """AWS side of authoritative mode: pin account+region, call
    DescribeTrainingJob ITSELF, follow ModelArtifacts.S3ModelArtifacts, fetch
    that exact object (VersionId + KMS captured from the response), and
    safe-extract the bundle. Nothing here is caller-suppliable.

    Codex round 31: an optional already-authenticated `session` may be passed
    so the launcher's ONE role-asserted session is reused (no second STS /
    second credential path); the account pin below still runs on it. When
    None (the standalone --live CLI), a fresh region-pinned session is made."""
    import boto3

    if session is None:
        session = boto3.session.Session(region_name=MEDZEN_REGION)
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != MEDZEN_ACCOUNT:
        raise SystemExit(
            f"live verification must run in account {MEDZEN_ACCOUNT}, caller "
            f"is in {identity.get('Account')!r}")
    job_id = str(packet.get("job_id") or "").strip()
    job_name = f"medzen-b5-{job_id}"
    sagemaker = session.client("sagemaker")
    receipt = session.client("sagemaker").describe_training_job(
        TrainingJobName=job_name)
    # Tags are NOT echoed by DescribeTrainingJob — inject them from ListTags
    # so verify_training_receipt can compare against the rendered request
    tags: list[dict[str, str]] = []
    next_token = None
    while True:
        kwargs = {"ResourceArn": receipt["TrainingJobArn"]}
        if next_token:
            kwargs["NextToken"] = next_token
        page = sagemaker.list_tags(**kwargs)
        tags.extend(page.get("Tags") or [])
        next_token = page.get("NextToken")
        if not next_token:
            break
    receipt["Tags"] = tags
    model_uri = str(((receipt.get("ModelArtifacts") or {})
                     .get("S3ModelArtifacts")) or "")
    if not model_uri.startswith("s3://"):
        raise SystemExit(
            f"DescribeTrainingJob returned no S3ModelArtifacts ({model_uri!r})")
    bucket, _, key = model_uri.removeprefix("s3://").partition("/")
    s3 = session.client("s3")
    # Codex #23 high: the bucket is versioned — select the ONE version created
    # in the job's execution window and fetch THAT explicit VersionId, never
    # the mutable current version
    versions = []
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket, Prefix=key):
        versions.extend(v for v in (page.get("Versions") or [])
                        if v.get("Key") == key)
    chosen = select_execution_window_version(
        versions, receipt.get("TrainingStartTime"),
        receipt.get("TrainingEndTime"))
    response = s3.get_object(Bucket=bucket, Key=key,
                             VersionId=chosen["VersionId"])
    # Codex #24 finding 2: refuse an oversized archive BEFORE streaming, and
    # count bytes WHILE streaming (a lying ContentLength cannot fill the disk)
    declared = int(response.get("ContentLength") or 0)
    if declared > ARCHIVE_MAX_BYTES:
        raise SystemExit(
            f"model.tar.gz declares {declared} bytes, over the "
            f"{ARCHIVE_MAX_BYTES} cap — refusing disk exhaustion")
    # Codex #26 finding 5: the peak footprint is the compressed archive PLUS
    # the full aggregate extraction cap PLUS the margin (extracted bytes are
    # NOT bounded by the compressed size — a well-compressed archive extracts
    # to much more), so preflight archive + aggregate-extraction + margin.
    import shutil as _shutil
    free = _shutil.disk_usage(workdir).free
    needed = required_free_bytes(declared)
    if free < needed:
        raise SystemExit(
            f"workdir has {free} bytes free; archive + extraction need "
            f"~{needed} — refusing to fill the disk")
    tar_path = workdir / "model.tar.gz"
    stream_with_cap(response["Body"], tar_path, ARCHIVE_MAX_BYTES,
                    label="model.tar.gz download")
    s3_meta = {
        "uri": model_uri,
        "VersionId": response.get("VersionId"),
        "version_selected_from_window": True,
        "version_last_modified": str(chosen.get("LastModified")),
        "ETag": response.get("ETag"),
        "SSEKMSKeyId": response.get("SSEKMSKeyId"),
        "ContentLength": response.get("ContentLength"),
    }
    extracted = safe_extract_bundle(tar_path, workdir / "bundle")
    creation_event = fetch_creation_event(
        session, job_name, receipt.get("CreationTime"))
    return receipt, extracted, s3_meta, creation_event


def load_verifier_spec(packet: dict[str, Any]) -> dict[str, Any]:
    """Load and CANONICALLY validate the result_verifier spec. Codex review
    #20 F4: the earlier launcher check accepted a spec that named a nonexistent
    script, a traversal `metrics_artifact`, `expected_steps=1` while training
    ran 30, an enormous GPU ceiling and an empty dev-language list. Both the
    launcher (validate_arm2_semantics) and this verifier now pin the canonical
    contract, so neither layer alone can be bypassed."""
    spec = packet.get("result_verifier")
    if not isinstance(spec, dict):
        raise SystemExit(
            "packet has no result_verifier block — the calibration packet "
            "MUST bind the result schema and acceptance bounds (Codex "
            "review #19 F3/F4)")
    required = ("expected_steps", "gpu_memory_ceiling_bytes",
                "required_preservation_coverage", "dev_sentinel_languages",
                "metrics_schema", "metrics_artifact", "script")
    missing = [k for k in required if k not in spec]
    if missing:
        raise SystemExit(f"result_verifier lacks {missing}")
    if spec["metrics_schema"] not in (METRICS_SCHEMA, METRICS_SCHEMA_V2):
        raise SystemExit(
            f"result_verifier.metrics_schema {spec['metrics_schema']!r} is "
            f"not one of this verifier's ({METRICS_SCHEMA!r}, "
            f"{METRICS_SCHEMA_V2!r})")
    if spec["metrics_schema"] == METRICS_SCHEMA_V2:
        # a dual-KD packet must bind the retention coverage requirement here,
        # exactly as /1 binds required_preservation_coverage (F4 discipline)
        ret_cov = spec.get("required_retention_coverage")
        if not isinstance(ret_cov, list) or not ret_cov:
            raise SystemExit(
                "result_verifier.required_retention_coverage must be a "
                "non-empty list when metrics_schema is /2 (dual-KD)")
        if spec.get("kd_enabled") is False:
            raise SystemExit(
                "result_verifier: metrics_schema /2 with kd_enabled=false is "
                "contradictory — the retention anchor is a KD term")
    if spec["script"] != CANONICAL_SCRIPT:
        raise SystemExit(
            f"result_verifier.script {spec['script']!r} must be the canonical "
            f"{CANONICAL_SCRIPT!r} — a run cannot substitute its own checker")
    if spec["metrics_artifact"] != CANONICAL_ARTIFACT:
        raise SystemExit(
            f"result_verifier.metrics_artifact {spec['metrics_artifact']!r} "
            f"must be the canonical {CANONICAL_ARTIFACT!r} (no path traversal)")
    steps = spec["expected_steps"]
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 1:
        raise SystemExit(f"result_verifier.expected_steps {steps!r} must be a "
                         "positive int")
    ceiling = spec["gpu_memory_ceiling_bytes"]
    if not isinstance(ceiling, int) or isinstance(ceiling, bool) \
            or not (0 < ceiling <= L4_PHYSICAL_BYTES):
        raise SystemExit(
            f"result_verifier.gpu_memory_ceiling_bytes {ceiling!r} must be in "
            f"1..{L4_PHYSICAL_BYTES} (the g6.xlarge L4's physical 24 GiB) — an "
            "enormous ceiling is not a real memory bound")
    # Codex review #20 F4 defense-in-depth: the standalone verifier must also
    # reject a defanged (empty/non-list) required_preservation_coverage, not
    # rely on the launcher having validated it.
    cov = spec["required_preservation_coverage"]
    if not isinstance(cov, list) or not cov:
        raise SystemExit("result_verifier.required_preservation_coverage must "
                         "be a non-empty list")
    dev_langs = spec["dev_sentinel_languages"]
    if not isinstance(dev_langs, list) or not dev_langs:
        raise SystemExit("result_verifier.dev_sentinel_languages must be a "
                         "non-empty list")
    dev_set = {str(x).strip().lower() for x in dev_langs}
    if not MANDATORY_DEV_SENTINELS.issubset(dev_set):
        raise SystemExit(
            f"result_verifier.dev_sentinel_languages must include the "
            f"regression sentinels {sorted(MANDATORY_DEV_SENTINELS)} — Arm-2 "
            "exists to protect them")
    # Codex review #21 F4: the dev evaluation data must be PREDECLARED — path,
    # sha256 and row count per dev-sentinel language — so the identity check
    # can hard-bind the wrapper's reported dev-manifest shas to the reviewed
    # declaration instead of accepting any plausible hash.
    dev_decl = spec.get("dev_manifests")
    if not isinstance(dev_decl, dict) or not dev_decl:
        raise SystemExit(
            "result_verifier.dev_manifests must predeclare each dev-sentinel "
            "slice (path, sha256, rows) — Codex review #21 F4")
    for lang in sorted(dev_set):
        decl = dev_decl.get(lang)
        if not isinstance(decl, dict):
            raise SystemExit(f"result_verifier.dev_manifests lacks {lang!r}")
        path = str(decl.get("path") or "")
        if not path or path.startswith("/") or ".." in path:
            raise SystemExit(
                f"dev_manifests[{lang!r}].path {path!r} must be repo-relative "
                "without traversal")
        if not _HEX64.fullmatch(str(decl.get("sha256") or "")):
            raise SystemExit(f"dev_manifests[{lang!r}].sha256 must be 64-hex")
        rows = decl.get("rows")
        if not isinstance(rows, int) or isinstance(rows, bool) or rows < 1:
            raise SystemExit(
                f"dev_manifests[{lang!r}].rows must be a positive int")
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True,
                        help="the Arm-2 calibration LAUNCH packet (committed)")
    parser.add_argument("--live", action="store_true",
                        help="AUTHORITATIVE mode (Codex #22 blocker 1): the "
                             "verifier itself pins account 558069890522 / "
                             "eu-central-1, calls DescribeTrainingJob, follows "
                             "ModelArtifacts.S3ModelArtifacts, fetches that "
                             "exact KMS-encrypted object (VersionId captured), "
                             "extracts model.pt + manifest + metrics and hashes "
                             "everything ITSELF. Nothing is caller-suppliable. "
                             "This is the ONLY mode whose verdict is "
                             "authoritative.")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="scratch directory for the --live download "
                             "(~2.6 GB); default: a temp dir")
    parser.add_argument("--run-verifier-sha256", default=None,
                        help="RETROSPECTIVE reverification after a REVIEWED "
                             "verifier correction (Codex stage-1 review "
                             "2026-08-25): the sha256 of scripts/"
                             "verify_arm2_calibration.py AS COMMITTED at the "
                             "run's reviewed commit (git show <run-commit>:"
                             "scripts/verify_arm2_calibration.py | sha256). "
                             "The run's recorded in-image verifier sha must "
                             "equal EXACTLY this value; without the flag it "
                             "must equal this verifier's own bytes (default, "
                             "fail-closed). The report records both shas.")
    parser.add_argument("--metrics", type=Path, default=None,
                        help="LOCAL-CROSSCHECK/SMOKE only: a local "
                             "calibration-metrics.json. Never authoritative — "
                             "local files are caller-suppliable.")
    parser.add_argument("--export-manifest", type=Path, default=None,
                        help="LOCAL-CROSSCHECK only: a local export "
                             "manifest.json. Never authoritative.")
    parser.add_argument("--export-model", type=Path, default=None,
                        help="LOCAL-CROSSCHECK only: a local model.pt (hashed "
                             "against the local manifest). Never authoritative.")
    parser.add_argument("--receipt", type=Path, default=None,
                        help="LOCAL-CROSSCHECK only: a local "
                             "describe-training-job JSON. Never authoritative.")
    parser.add_argument("--smoke", action="store_true",
                        help="metrics shape/identity check only; binds NO real "
                             "artifact. Never authoritative.")
    args = parser.parse_args(argv)

    packet = json.loads(args.packet.read_bytes())
    spec = load_verifier_spec(packet)
    packet_sha = _canonical_sha256(packet)
    verifier_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    # retrospective reverification: the run must have used the reviewed
    # verifier OF ITS OWN COMMIT (exact sha supplied by the reviewer); the
    # report still records this verifier's own bytes separately.
    own_verifier_sha = verifier_sha
    # Semantics correction (second-review bundle): the supplied run-commit sha
    # feeds ONLY the metrics-identity comparison (the run's in-image verifier
    # must equal ITS commit's reviewed verifier); the REPORT always records
    # THIS reviewing verifier's own bytes — that is what the launch gate binds
    # (a receipt must carry an attestation produced by the currently committed
    # verifier, while honestly acknowledging the run's own baked one).
    identity_expect_sha = verifier_sha
    if args.run_verifier_sha256:
        import re as _re
        if not _re.fullmatch(r"[0-9a-f]{64}", args.run_verifier_sha256):
            raise SystemExit("--run-verifier-sha256 must be 64-hex")
        identity_expect_sha = args.run_verifier_sha256
    job_id = str(packet.get("job_id") or "").strip()
    expected_job_name = f"medzen-b5-{job_id}" if job_id else None
    expected_contract_sha = str(
        (packet.get("execution_contract") or {}).get("sha256") or "") or None

    # ---- AUTHORITATIVE (--live): the verifier fetches everything itself ----
    if args.live:
        import tempfile
        workdir = args.workdir or Path(tempfile.mkdtemp(prefix="arm2-verify-"))
        workdir.mkdir(parents=True, exist_ok=True)
        receipt, extracted, s3_meta, creation_event = live_fetch(
            packet, workdir)
        failures, facts = verify_live_bundle(
            packet=packet, receipt=receipt, extracted=extracted,
            s3_meta=s3_meta, verifier_script_sha=identity_expect_sha)
        # Codex #26 findings 1-2: the CloudTrail creation event proves which
        # role created the job, in the right account/region, successfully, with
        # a two-sided requestParameters match (create-only smuggling caught).
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from b5_sagemaker_job import (ON_DEMAND_USD_PER_HOUR,
                                      is_campaign_arm_job,
                                      render_request as _render)
        # Codex stage-1 review (2026-08-25) finding 4c: scope the principal
        # expectation by TIER. Campaign/arm jobs must be created by the
        # protected arm-launch role. Below-tier calibration-shaped jobs may be
        # created by the calibration workflow role OR the documented local
        # route under the OWNER's exact IAM user (Codex #24 boundary) — never
        # an arbitrary principal.
        _wc = (float(packet.get("max_runtime_seconds")) / 3600.0
               * ON_DEMAND_USD_PER_HOUR[str(packet.get("instance_type"))])
        _above = is_campaign_arm_job(packet.get("environment") or {}, _wc)
        # Codex second review (2026-08-25) finding 2: comparative campaign
        # jobs launch as the stage-1 role; legacy arm jobs as the arm role.
        from b5_sagemaker_job import expected_arm_launch_role
        _expected = (
            (f"arn:aws:iam::{MEDZEN_ACCOUNT}:role/"
             + expected_arm_launch_role(packet.get("environment") or {}),)
            if _above else
            (CALIBRATION_LAUNCH_ROLE_ARN, OWNER_LOCAL_PRINCIPAL_ARN))
        failures.extend(verify_creation_event(
            creation_event, _render(packet),
            expected_job_name=expected_job_name or "",
            expected_job_arn=str(receipt.get("TrainingJobArn") or ""),
            expected_principal_role_arn=_expected))
        facts["creation_event_verified"] = True
        facts["launch_principal"] = (
            (creation_event.get("userIdentity") or {}).get("sessionContext")
            or {}).get("sessionIssuer", {}).get("arn")
        report = {
            "verdict": "PASS" if not failures else "FAIL",
            "mode": "live",
            "authoritative": True,
            "packet": str(args.packet),
            "packet_canonical_sha256": packet_sha,
            "verifier_script_sha256": verifier_sha,
            **facts,
            "failures": failures,
        }
        if args.run_verifier_sha256:
            report["reverification"] = {
                "run_verifier_sha256": args.run_verifier_sha256,
                "reviewing_verifier_own_sha256": own_verifier_sha,
                "note": ("retrospective reverification after a reviewed "
                         "verifier correction: the run's recorded in-image "
                         "verifier sha was required to equal the sha of this "
                         "script AS COMMITTED at the run's reviewed commit "
                         "(supplied by the reviewer), not this reviewing "
                         "verifier's own newer bytes"),
            }
        print(json.dumps(report, indent=1, sort_keys=True, default=str))
        return 0 if not failures else 1

    # ---- non-authoritative local modes (Codex #22 blocker 1: local files
    # are caller-suppliable, so these verdicts are NEVER authoritative) ----
    if args.metrics is None:
        raise SystemExit(
            "authoritative verification is --live (the verifier fetches "
            "everything itself); for a local check pass --metrics with "
            "--smoke, or --metrics + --export-manifest/--export-model/"
            "--receipt for a local cross-check")
    metrics = json.loads(args.metrics.read_bytes())
    local_inputs = (args.export_manifest, args.export_model, args.receipt)
    if any(x is None for x in local_inputs) and not args.smoke:
        raise SystemExit(
            "a local cross-check needs --export-manifest, --export-model AND "
            "--receipt; pass --smoke for a shape/identity check only. NEITHER "
            "is authoritative — use --live for the authoritative verdict")

    authenticated_export = None
    receipt_failures: list[str] = []
    if args.export_manifest is not None:
        manifest_bytes = args.export_manifest.read_bytes()
        manifest = json.loads(manifest_bytes)
        authenticated_export = {
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "model_sha256": manifest.get("model_sha256"),
        }
        if args.export_model is not None:
            actual_model_sha = _sha256_file(args.export_model)
            if actual_model_sha != str(manifest.get("model_sha256")):
                receipt_failures.append(
                    f"model.pt hashes to {actual_model_sha[:16]}, the export "
                    f"manifest declares "
                    f"{str(manifest.get('model_sha256'))[:16]} — the local "
                    "model is not the manifest's model")
            authenticated_export["model_sha256"] = actual_model_sha
    if args.receipt is not None:
        receipt = json.loads(args.receipt.read_bytes())
        if "TrainingJob" in receipt and "TrainingJobName" not in receipt:
            receipt = receipt["TrainingJob"]
        receipt_failures.extend(verify_training_receipt(
            receipt, packet, expected_job_name=expected_job_name or "",
            packet_canonical_sha=packet_sha))

    failures = verify_calibration(
        metrics, spec, packet_canonical_sha=packet_sha,
        verifier_script_sha=verifier_sha, expected_job_name=expected_job_name,
        expected_contract_sha=expected_contract_sha,
        authenticated_export=authenticated_export)
    failures.extend(receipt_failures)
    if not args.smoke:
        repo_root = Path(__file__).resolve().parents[1]
        failures.extend(verify_dev_row_receipts(
            metrics, spec,
            read_manifest=lambda rel: (repo_root / rel).read_bytes()))
    report = {
        "verdict": "PASS" if not failures else "FAIL",
        "mode": "smoke" if args.smoke else "local-crosscheck",
        # Codex #22 blocker 1: local files are caller-suppliable — a local
        # verdict is NEVER authoritative, only --live is
        "authoritative": False,
        "metrics": str(args.metrics),
        "packet": str(args.packet),
        "packet_canonical_sha256": packet_sha,
        "verifier_script_sha256": verifier_sha,
        "metrics_sha256": hashlib.sha256(
            args.metrics.read_bytes()).hexdigest(),
        "export_manifest_sha256": (authenticated_export or {}).get(
            "manifest_sha256"),
        "export_model_sha256": (authenticated_export or {}).get("model_sha256"),
        "failures": failures,
    }
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
