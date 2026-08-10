#!/usr/bin/env python3
"""Audit real-response fixture coverage for every explicit B6.6 AWS read API."""
from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any


EVIDENCE = "platform/evidence/B6-AWS-READ-FIXTURE-CAPTURE-2026-002.json"
STANDING_RULE = "platform/decisions/B6-AWS-READ-FIXTURE-FIDELITY-2026-001.json"
READ_PREFIXES = ("describe_", "list_", "get_", "head_", "lookup_", "simulate_")

PYTHON_SOURCE_APIS: dict[str, dict[str, str]] = {
    "scripts/b6_6_credential.py": {
        "describe_secret": "secretsmanager:DescribeSecret",
        "get_secret_value": "secretsmanager:GetSecretValue",
        "list_secret_version_ids": "secretsmanager:ListSecretVersionIds",
    },
    "scripts/b6_6_deadline.py": {
        "describe_auto_scaling_groups": "autoscaling:DescribeAutoScalingGroups",
        "describe_nodegroup": "eks:DescribeNodegroup",
        "describe_scheduled_actions": "autoscaling:DescribeScheduledActions",
        "get_caller_identity": "sts:GetCallerIdentity",
    },
    "scripts/b6_6_fargate_probe.py": {
        "describe_task_definition": "ecs:DescribeTaskDefinition",
        "describe_tasks": "ecs:DescribeTasks",
    },
    "scripts/b6_6_lbc_runtime.py": {
        "describe_listeners": "elasticloadbalancing:DescribeListeners",
        "describe_load_balancers": "elasticloadbalancing:DescribeLoadBalancers",
        "describe_rules": "elasticloadbalancing:DescribeRules",
        "describe_tags": "elasticloadbalancing:DescribeTags",
        "describe_target_groups": "elasticloadbalancing:DescribeTargetGroups",
        "describe_target_health": "elasticloadbalancing:DescribeTargetHealth",
        "get_caller_identity": "sts:GetCallerIdentity",
    },
    "scripts/b6_6_persistent_secret_bridge.py": {
        "describe_secret": "secretsmanager:DescribeSecret",
        "get_caller_identity": "sts:GetCallerIdentity",
        "get_role": "iam:GetRole",
        "get_secret_value": "secretsmanager:GetSecretValue",
        "get_user": "iam:GetUser",
    },
    "scripts/b6_6_probe_endpoints.py": {
        "describe_prefix_lists": "ec2:DescribePrefixLists",
        "describe_security_groups": "ec2:DescribeSecurityGroups",
        "describe_vpc_endpoints": "ec2:DescribeVpcEndpoints",
    },
    "scripts/b6_6_stage_a.py": {
        "describe_auto_scaling_groups": "autoscaling:DescribeAutoScalingGroups",
        "describe_clusters": "ecs:DescribeClusters",
        "describe_nodegroup": "eks:DescribeNodegroup",
        "get_caller_identity": "sts:GetCallerIdentity",
        "get_role": "iam:GetRole",
        "list_tasks": "ecs:ListTasks",
    },
}

SHELL_SOURCE_APIS: dict[str, set[str]] = {
    "scripts/b6_6_cleanup.sh": {
        "ecs:ListTasks",
        "elasticloadbalancing:DescribeLoadBalancers",
        "secretsmanager:DescribeSecret",
        "ssm:GetParametersByPath",
    },
    "scripts/b6_6_operations.sh": {
        "elasticloadbalancing:DescribeLoadBalancers",
        "ssm:GetParametersByPath",
        "sts:GetCallerIdentity",
    },
}

SERVICE_NAMES = {"elbv2": "elasticloadbalancing"}


def _python_read_methods(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr.startswith(READ_PREFIXES)
    }


def _shell_read_apis(path: Path) -> set[str]:
    result: set[str] = set()
    pattern = re.compile(r"\baws\s+([a-z0-9-]+)\s+([a-z0-9-]+)")
    for service, operation in pattern.findall(path.read_text()):
        if not operation.startswith(
            ("describe-", "list-", "get-", "head-", "lookup-", "simulate-")
        ):
            continue
        canonical_service = SERVICE_NAMES.get(service, service)
        canonical_operation = "".join(part.capitalize() for part in operation.split("-"))
        result.add(f"{canonical_service}:{canonical_operation}")
    return result


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _assert_source_inventory(root: Path) -> set[str]:
    discovered: set[str] = set()
    for relative, method_apis in PYTHON_SOURCE_APIS.items():
        methods = _python_read_methods(root / relative)
        if methods != set(method_apis):
            raise AssertionError(
                f"AWS read methods differ for {relative}: {sorted(methods)!r}"
            )
        discovered.update(method_apis.values())
    for relative, expected in SHELL_SOURCE_APIS.items():
        actual = _shell_read_apis(root / relative)
        if actual != expected:
            raise AssertionError(
                f"AWS shell read APIs differ for {relative}: {sorted(actual)!r}"
            )
        discovered.update(actual)
    return discovered


