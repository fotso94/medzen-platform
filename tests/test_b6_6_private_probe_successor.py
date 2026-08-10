from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _policy(sid: str, actions, resources, principal=None) -> str:
    if principal is None:
        principal = {
            "AWS": "arn:aws:iam::558069890522:role/medzen-b6-window-probe-execution"
        }
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": sid,
            "Effect": "Allow",
            "Principal": principal,
            "Action": actions,
            "Resource": resources,
        }],
    })


def _endpoint_set(state: str = "available") -> list[dict]:
    from scripts.b6_6_probe_endpoints import (
        LAYER_BUCKET_ARN,
        MAIN_ROUTE_TABLE,
        REPOSITORY_ARN,
        SERVICES,
        SUBNETS,
    )

    common = {
        "State": state,
        "Tags": [
            {"Key": "Boundary", "Value": "B6-6-PROBE"},
        ],
    }
    return [
        {
            **common,
            "ServiceName": SERVICES["ecr-api"],
            "VpcEndpointType": "Interface",
            "SubnetIds": sorted(SUBNETS),
            "PrivateDnsEnabled": True,
            "Groups": [{"GroupId": "sg-endpoint"}],
            "PolicyDocument": _policy(
                "ExactProbeRoleRegistryToken", "ecr:GetAuthorizationToken", "*"
            ),
            "Tags": [*common["Tags"], {"Key": "EndpointPurpose", "Value": "ecr-api"}],
        },
        {
            **common,
            "ServiceName": SERVICES["ecr-dkr"],
            "VpcEndpointType": "Interface",
            "SubnetIds": sorted(SUBNETS),
            "PrivateDnsEnabled": True,
            "Groups": [{"GroupId": "sg-endpoint"}],
            "PolicyDocument": _policy(
                "ExactProbeRoleQualifiedImagePull",
                [
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                ],
                REPOSITORY_ARN,
            ),
            "Tags": [*common["Tags"], {"Key": "EndpointPurpose", "Value": "ecr-dkr"}],
        },
        {
            **common,
            "ServiceName": SERVICES["s3"],
            "VpcEndpointType": "Gateway",
            "RouteTableIds": [MAIN_ROUTE_TABLE],
            "PolicyDocument": _policy(
                "MinimumEcrLayerBucketRead", "s3:GetObject", LAYER_BUCKET_ARN, "*"
            ),
            "Tags": [*common["Tags"], {"Key": "EndpointPurpose", "Value": "s3"}],
        },
    ]


def _endpoint_group(source: str = "sg-0a83abae6ab954543") -> dict:
    return {
        "GroupId": "sg-endpoint",
        "Tags": [{"Key": "Boundary", "Value": "B6-6-PROBE"}],
        "IpPermissions": [{
            "IpProtocol": "tcp",
            "FromPort": 443,
            "ToPort": 443,
            "UserIdGroupPairs": [{"GroupId": source}],
            "IpRanges": [],
            "Ipv6Ranges": [],
            "PrefixListIds": [],
        }],
        "IpPermissionsEgress": [],
    }


class FakeEC2:
    def __init__(self, endpoints=None, groups=None):
        self.endpoints = [] if endpoints is None else endpoints
        self.groups = [] if groups is None else groups

    def describe_vpc_endpoints(self, Filters):
        assert Filters == [{"Name": "vpc-id", "Values": ["vpc-051aa9df8b64bf141"]}]
        return {"VpcEndpoints": self.endpoints}

    def describe_security_groups(self, Filters):
        assert Filters[0]["Values"] == ["vpc-051aa9df8b64bf141"]
        assert Filters[1]["Values"] == ["medzen-b6-probe-vpce"]
        return {"SecurityGroups": self.groups}


