#!/usr/bin/env python3
"""Launch the single no-training stage authorised by PLAN-2026-003."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import (campaign, diagnostic_budget, mlflow_sync,  # noqa: E402
                      stage_descriptor)
from pipeline.campaign_tracking import CampaignTracker          # noqa: E402
from pipeline.ec2_stage_adapter import EC2StageAdapter           # noqa: E402
from pipeline.generation import config_fingerprint               # noqa: E402
from pipeline.validation_runner import frozen_validation         # noqa: E402
from scripts.run_campaign import build_services                  # noqa: E402

BUCKET = "medzen-speech"
REGION = "eu-central-1"
POLICY = ROOT / "platform/decisions/DQ-2026-003-policy-deferral-corrected.json"
ADOPTION_KEY = "curated/_versions/v2/ADOPTION-B4-CORRECTED.json"
BASE_MANIFEST_SHA = (
    "6a1987d462fc3330bb9eeeb488726bd7a16fd7d67f5aa08f0907eaa59d0913f1")
BASE_ARM_KEY = (
    "22d437ccff008ede0640d47ed4e94420da6b904dd9d3369696e094ec430bd03e")
BASE_ARTIFACT_KEY = (
    "candidates/evaluations/b4-corrected-18691a5/attempt-5/"
    "base_and_preflight/evaluations/base.json")
BASE_ARTIFACT_SHA = (
    "1803830091c19166290372256d24ed5aa0e6bc5864b62d33b79e4d3af1403a48")
ADAPTER_PREFIX = (
    "candidates/evaluations/b4-corrected-18691a5/attempt-5/"
    "sweep-lr-1e-04/asr/checkpoint-100/")
ADAPTER_TREE_SHA = (
    "5e8ddd18291911c776974fd09cdb291f1bf79da200de657b1159da2b7021ac94")
ADAPTER_SHA = (
    "b9abbbd9c9e7a38b2ca62370308a04991fa32ea089a450bf6f715fe519467eac")
DATASET_FINGERPRINT = (
    "ad8c63d157419cbdbadc1d6a2cf8790c0766d76b848152dbd1be4a1373288275")


def sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True,
        capture_output=True, text=True).stdout.strip()


def evaluator_sha() -> str:
    digest = hashlib.sha256()
    for rel in (
        "pipeline/termination_diagnostic.py", "pipeline/stage_runner.py",
        "pipeline/validation_runner.py", "pipeline/generation.py",
        "pipeline/stage_descriptor.py", "pipeline/normalizers.py",
    ):
        digest.update(rel.encode() + b"\0" + (ROOT / rel).read_bytes())
    return digest.hexdigest()


def session():
    import boto3
    return boto3.Session(
        profile_name=os.environ.get("AWS_PROFILE", "medzen"),
        region_name=REGION)


def validate_inputs(args) -> tuple[object, object, dict]:
    head = git("rev-parse", "HEAD")
    if git("status", "--porcelain"):
        raise SystemExit("REFUSING: diagnostic worktree is dirty")
    if args.git_sha != head:
        raise SystemExit("REFUSING: --git-sha differs from local HEAD")
    sess = session()
    s3 = sess.client("s3", region_name=REGION)

    # Reuse the campaign's full policy/adoption verifier, but not its spent
    # training ledger or launch sequence.
    preview = SimpleNamespace(
        adoption_key=ADOPTION_KEY, git_sha=args.git_sha,
        bundle_tar_sha256=args.bundle_tar_sha256,
        image_digest=args.image_digest, campaign_run=args.campaign_run,
        attempt=args.attempt, mlflow_db="/nonexistent/preview.db")
    services = build_services(s3, preview, preview=True)
    policy, adoption = campaign.verify_governance(services)
    if (adoption.get("dataset_fingerprint") != DATASET_FINGERPRINT
            or adoption.get("eligible_rows") != 4601):
        raise SystemExit("REFUSING: corrected adoption binding changed")

    bundle_key = f"candidates/bootstrap/{args.git_sha}/BUNDLE.json"
    bundle = json.loads(s3.get_object(
        Bucket=BUCKET, Key=bundle_key)["Body"].read())
    if (bundle.get("git_sha"), bundle.get("tar_sha256")) != (
            args.git_sha, args.bundle_tar_sha256):
        raise SystemExit("REFUSING: diagnostic bundle binding differs")

    base_raw = s3.get_object(
        Bucket=BUCKET, Key=BASE_ARTIFACT_KEY)["Body"].read()
    if sha(base_raw) != BASE_ARTIFACT_SHA:
        raise SystemExit("REFUSING: retained base evaluation changed")
    base_stage_key = (
        "candidates/evaluations/b4-corrected-18691a5/attempt-5/"
        "base_and_preflight/stage-result.json")
    base_stage = json.loads(s3.get_object(
        Bucket=BUCKET, Key=base_stage_key)["Body"].read())
    if (base_stage.get("base") or {}).get("base_arm_key") != BASE_ARM_KEY:
        raise SystemExit("REFUSING: retained base arm identity changed")

    adapter_raw = s3.get_object(
        Bucket=BUCKET, Key=ADAPTER_PREFIX + "ARTIFACT.json")["Body"].read()
    adapter = json.loads(adapter_raw)
    if (adapter.get("tree_sha256") != ADAPTER_TREE_SHA
            or (adapter.get("files") or {}).get(
                "adapter_model.safetensors", {}).get("sha256") != ADAPTER_SHA):
        raise SystemExit("REFUSING: retained 1e-4 adapter changed")

    infra = EC2StageAdapter(sess).preflight_campaign(
        args.git_sha, args.image_digest)
    ledger, _ = diagnostic_budget.load(s3)
    if diagnostic_budget.unresolved(ledger):
        raise SystemExit("REFUSING: diagnostic budget has unresolved spend")
    remaining = diagnostic_budget.remaining_usd(ledger)
    worst = diagnostic_budget.worst_case_usd("diagnostic")
    if worst > remaining:
        raise SystemExit("REFUSING: diagnostic stage does not fit its ledger")
    output_prefix = (
        f"candidates/evaluations/{args.campaign_run}/"
        f"attempt-{args.attempt}/diagnostic/")
    if s3.list_objects_v2(
            Bucket=BUCKET, Prefix=output_prefix, MaxKeys=1).get("KeyCount", 0):
        raise SystemExit("REFUSING: diagnostic output prefix is occupied")
    return sess, s3, {
        "git_sha": args.git_sha,
        "bundle_tar_sha256": args.bundle_tar_sha256,
        "image_digest": args.image_digest,
        "policy_sha256": policy["policy_sha256"],
        "dataset_fingerprint": adoption["dataset_fingerprint"],
        "base_artifact_sha256": BASE_ARTIFACT_SHA,
        "adapter_tree_sha256": ADAPTER_TREE_SHA,
        "adapter_sha256": ADAPTER_SHA,
        "diagnostic_worst_case_usd": worst,
        "diagnostic_budget_remaining_usd": remaining,
        "output_prefix": output_prefix,
        "infra": infra,
        "writes_performed": 0,
    }


def make_descriptor(args, tracker: CampaignTracker) -> dict:
    _, frozen_sha = frozen_validation()
    child = tracker.start_stage("termination-diagnostic", {
        "training_steps": 0, "retained_adapter_sha256": ADAPTER_SHA,
        "retained_adapter_tree_sha256": ADAPTER_TREE_SHA,
        "code_git_sha": args.git_sha,
        "code_tar_sha256": args.bundle_tar_sha256,
        "image_digest": args.image_digest,
        "purpose": "training_system_validation", "promotable": False,
    })
    return stage_descriptor.build(
        campaign_run=args.campaign_run, attempt=args.attempt,
        stage="diagnostic", git_sha=args.git_sha,
        bundle_tar_sha256=args.bundle_tar_sha256,
        image_digest=args.image_digest, policy_sha256=sha(POLICY.read_bytes()),
        adoption_key=ADOPTION_KEY, dataset_fingerprint=DATASET_FINGERPRINT,
        base_manifest_sha256=BASE_MANIFEST_SHA,
        validation_manifest_sha256=frozen_sha,
        base_arm_key=BASE_ARM_KEY, base_artifact_key=BASE_ARTIFACT_KEY,
        base_artifact_sha256=BASE_ARTIFACT_SHA,
        generation_config_fingerprint=config_fingerprint(),
        evaluator_sha256=evaluator_sha(), lr=1e-4, seed=0,
        max_steps=0, checkpoint_steps=[],
        reservation_id=diagnostic_budget.reservation_id(
            "diagnostic", args.attempt),
        watchdog_s=diagnostic_budget.WATCHDOG_S["diagnostic"],
        input_prefix=ADAPTER_PREFIX,
        input_artifact_sha256=ADAPTER_TREE_SHA,
        output_prefix=(
            f"candidates/evaluations/{args.campaign_run}/"
            f"attempt-{args.attempt}/diagnostic/"),
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
    print("READ-ONLY DIAGNOSTIC INPUT VALIDATION PASSED")
    print(json.dumps(packet, indent=2, sort_keys=True))
    if not args.confirm:
        print("VALIDATION ONLY — 0 writes, reservations or launches")
        return 0

    db = Path(
        f"/tmp/medzen-{args.campaign_run}-attempt-{args.attempt}.mlflow.db")
    tracker = CampaignTracker(db, args.campaign_run, args.attempt)
    descriptor = make_descriptor(args, tracker)
    reservation = diagnostic_budget.reserve(s3, "diagnostic", args.attempt)
    try:
        result = EC2StageAdapter(sess).run(descriptor)
        reconciled = diagnostic_budget.reconcile(
            s3, "diagnostic", args.attempt, result["actual_seconds"],
            result.get("instance_id"))
        stage_descriptor.verify_result(descriptor, result)
        tracker.finish_stage("termination-diagnostic", result, {
            "diagnostic_artifact_sha256":
                result["diagnostic_artifact_sha256"],
            "actual_usd": reconciled["actual_usd"],
        })
        tracker.finish_parent(True, "diagnostic completed; no training")
        snapshot = mlflow_sync.sync(
            s3, db, args.campaign_run, "termination-diagnostic",
            attempt=args.attempt, extra={
                "stage_descriptor_sha256":
                    stage_descriptor.descriptor_hash(descriptor),
                "diagnostic_artifact_sha256":
                    result["diagnostic_artifact_sha256"],
                "training_steps": 0,
            })
    except BaseException as exc:
        tracker.fail_stage("termination-diagnostic", str(exc))
        tracker.finish_parent(False, str(exc))
        raise
    print(json.dumps({
        "campaign_run": args.campaign_run,
        "stage_descriptor_sha256":
            stage_descriptor.descriptor_hash(descriptor),
        "diagnostic_artifact_key": result["diagnostic_artifact_key"],
        "diagnostic_artifact_sha256": result["diagnostic_artifact_sha256"],
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
