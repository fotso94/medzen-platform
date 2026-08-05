#!/usr/bin/env python3
"""Arm the 003C-C retry deadline within the cumulative two-hour allowance."""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from scripts import b6a_003c_b_deadline as base
except ModuleNotFoundError:  # Direct execution from scripts/.
    import b6a_003c_b_deadline as base


AUTH_ID = "B6A-AWS-AUTH-2026-003C-C"
ACTION_NAME = "medzen-b6a-003c-c-deadline-scale-zero"
MAX_WINDOW_SECONDS = 7140
MIN_WINDOW_SECONDS = 300


def _validate_authorization(path: Path, packet_sha256: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", packet_sha256) is None:
        raise base.DeadlineRefusal("exact 003C-C packet SHA-256 is required")
    try:
        record = json.loads(path.read_bytes())
    except Exception as exc:
        raise base.DeadlineRefusal("003C-C authorization is unreadable") from exc
    if record.get("id") != AUTH_ID or record.get("status") != "owner-approved":
        raise base.DeadlineRefusal("003C-C is not owner-approved")
    if record.get("packet", {}).get("sha256") != packet_sha256:
        raise base.DeadlineRefusal("003C-C authorization packet binding differs")
    if record.get("aws_scope", {}).get("maximum_window_seconds") != MAX_WINDOW_SECONDS:
        raise base.DeadlineRefusal("003C-C cumulative GPU window binding differs")


class DeadlineControl(base.DeadlineControl):
    def arm(self, *, now: datetime, window_seconds: int) -> dict[str, Any]:
        now = base._utc(now)
        if not MIN_WINDOW_SECONDS <= window_seconds <= MAX_WINDOW_SECONDS:
            raise base.DeadlineRefusal("retry window must be between 5 and 119 minutes")
        if not self._zero_asg(self._asg()):
            raise base.DeadlineRefusal("GPU Auto Scaling group is not initially zero")
        if self._actions():
            raise base.DeadlineRefusal("an Auto Scaling scheduled action already exists")
        deadline = now + timedelta(seconds=window_seconds)
        self.autoscaling.put_scheduled_update_group_action(
            AutoScalingGroupName=base.ASG_NAME,
            ScheduledActionName=ACTION_NAME,
            StartTime=deadline,
            MinSize=0,
            DesiredCapacity=0,
            MaxSize=1,
        )
        actions = self._actions()
        if len(actions) != 1 or actions[0].get("ScheduledActionName") != ACTION_NAME:
            raise base.DeadlineRefusal("retry deadline could not be read back exactly")
        self._verify_action(actions[0], deadline)
        return {
            "status": "ARMED_AND_VERIFIED",
            "asg": base.ASG_NAME,
            "action": ACTION_NAME,
            "armed_utc": now.isoformat().replace("+00:00", "Z"),
            "deadline_utc": deadline.isoformat().replace("+00:00", "Z"),
            "minimum": 0,
            "desired": 0,
            "maximum": 1,
            "window_seconds": window_seconds,
            "conservative_prior_billed_seconds": 60,
            "conservative_cumulative_maximum_seconds": 7200,
        }

    def verify(self, *, now: datetime) -> dict[str, Any]:
        now = base._utc(now)
        actions = self._actions()
        if len(actions) != 1 or actions[0].get("ScheduledActionName") != ACTION_NAME:
            raise base.DeadlineRefusal("exact retry deadline action is absent")
        deadline = base._utc(actions[0]["StartTime"])
        if deadline <= now or deadline > now + timedelta(seconds=MAX_WINDOW_SECONDS):
            raise base.DeadlineRefusal("retry deadline is expired or exceeds 7140 seconds")
        self._verify_action(actions[0], deadline)
        return {
            "status": "ARMED_AND_VERIFIED",
            "deadline_utc": deadline.isoformat().replace("+00:00", "Z"),
        }

    def disarm_after_zero(self, *, kubeconfig: Path) -> dict[str, Any]:
        nodegroup = self.eks.describe_nodegroup(
            clusterName=base.CLUSTER, nodegroupName=base.NODEGROUP
        )["nodegroup"]
        scaling = nodegroup.get("scalingConfig", {})
        if (
            nodegroup.get("status") != "ACTIVE"
            or nodegroup.get("health", {}).get("issues")
            or scaling.get("minSize") != 0
            or scaling.get("desiredSize") != 0
            or scaling.get("maxSize") != 1
        ):
            raise base.DeadlineRefusal("EKS GPU node group is not proven zero and healthy")
        if not self._zero_asg(self._asg()):
            raise base.DeadlineRefusal("GPU Auto Scaling group is not proven zero")
        self._kubernetes_zero(kubeconfig)
        actions = self._actions()
        if len(actions) != 1 or actions[0].get("ScheduledActionName") != ACTION_NAME:
            raise base.DeadlineRefusal("retry deadline is absent or ambiguous")
        self.autoscaling.delete_scheduled_action(
            AutoScalingGroupName=base.ASG_NAME,
            ScheduledActionName=ACTION_NAME,
        )
        if self._actions():
            raise base.DeadlineRefusal("retry deadline remains after deletion")
        return {"status": "DISARMED_AFTER_ZERO_PROOF", "action": ACTION_NAME}


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
            raise base.DeadlineRefusal("refusing to overwrite 003C-C deadline receipt")
        _validate_authorization(args.authorization, args.packet_sha256)
        autoscaling, eks = base._clients()
        control = DeadlineControl(autoscaling, eks)
        now = datetime.now(timezone.utc)
        if args.mode == "arm":
            result = control.arm(now=now, window_seconds=args.window_seconds)
        elif args.mode == "verify":
            result = control.verify(now=now)
        else:
            if args.kubeconfig is None:
                raise base.DeadlineRefusal("disarm requires --kubeconfig")
            if not 0 <= args.wait_seconds <= 1800:
                raise base.DeadlineRefusal("disarm wait must be between zero and 30 minutes")
            stop = time.monotonic() + args.wait_seconds
            while True:
                try:
                    result = control.disarm_after_zero(kubeconfig=args.kubeconfig)
                    break
                except base.DeadlineRefusal:
                    if time.monotonic() >= stop:
                        raise
                    time.sleep(15)
        encoded = json.dumps(result, sort_keys=True) + "\n"
        if args.receipt:
            args.receipt.write_text(encoded)
        print(encoded, end="")
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "REFUSED", "error": type(exc).__name__, "reason": str(exc)
        }))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
