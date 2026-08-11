#!/usr/bin/env python3
"""Run the probe with separate backend and probe-exclusive endpoint SGs."""
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

from scripts.b6_6_probe_endpoints import (
    BACKEND_SECURITY_GROUP,
    PROFILE,
    REGION,
    SUBNETS,
    verify_available,
)


CLUSTER = "medzen-b6-window-probe"
TASK_FAMILY = "medzen-b6-window-probe"
ACCOUNT = "558069890522"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/medzen-b6-window-probe-execution"
IMAGE = (
    f"{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/medzen-rag-index@"
    "sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"
)
TARGET = __import__("re").compile(
    r"^http://internal-medzen-b6-window-[0-9]+\.eu-central-1\.elb\.amazonaws\.com/readyz$"
)
ENTRY_POINT = ["/usr/local/bin/python", "-c"]
PROBE_ATTEMPTS = 24
PROBE_INTERVAL_SECONDS = 10
PROBE_REQUEST_TIMEOUT_SECONDS = 5
PROBE_DNS_EXIT_CODE = 21
PROBE_CONNECT_EXIT_CODE = 22
PROBE_BAD_STATUS_EXIT_CODE = 23
PROBE_PROGRAM = "\n".join(
    [
        "import http.client, json, os, socket, sys, time, urllib.error, urllib.request",
        "url = os.environ['TARGET_URL']",
        f"last_exit = {PROBE_CONNECT_EXIT_CODE}",
        f"for attempt in range({PROBE_ATTEMPTS}):",
        "    try:",
        f"        with urllib.request.urlopen(url, timeout={PROBE_REQUEST_TIMEOUT_SECONDS}) as response:",
        "            status = response.status",
        "            body = json.load(response)",
        "        if status == 200 and body.get('ready') is True:",
        "            sys.exit(0)",
        f"        last_exit = {PROBE_BAD_STATUS_EXIT_CODE}",
        "    except urllib.error.HTTPError:",
        f"        last_exit = {PROBE_BAD_STATUS_EXIT_CODE}",
        "    except urllib.error.URLError as exc:",
        f"        last_exit = {PROBE_DNS_EXIT_CODE} if isinstance(exc.reason, socket.gaierror) else {PROBE_CONNECT_EXIT_CODE}",
        "    except socket.gaierror:",
        f"        last_exit = {PROBE_DNS_EXIT_CODE}",
        "    except (ConnectionError, TimeoutError, OSError):",
        f"        last_exit = {PROBE_CONNECT_EXIT_CODE}",
        "    except http.client.HTTPException:",
        f"        last_exit = {PROBE_CONNECT_EXIT_CODE}",
        "    except (AttributeError, json.JSONDecodeError, UnicodeDecodeError, ValueError):",
        f"        last_exit = {PROBE_BAD_STATUS_EXIT_CODE}",
        f"    if attempt < {PROBE_ATTEMPTS - 1}:",
        f"        time.sleep({PROBE_INTERVAL_SECONDS})",
        "sys.exit(last_exit)",
    ]
)
COMMAND = [PROBE_PROGRAM]
QUALIFICATION_COMMAND = [
    "import sys; assert sys.version_info[:2] >= (3, 12)"
]


class ProbeRefusal(RuntimeError):
    pass


def _hash(value: str) -> str:
    return __import__("hashlib").sha256(value.encode()).hexdigest()


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
        raise ProbeRefusal("PROBE_TASK_DEFINITION_BOUNDARY_DIFFERS")
    container = containers[0]
    linux_parameters = container.get("linuxParameters", {})
    capabilities = linux_parameters.get("capabilities", {})
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
        or linux_parameters.get("initProcessEnabled") is not True
        or capabilities.get("add") not in (None, [])
        or "ALL" not in set(capabilities.get("drop", []))
        or environment != {"TARGET_URL": "http://not-set.invalid/readyz"}
        or container.get("secrets")
        or container.get("logConfiguration")
    ):
        raise ProbeRefusal("PROBE_CONTAINER_BOUNDARY_DIFFERS")
    arn = value.get("taskDefinitionArn")
    if not isinstance(arn, str) or not arn.startswith(
        f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/{TASK_FAMILY}:"
    ):
        raise ProbeRefusal("PROBE_TASK_DEFINITION_ARN_DIFFERS")
    return arn


