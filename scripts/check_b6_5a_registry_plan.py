#!/usr/bin/env python3
"""Fail closed unless a saved Terraform plan is exactly B6.5A packet 2026-001."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ADDRESS = "aws_iam_role_policy.registry_publisher"
EXPECTED_ACTIONS = {EXPECTED_ADDRESS: ("update",)}
REQUIRED_NEW_ACTIONS = {"ssm:AddTagsToResource", "ssm:ListTagsForResource"}
REQUIRED_RETAINED_ACTIONS = {
    "ssm:GetParameter",
    "ssm:GetParameters",
    "ssm:GetParametersByPath",
    "ssm:PutParameter",
    "ssm:DeleteParameter",
    "ssm:DeleteParameters",
}


def _policy_actions(policy: str) -> set[str]:
    value = json.loads(policy)
    return {
        action
        for statement in value.get("Statement", [])
        for action in (
            statement.get("Action", [])
            if isinstance(statement.get("Action", []), list)
            else [statement.get("Action")]
        )
        if isinstance(action, str)
    }


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    changed = {
        resource.get("address", "<missing-address>"): tuple(
            resource.get("change", {}).get("actions", [])
        )
        for resource in plan.get("resource_changes", [])
        if tuple(resource.get("change", {}).get("actions", [])) != ("no-op",)
    }
    if changed != EXPECTED_ACTIONS:
        raise ValueError(
            f"resource changes differ: got {changed}, expected {EXPECTED_ACTIONS}"
        )
    outputs = {
        name: tuple(change.get("actions", []))
        for name, change in plan.get("output_changes", {}).items()
        if tuple(change.get("actions", [])) != ("no-op",)
    }
    if outputs:
        raise ValueError(f"unexpected output changes: {outputs}")

    resource = next(
        item for item in plan["resource_changes"]
        if item.get("address") == EXPECTED_ADDRESS
    )
    change = resource.get("change", {})
    before = change.get("before")
    after = change.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise ValueError("publisher policy before/after snapshots are required")
    if set(before) != set(after):
        raise ValueError("publisher policy resource fields changed")
    for key in set(before) - {"policy"}:
        if before[key] != after[key]:
            raise ValueError(f"publisher policy field changed outside policy: {key}")
    before_actions = _policy_actions(before["policy"])
    after_actions = _policy_actions(after["policy"])
    if after_actions - before_actions != REQUIRED_NEW_ACTIONS:
        raise ValueError(
            "publisher action delta differs: "
            f"got {sorted(after_actions - before_actions)}"
        )
    if not REQUIRED_RETAINED_ACTIONS.issubset(after_actions):
        raise ValueError("required read/write/delete-deny actions were not retained")
    policy = json.loads(after["policy"])
    deny = next(
        (statement for statement in policy["Statement"]
         if statement.get("Sid") == "DenyParameterDeletion"),
        None,
    )
    if not deny or deny.get("Effect") != "Deny" or deny.get("Resource") != "*":
        raise ValueError("global parameter deletion deny was not retained")
    tags = next(
        (statement for statement in policy["Statement"]
         if statement.get("Sid") == "TagRegistryParametersForCostAllocation"),
        None,
    )
    if not tags or tags.get("Effect") != "Allow":
        raise ValueError("cost-allocation tag statement is missing")
    conditions = tags.get("Condition", {})
    required_tag_keys = {
        "Project", "Environment", "CostCenter", "Stage", "Workstream",
        "BudgetRegistry",
    }
    if set(conditions.get("StringEquals", {})) != {
        f"aws:RequestTag/{key}" for key in required_tag_keys
    }:
        raise ValueError("request-tag conditions are incomplete or unexpected")
    if set(conditions.get("ForAllValues:StringEquals", {}).get("aws:TagKeys", [])) != required_tag_keys:
        raise ValueError("allowed tag-key set differs from the packet")
    return {
        "status": "PASS_EXACT_B6_5A_PACKET_2026_001",
        "add": 0,
        "change": 1,
        "destroy": 0,
        "changed_resources": [{"address": EXPECTED_ADDRESS, "actions": ["update"]}],
        "added_actions": sorted(REQUIRED_NEW_ACTIONS),
        "global_delete_deny_retained": True,
        "exact_allocation_tag_keys": sorted(required_tag_keys),
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
        raw = args.plan.read_bytes()
        summary = validate_plan(load_saved_plan(args.plan))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSING B6.5A APPLY: {exc}", file=sys.stderr)
        return 2
    summary["saved_plan_sha256"] = hashlib.sha256(raw).hexdigest()
    summary["saved_plan_bytes"] = len(raw)
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
