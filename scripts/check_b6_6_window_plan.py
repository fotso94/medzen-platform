#!/usr/bin/env python3
"""Validate exact create/destroy-only Terraform deltas for B6.6."""
from __future__ import annotations

import argparse
import copy
import json
import re
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
    "aws_vpc_security_group_egress_rule.b6_probe_to_ecr_endpoints[0]",
    "aws_vpc_security_group_egress_rule.b6_probe_to_s3[0]",
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
ALB_ADDRESSES = {
    "aws_vpc_security_group_ingress_rule.b6_alb_from_backend[0]",
    "aws_vpc_security_group_ingress_rule.b6_nodes_from_alb[0]",
}
QUALIFICATION_ADDRESSES = ENDPOINT_ADDRESSES - ALB_ADDRESSES
PLAN_TASK_ENI_SECURITY_GROUPS = {"aws_security_group.b6_probe_endpoints"}
TASK_ENI_EGRESS_RULES = {
    "aws_vpc_security_group_egress_rule.b6_probe_to_ecr_endpoints[0]",
    "aws_vpc_security_group_egress_rule.b6_probe_to_s3[0]",
}
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
AWS_DESCRIPTION_CHARSET = re.compile(
    r"^[A-Za-z0-9. _:/()#,@\[\]+=&;{}!$*\-]*$"
)


