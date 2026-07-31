#!/usr/bin/env python3
"""Build and scan the corrected B4 trainer image on one bounded EC2 builder."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.builder_adapter import EC2Builder  # noqa: E402


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], check=True,
        capture_output=True, text=True).stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--git-sha", required=True)
    ap.add_argument("--tar-sha256", required=True)
    ap.add_argument("--attempt", default="attempt-1")
    ap.add_argument("--diagnostic-budget", action="store_true")
    ap.add_argument("--decode-budget", action="store_true")
    ap.add_argument("--confirm", action="store_true")
    a = ap.parse_args()
    if a.diagnostic_budget and a.decode_budget:
        raise SystemExit(
            "REFUSING: choose one builder budget namespace")
    if git("status", "--porcelain"):
        raise SystemExit("REFUSING: worktree is dirty")
    if git("rev-parse", "HEAD") != a.git_sha:
        raise SystemExit("REFUSING: --git-sha differs from local HEAD")
    if not a.confirm:
        if a.diagnostic_budget:
            from pipeline import diagnostic_budget
            max_cost = diagnostic_budget.worst_case_usd("builder")
        elif a.decode_budget:
            from pipeline import decode_budget
            max_cost = decode_budget.worst_case_usd("builder")
        else:
            max_cost = 0.17
        print(json.dumps({
            "ready_to_build": True,
            "would_create": "one c6i.2xlarge direct-EC2 builder",
            "git_sha": a.git_sha,
            "tar_sha256": a.tar_sha256,
            "attempt": a.attempt,
            "max_cost_usd": max_cost,
            "gpu_instances": 0,
            "eks_involved": False,
            "spot_involved": False,
        }, indent=2))
        print("DRY RUN — pass --confirm to reserve budget and launch")
        return 0
    session = boto3.Session(
        profile_name="medzen", region_name="eu-central-1")
    budget_module = None
    if a.diagnostic_budget:
        from pipeline import diagnostic_budget
        budget_module = diagnostic_budget
    elif a.decode_budget:
        from pipeline import decode_budget
        budget_module = decode_budget
    result = EC2Builder(
        session, **({"budget_module": budget_module}
                    if budget_module is not None else {})).run(
        a.git_sha, a.tar_sha256, a.attempt)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
