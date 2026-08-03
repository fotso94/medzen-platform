"""END-TO-END orchestration tests. These invoke the real entrypoint.

Distinct from the unit tests in test_retraining_controls.py: those exercise one
rule each, these run `campaign.run_campaign` against fake S3 and fake services
and assert the ORDER, plus that a failure at each boundary prevents the next
launch. The controls only matter if they sit on the path that actually runs.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import (budget, campaign, language_scope, mlflow_sync,
                      orchestrate, scope_deviation)  # noqa: E402

HISTORICAL_TRAINING = (
    "hausa", "igbo", "lingala", "luganda", "oromo",
    "pidgin", "swahili", "yoruba",
)
HISTORICAL_VALIDATION = ("lingala", "luganda", "oromo")
HISTORICAL_DEFERRED = ("acholi", "akan", "amharic", "ewe", "fula", "shona")
LANGS = HISTORICAL_VALIDATION
POLICY_SHA = "a" * 64
EXCL_SHA = "e" * 64


@pytest.fixture(autouse=True)
def historical_factory_scope(monkeypatch):
    """Exercise proven B4 factory behavior without reopening live scope."""
    monkeypatch.setattr(
        language_scope, "TRAINING_LANGUAGES", HISTORICAL_TRAINING)
    monkeypatch.setattr(
        language_scope, "VALIDATION_LANGUAGES", HISTORICAL_VALIDATION)
    monkeypatch.setattr(
        language_scope, "DEFERRED_LANGUAGES", HISTORICAL_DEFERRED)
    monkeypatch.setattr(
        orchestrate, "VALIDATION_LANGUAGES", HISTORICAL_VALIDATION)


class FakeS3:
    """Enough S3 to exercise conditional writes, read-back and listing."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[str] = []

    # -- helpers ----------------------------------------------------------
    def _etag(self, key):
        return '"' + hashlib.md5(self.objects[key]).hexdigest() + '"'

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError
        if Key not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        body = self.objects[Key]

        class B:
            def read(self_inner):
                return body
        return {"Body": B(), "ETag": self._etag(Key)}

    def put_object(self, Bucket, Key, Body, ContentType=None,
                   IfNoneMatch=None, IfMatch=None):
        from botocore.exceptions import ClientError
        if IfNoneMatch == "*" and Key in self.objects:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        if IfMatch is not None and self.objects.get(Key) is not None \
                and self._etag(Key) != IfMatch:
            raise ClientError({"Error": {"Code": "PreconditionFailed"}}, "PutObject")
        self.objects[Key] = Body
        self.put_calls.append(Key)
        return {"ETag": self._etag(Key)}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=None,
                        ContinuationToken=None):
        ks = sorted(k for k in self.objects if k.startswith(Prefix))
        return {"KeyCount": len(ks),
                "Contents": [{"Key": k} for k in ks],
                "IsTruncated": False}


def metrics(**over):
    d = {l: 0.80 for l in LANGS}
    d.update(over)
    return d


def base_metrics(**over):
    d = {l: 0.90 for l in LANGS}
    d.update(over)
    return d


def perfect():
    return {l: 1.0 for l in LANGS}


def zeros():
    return {l: 0.0 for l in LANGS}


def clean_termination():
    row_counts = {"lingala": 35, "luganda": 53, "oromo": 35}
    return {
        language: {
            "rows": row_counts[language], "count": 0, "checksums": [],
            "eos_missing_count": 0, "eos_missing_checksums": [],
            "cap_hit_count": 0, "cap_hit_checksums": [],
        }
        for language in LANGS
    }


def one_termination_failure(language="lingala", label="row-a"):
    result = clean_termination()
    checksum = hashlib.sha256(label.encode()).hexdigest()
    result[language] = {
        **result[language],
        "count": 1, "checksums": [checksum],
        "eos_missing_count": 1, "eos_missing_checksums": [checksum],
        "cap_hit_count": 1, "cap_hit_checksums": [checksum],
    }
    return result


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "mlflow.db"
    con = sqlite3.connect(p)
    con.execute("create table runs (id text)")
    con.execute("insert into runs values ('r1')")
    con.commit()
    con.close()
    return p


PINS = {
    "git_sha": "c" * 40, "bundle_tar_sha256": "d" * 64,
    "policy_sha256": POLICY_SHA, "adoption_key": "curated/_versions/v2/ADOPTION.json",
    "language_scope_sha256": language_scope.LANGUAGE_SCOPE_SHA256,
    "base_manifest_sha256": "e" * 64, "validation_manifest_sha256": "f" * 64,
    "generation_config_fingerprint": "0" * 64, "evaluator_sha256": "1" * 64,
    "holdout_manifest_key": "eval/lingala/asr/v2-holdout/manifest.jsonl",
    "holdout_manifest_sha256": "2" * 64,
    "holdout_evidence_sha256": "3" * 64,
    "mlflow_parent_run_id": "parent-1",
}


