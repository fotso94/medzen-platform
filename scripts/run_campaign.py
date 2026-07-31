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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import campaign                                     # noqa: E402
from pipeline.generation import config_fingerprint                # noqa: E402

BUCKET = "medzen-speech"
POLICY = "platform/decisions/DQ-2026-003-policy-deferral-corrected.json"
ROOT = Path(__file__).resolve().parent.parent


def s3():
    import boto3
    return boto3.Session(region_name="eu-central-1").client("s3")


def build_services(cli) -> campaign.Services:
    """Wire the real implementations. register_model stays None by design."""

    def verify_policy() -> dict:
        from pipeline.train_asr import load_exclusions
        rows, doc, sha = load_exclusions(str(ROOT / POLICY), expect=19)
        return {"policy_sha256": sha, "rows": len(rows),
                "human_review_performed": doc.get("human_review_performed")}

    def verify_adoption() -> dict:
        return json.loads(cli.get_object(
            Bucket=BUCKET,
            Key="curated/_versions/v2/ADOPTION.json")["Body"].read())

    # NOT YET IMPLEMENTED. Marked as placeholders so readiness() can see them
    # BEFORE any reservation or S3 write -- a callable that merely raises when
    # invoked is discovered only after budget has been committed.
    return campaign.Services(
        s3=cli, verify_policy=verify_policy, verify_adoption=verify_adoption,
        run_base_and_preflight=campaign.placeholder(
            "run_base_and_preflight: the direct-EC2 stage adapter is not "
            "implemented"),
        run_sweep=campaign.placeholder(
            "run_sweep: the direct-EC2 stage adapter is not implemented"),
        run_final=campaign.placeholder(
            "run_final: the direct-EC2 stage adapter is not implemented"),
        mlflow_db=None, launcher=None, image_digest=None,
        stage_descriptors=None, register_model=None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign-run", required=True)
    ap.add_argument("--attempt", default="1")
    ap.add_argument("--confirm", action="store_true",
                    help="required. Without it this prints what WOULD run and "
                         "exits without touching anything.")
    a = ap.parse_args()

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
    sv = build_services(cli=None)
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

    cli = s3()
    sv.s3 = cli
    out = campaign.run_campaign(sv, a.campaign_run, a.attempt)
    print(json.dumps({k: out[k] for k in
                      ("campaign_run", "selected_lr", "registered_models",
                       "promotable", "purpose", "trace_names")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
