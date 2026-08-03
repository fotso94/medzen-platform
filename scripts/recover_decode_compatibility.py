#!/usr/bin/env python3
"""Recover operator-side records after a completed decode-stage disconnect.

This command cannot launch or terminate an instance.  It finalises only an
already-terminated stage whose immutable descriptor, instance tags, container
result, and deleted root volume all match.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import decode_budget, mlflow_sync, stage_descriptor  # noqa: E402
from pipeline.campaign_tracking import CampaignTracker  # noqa: E402
from pipeline.ec2_stage_adapter import EC2StageAdapter  # noqa: E402
from scripts import run_termination_diagnostic as prior  # noqa: E402


def git_head() -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], check=True,
        capture_output=True, text=True).stdout.strip()


def reopen_and_finish_mlflow(
        db: Path, descriptor: dict, result: dict, actual_usd: float
        ) -> CampaignTracker:
    stage_key = "amharic-decode-compatibility"
    tracker = CampaignTracker.recover_existing(
        db, descriptor["campaign_run"], descriptor["attempt"],
        descriptor["mlflow_parent_run_id"],
        {stage_key: descriptor["mlflow_child_run_id"]})
    parent = tracker.client.get_run(tracker.parent_run_id)
    child_id = descriptor["mlflow_child_run_id"]
    child = tracker.client.get_run(child_id)

    # Preserve the observer failure as audit evidence, then restore the two
    # exact bound runs to active status so MLflow accepts the recovered result.
    for run in (parent, child):
        tracker.client.set_terminated(run.info.run_id, status="RUNNING")
        tracker.client.set_tag(
            run.info.run_id, "operator_observer_disconnect_recovered", "true")
        tracker.client.set_tag(
            run.info.run_id, "operator_recovery_git_sha", git_head())
    if parent.data.tags.get("campaign_outcome"):
        tracker.client.set_tag(
            tracker.parent_run_id, "observer_disconnect_error",
            parent.data.tags["campaign_outcome"][:5000])
    if child.data.tags.get("failure_reason"):
        tracker.client.set_tag(
            child_id, "observer_disconnect_error",
            child.data.tags["failure_reason"][:5000])
        tracker.client.delete_tag(child_id, "failure_reason")

    selection = result.get("selection") or {}
    tracker.finish_stage(stage_key, result, {
        "decode_artifact_sha256": result["decode_artifact_sha256"],
        "selected_strategy": selection.get("selected_strategy"),
        "actual_usd": actual_usd,
    })
    tracker.finish_parent(
        True,
        "decode experiment completed; operator observer disconnect recovered; "
        "no training authorised",
    )
    return tracker


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor-key", required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--volume-id", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    if not args.descriptor_key.startswith("candidates/evaluations/"):
        raise SystemExit("REFUSING: descriptor key is outside evaluations")
    session = prior.session()
    s3 = session.client("s3", region_name=prior.REGION)
    raw = s3.get_object(
        Bucket=prior.BUCKET, Key=args.descriptor_key)["Body"].read()
    descriptor = json.loads(raw)
    stage_descriptor.build(**descriptor)
    if descriptor["stage"] != "decode_compatibility":
        raise SystemExit("REFUSING: descriptor is not decode_compatibility")
    canonical_key = (
        descriptor["output_prefix"].rstrip("/") + "/descriptor.json")
    if args.descriptor_key != canonical_key:
        raise SystemExit("REFUSING: descriptor key and output prefix differ")
    packet = {
        "campaign_run": descriptor["campaign_run"],
        "attempt": descriptor["attempt"],
        "stage": descriptor["stage"],
        "stage_descriptor_sha256":
            stage_descriptor.descriptor_hash(descriptor),
        "instance_id": args.instance_id,
        "volume_id": args.volume_id,
        "operator_action": "recover-only; no launch or termination",
    }
    print(json.dumps(packet, indent=2, sort_keys=True))
    if not args.confirm:
        print("VALIDATION ONLY - no writes")
        return 0

    result = EC2StageAdapter(session).recover_terminated(
        descriptor, args.instance_id, args.volume_id)
    stage_descriptor.verify_result(descriptor, result)
    reconciled = decode_budget.reconcile(
        s3, descriptor["stage"], descriptor["attempt"],
        result["actual_seconds"], result["instance_id"])
    reopen_and_finish_mlflow(
        args.db, descriptor, result, reconciled["actual_usd"])
    snapshot = mlflow_sync.sync(
        s3, args.db, descriptor["campaign_run"],
        "amharic-decode-compatibility", attempt=descriptor["attempt"],
        extra={
            "stage_descriptor_sha256":
                stage_descriptor.descriptor_hash(descriptor),
            "decode_artifact_sha256": result["decode_artifact_sha256"],
            "selected_strategy":
                (result.get("selection") or {}).get("selected_strategy"),
            "training_steps": 0,
            "operator_disconnect_recovered": True,
        })
    print(json.dumps({
        **packet,
        "actual_seconds": result["actual_seconds"],
        "actual_usd": reconciled["actual_usd"],
        "root_volume_deleted": result["root_volume_deleted"],
        "exit_status": result["exit_status"],
        "selected_strategy":
            (result.get("selection") or {}).get("selected_strategy"),
        "training_authorised":
            (result.get("selection") or {}).get("training_authorised"),
        "mlflow_snapshot_sha256": snapshot["sha256"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
