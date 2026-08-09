#!/usr/bin/env python3
"""Validate exact create/destroy-only Terraform deltas for B6.6."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADDRESSES = {
    "helm_release.b6_load_balancer_controller[0]",
    "aws_vpc_security_group_ingress_rule.b6_alb_from_backend[0]",
    "aws_vpc_security_group_ingress_rule.b6_nodes_from_alb[0]",
    "aws_iam_role.b6_probe_execution[0]",
    "aws_iam_role_policy.b6_probe_execution[0]",
    "aws_ecs_cluster.b6_probe[0]",
    "aws_ecs_task_definition.b6_probe[0]",
}
SECRET_ADDRESSES = {
    "aws_secretsmanager_secret.b6_client_keys[0]",
    "aws_secretsmanager_secret_policy.b6_client_keys[0]",
    "aws_iam_role_policy.b6_client_keys_kms[0]",
}
RAG_DIGEST = "sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"


def load(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        ["terraform", f"-chdir={ROOT / 'infra'}", "show", "-json", str(path)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def changes(plan: dict[str, Any]) -> dict[str, list[str]]:
    return {
        item["address"]: item["change"]["actions"]
        for item in plan.get("resource_changes", [])
        if item["change"]["actions"] not in (["no-op"], ["read"])
    }


def _after(plan: dict[str, Any], address: str) -> dict[str, Any]:
    return next(
        item["change"]["after"] for item in plan["resource_changes"]
        if item["address"] == address
    )


def validate_create(plan: dict[str, Any]) -> None:
    actual = changes(plan)
    if actual != {address: ["create"] for address in ADDRESSES}:
        raise ValueError(f"create delta differs: {actual!r}")
    source = _after(plan, "aws_vpc_security_group_ingress_rule.b6_alb_from_backend[0]")
    if (
        source.get("security_group_id") != "sg-0f0f6c66852830013"
        or source.get("referenced_security_group_id") != "sg-0a83abae6ab954543"
        or source.get("from_port") != 80 or source.get("to_port") != 80
    ):
        raise ValueError("ALB source security-group rule differs")
    target = _after(plan, "aws_vpc_security_group_ingress_rule.b6_nodes_from_alb[0]")
    if (
        target.get("security_group_id") != "sg-070fc00321934eacb"
        or target.get("referenced_security_group_id") != "sg-0f0f6c66852830013"
        or target.get("from_port") != 8080 or target.get("to_port") != 8080
    ):
        raise ValueError("ALB target security-group rule differs")
    task = _after(plan, "aws_ecs_task_definition.b6_probe[0]")
    definitions = task.get("container_definitions", "")
    if RAG_DIGEST not in definitions or "not-set.invalid" not in definitions:
        raise ValueError("probe task image or runtime-only target binding differs")
    role = _after(plan, "aws_iam_role.b6_probe_execution[0]")
    if role.get("name") != "medzen-b6-window-probe-execution":
        raise ValueError("probe execution role differs")


def validate_destroy(plan: dict[str, Any]) -> None:
    actual = changes(plan)
    expected = ADDRESSES | SECRET_ADDRESSES
    if actual != {address: ["delete"] for address in expected}:
        raise ValueError(f"destroy delta differs: {actual!r}")


def validate_cleanup(plan: dict[str, Any]) -> None:
    actual = changes(plan)
    allowed = ADDRESSES | SECRET_ADDRESSES
    if not actual or not set(actual).issubset(allowed):
        raise ValueError(f"cleanup delta contains absent or unknown resources: {actual!r}")
    if any(actions != ["delete"] for actions in actual.values()):
        raise ValueError(f"cleanup contains a non-delete action: {actual!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "destroy", "cleanup"))
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan = load(args.plan)
        {"create": validate_create, "destroy": validate_destroy, "cleanup": validate_cleanup}[args.mode](plan)
    except (OSError, KeyError, ValueError, StopIteration, subprocess.SubprocessError) as exc:
        print(f"REFUSING B6.6 {args.mode.upper()}: {exc}", file=sys.stderr)
        return 2
    print(f"PASS_B6_6_{args.mode.upper()} changes={len(changes(load(args.plan)))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