class FakeTracker:
    """Records the real parent/child API without requiring MLflow in unit tests."""

    parent_run_id = "parent-1"

    def __init__(self):
        self.children = {}
        self.finished = []
        self.failed = []
        self.parent = None

    def start_stage(self, stage_key, params):
        if stage_key in self.children:
            raise SystemExit("duplicate child")
        rid = "child-" + hashlib.sha256(stage_key.encode()).hexdigest()[:16]
        self.children[stage_key] = {"run_id": rid, "params": params}
        return rid

    def finish_stage(self, stage_key, result, extra_params=None):
        self.finished.append((stage_key, result, extra_params or {}))

    def fail_stage(self, stage_key, reason):
        self.failed.append((stage_key, reason))

    def log_parent_metrics(self, metrics, tags=None):
        self.parent_metrics = (metrics, tags or {})

    def finish_parent(self, passed, reason=None):
        self.parent = (passed, reason)


def stage_result(descriptor, **kw):
    """A well-formed stage result: lifecycle evidence plus the echoed hash."""
    from pipeline import stage_descriptor as SD
    return {
        "stage_descriptor_sha256": SD.descriptor_hash(descriptor),
        "campaign_run": descriptor["campaign_run"],
        "attempt": descriptor["attempt"], "stage": descriptor["stage"],
        "instance_id": kw.pop("instance_id", "i-" + descriptor["stage"][:8]),
        "launched_utc": "2026-07-31T09:00:00Z",
        "terminated_utc": "2026-07-31T09:10:00Z",
        "actual_seconds": 600, "exit_status": 0,
        "root_volume_deleted": True,
        "aws_final_state": "terminated",
        **kw,
    }


def checkpoint(step, wer=None, sha=None, termination_failures=None):
    termination_failures = termination_failures or clean_termination()
    eos_rate = {
        language: round(
            (item["rows"] - item["eos_missing_count"]) / item["rows"], 4)
        for language, item in termination_failures.items()
    }
    cap_hit_rate = {
        language: round(item["cap_hit_count"] / item["rows"], 4)
        for language, item in termination_failures.items()
    }
    return {"step": step, "wer": wer or metrics(), "eos_rate": eos_rate,
            "cap_hit_rate": cap_hit_rate,
            "termination_failures": termination_failures,
            "smoke": {"passed": True, "reasons": []},
            "artifact_sha256": sha or hashlib.sha256(
                f"cp{step}".encode()).hexdigest(),
            "artifact_tree_sha256": hashlib.sha256(
                f"cp-tree{step}".encode()).hexdigest()}


def make_services(s3, db, **over):
    calls = []

    def verify_policy():
        calls.append("verify_policy")
        return {"policy_sha256": POLICY_SHA,
                "rows": language_scope.EXPECTED_POLICY_ROWS_TOTAL,
                "human_review_performed": False,
                "exclusion_checksums_sha256": EXCL_SHA}

    def verify_adoption():
        calls.append("verify_adoption")
        return {"status": "approved", "deferral_policy_sha256": POLICY_SHA,
                "complete_raw_sha256": "b" * 64,
                "current_complete_raw_sha256": "b" * 64,
                "manifests_verified": True,
                "exclusion_checksums_sha256": EXCL_SHA,
                "dataset_fingerprint": campaign.ADOPTED_FINGERPRINT,
                "eligible_rows": campaign.ADOPTED_ELIGIBLE_ROWS}

    def verify_language_scope():
        calls.append("verify_language_scope")
        return {
            "status": "approved",
            "language_scope_sha256": language_scope.LANGUAGE_SCOPE_SHA256,
            "training_languages": list(language_scope.TRAINING_LANGUAGES),
            "validation_languages": list(language_scope.VALIDATION_LANGUAGES),
            "deferred_languages": list(language_scope.DEFERRED_LANGUAGES),
            "dataset_fingerprint": campaign.EXPECTED_FINGERPRINT,
            "eligible_rows": campaign.EXPECTED_ELIGIBLE_ROWS,
        }

    def run_base_and_preflight(d):
        calls.append("run_base_and_preflight")
        return stage_result(
            d, base={"wer": base_metrics(), "base_arm_key": "a" * 64,
                     "artifact_sha256": "c" * 64,
                     "artifact_key":
                         d["output_prefix"].rstrip("/")
                         + "/evaluations/base.json"},
            preflight={"passed": True, "saved_adapter_sha256": "9" * 64,
                       "reloaded_adapter_sha256": "9" * 64})

    def run_sweep(d, lr):
        calls.append(f"run_sweep:{lr:.0e}")
        return stage_result(d, wer=metrics(), eos_rate=perfect(),
                            cap_hit_rate=zeros(),
                            termination_failures=clean_termination(),
                            smoke={"passed": True, "reasons": []},
                            artifact_sha256=hashlib.sha256(
                                f"sweep{lr}".encode()).hexdigest())

    def run_final(d, lr):
        calls.append(f"run_final:{lr:.0e}")
        return stage_result(
            d, steps_completed=600,
            checkpoints=[checkpoint(st) for st in campaign.FINAL_CHECKPOINTS])

    def run_artifactize(d):
        calls.append("run_artifactize")
        return stage_result(
            d, holdout={"gate": {"passed": True}},
            converted_gate={"passed": True},
            converted_tree_sha256="4" * 64,
            artifact_evaluation_sha256="5" * 64)

    def run_spot_checkpoint(d, lr):
        calls.append(f"run_spot_checkpoint:{lr:.0e}")
        return stage_result(
            d, exit_status="INTERRUPTED_AFTER_DURABLE_CHECKPOINT",
            operator_interrupted=True,
            checkpoint_tree_sha256="6" * 64,
            checkpoint_prefix=(d["output_prefix"].rstrip("/")
                               + "/resume-checkpoint/checkpoint-100"),
            spot_involved=True)

    def run_spot_resume(d, lr):
        calls.append(f"run_spot_resume:{lr:.0e}")
        return stage_result(
            d, exact_checkpoint_match=True,
            resumed_from_tree_sha256=d["input_artifact_sha256"],
            steps_completed=200, spot_involved=True)

    sv = campaign.Services(
        s3=s3, verify_policy=verify_policy, verify_adoption=verify_adoption,
        verify_language_scope=verify_language_scope,
        run_base_and_preflight=run_base_and_preflight, run_sweep=run_sweep,
        run_final=run_final, run_artifactize=run_artifactize,
        run_spot_checkpoint=run_spot_checkpoint,
        run_spot_resume=run_spot_resume,
        mlflow_db=db, tracker=FakeTracker(),
        launcher=object(),
        image_digest="sha256:" + "f" * 64, stage_descriptors=dict(PINS))
    for k, v in over.items():
        setattr(sv, k, v)
    return sv, calls


