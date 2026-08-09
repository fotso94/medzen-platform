#!/usr/bin/env python3
"""Validate the one-resource B6.5C publisher-policy transition."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ADDRESS = "aws_iam_role_policy.registry_publisher_b6_5c_tags"
EXPECTED_TAGS = {
    "aws:RequestTag/Project": "medzen-speech",
    "aws:RequestTag/Environment": "dev",
    "aws:RequestTag/CostCenter": "speech-platform",
    "aws:RequestTag/Stage": "B6.5C",
    "aws:RequestTag/Workstream": "ssm-deployment-registry",
    "aws:RequestTag/BudgetRegistry": "COST-REGISTRY-2026-003",
}


def actions(policy: dict) -> set[str]:
    result: set[str] = set()
    for statement in policy.get("Statement", []):
        value = statement.get("Action", [])
        result.update(value if isinstance(value, list) else [value])
    return result


def validate(path: Path) -> None:
    output = subprocess.run(
        ["terraform", "-chdir=infra", "show", "-json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    plan = json.loads(output)
    changes = {
        item["address"]: item["change"]["actions"]
        for item in plan.get("resource_changes", [])
        if item["change"]["actions"] not in (["no-op"], ["read"])
    }
    if changes != {ADDRESS: ["create"]}:
        raise ValueError(f"publisher delta mismatch: {changes!r}")
    resource = next(item for item in plan["resource_changes"] if item["address"] == ADDRESS)
    after = resource["change"]["after"]
    after_policy = json.loads(after["policy"])
    if actions(after_policy) != {"ssm:AddTagsToResource"}:
        raise ValueError("supplemental publisher policy contains another action")
    tag_statement = next(
        item for item in after_policy["Statement"]
        if item.get("Sid") == "TagOnlyB65CDeploymentRegistryParameters"
    )
    if tag_statement.get("Resource") != "arn:aws:ssm:eu-central-1:558069890522:parameter/medzen/registry/*":
        raise ValueError("supplemental tag resource escaped registry prefix")
    if tag_statement.get("Condition", {}).get("StringEquals") != EXPECTED_TAGS:
        raise ValueError("publisher allocation values differ")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check_b6_deployment_registry_plan.py PLAN", file=sys.stderr)
        return 2
    try:
        validate(Path(sys.argv[1]))
    except (OSError, KeyError, ValueError, subprocess.SubprocessError) as exc:
        print(f"REFUSING B6 DEPLOYMENT REGISTRY PLAN: {exc}", file=sys.stderr)
        return 2
    print("PASS_B6_DEPLOYMENT_REGISTRY_PLAN changes=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