def test_private_endpoint_boundary_is_exact_and_cleanup_treats_deleting_as_present():
    from scripts.b6_6_probe_endpoints import (
        EndpointRefusal,
        verify_absent,
        verify_available,
    )

    ready = FakeEC2(_endpoint_set(), [_endpoint_group()])
    result = verify_available(ready)
    assert result["interface_endpoint_count"] == 2
    assert result["gateway_endpoint_count"] == 1
    assert result["endpoint_ingress_source_security_group"] == "sg-0a83abae6ab954543"
    assert result["endpoint_ingress_port"] == 443
    assert verify_absent(FakeEC2()) == {
        "status": "PASS",
        "vpc_endpoint_count": 0,
        "endpoint_security_group_count": 0,
    }
    with pytest.raises(EndpointRefusal, match="not absent"):
        verify_absent(FakeEC2(_endpoint_set("deleting"), []))


@pytest.mark.parametrize(
    "mutation",
    ["wrong_source", "cidr", "public_dns", "wrong_policy", "s3_role_principal"],
)
def test_private_endpoint_boundary_refuses_network_or_policy_drift(mutation: str):
    from scripts.b6_6_probe_endpoints import EndpointRefusal, verify_available

    endpoints = _endpoint_set()
    group = _endpoint_group()
    if mutation == "wrong_source":
        group = _endpoint_group("sg-wrong")
    elif mutation == "cidr":
        group["IpPermissions"][0]["IpRanges"] = [{"CidrIp": "0.0.0.0/0"}]
    elif mutation == "public_dns":
        endpoints[0]["PrivateDnsEnabled"] = False
    elif mutation == "wrong_policy":
        endpoints[1]["PolicyDocument"] = _policy(
            "ExactProbeRoleQualifiedImagePull", "ecr:*", "*"
        )
    else:
        endpoints[2]["PolicyDocument"] = _policy(
            "MinimumEcrLayerBucketRead",
            "s3:GetObject",
            "arn:aws:s3:::prod-eu-central-1-starport-layer-bucket/*",
        )
    with pytest.raises(EndpointRefusal):
        verify_available(FakeEC2(endpoints, [group]))


def test_endpoint_wait_polls_pending_state_and_never_launches_early():
    from scripts.b6_6_probe_endpoints import wait_available

    class TransitioningEC2(FakeEC2):
        reads = 0

        def describe_vpc_endpoints(self, Filters):
            self.reads += 1
            state = "pending" if self.reads == 1 else "available"
            return {"VpcEndpoints": _endpoint_set(state)}

    now = [0.0]
    ec2 = TransitioningEC2([], [_endpoint_group()])
    result = wait_available(
        ec2,
        30,
        monotonic=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )
    assert result["status"] == "PASS"
    assert ec2.reads == 2
    assert now[0] == 15


def test_endpoint_wait_refuses_terminal_state_without_polling():
    from scripts.b6_6_probe_endpoints import EndpointRefusal, wait_available

    sleeps = []
    with pytest.raises(EndpointRefusal, match="terminal state"):
        wait_available(
            FakeEC2(_endpoint_set("failed"), [_endpoint_group()]),
            30,
            monotonic=lambda: 0.0,
            sleep=sleeps.append,
        )
    assert sleeps == []