# --------------------------------------------------------------------------- #
# the happy path, in order
# --------------------------------------------------------------------------- #
def test_full_campaign_runs_every_stage_in_the_required_order(db):
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    out = campaign.run_campaign(sv, "camp-1")

    names = out["trace_names"]
    order = [n for n in names if not n.startswith("mlflow-sync")]
    assert order[:6] == ["readiness", "verify-policy", "verify-adoption",
                         "verify-language-scope", "budget-reserve",
                         "base-and-preflight"]

    # the required sequence, each strictly before the next
    def idx(pred):
        return next(i for i, n in enumerate(names) if pred(n))
    assert idx(lambda n: n == "verify-policy") < idx(lambda n: n == "verify-adoption")
    assert idx(lambda n: n == "verify-adoption") < idx(lambda n: n == "budget-reserve")
    assert idx(lambda n: n == "budget-reserve") < idx(lambda n: n == "base-and-preflight")
    assert idx(lambda n: n == "base-and-preflight") < idx(lambda n: n == "sweep-run")
    assert idx(lambda n: n == "sweep-run") < idx(lambda n: n == "select-lr")
    assert idx(lambda n: n == "select-lr") < idx(lambda n: n == "final-run")
    assert idx(lambda n: n == "final-run") < idx(lambda n: n == "checkpoint-eval")
    assert idx(lambda n: n == "checkpoint-eval") < idx(lambda n: n == "artifactize")
    assert idx(lambda n: n == "artifactize") < idx(lambda n: n == "spot-interrupted")
    assert idx(lambda n: n == "spot-interrupted") < idx(lambda n: n == "spot-resumed")
    assert idx(lambda n: n == "spot-resumed") < idx(lambda n: n == "cleanup")

    assert names.count("sweep-run") == len(orchestrate.LR_CANDIDATES)
    assert names.count("checkpoint-eval") == len(campaign.FINAL_CHECKPOINTS)
    assert out["registered_models"] == 0 and out["promotable"] is False
    assert out["purpose"] == "training_system_validation"
    assert out["selected_checkpoint"]["step"] == 100
    assert out["stopped_on_failed_checkpoint"] is None
    assert out["servable_artifact"]["converted_gate"]["passed"] is True
    assert out["spot_resume"]["exact_checkpoint_match"] is True


def test_base_and_preflight_run_before_any_sweep(db):
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    campaign.run_campaign(sv, "camp-2")
    assert calls.index("run_base_and_preflight") < min(
        i for i, c in enumerate(calls) if c.startswith("run_sweep"))


def test_exactly_declared_gpu_stage_calls(db):
    """One base+preflight, declared sweeps, one final."""
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    out = campaign.run_campaign(sv, "camp-2b")
    gpu = [c for c in calls if c.startswith(("run_base_and_preflight",
                                             "run_sweep", "run_final",
                                             "run_artifactize",
                                             "run_spot_checkpoint",
                                             "run_spot_resume"))]
    assert (len(gpu) == 5 + len(orchestrate.LR_CANDIDATES)
            == out["gpu_instances"] == budget.MAX_GPU_INSTANCES)
    assert calls.count("run_base_and_preflight") == 1
    assert sum(1 for c in calls if c.startswith("run_sweep")) == len(
        orchestrate.LR_CANDIDATES)
    assert sum(1 for c in calls if c.startswith("run_final")) == 1


