#!/usr/bin/env python3
"""Fail closed unless a saved Terraform plan is exactly B6A packet 004."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ACTIONS = {"aws_eks_cluster.this": ("update",)}


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    changed = {
        resource.get("address", "<missing-address>"): tuple(
            resource.get("change", {}).get("actions", [])
        )
        for resource in plan.get("resource_changes", [])
        if tuple(resource.get("change", {}).get("actions", [])) != ("no-op",)
    }
    outputs = {
        name: tuple(change.get("actions", []))
        for name, change in plan.get("output_changes", {}).items()
        if tuple(change.get("actions", [])) != ("no-op",)
    }
    if changed != EXPECTED_ACTIONS:
        raise ValueError(
            f"resource changes differ: got {changed}, expected {EXPECTED_ACTIONS}"
        )
    if outputs:
        raise ValueError(f"unexpected output changes: {outputs}")
    return {
        "status": "PASS_EXACT_B6A_PACKET_2026_004",
        "add": 0,
        "change": 1,
        "destroy": 0,
        "changed_resources": [
            {"address": "aws_eks_cluster.this", "actions": ["update"]}
        ],
    }


def load_saved_plan(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["terraform", f"-chdir={ROOT / 'infra'}", "show", "-json", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "terraform show failed")
    return json.loads(result.stdout)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan_bytes = args.plan.read_bytes()
        summary = validate_plan(load_saved_plan(args.plan))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSING EKS SUPPORT-POLICY APPLY: {exc}", file=sys.stderr)
        return 2
    summary["saved_plan_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    summary["saved_plan_bytes"] = len(plan_bytes)
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
