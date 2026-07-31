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
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import budget, campaign, mlflow_sync, orchestrate   # noqa: E402

LANGS = orchestrate.VALIDATION_LANGUAGES
POLICY_SHA = "a" * 64


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

    def list_objects_v2(self, Bucket, Prefix, MaxKeys=None):
        n = sum(1 for k in self.objects if k.startswith(Prefix))
        return {"KeyCount": n}


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


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "mlflow.db"
    con = sqlite3.connect(p)
    con.execute("create table runs (id text)")
    con.execute("insert into runs values ('r1')")
    con.commit()
    con.close()
    return p


def make_services(s3, db, **over):
    calls = []

    def verify_policy():
        calls.append("verify_policy")
        return {"policy_sha256": POLICY_SHA, "rows": 19,
                "human_review_performed": False}

    def verify_adoption():
        calls.append("verify_adoption")
        return {"deferral_policy_sha256": POLICY_SHA,
                "complete_raw_sha256": "b" * 64}

    def run_preflight():
        calls.append("preflight")
        return {"passed": True, "overfit": {"passed": True},
                "adapter_effect": {"passed": True},
                "generation_smoke": {"passed": True}}

    def evaluate_base(campaign_run):
        calls.append("evaluate_base")
        return {"wer": base_metrics(), "base_arm_key": "k" * 64,
                "artifact_sha256": "c" * 64, "seconds": 300}

    def train(lr, steps, resume, seed, campaign_run, tag):
        calls.append(f"train:{tag}")
        assert resume is None, "the final run must start from scratch"
        return {"run_id": f"run-{tag}", "seconds": 600}

    def evaluate_checkpoint(run_id, step, campaign_run):
        calls.append(f"eval:{run_id}:{step}")
        return {"wer": metrics(), "eos_rate": perfect(),
                "cap_hit_rate": zeros(),
                "artifact_sha256": hashlib.sha256(
                    f"{run_id}-{step}".encode()).hexdigest()}

    sv = campaign.Services(
        s3=s3, verify_policy=verify_policy, verify_adoption=verify_adoption,
        run_preflight=run_preflight, evaluate_base=evaluate_base, train=train,
        evaluate_checkpoint=evaluate_checkpoint, mlflow_db=db)
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
    assert order[:6] == ["verify-policy", "verify-adoption", "budget-reserve",
                         "base-eval", "preflight", "budget-reserve"]

    # the required sequence, each strictly before the next
    def idx(pred):
        return next(i for i, n in enumerate(names) if pred(n))
    assert idx(lambda n: n == "verify-policy") < idx(lambda n: n == "verify-adoption")
    assert idx(lambda n: n == "verify-adoption") < idx(lambda n: n == "budget-reserve")
    assert idx(lambda n: n == "budget-reserve") < idx(lambda n: n == "base-eval")
    assert idx(lambda n: n == "base-eval") < idx(lambda n: n == "preflight")
    assert idx(lambda n: n == "preflight") < idx(lambda n: n == "sweep-run")
    assert idx(lambda n: n == "sweep-run") < idx(lambda n: n == "select-lr")
    assert idx(lambda n: n == "select-lr") < idx(lambda n: n == "final-run")
    assert idx(lambda n: n == "final-run") < idx(lambda n: n == "checkpoint-eval")
    assert idx(lambda n: n == "checkpoint-eval") < idx(lambda n: n == "cleanup")

    assert names.count("sweep-run") == 3
    assert names.count("checkpoint-eval") == len(campaign.FINAL_CHECKPOINTS)
    assert out["registered_models"] == 0 and out["promotable"] is False
    assert out["purpose"] == "training_system_validation"


def test_base_arm_is_evaluated_before_any_training(db):
    s3 = FakeS3()
    sv, calls = make_services(s3, db)
    campaign.run_campaign(sv, "camp-2")
    assert calls.index("evaluate_base") < calls.index("preflight")
    assert calls.index("evaluate_base") < min(
        i for i, c in enumerate(calls) if c.startswith("train:"))