def rendered_plan_description_inventory(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every rendered Terraform `description` field and fail on ambiguity."""
    inventory: list[dict[str, Any]] = []

    def visit(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key == "description":
                    if child is None or isinstance(child, str):
                        inventory.append({"path": child_path, "value": child})
                    elif (
                        isinstance(child, dict)
                        and set(child) == {"constant_value"}
                        and isinstance(child["constant_value"], str)
                    ):
                        inventory.append(
                            {
                                "path": f"{child_path}.constant_value",
                                "value": child["constant_value"],
                            }
                        )
                    else:
                        raise ValueError(
                            f"rendered plan description is not a known string at {child_path}"
                        )
                visit(child, child_path)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, f"{path}[{index}]")

    visit(plan, "$")
    return inventory


def lint_rendered_plan_description_charset(plan: dict[str, Any]) -> dict[str, Any]:
    """Refuse any rendered description outside EC2's documented character set."""
    inventory = rendered_plan_description_inventory(plan)
    invalid: list[dict[str, Any]] = []
    for item in inventory:
        value = item["value"]
        if value is not None and AWS_DESCRIPTION_CHARSET.fullmatch(value) is None:
            invalid.append(
                {
                    "path": item["path"],
                    "invalid_codepoints": sorted(
                        {
                            f"U+{ord(character):04X}"
                            for character in value
                            if AWS_DESCRIPTION_CHARSET.fullmatch(character) is None
                        }
                    ),
                }
            )
    if invalid:
        raise ValueError(f"rendered plan description charset differs: {invalid!r}")
    return {
        "status": "PASS",
        "description_fields": len(inventory),
        "string_descriptions": sum(item["value"] is not None for item in inventory),
        "null_descriptions": sum(item["value"] is None for item in inventory),
        "invalid_descriptions": 0,
        "allowed_character_class": "A-Za-z0-9. _-:/()#,@[]+=&;{}!$*",
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


def _references(plan: dict[str, Any], address: str, field: str) -> set[str]:
    return set(_config(plan, address)["expressions"][field].get("references", []))


def _references_only(
    plan: dict[str, Any], address: str, field: str, resource: str
) -> bool:
    references = _references(plan, address, field)
    return resource in references and all(
        item == resource or item.startswith(f"{resource}[")
        for item in references
    )


def lint_task_eni_security_group_egress(
    attached_security_groups: set[str],
    egress_rules_by_security_group: dict[str, set[str]],
) -> dict[str, Any]:
    """Refuse any task-ENI SG with no explicit outbound rule."""
    missing = sorted(
        group
        for group in attached_security_groups
        if not egress_rules_by_security_group.get(group)
    )
    if missing:
        raise ValueError(f"task ENI security group has no egress rule: {missing!r}")
    return {
        "status": "PASS",
        "task_eni_security_groups": len(attached_security_groups),
        "egress_rules": sum(
            len(egress_rules_by_security_group[group])
            for group in attached_security_groups
        ),
        "missing_egress_security_groups": 0,
    }


def validate_task_eni_security_group_egress(plan: dict[str, Any]) -> dict[str, Any]:
    egress_by_group = {group: set() for group in PLAN_TASK_ENI_SECURITY_GROUPS}
    for address in TASK_ENI_EGRESS_RULES:
        references = _references(plan, address, "security_group_id")
        for group in PLAN_TASK_ENI_SECURITY_GROUPS:
            if group in references:
                egress_by_group[group].add(address)
    result = lint_task_eni_security_group_egress(
        PLAN_TASK_ENI_SECURITY_GROUPS, egress_by_group
    )
    if result["egress_rules"] != 2:
        raise ValueError("probe task ENI security group must have exactly two egress rules")
    return result


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
    lint_rendered_plan_description_charset(plan)
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
    ecr_egress_address = (
        "aws_vpc_security_group_egress_rule.b6_probe_to_ecr_endpoints[0]"
    )
    ecr_egress = _after(plan, ecr_egress_address)
    if (
        ecr_egress.get("from_port") != 443
        or ecr_egress.get("to_port") != 443
        or ecr_egress.get("ip_protocol") != "tcp"
        or not _references_only(
            plan,
            ecr_egress_address,
            "security_group_id",
            "aws_security_group.b6_probe_endpoints",
        )
        or not _references_only(
            plan,
            ecr_egress_address,
            "referenced_security_group_id",
            "aws_security_group.b6_probe_endpoints",
        )
    ):
        raise ValueError("probe ECR endpoint egress rule differs")
    s3_egress_address = "aws_vpc_security_group_egress_rule.b6_probe_to_s3[0]"
    s3_egress = _after(plan, s3_egress_address)
    if (
        s3_egress.get("from_port") != 443
        or s3_egress.get("to_port") != 443
        or s3_egress.get("ip_protocol") != "tcp"
        or not _references_only(
            plan,
            s3_egress_address,
            "security_group_id",
            "aws_security_group.b6_probe_endpoints",
        )
        or not _references_only(
            plan,
            s3_egress_address,
            "prefix_list_id",
            "aws_vpc_endpoint.b6_probe_s3",
        )
    ):
        raise ValueError("probe S3 prefix-list egress rule differs")
    validate_task_eni_security_group_egress(plan)
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
    lint_rendered_plan_description_charset(plan)
    actual = changes(plan)
    if actual != {address: ["delete"] for address in ADDRESSES}:
        raise ValueError(f"destroy delta differs: {actual!r}")


def validate_cleanup(plan: dict[str, Any]) -> None:
    lint_rendered_plan_description_charset(plan)
    actual = changes(plan)
    allowed = ADDRESSES
    if not actual or not set(actual).issubset(allowed):
        raise ValueError(f"cleanup delta contains absent or unknown resources: {actual!r}")
    if any(actions != ["delete"] for actions in actual.values()):
        raise ValueError(f"cleanup contains a non-delete action: {actual!r}")


def validate_controller(plan: dict[str, Any]) -> None:
    lint_rendered_plan_description_charset(plan)
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


def validate_qualification(plan: dict[str, Any]) -> None:
    actual = changes(plan)
    if actual != {address: ["create"] for address in QUALIFICATION_ADDRESSES}:
        raise ValueError(f"qualification delta differs: {actual!r}")

    # Reuse the established full boundary validator after proving the real plan
    # contains only the eleven probe resources. These three sentinels represent
    # resources intentionally excluded from isolated Stage A; no sentinel value
    # is read from or applied to Terraform.
    combined = copy.deepcopy(plan)
    combined["resource_changes"].extend(
        [
            {
                "address": CONTROLLER,
                "change": {"actions": ["create"], "after": {}},
            },
            {
                "address": "aws_vpc_security_group_ingress_rule.b6_alb_from_backend[0]",
                "change": {
                    "actions": ["create"],
                    "after": {
                        "security_group_id": "sg-0f0f6c66852830013",
                        "referenced_security_group_id": "sg-0a83abae6ab954543",
                        "from_port": 80,
                        "to_port": 80,
                    },
                },
            },
            {
                "address": "aws_vpc_security_group_ingress_rule.b6_nodes_from_alb[0]",
                "change": {
                    "actions": ["create"],
                    "after": {
                        "security_group_id": "sg-070fc00321934eacb",
                        "referenced_security_group_id": "sg-0f0f6c66852830013",
                        "from_port": 8080,
                        "to_port": 8080,
                    },
                },
            },
        ]
    )
    validate_create(combined)


def validate_qualification_destroy(plan: dict[str, Any]) -> None:
    lint_rendered_plan_description_charset(plan)
    actual = changes(plan)
    if actual != {address: ["delete"] for address in QUALIFICATION_ADDRESSES}:
        raise ValueError(f"qualification destroy delta differs: {actual!r}")


def validate_qualification_cleanup(plan: dict[str, Any]) -> None:
    lint_rendered_plan_description_charset(plan)
    actual = changes(plan)
    if not actual or not set(actual).issubset(QUALIFICATION_ADDRESSES):
        raise ValueError(
            f"qualification cleanup contains absent or unknown resources: {actual!r}"
        )
    if any(actions != ["delete"] for actions in actual.values()):
        raise ValueError(
            f"qualification cleanup contains a non-delete action: {actual!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "create",
            "controller",
            "endpoints",
            "destroy",
            "cleanup",
            "qualification",
            "qualification-destroy",
            "qualification-cleanup",
        ),
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
            "qualification": validate_qualification,
            "qualification-destroy": validate_qualification_destroy,
            "qualification-cleanup": validate_qualification_cleanup,
        }[args.mode](plan)
    except (OSError, KeyError, ValueError, StopIteration, subprocess.SubprocessError) as exc:
        print(f"REFUSING B6.6 {args.mode.upper()}: {exc}", file=sys.stderr)
        return 2
    print(f"PASS_B6_6_{args.mode.upper()} changes={len(changes(load(args.plan)))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
