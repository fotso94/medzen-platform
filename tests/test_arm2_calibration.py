"""Arm-2 calibration instrumentation + verifier + packet-semantics + wrapper
tests (Codex reviews #19 F3/F4 and #20 F3/F4/F5). All host-safe: the metrics
accumulator, the result verifier, the launcher cross-check and the wrapper's
orchestration/scoring helpers are pure (no torch), so the acceptance gate and
the evidence binding are exercised off the GPU.
"""
from __future__ import annotations

import copy
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
from verify_arm2_calibration import (load_verifier_spec,  # noqa: E402
                                     verify_calibration)


# --------------------------------------------------------------------------
# helpers: build a fully-valid metrics artifact through the real code paths
# --------------------------------------------------------------------------

def _spec() -> dict:
    return json.loads(_PACKET.read_bytes())["result_verifier"]


def _identity() -> dict:
    return {
        "run_fingerprint": "f" * 64,
        "training_job_name": "medzen-b5-b5-universal-arm2-ftcal-2026-001",
        "export_manifest_sha256": "a" * 64,
        "export_model_sha256": "b" * 64,
        "dev_manifest_shas": {"lingala": "c" * 64, "swahili": "d" * 64},
        "scorer": "ctc-greedy/argmax+collapse; metric=corpus-word-error-rate/1",
        "packet_sha256": "e" * 64,
        "verifier_script_sha256": "9" * 64,
    }


def _good_artifact(steps: int = 30) -> dict:
    metrics = CalibrationMetrics()
    for step in range(1, steps + 1):
        metrics.record_micro({
            "ctc": 1.0, "kd": 0.4, "total": 1.0 + 0.5 * 0.4, "alpha": 0.5,
            "kd_coverage": {lang: {"rows": 1, "frames": 5}
                            for lang in ("english", "french",
                                         "swahili", "lingala")}})
        metrics.commit_step(step, lr=1e-5)
    return metrics.finalize(
        status="COMPLETED", steps_completed=steps, max_steps=steps,
        peak_gpu_bytes=10_000_000_000, wall_seconds=120.0,
        samples_per_step=16, identity=_identity(),
        serve={"readyz": True, "adapter_residue": False, "weights_finite": True},
        dev_sentinel_wer={"lingala": 0.18, "swahili": 0.13})


# --------------------------------------------------------------------------
# F3 (a): the metrics accumulator produces the verifier's artifact schema
# --------------------------------------------------------------------------

def test_calibration_metrics_builds_the_verifier_schema():
    artifact = _good_artifact(steps=2)
    assert artifact["schema"] == CALIBRATION_METRICS_SCHEMA
    assert len(artifact["per_step"]) == 2
    assert artifact["per_step"][0]["kd"] == 0.4
    assert artifact["per_step"][0]["alpha"] == 0.5
    assert artifact["step_sequence"] == [1, 2]
    assert artifact["kd_positive_finite_steps"] == 2
    assert artifact["kd_coverage"]["english"] == {"rows": 2, "frames": 10}
    assert artifact["throughput"]["steps_per_min"] > 0
    assert artifact["throughput"]["samples_per_sec"] > 0  # Codex #20 F5


def test_metrics_resume_restores_full_trajectory():
    """Codex review #20 F5: the accumulator restarted empty on resume. state
    round-trips so per_step keeps every recorded step."""
    m1 = CalibrationMetrics()
    for step in (1, 2):
        m1.record_micro({"ctc": 1.0, "kd": 0.4, "total": 1.2, "alpha": 0.5,
                         "kd_coverage": {"lingala": {"rows": 1, "frames": 5}}})
        m1.commit_step(step, lr=1e-5)
    state = json.loads(json.dumps(m1.to_state()))  # survives a JSON checkpoint
    m2 = CalibrationMetrics()
    m2.restore(state)
    for step in (3, 4):
        m2.record_micro({"ctc": 1.0, "kd": 0.4, "total": 1.2, "alpha": 0.5,
                         "kd_coverage": {"lingala": {"rows": 1, "frames": 5}}})
        m2.commit_step(step, lr=1e-5)
    art = m2.finalize(status="COMPLETED", steps_completed=4, max_steps=4,
                      peak_gpu_bytes=1, wall_seconds=1.0, samples_per_step=16)
    assert art["step_sequence"] == [1, 2, 3, 4]
    assert art["kd_coverage"]["lingala"] == {"rows": 4, "frames": 20}


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
    (lambda a: a["per_step"].__setitem__(
        0, {**a["per_step"][0], "total": 9.9}), "loss equation"),
    (lambda a: a["per_step"].__setitem__(
        0, {**a["per_step"][0], "step": 99}), "contiguous"),
    (lambda a: a.update(kd_positive_finite_steps=29), "positive-and-finite"),
    (lambda a: a["kd_coverage"].pop("lingala"), "lingala"),
    (lambda a: a["kd_coverage"].__setitem__("french", {"rows": 0, "frames": 0}),
     "0 KD rows"),
    (lambda a: a.update(peak_gpu_bytes=None), "peak_gpu_bytes"),
    (lambda a: a.update(peak_gpu_bytes=99_000_000_000), "exceeds"),
    (lambda a: a["throughput"].update(steps_per_min=0), "steps_per_min"),
    (lambda a: a["throughput"].update(samples_per_sec=0), "samples_per_sec"),
    (lambda a: a.update(serve=None), "readyz"),
    (lambda a: a["serve"].update(adapter_residue=True), "adapter_residue"),
    (lambda a: a.update(dev_sentinel_wer=None), "dev_sentinel_wer"),
    (lambda a: a["dev_sentinel_wer"].pop("swahili"), "swahili"),
    (lambda a: a.update(identity=None), "identity block is absent"),
    (lambda a: a["identity"].update(run_fingerprint=""), "run_fingerprint"),
    (lambda a: a["identity"].update(scorer=""), "scorer"),
    (lambda a: a["identity"]["dev_manifest_shas"].pop("lingala"),
     "dev_manifest_shas"),
])
def test_verifier_fails_each_defect(mutate, needle):
    artifact = _good_artifact()
    mutate(artifact)
    failures = verify_calibration(artifact, _spec())
    assert failures, f"expected a failure for {needle}"
    assert any(needle in f for f in failures), (needle, failures)