class FakeECS:
    task_arn = (
        "arn:aws:ecs:eu-central-1:558069890522:task/"
        "medzen-b6-window-probe/11111111111111111111111111111111"
    )

    def __init__(self, exit_code=0, reason=""):
        self.exit_code = exit_code
        self.reason = reason
        self.run_network = None

    def describe_task_definition(self, taskDefinition):
        from scripts.b6_6_fargate_probe import IMAGE, ROLE_ARN, TASK_FAMILY

        assert taskDefinition == TASK_FAMILY
        return {"taskDefinition": {
            "taskDefinitionArn": (
                "arn:aws:ecs:eu-central-1:558069890522:task-definition/"
                "medzen-b6-window-probe:1"
            ),
            "family": TASK_FAMILY,
            "status": "ACTIVE",
            "networkMode": "awsvpc",
            "cpu": "256",
            "memory": "512",
            "executionRoleArn": ROLE_ARN,
            "requiresCompatibilities": ["FARGATE"],
            "containerDefinitions": [{
                "name": "probe",
                "image": IMAGE,
                "essential": True,
                "entryPoint": ["/usr/local/bin/python", "-c"],
                "command": [
                    "import json,os,urllib.request; u=os.environ['TARGET_URL']; r=urllib.request.urlopen(u,timeout=15); v=json.load(r); assert r.status==200 and v.get('ready') is True"
                ],
                "readonlyRootFilesystem": True,
                "linuxParameters": {"initProcessEnabled": True},
                "environment": [{"name": "TARGET_URL", "value": "http://not-set.invalid/readyz"}],
            }],
        }}

    def run_task(self, **kwargs):
        self.run_network = kwargs["networkConfiguration"]["awsvpcConfiguration"]
        return {"tasks": [{"taskArn": self.task_arn}], "failures": []}

    def describe_tasks(self, cluster, tasks):
        assert tasks == [self.task_arn]
        return {"tasks": [{
            "taskArn": self.task_arn,
            "lastStatus": "STOPPED",
            "stopCode": "TaskFailedToStart" if self.exit_code else "EssentialContainerExited",
            "stoppedReason": self.reason,
            "containers": [{
                "exitCode": self.exit_code,
                "runtimeId": "runtime" if self.exit_code == 0 else "",
            }],
        }], "failures": []}


def test_fargate_probe_rechecks_endpoints_then_uses_no_public_ip():
    from scripts.b6_6_fargate_probe import run_probe

    ecs = FakeECS()
    result = run_probe(
        ecs,
        FakeEC2(_endpoint_set(), [_endpoint_group()]),
        "http://internal-medzen-b6-window-123.eu-central-1.elb.amazonaws.com/readyz",
        60,
    )
    assert result["status"] == "PASS"
    assert result["readyz_request_completed"] is True
    assert ecs.run_network == {
        "subnets": [
            "subnet-00232b25bc1ac407a",
            "subnet-01fb2fc3f56bce55e",
            "subnet-05029419c6c61a536",
        ],
        "securityGroups": ["sg-0a83abae6ab954543"],
        "assignPublicIp": "DISABLED",
    }


def test_fargate_refusal_is_sanitized_and_does_not_persist_raw_aws_reason():
    from scripts.b6_6_fargate_probe import run_probe

    raw = "CannotPullContainerError: private registry details that must not persist"
    result = run_probe(
        FakeECS(1, raw),
        FakeEC2(_endpoint_set(), [_endpoint_group()]),
        "http://internal-medzen-b6-window-123.eu-central-1.elb.amazonaws.com/readyz",
        60,
    )
    assert result["status"] == "REFUSED"
    assert result["reason_code"] == "IMAGE_PULL_FAILURE"
    assert raw not in json.dumps(result)


