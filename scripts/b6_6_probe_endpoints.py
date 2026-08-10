#!/usr/bin/env python3
"""Verify the principal-independent, probe-exclusive ECR endpoint path."""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
VPC_ID = "vpc-051aa9df8b64bf141"
BACKEND_SECURITY_GROUP = "sg-0a83abae6ab954543"
ENDPOINT_SECURITY_GROUP_NAME = "medzen-b6-probe-vpce"
MAIN_ROUTE_TABLE = "rtb-0c6eb6874ce0565dc"
SUBNETS = {
    "subnet-00232b25bc1ac407a",
    "subnet-05029419c6c61a536",
    "subnet-01fb2fc3f56bce55e",
}
BOUNDARY = "B6-6-PROBE"
REPOSITORY_ARN = f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/medzen-rag-index"
LAYER_BUCKET_ARN = f"arn:aws:s3:::prod-{REGION}-starport-layer-bucket/*"
SERVICES = {
    "ecr-api": f"com.amazonaws.{REGION}.ecr.api",
    "ecr-dkr": f"com.amazonaws.{REGION}.ecr.dkr",
    "s3": f"com.amazonaws.{REGION}.s3",
}


class EndpointRefusal(RuntimeError):
    pass


class EndpointPending(RuntimeError):
    pass


@dataclass(frozen=True)
class EgressRule:
    """Policy-relevant projection of one EC2 security-group rule."""

    rule_id: str
    group_id: str
    protocol: str
    from_port: int | None
    to_port: int | None
    referenced_group_id: str | None
    prefix_list_id: str | None
    cidr_ipv4: str | None
    cidr_ipv6: str | None


def _client(profile: str) -> Any:
    import boto3

    return boto3.Session(profile_name=profile, region_name=REGION).client("ec2")


def _as_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def _tags(value: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("Key")): str(item.get("Value"))
        for item in value.get("Tags", [])
    }


def _endpoint_security_groups(ec2: Any) -> list[dict[str, Any]]:
    return ec2.describe_security_groups(
        Filters=[
            {"Name": "vpc-id", "Values": [VPC_ID]},
            {"Name": "group-name", "Values": [ENDPOINT_SECURITY_GROUP_NAME]},
        ]
    ).get("SecurityGroups", [])


def _all_service_endpoints(ec2: Any) -> list[dict[str, Any]]:
    response = ec2.describe_vpc_endpoints(
        Filters=[{"Name": "vpc-id", "Values": [VPC_ID]}]
    )
    return [
        item
        for item in response.get("VpcEndpoints", [])
        if item.get("ServiceName") in set(SERVICES.values())
        and item.get("State") != "deleted"
    ]


def verify_absent(ec2: Any) -> dict[str, Any]:
    endpoints = _all_service_endpoints(ec2)
    groups = _endpoint_security_groups(ec2)
    if endpoints or groups:
        raise EndpointRefusal("temporary probe endpoint boundary is not absent")
    return {
        "status": "PASS",
        "probe_vpc_endpoints": 0,
        "probe_endpoint_security_groups": 0,
    }


def _verify_policy(
    raw: str,
    *,
    sid: str,
    actions: set[str],
    resources: set[str],
) -> None:
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise EndpointRefusal("endpoint policy is malformed") from exc
    statements = value.get("Statement", [])
    if not isinstance(statements, list):
        raise EndpointRefusal("endpoint policy statements are malformed")
    allowed = [item for item in statements if item.get("Effect") == "Allow"]
    if len(allowed) != 1 or any(
        item.get("Effect") not in {"Allow", "Deny"} for item in statements
    ):
        raise EndpointRefusal("endpoint policy allow boundary differs")
    statement = allowed[0]
    principal = statement.get("Principal")
    if isinstance(principal, dict):
        principal = principal.get("AWS")
    if (
        statement.get("Sid") != sid
        or statement.get("Effect") != "Allow"
        or _as_set(statement.get("Action")) != actions
        or _as_set(statement.get("Resource")) != resources
        or _as_set(principal) != {"*"}
        or "Condition" in statement
    ):
        raise EndpointRefusal("endpoint policy boundary differs")


def _verify_endpoint_security_group(ec2: Any) -> dict[str, Any]:
    groups = _endpoint_security_groups(ec2)
    if not groups:
        raise EndpointPending("exact endpoint security group is not available")
    if len(groups) != 1:
        raise EndpointRefusal("endpoint security group count differs")
    group = groups[0]
    group_id = str(group.get("GroupId", ""))
    permissions = group.get("IpPermissions", [])
    if (
        not group_id
        or _tags(group).get("Boundary") != BOUNDARY
        or len(permissions) != 1
    ):
        raise EndpointRefusal("endpoint security group identity differs")
    permission = permissions[0]
    pairs = permission.get("UserIdGroupPairs", [])
    if (
        permission.get("IpProtocol") != "tcp"
        or permission.get("FromPort") != 443
        or permission.get("ToPort") != 443
        or len(pairs) != 1
        or {item.get("GroupId") for item in pairs} != {group_id}
        or permission.get("IpRanges")
        or permission.get("Ipv6Ranges")
        or permission.get("PrefixListIds")
    ):
        raise EndpointRefusal(
            "endpoint SG must admit TLS only from its probe-exclusive self reference"
        )
    return group


