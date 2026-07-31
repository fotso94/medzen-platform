#!/usr/bin/env python3
"""Launch the one no-training stage prepared by PLAN-2026-004."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import decode_budget, mlflow_sync, stage_descriptor  # noqa: E402
from pipeline.campaign_tracking import CampaignTracker  # noqa: E402
from pipeline.decode_compatibility import strategy_fingerprint  # noqa: E402
from pipeline.ec2_stage_adapter import EC2StageAdapter  # noqa: E402
from pipeline.validation_runner import frozen_validation  # noqa: E402
from scripts import run_termination_diagnostic as prior  # noqa: E402

BUCKET = prior.BUCKET
REGION = prior.REGION
POLICY = prior.POLICY
ADOPTION_KEY = prior.ADOPTION_KEY
BASE_MANIFEST_SHA = prior.BASE_MANIFEST_SHA
BASE_ARM_KEY = prior.BASE_ARM_KEY
BASE_ARTIFACT_KEY = prior.BASE_ARTIFACT_KEY
BASE_ARTIFACT_SHA = prior.BASE_ARTIFACT_SHA
ADAPTER_PREFIX = prior.ADAPTER_PREFIX
ADAPTER_TREE_SHA = prior.ADAPTER_TREE_SHA
ADAPTER_SHA = prior.ADAPTER_SHA
DATASET_FINGERPRINT = prior.DATASET_FINGERPRINT


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True,
        capture_output=True, text=True).stdout.strip()


def evaluator_sha() -> str:
    digest = hashlib.sha256()
    for rel in (
        "pipeline/decode_compatibility.py", "pipeline/stage_runner.py",
        "pipeline/validation_runner.py", "pipeline/generation.py",
        "pipeline/stage_descriptor.py", "pipeline/normalizers.py",
    ):
        digest.update(rel.encode() + b"\0" + (ROOT / rel).read_bytes())
    return digest.hexdigest()


def validate_inputs(args) -> tuple[object, object, dict]:
    head = git("rev-parse", "HEAD")
    if git("status", "--porcelain"):
        raise SystemExit("REFUSING: decode experiment worktree is dirty")
    if args.git_sha != head:
        raise SystemExit("REFUSING: --git-sha differs from local HEAD")
    sess = prior.session()
    s3 = sess.client("s3", region_name=REGION)
    governance = prior.verify_diagnostic_governance(s3)

    bundle = json.loads(s3.get_object(
        Bucket=BUCKET,
        Key=f"candidates/bootstrap/{args.git_sha}/BUNDLE.json"
    )["Body"].read())
    if (bundle.get("git_sha"), bundle.get("tar_sha256")) != (
            args.git_sha, args.bundle_tar_sha256):
        raise SystemExit("REFUSING: decode bundle binding differs")

    base_raw = s3.get_object(
        Bucket=BUCKET, Key=BASE_ARTIFACT_KEY)["Body"].read()
    if sha(base_raw) != BASE_ARTIFACT_SHA:
        raise SystemExit("REFUSING: retained base evaluation changed")
    base_stage = json.loads(s3.get_object(
        Bucket=BUCKET,
        Key=("candidates/evaluations/b4-corrected-18691a5/attempt-5/"
             "base_and_preflight/stage-result.json"))["Body"].read())
    if (base_stage.get("base") or {}).get("base_arm_key") != BASE_ARM_KEY:
        raise SystemExit("REFUSING: retained base arm identity changed")

    adapter = json.loads(s3.get_object(
        Bucket=BUCKET, Key=ADAPTER_PREFIX + "ARTIFACT.json")["Body"].read())
    if (adapter.get("tree_sha256") != ADAPTER_TREE_SHA
            or (adapter.get("files") or {}).get(
                "adapter_model.safetensors", {}).get("sha256")
            != ADAPTER_SHA):
        raise SystemExit("REFUSING: retained 1e-4 adapter changed")

    infra = EC2StageAdapter(sess).preflight_campaign(
        args.git_sha, args.image_digest)
    ledger, _ = decode_budget.load(s3)
    if decode_budget.unresolved(ledger):
        raise SystemExit("REFUSING: decode budget has unresolved spend")
    remaining = decode_budget.remaining_usd(ledger)
    worst = decode_budget.worst_case_usd("decode_compatibility")
    if worst > remaining:
        raise SystemExit("REFUSING: decode stage does not fit its ledger")
    output_prefix = (
        f"candidates/evaluations/{args.campaign_run}/"
        f"attempt-{args.attempt}/decode_compatibility/")
    if s3.list_objects_v2(
            Bucket=BUCKET, Prefix=output_prefix,
            MaxKeys=1).get("KeyCount", 0):
        raise SystemExit("REFUSING: decode output prefix is occupied")
    return sess, s3, {
        "git_sha": args.git_sha,
        "bundle_tar_sha256": args.bundle_tar_sha256,
        "image_digest": args.image_digest,
        "policy_sha256": governance["policy_sha256"],
        "policy_audit_sha256": governance["audit_sha256"],
        "complete_raw_sha256": governance["complete_raw_sha256"],
        "deferred_checksums_sha256":
            governance["deferred_checksums_sha256"],
        "dataset_fingerprint": governance["dataset_fingerprint"],
        "base_artifact_sha256": BASE_ARTIFACT_SHA,
        "adapter_tree_sha256": ADAPTER_TREE_SHA,
        "adapter_sha256": ADAPTER_SHA,
        "strategy_fingerprint": strategy_fingerprint(),
        "decode_worst_case_usd": worst,
        "decode_budget_remaining_usd": remaining,
        "output_prefix": output_prefix,
        "infra": infra,
        "writes_performed": 0,
    }


def make_descriptor(args, tracker: CampaignTracker) -> dict:
    _, frozen_sha = frozen_validation()
    child = tracker.start_stage("amharic-decode-compatibility", {
        "training_steps": 0,
        "retained_adapter_sha256": ADAPTER_SHA,
        "retained_adapter_tree_sha256": ADAPTER_TREE_SHA,
        "strategy_fingerprint": strategy_fingerprint(),
        "code_git_sha": args.git_sha,
        "code_tar_sha256": args.bundle_tar_sha256,
        "image_digest": args.image_digest,
        "purpose": "training_system_validation",
        "promotable": False,
    })
    return stage_descriptor.build(
        campaign_run=args.campaign_run, attempt=args.attempt,
        stage="decode_compatibility", git_sha=args.git_sha,
        bundle_tar_sha256=args.bundle_tar_sha256,
        image_digest=args.image_digest, policy_sha256=sha(POLICY.read_bytes()),
        adoption_key=ADOPTION_KEY, dataset_fingerprint=DATASET_FINGERPRINT,
        base_manifest_sha256=BASE_MANIFEST_SHA,
        validation_manifest_sha256=frozen_sha,
        base_arm_key=BASE_ARM_KEY, base_artifact_key=BASE_ARTIFACT_KEY,
        base_artifact_sha256=BASE_ARTIFACT_SHA,
        generation_config_fingerprint=strategy_fingerprint(),
        evaluator_sha256=evaluator_sha(), lr=1e-4, seed=0,
        max_steps=0, checkpoint_steps=[],
        reservation_id=decode_budget.reservation_id(
            "decode_compatibility", args.attempt),
        watchdog_s=decode_budget.WATCHDOG_S["decode_compatibility"],
        input_prefix=ADAPTER_PREFIX,
        input_artifact_sha256=ADAPTER_TREE_SHA,
        output_prefix=(
            f"candidates/evaluations/{args.campaign_run}/"
            f"attempt-{args.attempt}/decode_compatibility/"),
        mlflow_parent_run_id=tracker.parent_run_id,
        mlflow_child_run_id=child,
        purpose="training_system_validation", promotable=False)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--campaign-run", required=True)
    parser.add_argument("--attempt", default="1")
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--bundle-tar-sha256", required=True)
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    sess, s3, packet = validate_inputs(args)
    print("READ-ONLY DECODE INPUT VALIDATION PASSED")
    print(json.dumps(packet, indent=2, sort_keys=True))
    if not args.confirm:
        print("VALIDATION ONLY - 0 writes, reservations or launches")
        return 0

    db = Path(
        f"/tmp/medzen-{args.campaign_run}-attempt-{args.attempt}.mlflow.db")
    tracker = CampaignTracker(db, args.campaign_run, args.attempt)
    descriptor = make_descriptor(args, tracker)
    decode_budget.reserve(s3, "decode_compatibility", args.attempt)
    try:
        result = EC2StageAdapter(sess).run(descriptor)
        reconciled = decode_budget.reconcile(
            s3, "decode_compatibility", args.attempt,
            result["actual_seconds"], result.get("instance_id"))
        stage_descriptor.verify_result(descriptor, result)
        selection = result.get("selection") or {}
        tracker.finish_stage("amharic-decode-compatibility", result, {
            "decode_artifact_sha256": result["decode_artifact_sha256"],
            "selected_strategy": selection.get("selected_strategy"),
            "actual_usd": reconciled["actual_usd"],
        })
        tracker.finish_parent(True, "decode experiment completed; no training")
        snapshot = mlflow_sync.sync(
            s3, db, args.campaign_run, "amharic-decode-compatibility",
            attempt=args.attempt, extra={
                "stage_descriptor_sha256":
                    stage_descriptor.descriptor_hash(descriptor),
                "decode_artifact_sha256": result["decode_artifact_sha256"],
                "selected_strategy": selection.get("selected_strategy"),
                "training_steps": 0,
            })
    except BaseException as exc:
        tracker.fail_stage("amharic-decode-compatibility", str(exc))
        tracker.finish_parent(False, str(exc))
        raise
    print(json.dumps({
        "campaign_run": args.campaign_run,
        "stage_descriptor_sha256":
            stage_descriptor.descriptor_hash(descriptor),
        "decode_artifact_key": result["decode_artifact_key"],
        "decode_artifact_sha256": result["decode_artifact_sha256"],
        "selected_strategy": selection.get("selected_strategy"),
        "instance_id": result["instance_id"],
        "root_volume_deleted": result["root_volume_deleted"],
        "actual_seconds": result["actual_seconds"],
        "actual_usd": reconciled["actual_usd"],
        "mlflow_snapshot_sha256": snapshot["sha256"],
        "training_steps": 0,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
