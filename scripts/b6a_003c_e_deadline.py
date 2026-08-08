#!/usr/bin/env python3
"""Arm the 003C-E deadline within the confirmed remaining GPU allowance."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pipeline.runtime_receipts_v2 import ReceiptStore
from scripts import b6a_003c_b_deadline as base
from scripts import b6a_003c_e_common as common


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "platform/runtime-receipt-policy-v2.yaml"
ACTION_NAME = "medzen-b6a-003c-e-deadline-scale-zero"
MAX_WINDOW_SECONDS = common.MAX_WINDOW_SECONDS
MIN_WINDOW_SECONDS = 300


class DeadlineControl(base.DeadlineControl):
    def arm(self, *, now: datetime, window_seconds: int) -> dict[str, Any]:
        now = base._utc(now)
        if not MIN_WINDOW_SECONDS <= window_seconds <= MAX_WINDOW_SECONDS:
            raise base.DeadlineRefusal("003C-E window must be between 300 and 5109 seconds")
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
            raise base.DeadlineRefusal("003C-E deadline could not be read back exactly")
        self._verify_action(actions[0], deadline)
        return {
            "status": "PASS",
            "asg": base.ASG_NAME,
            "action": ACTION_NAME,
            "armed_utc": now.isoformat().replace("+00:00", "Z"),
            "deadline_utc": deadline.isoformat().replace("+00:00", "Z"),
            "minimum": 0,
            "desired": 0,
            "maximum": 1,
            "window_seconds": window_seconds,
            "conservative_prior_billed_seconds": 2091,
            "conservative_cumulative_maximum_seconds": 7200,
        }

    def verify(self, *, now: datetime) -> dict[str, Any]:
        now = base._utc(now)
        actions = self._actions()
        if len(actions) != 1 or actions[0].get("ScheduledActionName") != ACTION_NAME:
            raise base.DeadlineRefusal("exact 003C-E deadline action is absent")
        deadline = base._utc(actions[0]["StartTime"])
        if deadline <= now or deadline > now + timedelta(seconds=MAX_WINDOW_SECONDS):
            raise base.DeadlineRefusal("003C-E deadline is expired or exceeds 5109 seconds")
        self._verify_action(actions[0], deadline)
        return {"status": "PASS", "deadline_utc": deadline.isoformat().replace("+00:00", "Z")}

    def disarm_003c_e_after_zero(self, *, kubeconfig: Path) -> dict[str, Any]:
        nodegroup = self.eks.describe_nodegroup(
            clusterName=base.CLUSTER, nodegroupName=base.NODEGROUP
        )["nodegroup"]
        scaling = nodegroup.get("scalingConfig", {})
        if (
            nodegroup.get("status") != "ACTIVE"
            or nodegroup.get("health", {}).get("issues")
            or scaling != {"minSize": 0, "maxSize": 1, "desiredSize": 0}
        ):
            raise base.DeadlineRefusal("EKS GPU node group is not proven zero and healthy")
        if not self._zero_asg(self._asg()):
            raise base.DeadlineRefusal("GPU Auto Scaling group is not proven zero")
        self._kubernetes_zero(kubeconfig)
        actions = self._actions()
        if len(actions) != 1 or actions[0].get("ScheduledActionName") != ACTION_NAME:
            raise base.DeadlineRefusal("003C-E deadline is absent or ambiguous")
        self.autoscaling.delete_scheduled_action(
            AutoScalingGroupName=base.ASG_NAME,
            ScheduledActionName=ACTION_NAME,
        )
        if self._actions():
            raise base.DeadlineRefusal("003C-E deadline remains after deletion")
        return {
            "status": "PASS",
            "action": ACTION_NAME,
            "gpu_nodegroup_zero": True,
            "gpu_asg_zero": True,
            "gpu_nodes_zero": True,
            "b6a_workload_zero": True,
            "deadline_disarmed_after_zero": True,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("arm", "verify", "disarm"))
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet-sha256", required=True)
    parser.add_argument("--receipts-dir", type=Path, required=True)
    parser.add_argument("--window-seconds", type=int, default=MAX_WINDOW_SECONDS)
    parser.add_argument("--kubeconfig", type=Path)
    parser.add_argument("--wait-seconds", type=int, default=900)
    args = parser.parse_args()
    try:
        common.authorization(args.authorization, args.packet_sha256, ROOT)
        store = ReceiptStore(args.receipts_dir, policy_path=POLICY)
        autoscaling, eks = base._clients()
        control = DeadlineControl(autoscaling, eks)
        now = datetime.now(timezone.utc)
        if args.mode == "arm":
            result = control.arm(now=now, window_seconds=args.window_seconds)
            receipt = store.persist("deadline", "PASS", result)
        elif args.mode == "verify":
            store.require_pass("deadline")
            receipt = control.verify(now=now)
        else:
            if args.kubeconfig is None:
                raise base.DeadlineRefusal("disarm requires --kubeconfig")
            if not 0 <= args.wait_seconds <= 1800:
                raise base.DeadlineRefusal("disarm wait must be between zero and 30 minutes")
            stop = time.monotonic() + args.wait_seconds
            while True:
                try:
                    result = control.disarm_003c_e_after_zero(kubeconfig=args.kubeconfig)
                    break
                except base.DeadlineRefusal:
                    if time.monotonic() >= stop:
                        raise
                    time.sleep(15)
            receipt = store.persist("cleanup", "PASS", result)
        print(json.dumps(receipt, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