def _normalize_security_group_rules(response: dict[str, Any]) -> list[EgressRule]:
    """Normalize a real DescribeSecurityGroupRules page into policy values."""
    raw = response.get("SecurityGroupRules")
    if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
        raise EndpointRefusal("security-group-rule response is malformed")
    result: list[EgressRule] = []
    for item in raw:
        if not isinstance(item.get("IsEgress"), bool):
            raise EndpointRefusal("security-group-rule direction is malformed")
        if item["IsEgress"] is not True:
            continue
        referenced = item.get("ReferencedGroupInfo")
        if referenced is not None and not isinstance(referenced, dict):
            raise EndpointRefusal("security-group-rule reference is malformed")
        result.append(
            EgressRule(
                rule_id=str(item.get("SecurityGroupRuleId", "")),
                group_id=str(item.get("GroupId", "")),
                protocol=str(item.get("IpProtocol", "")),
                from_port=item.get("FromPort"),
                to_port=item.get("ToPort"),
                referenced_group_id=(
                    str(referenced.get("GroupId", "")) if referenced else None
                ),
                prefix_list_id=item.get("PrefixListId"),
                cidr_ipv4=item.get("CidrIpv4"),
                cidr_ipv6=item.get("CidrIpv6"),
            )
        )
    return result


def _read_security_group_egress_rules(ec2: Any, group_id: str) -> list[EgressRule]:
    if not group_id:
        raise EndpointRefusal("probe endpoint SG identity is absent")
    rules: list[EgressRule] = []
    token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        request: dict[str, Any] = {
            "Filters": [{"Name": "group-id", "Values": [group_id]}]
        }
        if token is not None:
            request["NextToken"] = token
        response = ec2.describe_security_group_rules(**request)
        if not isinstance(response, dict):
            raise EndpointRefusal("security-group-rule response is malformed")
        rules.extend(_normalize_security_group_rules(response))
        next_token = response.get("NextToken")
        if next_token is None:
            return rules
        if (
            not isinstance(next_token, str)
            or not next_token
            or next_token in seen_tokens
        ):
            raise EndpointRefusal("security-group-rule pagination is malformed")
        seen_tokens.add(next_token)
        token = next_token


def _verify_security_group_egress_rules(
    rules: list[EgressRule], group_id: str, s3_prefix_list_id: str
) -> None:
    if not group_id or not s3_prefix_list_id or len(rules) != 2:
        raise EndpointRefusal("probe endpoint SG egress count or identity differs")
    ecr_rules: list[EgressRule] = []
    s3_rules: list[EgressRule] = []
    for rule in rules:
        if (
            not rule.rule_id.startswith("sgr-")
            or rule.group_id != group_id
            or rule.protocol != "tcp"
            or rule.from_port != 443
            or rule.to_port != 443
        ):
            raise EndpointRefusal("probe endpoint SG egress transport differs")
        has_reference = bool(rule.referenced_group_id)
        has_prefix = bool(rule.prefix_list_id)
        has_cidr = bool(rule.cidr_ipv4) or bool(rule.cidr_ipv6)
        if (
            has_reference
            and not has_prefix
            and not has_cidr
            and rule.referenced_group_id == group_id
        ):
            ecr_rules.append(rule)
        elif (
            has_prefix
            and not has_reference
            and not has_cidr
            and rule.prefix_list_id == s3_prefix_list_id
        ):
            s3_rules.append(rule)
        else:
            raise EndpointRefusal("probe endpoint SG egress destination differs")
    if len(ecr_rules) != 1 or len(s3_rules) != 1:
        raise EndpointRefusal("probe endpoint SG requires ECR and S3 TLS egress")


def _verify_security_group_egress(
    ec2: Any, group_id: str, s3_prefix_list_id: str
) -> None:
    _verify_security_group_egress_rules(
        _read_security_group_egress_rules(ec2, group_id),
        group_id,
        s3_prefix_list_id,
    )