def test_verifier_binds_packet_and_verifier_sha():
    """Codex review #20 F5: metrics produced under a DIFFERENT packet or a
    DIFFERENT verifier are rejected."""
    art = _good_artifact()
    ok = verify_calibration(art, _spec(),
                            packet_canonical_sha="e" * 64,
                            verifier_script_sha="9" * 64)
    assert ok == []
    bad_pkt = verify_calibration(art, _spec(),
                                 packet_canonical_sha="0" * 64,
                                 verifier_script_sha="9" * 64)
    assert any("packet_sha256" in f for f in bad_pkt)
    bad_ver = verify_calibration(art, _spec(),
                                 packet_canonical_sha="e" * 64,
                                 verifier_script_sha="0" * 64)
    assert any("verifier_script_sha256" in f for f in bad_ver)


# --------------------------------------------------------------------------
# F4 (verifier-side): load_verifier_spec pins the CANONICAL contract — the
# exact bypass set Codex reproduced must now be rejected
# --------------------------------------------------------------------------

def test_load_verifier_spec_accepts_the_committed_packet():
    assert load_verifier_spec(json.loads(_PACKET.read_bytes()))


@pytest.mark.parametrize("mutate,needle", [
    (lambda s: s.update(script="scripts/always_pass.py"), "canonical"),
    (lambda s: s.update(metrics_artifact="../../fake.json"), "traversal"),
    (lambda s: s.update(expected_steps=0), "positive int"),
    (lambda s: s.update(gpu_memory_ceiling_bytes=10 ** 15), "physical"),
    (lambda s: s.update(dev_sentinel_languages=[]), "non-empty"),
    (lambda s: s.update(dev_sentinel_languages=["french"]), "regression sentinels"),
])
def test_load_verifier_spec_rejects_bypass_specs(mutate, needle):
    packet = json.loads(_PACKET.read_bytes())
    mutate(packet["result_verifier"])
    with pytest.raises(SystemExit) as exc:
        load_verifier_spec(packet)
    assert needle in str(exc.value), (needle, str(exc.value))


def test_expected_steps_one_is_caught_by_the_metrics_cross_check():
    """expected_steps=1 while the run does 30 is caught at verify time via the
    metrics max_steps/steps_completed cross-check (the launcher also pins it to
    MEDZEN_MAX_STEPS) — not by load_verifier_spec, which can't know the budget."""
    spec = _spec()
    spec = {**spec, "expected_steps": 1}
    failures = verify_calibration(_good_artifact(steps=30), spec)
    assert any("expected 1" in f or "!= expected 1" in f for f in failures)


# --------------------------------------------------------------------------
# F4 (launcher-side): validate_arm2_semantics cross-check + canonical contract
# --------------------------------------------------------------------------

def _packet_and_env():
    packet = json.loads(_PACKET.read_bytes())
    env = dict(packet["environment"])
    # the launcher injects this at render time; emulate for the unit test
    env["MEDZEN_CALIBRATION_PACKET_SHA256"] = "a" * 64
    return packet, env


def test_arm2_semantics_accepts_the_committed_packet():
    packet, env = _packet_and_env()
    validate_arm2_semantics(packet, env)  # no raise


