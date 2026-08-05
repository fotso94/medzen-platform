#!/usr/bin/env python3
"""Refuse unless a saved plan is exactly B6A packet 2026-005."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADDRESS = "aws_ecr_registry_scanning_configuration.b6a_runtime"
FILTERS = {
    ("medzen-model-loader", "WILDCARD"),
    ("medzen-asr-runtime", "WILDCARD"),
    ("medzen-nvidia-dra", "WILDCARD"),
}


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    changed = {
        item.get("address", "<missing-address>"): tuple(
            item.get("change", {}).get("actions", [])
        )
        for item in plan.get("resource_changes", [])
        if tuple(item.get("change", {}).get("actions", [])) != ("no-op",)
    }
    if changed != {ADDRESS: ("create",)}:
        raise ValueError(
            f"resource changes differ: got {changed}, expected only {ADDRESS} create"
        )
    outputs = {
        name: tuple(change.get("actions", []))
        for name, change in plan.get("output_changes", {}).items()
        if tuple(change.get("actions", [])) != ("no-op",)
    }
    if outputs:
        raise ValueError(f"unexpected output changes: {outputs}")

    change = next(
        item["change"] for item in plan["resource_changes"]
        if item.get("address") == ADDRESS
    )
    after = change.get("after")
    if not isinstance(after, dict) or after.get("scan_type") != "BASIC":
        raise ValueError("registry scan type must be BASIC")
    rules = after.get("rule")
    if not isinstance(rules, list) or len(rules) != 1:
        raise ValueError("exactly one B6A scan rule is required")
    rule = rules[0]
    if rule.get("scan_frequency") != "SCAN_ON_PUSH":
        raise ValueError("B6A repositories must use SCAN_ON_PUSH")
    filters = {
        (item.get("filter"), item.get("filter_type"))
        for item in rule.get("repository_filter", [])
        if isinstance(item, dict)
    }
    if filters != FILTERS:
        raise ValueError(
            f"repository filters differ: got {sorted(filters)!r}, "
            f"expected {sorted(FILTERS)!r}"
        )
    if any("*" in name for name, _ in filters):
        raise ValueError("wildcard repository names are forbidden")
    return {
        "status": "PASS_EXACT_B6A_PACKET_2026_005",
        "add": 1,
        "change": 0,
        "destroy": 0,
        "changed_resources": [
            {"address": ADDRESS, "actions": ["create"]}
        ],
        "repository_filters": sorted(name for name, _ in FILTERS),
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
        print(f"REFUSING B6A PACKET 2026-005 APPLY: {exc}", file=sys.stderr)
        return 2
    summary["saved_plan_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    summary["saved_plan_bytes"] = len(plan_bytes)
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