def _failure_reason(task: dict[str, Any]) -> str:
    reason = str(task.get("stoppedReason", ""))
    stop_code = str(task.get("stopCode", ""))
    lowered = reason.lower()
    if "ecr" in lowered and ("authorization" in lowered or "registry auth" in lowered):
        return "ECR_IMAGE_PULL_FAILURE"
    if "cannotpullcontainer" in lowered or "pull image" in lowered:
        return "IMAGE_PULL_FAILURE"
    if stop_code == "TaskFailedToStart":
        return "TASK_FAILED_TO_START"
    if stop_code == "ServiceSchedulerInitiated":
        return "TASK_STOPPED_BY_SERVICE"
    return "PROBE_CONTAINER_NONZERO_OR_UNKNOWN_STOP"


def _probe_exit_reason(exit_code: Any, task: dict[str, Any]) -> str:
    if exit_code == PROBE_DNS_EXIT_CODE:
        return "PROBE_DNS_RETRIES_EXHAUSTED"
    if exit_code == PROBE_CONNECT_EXIT_CODE:
        return "PROBE_CONNECT_RETRIES_EXHAUSTED"
    if exit_code == PROBE_BAD_STATUS_EXIT_CODE:
        return "PROBE_BAD_STATUS_OR_BODY_RETRIES_EXHAUSTED"
    return _failure_reason(task)


def _safe_task_result(task: dict[str, Any]) -> dict[str, Any]:
    containers = task.get("containers", [])
    if len(containers) != 1:
        raise ProbeRefusal("PROBE_TASK_CONTAINER_COUNT_DIFFERS")
    container = containers[0]
    task_arn = str(task.get("taskArn", ""))
    if not task_arn:
        raise ProbeRefusal("PROBE_TASK_ARN_ABSENT")
    exit_code = container.get("exitCode")
    container_status = container.get("lastStatus")
    application_started = container_status == "RUNNING" or (
        container_status == "STOPPED" and isinstance(exit_code, int)
    )
    if (
        exit_code == 0
        and task.get("lastStatus") == "STOPPED"
        and container_status == "STOPPED"
    ):
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
        "reason_code": _probe_exit_reason(exit_code, task),
        "task_arn_sha256": _hash(task_arn),
        "task_stop_code": str(task.get("stopCode", "ABSENT")),
        "container_exit_code": exit_code if isinstance(exit_code, int) else None,
        "container_exit_code_present": isinstance(exit_code, int),
        "application_started": application_started,
        "readyz_request_completed": False,
        "assign_public_ip": "DISABLED",
        "private_endpoint_count": 3,
    }


