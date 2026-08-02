#!/usr/bin/env python3
"""Run only the owner-approved checkpoint-400 conversion diagnostic.

This launcher performs zero training.  It reuses the immutable checkpoint and
same-run base from CAMPAIGNRUN-2026-012, reserves one artifactize lifecycle,
and refuses any output namespace that is not empty.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import (budget, campaign, language_scope, mlflow_sync,  # noqa: E402
                      scope_deviation, stage_descriptor)
from pipeline.campaign_tracking import CampaignTracker  # noqa: E402
from pipeline.ec2_stage_adapter import (EC2StageAdapter, EC2StageConfig,
                                        render_user_data)  # noqa: E402
from pipeline.generation import config_fingerprint  # noqa: E402
from pipeline.validation_runner import frozen_validation  # noqa: E402
from scripts import run_campaign as campaign_launch  # noqa: E402

BUCKET = "medzen-speech"
REGION = "eu-central-1"
SOURCE_RECORD = (
    ROOT / "platform/evidence/CAMPAIGNRUN-2026-012-failed.json")
HOLDOUT_RECORD = (
    ROOT / "platform/evidence/VAL-2026-003-lingala-post-selection-holdout.json")
STAGE_KEY = "conversion-diagnostic"


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True,
        capture_output=True, text=True).stdout.strip()


def session():
    import boto3
    return boto3.Session(profile_name="medzen", region_name=REGION)


def evaluator_sha() -> str:
    digest = hashlib.sha256()
    for rel in (
        "pipeline/stage_runner.py", "pipeline/validation_runner.py",
        "scripts/evaluate_candidate.py", "pipeline/generation.py",
        "pipeline/normalizers.py", "pipeline/stage_descriptor.py",
        "pipeline/scope_deviation.py",
        "platform/decisions/B4-SCOPE-2026-002-simplified-exit.json",
    ):
        digest.update(rel.encode() + b"\0" + (ROOT / rel).read_bytes())
    return digest.hexdigest()


def _source_bindings(s3) -> dict:
    source = json.loads(SOURCE_RECORD.read_bytes())
    selected_step = scope_deviation.DECISION_DOC["servable_artifact"][
        "conversion_diagnostic"]["selected_checkpoint"]
    if (source.get("campaign_run"), source.get("source_training", {}).get(
            "selected_checkpoint")) != (
                "b4-scoped-count-tolerance-61145b7", selected_step):
        raise SystemExit(
            "REFUSING: failed-run evidence no longer binds checkpoint-400")
    source_run = source["campaign_run"]
    source_attempt = source["source_training"]["attempt"]
    root = f"candidates/evaluations/{source_run}/attempt-{source_attempt}/"
    base_key = root + "base_and_preflight/evaluations/base.json"
    base_raw = s3.get_object(Bucket=BUCKET, Key=base_key)["Body"].read()
    base_stage_raw = s3.get_object(
        Bucket=BUCKET,
        Key=root + "base_and_preflight/stage-result.json")["Body"].read()
    base_stage = json.loads(base_stage_raw)
    base = base_stage.get("base") or {}
    if base.get("artifact_key") != base_key:
        raise SystemExit("REFUSING: source base artifact key changed")
    if base.get("artifact_sha256") != sha(base_raw):
        raise SystemExit("REFUSING: source base evaluation changed")

    selected_prefix = root + f"final/asr/checkpoint-{selected_step}"
    artifact_raw = s3.get_object(
        Bucket=BUCKET,
        Key=selected_prefix.rstrip("/") + "/ARTIFACT.json")["Body"].read()
    artifact = json.loads(artifact_raw)
    selected = source["source_training"]
    if artifact.get("tree_sha256") != selected["artifact_tree_sha256"]:
        raise SystemExit("REFUSING: selected checkpoint tree changed")
    adapter_sha = (artifact.get("files") or {}).get(
        "adapter_model.safetensors", {}).get("sha256")
    if adapter_sha != selected["adapter_sha256"]:
        raise SystemExit("REFUSING: selected adapter bytes changed")

    eval_key = root + f"final/evaluations/checkpoint-{selected_step}.json"
    eval_raw = s3.get_object(Bucket=BUCKET, Key=eval_key)["Body"].read()
    if sha(eval_raw) != selected["evaluation_sha256"]:
        raise SystemExit("REFUSING: selected checkpoint evaluation changed")
    evaluation = json.loads(eval_raw)
    if evaluation.get("arm") != "candidate":
        raise SystemExit("REFUSING: selected evaluation is not the candidate")
    return {
        "source_campaign_run": source_run,
        "source_attempt": source_attempt,
        "selected_checkpoint": selected_step,
        "input_prefix": selected_prefix,
        "input_artifact_sha256": selected["artifact_tree_sha256"],
        "input_adapter_sha256": selected["adapter_sha256"],
        "input_evaluation_key": eval_key,
        "input_evaluation_sha256": selected["evaluation_sha256"],
        "base_arm_key": base["base_arm_key"],
        "base_artifact_key": base_key,
        "base_artifact_sha256": base["artifact_sha256"],
    }


def _governance(s3, args) -> dict:
    preview_args = SimpleNamespace(
        git_sha=args.git_sha,
        bundle_tar_sha256=args.bundle_tar_sha256,
        image_digest=args.image_digest,
        adoption_key=language_scope.ADOPTION_KEY,
        campaign_run=args.campaign_run,
        attempt=args.attempt,
        mlflow_db="/nonexistent/preview.db",
    )
    services = campaign_launch.build_services(
        s3, preview_args, preview=True)
    campaign.require_ready(services)
    policy, adoption, scoped = campaign.verify_governance(services)
    return {"policy": policy, "adoption": adoption, "scope": scoped}


def make_descriptor(args, pins: dict, *, parent_run_id: str,
                    child_run_id: str) -> dict:
    _, frozen_sha = frozen_validation()
    holdout = json.loads(HOLDOUT_RECORD.read_bytes())
    budget_attempt = f"{args.campaign_run}-{args.attempt}-artifactize"
    return stage_descriptor.build(
        campaign_run=args.campaign_run, attempt=args.attempt,
        stage="artifactize", git_sha=args.git_sha,
        bundle_tar_sha256=args.bundle_tar_sha256,
        image_digest=args.image_digest,
        policy_sha256=pins["policy_sha256"],
        adoption_key=language_scope.ADOPTION_KEY,
        dataset_fingerprint=language_scope.EXPECTED_DATASET_FINGERPRINT,
        language_scope_sha256=language_scope.LANGUAGE_SCOPE_SHA256,
        training_languages=list(language_scope.TRAINING_LANGUAGES),
        validation_languages=list(language_scope.VALIDATION_LANGUAGES),
        scope_deviation_sha256=scope_deviation.DECISION_SHA256,
        a5_gate_disposition_sha256=scope_deviation.A5_GATES_SHA256,
        termination_gate=scope_deviation.TERMINATION_GATE,
        holdout_manifest_key=holdout["holdout_manifest_key"],
        holdout_manifest_sha256=holdout["holdout_manifest_sha256"],
        holdout_evidence_sha256=sha(HOLDOUT_RECORD.read_bytes()),
        base_manifest_sha256=(
            "6a1987d462fc3330bb9eeeb488726bd7a16fd7d67f5aa08f0907eaa59d0913f1"),
        validation_manifest_sha256=frozen_sha,
        base_arm_key=pins["base_arm_key"],
        base_artifact_key=pins["base_artifact_key"],
        base_artifact_sha256=pins["base_artifact_sha256"],
        generation_config_fingerprint=config_fingerprint(),
        evaluator_sha256=evaluator_sha(),
        lr=1e-4, seed=0, max_steps=0, checkpoint_steps=[],
        reservation_id=budget.reservation_id(
            "artifactize", budget_attempt),
        watchdog_s=budget.WATCHDOG_S["artifactize"],
        input_prefix=pins["input_prefix"],
        input_artifact_sha256=pins["input_artifact_sha256"],
        input_evaluation_key=pins["input_evaluation_key"],
        input_evaluation_sha256=pins["input_evaluation_sha256"],
        output_prefix=(
            f"candidates/evaluations/{args.campaign_run}/"
            f"attempt-{args.attempt}/artifactize/"),
        mlflow_parent_run_id=parent_run_id,
        mlflow_child_run_id=child_run_id,
        purpose="training_system_validation", promotable=False)


def validate_inputs(args, *, descriptor: dict | None = None
                    ) -> tuple[object, object, dict, dict]:
    if git("status", "--porcelain"):
        raise SystemExit("REFUSING: conversion diagnostic worktree is dirty")
    if args.git_sha != git("rev-parse", "HEAD"):
        raise SystemExit("REFUSING: --git-sha differs from local HEAD")
    if args.campaign_run != "b4-scoped-count-tolerance-61145b7":
        raise SystemExit("REFUSING: diagnostic must remain in its source campaign")
    if args.attempt != "5":
        raise SystemExit("REFUSING: this owner-approved diagnostic is attempt 5")

    sess = session()
    s3 = sess.client("s3", region_name=REGION)
    governance = _governance(s3, args)
    bundle = json.loads(s3.get_object(
        Bucket=BUCKET,
        Key=f"candidates/bootstrap/{args.git_sha}/BUNDLE.json")["Body"].read())
    if (bundle.get("git_sha"), bundle.get("tar_sha256")) != (
            args.git_sha, args.bundle_tar_sha256):
        raise SystemExit("REFUSING: conversion bundle binding differs")
    source = _source_bindings(s3)
    source["policy_sha256"] = governance["policy"]["policy_sha256"]

    output_prefix = (
        f"candidates/evaluations/{args.campaign_run}/"
        f"attempt-{args.attempt}/artifactize/")
    if s3.list_objects_v2(
            Bucket=BUCKET, Prefix=output_prefix,
            MaxKeys=1).get("KeyCount", 0):
        raise SystemExit("REFUSING: conversion output prefix is occupied")
    infra = EC2StageAdapter(sess).preflight_campaign(
        args.git_sha, args.image_digest)
    ledger, _ = budget.load(s3)
    if budget.unresolved(ledger):
        raise SystemExit("REFUSING: aggregate budget has unresolved spend")
    committed = budget.committed_usd(ledger)
    worst = budget.worst_case_usd("artifactize")
    if committed + worst > budget.CEILING_USD:
        raise SystemExit("REFUSING: conversion stage exceeds aggregate budget")

    if descriptor is not None:
        expected = make_descriptor(
            args, source,
            parent_run_id=descriptor["mlflow_parent_run_id"],
            child_run_id=descriptor["mlflow_child_run_id"])
        if expected != descriptor:
            raise SystemExit(
                "REFUSING: prepared descriptor differs from current inputs")
        _, user_data_sha = render_user_data(descriptor, EC2StageConfig())
        descriptor_sha = stage_descriptor.descriptor_hash(descriptor)
    else:
        user_data_sha = None
        descriptor_sha = None
    packet = {
        "git_sha": args.git_sha,
        "bundle_tar_sha256": args.bundle_tar_sha256,
        "image_digest": args.image_digest,
        "scope_deviation_sha256": scope_deviation.DECISION_SHA256,
        "source_checkpoint": source["selected_checkpoint"],
        "source_adapter_tree_sha256": source["input_artifact_sha256"],
        "source_evaluation_sha256": source["input_evaluation_sha256"],
        "diagnostic_arms": scope_deviation.DECISION_DOC[
            "servable_artifact"]["conversion_diagnostic"][
                "arms_in_fixed_order"],
        "artifactize_worst_case_usd": worst,
        "aggregate_committed_usd": committed,
        "aggregate_if_worst_case_usd": round(committed + worst, 4),
        "aggregate_ceiling_usd": budget.CEILING_USD,
        "unresolved_reservations": 0,
        "output_prefix": output_prefix,
        "stage_descriptor_sha256": descriptor_sha,
        "user_data_sha256": user_data_sha,
        "infra": infra,
        "writes_performed": 0,
    }
    return sess, s3, source, packet


def prepare(args) -> dict:
    sess, s3, source, _ = validate_inputs(args)
    db = Path(args.mlflow_db)
    if db.exists() or Path(args.descriptor).exists():
        raise SystemExit(
            "REFUSING: prepared descriptor or MLflow database already exists")
    tracker = CampaignTracker(db, args.campaign_run, args.attempt)
    child = tracker.start_stage(STAGE_KEY, {
        "training_steps": 0,
        "source_checkpoint": source["selected_checkpoint"],
        "source_adapter_tree_sha256": source["input_artifact_sha256"],
        "code_git_sha": args.git_sha,
        "code_tar_sha256": args.bundle_tar_sha256,
        "image_digest": args.image_digest,
        "scope_deviation_sha256": scope_deviation.DECISION_SHA256,
        "promotable": False,
    })
    descriptor = make_descriptor(
        args, source, parent_run_id=tracker.parent_run_id,
        child_run_id=child)
    Path(args.descriptor).write_text(
        json.dumps(descriptor, indent=2, sort_keys=True) + "\n")
    _, _, _, packet = validate_inputs(args, descriptor=descriptor)
    return packet


def execute(args) -> dict:
    descriptor_path = Path(args.descriptor)
    db = Path(args.mlflow_db)
    if not descriptor_path.is_file() or not db.is_file():
        raise SystemExit(
            "REFUSING: prepare the descriptor and MLflow database first")
    descriptor = json.loads(descriptor_path.read_bytes())
    sess, s3, _, packet = validate_inputs(args, descriptor=descriptor)
    tracker = CampaignTracker.recover_existing(
        db, args.campaign_run, args.attempt,
        descriptor["mlflow_parent_run_id"],
        {STAGE_KEY: descriptor["mlflow_child_run_id"]})
    budget_attempt = f"{args.campaign_run}-{args.attempt}-artifactize"
    reservation = budget.reserve(s3, "artifactize", budget_attempt)
    result = None
    try:
        result = EC2StageAdapter(sess).run(descriptor)
        reconciled = budget.reconcile(
            s3, "artifactize", budget_attempt,
            result["actual_seconds"], result.get("instance_id"))
        stage_descriptor.verify_result(descriptor, result)
        passed = bool(
            (result.get("converted_gate") or {}).get("passed")
            and (result.get("holdout") or {}).get("gate", {}).get("passed")
            and result.get("servable_artifact_published"))
        tracker.finish_stage(STAGE_KEY, result, {
            "selected_precision": result.get("selected_precision"),
            "artifact_evaluation_sha256":
                result.get("artifact_evaluation_sha256"),
            "active_gates_passed": passed,
            "actual_usd": reconciled["actual_usd"],
            "registered_models": 0,
        })
        tracker.finish_parent(
            passed,
            "converted artifact passed every active gate" if passed else
            "conversion diagnostic completed without a servable candidate")
        snapshot = mlflow_sync.sync(
            s3, db, args.campaign_run, STAGE_KEY,
            attempt=args.attempt, extra={
                "stage_descriptor_sha256":
                    stage_descriptor.descriptor_hash(descriptor),
                "selected_precision": result.get("selected_precision"),
                "active_gates_passed": passed,
                "training_steps": 0,
                "aggregate_committed_usd":
                    budget.committed_usd(budget.load(s3)[0]),
            })
    except BaseException as exc:
        tracker.fail_stage(STAGE_KEY, str(exc))
        tracker.finish_parent(False, str(exc))
        raise
    return {
        **packet,
        "reservation_id": reservation["reservation_id"],
        "instance_id": result["instance_id"],
        "root_volume_deleted": result["root_volume_deleted"],
        "actual_seconds": result["actual_seconds"],
        "actual_usd": reconciled["actual_usd"],
        "selected_precision": result.get("selected_precision"),
        "converted_gate_passed":
            (result.get("converted_gate") or {}).get("passed"),
        "holdout_gate_passed":
            (result.get("holdout") or {}).get("gate", {}).get("passed"),
        "servable_artifact_published":
            result.get("servable_artifact_published", False),
        "artifact_evaluation_sha256":
            result.get("artifact_evaluation_sha256"),
        "mlflow_snapshot_sha256": snapshot["sha256"],
        "training_steps": 0,
        "registered_models": 0,
        "b5_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-run", required=True)
    parser.add_argument("--attempt", default="5")
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--bundle-tar-sha256", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--mlflow-db", required=True)
    parser.add_argument("--descriptor", required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    if args.prepare == args.confirm:
        raise SystemExit("REFUSING: choose exactly one of --prepare or --confirm")
    result = prepare(args) if args.prepare else execute(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.prepare:
        print("PREPARED ONLY - 0 AWS writes, reservations or launches")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