def _create_plan() -> dict:
    from scripts.check_b6_6_window_plan import ADDRESSES, RAG_DIGEST, SUBNETS

    after = {address: {} for address in ADDRESSES}
    after["aws_vpc_security_group_ingress_rule.b6_alb_from_backend[0]"] = {
        "security_group_id": "sg-0f0f6c66852830013",
        "referenced_security_group_id": "sg-0a83abae6ab954543",
        "from_port": 80,
        "to_port": 80,
    }
    after["aws_vpc_security_group_ingress_rule.b6_nodes_from_alb[0]"] = {
        "security_group_id": "sg-070fc00321934eacb",
        "referenced_security_group_id": "sg-0f0f6c66852830013",
        "from_port": 8080,
        "to_port": 8080,
    }
    after["aws_security_group.b6_probe_endpoints[0]"] = {
        "name": "medzen-b6-probe-vpce",
        "vpc_id": "vpc-051aa9df8b64bf141",
        "ingress": [],
        "egress": [],
    }
    after["aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]"] = {
        "referenced_security_group_id": "sg-0a83abae6ab954543",
        "from_port": 443,
        "to_port": 443,
        "ip_protocol": "tcp",
    }
    for purpose, service, sid, actions, resource in (
        (
            "ecr_api", "ecr.api", "ExactProbeRoleRegistryToken",
            "ecr:GetAuthorizationToken", "*",
        ),
        (
            "ecr_dkr", "ecr.dkr", "ExactProbeRoleQualifiedImagePull",
            ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
            "arn:aws:ecr:eu-central-1:558069890522:repository/medzen-rag-index",
        ),
    ):
        after[f"aws_vpc_endpoint.b6_probe_{purpose}[0]"] = {
            "vpc_id": "vpc-051aa9df8b64bf141",
            "service_name": f"com.amazonaws.eu-central-1.{service}",
            "vpc_endpoint_type": "Interface",
            "subnet_ids": sorted(SUBNETS),
            "private_dns_enabled": True,
            "policy": _policy(sid, actions, resource),
        }
    after["aws_vpc_endpoint.b6_probe_s3[0]"] = {
        "vpc_id": "vpc-051aa9df8b64bf141",
        "service_name": "com.amazonaws.eu-central-1.s3",
        "vpc_endpoint_type": "Gateway",
        "route_table_ids": ["rtb-0c6eb6874ce0565dc"],
        "policy": _policy(
            "MinimumEcrLayerBucketRead",
            "s3:GetObject",
            "arn:aws:s3:::prod-eu-central-1-starport-layer-bucket/*",
            "*",
        ),
    }
    after["aws_ecs_task_definition.b6_probe[0]"] = {
        "container_definitions": f"{RAG_DIGEST} not-set.invalid",
    }
    after["aws_iam_role.b6_probe_execution[0]"] = {
        "name": "medzen-b6-window-probe-execution",
    }
    resources = [
        {
            "address": "aws_security_group.b6_probe_endpoints",
            "expressions": {},
        },
        {
            "address": "aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints",
            "expressions": {
                "security_group_id": {
                    "references": ["aws_security_group.b6_probe_endpoints"]
                }
            },
        },
    ]
    for purpose in ("ecr_api", "ecr_dkr"):
        resources.append({
            "address": f"aws_vpc_endpoint.b6_probe_{purpose}",
            "expressions": {
                "security_group_ids": {
                    "references": ["aws_security_group.b6_probe_endpoints"]
                }
            },
        })
    return {
        "resource_changes": [
            {"address": address, "change": {"actions": ["create"], "after": after[address]}}
            for address in sorted(ADDRESSES)
        ],
        "configuration": {"root_module": {"resources": resources}},
    }


def test_plan_guard_requires_exact_twelve_create_and_fifteen_destroy_resources():
    from scripts.check_b6_6_window_plan import (
        ADDRESSES,
        SECRET_ADDRESSES,
        validate_create,
        validate_destroy,
    )

    create = _create_plan()
    validate_create(create)
    assert len(ADDRESSES) == 12
    assert len(ADDRESSES | SECRET_ADDRESSES) == 15
    destroy = {
        "resource_changes": [
            {"address": address, "change": {"actions": ["delete"], "after": None}}
            for address in sorted(ADDRESSES | SECRET_ADDRESSES)
        ]
    }
    validate_destroy(destroy)


def test_plan_guard_refuses_broad_endpoint_source_or_missing_endpoint():
    from scripts.check_b6_6_window_plan import validate_create

    broad = _create_plan()
    next(
        item for item in broad["resource_changes"]
        if item["address"] == "aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]"
    )["change"]["after"]["referenced_security_group_id"] = "sg-broad"
    with pytest.raises(ValueError, match="source rule differs"):
        validate_create(broad)

    missing = _create_plan()
    missing["resource_changes"] = missing["resource_changes"][:-1]
    with pytest.raises(ValueError, match="create delta differs"):
        validate_create(missing)


