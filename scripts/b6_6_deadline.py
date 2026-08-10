#!/usr/bin/env python3
"""Arm independent CPU and GPU scale-to-zero deadlines for B6.6."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ACCOUNT = "558069890522"
REGION = "eu-central-1"
PROFILE = "medzen"
CLUSTER = "medzen-speech"
# Packets 2026-008 and 2026-010 consumed a conservatively bounded cumulative
# 3,157 seconds before their zero-state proofs. Attempt 4 may use only the
# remaining 11,243 seconds of the original 14,400-second allowance.
WINDOW_SECONDS = 11243
GROUPS = {
    "cpu": {
        "asg": "eks-cpu-32cfd795-fa28-d1d9-1b8c-2ed678be1772",
        "nodegroup": "cpu",
        "maximum": 4,
        "action": "medzen-b6-6-cpu-deadline-scale-zero",
    },
    "gpu": {
        "asg": "eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26",
        "nodegroup": "gpu",
        "maximum": 1,
        "action": "medzen-b6-6-gpu-deadline-scale-zero",
    },
}


class DeadlineRefusal(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DeadlineRefusal("deadline must be timezone-aware")
    return value.astimezone(timezone.utc).replace(microsecond=0)


class DeadlineControl:
    def __init__(self, autoscaling: Any, eks: Any):
        self.autoscaling = autoscaling
        self.eks = eks

    def _group(self, name: str) -> dict[str, Any]:
        groups = self.autoscaling.describe_auto_scaling_groups(
            AutoScalingGroupNames=[GROUPS[name]["asg"]]
        ).get("AutoScalingGroups", [])
        if len(groups) != 1:
            raise DeadlineRefusal(f"exact {name} Auto Scaling group is absent")
        return groups[0]

    def _actions(self, name: str) -> list[dict[str, Any]]:
        return self.autoscaling.describe_scheduled_actions(
            AutoScalingGroupName=GROUPS[name]["asg"]
        ).get("ScheduledUpdateGroupActions", [])

    @staticmethod
    def _zero(group: dict[str, Any], maximum: int) -> bool:
        return (
            group.get("MinSize") == 0
            and group.get("DesiredCapacity") == 0
            and group.get("MaxSize") == maximum
            and group.get("Instances") == []
        )

    def arm(self, now: datetime) -> dict[str, Any]:
        now = _utc(now)
        deadline = now + timedelta(seconds=WINDOW_SECONDS)
        for name, binding in GROUPS.items():
            if not self._zero(self._group(name), binding["maximum"]):
                raise DeadlineRefusal(f"{name} group is not initially zero")
            if self._actions(name):
                raise DeadlineRefusal(f"{name} has an existing scheduled action")
        created: list[str] = []
        try:
            for name, binding in GROUPS.items():
                self.autoscaling.put_scheduled_update_group_action(
                    AutoScalingGroupName=binding["asg"],
                    ScheduledActionName=binding["action"],
                    StartTime=deadline,
                    MinSize=0,
                    DesiredCapacity=0,
                    MaxSize=binding["maximum"],
                )
                created.append(name)
            self.verify(now)
        except Exception:
            for name in created:
                binding = GROUPS[name]
                self.autoscaling.delete_scheduled_action(
                    AutoScalingGroupName=binding["asg"],
                    ScheduledActionName=binding["action"],
                )
            raise
        return {
            "status": "PASS",
            "armed_utc": now.isoformat().replace("+00:00", "Z"),
            "deadline_utc": deadline.isoformat().replace("+00:00", "Z"),
            "window_seconds": WINDOW_SECONDS,
            "groups": {name: {"asg": value["asg"], "action": value["action"]} for name, value in GROUPS.items()},
        }

    def verify(self, now: datetime) -> dict[str, Any]:
        now = _utc(now)
        deadlines: set[datetime] = set()
        for name, binding in GROUPS.items():
            actions = self._actions(name)
            if len(actions) != 1 or actions[0].get("ScheduledActionName") != binding["action"]:
                raise DeadlineRefusal(f"exact {name} deadline is absent")
            action = actions[0]
            start = action.get("StartTime")
            if not isinstance(start, datetime):
                raise DeadlineRefusal(f"{name} deadline time is malformed")
            start = _utc(start)
            if start <= now or start > now + timedelta(seconds=WINDOW_SECONDS):
                raise DeadlineRefusal(f"{name} deadline is expired or too late")
            if action.get("MinSize") != 0 or action.get("DesiredCapacity") != 0 or action.get("MaxSize") != binding["maximum"]:
                raise DeadlineRefusal(f"{name} deadline scaling differs")
            deadlines.add(start)
        if len(deadlines) != 1:
            raise DeadlineRefusal("CPU and GPU deadlines differ")
        return {"status": "PASS", "deadline_utc": deadlines.pop().isoformat().replace("+00:00", "Z")}

    def disarm_after_zero(self) -> dict[str, Any]:
        for name, binding in GROUPS.items():
            nodegroup = self.eks.describe_nodegroup(
                clusterName=CLUSTER, nodegroupName=binding["nodegroup"]
            )["nodegroup"]
            scaling = nodegroup.get("scalingConfig", {})
            if (
                nodegroup.get("status") != "ACTIVE"
                or nodegroup.get("health", {}).get("issues")
                or scaling != {"minSize": 0, "maxSize": binding["maximum"], "desiredSize": 0}
                or not self._zero(self._group(name), binding["maximum"])
            ):
                raise DeadlineRefusal(f"{name} is not proven zero")
        for name, binding in GROUPS.items():
            actions = self._actions(name)
            if len(actions) != 1 or actions[0].get("ScheduledActionName") != binding["action"]:
                raise DeadlineRefusal(f"exact {name} deadline is absent before disarm")
            self.autoscaling.delete_scheduled_action(
                AutoScalingGroupName=binding["asg"],
                ScheduledActionName=binding["action"],
            )
        if any(self._actions(name) for name in GROUPS):
            raise DeadlineRefusal("a B6.6 deadline remains")
        return {"status": "PASS", "cpu_zero": True, "gpu_zero": True, "deadlines_removed_after_zero": True}


def _clients() -> tuple[Any, Any]:
    import boto3

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != ACCOUNT:
        raise DeadlineRefusal("AWS account differs")
    return session.client("autoscaling"), session.client("eks")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("arm", "verify", "disarm"))
    parser.add_argument("--wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    try:
        autoscaling, eks = _clients()
        control = DeadlineControl(autoscaling, eks)
        if args.mode == "arm":
            result = control.arm(datetime.now(timezone.utc))
        elif args.mode == "verify":
            result = control.verify(datetime.now(timezone.utc))
        else:
            stop = time.monotonic() + args.wait_seconds
            while True:
                try:
                    result = control.disarm_after_zero()
                    break
                except DeadlineRefusal:
                    if time.monotonic() >= stop:
                        raise
                    time.sleep(15)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
