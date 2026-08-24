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
import json
import math
import sys
from pathlib import Path
from typing import Any

METRICS_SCHEMA = "b5-arm2-calibration-metrics/1"


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) \
        and math.isfinite(float(value))


def verify_calibration(metrics: dict[str, Any],
                       verifier_spec: dict[str, Any]) -> list[str]:
    """Return a list of human-readable FAILURE strings — empty means PASS.

    Every acceptance criterion is checked independently so one run surfaces
    ALL the reasons it is not calibration-clean, not just the first."""
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

    # (2) separate CTC / KD / total per step, KD > 0 and finite every step
    per_step = metrics.get("per_step") or []
    if len(per_step) != expected_steps:
        fail(f"per_step has {len(per_step)} records, expected {expected_steps}")
    for record in per_step:
        for key in ("ctc", "kd", "total"):
            if not _finite_number(record.get(key)):
                fail(f"step {record.get('step')}: {key} is not a finite number "
                     f"({record.get(key)!r})")
        if _finite_number(record.get("kd")) and float(record["kd"]) <= 0.0:
            fail(f"step {record.get('step')}: KD={record['kd']} is not > 0 "
                 "(the distillation term is not live on preservation batches)")
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

    # (5) throughput recorded
    throughput = metrics.get("throughput") or {}
    if not _finite_number(throughput.get("steps_per_min")) \
            or float(throughput.get("steps_per_min", 0)) <= 0:
        fail(f"throughput.steps_per_min not recorded as > 0 "
             f"({throughput.get('steps_per_min')!r})")

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

    return failures


def load_verifier_spec(packet: dict[str, Any]) -> dict[str, Any]:
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

    failures = verify_calibration(metrics, spec)
    report = {
        "verdict": "PASS" if not failures else "FAIL",
        "metrics": str(args.metrics),
        "packet": str(args.packet),
        "failures": failures,
    }
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
