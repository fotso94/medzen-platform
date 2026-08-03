"""The run record must describe what happened, and must not imply more.

The specific failure it guards against: a record of a completed run being read
later as a record of a *good* run. Training loss fell; nothing measured whether
transcription improved, because no evaluation was run.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REC = ROOT / "platform/evidence/training-run-23868bab2d84.json"
SCRIPT = ROOT / "scripts/record_training_run.py"
POLICY = ROOT / "platform/decisions/DQ-2026-002-policy-deferral.json"


@pytest.fixture(scope="module")
def rec():
    return json.loads(REC.read_bytes())


# --------------------------------------------------------------------------- #
# what it claims, and what it refuses to claim
# --------------------------------------------------------------------------- #
def test_completion_is_not_quality(rec):
    assert rec["status"] == "COMPLETED"
    assert rec["evaluation_performed"] is False
    assert rec["promotion_performed"] is False
    assert "NOTHING about model quality" in rec["statement"]
    assert "optimiser worked" in rec["statement"]


def test_artifacts_stay_in_candidates(rec):
    assert rec["artifacts"]["scope"] == "candidates/ only"
    assert rec["artifact_prefix"].startswith("s3://medzen-speech/candidates/asr/")
    blob = json.dumps(rec)
    for forbidden in ("s3://medzen-speech/eval/", "s3://medzen-speech/registry/"):
        assert forbidden not in blob


# --------------------------------------------------------------------------- #
# identity: run, fingerprint, adapter
# --------------------------------------------------------------------------- #
def test_binds_the_run_id_and_mlflow(rec):
    assert rec["run_id"] == "23868bab2d8448759fc1b9ed26156952"
    assert rec["mlflow_run_status"] == "FINISHED"
    assert rec["mlflow_db"].endswith("/23868bab2d8448759fc1b9ed26156952/mlflow.db")


def test_fingerprint_matches_the_preflight_and_two_sources_agree(rec):
    fp = "77c7ce61edba96c806fa22a0d50792fc4976a3cebe01a3322493583d94cb9b7c"
    assert rec["data"]["dataset_fingerprint"] == fp
    assert set(rec["data"]["fingerprint_sources_agree"]) == {"run.json",
                                                            "mlflow.params"}


def test_adapter_checksum_is_bound_and_consistent(rec):
    a = rec["artifacts"]
    sha = a["adapter_sha256"]
    assert len(sha) == 64 and all(c in "0123456789abcdef" for c in sha)
    assert a["final"]["adapter_model.safetensors"]["sha256"] == sha
    assert a["final"]["adapter_model.safetensors"]["bytes"] == a["adapter_bytes"]
    assert a["adapter_bytes"] > 1_000_000


def test_all_six_checkpoints_present_and_non_empty(rec):
    c = rec["artifacts"]["checkpoints"]
    assert sorted(c) == [f"checkpoint-{s}" for s in (100, 200, 300, 400, 500, 600)]
    assert all(v["objects"] > 0 and v["bytes"] > 0 for v in c.values())


# --------------------------------------------------------------------------- #
# training facts come from the checkpoint, not the trainer's own summary
# --------------------------------------------------------------------------- #
def test_training_summary(rec):
    t = rec["training"]
    assert t["steps"] == 600
    assert t["loss_is_finite"] is True
    assert t["descent_gate_step"] == 100 and t["descent_gate_verdict"] == "PASSED"
    assert t["first5_mean"] > t["last5_mean"], "loss must have fallen"
    assert t["device"] == "cuda" and t["gpu_peak_mb"] > 0
    assert len(t["loss_by_step"]) == t["losses_logged"] == 60


# --------------------------------------------------------------------------- #
# deferral
# --------------------------------------------------------------------------- #
def test_deferral_evidence_is_exact(rec):
    d = rec["deferral"]
    assert d["exclusions_declared"] == d["exclusions_removed"] == 20
    assert d["exclusions_over_decoder_limit"] == 6
    assert d["exclusions_extreme_token_rate"] == 14
    assert d["exclusions_applied"] == "before_temperature_sampling"
    assert d["over_limit_rows_remaining"] == 0
    assert d["eligible_rows_before_exclusions"] == 4620
    assert d["eligible_rows_after_exclusions"] == 4600
    assert d["promotion_permitted"] is False


def test_policy_hash_is_recomputed_and_matches_the_local_policy(rec):
    import hashlib
    local = hashlib.sha256(POLICY.read_bytes()).hexdigest()
    d = rec["deferral"]
    assert d["policy_sha256_recomputed_from_s3"] == local
    assert d["exclusions_policy_sha256"] == local
    assert d["adoption_binds_this_policy"] is True
    assert d["corpus_unchanged_since_adoption"] is True


def test_record_says_the_deferred_rows_are_still_unreviewed(rec):
    d = rec["deferral"]
    assert d["human_review_performed"] is False
    assert "remain UNREVIEWED" in d["note"]
    assert "does not license their reuse" in d["note"]
    # and the draft really is still open
    draft = json.loads(
        (ROOT / "platform/decisions/DQ-2026-001-label-review.json").read_bytes())
    assert draft["status"] == "draft"
    assert all(e["classification"] is None for e in draft["entries"])


# --------------------------------------------------------------------------- #
# the recorder verifies rather than copies
# --------------------------------------------------------------------------- #
def test_recorder_hashes_bytes_not_etags():
    """A multipart ETag is a hash of hashes and says nothing about content."""
    s = SCRIPT.read_text()
    assert "No ETag shortcuts" in s
    assert "hashlib.sha256()" in s
    assert '"ETag"' not in s


def test_recorder_reads_steps_from_the_checkpoint_not_the_summary():
    s = SCRIPT.read_text()
    assert "trainer_state.json" in s
    assert 'run.get("steps") != ts["global_step"]' in s


def test_recorder_cross_checks_three_sources():
    s = SCRIPT.read_text()
    assert "mlflow.db" in s
    assert "fingerprint differs between run.json and MLflow" in s
    assert "the applied policy hash does not match the policy in S3" in s
    assert "the corpus changed since it was adopted" in s


def test_recorder_refuses_on_any_disagreement():
    s = SCRIPT.read_text()
    assert "REFUSING" in s
    assert "return 1" in s
    assert "Disagreement between any two sources is fatal" in s


def test_recorder_confirms_the_image_in_ecr():
    s = SCRIPT.read_text()
    assert "describe_images" in s
    assert "is not an ECR " in s
