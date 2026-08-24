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


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def _canonical_sha256(obj: Any) -> str:
    return hashlib.sha256(json.dumps(
        obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_calibration(metrics: dict[str, Any],
                       verifier_spec: dict[str, Any],
                       *, packet_canonical_sha: str | None = None,
                       verifier_script_sha: str | None = None) -> list[str]:
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
    # have contributed real rows AND valid frames
    coverage = metrics.get("kd_coverage") or {}
    for language in verifier_spec["required_preservation_coverage"]:
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

    # (8) IDENTITY BINDING (Codex review #20 F5): prove the metrics came from
    # the declared run, export, scorer, dev manifests, this exact packet, and
    # this exact verifier — not an unbound file a caller hand-crafted.
    identity = metrics.get("identity")
    if not isinstance(identity, dict):
        fail("identity block is absent — the metrics are not bound to a run, "
             "export, scorer, packet or verifier")
    else:
        for field in IDENTITY_FIELDS:
            value = identity.get(field)
            if value in (None, "", {}, []):
                fail(f"identity.{field} is absent — evidence is not bound")
        # dev_manifest_shas must cover every dev-sentinel language
        dev_shas = identity.get("dev_manifest_shas") or {}
        for language in verifier_spec["dev_sentinel_languages"]:
            if not str(dev_shas.get(language) or "").strip():
                fail(f"identity.dev_manifest_shas[{language!r}] absent — the "
                     "dev slice that produced the WER is not bound")
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

    return failures


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
    return spec


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, required=True,
                        help="calibration-metrics.json written by the trainer "
                             "+ calibration wrapper")
    parser.add_argument("--packet", type=Path, required=True,
                        help="the Arm-2 calibration bindings packet")
    args = parser.parse_args(argv)

    packet = json.loads(args.packet.read_bytes())
    metrics = json.loads(args.metrics.read_bytes())
    spec = load_verifier_spec(packet)

    # bind the metrics to THIS packet (canonical sha) and THIS verifier's bytes
    packet_sha = _canonical_sha256(packet)
    verifier_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    failures = verify_calibration(
        metrics, spec, packet_canonical_sha=packet_sha,
        verifier_script_sha=verifier_sha)
    report = {
        "verdict": "PASS" if not failures else "FAIL",
        "metrics": str(args.metrics),
        "packet": str(args.packet),
        "packet_canonical_sha256": packet_sha,
        "verifier_script_sha256": verifier_sha,
        "failures": failures,
    }
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