def _assert_endpoint_reduction(root: Path) -> dict[str, Any]:
    runtime = (root / "scripts/b6_6_probe_endpoints.py").read_text()
    plan = (root / "scripts/check_b6_6_window_plan.py").read_text()
    runtime_forbidden = (
        "describe_security_group_rules",
        'get("SubnetIds"',
        'get("Groups"',
        'get("PrivateDnsEnabled"',
        'get("VpcEndpointType"',
        "IpPermissions",
    )
    plan_forbidden = (
        "subnet_ids",
        "security_group_ids",
        "prefix_list_id",
        "validate_task_eni_security_group_egress",
        "lint_task_eni_security_group_egress",
        "_references_only",
    )
    if any(value in runtime for value in runtime_forbidden):
        raise AssertionError("runtime endpoint verifier retains a network-shape assertion")
    if any(value in plan for value in plan_forbidden):
        raise AssertionError("plan endpoint verifier retains a network-shape assertion")
    if (
        "describe_prefix_lists" not in runtime
        or 's3.get("RouteTableIds"' not in runtime
        or runtime.count("_verify_policy(") != 4
    ):
        raise AssertionError("required basic endpoint or policy verification differs")
    return {
        "status": "PASS",
        "network_shape_assertions": 0,
        "policy_documents_verified": 3,
        "gateway_route_table_assertion": 1,
        "s3_prefix_list_api": "ec2:DescribePrefixLists",
        "connectivity_evidence": "THREE_CONSECUTIVE_PRIVATE_PROBE_LAUNCHES",
        "required_consecutive_probe_passes": 3,
    }


def audit(root: Path) -> dict[str, Any]:
    evidence_path = root / EVIDENCE
    rule_path = root / STANDING_RULE
    evidence = json.loads(evidence_path.read_bytes())
    rule = json.loads(rule_path.read_bytes())
    if (
        evidence.get("status")
        != "PASS_READ_ONLY_LIVE_CAPTURE_COMPLETE_RUNNER_COVERAGE"
        or evidence.get("aws", {}).get("mutations") != 0
        or rule.get("status") != "owner-directed-standing-rule"
    ):
        raise AssertionError("AWS read fixture authority differs")

    discovered = _assert_source_inventory(root)
    inventory = set(evidence.get("runtime_api_inventory", []))
    if discovered != inventory:
        raise AssertionError("AWS read API inventory is incomplete or stale")

    captures = evidence.get("captures")
    if not isinstance(captures, list):
        raise AssertionError("AWS read fixture capture list is malformed")
    coverage: dict[str, int] = {api: 0 for api in inventory}
    fixture_hashes: dict[str, str] = {}
    payloads: dict[str, list[dict[str, Any]]] = {}
    for capture in captures:
        api = capture.get("api")
        relative = capture.get("path")
        digest = capture.get("sha256")
        if api not in inventory or not isinstance(relative, str) or not isinstance(digest, str):
            raise AssertionError("AWS read fixture capture binding is malformed")
        path = root / relative
        if _sha256(path) != digest:
            raise AssertionError(f"AWS read fixture hash differs: {relative}")
        payload = json.loads(path.read_bytes())
        if not isinstance(payload, dict):
            raise AssertionError(f"AWS read fixture is not an object: {relative}")
        coverage[api] += 1
        fixture_hashes[relative] = digest
        payloads.setdefault(api, []).append(payload)
    if any(count < 1 for count in coverage.values()):
        raise AssertionError("at least one runner AWS read API has no real fixture")

    gateway = next(
        endpoint
        for payload in payloads["ec2:DescribeVpcEndpoints"]
        for endpoint in payload.get("VpcEndpoints", [])
        if endpoint.get("VpcEndpointType") == "Gateway"
    )
    interfaces = [
        endpoint
        for payload in payloads["ec2:DescribeVpcEndpoints"]
        for endpoint in payload.get("VpcEndpoints", [])
        if endpoint.get("VpcEndpointType") == "Interface"
    ]
    prefix_lists = payloads["ec2:DescribePrefixLists"][0].get("PrefixLists", [])
    groups = payloads["ec2:DescribeSecurityGroups"][0].get("SecurityGroups", [])
    denied = payloads["secretsmanager:GetSecretValue"][0]
    if (
        not interfaces
        or gateway.get("State") != "available"
        or "PrefixListId" in gateway
        or not gateway.get("RouteTableIds")
        or len(prefix_lists) != 1
        or prefix_lists[0].get("PrefixListId") != "pl-6ea54007"
        or not groups
        or denied.get("Error", {}).get("Code") != "AccessDeniedException"
        or "SecretString" in json.dumps(denied)
    ):
        raise AssertionError("recorded AWS response facts differ")

    return {
        "status": "PASS",
        "standing_rule_path": STANDING_RULE,
        "standing_rule_sha256": _sha256(rule_path),
        "evidence_path": EVIDENCE,
        "evidence_sha256": _sha256(evidence_path),
        "runtime_read_api_count": len(inventory),
        "fixture_count": len(captures),
        "uncovered_read_apis": 0,
        "fixture_hashes": dict(sorted(fixture_hashes.items())),
        "describe_vpc_endpoints_prefix_list_id_present": False,
        "s3_prefix_list_id": "pl-6ea54007",
        "network_reduction": _assert_endpoint_reduction(root),
        "real_aws_calls": 0,
    }


if __name__ == "__main__":
    print(json.dumps(audit(Path(__file__).resolve().parents[1]), sort_keys=True))
