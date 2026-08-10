#!/usr/bin/env python3
"""Validate exact create/destroy-only Terraform deltas for B6.6."""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ADDRESSES = {
    "helm_release.b6_load_balancer_controller[0]",
    "aws_security_group.b6_probe_endpoints[0]",
    "aws_vpc_security_group_ingress_rule.b6_alb_from_backend[0]",
    "aws_vpc_security_group_ingress_rule.b6_nodes_from_alb[0]",
    "aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]",
    "aws_vpc_endpoint.b6_probe_ecr_api[0]",
    "aws_vpc_endpoint.b6_probe_ecr_dkr[0]",
    "aws_vpc_endpoint.b6_probe_s3[0]",
    "aws_iam_role.b6_probe_execution[0]",
    "aws_iam_role_policy.b6_probe_execution[0]",
    "aws_ecs_cluster.b6_probe[0]",
    "aws_ecs_task_definition.b6_probe[0]",
}
CONTROLLER = "helm_release.b6_load_balancer_controller[0]"
ENDPOINT_ADDRESSES = ADDRESSES - {CONTROLLER}
RAG_DIGEST = "sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"
VPC_ID = "vpc-051aa9df8b64bf141"
PROBE_SG = "sg-0a83abae6ab954543"
ENDPOINT_SG_NAME = "medzen-b6-probe-vpce"
MAIN_ROUTE_TABLE = "rtb-0c6eb6874ce0565dc"
SUBNETS = {
    "subnet-00232b25bc1ac407a",
    "subnet-05029419c6c61a536",
    "subnet-01fb2fc3f56bce55e",
}


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


def _config(plan: dict[str, Any], address: str) -> dict[str, Any]:
    base = address.removesuffix("[0]")
    return next(
        item for item in plan["configuration"]["root_module"]["resources"]
        if item["address"] == base
    )


