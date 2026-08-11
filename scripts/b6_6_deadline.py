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
# Each approved attempt receives one independent 4,500-second deadline. Packet
# Prospective packet 2026-030A carries only the remaining independent attempt;
# unused time from attempt 1 is not added to its 4,500-second deadline.
WINDOW_SECONDS = 4500
POST_MUTATION_STABLE_OBSERVATIONS = 3
POST_MUTATION_POLL_SECONDS = 2
POST_MUTATION_VERIFY_SECONDS = 30
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


class DeadlineBoundaryRefusal(DeadlineRefusal):
    """Non-transient deadline state that must refuse without polling."""

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
            stable = self.wait_verify()
        except Exception:
            for name in created:
                binding = GROUPS[name]
                self.autoscaling.delete_scheduled_action(
                    AutoScalingGroupName=binding["asg"],
                    ScheduledActionName=binding["action"],
                )
            self._wait_actions_absent()
            raise
        return {
            "status": "PASS",
            "armed_utc": now.isoformat().replace("+00:00", "Z"),
            "deadline_utc": deadline.isoformat().replace("+00:00", "Z"),
            "window_seconds": WINDOW_SECONDS,
            "groups": {name: {"asg": value["asg"], "action": value["action"]} for name, value in GROUPS.items()},
            "stable_deadline_observations": stable[
                "stable_deadline_observations"
            ],
            "verification_polls": stable["verification_polls"],
        }

    def wait_verify(
        self,
        wait_seconds: int = POST_MUTATION_VERIFY_SECONDS,
        *,
        stable_observations: int = POST_MUTATION_STABLE_OBSERVATIONS,
        poll_seconds: int = POST_MUTATION_POLL_SECONDS,
        monotonic: Any = time.monotonic,
        sleep: Any = time.sleep,
        now: Any = lambda: datetime.now(timezone.utc),
    ) -> dict[str, Any]:
        if (
            wait_seconds < 1
            or wait_seconds > POST_MUTATION_VERIFY_SECONDS
            or stable_observations != POST_MUTATION_STABLE_OBSERVATIONS
            or poll_seconds != POST_MUTATION_POLL_SECONDS
        ):
            raise DeadlineRefusal("deadline stable-verification boundary differs")
        deadline = monotonic() + wait_seconds
        consecutive = 0
        polls = 0
        expected_deadline: str | None = None
        last_error: DeadlineRefusal | None = None
        while True:
            polls += 1
            try:
                result = self.verify(now())
                observed = str(result["deadline_utc"])
                if observed == expected_deadline:
                    consecutive += 1
                else:
                    expected_deadline = observed
                    consecutive = 1
                last_error = None
                if consecutive == stable_observations:
                    return {
                        **result,
                        "stable_deadline_observations": consecutive,
                        "verification_polls": polls,
                    }
            except DeadlineRefusal as exc:
                consecutive = 0
                expected_deadline = None
                last_error = exc
            if monotonic() >= deadline:
                if last_error is not None:
                    raise last_error
                raise DeadlineRefusal("deadline did not remain stable before timeout")
            sleep(poll_seconds)

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

    def disarm_after_zero(
        self, *, sleep: Any = time.sleep, monotonic: Any = time.monotonic
    ) -> dict[str, Any]:
        zero_polls = self._wait_zero_stable(sleep=sleep, monotonic=monotonic)
        for name, binding in GROUPS.items():
            actions = self._actions(name)
            if len(actions) != 1 or actions[0].get("ScheduledActionName") != binding["action"]:
                raise DeadlineRefusal(f"exact {name} deadline is absent before disarm")
            self.autoscaling.delete_scheduled_action(
                AutoScalingGroupName=binding["asg"],
                ScheduledActionName=binding["action"],
            )
        absence_polls = self._wait_actions_absent(sleep=sleep, monotonic=monotonic)
        return {"status": "PASS", "cpu_zero": True, "gpu_zero": True, "deadlines_removed_after_zero": True, "stable_zero_observations": POST_MUTATION_STABLE_OBSERVATIONS, "zero_verification_polls": zero_polls, "stable_deadline_absence_observations": POST_MUTATION_STABLE_OBSERVATIONS, "deadline_absence_polls": absence_polls}

    def _assert_zero(self) -> None:
        for name, binding in GROUPS.items():
            nodegroup = self.eks.describe_nodegroup(
                clusterName=CLUSTER, nodegroupName=binding["nodegroup"]
            )["nodegroup"]
            scaling = nodegroup.get("scalingConfig", {})
            if (
                nodegroup.get("status") != "ACTIVE"
                or nodegroup.get("health", {}).get("issues")
                or scaling.get("minSize") != 0
                or scaling.get("maxSize") != binding["maximum"]
                or scaling.get("desiredSize") != 0
                or not self._zero(self._group(name), binding["maximum"])
            ):
                raise DeadlineRefusal(f"{name} is not proven zero")

    def _wait_zero_stable(
        self,
        *,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
        wait_seconds: int = POST_MUTATION_VERIFY_SECONDS,
    ) -> int:
        deadline = monotonic() + wait_seconds
        consecutive = 0
        polls = 0
        while True:
            polls += 1
            try:
                self._assert_zero()
                consecutive += 1
                if consecutive == POST_MUTATION_STABLE_OBSERVATIONS:
                    return polls
            except DeadlineRefusal:
                consecutive = 0
            if monotonic() >= deadline:
                raise DeadlineRefusal("worker groups did not remain stably zero")
            sleep(POST_MUTATION_POLL_SECONDS)

    def _wait_actions_absent(
        self,
        *,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
        wait_seconds: int = POST_MUTATION_VERIFY_SECONDS,
    ) -> int:
        deadline = monotonic() + wait_seconds
        consecutive = 0
        polls = 0
        while True:
            polls += 1
            if any(self._actions(name) for name in GROUPS):
                consecutive = 0
            else:
                consecutive += 1
                if consecutive == POST_MUTATION_STABLE_OBSERVATIONS:
                    return polls
            if monotonic() >= deadline:
                raise DeadlineRefusal("a B6.6 deadline remains")
            sleep(POST_MUTATION_POLL_SECONDS)

    def cleanup_after_zero(
        self,
        deadline_receipt_status: str,
        *,
        sleep: Any = time.sleep,
        monotonic: Any = time.monotonic,
    ) -> dict[str, Any]:
        """Reconcile only exact actions that actually exist after zero capacity."""
        if deadline_receipt_status not in {"PASS", "REFUSED", "ABSENT"}:
            raise DeadlineBoundaryRefusal("deadline receipt status is unknown")
        zero_polls = self._wait_zero_stable(sleep=sleep, monotonic=monotonic)
        present: dict[str, bool] = {}
        for name, binding in GROUPS.items():
            actions = self._actions(name)
            if len(actions) > 1 or any(
                action.get("ScheduledActionName") != binding["action"]
                for action in actions
            ):
                raise DeadlineBoundaryRefusal(
                    f"{name} scheduled-action boundary differs"
                )
            present[name] = len(actions) == 1
        for name, binding in GROUPS.items():
            if present[name]:
                self.autoscaling.delete_scheduled_action(
                    AutoScalingGroupName=binding["asg"],
                    ScheduledActionName=binding["action"],
                )
        absence_polls = self._wait_actions_absent(sleep=sleep, monotonic=monotonic)
        return {
            "status": "PASS",
            "cpu_zero": True,
            "gpu_zero": True,
            "deadline_receipt_status": deadline_receipt_status,
            "deadline_actions_before": sum(present.values()),
            "deadline_actions_removed": sum(present.values()),
            "deadline_actions_after": 0,
            "pre_deadline_refusal_supported": deadline_receipt_status
            in {"REFUSED", "ABSENT"},
            "stable_zero_observations": POST_MUTATION_STABLE_OBSERVATIONS,
            "zero_verification_polls": zero_polls,
            "stable_deadline_absence_observations": POST_MUTATION_STABLE_OBSERVATIONS,
            "deadline_absence_polls": absence_polls,
        }


