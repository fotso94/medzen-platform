#!/usr/bin/env python3
"""Wait for the exact B6.6 worker set to exist and become Ready."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Callable


EXPECTED = {"cpu": 2, "gpu": 1}
STABLE_OBSERVATIONS = 3
POLL_SECONDS = 5


class WorkerReadinessRefusal(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _ready(node: dict[str, Any]) -> bool:
    conditions = node.get("status", {}).get("conditions", [])
    return any(
        item.get("type") == "Ready" and item.get("status") == "True"
        for item in conditions
    )


def kubectl_snapshot(kubeconfig: Path, workload: str) -> list[dict[str, Any]]:
    result = subprocess.run(
        [
            "kubectl", "--kubeconfig", str(kubeconfig), "get", "nodes",
            "-l", f"workload={workload}", "--request-timeout=15s", "-o", "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if result.returncode != 0:
        raise WorkerReadinessRefusal("KUBECTL_NODE_READ_FAILED")
    try:
        value = json.loads(result.stdout)
        items = value["items"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise WorkerReadinessRefusal("KUBECTL_NODE_RESPONSE_MALFORMED") from exc
    if not isinstance(items, list):
        raise WorkerReadinessRefusal("KUBECTL_NODE_RESPONSE_MALFORMED")
    return items


def wait_for_workers(
    snapshot: Callable[[str], list[dict[str, Any]]],
    wait_seconds: int,
    *,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if wait_seconds < 1 or wait_seconds > 1200:
        raise WorkerReadinessRefusal("WAIT_BOUND_INVALID")
    started = monotonic()
    deadline = started + wait_seconds
    reads = 0
    consecutive = 0
    while True:
        observed: dict[str, tuple[int, int]] = {}
        try:
            for workload, expected in EXPECTED.items():
                nodes = snapshot(workload)
                count = len(nodes)
                ready = sum(1 for node in nodes if _ready(node))
                reads += 1
                if count > expected:
                    raise WorkerReadinessRefusal(
                        f"{workload.upper()}_NODE_COUNT_EXCEEDS_BOUND"
                    )
                observed[workload] = (count, ready)
        except WorkerReadinessRefusal as exc:
            if exc.code != "KUBECTL_NODE_READ_FAILED":
                raise
            consecutive = 0
            observed = {}
        if len(observed) == len(EXPECTED) and all(
            observed[name] == (expected, expected)
            for name, expected in EXPECTED.items()
        ):
            consecutive += 1
            if consecutive == STABLE_OBSERVATIONS:
                return {
                    "cpu_nodes_ready": EXPECTED["cpu"],
                    "gpu_nodes_ready": EXPECTED["gpu"],
                    "maximum_cpu_nodes": EXPECTED["cpu"],
                    "maximum_gpu_nodes": EXPECTED["gpu"],
                    "observation_reads": reads,
                    "stable_observations": consecutive,
                    "poll_interval_seconds": POLL_SECONDS,
                    "resources_existed_before_ready_evaluation": True,
                }
        else:
            consecutive = 0
        if monotonic() >= deadline:
            raise WorkerReadinessRefusal("EXACT_WORKER_SET_NOT_READY_BEFORE_DEADLINE")
        sleep(POLL_SECONDS)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=1200)
    args = parser.parse_args()
    try:
        if not args.kubeconfig.is_file():
            raise WorkerReadinessRefusal("KUBECONFIG_ABSENT")
        result = wait_for_workers(
            lambda workload: kubectl_snapshot(args.kubeconfig, workload),
            args.wait_seconds,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except WorkerReadinessRefusal as exc:
        print(json.dumps({"reason_code": exc.code}, sort_keys=True))
        return 2
    except (OSError, subprocess.SubprocessError):
        print(json.dumps({"reason_code": "WORKER_GATE_EXECUTION_FAILED"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
