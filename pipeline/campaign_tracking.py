"""MLflow run structure for the corrected B4 campaign.

The training containers are disposable.  The authoritative MLflow database
lives with the operator, where every stage result is recorded only after the
EC2 adapter has verified the result object, observed termination, and proved
root-volume deletion.  Immutable SQLite snapshots are then written by
``pipeline.mlflow_sync``.

This module creates the real parent/child runs promised by PLAN-2026-002.  It
does not register models and exposes no registry operation.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any


EXPERIMENT = "b4-corrected-training-system-validation"
PARENT_FAILED_RUN = "23868bab2d8448759fc1b9ed26156952"


def _flat_params(values: dict[str, Any]) -> dict[str, str]:
    """MLflow params are immutable strings, so normalise them once."""
    out: dict[str, str] = {}
    for key, value in values.items():
        if isinstance(value, (dict, list, tuple)):
            value = json.dumps(value, sort_keys=True, separators=(",", ":"))
        elif value is None:
            value = ""
        elif isinstance(value, bool):
            value = str(value).lower()
        out[str(key)] = str(value)
    return out


def _numeric_metrics(result: dict) -> dict[str, float]:
    """Extract the stage metrics that PLAN-2026-002 requires.

    Per-language values remain separate; a macro number never replaces the
    nine measurements that produced it.
    """
    metrics: dict[str, float] = {}

    def add(name: str, value: Any) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        value = float(value)
        if math.isfinite(value):
            metrics[name] = value

    for name in ("train_loss", "grad_norm", "steps_completed",
                 "actual_seconds", "gpu_peak_mb"):
        add(name, result.get(name))

    maps = {
        "wer": "val_wer", "cer": "val_cer",
        "eos_rate": "val_eos_rate",
        "cap_hit_rate": "val_cap_hit_rate",
        "generated_tokens_median": "val_gen_tokens_median",
        "generated_tokens_max": "val_gen_tokens_max",
    }
    for source, prefix in maps.items():
        for language, value in (result.get(source) or {}).items():
            add(f"{prefix}_{language}", value)

    gate = result.get("gate") or {}
    for name in ("macro_wer", "min_eos_rate", "max_cap_hit_rate",
                 "worst_language_regression"):
        add(f"val_{name}", gate.get(name))
    for arm_name, arm in (result.get("arms") or {}).items():
        safe_arm = str(arm_name).replace("-", "_")
        for language, measured in (arm.get("per_language") or {}).items():
            teacher = measured.get("teacher_forced") or {}
            generated = measured.get("generation") or {}
            add(f"diag_{safe_arm}_{language}_total_nll",
                teacher.get("total_nll_per_token"))
            add(f"diag_{safe_arm}_{language}_content_nll",
                teacher.get("content_nll_per_token"))
            add(f"diag_{safe_arm}_{language}_eos_nll_mean",
                (teacher.get("eos_nll") or {}).get("mean"))
            add(f"diag_{safe_arm}_{language}_eos_probability_mean",
                (teacher.get("eos_probability") or {}).get("mean"))
            add(f"diag_{safe_arm}_{language}_eos_rank_median",
                (teacher.get("eos_rank") or {}).get("median"))
            add(f"diag_{safe_arm}_{language}_eos_rate",
                generated.get("eos_rate"))
            add(f"diag_{safe_arm}_{language}_cap_hit_rate",
                generated.get("cap_hit_rate"))
            add(f"diag_{safe_arm}_{language}_unique_token_ratio_mean",
                (generated.get("unique_token_ratio") or {}).get("mean"))
            add(f"diag_{safe_arm}_{language}_repeated_bigram_rate_mean",
                (generated.get("repeated_bigram_rate") or {}).get("mean"))
    for strategy, arms in (result.get("strategies") or {}).items():
        safe_strategy = str(strategy).replace("-", "_")
        for arm_name, measured in (arms or {}).items():
            safe_arm = str(arm_name).replace("-", "_")
            prefix = f"decode_{safe_strategy}_{safe_arm}"
            for source in ("wer", "cer", "eos_rate", "cap_hit_rate"):
                add(f"{prefix}_{source}", measured.get(source))
            add(f"{prefix}_generated_tokens_median",
                (measured.get("generated_tokens") or {}).get("median"))
            add(f"{prefix}_latency_median_s",
                (measured.get("latency_s") or {}).get("median"))
            add(f"{prefix}_unique_token_ratio_mean",
                (measured.get("unique_token_ratio") or {}).get("mean"))
            add(f"{prefix}_repeated_bigram_rate_mean",
                (measured.get("repeated_bigram_rate") or {}).get("mean"))
    return metrics


class CampaignTracker:
    """Create and update one parent plus immutable-purpose child runs."""

    def __init__(self, db: Path, campaign_run: str, attempt: str = "1"):
        import mlflow

        self.db = Path(db).resolve()
        self.db.parent.mkdir(parents=True, exist_ok=True)
        self.tracking_uri = f"sqlite:///{self.db}"
        self.campaign_run = campaign_run
        self.attempt = str(attempt)
        mlflow.set_tracking_uri(self.tracking_uri)
        if mlflow.get_experiment_by_name(EXPERIMENT) is None:
            mlflow.create_experiment(EXPERIMENT)
        self.experiment_id = mlflow.get_experiment_by_name(EXPERIMENT).experiment_id
        self.client = mlflow.tracking.MlflowClient(tracking_uri=self.tracking_uri)
        self.parent_run_id = self._create_parent()
        self.children: dict[str, str] = {}

    def _create_parent(self) -> str:
        tags = {
            "mlflow.runName": self.campaign_run,
            "medzen.campaign_run": self.campaign_run,
            "medzen.attempt": self.attempt,
            "phase": "B4",
            "purpose": "training_system_validation",
            "promotable": "false",
            "parent_failed_run": PARENT_FAILED_RUN,
            "registered_models_expected": "0",
        }
        run = self.client.create_run(self.experiment_id, tags=tags,
                                     run_name=self.campaign_run)
        self.client.log_batch(
            run.info.run_id,
            params=[
                self._param("campaign_run", self.campaign_run),
                self._param("attempt", self.attempt),
                self._param("parent_failed_run", PARENT_FAILED_RUN),
            ],
        )
        return run.info.run_id

    @staticmethod
    def _param(key: str, value: Any):
        from mlflow.entities import Param
        return Param(str(key), str(value))

    @staticmethod
    def _metric(key: str, value: float, step: int = 0):
        from mlflow.entities import Metric
        return Metric(str(key), float(value), int(time.time() * 1000), int(step))

    @staticmethod
    def _tag(key: str, value: Any):
        from mlflow.entities import RunTag
        return RunTag(str(key), str(value))

    def start_stage(self, stage_key: str, params: dict[str, Any]) -> str:
        """Create one child before its immutable descriptor is finalised."""
        if stage_key in self.children:
            raise SystemExit(
                f"REFUSING: MLflow child {stage_key!r} already exists in this "
                "attempt; reruns require a new attempt namespace")
        tags = {
            "mlflow.parentRunId": self.parent_run_id,
            "mlflow.runName": stage_key,
            "medzen.campaign_run": self.campaign_run,
            "medzen.attempt": self.attempt,
            "medzen.stage": stage_key,
            "purpose": "training_system_validation",
            "promotable": "false",
        }
        run = self.client.create_run(self.experiment_id, tags=tags,
                                     run_name=stage_key)
        rid = run.info.run_id
        flat = _flat_params(params)
        self.client.log_batch(
            rid, params=[self._param(k, v) for k, v in flat.items()])
        self.children[stage_key] = rid
        return rid

    def finish_stage(self, stage_key: str, result: dict,
                     extra_params: dict[str, Any] | None = None) -> None:
        rid = self.children[stage_key]
        params = _flat_params(extra_params or {})
        metrics = _numeric_metrics(result)
        step = int(result.get("steps_completed") or 0)
        self.client.log_batch(
            rid,
            metrics=[self._metric(k, v, step=step)
                     for k, v in metrics.items()],
            params=[self._param(k, v) for k, v in params.items()],
            tags=[
                self._tag("instance_id", result.get("instance_id", "")),
                self._tag("root_volume_deleted",
                          str(result.get("root_volume_deleted", False)).lower()),
                self._tag("stage_descriptor_sha256",
                          result.get("stage_descriptor_sha256", "")),
            ],
        )
        self.client.set_terminated(rid, status="FINISHED")

    def fail_stage(self, stage_key: str, reason: str) -> None:
        rid = self.children.get(stage_key)
        if rid is None:
            return
        self.client.set_tag(rid, "failure_reason", reason[:5000])
        self.client.set_terminated(rid, status="FAILED")

    def log_parent_metrics(self, metrics: dict[str, float],
                           tags: dict[str, Any] | None = None) -> None:
        self.client.log_batch(
            self.parent_run_id,
            metrics=[self._metric(k, v) for k, v in metrics.items()
                     if isinstance(v, (int, float))
                     and not isinstance(v, bool)
                     and math.isfinite(float(v))],
            tags=[self._tag(k, v) for k, v in (tags or {}).items()],
        )

    def finish_parent(self, passed: bool, reason: str | None = None) -> None:
        self.client.set_tag(self.parent_run_id, "campaign_passed",
                            str(bool(passed)).lower())
        if reason:
            self.client.set_tag(self.parent_run_id, "campaign_outcome",
                                reason[:5000])
        self.client.set_terminated(
            self.parent_run_id, status="FINISHED" if passed else "FAILED")