def _clients() -> tuple[Any, Any]:
    import boto3

    session = boto3.Session(profile_name=PROFILE, region_name=REGION)
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != ACCOUNT:
        raise DeadlineRefusal("AWS account differs")
    return session.client("autoscaling"), session.client("eks")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("arm", "verify", "disarm", "cleanup"))
    parser.add_argument("--wait-seconds", type=int, default=1800)
    parser.add_argument(
        "--deadline-receipt-status", choices=("PASS", "REFUSED", "ABSENT")
    )
    args = parser.parse_args()
    try:
        autoscaling, eks = _clients()
        control = DeadlineControl(autoscaling, eks)
        if args.mode == "arm":
            result = control.arm(datetime.now(timezone.utc))
        elif args.mode == "verify":
            result = control.wait_verify()
        elif args.mode == "disarm":
            stop = time.monotonic() + args.wait_seconds
            while True:
                try:
                    result = control.disarm_after_zero()
                    break
                except DeadlineRefusal:
                    if time.monotonic() >= stop:
                        raise
                    time.sleep(15)
        else:
            if args.deadline_receipt_status is None:
                raise DeadlineRefusal("deadline receipt status is required for cleanup")
            stop = time.monotonic() + args.wait_seconds
            while True:
                try:
                    result = control.cleanup_after_zero(args.deadline_receipt_status)
                    break
                except DeadlineBoundaryRefusal:
                    raise
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
