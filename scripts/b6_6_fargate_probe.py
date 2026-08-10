#!/usr/bin/env python3
"""Run one bounded, PHI-free Fargate readiness probe with safe evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.b6_6_probe_endpoints import (
    ACCOUNT,
    PROFILE,
    PROBE_SECURITY_GROUP,
    REGION,
    SUBNETS,
    verify_available,
)


CLUSTER = "medzen-b6-window-probe"
TASK_FAMILY = "medzen-b6-window-probe"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/medzen-b6-window-probe-execution"
IMAGE = (
    f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/medzen-rag-index@"
    "sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"
)
TARGET = re.compile(
    r"^http://internal-medzen-b6-window-[0-9]+\.eu-central-1\.elb\.amazonaws\.com/readyz$"
)
ENTRY_POINT = ["/usr/local/bin/python", "-c"]
COMMAND = [
    "import json,os,urllib.request; u=os.environ['TARGET_URL']; r=urllib.request.urlopen(u,timeout=15); v=json.load(r); assert r.status==200 and v.get('ready') is True"
]


class ProbeRefusal(RuntimeError):
    pass


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _task_definition(ecs: Any) -> str:
    value = ecs.describe_task_definition(taskDefinition=TASK_FAMILY)["taskDefinition"]
    containers = value.get("containerDefinitions", [])
    if (
        value.get("family") != TASK_FAMILY
        or value.get("status") != "ACTIVE"
        or value.get("networkMode") != "awsvpc"
        or value.get("cpu") != "256"
        or value.get("memory") != "512"
        or value.get("executionRoleArn") != ROLE_ARN
        or set(value.get("requiresCompatibilities", [])) != {"FARGATE"}
        or len(containers) != 1
    ):
        raise ProbeRefusal("probe task-definition boundary differs")
    container = containers[0]
    environment = {
        item.get("name"): item.get("value") for item in container.get("environment", [])
    }
    if (
        container.get("name") != "probe"
        or container.get("image") != IMAGE
        or container.get("essential") is not True
        or container.get("entryPoint") != ENTRY_POINT
        or container.get("command") != COMMAND
        or container.get("readonlyRootFilesystem") is not True
        or container.get("linuxParameters") != {"initProcessEnabled": True}
        or environment != {"TARGET_URL": "http://not-set.invalid/readyz"}
        or container.get("secrets")
        or container.get("logConfiguration")
    ):
        raise ProbeRefusal("probe container boundary differs")
    arn = value.get("taskDefinitionArn")
    if not isinstance(arn, str) or not arn.startswith(
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{TASK_FAMILY}:"
    ):
        raise ProbeRefusal("probe task-definition ARN differs")
    return arn


def _failure_reason(task: dict[str, Any]) -> str:
    reason = str(task.get("stoppedReason", ""))
    stop_code = str(task.get("stopCode", ""))
    lowered = reason.lower()
    if "ecr" in lowered and (
        "authorization" in lowered or "registry auth" in lowered
    ):
        return "ECR_IMAGE_PULL_FAILURE"
    if "cannotpullcontainer" in lowered or "pull image" in lowered:
        return "IMAGE_PULL_FAILURE"
    if stop_code == "TaskFailedToStart":
        return "TASK_FAILED_TO_START"
    if stop_code == "ServiceSchedulerInitiated":
        return "TASK_STOPPED_BY_SERVICE"
    return "PROBE_CONTAINER_NONZERO_OR_UNKNOWN_STOP"


def _safe_task_result(task: dict[str, Any]) -> dict[str, Any]:
    containers = task.get("containers", [])
    if len(containers) != 1:
        raise ProbeRefusal("probe task container count differs")
    container = containers[0]
    task_arn = str(task.get("taskArn", ""))
    if not task_arn:
        raise ProbeRefusal("probe task ARN is absent")
    exit_code = container.get("exitCode")
    if exit_code == 0 and task.get("lastStatus") == "STOPPED":
        return {
            "status": "PASS",
            "reason_code": "READYZ_ASSERTION_PASSED",
            "task_arn_sha256": _hash(task_arn),
            "container_exit_code": 0,
            "application_started": True,
            "readyz_request_completed": True,
            "assign_public_ip": "DISABLED",
            "private_endpoint_count": 3,
        }
    return {
        "status": "REFUSED",
        "reason_code": _failure_reason(task),
        "task_arn_sha256": _hash(task_arn),
        "task_stop_code": str(task.get("stopCode", "ABSENT")),
        "container_exit_code_present": isinstance(exit_code, int),
        "application_started": bool(container.get("runtimeId")),
        "readyz_request_completed": False,
        "assign_public_ip": "DISABLED",
        "private_endpoint_count": 3,
    }


def run_probe(
    ecs: Any,
    ec2: Any,
    target_url: str,
    wait_seconds: int,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if TARGET.fullmatch(target_url) is None:
        raise ProbeRefusal("probe target URL differs")
    if wait_seconds < 1 or wait_seconds > 600:
        raise ProbeRefusal("probe task wait bound differs")
    verify_available(ec2)
    definition = _task_definition(ecs)
    response = ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=definition,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": sorted(SUBNETS),
                "securityGroups": [PROBE_SECURITY_GROUP],
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": "probe",
                    "environment": [{"name": "TARGET_URL", "value": target_url}],
                }
            ]
        },
    )
    if response.get("failures") or len(response.get("tasks", [])) != 1:
        return {
            "status": "REFUSED",
            "reason_code": "RUN_TASK_REFUSED",
            "application_started": False,
            "readyz_request_completed": False,
            "assign_public_ip": "DISABLED",
            "private_endpoint_count": 3,
        }
    task_arn = response["tasks"][0].get("taskArn", "")
    if not isinstance(task_arn, str) or not task_arn:
        raise ProbeRefusal("run-task response has no task ARN")
    stop = monotonic() + wait_seconds
    while True:
        described = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])
        if described.get("failures") or len(described.get("tasks", [])) != 1:
            raise ProbeRefusal("probe task read-back differs")
        task = described["tasks"][0]
        if task.get("lastStatus") == "STOPPED":
            return _safe_task_result(task)
        if monotonic() >= stop:
            return {
                "status": "REFUSED",
                "reason_code": "PROBE_TASK_TIMEOUT",
                "task_arn_sha256": _hash(task_arn),
                "application_started": False,
                "readyz_request_completed": False,
                "assign_public_ip": "DISABLED",
                "private_endpoint_count": 3,
            }
        sleep(10)


def _clients(profile: str) -> tuple[Any, Any]:
    import boto3

    session = boto3.Session(profile_name=profile, region_name=REGION)
    if session.client("sts").get_caller_identity().get("Account") != ACCOUNT:
        raise ProbeRefusal("AWS account differs")
    return session.client("ecs"), session.client("ec2")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--wait-seconds", type=int, default=600)
    args = parser.parse_args()
    try:
        ecs, ec2 = _clients(args.profile)
        result = run_probe(ecs, ec2, args.target_url, args.wait_seconds)
    except Exception as exc:
        result = {
            "status": "REFUSED",
            "reason_code": type(exc).__name__,
            "application_started": False,
            "readyz_request_completed": False,
        }
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("status") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
