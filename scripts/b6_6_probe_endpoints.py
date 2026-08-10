#!/usr/bin/env python3
"""Verify the window-only private image-pull path for the B6.6 probe."""
from __future__ import annotations

import argparse
import json
import time
from typing import Any, Callable


ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
VPC_ID = "vpc-051aa9df8b64bf141"
PROBE_SECURITY_GROUP = "sg-0a83abae6ab954543"
ENDPOINT_SECURITY_GROUP_NAME = "medzen-b6-probe-vpce"
MAIN_ROUTE_TABLE = "rtb-0c6eb6874ce0565dc"
SUBNETS = {
    "subnet-00232b25bc1ac407a",
    "subnet-05029419c6c61a536",
    "subnet-01fb2fc3f56bce55e",
}
BOUNDARY = "B6-6-PROBE"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/medzen-b6-window-probe-execution"
REPOSITORY_ARN = (
    f"arn:aws:ecr:{REGION}:{ACCOUNT}:repository/medzen-rag-index"
)
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


def _tags(value: dict[str, Any]) -> dict[str, str]:
    return {
        str(item.get("Key", "")): str(item.get("Value", ""))
        for item in value.get("Tags", [])
    }


def _as_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    raise EndpointRefusal("endpoint policy field is malformed")


def _verify_policy(
    raw: str,
    *,
    sid: str,
    actions: set[str],
    resources: set[str],
    principals: set[str] | None = None,
) -> None:
    if principals is None:
        principals = {ROLE_ARN}
    try:
        value = json.loads(raw)
    except Exception as exc:
        raise EndpointRefusal("endpoint policy is malformed") from exc
    statements = value.get("Statement", [])
    if not isinstance(statements, list) or len(statements) != 1:
        raise EndpointRefusal("endpoint policy statement count differs")
    statement = statements[0]
    principal = statement.get("Principal")
    if isinstance(principal, dict):
        principal_values = _as_set(principal.get("AWS"))
    else:
        principal_values = _as_set(principal)
    if (
        statement.get("Sid") != sid
        or statement.get("Effect") != "Allow"
        or _as_set(statement.get("Action")) != actions
        or _as_set(statement.get("Resource")) != resources
        or principal_values != principals
        or "Condition" in statement
    ):
        raise EndpointRefusal("endpoint policy boundary differs")


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


def _endpoint_security_groups(ec2: Any) -> list[dict[str, Any]]:
    response = ec2.describe_security_groups(
        Filters=[
            {"Name": "vpc-id", "Values": [VPC_ID]},
            {"Name": "group-name", "Values": [ENDPOINT_SECURITY_GROUP_NAME]},
        ]
    )
    return response.get("SecurityGroups", [])


def verify_absent(ec2: Any) -> dict[str, Any]:
    endpoints = _all_service_endpoints(ec2)
    groups = _endpoint_security_groups(ec2)
    if endpoints or groups:
        raise EndpointRefusal("window-only probe endpoint boundary is not absent")
    return {
        "status": "PASS",
        "vpc_endpoint_count": 0,
        "endpoint_security_group_count": 0,
    }


def _verify_endpoint_security_group(ec2: Any) -> str:
    groups = _endpoint_security_groups(ec2)
    if not groups:
        raise EndpointPending("exact endpoint security group is not available")
    if len(groups) != 1:
        raise EndpointRefusal("endpoint security group count differs")
    group = groups[0]
    if _tags(group).get("Boundary") != BOUNDARY:
        raise EndpointRefusal("endpoint security group boundary tag differs")
    permissions = group.get("IpPermissions", [])
    if len(permissions) != 1:
        raise EndpointRefusal("endpoint security group ingress count differs")
    permission = permissions[0]
    pairs = permission.get("UserIdGroupPairs", [])
    if (
        permission.get("IpProtocol") != "tcp"
        or permission.get("FromPort") != 443
        or permission.get("ToPort") != 443
        or [item.get("GroupId") for item in pairs] != [PROBE_SECURITY_GROUP]
        or permission.get("IpRanges")
        or permission.get("Ipv6Ranges")
        or permission.get("PrefixListIds")
        or group.get("IpPermissionsEgress")
    ):
        raise EndpointRefusal("endpoint security group is not scoped to the probe SG only")
    return str(group.get("GroupId", ""))


def verify_available(ec2: Any) -> dict[str, Any]:
    endpoint_sg = _verify_endpoint_security_group(ec2)
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
            or [item.get("GroupId") for item in endpoint.get("Groups", [])]
            != [endpoint_sg]
        ):
            raise EndpointRefusal(f"{purpose} endpoint network boundary differs")

    _verify_policy(
        str(by_purpose["ecr-api"].get("PolicyDocument", "")),
        sid="ExactProbeRoleRegistryToken",
        actions={"ecr:GetAuthorizationToken"},
        resources={"*"},
    )
    _verify_policy(
        str(by_purpose["ecr-dkr"].get("PolicyDocument", "")),
        sid="ExactProbeRoleQualifiedImagePull",
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
    ):
        raise EndpointRefusal("s3 endpoint network boundary differs")
    _verify_policy(
        str(s3.get("PolicyDocument", "")),
        sid="MinimumEcrLayerBucketRead",
        actions={"s3:GetObject"},
        resources={LAYER_BUCKET_ARN},
        principals={"*"},
    )
    return {
        "status": "PASS",
        "interface_endpoint_count": 2,
        "gateway_endpoint_count": 1,
        "private_dns_interface_endpoints": 2,
        "interface_subnet_bindings": 6,
        "endpoint_security_group_id": endpoint_sg,
        "endpoint_ingress_source_security_group": PROBE_SECURITY_GROUP,
        "endpoint_ingress_port": 443,
        "gateway_route_table_id": MAIN_ROUTE_TABLE,
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


def _client(profile: str) -> Any:
    import boto3

    session = boto3.Session(profile_name=profile, region_name=REGION)
    if session.client("sts").get_caller_identity().get("Account") != ACCOUNT:
        raise EndpointRefusal("AWS account differs")
    return session.client("ec2")


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
