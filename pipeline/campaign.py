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

from pipeline import budget, mlflow_sync, orchestrate

FINAL_CHECKPOINTS = (100, 200, 300, 400, 500, 600)


class CampaignError(SystemExit):
    """Stops the campaign. Carries the boundary that refused."""


@dataclass
class Services:
    s3: Any
    verify_policy: Callable[[], dict]
    verify_adoption: Callable[[], dict]
    run_preflight: Callable[[], dict]          # overfit + smoke, on a GPU box
    evaluate_base: Callable[[str], dict]       # -> per-language metrics
    train: Callable[..., dict]                 # lr, steps, resume -> run info
    evaluate_checkpoint: Callable[..., dict]   # -> metrics + artifact sha256
    mlflow_db: Any = None
    register_model: Callable[..., Any] | None = None   # must stay None


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
          **extra) -> None:
    """Sync failure is fatal: a stage whose evidence did not persist has not
    happened, and the next stage must not build on it."""
    if sv.mlflow_db is None:
        trace.add(f"mlflow-sync:{stage}", skipped="no tracking db")
        return
    rec = mlflow_sync.sync(sv.s3, sv.mlflow_db, campaign_run, stage, extra=extra)
    trace.add(f"mlflow-sync:{stage}", sha256=rec["sha256"], key=rec["key"])