def test_runner_persists_endpoint_and_probe_receipts_in_exact_order():
    runner = (ROOT / "scripts/run_b6_6_integration_window.sh").read_text()
    cleanup = (ROOT / "scripts/b6_6_cleanup.sh").read_text()
    assert runner.index("b6_6_probe_endpoints.py available") < runner.index(
        "b6_6_receipt.py endpoints_ready PASS"
    ) < runner.index("rollout status deployment/aws-load-balancer-controller")
    assert runner.index("b6_6_receipt.py fargate_probe PASS") < runner.index(
        "b6_6_lbc_runtime.py verify"
    )
    assert "--wait-seconds 900" in runner
    assert cleanup.index("enable_b6_integration_window=false") < cleanup.index(
        "b6_6_probe_endpoints.py absent"
    ) < cleanup.index("nodegroup-name gpu")


def test_historical_packet_and_refusal_hashes_remain_unchanged():
    import hashlib

    expected = {
        "platform/decisions/B6-AWS-CHANGE-PACKET-2026-013-b6-6-attempt-4.md": (
            "8e286f062aea3148d22689763969995fceafe5134b3f5030396ce7807509e5af"
        ),
        "platform/decisions/B6-AWS-AUTH-2026-013-b6-6-attempt-4.json": (
            "08ce4e252ceedee7930229515b786625b4049437f5bf144a6446ba94e4f4afec"
        ),
        "platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json": (
            "daa679e744738094059a6faae53e5ebb7d44dd920c4da1ba5bac71100049590a"
        ),
    }
    for relative, digest in expected.items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == digest


def test_successor_packet_is_explicitly_non_executable_until_credentials_are_rebound():
    import hashlib
    from scripts.b6_6_bindings import REQUIRED_SOURCES

    packet = (
        ROOT
        / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-014-b6-6-private-probe-successor.md"
    ).read_text()
    assert "EXECUTION BLOCKED ON FRESH SYNTHETIC CREDENTIAL EVIDENCE" in packet
    assert "No `B6-AWS-AUTH-2026-014` record exists" in packet
    assert "exactly `12 add / 0 change / 0 destroy`" in packet
    assert "exactly `0 add / 0 change / 15 destroy`" in packet
    assert "TCP/443 ingress rule whose only source" in packet
    assert "waits at most 900 seconds" in packet
    assert "exactly the three rules above" in packet
    assert "Maximum successor deadline: `9,581 seconds`" in packet
    assert "Until all six are true, **do not approve or execute packet 2026-014**" in packet
    for relative in REQUIRED_SOURCES:
        digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
        assert f"| `{relative}` | `{digest}` |" in packet


def test_local_preparation_evidence_is_non_authorizing_and_packet_bound():
    import hashlib

    evidence = json.loads(
        (
            ROOT
            / "platform/evidence/B6-6-PRIVATE-PROBE-SUCCESSOR-LOCAL-PREPARATION-2026-001.json"
        ).read_bytes()
    )
    packet = ROOT / evidence["packet"]["path"]
    assert evidence["packet"]["authorized"] is False
    assert evidence["packet"]["executable"] is False
    assert evidence["packet"]["owner_authorization_record_created"] is False
    assert hashlib.sha256(packet.read_bytes()).hexdigest() == evidence["packet"]["sha256"]
    assert evidence["implemented_corrections"]["terraform_guards"] == {
        "exact_create_adds": 12,
        "exact_create_changes": 0,
        "exact_create_destroys": 0,
        "exact_full_cleanup_destroys": 15,
        "endpoint_absence_required_before_deadline_disarm": True,
        "deleting_endpoint_counts_as_present": True,
    }
    assert evidence["open_prerequisite"]["packet_execution_permitted"] is False
    assert all(value == 0 for value in evidence["explicit_non_events"].values())
