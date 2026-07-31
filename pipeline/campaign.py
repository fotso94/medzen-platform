"""THE entrypoint for the Option B campaign. There is no other launch path.

Every control lives on this path: policy and adoption verification, budget
reservation, base arm first, overfit and smoke, three sequential LR runs, all
four gates, deterministic selection, a final run from scratch, write-once
prefixes, immutable MLflow snapshots, and zero model registration.

The services it needs -- S3, the trainer, the evaluator, MLflow sync -- arrive
as a `Services` object. That is what makes the ordering testable end to end
without an AWS account or a GPU: the dry-run test passes fakes and asserts the
sequence, and the same code runs in production with the real ones. A second
launcher that skipped a step would be a second set of controls; there isn't one.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from pipeline import budget, mlflow_sync, orchestrate, stage_descriptor

FINAL_CHECKPOINTS = (100, 200, 300, 400, 500, 600)

# The corrected corpus. Anything else is a different experiment.
EXPECTED_FINGERPRINT = \
    "ad8c63d157419cbdbadc1d6a2cf8790c0766d76b848152dbd1be4a1373288275"
EXPECTED_ELIGIBLE_ROWS = 4601


class CampaignError(SystemExit):
    """Stops the campaign. Carries the boundary that refused."""


PLACEHOLDER = "__placeholder__"


def placeholder(reason: str) -> Callable[..., Any]:
    """A service that is not implemented yet, and says so by construction.

    Marked so `readiness()` can SEE it. A callable that merely raises when
    invoked is discovered only after budget has been reserved and S3 written --
    which is exactly the wrong time to find out.
    """
    def _refuse(*a, **kw):
        raise CampaignError(f"REFUSING: {reason}")
    _refuse.__medzen_placeholder__ = reason          # type: ignore[attr-defined]
    return _refuse


def is_placeholder(fn: Any) -> bool:
    return getattr(fn, "__medzen_placeholder__", None) is not None


@dataclass
class Services:
    s3: Any
    verify_policy: Callable[[], dict]
    verify_adoption: Callable[[], dict]
    # STAGE-SHAPED. Each is one complete g6.xlarge lifecycle: launch, run,
    # verify, terminate, prove cleanup. The operator never orchestrates
    # individual operations, because "evaluate_base" and "train" as separate
    # callbacks made the instance count unknowable -- five stage calls is five
    # instances, and that is checkable.
    run_base_and_preflight: Callable[[dict], dict]
    run_sweep: Callable[[dict, float], dict]
    run_final: Callable[[dict, float], dict]
    mlflow_db: Any = None
    launcher: Any = None                       # the EC2 adapter
    image_digest: str | None = None
    stage_descriptors: dict | None = None
    register_model: Callable[..., Any] | None = None   # must stay None

    SERVICE_FIELDS = ("verify_policy", "verify_adoption",
                      "run_base_and_preflight", "run_sweep", "run_final")


def readiness(sv: Services) -> dict:
    """Is this wiring complete enough to spend money? Checked BEFORE anything.

    The previous version would verify governance, reserve budget and write to
    S3, and only then discover that `train` was a stub. Reserving against a
    campaign that cannot run leaves a dangling commitment and an S3 object for
    a run that never happened.
    """
    missing = [f for f in Services.SERVICE_FIELDS
               if is_placeholder(getattr(sv, f))]
    problems = []
    if missing:
        problems.append(
            "placeholder service(s): " + ", ".join(
                f"{f} ({getattr(getattr(sv, f), '__medzen_placeholder__')})"
                for f in missing))
    if sv.mlflow_db is None:
        problems.append("mlflow_db is absent; no run evidence could be recorded")
    if sv.launcher is None:
        problems.append("no AWS launcher configured; stages have nowhere to run")
    if not sv.image_digest:
        problems.append("image digest missing; the artifact identity is unknown")
    if not sv.stage_descriptors:
        problems.append("immutable stage descriptors missing")
    if sv.register_model is not None:
        problems.append("a model registration hook is present; Option B is "
                        "non-promotable by construction")
    return {"ready": not problems, "problems": problems,
            "placeholder_services": missing}


def require_ready(sv: Services) -> dict:
    r = readiness(sv)
    if not r["ready"]:
        raise CampaignError(
            "REFUSING: the campaign is not production-ready, so nothing has "
            "been reserved, read or written:\n  " + "\n  ".join(r["problems"]))
    return r


@dataclass
class Trace:
    """The ordered record of what actually happened."""
    steps: list[dict] = field(default_factory=list)

    def add(self, name: str, **kw) -> None:
        # "name", not "step": a caller logging step=300 for a checkpoint would
        # otherwise overwrite the entry's own identity, and the trace would
        # silently become a list of integers.
        # A duplicate `name=` is caught by Python itself with TypeError, so no
        # guard is written for it here -- one would be unreachable.
        self.steps.append({"name": name,
                           "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           **kw})

    @property
    def names(self) -> list[str]:
        return [s["name"] for s in self.steps]


def _sync(sv: Services, campaign_run: str, stage: str, trace: Trace,
          attempt: str = "1", **extra) -> None:
    """Sync failure is fatal: a stage whose evidence did not persist has not
    happened, and the next stage must not build on it."""
    if sv.mlflow_db is None:
        trace.add(f"mlflow-sync:{stage}", skipped="no tracking db")
        return
    rec = mlflow_sync.sync(sv.s3, sv.mlflow_db, campaign_run, stage,
                           attempt=attempt, extra=extra)
    trace.add(f"mlflow-sync:{stage}", sha256=rec["sha256"], key=rec["key"])


def make_descriptor(sv: Services, campaign_run: str, attempt: str, stage: str,
                    lr: float | None, max_steps: int,
                    reservation_id: str) -> dict:
    """One descriptor per stage, built from the pins the campaign already holds."""
    p = sv.stage_descriptors or {}
    return stage_descriptor.build(
        campaign_run=campaign_run, attempt=attempt, stage=stage,
        git_sha=p["git_sha"], bundle_tar_sha256=p["bundle_tar_sha256"],
        image_digest=sv.image_digest,
        policy_sha256=p["policy_sha256"], adoption_key=p["adoption_key"],
        dataset_fingerprint=EXPECTED_FINGERPRINT,
        base_manifest_sha256=p["base_manifest_sha256"],
        validation_manifest_sha256=p["validation_manifest_sha256"],
        generation_config_fingerprint=p["generation_config_fingerprint"],
        evaluator_sha256=p["evaluator_sha256"],
        lr=lr, seed=orchestrate.SEED, max_steps=max_steps,
        checkpoint_steps=list(FINAL_CHECKPOINTS) if stage == "final" else [],
        reservation_id=reservation_id,
        watchdog_s=budget.WATCHDOG_S[
            "base_and_preflight" if stage == "base_and_preflight"
            else "sweep_run" if stage == "sweep" else "final_run"],
        input_prefix=f"curated/_versions/v2/",
        output_prefix=f"candidates/evaluations/{campaign_run}/attempt-{attempt}/{stage}/",
        mlflow_parent_run_id=p["mlflow_parent_run_id"],
        mlflow_child_run_id=f"{campaign_run}-{attempt}-{stage}"
                            + (f"-lr{lr:.0e}" if lr else ""),
        purpose="training_system_validation", promotable=False)


def verify_interleaving(result: dict, base_wer: dict) -> list[dict]:
    """Prove the final run GATED as it trained, rather than after.

    The contract the container must satisfy: checkpoints appear in ascending
    order with no gaps, and if one fails there are NO checkpoints after it and
    `steps_completed` stops at that boundary. Evaluating all 600 steps and then
    reporting a failure at 300 would satisfy a naive reading of "checkpoint 300
    failed" while having spent the whole budget -- and, worse, produced 300
    optimisation steps against an objective already known to be broken.
    """
    cps = result.get("checkpoints") or []
    if not cps:
        raise CampaignError("REFUSING: the final stage reported no checkpoints")

    steps = [c["step"] for c in cps]
    if steps != sorted(steps):
        raise CampaignError(f"REFUSING: checkpoints out of order: {steps}")
    expected = list(FINAL_CHECKPOINTS)[:len(steps)]
    if steps != expected:
        raise CampaignError(
            f"REFUSING: checkpoint steps {steps} are not the leading prefix of "
            f"{list(FINAL_CHECKPOINTS)}; a gap means a boundary was skipped")

    out = []
    for c in cps:
        if not c.get("artifact_sha256"):
            raise CampaignError(
                f"REFUSING: checkpoint-{c['step']} produced no evaluation "
                "artifact")
        gate = orchestrate.evaluate_gates(c["wer"], base_wer, c["eos_rate"],
                                          c["cap_hit_rate"])
        out.append({**c, "gate": gate})

    failed = [c for c in out if not c["gate"]["passed"]]
    if failed:
        first = failed[0]["step"]
        later = [c["step"] for c in out if c["step"] > first]
        if later:
            raise CampaignError(
                f"REFUSING: checkpoint-{first} failed but the stage also "
                f"reported {later}. Training must STOP at a failing gate, not "
                "continue and report the failure afterwards.")
        done = result.get("steps_completed")
        if done is None or done > first:
            raise CampaignError(
                f"REFUSING: checkpoint-{first} failed but the stage reports "
                f"steps_completed={done}. Optimisation step {first + 1} must "
                "never have run.")
    elif result.get("steps_completed") != FINAL_CHECKPOINTS[-1]:
        raise CampaignError(
            f"REFUSING: all gates passed but steps_completed is "
            f"{result.get('steps_completed')}, not {FINAL_CHECKPOINTS[-1]}")
    return out


def run_campaign(sv: Services, campaign_run: str, attempt: str = "1") -> dict:
    """The whole ordering, enforced. Returns the trace and the outcome."""
    trace = Trace()

    # ---- 0. readiness BEFORE any read, write or reservation ---------------
    require_ready(sv)
    trace.add("readiness", ready=True)

    # ---- 1. governance BEFORE anything costs money ------------------------
    pol = sv.verify_policy()
    if pol.get("human_review_performed") is not False:
        raise CampaignError("REFUSING: policy does not record "
                            "human_review_performed=false")
    trace.add("verify-policy", policy_sha256=pol["policy_sha256"],
              rows=pol["rows"])

    adopt = sv.verify_adoption()
    problems = []
    if adopt.get("status") != "approved":
        problems.append(f"adoption status is {adopt.get('status')!r}")
    if adopt.get("deferral_policy_sha256") != pol["policy_sha256"]:
        problems.append("adoption binds a different deferral policy; approval "
                        "does not transfer between policies")
    if adopt.get("complete_raw_sha256") != adopt.get("current_complete_raw_sha256"):
        problems.append(
            f"COMPLETE.json now hashes "
            f"{str(adopt.get('current_complete_raw_sha256'))[:16]} but the "
            f"adoption approved {str(adopt.get('complete_raw_sha256'))[:16]}; "
            "the corpus changed after it was adopted")
    if adopt.get("manifests_verified") is not True:
        problems.append("manifest versions/hashes were not verified against "
                        "the completion record")
    if adopt.get("exclusion_checksums_sha256") != pol.get("exclusion_checksums_sha256"):
        problems.append("the adopted exclusion checksum set is not the policy's")
    if adopt.get("dataset_fingerprint") != EXPECTED_FINGERPRINT:
        problems.append(
            f"dataset fingerprint {str(adopt.get('dataset_fingerprint'))[:16]} "
            f"is not the corrected {EXPECTED_FINGERPRINT[:16]}")
    if adopt.get("eligible_rows") != EXPECTED_ELIGIBLE_ROWS:
        problems.append(f"eligible rows {adopt.get('eligible_rows')} != "
                        f"{EXPECTED_ELIGIBLE_ROWS}")
    if problems:
        raise CampaignError("REFUSING: adoption verification failed, so "
                            "nothing has been reserved:\n  "
                            + "\n  ".join(problems))
    trace.add("verify-adoption",
              complete_raw_sha256=adopt["complete_raw_sha256"],
              dataset_fingerprint=adopt["dataset_fingerprint"],
              eligible_rows=adopt["eligible_rows"])

    # ---- 2. reserve the worst case BEFORE the first instance --------------
    # base evaluation and preflight SHARE one GPU instance: both need the
    # pinned base loaded, and preflight builds a fresh LoRA on top of it.
    # One instance, one reservation, one lifecycle.
    res = budget.reserve(sv.s3, "base_and_preflight", f"{attempt}")
    trace.add("budget-reserve", stage="base_and_preflight",
              reservation_id=res["reservation_id"],
              worst_case_usd=res["worst_case_usd"])

    _sync(sv, campaign_run, "parent", trace, attempt=attempt, policy_sha256=pol["policy_sha256"])

    # ---- 3. ONE instance: base arm, then preflight ------------------------
    d0 = make_descriptor(sv, campaign_run, attempt, "base_and_preflight",
                         lr=None, max_steps=0,
                         reservation_id=res["reservation_id"])
    r0 = sv.run_base_and_preflight(d0)
    stage_descriptor.verify_result(d0, r0)
    base = r0["base"]
    orchestrate.validate_metric_map(base["wer"], "base WER")
    if not r0["preflight"].get("passed"):
        raise CampaignError(
            "REFUSING: training preflight failed; no learning-rate sweep will "
            f"start.\n  {r0['preflight'].get('reasons')}")
    trace.add("base-and-preflight", instance_id=r0["instance_id"],
              base_arm_key=base["base_arm_key"],
              base_artifact_sha256=base["artifact_sha256"],
              preflight_passed=True,
              root_volume_deleted=r0["root_volume_deleted"])
    budget.reconcile(sv.s3, "base_and_preflight", attempt,
                     r0["actual_seconds"], instance_id=r0["instance_id"])
    _sync(sv, campaign_run, "base_and_preflight", trace, attempt=attempt,
          artifact_sha256=base["artifact_sha256"])

    # ---- 4. three sequential sweep instances ------------------------------
    results = []
    for lr in orchestrate.LR_CANDIDATES:
        tag = f"lr-{lr:.0e}"
        rr = budget.reserve(sv.s3, "sweep_run", f"{attempt}-{tag}")
        trace.add("budget-reserve", stage="sweep_run", lr=lr,
                  reservation_id=rr["reservation_id"])
        d = make_descriptor(sv, campaign_run, attempt, "sweep", lr=lr,
                            max_steps=orchestrate.SWEEP_STEPS,
                            reservation_id=rr["reservation_id"])
        r = sv.run_sweep(d, lr)
        stage_descriptor.verify_result(d, r)
        gate = orchestrate.evaluate_gates(r["wer"], base["wer"],
                                          r["eos_rate"], r["cap_hit_rate"])
        results.append({"lr": lr, "gate": gate})
        trace.add("sweep-run", lr=lr, instance_id=r["instance_id"],
                  macro_wer=gate["macro_wer"], passed=gate["passed"],
                  artifact_sha256=r["artifact_sha256"])
        budget.reconcile(sv.s3, "sweep_run", f"{attempt}-{tag}",
                         r["actual_seconds"], instance_id=r["instance_id"])
        _sync(sv, campaign_run, f"sweep-{tag}", trace, attempt=attempt,
              artifact_sha256=r["artifact_sha256"])

    # ---- 5. deterministic selection ---------------------------------------
    sel = orchestrate.select_lr(results)
    trace.add("select-lr", **{k: sel[k] for k in
                              ("selected_lr", "macro_wer", "tie_broken")})
    _sync(sv, campaign_run, "selection", trace, attempt=attempt,
          selected_lr=sel["selected_lr"])

    # ---- 6. ONE final instance, gates INTERLEAVED with training -----------
    if orchestrate.FINAL_RUN_RESUMES_SWEEP:
        raise CampaignError("REFUSING: the final run must not resume the sweep")
    rf = budget.reserve(sv.s3, "final_run", f"{attempt}-final")
    trace.add("budget-reserve", stage="final_run",
              reservation_id=rf["reservation_id"])
    df = make_descriptor(sv, campaign_run, attempt, "final",
                         lr=sel["selected_lr"], max_steps=600,
                         reservation_id=rf["reservation_id"])
    rfin = sv.run_final(df, sel["selected_lr"])
    stage_descriptor.verify_result(df, rfin)
    trace.add("final-run", instance_id=rfin["instance_id"],
              lr=sel["selected_lr"], resumed=False,
              steps_completed=rfin.get("steps_completed"),
              root_volume_deleted=rfin["root_volume_deleted"])
    _sync(sv, campaign_run, "final-start", trace, attempt=attempt,
          instance=rfin["instance_id"])

    checkpoints = verify_interleaving(rfin, base["wer"])
    for c in checkpoints:
        trace.add("checkpoint-eval", step=c["step"], passed=c["gate"]["passed"],
                  artifact_sha256=c["artifact_sha256"])
        _sync(sv, campaign_run, f"final-checkpoint-{c['step']}", trace,
              attempt=attempt, artifact_sha256=c["artifact_sha256"])

    budget.reconcile(sv.s3, "final_run", f"{attempt}-final",
                     rfin["actual_seconds"], instance_id=rfin["instance_id"])

    failed = [c for c in checkpoints if not c["gate"]["passed"]]
    _sync(sv, campaign_run, "final-complete" if not failed else "final-failed",
          trace, attempt=attempt)
    ledger, _ = budget.load(sv.s3)
    trace.add("cleanup", unresolved_reservations=len(budget.unresolved(ledger)),
              gpu_instances_used=5)
    if failed:
        raise CampaignError(
            f"REFUSING: checkpoint-{failed[0]['step']} failed a gate and "
            "training stopped there:\n  "
            + "\n  ".join(failed[0]["gate"]["failures"]))

    return {
        "campaign_run": campaign_run,
        "selected_lr": sel["selected_lr"],
        "checkpoints": checkpoints,
        "gpu_instances": 5,
        "registered_models": 0,
        "promotable": False,
        "purpose": "training_system_validation",
        "trace": trace.steps,
        "trace_names": trace.names,
    }
