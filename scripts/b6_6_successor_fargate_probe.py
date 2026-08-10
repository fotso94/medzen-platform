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

from scripts import b6_6_fargate_probe as proven
from scripts.b6_6_successor_probe_endpoints import (
    BACKEND_SECURITY_GROUP,
    PROFILE,
    REGION,
    SUBNETS,
    verify_available,
)


ProbeRefusal = proven.ProbeRefusal


def run_probe(
    ecs: Any,
    ec2: Any,
    target_url: str,
    wait_seconds: int,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if proven.TARGET.fullmatch(target_url) is None:
        raise ProbeRefusal("probe target URL differs")
    if wait_seconds < 1 or wait_seconds > 600:
        raise ProbeRefusal("probe task wait bound differs")
    endpoint = verify_available(ec2)
    endpoint_sg = endpoint["endpoint_security_group_id"]
    definition = proven._task_definition(ecs)
    response = ecs.run_task(
        cluster=proven.CLUSTER,
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
        raise ProbeRefusal("run-task response has no task ARN")
    stop = monotonic() + wait_seconds
    while True:
        described = ecs.describe_tasks(cluster=proven.CLUSTER, tasks=[task_arn])
        if described.get("failures") or len(described.get("tasks", [])) != 1:
            raise ProbeRefusal("probe task read-back differs")
        task = described["tasks"][0]
        if task.get("lastStatus") == "STOPPED":
            result = proven._safe_task_result(task)
            result["probe_task_security_group_count"] = 2
            result["probe_exclusive_endpoint_security_group"] = True
            return result
        if monotonic() >= stop:
            return {
                "status": "REFUSED",
                "reason_code": "PROBE_TASK_TIMEOUT",
                "task_arn_sha256": proven._hash(task_arn),
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
        ecs, ec2 = proven._clients(args.profile)
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
