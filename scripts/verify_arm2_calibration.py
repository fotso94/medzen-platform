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
# the ONLY members live mode extracts from model.tar.gz (path-traversal-safe)
BUNDLE_MEMBERS = ("calibration-metrics.json", "export/manifest.json",
                  "export/model.pt")
# identity fields that must be a 64-hex sha256 (Codex review #20 F5 follow-up:
# presence-only let a fabricated file through — enforce the FORMAT of every
# sha and the value of the derivable ones)
IDENTITY_SHA_FIELDS = ("run_fingerprint", "export_manifest_sha256",
                       "export_model_sha256", "packet_sha256",
                       "execution_contract_sha256", "verifier_script_sha256")
# the canonical scorer string the calibration wrapper stamps; kept in lock-step
# with pipeline.omniasr_calibrate.SCORER_ID (a host test asserts equality).
CANONICAL_SCORER = ("ctc-greedy/argmax+collapse+blank-strip; "
                    "normalizer=pipeline.normalizers.for_language; "
                    "metric=corpus-word-error-rate/1")
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
    if schema != METRICS_SCHEMA:
        fail(f"metrics schema {schema!r} != expected {METRICS_SCHEMA!r}")
        return failures  # nothing else is trustworthy under a wrong schema

    # (1) completed the declared step budget — no silent short/over run
    expected_steps = int(verifier_spec["expected_steps"])
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
        if _finite_number(record.get("kd")) and float(record["kd"]) <= 0.0:
            fail(f"step {record.get('step')}: KD={record['kd']} is not > 0 "
                 "(the distillation term is not live on preservation batches)")
        if all(_finite_number(record.get(k)) for k in ("ctc", "kd", "total",
                                                       "alpha")):
            expected_total = float(record["ctc"]) + float(record["alpha"]) \
                * float(record["kd"])
            if not math.isclose(float(record["total"]), expected_total,
                                rel_tol=1e-4, abs_tol=1e-5):
                fail(f"step {record.get('step')}: total={record['total']} != "
                     f"ctc + alpha*kd ({expected_total}) — loss equation "
                     "violated")
    # step_sequence must corroborate per_step exactly
    if metrics.get("step_sequence") != [r.get("step") for r in per_step]:
        fail("step_sequence does not match per_step ordering")
    positive = int(metrics.get("kd_positive_finite_steps", 0))
    if positive != expected_steps:
        fail(f"kd_positive_finite_steps={positive} != {expected_steps} — KD "
             "was not positive-and-finite on every step")

    # (3) per-language KD coverage: every required preservation language must
    # have contributed real rows AND valid frames. Codex review #20 F4
    # defense-in-depth: an EMPTY required_preservation_coverage must FAIL here
    # (not silently skip), so the standalone verifier cannot be defanged even
    # if it were run against a spec the launcher never validated.
    required_coverage = verifier_spec.get("required_preservation_coverage") or []
    if not required_coverage:
        fail("result_verifier.required_preservation_coverage is empty — the "
             "KD-coverage check cannot be defanged to a no-op")
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
            fail(f"preservation language {language!r} contributed 0 KD frames")

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
    reviewed packet (Codex review #21 F3: terminal status, image digest,
    environment, KMS, output location and instance were previously unchecked).
    Returns failure strings; empty means the receipt matches the packet."""
    failures: list[str] = []

    def fail(msg: str) -> None:
        failures.append(msg)

    if str(receipt.get("TrainingJobName")) != expected_job_name:
        fail(f"receipt.TrainingJobName {receipt.get('TrainingJobName')!r} != "
             f"the derived {expected_job_name!r}")
    if str(receipt.get("TrainingJobStatus")) != "Completed":
        fail(f"receipt.TrainingJobStatus {receipt.get('TrainingJobStatus')!r} "
             "is not Completed — no silent success")
    algo = receipt.get("AlgorithmSpecification") or {}
    if str(algo.get("TrainingImage")) != str(packet.get("image_uri_with_digest")):
        fail(f"receipt image {algo.get('TrainingImage')!r} != the packet's "
             f"pinned digest {packet.get('image_uri_with_digest')!r}")
    if list(algo.get("ContainerArguments") or []) != \
            ["-m", "pipeline.omniasr_calibrate"]:
        fail(f"receipt ContainerArguments {algo.get('ContainerArguments')!r} "
             "!= the calibration entrypoint ['-m', 'pipeline.omniasr_calibrate']")
    # the job's ACTUAL environment must equal the packet's, plus the FOUR
    # launcher-injected identity keys — an env drifted from the packet means
    # the job that ran is not the reviewed calibration
    expected_env = dict(packet.get("environment") or {})
    expected_env["MEDZEN_CALIBRATION_PACKET_SHA256"] = packet_canonical_sha
    expected_env["MEDZEN_TRAINING_JOB_NAME"] = expected_job_name
    contract_decl = packet.get("execution_contract") or {}
    if str(contract_decl.get("path") or "").strip():
        expected_env["MEDZEN_EXECUTION_CONTRACT"] = \
            "/opt/medzen/" + str(contract_decl["path"])
        expected_env["MEDZEN_EXECUTION_CONTRACT_SHA256"] = \
            str(contract_decl.get("sha256") or "")
    actual_env = dict(receipt.get("Environment") or {})
    if actual_env != expected_env:
        drift = sorted(set(expected_env.items()) ^ set(actual_env.items()))
        fail(f"receipt Environment differs from the packet's rendered "
             f"environment ({len(drift)} drifted entries, e.g. {drift[:3]})")
    # Codex review #22 blocker 1: verify the COMPLETE job request, not just a
    # sample of fields — role, network isolation, VPC, volume, instance count,
    # checkpoint config, and no input channels (the trainer fetches its own
    # governed data; a smuggled input channel would be ungoverned data).
    if str(receipt.get("RoleArn")) != \
            "arn:aws:iam::558069890522:role/medzen-trainer-role":
        fail(f"receipt RoleArn {receipt.get('RoleArn')!r} is not the pinned "
             "trainer role")
    if bool(receipt.get("EnableNetworkIsolation")) is not False:
        fail("receipt EnableNetworkIsolation is not False (the calibration "
             "job fetches audio/model from S3 through the VPC endpoints)")
    vpc = receipt.get("VpcConfig") or {}
    if sorted(vpc.get("SecurityGroupIds") or []) != \
            sorted(packet.get("security_group_ids") or []):
        fail(f"receipt VpcConfig.SecurityGroupIds {vpc.get('SecurityGroupIds')!r}"
             f" != the packet's {packet.get('security_group_ids')!r}")
    if sorted(vpc.get("Subnets") or []) != sorted(packet.get("subnets") or []):
        fail(f"receipt VpcConfig.Subnets {vpc.get('Subnets')!r} != the "
             f"packet's {packet.get('subnets')!r}")
    resources_full = receipt.get("ResourceConfig") or {}
    if int(resources_full.get("InstanceCount") or 0) != 1:
        fail(f"receipt InstanceCount {resources_full.get('InstanceCount')!r} "
             "!= 1")
    if int(resources_full.get("VolumeSizeInGB") or -1) != \
            int(packet.get("volume_gb") or -2):
        fail(f"receipt VolumeSizeInGB {resources_full.get('VolumeSizeInGB')!r}"
             f" != the packet's {packet.get('volume_gb')!r}")
    checkpoint = receipt.get("CheckpointConfig") or {}
    packet_job_id = str(packet.get("job_id") or "").strip()
    expected_ckpt = (f"s3://medzen-speech/research/b5-training/{packet_job_id}"
                     "/checkpoints")
    if str(checkpoint.get("S3Uri")) != expected_ckpt:
        fail(f"receipt CheckpointConfig.S3Uri {checkpoint.get('S3Uri')!r} != "
             f"the derived {expected_ckpt!r}")
    if receipt.get("InputDataConfig"):
        fail("receipt has InputDataConfig channels — the calibration job "
             "takes NO input channels (its data path is the governed mix + "
             "the baked dev slices); a channel would be ungoverned data")
    out = receipt.get("OutputDataConfig") or {}
    if str(out.get("KmsKeyId")) != str(packet.get("kms_key_arn")):
        fail(f"receipt output KMS {out.get('KmsKeyId')!r} != the packet's "
             f"{packet.get('kms_key_arn')!r}")
    job_id = str(packet.get("job_id") or "").strip()
    expected_output = f"s3://medzen-speech/research/b5-training/{job_id}/output"
    if str(out.get("S3OutputPath")) != expected_output:
        fail(f"receipt S3OutputPath {out.get('S3OutputPath')!r} != the "
             f"derived {expected_output!r} — fetch the artifacts from the "
             "declared KMS-encrypted output only")
    resources = receipt.get("ResourceConfig") or {}
    if str(resources.get("InstanceType")) != str(packet.get("instance_type")):
        fail(f"receipt instance {resources.get('InstanceType')!r} != the "
             f"packet's {packet.get('instance_type')!r}")
    stopping = receipt.get("StoppingCondition") or {}
    if int(stopping.get("MaxRuntimeInSeconds") or -1) != \
            int(packet.get("max_runtime_seconds") or -2):
        fail(f"receipt MaxRuntimeInSeconds "
             f"{stopping.get('MaxRuntimeInSeconds')!r} != the packet's "
             f"{packet.get('max_runtime_seconds')!r}")
    if bool(receipt.get("EnableManagedSpotTraining")) != \
            bool(packet.get("managed_spot")):
        fail("receipt EnableManagedSpotTraining differs from the packet")
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


def safe_extract_bundle(tar_path: Path, workdir: Path) -> dict[str, Path]:
    """Extract ONLY the allowlisted bundle members from model.tar.gz,
    refusing absolute paths, traversal, links, or missing members. Returns
    {member_name: extracted_path}."""
    import tarfile

    out: dict[str, Path] = {}
    with tarfile.open(tar_path, "r:*") as archive:
        names = {m.name.lstrip("./"): m for m in archive.getmembers()}
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
            dest = workdir / member_name
            dest.parent.mkdir(parents=True, exist_ok=True)
            with archive.extractfile(member) as src, dest.open("wb") as dst:
                for chunk in iter(lambda: src.read(1 << 22), b""):
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
    expected_prefix = f"s3://medzen-speech/research/b5-training/{job_id}/output"
    if not model_uri.startswith(expected_prefix):
        failures.append(
            f"ModelArtifacts {model_uri!r} is outside the derived output "
            f"prefix {expected_prefix!r}")

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


def live_fetch(packet: dict[str, Any], workdir: Path) -> tuple[
        dict[str, Any], dict[str, Path], dict[str, Any]]:
    """AWS side of authoritative mode: pin account+region, call
    DescribeTrainingJob ITSELF, follow ModelArtifacts.S3ModelArtifacts, fetch
    that exact object (VersionId + KMS captured from the response), and
    safe-extract the bundle. Nothing here is caller-suppliable."""
    import boto3

    session = boto3.session.Session(region_name=MEDZEN_REGION)
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != MEDZEN_ACCOUNT:
        raise SystemExit(
            f"live verification must run in account {MEDZEN_ACCOUNT}, caller "
            f"is in {identity.get('Account')!r}")
    job_id = str(packet.get("job_id") or "").strip()
    job_name = f"medzen-b5-{job_id}"
    receipt = session.client("sagemaker").describe_training_job(
        TrainingJobName=job_name)
    model_uri = str(((receipt.get("ModelArtifacts") or {})
                     .get("S3ModelArtifacts")) or "")
    if not model_uri.startswith("s3://"):
        raise SystemExit(
            f"DescribeTrainingJob returned no S3ModelArtifacts ({model_uri!r})")
    bucket, _, key = model_uri.removeprefix("s3://").partition("/")
    response = session.client("s3").get_object(Bucket=bucket, Key=key)
    tar_path = workdir / "model.tar.gz"
    with tar_path.open("wb") as stream:
        for chunk in iter(lambda: response["Body"].read(1 << 22), b""):
            stream.write(chunk)
    s3_meta = {
        "uri": model_uri,
        "VersionId": response.get("VersionId"),
        "ETag": response.get("ETag"),
        "SSEKMSKeyId": response.get("SSEKMSKeyId"),
        "ContentLength": response.get("ContentLength"),
    }
    extracted = safe_extract_bundle(tar_path, workdir / "bundle")
    return receipt, extracted, s3_meta


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
    if spec["metrics_schema"] != METRICS_SCHEMA:
        raise SystemExit(
            f"result_verifier.metrics_schema {spec['metrics_schema']!r} != "
            f"this verifier's {METRICS_SCHEMA!r}")
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
    job_id = str(packet.get("job_id") or "").strip()
    expected_job_name = f"medzen-b5-{job_id}" if job_id else None
    expected_contract_sha = str(
        (packet.get("execution_contract") or {}).get("sha256") or "") or None

    # ---- AUTHORITATIVE (--live): the verifier fetches everything itself ----
    if args.live:
        import tempfile
        workdir = args.workdir or Path(tempfile.mkdtemp(prefix="arm2-verify-"))
        workdir.mkdir(parents=True, exist_ok=True)
        receipt, extracted, s3_meta = live_fetch(packet, workdir)
        failures, facts = verify_live_bundle(
            packet=packet, receipt=receipt, extracted=extracted,
            s3_meta=s3_meta, verifier_script_sha=verifier_sha)
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
