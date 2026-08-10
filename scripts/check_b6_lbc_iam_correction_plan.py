#!/usr/bin/env python3
"""Fail closed unless a Terraform plan is the exact B6 LBC IAM correction."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADDRESS = "aws_iam_role_policy.b6_load_balancer_controller"
ALB_SECURITY_GROUP = "sg-0f0f6c66852830013"
REPLACED_SID = "CreateAndManageExactB6ListenersAndRules"
NEW_SIDS = {
    "CreateOnlyExactB6ListenersOnClusterTaggedAlb",
    "CreateOnlyExactB6RulesOnClusterTaggedListener",
    "ManageOnlyExactClusterTaggedB6Listeners",
    "ManageOnlyExactClusterTaggedB6Rules",
    "TagOnlyDuringExactB6ListenerAndRuleCreation",
}


def load_plan(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [str(ROOT / "scripts/terraform_medzen.sh"), "show", "-json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def changed(plan: dict[str, Any]) -> dict[str, list[str]]:
    return {
        item["address"]: item["change"]["actions"]
        for item in plan.get("resource_changes", [])
        if item["change"]["actions"] not in (["no-op"], ["read"])
    }


def _policy(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict) or value.get("Version") != "2012-10-17":
        raise ValueError("inline policy is malformed or has an unexpected version")
    return value


def _by_sid(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {item.get("Sid"): item for item in policy.get("Statement", [])}
    if None in result or len(result) != len(policy.get("Statement", [])):
        raise ValueError("every policy statement must have a unique Sid")
    return result


def _actions(policy: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for statement in policy["Statement"]:
        actions = statement["Action"]
        result.update(actions if isinstance(actions, list) else [actions])
    return result


def validate(plan: dict[str, Any]) -> None:
    if changed(plan) != {ADDRESS: ["update"]}:
        raise ValueError(f"IAM correction delta mismatch: {changed(plan)!r}")
    resource = next(
        item for item in plan["resource_changes"] if item["address"] == ADDRESS
    )["change"]
    before_raw = resource.get("before") or {}
    after_raw = resource.get("after") or {}
    if before_raw.get("name") != "medzen-lbc-access" or after_raw.get("name") != "medzen-lbc-access":
        raise ValueError("inline policy name changed")
    if before_raw.get("role") != "medzen-lbc-role" or after_raw.get("role") != "medzen-lbc-role":
        raise ValueError("controller role binding changed")

    before = _policy(before_raw.get("policy"))
    after = _policy(after_raw.get("policy"))
    before_by_sid = _by_sid(before)
    after_by_sid = _by_sid(after)
    if REPLACED_SID not in before_by_sid or REPLACED_SID in after_by_sid:
        raise ValueError("the exact defective listener/rule statement was not replaced")
    if set(after_by_sid) - set(before_by_sid) != NEW_SIDS:
        raise ValueError("unexpected statement added to the inline policy")
    if set(before_by_sid) - set(after_by_sid) != {REPLACED_SID}:
        raise ValueError("unexpected statement removed from the inline policy")
    for sid in set(before_by_sid) & set(after_by_sid):
        if before_by_sid[sid] != after_by_sid[sid]:
            raise ValueError(f"unrelated statement changed: {sid}")
    if any(item.get("Effect") == "Deny" for item in before["Statement"]):
        raise ValueError("unexpected pre-existing Deny statement requires separate review")
    if any(item.get("Effect") == "Deny" for item in after["Statement"]):
        raise ValueError("unexpected Deny statement was introduced")
    if _actions(before) != _actions(after):
        raise ValueError("the IAM action set changed")

    expected_text = (
        ROOT / "platform/iam/medzen-lbc-role.policy.template.json"
    ).read_text().replace("${alb_security_group_id}", ALB_SECURITY_GROUP)
    if after != json.loads(expected_text):
        raise ValueError("planned policy does not match the reviewed template")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        validate(load_plan(args.plan))
    except (OSError, KeyError, StopIteration, ValueError, subprocess.SubprocessError) as exc:
        print(f"REFUSING B6 LBC IAM CORRECTION: {exc}", file=sys.stderr)
        return 2
    print("PASS_B6_LBC_IAM_CORRECTION changes=1 add=0 update=1 destroy=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