def test_every_checkpoint_produces_a_unique_artifact_and_snapshot(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    out = campaign.run_campaign(sv, "camp-3")
    shas = [c["artifact_sha256"] for c in out["checkpoints"]]
    assert len(set(shas)) == len(campaign.FINAL_CHECKPOINTS)
    prefixes = [c["prefix"] for c in out["checkpoints"]]
    assert len(set(prefixes)) == len(prefixes)
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
    assert not any(c.startswith("train:") for c in calls)


def test_adoption_bound_to_a_different_policy_stops_before_spending(db):
    s3 = FakeS3()
    sv, calls = make_services(
        s3, db, verify_adoption=lambda: {"deferral_policy_sha256": "z" * 64,
                                         "complete_raw_sha256": "b" * 64})
    with pytest.raises(SystemExit, match="different deferral policy"):
        campaign.run_campaign(sv, "camp-f2")
    assert budget.load(s3)[0]["reservations"] == {}
    assert "evaluate_base" not in calls


def test_failed_preflight_prevents_the_lr_sweep(db):
    s3 = FakeS3()
    sv, calls = make_services(
        s3, db, run_preflight=lambda: {"passed": False,
                                       "reasons": ["no EOS emitted"]})
    with pytest.raises(SystemExit, match="no learning-rate sweep will start"):
        campaign.run_campaign(sv, "camp-f3")
    assert not any(c.startswith("train:") for c in calls)


def test_all_candidates_failing_gates_prevents_the_final_run(db):
    s3 = FakeS3()

    def bad_eval(run_id, step, campaign_run):
        return {"wer": metrics(shona=0.99), "eos_rate": perfect(),
                "cap_hit_rate": zeros(),
                "artifact_sha256": hashlib.sha256(
                    f"{run_id}{step}".encode()).hexdigest()}

    sv, calls = make_services(s3, db, evaluate_checkpoint=bad_eval)
    with pytest.raises(SystemExit, match="no learning rate passed all four"):
        campaign.run_campaign(sv, "camp-f4")
    assert "train:final" not in calls


def test_a_failing_checkpoint_stops_the_remaining_checkpoints(db):
    s3 = FakeS3()
    seen = []

    def eval_cp(run_id, step, campaign_run):
        seen.append(step)
        wer = metrics() if step < 300 else metrics(oromo=0.99)
        return {"wer": wer, "eos_rate": perfect(), "cap_hit_rate": zeros(),
                "artifact_sha256": hashlib.sha256(
                    f"{run_id}{step}".encode()).hexdigest()}

    sv, _ = make_services(s3, db, evaluate_checkpoint=eval_cp)
    with pytest.raises(SystemExit, match="checkpoint-300 failed a gate"):
        campaign.run_campaign(sv, "camp-f5")
    assert 400 not in seen, "later checkpoints must not run after a failure"


def test_a_checkpoint_without_an_artifact_stops_the_campaign(db):
    s3 = FakeS3()

    def no_artifact(run_id, step, campaign_run):
        base = {"wer": metrics(), "eos_rate": perfect(), "cap_hit_rate": zeros()}
        if run_id == "run-final" and step == 200:
            return {**base, "artifact_sha256": None}
        return {**base, "artifact_sha256": hashlib.sha256(
            f"{run_id}{step}".encode()).hexdigest()}

    sv, _ = make_services(s3, db, evaluate_checkpoint=no_artifact)
    with pytest.raises(SystemExit, match="produced no evaluation artifact"):
        campaign.run_campaign(sv, "camp-f6")


def test_an_occupied_checkpoint_prefix_stops_the_campaign(db):
    s3 = FakeS3()
    sv, _ = make_services(s3, db)
    s3.objects["candidates/evaluations/run-final/checkpoint-100/x.json"] = b"{}"
    with pytest.raises(SystemExit, match="already contains objects"):
        campaign.run_campaign(sv, "camp-f7")


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
    assert budget.committed_usd(ledger) == budget.worst_case_usd("final_run")
    assert budget.unresolved(ledger) == [
        budget.reservation_id("final_run", "crashed")]


# --------------------------------------------------------------------------- #
# MLflow recovery after interruption
# --------------------------------------------------------------------------- #
def test_recovery_lists_every_completed_stage_after_interruption(db):
    s3 = FakeS3()

    def stop_at_300(run_id, step, campaign_run):
        if run_id == "run-final" and step == 300:
            raise RuntimeError("instance interrupted")
        return {"wer": metrics(), "eos_rate": perfect(), "cap_hit_rate": zeros(),
                "artifact_sha256": hashlib.sha256(
                    f"{run_id}{step}".encode()).hexdigest()}

    sv, _ = make_services(s3, db, evaluate_checkpoint=stop_at_300)
    with pytest.raises(RuntimeError):
        campaign.run_campaign(sv, "camp-r1")

    rec = mlflow_sync.recover(s3, "camp-r1")
    assert rec["interrupted"] is True
    assert "parent" in rec["stage_names"] and "base_eval" in rec["stage_names"]
    assert rec["last_completed_stage"] == "final-checkpoint-200"
    assert len(rec["last_snapshot_sha256"]) == 64


def test_recovery_of_an_unknown_campaign_is_empty_not_an_error():
    rec = mlflow_sync.recover(FakeS3(), "never-ran")
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
    mlflow_sync.sync(s3, db, "camp-x", "parent")
    with pytest.raises(SystemExit, match="write-once"):
        mlflow_sync.sync(s3, db, "camp-x", "parent")


def test_sync_verifies_by_readback(db):
    s3 = FakeS3()
    rec = mlflow_sync.sync(s3, db, "camp-y", "parent")
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
                "mlflow_sync", "campaign"):
        callers = [p.name for p in prod
                   if p.name != f"{mod}.py" and mod in _imports_of(p)]
        assert callers, f"{mod} has no production caller"


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
    assert "DRY RUN" in src
    i_confirm = src.index("if not a.confirm:")
    i_run = src.index("campaign.run_campaign(")
    assert i_confirm < i_run, "the dry-run check must precede execution"


def test_launcher_never_wires_a_registration_hook():
    src = (ROOT / "scripts/run_campaign.py").read_text()
    assert "register_model=None" in src


def test_preflight_has_no_standalone_launch_path():
    """It must not become an alternate entrypoint that skips the ordering."""
    src = (ROOT / "scripts/run_preflight.py").read_text()
    assert "no standalone launch path" in src
    assert "def run(" in src, "but it IS importable by the campaign"