def test_each_sweep_learning_rate_has_a_distinct_write_once_prefix(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    base = {
        "base_arm_key": "a" * 64,
        "artifact_key": "candidates/evaluations/camp-prefix/base.json",
        "artifact_sha256": "b" * 64,
    }
    descriptors = [
        campaign.make_descriptor(
            sv, "camp-prefix", "1", "sweep", lr,
            orchestrate.TRAINING_SCHEDULE_HORIZON,
            f"reservation-{index}", base_result=base)
        for index, lr in enumerate(orchestrate.LR_CANDIDATES)
    ]
    assert [d["output_prefix"] for d in descriptors] == [
        "candidates/evaluations/camp-prefix/attempt-1/sweep-lr-1e-04/",
    ]
    assert all(d["max_steps"] == 600 for d in descriptors)
    assert all(d["checkpoint_steps"] == [100] for d in descriptors)


def test_one_reservation_per_stage_lifecycle(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    campaign.run_campaign(sv, "camp-2c")
    ledger, _ = budget.load(s3)
    assert len(ledger["reservations"]) == budget.MAX_GPU_INSTANCES
    assert all(r["state"] == "reconciled" for r in ledger["reservations"].values())


def test_two_campaigns_with_attempt_one_get_distinct_budget_lifecycles(db):
    s3 = FakeS3()
    first, _ = make_services(s3, db)
    campaign.run_campaign(first, "camp-budget-a", attempt="1")
    second, _ = make_services(s3, db)
    campaign.run_campaign(second, "camp-budget-b", attempt="1")
    ledger, _ = budget.load(s3)
    assert len(ledger["reservations"]) == 2 * budget.MAX_GPU_INSTANCES
    attempts = {r["attempt"] for r in ledger["reservations"].values()}
    assert any(a.startswith("camp-budget-a-1") for a in attempts)
    assert any(a.startswith("camp-budget-b-1") for a in attempts)


def test_every_checkpoint_produces_a_unique_artifact_and_snapshot(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    out = campaign.run_campaign(sv, "camp-3")
    shas = [c["artifact_sha256"] for c in out["checkpoints"]]
    assert len(set(shas)) == len(campaign.FINAL_CHECKPOINTS)
    steps = [c["step"] for c in out["checkpoints"]]
    assert steps == list(campaign.FINAL_CHECKPOINTS)
    snaps = [k for k in s3.objects if k.startswith("mlflow/snapshots/camp-3/")]
    for step in campaign.FINAL_CHECKPOINTS:
        assert any(f"final-checkpoint-{step}/" in k for k in snaps)


def test_mlflow_snapshots_are_immutable_and_never_the_live_db_path(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    campaign.run_campaign(sv, "camp-4")
    assert not any(k.startswith("mlflow/db/") for k in s3.objects), \
        "the live per-run db path must never be written"
    snaps = [k for k in s3.put_calls if k.endswith("/mlflow.db")]
    assert len(snaps) == len(set(snaps)), "no snapshot key written twice"


def test_budget_is_reserved_before_launch_and_reconciled_after(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    campaign.run_campaign(sv, "camp-5")
    ledger, _ = budget.load(s3)
    assert budget.unresolved(ledger) == [], "no reservation left dangling"
    assert all(r["state"] == "reconciled" for r in ledger["reservations"].values())
    assert budget.committed_usd(ledger) < budget.CEILING_USD


# --------------------------------------------------------------------------- #
# failure at each boundary prevents the next launch
# --------------------------------------------------------------------------- #
def test_bad_policy_stops_before_any_reservation(db):
    s3 = FakeS3()
    sv, calls = make_services(
        s3, db, verify_policy=lambda: {"policy_sha256": POLICY_SHA, "rows": 19,
                                       "human_review_performed": True})
    with pytest.raises(SystemExit, match="human_review_performed=false"):
        campaign.run_campaign(sv, "camp-f1")
    assert budget.load(s3)[0]["reservations"] == {}
    assert s3.objects == {}, "no S3 mutation before governance passes"
    assert not any(c.startswith("train:") for c in calls)


def test_adoption_bound_to_a_different_policy_stops_before_spending(db):
    s3 = FakeS3()
    sv, calls = make_services(
        s3, db, verify_adoption=lambda: {"deferral_policy_sha256": "z" * 64,
                                         "complete_raw_sha256": "b" * 64})
    with pytest.raises(SystemExit, match="does not transfer between policies"):
        campaign.run_campaign(sv, "camp-f2")
    assert budget.load(s3)[0]["reservations"] == {}
    assert "evaluate_base" not in calls


def test_failed_preflight_prevents_the_lr_sweep(db):
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    ok = sv.run_base_and_preflight

    def bad(d):
        r = ok(d)
        r["preflight"] = {"passed": False, "reasons": ["no EOS emitted"]}
        return r
    sv.run_base_and_preflight = bad
    with pytest.raises(SystemExit, match="no learning-rate sweep will start"):
        campaign.run_campaign(sv, "camp-f3")
    assert not any(c.startswith("run_sweep") for c in calls)
    assert budget.unresolved(budget.load(s3)[0]) == [], \
        "the terminated base instance must reconcile even when preflight fails"


def test_all_candidates_failing_gates_prevents_the_final_run(db):
    s3 = FakeS3()
    sv, calls = make_services(s3, db)

    def bad(d, lr):
        calls.append(f"run_sweep:{lr:.0e}")
        return stage_result(d, wer=metrics(oromo=0.99), eos_rate=perfect(),
                            cap_hit_rate=zeros(),
                            termination_failures=clean_termination(),
                            artifact_sha256="a" * 64)
    sv.run_sweep = bad
    with pytest.raises(SystemExit, match="no learning rate passed all four"):
        campaign.run_campaign(sv, "camp-f4")
    assert not any(c.startswith("run_final") for c in calls)
    failed_prefix = "mlflow/snapshots/camp-f4/attempt-1/campaign-failed/"
    assert failed_prefix + "mlflow.db" in s3.objects
    assert failed_prefix + "record.json" in s3.objects
    record = json.loads(s3.objects[failed_prefix + "record.json"])
    assert record["registered_models"] == 0
    assert "no learning rate passed all four" in record["reason"]
    assert sv.tracker.parent[0] is False


def test_checkpoint_300_failure_halts_and_selects_an_earlier_pass(db):
    """A conforming container stops training AT the boundary: it reports
    checkpoints 100-300 only, and steps_completed == 300."""
    s3 = FakeS3()
    sv, _ = make_services(s3, db)

    def final_stops(d, lr):
        return stage_result(d, steps_completed=300, checkpoints=[
            checkpoint(100), checkpoint(200),
            checkpoint(300, wer=metrics(oromo=0.99))])
    sv.run_final = final_stops
    out = campaign.run_campaign(sv, "camp-f5")
    assert out["selected_checkpoint"]["step"] == 100
    assert out["stopped_on_failed_checkpoint"] == 300
    assert sv.tracker.parent == (
        True,
        "selected checkpoint-100 after the predeclared stop at checkpoint-300")
    prefix = "mlflow/snapshots/camp-f5/attempt-1/final-selected/"
    assert prefix + "mlflow.db" in s3.objects
    record = json.loads(s3.objects[prefix + "record.json"])
    assert record["selected_checkpoint_step"] == 100
    assert record["stopped_on_failed_checkpoint"] == 300


def test_first_checkpoint_failure_has_no_candidate_and_fails(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)

    def final_stops(d, lr):
        return stage_result(d, steps_completed=100, checkpoints=[
            checkpoint(100, wer=metrics(oromo=0.99))])
    sv.run_final = final_stops
    with pytest.raises(SystemExit, match="no final checkpoint passed"):
        campaign.run_campaign(sv, "camp-f5-first")
    assert sv.tracker.parent[0] is False


def test_lowest_macro_passing_checkpoint_is_selected_before_late_failure(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)

    def final_stops(d, lr):
        return stage_result(d, steps_completed=600, checkpoints=[
            checkpoint(100, wer=metrics(lingala=0.84)),
            checkpoint(200, wer=metrics(lingala=0.78)),
            checkpoint(300, wer=metrics(lingala=0.74)),
            checkpoint(400, wer=metrics(lingala=0.71)),
            checkpoint(500, wer=metrics(lingala=0.70)),
            checkpoint(600, wer=metrics(lingala=0.99))])
    sv.run_final = final_stops
    out = campaign.run_campaign(sv, "camp-f5-late")
    assert out["selected_checkpoint"]["step"] == 500
    assert out["stopped_on_failed_checkpoint"] == 600


def test_checkpoint_selection_tie_breaks_to_the_earlier_step():
    checkpoints = [
        {"step": 200, "gate": {"passed": True, "macro_wer": 0.7}},
        {"step": 100, "gate": {"passed": True, "macro_wer": 0.7}},
    ]
    assert campaign.select_passing_checkpoint(checkpoints)["step"] == 100


def test_a_container_that_trained_past_a_failed_gate_is_refused(db):
    """Evaluating all 600 steps and reporting the 300 failure afterwards spends
    the whole budget training against a broken objective."""
    s3 = FakeS3()
    sv, _ = make_services(s3, db)

    def final_kept_going(d, lr):
        return stage_result(d, steps_completed=600, checkpoints=[
            checkpoint(100), checkpoint(200),
            checkpoint(300, wer=metrics(oromo=0.99)),
            checkpoint(400), checkpoint(500), checkpoint(600)])
    sv.run_final = final_kept_going
    with pytest.raises(SystemExit, match="Training must STOP at a failing gate"):
        campaign.run_campaign(sv, "camp-f5b")
    assert budget.unresolved(budget.load(s3)[0]) == [], \
        "the terminated final instance must reconcile before semantic refusal"


def test_a_failed_gate_with_too_many_steps_completed_is_refused(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)

    def sneaky(d, lr):
        return stage_result(d, steps_completed=600, checkpoints=[
            checkpoint(100), checkpoint(200),
            checkpoint(300, wer=metrics(oromo=0.99))])
    sv.run_final = sneaky
    with pytest.raises(SystemExit, match="must\n *never have run|never have run"):
        campaign.run_campaign(sv, "camp-f5c")


def test_one_failure_is_tolerated_but_same_checksum_recurrence_stops():
    first = checkpoint(
        100, termination_failures=one_termination_failure(label="same"))
    second = checkpoint(
        200, termination_failures=one_termination_failure(label="same"))
    out = campaign.verify_interleaving(
        {"checkpoints": [first, second], "steps_completed": 200},
        base_metrics(), scope_deviation.TERMINATION_GATE)
    assert out[0]["gate"]["passed"] is True
    assert out[1]["gate"]["passed"] is False
    assert out[1]["gate"]["recurrent_termination_checksums"] == {
        "lingala": [hashlib.sha256(b"same").hexdigest()]}


def test_different_single_failures_do_not_trigger_recurrence():
    checkpoints = [
        checkpoint(
            step,
            termination_failures=one_termination_failure(label=f"row-{step}"))
        for step in campaign.FINAL_CHECKPOINTS
    ]
    out = campaign.verify_interleaving(
        {"checkpoints": checkpoints, "steps_completed": 600},
        base_metrics(), scope_deviation.TERMINATION_GATE)
    assert all(c["gate"]["passed"] for c in out)
    assert all(not c["gate"]["recurrent_termination_checksums"] for c in out)


def test_checkpoint_gaps_are_refused(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    sv.run_final = lambda d, lr: stage_result(
        d, steps_completed=600,
        checkpoints=[checkpoint(100), checkpoint(300)])
    with pytest.raises(SystemExit, match="leading prefix"):
        campaign.run_campaign(sv, "camp-f5d")


def test_a_checkpoint_without_an_artifact_stops_the_campaign(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    cps = [checkpoint(st) for st in campaign.FINAL_CHECKPOINTS]
    cps[1]["artifact_sha256"] = None
    sv.run_final = lambda d, lr: stage_result(d, steps_completed=600,
                                              checkpoints=cps)
    with pytest.raises(SystemExit, match="produced no evaluation artifact"):
        campaign.run_campaign(sv, "camp-f6")


def test_a_result_echoing_the_wrong_descriptor_is_refused(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    ok = sv.run_sweep

    def tampered(d, lr):
        r = ok(d, lr)
        r["stage_descriptor_sha256"] = "0" * 64
        return r
    sv.run_sweep = tampered
    with pytest.raises(SystemExit, match="the instance ran something else"):
        campaign.run_campaign(sv, "camp-f6b")
    assert budget.unresolved(budget.load(s3)[0]) == [], \
        "a terminated instance must reconcile before semantic refusal"


@pytest.mark.parametrize("field,value", [
    ("root_volume_deleted", False), ("exit_status", 1),
    ("instance_id", None), ("terminated_utc", None),
])
def test_unproven_cleanup_or_failure_is_refused(db, field, value):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    ok = sv.run_base_and_preflight

    def bad(d):
        r = ok(d)
        r[field] = value
        return r
    sv.run_base_and_preflight = bad
    expected = (
        "termination is not proven"
        if field in ("instance_id", "terminated_utc")
        else "does not match what was")
    with pytest.raises(SystemExit, match=expected):
        campaign.run_campaign(sv, "camp-f6c")


def test_reconciliation_only_after_confirmed_termination(db):
    """An unproven termination keeps its conservative reservation."""
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    ok = sv.run_base_and_preflight

    def unproven(d):
        result = ok(d)
        result["aws_final_state"] = "running"
        return result

    sv.run_base_and_preflight = unproven
    with pytest.raises(SystemExit, match="termination is not proven"):
        campaign.run_campaign(sv, "camp-unproven")
    assert len(budget.unresolved(budget.load(s3)[0])) == 1


def test_no_automatic_relaunch_after_a_failure(db):
    """A failed stage must not be retried inside the campaign."""
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    sv.run_sweep = lambda d, lr: (_ for _ in ()).throw(
        RuntimeError("instance died"))
    with pytest.raises(RuntimeError):
        campaign.run_campaign(sv, "camp-f7")
    assert sum(1 for c in calls if c.startswith("run_sweep")) == 0
    src = (ROOT / "pipeline/campaign.py").read_text()
    assert "retry" not in src.lower() or "no automatic" in src.lower()


def test_a_registration_hook_is_refused_outright(db):
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    sv.register_model = lambda *a, **k: None
    with pytest.raises(SystemExit, match="non-promotable by construction"):
        campaign.run_campaign(sv, "camp-f8")
    assert calls == [], "nothing runs when registration is even possible"


def test_an_unresolved_reservation_blocks_the_next_launch(db):
    s3 = FakeS3()
    budget.reserve(s3, "final_run", "orphan")       # never reconciled
    sv, calls = make_services(s3, db)
    with pytest.raises(SystemExit, match="still unresolved"):
        campaign.run_campaign(sv, "camp-f9")
    assert "evaluate_base" not in calls


def test_a_crash_after_launch_still_counts_the_worst_case(db):
    """The reservation is durable before the instance exists, so a process that
    dies mid-stage leaves the full worst case committed."""
    s3 = FakeS3()
    budget.reserve(s3, "final_run", "crashed")
    ledger, _ = budget.load(s3)
    assert budget.committed_usd(ledger) == pytest.approx(
        budget.HISTORICAL_SPEND_USD + budget.worst_case_usd("final_run"))
    assert budget.unresolved(ledger) == [
        budget.reservation_id("final_run", "crashed")]


# --------------------------------------------------------------------------- #
# MLflow recovery after interruption
# --------------------------------------------------------------------------- #
def test_recovery_lists_every_completed_stage_after_interruption(db):
    s3 = FakeS3()

    sv, _ = make_services(s3, db)
    sv.run_final = lambda d, lr: (_ for _ in ()).throw(
        RuntimeError("instance interrupted"))
    with pytest.raises(RuntimeError):
        campaign.run_campaign(sv, "camp-r1")

    rec = mlflow_sync.recover(s3, "camp-r1", "1")
    assert rec["interrupted"] is False
    assert "parent" in rec["stage_names"]
    assert "base_and_preflight" in rec["stage_names"]
    assert "selection" in rec["stage_names"]
    assert rec["last_completed_stage"] == "campaign-failed"
    assert len(rec["last_snapshot_sha256"]) == 64


def test_recovery_of_an_unknown_campaign_is_empty_not_an_error():
    rec = mlflow_sync.recover(FakeS3(), "never-ran", "1")
    assert rec["stages"] == [] and rec["last_completed_stage"] is None


def test_snapshot_is_a_consistent_backup_not_a_file_copy(db, tmp_path):
    """Taken through SQLite's own backup API, so it opens as a valid db."""
    out = tmp_path / "snap.db"
    mlflow_sync.consistent_snapshot(db, out)
    con = sqlite3.connect(out)
    assert con.execute("select id from runs").fetchone()[0] == "r1"
    con.close()


def test_a_second_snapshot_of_the_same_stage_is_refused(db):
    s3 = FakeS3()
    mlflow_sync.sync(s3, db, "camp-x", "parent", attempt="1")
    with pytest.raises(SystemExit, match="write-once"):
        mlflow_sync.sync(s3, db, "camp-x", "parent", attempt="1")


def test_sync_verifies_by_readback(db):
    s3 = FakeS3()
    rec = mlflow_sync.sync(s3, db, "camp-y", "parent", attempt="1")
    stored = s3.objects[rec["key"].split("medzen-speech/", 1)[1]]
    assert hashlib.sha256(stored).hexdigest() == rec["sha256"]


def test_trace_entry_identity_cannot_be_overwritten_by_a_payload_key():
    """add("checkpoint-eval", step=300) must not turn the entry's own name into
    an integer -- which it did, making the trace a list of ints."""
    t = campaign.Trace()
    t.add("checkpoint-eval", step=300)
    assert t.names == ["checkpoint-eval"]
    assert t.steps[0]["step"] == 300
    with pytest.raises(TypeError, match="multiple values for argument 'name'"):
        t.add("x", name="y")


# --------------------------------------------------------------------------- #
# every new module has a production caller
# --------------------------------------------------------------------------- #
def _imports_of(path: Path) -> set[str]:
    """Modules imported by a file, via the AST.

    Substring matching cannot see the third name in
    `from pipeline import budget, mlflow_sync, orchestrate`.
    """
    import ast
    names = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.Import):
            names |= {a.name.split(".")[-1] for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            if n.module:
                names.add(n.module.split(".")[-1])
            names |= {a.name for a in n.names}
    return names


def test_every_new_module_has_a_production_caller():
    """A control that only tests call is not governing anything."""
    prod = list((ROOT / "pipeline").glob("*.py")) + \
        list((ROOT / "scripts").glob("*.py"))
    for mod in ("generation", "budget", "orchestrate", "smoke",
                "mlflow_sync", "campaign", "campaign_tracking",
                "ec2_stage_adapter", "validation_runner",
                "stage_descriptor"):
        callers = [p.name for p in prod
                   if p.name != f"{mod}.py" and mod in _imports_of(p)]
        assert callers, f"{mod} has no production caller"

    user_data = (ROOT / "pipeline/stage_userdata.sh").read_text()
    assert "pipeline.stage_runner" in user_data, \
        "the container stage runner is not on the EC2 execution path"


def test_there_is_exactly_one_launch_entrypoint():
    """A second launcher would be a second set of controls."""
    entry = ROOT / "scripts/run_campaign.py"
    assert entry.exists()
    src = entry.read_text()
    assert "campaign.run_campaign(" in src
    others = [p for p in (ROOT / "scripts").glob("*.py")
              if p.name != "run_campaign.py"
              and "campaign.run_campaign(" in p.read_text()]
    assert not others, f"alternate launch path: {[p.name for p in others]}"


def test_launcher_defaults_to_a_dry_run():
    src = (ROOT / "scripts/run_campaign.py").read_text()
    assert '"--confirm", action="store_true"' in src
    assert '"--validate-inputs", action="store_true"' in src
    assert "DRY RUN" in src
    i_confirm = src.index("if not a.confirm and not a.validate_inputs:")
    i_run = src.index("campaign.run_campaign(")
    assert i_confirm < i_run, "the dry-run check must precede execution"
    assert "require_clean_tree()" in src
    assert "preflight_campaign(" in src
    assert "writes_performed" in src


def test_launcher_dry_run_executes_and_reports_the_dynamic_topology():
    """Exercise main(): a missing topology import must fail before AWS does."""
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts/run_campaign.py"),
         "--campaign-run", "b4-scoped-dry-run",
         "--git-sha", "a" * 40],
        cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "topology      6 GPU instances" in out.stdout
    assert "1 sweep(s)" in out.stdout
    assert "DRY RUN" in out.stdout


def test_launcher_never_wires_a_registration_hook():
    src = (ROOT / "scripts/run_campaign.py").read_text()
    assert "register_model=None" in src


def test_preflight_has_no_standalone_launch_path():
    """It must not become an alternate entrypoint that skips the ordering."""
    src = (ROOT / "scripts/run_preflight.py").read_text()
    assert "no standalone launch path" in src
    assert "def run(" in src, "but it IS importable by the campaign"


def test_recovery_orders_stages_completed_within_the_same_second(db):
    """`utc` has second resolution; stages routinely finish inside one second.
    Sorting on it alone returned an arbitrary last-completed stage."""
    s3 = FakeS3()
    for stage in ("parent", "base_eval", "preflight", "final-checkpoint-100"):
        mlflow_sync.sync(s3, db, "camp-order", stage, attempt="1")
    rec = mlflow_sync.recover(s3, "camp-order", "1")
    assert rec["stage_names"] == ["parent", "base_eval", "preflight",
                                  "final-checkpoint-100"]
    assert rec["last_completed_stage"] == "final-checkpoint-100"
    assert all(s["seq_ns"] > 0 for s in rec["stages"])


def test_attempt_two_does_not_overwrite_attempt_one(db):
    """A retry must preserve why the first attempt failed."""
    s3 = FakeS3()
    a1 = mlflow_sync.sync(s3, db, "camp-a", "parent", attempt="1")
    a2 = mlflow_sync.sync(s3, db, "camp-a", "parent", attempt="2")
    assert a1["key"] != a2["key"]
    assert "attempt-1" in a1["key"] and "attempt-2" in a2["key"]
    r1 = mlflow_sync.recover(s3, "camp-a", "1")
    r2 = mlflow_sync.recover(s3, "camp-a", "2")
    assert r1["stage_names"] == ["parent"] and r2["stage_names"] == ["parent"]
    assert r1["last_snapshot_sha256"] and r2["last_snapshot_sha256"]


def test_attempt_cannot_collide_with_a_stage_name(db):
    """attempt-N is its own path segment, so 'parent' or 'base_eval' as an
    attempt value cannot land on another stage's key."""
    s3 = FakeS3()
    k_parent = mlflow_sync.snapshot_key("c", "1", "parent")
    k_weird = mlflow_sync.snapshot_key("c", "parent", "base_eval")
    assert k_parent != k_weird
    assert k_parent == "mlflow/snapshots/c/attempt-1/parent/mlflow.db"


def test_recovery_reads_immutable_records_not_a_mutable_index(db):
    """A lost index append must not erase a stage that really happened."""
    s3 = FakeS3()
    mlflow_sync.sync(s3, db, "camp-i", "parent", attempt="1")
    assert not any(k.endswith("index.jsonl") for k in s3.objects)
    assert any(k.endswith("/record.json") for k in s3.objects)
    rec = mlflow_sync.recover(s3, "camp-i", "1")
    assert rec["stage_names"] == ["parent"]


def test_confirm_with_placeholder_services_mutates_nothing(db):
    """The safety property: an incomplete wiring must not reserve or write."""
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    sv.run_sweep = campaign.placeholder("stage adapter not implemented")
    with pytest.raises(SystemExit, match="not production-ready"):
        campaign.run_campaign(sv, "camp-pl")
    assert s3.objects == {}, "no S3 object written"
    assert calls == [], "no service invoked"


@pytest.mark.parametrize("field,value", [
    ("mlflow_db", None), ("tracker", None), ("launcher", None),
    ("image_digest", None),
    ("stage_descriptors", None),
])
def test_incomplete_wiring_refuses_before_any_mutation(db, field, value):
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    setattr(sv, field, value)
    with pytest.raises(SystemExit, match="not production-ready"):
        campaign.run_campaign(sv, "camp-nr")
    assert s3.objects == {} and calls == []


def test_readiness_names_every_problem_at_once(db):
    sv, _ = make_services(FakeS3(), db)
    sv.run_final = campaign.placeholder("stub")
    sv.launcher = None
    r = campaign.readiness(sv)
    assert r["ready"] is False
    assert any("placeholder" in p for p in r["problems"])
    assert any("launcher" in p for p in r["problems"])


@pytest.mark.parametrize("bad", [
    {"status": "draft"},
    {"current_complete_raw_sha256": "z" * 64},
    {"manifests_verified": False},
    {"exclusion_checksums_sha256": "z" * 64},
    {"dataset_fingerprint": "z" * 64},
    {"eligible_rows": 4600},
])
def test_adoption_mismatch_stops_before_any_reservation(db, bad):
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    good = sv.verify_adoption()
    sv.verify_adoption = lambda: {**good, **bad}
    with pytest.raises(SystemExit, match="adoption verification failed"):
        campaign.run_campaign(sv, "camp-adopt")
    assert budget.load(s3)[0]["reservations"] == {}
    assert "evaluate_base" not in calls
