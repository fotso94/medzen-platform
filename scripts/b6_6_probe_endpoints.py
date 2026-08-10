#!/usr/bin/env python3
"""Verify the principal-independent, probe-exclusive ECR endpoint path."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
VPC_ID = "vpc-051aa9df8b64bf141"
# Execution inputs used by the probe launcher, not endpoint response assertions.
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


def _s3_prefix_lists(ec2: Any) -> list[dict[str, Any]]:
    return ec2.describe_prefix_lists(
        Filters=[
            {
                "Name": "prefix-list-name",
                "Values": [SERVICES["s3"]],
            }
        ]
    ).get("PrefixLists", [])


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
    if not group_id or _tags(group).get("Boundary") != BOUNDARY:
        raise EndpointRefusal("endpoint security group identity differs")
    return group


def _s3_prefix_list_id(ec2: Any) -> str:
    prefix_lists = _s3_prefix_lists(ec2)
    if len(prefix_lists) != 1:
        raise EndpointRefusal("S3 managed prefix list count differs")
    prefix_list = prefix_lists[0]
    prefix_list_id = prefix_list.get("PrefixListId")
    if (
        prefix_list.get("PrefixListName") != SERVICES["s3"]
        or not isinstance(prefix_list_id, str)
        or not prefix_list_id.startswith("pl-")
    ):
        raise EndpointRefusal("S3 managed prefix list identity differs")
    return prefix_list_id


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
    if set(s3.get("RouteTableIds", [])) != {MAIN_ROUTE_TABLE}:
        raise EndpointRefusal("s3 gateway route-table boundary differs")
    _verify_policy(
        str(s3.get("PolicyDocument", "")),
        sid="MinimumEcrLayerBucketRead",
        actions={"s3:GetObject"},
        resources={LAYER_BUCKET_ARN},
    )
    s3_prefix_list_id = _s3_prefix_list_id(ec2)
    return {
        "status": "PASS",
        "interface_endpoint_count": 2,
        "gateway_endpoint_count": 1,
        "endpoint_security_group_id": endpoint_sg,
        "endpoint_policies_verified": 3,
        "network_shape_assertions": 0,
        "connectivity_proof": "DEFERRED_TO_THREE_PROBE_LAUNCHES",
        "s3_prefix_list_id": s3_prefix_list_id,
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
