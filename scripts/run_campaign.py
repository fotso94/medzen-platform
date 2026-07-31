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

    def run_preflight() -> dict:
        raise SystemExit(
            "REFUSING: the preflight requires a constructed model on a GPU "
            "instance. scripts/run_preflight.run() is invoked from the trainer "
            "container; this launcher must be executed there, not locally.")

    def evaluate_base(campaign_run: str) -> dict:
        raise SystemExit("REFUSING: base evaluation runs on the GPU instance")

    def train(**kw) -> dict:
        raise SystemExit("REFUSING: training runs on the GPU instance")

    def evaluate_checkpoint(**kw) -> dict:
        raise SystemExit("REFUSING: checkpoint evaluation runs on the GPU instance")

    return campaign.Services(
        s3=cli, verify_policy=verify_policy, verify_adoption=verify_adoption,
        run_preflight=run_preflight, evaluate_base=evaluate_base,
        train=train, evaluate_checkpoint=evaluate_checkpoint,
        mlflow_db=None, register_model=None)


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
    print("ordering      verify-policy -> verify-adoption -> budget-reserve -> "
          "base-eval -> preflight -> 3x sweep -> select -> final -> "
          "6x checkpoint -> cleanup")
    print("registration  disabled by construction (register_model is None)")

    if not a.confirm:
        print("\nDRY RUN — nothing reserved, launched or written. "
              "Pass --confirm to execute.")
        return 0

    cli = s3()
    out = campaign.run_campaign(build_services(cli), a.campaign_run, a.attempt)
    print(json.dumps({k: out[k] for k in
                      ("campaign_run", "selected_lr", "registered_models",
                       "promotable", "purpose", "trace_names")}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