def run_campaign(sv: Services, campaign_run: str, attempt: str = "1") -> dict:
    """The whole ordering, enforced. Returns the trace and the outcome."""
    trace = Trace()

    if sv.register_model is not None:
        raise CampaignError(
            "REFUSING: a model registration hook is present. Option B is "
            "non-promotable by construction and must reach zero registered "
            "models.")

    # ---- 1. governance BEFORE anything costs money ------------------------
    pol = sv.verify_policy()
    if pol.get("human_review_performed") is not False:
        raise CampaignError("REFUSING: policy does not record "
                            "human_review_performed=false")
    trace.add("verify-policy", policy_sha256=pol["policy_sha256"],
              rows=pol["rows"])

    adopt = sv.verify_adoption()
    if adopt.get("deferral_policy_sha256") != pol["policy_sha256"]:
        raise CampaignError(
            "REFUSING: the adoption record binds a different deferral policy. "
            "Approval does not transfer between policies.")
    trace.add("verify-adoption", complete_raw_sha256=adopt["complete_raw_sha256"])

    # ---- 2. reserve the worst case BEFORE the first instance --------------
    res = budget.reserve(sv.s3, "base_eval", f"{attempt}")
    trace.add("budget-reserve", stage="base_eval",
              reservation_id=res["reservation_id"],
              worst_case_usd=res["worst_case_usd"])

    _sync(sv, campaign_run, "parent", trace, policy_sha256=pol["policy_sha256"])

    # ---- 3. base arm FIRST, before any candidate exists -------------------
    base = sv.evaluate_base(campaign_run)
    orchestrate.validate_metric_map(base["wer"], "base WER")
    trace.add("base-eval", base_arm_key=base["base_arm_key"],
              artifact_sha256=base["artifact_sha256"])
    budget.reconcile(sv.s3, "base_eval", attempt, base.get("seconds", 0.0))
    _sync(sv, campaign_run, "base_eval", trace,
          artifact_sha256=base["artifact_sha256"])

    # ---- 4. preflight: overfit + smoke, before ANY sweep -------------------
    pf = sv.run_preflight()
    if not pf.get("passed"):
        raise CampaignError(
            "REFUSING: training preflight failed; no learning-rate sweep will "
            f"start.\n  {pf.get('reasons')}")
    trace.add("preflight", overfit=pf["overfit"], adapter_effect=pf["adapter_effect"],
              generation_smoke=pf["generation_smoke"])

    # ---- 5. three sequential LR candidates --------------------------------
    results = []
    for lr in orchestrate.LR_CANDIDATES:
        tag = f"lr-{lr:.0e}"
        r = budget.reserve(sv.s3, "sweep_run", f"{attempt}-{tag}")
        trace.add("budget-reserve", stage="sweep_run", lr=lr,
                  reservation_id=r["reservation_id"])
        run = sv.train(lr=lr, steps=orchestrate.SWEEP_STEPS, resume=None,
                       seed=orchestrate.SEED, campaign_run=campaign_run, tag=tag)
        ev = sv.evaluate_checkpoint(run_id=run["run_id"],
                                    step=orchestrate.SWEEP_COMPARISON_CHECKPOINT,
                                    campaign_run=campaign_run)
        gate = orchestrate.evaluate_gates(ev["wer"], base["wer"],
                                          ev["eos_rate"], ev["cap_hit_rate"])
        results.append({"lr": lr, "gate": gate})
        trace.add("sweep-run", lr=lr, macro_wer=gate["macro_wer"],
                  passed=gate["passed"], artifact_sha256=ev["artifact_sha256"])
        budget.reconcile(sv.s3, "sweep_run", f"{attempt}-{tag}",
                         run.get("seconds", 0.0))
        _sync(sv, campaign_run, f"sweep-{tag}", trace,
              artifact_sha256=ev["artifact_sha256"])

    # ---- 6. deterministic selection ---------------------------------------
    sel = orchestrate.select_lr(results)
    trace.add("select-lr", **{k: sel[k] for k in
                              ("selected_lr", "macro_wer", "tie_broken")})
    _sync(sv, campaign_run, "selection", trace, selected_lr=sel["selected_lr"])

    # ---- 7. final run FROM SCRATCH ----------------------------------------
    if orchestrate.FINAL_RUN_RESUMES_SWEEP:
        raise CampaignError("REFUSING: the final run must not resume the sweep")
    r = budget.reserve(sv.s3, "final_run", f"{attempt}-final")
    trace.add("budget-reserve", stage="final_run",
              reservation_id=r["reservation_id"])
    final = sv.train(lr=sel["selected_lr"], steps=600, resume=None,
                     seed=orchestrate.SEED, campaign_run=campaign_run,
                     tag="final")
    trace.add("final-run", run_id=final["run_id"], resumed=False)
    _sync(sv, campaign_run, "final-start", trace, run_id=final["run_id"])

    # ---- 8. every promised checkpoint, or the campaign stops --------------
    checkpoints = []
    for step in FINAL_CHECKPOINTS:
        prefix = orchestrate.evaluation_prefix(final["run_id"], step)
        orchestrate.require_absent(sv.s3, budget.BUCKET, prefix)   # write-once
        ev = sv.evaluate_checkpoint(run_id=final["run_id"], step=step,
                                    campaign_run=campaign_run)
        if not ev.get("artifact_sha256"):
            raise CampaignError(
                f"REFUSING: checkpoint-{step} produced no evaluation artifact. "
                "Every promised checkpoint must yield its own record before "
                "the next one runs.")
        gate = orchestrate.evaluate_gates(ev["wer"], base["wer"],
                                          ev["eos_rate"], ev["cap_hit_rate"])
        checkpoints.append({"step": step, "gate": gate,
                            "artifact_sha256": ev["artifact_sha256"],
                            "prefix": prefix})
        trace.add("checkpoint-eval", step=step, passed=gate["passed"],
                  artifact_sha256=ev["artifact_sha256"])
        _sync(sv, campaign_run, f"final-checkpoint-{step}", trace,
              artifact_sha256=ev["artifact_sha256"])
        if not gate["passed"]:
            raise CampaignError(
                f"REFUSING: checkpoint-{step} failed a gate:\n  "
                + "\n  ".join(gate["failures"]))

    budget.reconcile(sv.s3, "final_run", f"{attempt}-final",
                     final.get("seconds", 0.0))
    _sync(sv, campaign_run, "final-complete", trace)
    trace.add("cleanup", unresolved_reservations=len(
        budget.unresolved(budget.load(sv.s3)[0])))

    return {
        "campaign_run": campaign_run,
        "selected_lr": sel["selected_lr"],
        "checkpoints": checkpoints,
        "registered_models": 0,
        "promotable": False,
        "purpose": "training_system_validation",
        "trace": trace.steps,
        "trace_names": trace.names,
    }
