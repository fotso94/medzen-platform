#!/usr/bin/env python3
"""Arm and verify the independent AWS-side B6A GPU deadline."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
CLUSTER = "medzen-speech"
NODEGROUP = "gpu"
ASG_NAME = "eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26"
ACTION_NAME = "medzen-b6a-003c-b-deadline-scale-zero"
MAX_WINDOW_SECONDS = 7200
MIN_WINDOW_SECONDS = 300


class DeadlineRefusal(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DeadlineRefusal("deadline must be timezone-aware")
    return value.astimezone(timezone.utc).replace(microsecond=0)


def _validate_authorization(path: Path, packet_sha256: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise DeadlineRefusal("exact packet SHA-256 is required")
    try:
        record = json.loads(path.read_bytes())
    except Exception as exc:
        raise DeadlineRefusal("003C-B authorization record is unreadable") from exc
    if record.get("id") != "B6A-AWS-AUTH-2026-003C-B":
        raise DeadlineRefusal("authorization id differs")
    if record.get("status") != "owner-approved":
        raise DeadlineRefusal("003C-B is not owner-approved")
    if record.get("packet", {}).get("sha256") != packet_sha256:
        raise DeadlineRefusal("authorization does not bind the packet SHA-256")


class DeadlineControl:
    def __init__(self, autoscaling: Any, eks: Any,
                 runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run):
        self.autoscaling = autoscaling
        self.eks = eks
        self.runner = runner

    def _asg(self) -> dict[str, Any]:
        groups = self.autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=[ASG_NAME]
        ).get("AutoScalingGroups", [])
        if len(groups) != 1 or groups[0].get("AutoScalingGroupName") != ASG_NAME:
            raise DeadlineRefusal("exact GPU Auto Scaling group was not found")
        return groups[0]

    def _actions(self) -> list[dict[str, Any]]:
        return self.autoscaling.describe_scheduled_actions(
            AutoScalingGroupName=ASG_NAME
        ).get("ScheduledUpdateGroupActions", [])

    @staticmethod
    def _zero_asg(group: dict[str, Any]) -> bool:
        return (
            group.get("MinSize") == 0
            and group.get("DesiredCapacity") == 0
            and group.get("MaxSize") == 1
            and group.get("Instances") == []
        )

    @staticmethod
    def _verify_action(action: dict[str, Any], deadline: datetime) -> None:
        start = action.get("StartTime")
        if not isinstance(start, datetime) or _utc(start) != _utc(deadline):
            raise DeadlineRefusal("deadline action start time differs")
        if action.get("MinSize") != 0 or action.get("DesiredCapacity") != 0:
            raise DeadlineRefusal("deadline action does not scale to zero")
        if action.get("MaxSize") != 1:
            raise DeadlineRefusal("deadline action changes the maximum unexpectedly")

    def arm(self, *, now: datetime, window_seconds: int) -> dict[str, Any]:
        now = _utc(now)
        if not MIN_WINDOW_SECONDS <= window_seconds <= MAX_WINDOW_SECONDS:
            raise DeadlineRefusal("GPU window must be between 5 minutes and 2 hours")
        if not self._zero_asg(self._asg()):
            raise DeadlineRefusal("GPU Auto Scaling group is not initially zero")
        if self._actions():
            raise DeadlineRefusal("an Auto Scaling scheduled action already exists")
        deadline = now + timedelta(seconds=window_seconds)
        self.autoscaling.put_scheduled_update_group_action(
            AutoScalingGroupName=ASG_NAME,
            ScheduledActionName=ACTION_NAME,
            StartTime=deadline,
            MinSize=0,
            DesiredCapacity=0,
            MaxSize=1,
        )
        actions = self._actions()
        if len(actions) != 1 or actions[0].get("ScheduledActionName") != ACTION_NAME:
            raise DeadlineRefusal("deadline action could not be read back exactly")
        self._verify_action(actions[0], deadline)
        return {
            "status": "ARMED_AND_VERIFIED",
            "asg": ASG_NAME,
            "action": ACTION_NAME,
            "armed_utc": now.isoformat().replace("+00:00", "Z"),
            "deadline_utc": deadline.isoformat().replace("+00:00", "Z"),
            "minimum": 0,
            "desired": 0,
            "maximum": 1,
        }

    def verify(self, *, now: datetime) -> dict[str, Any]:
        now = _utc(now)
        actions = self._actions()
        if len(actions) != 1 or actions[0].get("ScheduledActionName") != ACTION_NAME:
            raise DeadlineRefusal("exact armed deadline action is absent")
        deadline = _utc(actions[0]["StartTime"])
        if deadline <= now or deadline > now + timedelta(seconds=MAX_WINDOW_SECONDS):
            raise DeadlineRefusal("armed deadline is expired or exceeds two hours")
        self._verify_action(actions[0], deadline)
        return {
            "status": "ARMED_AND_VERIFIED",
            "deadline_utc": deadline.isoformat().replace("+00:00", "Z"),
        }

    def _kubernetes_zero(self, kubeconfig: Path) -> None:
        commands = [
            ["kubectl", "--kubeconfig", str(kubeconfig), "get", "nodes", "-l",
             "workload=gpu", "-o", "json"],
            ["kubectl", "--kubeconfig", str(kubeconfig), "get", "pods", "-n",
             "medzen", "-l", "medzen.io/classification=platform-proof-only",
             "-o", "json"],
            ["kubectl", "--kubeconfig", str(kubeconfig), "get", "deployments", "-n",
             "medzen", "-l", "app.kubernetes.io/name=asr-runtime-b6a", "-o", "json"],
        ]
        results = []
        for command in commands:
            completed = self.runner(command, check=False, text=True, capture_output=True)
            if completed.returncode != 0:
                raise DeadlineRefusal("Kubernetes zero-state query failed")
            try:
                results.append(json.loads(completed.stdout))
            except json.JSONDecodeError as exc:
                raise DeadlineRefusal("Kubernetes zero-state response is malformed") from exc
        nodes, pods, deployments = results
        if nodes.get("items") or pods.get("items"):
            raise DeadlineRefusal("GPU nodes or B6A Pods remain")
        for deployment in deployments.get("items", []):
            if deployment.get("spec", {}).get("replicas", 0) != 0:
                raise DeadlineRefusal("B6A Deployment replicas remain")
            if deployment.get("status", {}).get("replicas", 0) != 0:
                raise DeadlineRefusal("B6A running replicas remain")

    def disarm_after_zero(self, *, kubeconfig: Path) -> dict[str, Any]:
        nodegroup = self.eks.describe_nodegroup(
            clusterName=CLUSTER, nodegroupName=NODEGROUP
        )["nodegroup"]
        scaling = nodegroup.get("scalingConfig", {})
        if (
            nodegroup.get("status") != "ACTIVE"
            or nodegroup.get("health", {}).get("issues")
            or scaling.get("minSize") != 0
            or scaling.get("desiredSize") != 0
            or scaling.get("maxSize") != 1
        ):
            raise DeadlineRefusal("EKS GPU node group is not proven zero and healthy")
        if not self._zero_asg(self._asg()):
            raise DeadlineRefusal("GPU Auto Scaling group is not proven zero")
        self._kubernetes_zero(kubeconfig)
        actions = self._actions()
        if len(actions) != 1 or actions[0].get("ScheduledActionName") != ACTION_NAME:
            raise DeadlineRefusal("deadline action is absent or ambiguous")
        self.autoscaling.delete_scheduled_action(
            AutoScalingGroupName=ASG_NAME,
            ScheduledActionName=ACTION_NAME,
        )
        if self._actions():
            raise DeadlineRefusal("deadline action remains after deletion")
        return {"status": "DISARMED_AFTER_ZERO_PROOF", "action": ACTION_NAME}


def _clients():
    import boto3

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    return session.client("autoscaling"), session.client("eks")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("arm", "verify", "disarm"))
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--window-seconds", type=int, default=MAX_WINDOW_SECONDS)
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        if args.receipt and args.receipt.exists():
            raise DeadlineRefusal("refusing to overwrite deadline receipt")
        _validate_authorization(args.authorization, args.packet_sha256)
        autoscaling, eks = _clients()
        control = DeadlineControl(autoscaling, eks)
        now = datetime.now(timezone.utc)
        if args.mode == "arm":
            result = control.arm(now=now, window_seconds=args.window_seconds)
        elif args.mode == "verify":
            result = control.verify(now=now)
        else:
            if args.kubeconfig is None:
                raise DeadlineRefusal("disarm requires --kubeconfig")
            if not 0 <= args.wait_seconds <= 1800:
                raise DeadlineRefusal("disarm wait must be between zero and 30 minutes")
            stop = time.monotonic() + args.wait_seconds
            while True:
                try:
                    result = control.disarm_after_zero(kubeconfig=args.kubeconfig)
                    break
                except DeadlineRefusal:
                    if time.monotonic() >= stop:
                        raise
                    time.sleep(15)
        encoded = json.dumps(result, sort_keys=True) + "\n"
        if args.receipt:
            args.receipt.write_text(encoded)
        print(encoded, end="")
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