def run_isolated_probe(
    ecs: Any,
    ec2: Any,
    wait_seconds: int,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Launch one Stage A task using only the probe-exclusive endpoint SG."""
    if wait_seconds < 1 or wait_seconds > 300:
        raise ProbeRefusal("PROBE_TASK_WAIT_BOUND_DIFFERS")
    endpoint = verify_available(ec2)
    endpoint_sg = endpoint["endpoint_security_group_id"]
    definition = _task_definition(ecs)
    response = ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=definition,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": sorted(SUBNETS),
                "securityGroups": [endpoint_sg],
                "assignPublicIp": "DISABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {"name": "probe", "command": QUALIFICATION_COMMAND}
            ]
        },
    )
    if response.get("failures") or len(response.get("tasks", [])) != 1:
        return {
            "status": "REFUSED",
            "reason_code": "RUN_TASK_REFUSED",
            "application_started": False,
            "image_pull_proven": False,
            "assign_public_ip": "DISABLED",
            "private_endpoint_count": 3,
            "probe_task_security_group_count": 1,
        }
    task_arn = response["tasks"][0].get("taskArn", "")
    if not isinstance(task_arn, str) or not task_arn:
        raise ProbeRefusal("RUN_TASK_RESPONSE_HAS_NO_TASK_ARN")
    stop = monotonic() + wait_seconds
    while True:
        described = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])
        if described.get("failures") or len(described.get("tasks", [])) != 1:
            raise ProbeRefusal("PROBE_TASK_READBACK_DIFFERS")
        task = described["tasks"][0]
        if task.get("lastStatus") == "STOPPED":
            result = _safe_task_result(task)
            if result.get("status") == "PASS":
                result["reason_code"] = "ISOLATED_IMAGE_PULL_AND_PROCESS_EXIT_PASSED"
                result["readyz_request_completed"] = False
            result.update(
                {
                    "qualification_mode": "ISOLATED_STAGE_A",
                    "image_pull_proven": result.get("status") == "PASS",
                    "probe_task_security_group_count": 1,
                    "probe_exclusive_endpoint_security_group": True,
                }
            )
            return result
        if monotonic() >= stop:
            return {
                "status": "REFUSED",
                "reason_code": "PROBE_TASK_TIMEOUT",
                "task_arn_sha256": _hash(task_arn),
                "application_started": False,
                "image_pull_proven": False,
                "assign_public_ip": "DISABLED",
                "private_endpoint_count": 3,
                "probe_task_security_group_count": 1,
                "qualification_mode": "ISOLATED_STAGE_A",
            }
        sleep(10)


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
        raise ProbeRefusal("PROBE_TARGET_URL_DIFFERS")
    if wait_seconds < 1 or wait_seconds > 600:
        raise ProbeRefusal("PROBE_TASK_WAIT_BOUND_DIFFERS")
    endpoint = verify_available(ec2)
    endpoint_sg = endpoint["endpoint_security_group_id"]
    definition = _task_definition(ecs)
    response = ecs.run_task(
        cluster=CLUSTER,
        taskDefinition=definition,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": sorted(SUBNETS),
                "securityGroups": sorted(
                    [BACKEND_SECURITY_GROUP, endpoint_sg]
                ),
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
            "probe_task_security_group_count": 2,
        }
    task_arn = response["tasks"][0].get("taskArn", "")
    if not isinstance(task_arn, str) or not task_arn:
        raise ProbeRefusal("RUN_TASK_RESPONSE_HAS_NO_TASK_ARN")
    stop = monotonic() + wait_seconds
    while True:
        described = ecs.describe_tasks(cluster=CLUSTER, tasks=[task_arn])
        if described.get("failures") or len(described.get("tasks", [])) != 1:
            raise ProbeRefusal("PROBE_TASK_READBACK_DIFFERS")
        task = described["tasks"][0]
        if task.get("lastStatus") == "STOPPED":
            result = _safe_task_result(task)
            result["probe_task_security_group_count"] = 2
            result["probe_exclusive_endpoint_security_group"] = True
            return result
        if monotonic() >= stop:
            return {
                "status": "REFUSED",
                "reason_code": "PROBE_TASK_TIMEOUT",
                "task_arn_sha256": _hash(task_arn),
                "application_started": False,
                "readyz_request_completed": False,
                "assign_public_ip": "DISABLED",
                "private_endpoint_count": 3,
                "probe_task_security_group_count": 2,
            }
        sleep(10)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-url", required=True)
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--wait-seconds", type=int, default=600)
    args = parser.parse_args()
    try:
        import boto3

        session = boto3.Session(profile_name=args.profile, region_name=REGION)
        ecs, ec2 = session.client("ecs"), session.client("ec2")
        result = run_probe(ecs, ec2, args.target_url, args.wait_seconds)
    except ProbeRefusal as exc:
        result = {
            "status": "REFUSED",
            "reason_code": str(exc),
            "application_started": False,
            "readyz_request_completed": False,
        }
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
