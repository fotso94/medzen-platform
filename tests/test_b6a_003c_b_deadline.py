from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.b6a_003c_b_deadline import (
    ACTION_NAME,
    ASG_NAME,
    DeadlineControl,
    DeadlineRefusal,
    MAX_WINDOW_SECONDS,
)


NOW = datetime(2026, 8, 5, 4, 0, tzinfo=timezone.utc)


class AutoScaling:
    def __init__(self):
        self.actions = []
        self.group = {
            "AutoScalingGroupName": ASG_NAME,
            "MinSize": 0,
            "DesiredCapacity": 0,
            "MaxSize": 1,
            "Instances": [],
        }
        self.put = None
        self.deleted = None

    def describe_auto_scaling_groups(self, **kwargs):
        return {"AutoScalingGroups": [self.group]}

    def describe_scheduled_actions(self, **kwargs):
        return {"ScheduledUpdateGroupActions": list(self.actions)}

    def put_scheduled_update_group_action(self, **kwargs):
        self.put = kwargs
        self.actions = [{
            "ScheduledActionName": kwargs["ScheduledActionName"],
            "StartTime": kwargs["StartTime"],
            "MinSize": kwargs["MinSize"],
            "DesiredCapacity": kwargs["DesiredCapacity"],
            "MaxSize": kwargs["MaxSize"],
        }]

    def delete_scheduled_action(self, **kwargs):
        self.deleted = kwargs
        self.actions = []


class EKS:
    def __init__(self):
        self.nodegroup = {
            "status": "ACTIVE",
            "health": {"issues": []},
            "scalingConfig": {"minSize": 0, "desiredSize": 0, "maxSize": 1},
        }

    def describe_nodegroup(self, **kwargs):
        return {"nodegroup": self.nodegroup}


def kubernetes(*commands):
    outputs = iter(commands or ({"items": []}, {"items": []}, {"items": []}))

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, json.dumps(next(outputs)), "")

    return run


def test_arm_creates_and_reads_back_exact_two_hour_scale_zero_action():
    autoscaling = AutoScaling()
    control = DeadlineControl(autoscaling, EKS(), kubernetes())
    result = control.arm(now=NOW, window_seconds=MAX_WINDOW_SECONDS)
    assert result["status"] == "ARMED_AND_VERIFIED"
    assert result["deadline_utc"] == "2026-08-05T06:00:00Z"
    assert autoscaling.put == {
        "AutoScalingGroupName": ASG_NAME,
        "ScheduledActionName": ACTION_NAME,
        "StartTime": NOW + timedelta(hours=2),
        "MinSize": 0,
        "DesiredCapacity": 0,
        "MaxSize": 1,
    }


def test_arm_refuses_long_window_nonzero_gpu_or_existing_action():
    autoscaling = AutoScaling()
    control = DeadlineControl(autoscaling, EKS(), kubernetes())
    with pytest.raises(DeadlineRefusal, match="2 hours"):
        control.arm(now=NOW, window_seconds=MAX_WINDOW_SECONDS + 1)
    autoscaling.group["DesiredCapacity"] = 1
    with pytest.raises(DeadlineRefusal, match="not initially zero"):
        control.arm(now=NOW, window_seconds=MAX_WINDOW_SECONDS)
    autoscaling.group["DesiredCapacity"] = 0
    autoscaling.actions = [{"ScheduledActionName": "unexpected"}]
    with pytest.raises(DeadlineRefusal, match="already exists"):
        control.arm(now=NOW, window_seconds=MAX_WINDOW_SECONDS)


def test_disarm_requires_eks_asg_nodes_pods_and_replicas_all_zero(tmp_path):
    autoscaling = AutoScaling()
    control = DeadlineControl(autoscaling, EKS(), kubernetes())
    control.arm(now=NOW, window_seconds=MAX_WINDOW_SECONDS)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("test")
    result = control.disarm_after_zero(kubeconfig=kubeconfig)
    assert result["status"] == "DISARMED_AFTER_ZERO_PROOF"
    assert autoscaling.deleted == {
        "AutoScalingGroupName": ASG_NAME,
        "ScheduledActionName": ACTION_NAME,
    }


def test_disarm_failure_leaves_deadline_armed(tmp_path):
    autoscaling = AutoScaling()
    control = DeadlineControl(
        autoscaling,
        EKS(),
        kubernetes(
            {"items": [{"metadata": {"name": "gpu-node"}}]},
            {"items": []},
            {"items": []},
        ),
    )
    control.arm(now=NOW, window_seconds=MAX_WINDOW_SECONDS)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("test")
    with pytest.raises(DeadlineRefusal, match="GPU nodes"):
        control.disarm_after_zero(kubeconfig=kubeconfig)
    assert autoscaling.actions[0]["ScheduledActionName"] == ACTION_NAME
    assert autoscaling.deleted is None


def test_cleanup_script_has_immediate_and_independent_zero_controls():
    text = (ROOT / "scripts/b6a_003c_b_cleanup.sh").read_text()
    assert "set -euo pipefail" in text
    assert "scale deployment/asr-runtime-b6a" in text
    assert "desiredSize=0" in text
    assert "scripts/b6a_003c_b_deadline.py disarm" in text
    assert text.index("desiredSize=0") < text.index("deadline.py disarm")
    assert "leaves the" in text and "deadline action armed" in text


def test_deadline_receipt_overwrite_refusal_precedes_aws_clients():
    text = (ROOT / "scripts/b6a_003c_b_deadline.py").read_text()
    refusal = text.index("if args.receipt and args.receipt.exists()")
    clients = text.index("autoscaling, eks = _clients()")
    assert refusal < clients
