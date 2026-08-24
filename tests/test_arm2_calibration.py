"""Arm-2 calibration instrumentation + verifier + packet-semantics tests
(Codex review #19 F3/F4). All host-safe: the metrics accumulator, the
result verifier and the launcher's recipe/environment cross-check are pure
(no torch), so the acceptance gate is exercised off the GPU.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from pipeline.omniasr_train import (CALIBRATION_METRICS_SCHEMA,
                                    CalibrationMetrics)

_REPO = Path(__file__).resolve().parents[1]
_PACKET = (_REPO / "platform/manifests/"
           "B5-UNIVERSAL-ARM2-FTCAL-SAGEMAKER-BINDINGS-2026-001.DRAFT.json")

sys.path.insert(0, str(_REPO / "scripts"))
from b5_sagemaker_job import JobRefusal, validate_arm2_semantics  # noqa: E402
from verify_arm2_calibration import verify_calibration  # noqa: E402


# --------------------------------------------------------------------------
# F3 (a): the metrics accumulator produces the verifier's artifact schema
# --------------------------------------------------------------------------

def _run_two_steps(metrics: CalibrationMetrics) -> None:
    # grad_accum=2 micro-batches per step; one english + one lingala row each
    for step in (1, 2):
        for _ in range(2):
            metrics.record_micro({
                "ctc": 1.0, "kd": 0.5, "total": 1.25, "alpha": 0.5,
                "kd_coverage": {"english": {"rows": 1, "frames": 4},
                                "lingala": {"rows": 1, "frames": 6}}})
        metrics.commit_step(step, lr=1e-5)


def test_calibration_metrics_builds_the_verifier_schema():
    metrics = CalibrationMetrics()
    _run_two_steps(metrics)
    artifact = metrics.finalize(
        status="COMPLETED", steps_completed=2, max_steps=2,
        peak_gpu_bytes=1_000_000, wall_seconds=6.0,
        serve={"readyz": True, "adapter_residue": False, "weights_finite": True},
        dev_sentinel_wer={"lingala": 0.18, "swahili": 0.13})
    assert artifact["schema"] == CALIBRATION_METRICS_SCHEMA
    assert len(artifact["per_step"]) == 2
    # per-step means over the 2 micro-batches
    assert artifact["per_step"][0]["kd"] == 0.5
    assert artifact["kd_positive_finite_steps"] == 2
    # coverage summed across all micro-batches of all steps (2 steps x 2 micro)
    assert artifact["kd_coverage"]["english"] == {"rows": 4, "frames": 16}
    assert artifact["kd_coverage"]["lingala"] == {"rows": 4, "frames": 24}
    assert artifact["throughput"]["steps_per_min"] > 0


def _good_artifact() -> dict:
    metrics = CalibrationMetrics()
    for step in range(1, 31):
        metrics.record_micro({
            "ctc": 1.0, "kd": 0.4, "total": 1.2, "alpha": 0.5,
            "kd_coverage": {lang: {"rows": 1, "frames": 5}
                            for lang in ("english", "french",
                                         "swahili", "lingala")}})
        metrics.commit_step(step, lr=1e-5)
    return metrics.finalize(
        status="COMPLETED", steps_completed=30, max_steps=30,
        peak_gpu_bytes=10_000_000_000, wall_seconds=120.0,
        serve={"readyz": True, "adapter_residue": False, "weights_finite": True},
        dev_sentinel_wer={"lingala": 0.18, "swahili": 0.13})


def _spec() -> dict:
    return json.loads(_PACKET.read_bytes())["result_verifier"]


# --------------------------------------------------------------------------
# F3 (b): the verifier PASSES a clean run and FAILS every defect
# --------------------------------------------------------------------------

def test_verifier_passes_a_clean_calibration():
    assert verify_calibration(_good_artifact(), _spec()) == []


@pytest.mark.parametrize("mutate,needle", [
    (lambda a: a.update(status="TRAINING_DIVERGED_NONFINITE"), "COMPLETED"),
    (lambda a: a.update(steps_completed=29), "steps_completed"),
    (lambda a: a["per_step"].__setitem__(0, {**a["per_step"][0], "kd": 0.0}),
     "not > 0"),
    (lambda a: a["per_step"].__setitem__(
        0, {**a["per_step"][0], "kd": float("nan")}), "finite"),
    (lambda a: a.update(kd_positive_finite_steps=29), "positive-and-finite"),
    (lambda a: a["kd_coverage"].pop("lingala"), "lingala"),
    (lambda a: a["kd_coverage"].__setitem__("french", {"rows": 0, "frames": 0}),
     "0 KD rows"),
    (lambda a: a.update(peak_gpu_bytes=None), "peak_gpu_bytes"),
    (lambda a: a.update(peak_gpu_bytes=99_000_000_000), "exceeds"),
    (lambda a: a["throughput"].update(steps_per_min=0), "steps_per_min"),
    (lambda a: a.update(serve=None), "readyz"),
    (lambda a: a["serve"].update(adapter_residue=True), "adapter_residue"),
    (lambda a: a.update(dev_sentinel_wer=None), "dev_sentinel_wer"),
    (lambda a: a["dev_sentinel_wer"].pop("swahili"), "swahili"),
])
def test_verifier_fails_each_defect(mutate, needle):
    artifact = _good_artifact()
    mutate(artifact)
    failures = verify_calibration(artifact, _spec())
    assert failures, f"expected a failure for {needle}"
    assert any(needle in f for f in failures), (needle, failures)


def test_verifier_rejects_a_wrong_schema_outright():
    artifact = _good_artifact()
    artifact["schema"] = "something-else/9"
    failures = verify_calibration(artifact, _spec())
    assert failures and "schema" in failures[0]


# --------------------------------------------------------------------------
# F4: the launcher's recipe/environment cross-check
# --------------------------------------------------------------------------

def _packet_and_env():
    packet = json.loads(_PACKET.read_bytes())
    return packet, packet["environment"]


def test_arm2_semantics_accepts_the_committed_packet():
    packet, env = _packet_and_env()
    validate_arm2_semantics(packet, env)  # no raise


@pytest.mark.parametrize("mutate,needle", [
    (lambda b, e: b["distillation"].update(kd_alpha=0.9), "kd_alpha"),
    (lambda b, e: e.update(MEDZEN_KD_TEMPERATURE="2.0"), "kd_temperature"),
    (lambda b, e: b.update(acceptance_criteria=[]), "acceptance_criteria"),
    (lambda b, e: b.pop("result_verifier"), "result_verifier"),
    (lambda b, e: e.update(MEDZEN_KD_LANGUAGE_WEIGHTS="lingala=9.0"),
     "language_weights"),
    (lambda b, e: b["distillation"].update(teacher_card="x"), "teacher_card"),
    (lambda b, e: b.pop("distillation"), "no top-level `distillation`"),
    (lambda b, e: e.update(MEDZEN_KD_ENABLE="0"), "disagree about whether"),
])
def test_arm2_semantics_refuses_internal_contradiction(mutate, needle):
    packet, env = _packet_and_env()
    b, e = copy.deepcopy(packet), copy.deepcopy(env)
    mutate(b, e)
    with pytest.raises(JobRefusal) as exc:
        validate_arm2_semantics(b, e)
    assert needle in str(exc.value), (needle, str(exc.value))


def test_arm2_semantics_is_noop_without_kd():
    # a non-KD packet (no distillation, KD off) must not be forced to carry
    # the KD recipe/verifier bindings
    validate_arm2_semantics({}, {"MEDZEN_KD_ENABLE": "0"})
