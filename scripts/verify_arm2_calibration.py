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
                   "verifier_script_sha256")
# identity fields that must be a 64-hex sha256 (Codex review #20 F5 follow-up:
# presence-only let a fabricated file through — enforce the FORMAT of every
# sha and the value of the derivable ones)
IDENTITY_SHA_FIELDS = ("run_fingerprint", "export_manifest_sha256",
                       "export_model_sha256", "packet_sha256",
                       "verifier_script_sha256")
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
    # the job's ACTUAL environment must equal the packet's, plus the two
    # launcher-injected identity keys — an env drifted from the packet means
    # the job that ran is not the reviewed calibration
    expected_env = dict(packet.get("environment") or {})
    expected_env["MEDZEN_CALIBRATION_PACKET_SHA256"] = packet_canonical_sha
    expected_env["MEDZEN_TRAINING_JOB_NAME"] = expected_job_name
    actual_env = dict(receipt.get("Environment") or {})
    if actual_env != expected_env:
        drift = sorted(set(expected_env.items()) ^ set(actual_env.items()))
        fail(f"receipt Environment differs from the packet's rendered "
             f"environment ({len(drift)} drifted entries, e.g. {drift[:3]})")
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
    parser.add_argument("--metrics", type=Path, required=True,
                        help="calibration-metrics.json written by the trainer "
                             "+ calibration wrapper")
    parser.add_argument("--packet", type=Path, required=True,
                        help="the Arm-2 calibration bindings packet")
    parser.add_argument("--export-manifest", type=Path, default=None,
                        help="the export manifest.json FETCHED FROM THE JOB'S "
                             "KMS-encrypted S3 output. REQUIRED for an "
                             "authoritative verdict (it binds the metrics' "
                             "export shas to the real export); omit only with "
                             "--smoke for an in-repo shape check.")
    parser.add_argument("--export-model", type=Path, default=None,
                        help="the exported model checkpoint (model.pt) FETCHED "
                             "FROM THE JOB'S KMS-encrypted S3 output. REQUIRED "
                             "for an authoritative verdict — it is HASHED and "
                             "must equal both the manifest's declared "
                             "model_sha256 and identity.export_model_sha256 "
                             "(Codex review #21 F3: trusting the hash written "
                             "inside the manifest is not authentication).")
    parser.add_argument("--receipt", type=Path, default=None,
                        help="the raw JSON of `aws sagemaker "
                             "describe-training-job --training-job-name "
                             "medzen-b5-<job_id>`. REQUIRED for an "
                             "authoritative verdict — terminal status, image "
                             "digest, environment, KMS, output location and "
                             "instance are machine-checked against the packet.")
    parser.add_argument("--smoke", action="store_true",
                        help="allow a non-authoritative run WITHOUT the "
                             "authenticated export/model/receipt (shape and "
                             "identity check only; binds NO real artifact)")
    args = parser.parse_args(argv)

    packet = json.loads(args.packet.read_bytes())
    metrics = json.loads(args.metrics.read_bytes())
    spec = load_verifier_spec(packet)

    # bind the metrics to THIS packet (canonical sha), THIS verifier's bytes,
    # and the job name DERIVED from the packet's job_id (medzen-b5-<job_id>)
    packet_sha = _canonical_sha256(packet)
    verifier_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    job_id = str(packet.get("job_id") or "").strip()
    expected_job_name = f"medzen-b5-{job_id}" if job_id else None

    # authenticate the export + job against the REAL artifacts (Codex reviews
    # #20 F5 / #21 F3). The authoritative run REQUIRES all three fetched
    # inputs; only --smoke may skip them, and then the verdict is explicitly
    # non-authoritative (no real artifact is bound).
    authoritative_inputs = (args.export_manifest, args.export_model,
                            args.receipt)
    if any(x is None for x in authoritative_inputs) and not args.smoke:
        raise SystemExit(
            "an authoritative verdict requires --export-manifest, "
            "--export-model AND --receipt (all fetched from the job's "
            "KMS-encrypted S3 output / the SageMaker API); pass --smoke for a "
            "non-authoritative shape/identity check only")

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
            # hash the ACTUAL model bytes — the manifest's declared hash is a
            # claim until the artifact itself reproduces it
            actual_model_sha = _sha256_file(args.export_model)
            if actual_model_sha != str(manifest.get("model_sha256")):
                receipt_failures.append(
                    f"model.pt hashes to {actual_model_sha[:16]}, the export "
                    f"manifest declares "
                    f"{str(manifest.get('model_sha256'))[:16]} — the fetched "
                    "model is not the manifest's model")
            authenticated_export["model_sha256"] = actual_model_sha
    if args.receipt is not None:
        receipt = json.loads(args.receipt.read_bytes())
        # tolerate the awscli top-level shape {"TrainingJobName": ...} or a
        # wrapper {"TrainingJob": {...}}
        if "TrainingJob" in receipt and "TrainingJobName" not in receipt:
            receipt = receipt["TrainingJob"]
        receipt_failures.extend(verify_training_receipt(
            receipt, packet, expected_job_name=expected_job_name or "",
            packet_canonical_sha=packet_sha))

    failures = verify_calibration(
        metrics, spec, packet_canonical_sha=packet_sha,
        verifier_script_sha=verifier_sha, expected_job_name=expected_job_name,
        authenticated_export=authenticated_export)
    failures.extend(receipt_failures)
    report = {
        "verdict": "PASS" if not failures else "FAIL",
        "authoritative": all(x is not None for x in authoritative_inputs),
        "metrics": str(args.metrics),
        "packet": str(args.packet),
        "packet_canonical_sha256": packet_sha,
        "verifier_script_sha256": verifier_sha,
        # byte-binding of every reviewed input, for the review record (the
        # reviewer attaches the S3 VersionIds of the fetched objects alongside)
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