def _policy_statement(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if value.get("Version") != "2012-10-17":
        raise ValueError("endpoint policy version differs")
    statements = value.get("Statement", [])
    if not isinstance(statements, list) or len(statements) != 1:
        raise ValueError("endpoint policy statement count differs")
    return statements[0]


def _string_set(value: Any, field: str) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    raise ValueError(f"endpoint policy {field} is malformed")


def _principal_set(statement: dict[str, Any]) -> set[str]:
    principal = statement.get("Principal")
    if isinstance(principal, dict):
        principal = principal.get("AWS")
    return _string_set(principal, "principal")


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
    endpoint_group = _after(plan, "aws_security_group.b6_probe_endpoints[0]")
    if (
        endpoint_group.get("name") != ENDPOINT_SG_NAME
        or endpoint_group.get("vpc_id") != VPC_ID
        or endpoint_group.get("ingress") not in (None, [])
        or endpoint_group.get("egress") not in (None, [])
    ):
        raise ValueError("probe endpoint security group differs")
    endpoint_source = _after(
        plan, "aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]"
    )
    if (
        endpoint_source.get("referenced_security_group_id")
        != endpoint_source.get("security_group_id")
        or endpoint_source.get("from_port") != 443
        or endpoint_source.get("to_port") != 443
        or endpoint_source.get("ip_protocol") != "tcp"
    ):
        raise ValueError("probe endpoint source rule differs")
    endpoint_group_references = _config(
        plan, "aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]"
    )["expressions"]["security_group_id"].get("references", [])
    if "aws_security_group.b6_probe_endpoints" not in endpoint_group_references:
        raise ValueError("probe endpoint destination SG reference differs")
    for purpose, service, sid, actions, resources in (
        (
            "ecr_api",
            "com.amazonaws.eu-central-1.ecr.api",
            "ProbeNetworkRegistryToken",
            {"ecr:GetAuthorizationToken"},
            {"*"},
        ),
        (
            "ecr_dkr",
            "com.amazonaws.eu-central-1.ecr.dkr",
            "ProbeNetworkQualifiedImagePull",
            {
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
            },
            {"arn:aws:ecr:eu-central-1:558069890522:repository/medzen-rag-index"},
        ),
    ):
        address = f"aws_vpc_endpoint.b6_probe_{purpose}[0]"
        endpoint = _after(plan, address)
        if (
            endpoint.get("vpc_id") != VPC_ID
            or endpoint.get("service_name") != service
            or endpoint.get("vpc_endpoint_type") != "Interface"
            or set(endpoint.get("subnet_ids") or []) != SUBNETS
            or endpoint.get("private_dns_enabled") is not True
        ):
            raise ValueError(f"{purpose} endpoint network boundary differs")
        references = _config(plan, address)["expressions"]["security_group_ids"].get(
            "references", []
        )
        if "aws_security_group.b6_probe_endpoints" not in references:
            raise ValueError(f"{purpose} endpoint SG reference differs")
        statement = _policy_statement(endpoint.get("policy", ""))
        if (
            statement.get("Sid") != sid
            or statement.get("Effect") != "Allow"
            or _string_set(statement.get("Action"), "actions") != actions
            or _string_set(statement.get("Resource"), "resources") != resources
            or _principal_set(statement) != {"*"}
            or "Condition" in statement
        ):
            raise ValueError(f"{purpose} endpoint policy differs")
    s3 = _after(plan, "aws_vpc_endpoint.b6_probe_s3[0]")
    if (
        s3.get("vpc_id") != VPC_ID
        or s3.get("service_name") != "com.amazonaws.eu-central-1.s3"
        or s3.get("vpc_endpoint_type") != "Gateway"
        or set(s3.get("route_table_ids") or []) != {MAIN_ROUTE_TABLE}
        or s3.get("subnet_ids") not in (None, [])
        or s3.get("security_group_ids") not in (None, [])
        or s3.get("private_dns_enabled") not in (None, False)
    ):
        raise ValueError("s3 endpoint network boundary differs")
    s3_statement = _policy_statement(s3.get("policy", ""))
    if (
        s3_statement.get("Sid") != "MinimumEcrLayerBucketRead"
        or s3_statement.get("Effect") != "Allow"
        or _string_set(s3_statement.get("Action"), "actions") != {"s3:GetObject"}
        or _string_set(s3_statement.get("Resource"), "resources")
        != {"arn:aws:s3:::prod-eu-central-1-starport-layer-bucket/*"}
        or _principal_set(s3_statement) != {"*"}
        or "Condition" in s3_statement
    ):
        raise ValueError("s3 endpoint policy differs")
    task = _after(plan, "aws_ecs_task_definition.b6_probe[0]")
    definitions = task.get("container_definitions", "")
    if RAG_DIGEST not in definitions or "not-set.invalid" not in definitions:
        raise ValueError("probe task image or runtime-only target binding differs")
    role = _after(plan, "aws_iam_role.b6_probe_execution[0]")
    if role.get("name") != "medzen-b6-window-probe-execution":
        raise ValueError("probe execution role differs")


def validate_destroy(plan: dict[str, Any]) -> None:
    actual = changes(plan)
    if actual != {address: ["delete"] for address in ADDRESSES}:
        raise ValueError(f"destroy delta differs: {actual!r}")


def validate_cleanup(plan: dict[str, Any]) -> None:
    actual = changes(plan)
    allowed = ADDRESSES
    if not actual or not set(actual).issubset(allowed):
        raise ValueError(f"cleanup delta contains absent or unknown resources: {actual!r}")
    if any(actions != ["delete"] for actions in actual.values()):
        raise ValueError(f"cleanup contains a non-delete action: {actual!r}")


def validate_controller(plan: dict[str, Any]) -> None:
    actual = changes(plan)
    if actual != {CONTROLLER: ["create"]}:
        raise ValueError(f"controller delta differs: {actual!r}")
    release = _after(plan, CONTROLLER)
    if (
        release.get("name") != "aws-load-balancer-controller"
        or release.get("namespace") != "kube-system"
        or release.get("chart") != "aws-load-balancer-controller"
        or release.get("version") != "3.5.0"
        or release.get("repository") != "https://aws.github.io/eks-charts"
        or release.get("atomic") is not True
        or release.get("wait") is not True
        or release.get("wait_for_jobs") is not True
    ):
        raise ValueError("controller release boundary differs")


def validate_endpoints(plan: dict[str, Any]) -> None:
    actual = changes(plan)
    if actual != {address: ["create"] for address in ENDPOINT_ADDRESSES}:
        raise ValueError(f"endpoint/probe delta differs: {actual!r}")
    combined = copy.deepcopy(plan)
    release = next(
        item for item in combined["resource_changes"] if item["address"] == CONTROLLER
    )
    if release["change"]["actions"] != ["no-op"]:
        raise ValueError("controller is not stable before endpoint creation")
    release["change"]["actions"] = ["create"]
    validate_create(combined)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode", choices=("create", "controller", "endpoints", "destroy", "cleanup")
    )
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan = load(args.plan)
        {
            "create": validate_create,
            "controller": validate_controller,
            "endpoints": validate_endpoints,
            "destroy": validate_destroy,
            "cleanup": validate_cleanup,
        }[args.mode](plan)
    except (OSError, KeyError, ValueError, StopIteration, subprocess.SubprocessError) as exc:
        print(f"REFUSING B6.6 {args.mode.upper()}: {exc}", file=sys.stderr)
        return 2
    print(f"PASS_B6_6_{args.mode.upper()} changes={len(changes(load(args.plan)))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
