#!/usr/bin/env python3
"""THE launch entrypoint for the Option B campaign. There is no other.

It constructs the real services and hands them to `pipeline.campaign`, which
owns the ordering. Everything a reviewer cares about -- policy and adoption
verification, budget reservation, base arm first, preflight before any sweep,
four gates, deterministic selection, write-once prefixes, immutable MLflow
snapshots, zero registration -- lives in that one function, so there is no
second path where a control could be skipped.

    python scripts/run_campaign.py --campaign-run b4-corrected-<sha12> --confirm
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import campaign                                     # noqa: E402
from pipeline.campaign_tracking import CampaignTracker            # noqa: E402
from pipeline.ec2_stage_adapter import EC2StageAdapter             # noqa: E402
from pipeline.generation import config_fingerprint                # noqa: E402
from pipeline.validation_runner import frozen_validation          # noqa: E402

BUCKET = "medzen-speech"
POLICY = "platform/decisions/DQ-2026-003-policy-deferral-corrected.json"
ADOPTION_KEY = "curated/_versions/v2/ADOPTION-B4-CORRECTED.json"
ROOT = Path(__file__).resolve().parent.parent


def aws_session():
    import boto3
    profile = os.environ.get("AWS_PROFILE", "medzen")
    try:
        return boto3.Session(profile_name=profile, region_name="eu-central-1")
    except Exception:
        return boto3.Session(region_name="eu-central-1")


def current_git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True).stdout.strip()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def evaluator_sha() -> str:
    """Bind every file that implements the shared validation path."""
    h = hashlib.sha256()
    for rel in (
        "scripts/evaluate_candidate.py",
        "pipeline/validation_runner.py",
        "pipeline/generation.py",
        "pipeline/normalizers.py",
    ):
        body = (ROOT / rel).read_bytes()
        h.update(rel.encode() + b"\0" + body)
    return h.hexdigest()


class PreviewTracker:
    """Readiness shape for a no-write dry run."""
    parent_run_id = "preview-parent"


class PreviewLauncher:
    """Dry-run service shape. Never invoked."""
    def run_base_and_preflight(self, descriptor):
        raise AssertionError("preview launcher must never execute")

    def run_sweep(self, descriptor, lr):
        raise AssertionError("preview launcher must never execute")

    def run_final(self, descriptor, lr):
        raise AssertionError("preview launcher must never execute")


def build_services(cli, args, *, preview: bool = False,
                   session=None) -> campaign.Services:
    """Wire the real implementations. register_model stays None by design."""

    def verify_policy() -> dict:
        from pipeline.train_asr import load_exclusions
        rows, doc, sha = load_exclusions(str(ROOT / POLICY), expect=19)
        return {"policy_sha256": sha, "rows": len(rows),
                "human_review_performed": doc.get("human_review_performed"),
                "exclusion_checksums_sha256":
                    doc["bindings"]["deferred_checksums_sha256"]}

    def verify_adoption() -> dict:
        from pipeline import review_bindings as RB
        raw = cli.get_object(
            Bucket=BUCKET, Key=args.adoption_key)["Body"].read()
        doc = json.loads(raw)
        complete_raw = cli.get_object(
            Bucket=BUCKET,
            Key="curated/_versions/v2/COMPLETE.json")["Body"].read()
        bindings = RB.recompute(cli)
        problems = [
            p for p in RB.verify(bindings)
            if not p.startswith("uncommitted changes")
        ]
        policy = json.loads((ROOT / POLICY).read_bytes())
        return {
            **doc,
            "current_complete_raw_sha256":
                hashlib.sha256(complete_raw).hexdigest(),
            "manifests_verified": not problems,
            "manifest_verification_problems": problems,
            "exclusion_checksums_sha256":
                doc.get("deferred_checksums_sha256"),
            "dataset_fingerprint": doc.get("dataset_fingerprint"),
            "eligible_rows": doc.get("eligible_rows"),
            "policy_expected_exclusion_checksums_sha256":
                policy["bindings"]["deferred_checksums_sha256"],
        }

    frozen, frozen_sha = frozen_validation()
    git_sha = args.git_sha or current_git_sha()
    policy_sha = file_sha(ROOT / POLICY)
    pins = {
        "git_sha": git_sha,
        "bundle_tar_sha256": args.bundle_tar_sha256,
        "policy_sha256": policy_sha,
        "adoption_key": args.adoption_key,
        "base_manifest_sha256":
            "6a1987d462fc3330bb9eeeb488726bd7a16fd7d67f5aa08f0907eaa59d0913f1",
        "validation_manifest_sha256": frozen_sha,
        "validation_manifest_hashes": {
            language: value["manifest_sha256"]
            for language, value in frozen["sets"].items()
        },
        "generation_config_fingerprint": config_fingerprint(),
        "evaluator_sha256": evaluator_sha(),
        "mlflow_parent_run_id": "preview-parent",
    }
    if preview:
        tracker = PreviewTracker()
        launcher = PreviewLauncher()
        db = Path("/nonexistent/preview-mlflow.db")
    else:
        db = Path(args.mlflow_db)
        tracker = CampaignTracker(
            db, args.campaign_run, attempt=args.attempt)
        pins["mlflow_parent_run_id"] = tracker.parent_run_id
        launcher = EC2StageAdapter(session)
    return campaign.Services(
        s3=cli, verify_policy=verify_policy, verify_adoption=verify_adoption,
        run_base_and_preflight=launcher.run_base_and_preflight,
        run_sweep=launcher.run_sweep,
        run_final=launcher.run_final,
        mlflow_db=db, tracker=tracker, launcher=launcher,
        image_digest=args.image_digest,
        stage_descriptors=pins, register_model=None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-run", required=True)
    ap.add_argument("--attempt", default="1")
    ap.add_argument("--git-sha", default=None)
    ap.add_argument("--bundle-tar-sha256", default="0" * 64)
    ap.add_argument("--image-digest", default="sha256:" + "0" * 64)
    ap.add_argument("--adoption-key", default=ADOPTION_KEY)
    ap.add_argument(
        "--mlflow-db", default=None)
    ap.add_argument("--confirm", action="store_true",
                    help="required. Without it this prints what WOULD run and "
                         "exits without touching anything.")
    a = ap.parse_args()
    if a.mlflow_db is None:
        safe_run = "".join(
            c if c.isalnum() or c in "-_" else "_"
            for c in a.campaign_run)
        a.mlflow_db = (
            f"/tmp/medzen-{safe_run}-attempt-{a.attempt}.mlflow.db")

    print(f"campaign      {a.campaign_run} attempt {a.attempt}")
    print(f"generation    {config_fingerprint()}")
    print(f"policy        {POLICY}")
    print("topology      5 GPU instances: 1 base+preflight, 3 sweeps, 1 final")
    print("ordering      verify-policy -> verify-adoption -> reserve -> "
          "run_base_and_preflight -> 3x run_sweep -> select -> run_final "
          "(gates interleaved at 100..600) -> cleanup")
    print("registration  disabled by construction (register_model is None)")

    # Readiness is checked BEFORE the S3 client is even built, so --confirm on
    # an incomplete wiring cannot read, write or reserve anything.
    sv = build_services(cli=None, args=a, preview=True)
    r = campaign.readiness(sv)
    print(f"readiness    {'READY' if r['ready'] else 'NOT READY'}")
    for prob in r["problems"]:
        print(f"  - {prob}")

    if not a.confirm:
        print("\nDRY RUN — nothing reserved, launched or written. "
              "Pass --confirm to execute.")
        return 0
    if not r["ready"]:
        print("\nREFUSING: --confirm given, but the campaign is not "
              "production-ready. Nothing was read, written or reserved.")
        return 2

    if a.bundle_tar_sha256 == "0" * 64:
        print("REFUSING: --bundle-tar-sha256 is required for execution")
        return 2
    if a.image_digest == "sha256:" + "0" * 64:
        print("REFUSING: --image-digest is required for execution")
        return 2
    if a.git_sha and a.git_sha != current_git_sha():
        print("REFUSING: --git-sha differs from the checked-out commit")
        return 2
    sess = aws_session()
    cli = sess.client("s3", region_name="eu-central-1")
    sv = build_services(cli=cli, args=a, session=sess)
    campaign.require_ready(sv)
    out = campaign.run_campaign(sv, a.campaign_run, a.attempt)
    print(json.dumps({k: out[k] for k in
                      ("campaign_run", "selected_lr", "registered_models",
                       "promotable", "purpose", "trace_names")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