@pytest.mark.parametrize("mutate,needle", [
    (lambda b, e: b["distillation"].update(kd_alpha=0.9), "kd_alpha"),
    (lambda b, e: e.update(MEDZEN_KD_TEMPERATURE="2.0"), "kd_temperature"),
    (lambda b, e: b.update(acceptance_criteria=[]), "acceptance_criteria"),
    (lambda b, e: b.pop("result_verifier"), "result_verifier"),
    (lambda b, e: b["result_verifier"].update(script="scripts/always_pass.py"),
     "must be"),
    (lambda b, e: b["result_verifier"].update(metrics_artifact="../../x.json"),
     "traversal"),
    (lambda b, e: b["result_verifier"].update(expected_steps=1),
     "expected_steps"),
    (lambda b, e: b["result_verifier"].update(gpu_memory_ceiling_bytes=10 ** 15),
     "physical"),
    (lambda b, e: b["result_verifier"].update(dev_sentinel_languages=["french"]),
     "regression sentinels"),
    (lambda b, e: e.update(MEDZEN_KD_LANGUAGE_WEIGHTS="lingala=9.0"),
     "language_weights"),
    (lambda b, e: e.pop("MEDZEN_CALIBRATION_PACKET_SHA256"),
     "MEDZEN_CALIBRATION_PACKET_SHA256"),
    (lambda b, e: e.pop("MEDZEN_DEV_SENTINEL_MANIFEST_FILES"),
     "MEDZEN_DEV_SENTINEL_MANIFEST_FILES"),
    (lambda b, e: e.update(MEDZEN_DEV_SENTINEL_MANIFEST_FILES="lingala=x.jsonl"),
     "slice for every"),
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
    validate_arm2_semantics({}, {"MEDZEN_KD_ENABLE": "0"})


# --------------------------------------------------------------------------
# F3 wrapper: pure orchestration/scoring helpers (host-safe)
# --------------------------------------------------------------------------

def test_word_error_rate_matches_known_cases():
    from pipeline.omniasr_calibrate import word_error_rate
    assert word_error_rate(["a b c"], ["a b c"]) == 0.0          # perfect
    assert word_error_rate(["a b c d"], ["a x c d"]) == 0.25     # 1 sub / 4
    # corpus-level: edits summed over refs / total ref words
    assert word_error_rate(["a b", "c d"], ["a b", "c x"]) == 0.25


def test_parse_dev_manifest_files_and_traversal_guard():
    from pipeline.omniasr_calibrate import (CalibrationRefusal,
                                            parse_dev_manifest_files)
    got = parse_dev_manifest_files("lingala=a/l.jsonl,swahili=a/s.jsonl")
    assert got == {"lingala": "a/l.jsonl", "swahili": "a/s.jsonl"}
    for bad in ("lingala=/abs/l.jsonl", "lingala=../escape.jsonl", "noeq"):
        with pytest.raises(CalibrationRefusal):
            parse_dev_manifest_files(bad)


def test_build_identity_and_patch_metrics(tmp_path):
    from pipeline.omniasr_calibrate import build_identity, patch_metrics
    identity = build_identity(
        run_fingerprint="f" * 64, training_job_name="job",
        export={"manifest_sha256": "a" * 64, "checkpoint_sha256": "b" * 64},
        dev_manifest_shas={"lingala": "c" * 64, "swahili": "d" * 64},
        packet_sha256="e" * 64, verifier_script_sha256="9" * 64)
    assert identity["export_model_sha256"] == "b" * 64
    # patch merges into the trainer's artifact (must already exist)
    metrics_path = tmp_path / "calibration-metrics.json"
    metrics_path.write_bytes(json.dumps({"schema": "x", "serve": None}).encode())
    merged = patch_metrics(
        metrics_path, serve={"readyz": True}, dev_sentinel_wer={"lingala": 0.1},
        identity=identity)
    assert merged["serve"] == {"readyz": True}
    assert merged["identity"]["run_fingerprint"] == "f" * 64
    from pipeline.omniasr_calibrate import CalibrationRefusal
    with pytest.raises(CalibrationRefusal):
        patch_metrics(tmp_path / "missing.json", serve={}, dev_sentinel_wer={},
                      identity=identity)


def test_run_verifier_smoke_passes_on_a_clean_artifact(tmp_path):
    """The wrapper's in-image smoke: writes a clean artifact and the canonical
    verifier returns 0. bind_packet_sha=False (the in-image packet is DRAFT)."""
    from pipeline.omniasr_calibrate import run_verifier
    art = _good_artifact()
    # verifier_script_sha256 must match the ACTUAL in-repo verifier bytes for
    # the smoke's self-bind; recompute and stamp it
    import hashlib
    vsha = hashlib.sha256(
        (_REPO / "scripts/verify_arm2_calibration.py").read_bytes()).hexdigest()
    art["identity"]["verifier_script_sha256"] = vsha
    metrics_path = tmp_path / "calibration-metrics.json"
    metrics_path.write_bytes(json.dumps(art).encode())
    assert run_verifier(metrics_path, _PACKET, bind_packet_sha=False) == 0