def verify_available(ec2: Any) -> dict[str, Any]:
    endpoint_group = _verify_endpoint_security_group(ec2)
    endpoint_sg = str(endpoint_group["GroupId"])
    endpoints = _all_service_endpoints(ec2)
    if len(endpoints) < 3:
        raise EndpointPending("exact probe endpoint set is not available")
    if len(endpoints) > 3:
        raise EndpointRefusal("probe endpoint count differs")
    by_purpose: dict[str, dict[str, Any]] = {}
    for endpoint in endpoints:
        tags = _tags(endpoint)
        purpose = tags.get("EndpointPurpose", "")
        if tags.get("Boundary") != BOUNDARY or purpose not in SERVICES:
            raise EndpointRefusal("probe endpoint tags differ")
        if purpose in by_purpose:
            raise EndpointRefusal("duplicate probe endpoint purpose")
        by_purpose[purpose] = endpoint
    if set(by_purpose) != set(SERVICES):
        raise EndpointPending("probe endpoint purposes are incomplete")

    for purpose in ("ecr-api", "ecr-dkr"):
        endpoint = by_purpose[purpose]
        state = endpoint.get("State")
        if state in {"pending", "pendingAcceptance"}:
            raise EndpointPending(f"{purpose} endpoint is not available")
        if state != "available":
            raise EndpointRefusal(f"{purpose} endpoint entered a terminal state")
        if (
            endpoint.get("ServiceName") != SERVICES[purpose]
            or endpoint.get("VpcEndpointType") != "Interface"
            or set(endpoint.get("SubnetIds", [])) != SUBNETS
            or endpoint.get("PrivateDnsEnabled") is not True
            or len(endpoint.get("Groups", [])) != 1
            or {item.get("GroupId") for item in endpoint.get("Groups", [])}
            != {endpoint_sg}
        ):
            raise EndpointRefusal(f"{purpose} endpoint network boundary differs")

    _verify_policy(
        str(by_purpose["ecr-api"].get("PolicyDocument", "")),
        sid="ProbeNetworkRegistryToken",
        actions={"ecr:GetAuthorizationToken"},
        resources={"*"},
    )
    _verify_policy(
        str(by_purpose["ecr-dkr"].get("PolicyDocument", "")),
        sid="ProbeNetworkQualifiedImagePull",
        actions={
            "ecr:BatchCheckLayerAvailability",
            "ecr:BatchGetImage",
            "ecr:GetDownloadUrlForLayer",
        },
        resources={REPOSITORY_ARN},
    )

    s3 = by_purpose["s3"]
    state = s3.get("State")
    if state in {"pending", "pendingAcceptance"}:
        raise EndpointPending("s3 endpoint is not available")
    if state != "available":
        raise EndpointRefusal("s3 endpoint entered a terminal state")
    if (
        s3.get("ServiceName") != SERVICES["s3"]
        or s3.get("VpcEndpointType") != "Gateway"
        or set(s3.get("RouteTableIds", [])) != {MAIN_ROUTE_TABLE}
        or s3.get("SubnetIds")
        or s3.get("Groups")
        or not isinstance(s3.get("PrefixListId"), str)
        or not s3.get("PrefixListId")
    ):
        raise EndpointRefusal("s3 endpoint network boundary differs")
    _verify_security_group_egress(ec2, endpoint_sg, str(s3["PrefixListId"]))
    _verify_policy(
        str(s3.get("PolicyDocument", "")),
        sid="MinimumEcrLayerBucketRead",
        actions={"s3:GetObject"},
        resources={LAYER_BUCKET_ARN},
    )
    return {
        "status": "PASS",
        "interface_endpoint_count": 2,
        "gateway_endpoint_count": 1,
        "private_dns_interface_endpoints": 2,
        "interface_subnet_bindings": 6,
        "endpoint_security_group_id": endpoint_sg,
        "endpoint_ingress_source_security_group": endpoint_sg,
        "endpoint_ingress_source_mode": "PROBE_EXCLUSIVE_SELF_REFERENCE",
        "probe_task_security_groups": sorted(
            [BACKEND_SECURITY_GROUP, endpoint_sg]
        ),
        "endpoint_ingress_port": 443,
        "endpoint_egress_rule_count": 2,
        "ecr_egress_destination": "PROBE_EXCLUSIVE_SELF_REFERENCE",
        "s3_egress_prefix_list_id": str(s3["PrefixListId"]),
        "dns_security_group_filtering": (
            "NOT_APPLICABLE_AMAZON_PROVIDED_VPC_RESOLVER"
        ),
        "gateway_route_table_id": MAIN_ROUTE_TABLE,
        "ecr_endpoint_principal_mode": "REQUIRED_WILDCARD_NO_ROLE_REFERENCE",
    }


def wait_available(
    ec2: Any,
    wait_seconds: int,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if wait_seconds < 1 or wait_seconds > 900:
        raise EndpointRefusal("endpoint wait bound differs")
    stop = monotonic() + wait_seconds
    while True:
        try:
            return verify_available(ec2)
        except EndpointPending:
            if monotonic() >= stop:
                raise EndpointRefusal("probe endpoints did not become available in time")
            sleep(15)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("absent", "available"))
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--wait-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        client = _client(args.profile)
        result = (
            verify_absent(client)
            if args.mode == "absent"
            else wait_available(client, args.wait_seconds)
        )
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
