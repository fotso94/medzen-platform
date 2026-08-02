#!/usr/bin/env python3
"""Prove exact S3 checkpoint interruption/resume after artifact approval.

This is the owner-approved tail of B4-SCOPE-2026-002.  It does not select a
model, open a quality holdout, register anything, or permit B5.  One Spot
instance trains to step 100, publishes an immutable checkpoint, and is
deliberately interrupted only after operator readback.  A second Spot instance
must download that exact tree and continue to step 200.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import budget, campaign, mlflow_sync, stage_descriptor  # noqa: E402
from pipeline.campaign_tracking import CampaignTracker  # noqa: E402
from pipeline.ec2_stage_adapter import (EC2StageAdapter,  # noqa: E402
                                        EC2StageConfig, render_user_data)
from scripts import run_campaign as campaign_launch  # noqa: E402
from scripts import run_conversion_diagnostic as conversion  # noqa: E402

BUCKET = "medzen-speech"
REGION = "eu-central-1"
CAMPAIGN_RUN = "b4-scoped-count-tolerance-61145b7"
ATTEMPT = "6"
LR = 1e-4
COMPLETION_RECORD = (
    ROOT / "platform/evidence/CAMPAIGNRUN-2026-013-passed.json")


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True,
        capture_output=True, text=True).stdout.strip()


def aws_session():
    import boto3
    return boto3.Session(profile_name="medzen", region_name=REGION)


def _artifact_precondition(s3) -> dict:
    """Refuse Spot spend unless the selected serving artifact really passed."""
    local = json.loads(COMPLETION_RECORD.read_bytes())
    if (local.get("status"), local.get("attempt"), local.get("promotable")) != (
            "PASSED_CONTROLLED_CONVERSION_DIAGNOSTIC", "5", False):
        raise SystemExit("REFUSING: conversion completion record changed")
    key = (
        f"candidates/evaluations/{CAMPAIGN_RUN}/attempt-5/artifactize/"
        "artifact-evaluation.json")
    raw = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    expected_sha = local["executed_provenance"]["artifact_evaluation_sha256"]
    if sha(raw) != expected_sha:
        raise SystemExit("REFUSING: artifact evaluation bytes changed")
    result = json.loads(raw)
    if (result.get("selected_precision") != "float16"
            or result.get("servable_artifact_published") is not True
            or (result.get("converted_gate") or {}).get("passed") is not True
            or (result.get("holdout") or {}).get("gate", {}).get("passed")
            is not True
            or result.get("training_steps") != 0
            or result.get("registered_models") != 0
            or result.get("b5_allowed") is not False):
        raise SystemExit(
            "REFUSING: serving artifact did not retain every approved outcome")
    manifest_key = (
        f"candidates/evaluations/{CAMPAIGN_RUN}/attempt-5/artifactize/"
        "ctranslate2-float16/ARTIFACT.json")
    manifest = json.loads(s3.get_object(
        Bucket=BUCKET, Key=manifest_key)["Body"].read())
    tree = local["artifact"]["tree_sha256"]
    if manifest.get("tree_sha256") != tree:
        raise SystemExit("REFUSING: selected serving artifact tree changed")
    return {
        "artifact_evaluation_key": key,
        "artifact_evaluation_sha256": expected_sha,
        "servable_artifact_tree_sha256": tree,
        "selected_precision": "float16",
    }


def _preview(args, s3):
    return campaign_launch.build_services(s3, args, preview=True)


def _base(source: dict) -> dict:
    return {
        "base_arm_key": source["base_arm_key"],
        "artifact_key": source["base_artifact_key"],
        "artifact_sha256": source["base_artifact_sha256"],
    }


def _descriptor_services(pins: dict, image_digest: str,
                         parent_run_id: str):
    bound = dict(pins)
    bound["mlflow_parent_run_id"] = parent_run_id
    return SimpleNamespace(
        stage_descriptors=bound, image_digest=image_digest,
        tracker=SimpleNamespace(parent_run_id=parent_run_id))


def checkpoint_descriptor(args, pins: dict, source: dict,
                          parent_run_id: str, child_run_id: str) -> dict:
    attempt_key = f"{args.campaign_run}-{args.attempt}-spot-checkpoint"
    sv = _descriptor_services(pins, args.image_digest, parent_run_id)
    return campaign.make_descriptor(
        sv, args.campaign_run, args.attempt, "spot_checkpoint", lr=LR,
        max_steps=100, checkpoint_steps=[100],
        reservation_id=budget.reservation_id(
            "spot_checkpoint", attempt_key),
        mlflow_child_run_id=child_run_id, base_result=_base(source))


def resume_descriptor(args, pins: dict, source: dict,
                      parent_run_id: str, child_run_id: str,
                      checkpoint: dict) -> dict:
    attempt_key = f"{args.campaign_run}-{args.attempt}-spot-resume"
    sv = _descriptor_services(pins, args.image_digest, parent_run_id)
    return campaign.make_descriptor(
        sv, args.campaign_run, args.attempt, "spot_resume", lr=LR,
        max_steps=200, checkpoint_steps=[100, 200],
        reservation_id=budget.reservation_id("spot_resume", attempt_key),
        mlflow_child_run_id=child_run_id, base_result=_base(source),
        input_prefix=checkpoint["checkpoint_prefix"],
        input_artifact_sha256=checkpoint["checkpoint_tree_sha256"])


def require_checkpoint_result(result: dict) -> None:
    if (result.get("operator_interrupted") is not True
            or not result.get("checkpoint_tree_sha256")
            or result.get("checkpoint_step") != 100
            or result.get("root_volume_deleted") is not True):
        raise SystemExit(
            "REFUSING: interruption lacks an exact durable checkpoint proof")


def require_resume_result(result: dict, checkpoint: dict) -> None:
    finite = result.get("training_finite")
    finite_ok = (
        isinstance(finite, dict)
        and finite.get("passed") is True
        and finite.get("reasons") == []
        and isinstance(finite.get("train_loss"), (int, float))
        and not isinstance(finite.get("train_loss"), bool)
        and math.isfinite(float(finite["train_loss"]))
        and isinstance(finite.get("grad_norm"), (int, float))
        and not isinstance(finite.get("grad_norm"), bool)
        and math.isfinite(float(finite["grad_norm"]))
        and isinstance(finite.get("losses_logged"), int)
        and finite["losses_logged"] > 0
        and isinstance(finite.get("gradients_logged"), int)
        and finite["gradients_logged"] > 0)
    if (result.get("exact_checkpoint_match") is not True
            or result.get("resumed_from_tree_sha256")
            != checkpoint["checkpoint_tree_sha256"]
            or result.get("resumed_from_step") != 100
            or result.get("steps_completed") != 200
            or not finite_ok
            or result.get("root_volume_deleted") is not True):
        raise SystemExit(
            "REFUSING: replacement Spot instance did not resume the exact "
            "authorised checkpoint to step 200")


def completed_checkpoint(s3, args, local_descriptor: dict) -> dict:
    """Recover only a fully terminated, immutable first Spot lifecycle."""
    prefix = (
        f"candidates/evaluations/{args.campaign_run}/"
        f"attempt-{args.attempt}/spot_checkpoint/")
    remote_descriptor = json.loads(s3.get_object(
        Bucket=BUCKET, Key=prefix + "descriptor.json")["Body"].read())
    if remote_descriptor != local_descriptor:
        raise SystemExit(
            "REFUSING: local and durable checkpoint descriptors differ")
    result = json.loads(s3.get_object(
        Bucket=BUCKET, Key=prefix + "stage-result.json")["Body"].read())
    stage_descriptor.verify_result(remote_descriptor, result)
    require_checkpoint_result(result)
    if (result.get("aws_final_state") != "terminated"
            or result.get("lifecycle") != "spot-direct-ec2"):
        raise SystemExit(
            "REFUSING: completed checkpoint lifecycle is not terminated Spot")
    return result


def completed_resume(s3, args, local_descriptor: dict,
                     checkpoint: dict) -> dict:
    """Recover only an immutable, terminated exact-resume lifecycle."""
    prefix = (
        f"candidates/evaluations/{args.campaign_run}/"
        f"attempt-{args.attempt}/spot_resume/")
    remote_descriptor = json.loads(s3.get_object(
        Bucket=BUCKET, Key=prefix + "descriptor.json")["Body"].read())
    if remote_descriptor != local_descriptor:
        raise SystemExit(
            "REFUSING: local and durable resume descriptors differ")
    result = json.loads(s3.get_object(
        Bucket=BUCKET, Key=prefix + "stage-result.json")["Body"].read())
    stage_descriptor.verify_result(remote_descriptor, result)
    require_resume_result(result, checkpoint)
    if (result.get("aws_final_state") != "terminated"
            or result.get("lifecycle") != "spot-direct-ec2"):
        raise SystemExit(
            "REFUSING: completed resume lifecycle is not terminated Spot")
    return result


def validate_inputs(args, descriptor: dict | None = None,
                    resume_local_descriptor: dict | None = None,
                    *, resume_completed: bool = False,
                    recover_completed: bool = False):
    if (args.campaign_run, str(args.attempt)) != (CAMPAIGN_RUN, ATTEMPT):
        raise SystemExit("REFUSING: Spot proof is bound to campaign attempt 6")
    observer_git_sha = git("rev-parse", "HEAD")
    if not recover_completed and observer_git_sha != args.git_sha:
        raise SystemExit("REFUSING: --git-sha differs from local HEAD")
    if git("status", "--porcelain"):
        raise SystemExit("REFUSING: worktree is dirty")
    sess = aws_session()
    s3 = sess.client("s3", region_name=REGION)
    preview = _preview(args, s3)
    campaign.require_ready(preview)
    campaign.verify_governance(preview)
    source = conversion._source_bindings(s3)
    artifact = _artifact_precondition(s3)
    bundle = json.loads(s3.get_object(
        Bucket=BUCKET,
        Key=f"candidates/bootstrap/{args.git_sha}/BUNDLE.json")["Body"].read())
    if (bundle.get("git_sha"), bundle.get("tar_sha256")) != (
            args.git_sha, args.bundle_tar_sha256):
        raise SystemExit("REFUSING: published bundle differs from launch pins")
    infra = EC2StageAdapter(sess).preflight_campaign(
        args.git_sha, args.image_digest)
    ledger, _ = budget.load(s3)
    if budget.unresolved(ledger):
        raise SystemExit("REFUSING: aggregate budget has unresolved spend")
    worst_checkpoint = budget.worst_case_usd("spot_checkpoint")
    worst_resume = budget.worst_case_usd("spot_resume")
    committed = budget.committed_usd(ledger)
    if committed + worst_checkpoint + worst_resume > budget.CEILING_USD:
        raise SystemExit("REFUSING: Spot proof exceeds aggregate budget")
    for stage in ("spot_checkpoint", "spot_resume"):
        prefix = (
            f"candidates/evaluations/{args.campaign_run}/"
            f"attempt-{args.attempt}/{stage}/")
        occupied = s3.list_objects_v2(
            Bucket=BUCKET, Prefix=prefix, MaxKeys=1).get("KeyCount", 0)
        if stage == "spot_checkpoint" and (resume_completed
                                             or recover_completed):
            if not occupied or descriptor is None:
                raise SystemExit(
                    "REFUSING: no completed checkpoint lifecycle to recover")
            completed_checkpoint(s3, args, descriptor)
        elif stage == "spot_resume" and recover_completed:
            if (not occupied or resume_local_descriptor is None
                    or descriptor is None):
                raise SystemExit(
                    "REFUSING: no completed resume lifecycle to recover")
            checkpoint = completed_checkpoint(s3, args, descriptor)
            completed_resume(
                s3, args, resume_local_descriptor, checkpoint)
        elif occupied:
            raise SystemExit(f"REFUSING: output prefix {prefix} is occupied")
    pins = dict(preview.stage_descriptors)
    if (descriptor is not None and not resume_completed
            and not recover_completed):
        expected = checkpoint_descriptor(
            args, pins, source, descriptor["mlflow_parent_run_id"],
            descriptor["mlflow_child_run_id"])
        if expected != descriptor:
            raise SystemExit("REFUSING: prepared Spot descriptor changed")
    return sess, s3, source, pins, {
        "git_sha": args.git_sha,
        "observer_git_sha": observer_git_sha,
        "bundle_tar_sha256": args.bundle_tar_sha256,
        "image_digest": args.image_digest,
        "artifact_precondition": artifact,
        "spot_checkpoint_worst_case_usd": worst_checkpoint,
        "spot_resume_worst_case_usd": worst_resume,
        "aggregate_committed_usd": committed,
        "aggregate_if_worst_case_usd": round(
            committed + worst_checkpoint + worst_resume, 4),
        "aggregate_ceiling_usd": budget.CEILING_USD,
        "infra": infra,
        "preflight_writes_performed": 0,
    }


def _reconciled_lifecycle(s3, descriptor: dict, result: dict,
                          stage: str) -> dict:
    ledger, _ = budget.load(s3)
    existing = ledger["reservations"].get(descriptor["reservation_id"])
    if (not existing or existing.get("state") != "reconciled"
            or existing.get("stage") != stage
            or existing.get("instance_id") != result.get("instance_id")
            or existing.get("actual_seconds") != result.get("actual_seconds")
            or not isinstance(existing.get("actual_usd"), (int, float))
            or not math.isfinite(float(existing["actual_usd"]))):
        raise SystemExit(
            f"REFUSING: completed {stage} budget is not reconciled")
    return existing


def _resume_tracking_result(result: dict) -> dict:
    """Expose validated structured finite evidence as MLflow metrics."""
    finite = result["training_finite"]
    return {
        **result,
        "train_loss": finite["train_loss"],
        "grad_norm": finite["grad_norm"],
    }


def recover_completed(args) -> dict:
    """Close tracking for two completed lifecycles without launching again."""
    checkpoint_path = Path(args.checkpoint_descriptor)
    resume_path = Path(args.resume_descriptor)
    db = Path(args.mlflow_db)
    if not checkpoint_path.is_file() or not resume_path.is_file() \
            or not db.is_file():
        raise SystemExit(
            "REFUSING: completed-resume recovery inputs are absent")
    prepared = json.loads(checkpoint_path.read_bytes())
    resume_descriptor_local = json.loads(resume_path.read_bytes())
    sess, s3, source, pins, packet = validate_inputs(
        args, descriptor=prepared,
        resume_local_descriptor=resume_descriptor_local,
        recover_completed=True)
    del sess, source, pins
    checkpoint = completed_checkpoint(s3, args, prepared)
    resumed = completed_resume(
        s3, args, resume_descriptor_local, checkpoint)
    checkpoint_cost = _reconciled_lifecycle(
        s3, prepared, checkpoint, "spot_checkpoint")
    resume_cost = _reconciled_lifecycle(
        s3, resume_descriptor_local, resumed, "spot_resume")
    tracker = CampaignTracker.recover_existing(
        db, args.campaign_run, args.attempt,
        prepared["mlflow_parent_run_id"], {
            "spot_checkpoint": prepared["mlflow_child_run_id"],
            "spot_resume": resume_descriptor_local["mlflow_child_run_id"],
        })
    tracker.finish_stage("spot_resume", _resume_tracking_result(resumed), {
        "resumed_from_tree_sha256": resumed["resumed_from_tree_sha256"],
        "steps_completed": 200,
        "exact_checkpoint_match": True,
    })
    tracker.finish_parent(True, "exact S3 Spot checkpoint resume proved")
    resume_snapshot = _sync(
        s3, db, args, "spot-resume",
        resumed_from_tree_sha256=resumed["resumed_from_tree_sha256"],
        resumed_checkpoint_tree_sha256=
            resumed["resumed_checkpoint_tree_sha256"])
    checkpoint_snapshot = json.loads(s3.get_object(
        Bucket=BUCKET,
        Key=mlflow_sync.record_key(
            args.campaign_run, args.attempt,
            "spot-checkpoint"))["Body"].read())
    final_ledger, _ = budget.load(s3)
    return {
        **packet,
        "recovery_only": True,
        "aws_resources_created_during_recovery": 0,
        "checkpoint_instance_id": checkpoint["instance_id"],
        "checkpoint_actual_seconds": checkpoint["actual_seconds"],
        "checkpoint_actual_usd": checkpoint_cost["actual_usd"],
        "checkpoint_tree_sha256": checkpoint["checkpoint_tree_sha256"],
        "checkpoint_root_volume_deleted": checkpoint["root_volume_deleted"],
        "checkpoint_snapshot_sha256": checkpoint_snapshot["sha256"],
        "resume_instance_id": resumed["instance_id"],
        "resume_actual_seconds": resumed["actual_seconds"],
        "resume_actual_usd": resume_cost["actual_usd"],
        "resumed_from_tree_sha256": resumed["resumed_from_tree_sha256"],
        "resumed_checkpoint_tree_sha256":
            resumed["resumed_checkpoint_tree_sha256"],
        "resume_root_volume_deleted": resumed["root_volume_deleted"],
        "resume_snapshot_sha256": resume_snapshot["sha256"],
        "exact_checkpoint_match": True,
        "steps_completed": 200,
        "aggregate_committed_usd": budget.committed_usd(final_ledger),
        "aggregate_remaining_usd": budget.remaining_usd(final_ledger),
        "unresolved_reservations": len(budget.unresolved(final_ledger)),
        "registered_models": 0,
        "b5_allowed": False,
    }


def prepare(args) -> dict:
    sess, s3, source, pins, packet = validate_inputs(args)
    del sess, s3
    db = Path(args.mlflow_db)
    checkpoint_path = Path(args.checkpoint_descriptor)
    resume_path = Path(args.resume_descriptor)
    if db.exists() or checkpoint_path.exists() or resume_path.exists():
        raise SystemExit("REFUSING: prepared Spot files already exist")
    tracker = CampaignTracker(db, args.campaign_run, args.attempt)
    sv = _descriptor_services(pins, args.image_digest, tracker.parent_run_id)
    child = tracker.start_stage(
        "spot_checkpoint", campaign._tracking_params(
            sv, "spot_checkpoint", lr=LR))
    descriptor = checkpoint_descriptor(
        args, pins, source, tracker.parent_run_id, child)
    checkpoint_path.write_text(
        json.dumps(descriptor, indent=2, sort_keys=True) + "\n")
    _, user_data_sha = render_user_data(descriptor, EC2StageConfig())
    return {
        **packet,
        "checkpoint_descriptor_sha256":
            stage_descriptor.descriptor_hash(descriptor),
        "checkpoint_user_data_sha256": user_data_sha,
        "mlflow_parent_run_id": tracker.parent_run_id,
    }


def _sync(s3, db: Path, args, stage: str, **extra) -> dict:
    ledger, _ = budget.load(s3)
    return mlflow_sync.sync(
        s3, db, args.campaign_run, stage, attempt=args.attempt,
        extra={
            **extra,
            "aggregate_committed_usd": budget.committed_usd(ledger),
            "aggregate_unresolved_reservations": len(
                budget.unresolved(ledger)),
            "registered_models": 0,
            "b5_allowed": False,
        })


def execute(args) -> dict:
    checkpoint_path = Path(args.checkpoint_descriptor)
    db = Path(args.mlflow_db)
    if not checkpoint_path.is_file() or not db.is_file():
        raise SystemExit("REFUSING: prepare the Spot proof first")
    prepared = json.loads(checkpoint_path.read_bytes())
    sess, s3, source, pins, packet = validate_inputs(
        args, descriptor=prepared,
        resume_completed=args.resume_completed_checkpoint)
    tracker = CampaignTracker.recover_existing(
        db, args.campaign_run, args.attempt,
        prepared["mlflow_parent_run_id"],
        {"spot_checkpoint": prepared["mlflow_child_run_id"]})
    launcher = EC2StageAdapter(sess)

    checkpoint_attempt = (
        f"{args.campaign_run}-{args.attempt}-spot-checkpoint")
    if args.resume_completed_checkpoint:
        checkpoint = completed_checkpoint(s3, args, prepared)
        ledger, _ = budget.load(s3)
        existing = ledger["reservations"].get(prepared["reservation_id"])
        if (not existing or existing.get("state") != "reconciled"
                or existing.get("instance_id") != checkpoint["instance_id"]):
            raise SystemExit(
                "REFUSING: completed checkpoint budget is not reconciled")
        checkpoint_cost = {"actual_usd": existing["actual_usd"]}
        snapshot_record = json.loads(s3.get_object(
            Bucket=BUCKET,
            Key=mlflow_sync.record_key(
                args.campaign_run, args.attempt,
                "spot-checkpoint"))["Body"].read())
        checkpoint_snapshot = snapshot_record
    else:
        held = budget.reserve(s3, "spot_checkpoint", checkpoint_attempt)
        if held["reservation_id"] != prepared["reservation_id"]:
            raise SystemExit("REFUSING: checkpoint reservation identity changed")
        checkpoint = launcher.run_spot_checkpoint(prepared, LR)
        checkpoint_cost = budget.reconcile(
            s3, "spot_checkpoint", checkpoint_attempt,
            checkpoint["actual_seconds"], checkpoint.get("instance_id"))
        stage_descriptor.verify_result(prepared, checkpoint)
        require_checkpoint_result(checkpoint)
        tracker.finish_stage("spot_checkpoint", checkpoint, {
            "checkpoint_tree_sha256": checkpoint["checkpoint_tree_sha256"],
            "operator_interrupted": True,
        })
        checkpoint_snapshot = _sync(
            s3, db, args, "spot-checkpoint",
            checkpoint_tree_sha256=checkpoint["checkpoint_tree_sha256"])

    resume_child = tracker.start_stage(
        "spot_resume", campaign._tracking_params(
            _descriptor_services(
                pins, args.image_digest, tracker.parent_run_id),
            "spot_resume", lr=LR))
    resume = resume_descriptor(
        args, pins, source, tracker.parent_run_id, resume_child, checkpoint)
    resume_path = Path(args.resume_descriptor)
    if resume_path.exists():
        raise SystemExit("REFUSING: resume descriptor path is occupied")
    resume_path.write_text(json.dumps(resume, indent=2, sort_keys=True) + "\n")
    resume_attempt = f"{args.campaign_run}-{args.attempt}-spot-resume"
    held_resume = budget.reserve(s3, "spot_resume", resume_attempt)
    if held_resume["reservation_id"] != resume["reservation_id"]:
        raise SystemExit("REFUSING: resume reservation identity changed")
    resumed = launcher.run_spot_resume(resume, LR)
    resume_cost = budget.reconcile(
        s3, "spot_resume", resume_attempt,
        resumed["actual_seconds"], resumed.get("instance_id"))
    stage_descriptor.verify_result(resume, resumed)
    require_resume_result(resumed, checkpoint)
    tracker.finish_stage("spot_resume", _resume_tracking_result(resumed), {
        "resumed_from_tree_sha256": resumed["resumed_from_tree_sha256"],
        "steps_completed": 200,
        "exact_checkpoint_match": True,
    })
    tracker.finish_parent(True, "exact S3 Spot checkpoint resume proved")
    resume_snapshot = _sync(
        s3, db, args, "spot-resume",
        resumed_from_tree_sha256=resumed["resumed_from_tree_sha256"],
        resumed_checkpoint_tree_sha256=
            resumed["resumed_checkpoint_tree_sha256"])
    final_ledger, _ = budget.load(s3)
    return {
        **packet,
        "checkpoint_instance_id": checkpoint["instance_id"],
        "checkpoint_actual_seconds": checkpoint["actual_seconds"],
        "checkpoint_actual_usd": checkpoint_cost["actual_usd"],
        "checkpoint_tree_sha256": checkpoint["checkpoint_tree_sha256"],
        "checkpoint_root_volume_deleted": checkpoint["root_volume_deleted"],
        "checkpoint_snapshot_sha256": checkpoint_snapshot["sha256"],
        "resume_instance_id": resumed["instance_id"],
        "resume_actual_seconds": resumed["actual_seconds"],
        "resume_actual_usd": resume_cost["actual_usd"],
        "resumed_from_tree_sha256": resumed["resumed_from_tree_sha256"],
        "resumed_checkpoint_tree_sha256":
            resumed["resumed_checkpoint_tree_sha256"],
        "resume_root_volume_deleted": resumed["root_volume_deleted"],
        "resume_snapshot_sha256": resume_snapshot["sha256"],
        "exact_checkpoint_match": True,
        "steps_completed": 200,
        "aggregate_committed_usd": budget.committed_usd(final_ledger),
        "aggregate_remaining_usd": budget.remaining_usd(final_ledger),
        "unresolved_reservations": len(budget.unresolved(final_ledger)),
        "registered_models": 0,
        "b5_allowed": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-run", default=CAMPAIGN_RUN)
    ap.add_argument("--attempt", default=ATTEMPT)
    ap.add_argument("--git-sha", required=True)
    ap.add_argument("--bundle-tar-sha256", required=True)
    ap.add_argument("--image-digest", required=True)
    ap.add_argument("--adoption-key", default=campaign_launch.ADOPTION_KEY)
    ap.add_argument("--mlflow-db", required=True)
    ap.add_argument("--checkpoint-descriptor", required=True)
    ap.add_argument("--resume-descriptor", required=True)
    ap.add_argument(
        "--resume-completed-checkpoint", action="store_true",
        help="recover the immutable terminated first lifecycle and run only "
             "the replacement resume lifecycle")
    ap.add_argument(
        "--recover-completed-resume", action="store_true",
        help="validate two already-terminated immutable lifecycles and close "
             "tracking without a reservation or launch")
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()
    if args.resume_completed_checkpoint and args.recover_completed_resume:
        raise SystemExit("REFUSING: choose one Spot recovery mode")
    if args.recover_completed_resume and not args.confirm:
        raise SystemExit(
            "REFUSING: completed-resume recovery writes an immutable MLflow "
            "snapshot; pass --confirm")
    result = (recover_completed(args) if args.recover_completed_resume
              else execute(args) if args.confirm else prepare(args))
    print(json.dumps(result, indent=2, sort_keys=True))
    if not args.confirm:
        print("PREPARED ONLY — no AWS resource, reservation or S3 object created")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
