from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import b6a_003c_b_deadline as base
from scripts.b6a_003c_c_deadline import (
    ACTION_NAME,
    DeadlineControl,
    MAX_WINDOW_SECONDS,
)


NOW = datetime(2026, 8, 5, 5, 0, tzinfo=timezone.utc)


class AutoScaling:
    def __init__(self):
        self.actions = []
        self.group = {
            "AutoScalingGroupName": base.ASG_NAME,
            "MinSize": 0,
            "DesiredCapacity": 0,
            "MaxSize": 1,
            "Instances": [],
        }
        self.deleted = None

    def describe_auto_scaling_groups(self, **kwargs):
        return {"AutoScalingGroups": [self.group]}

    def describe_scheduled_actions(self, **kwargs):
        return {"ScheduledUpdateGroupActions": list(self.actions)}

    def put_scheduled_update_group_action(self, **kwargs):
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
    def describe_nodegroup(self, **kwargs):
        return {"nodegroup": {
            "status": "ACTIVE",
            "health": {"issues": []},
            "scalingConfig": {"minSize": 0, "desiredSize": 0, "maxSize": 1},
        }}


def kubernetes(command, **kwargs):
    return subprocess.CompletedProcess(command, 0, json.dumps({"items": []}), "")


def test_retry_deadline_caps_cumulative_allowance_at_two_hours():
    autoscaling = AutoScaling()
    control = DeadlineControl(autoscaling, EKS(), kubernetes)
    result = control.arm(now=NOW, window_seconds=MAX_WINDOW_SECONDS)
    assert MAX_WINDOW_SECONDS == 7140
    assert result["deadline_utc"] == "2026-08-05T06:59:00Z"
    assert result["conservative_prior_billed_seconds"] == 60
    assert result["conservative_cumulative_maximum_seconds"] == 7200
    assert autoscaling.actions[0]["ScheduledActionName"] == ACTION_NAME
    with pytest.raises(base.DeadlineRefusal, match="119 minutes"):
        DeadlineControl(AutoScaling(), EKS(), kubernetes).arm(
            now=NOW, window_seconds=7141
        )


def test_retry_deadline_disarms_only_after_zero_proof(tmp_path):
    autoscaling = AutoScaling()
    control = DeadlineControl(autoscaling, EKS(), kubernetes)
    control.arm(now=NOW, window_seconds=MAX_WINDOW_SECONDS)
    kubeconfig = tmp_path / "kubeconfig"
    kubeconfig.write_text("test")
    result = control.disarm_after_zero(kubeconfig=kubeconfig)
    assert result == {"status": "DISARMED_AFTER_ZERO_PROOF", "action": ACTION_NAME}
    assert autoscaling.deleted["ScheduledActionName"] == ACTION_NAME


def test_003c_c_does_not_mutate_003c_b_deadline_constants():
    assert base.ACTION_NAME == "medzen-b6a-003c-b-deadline-scale-zero"
    assert base.MAX_WINDOW_SECONDS == 7200
    assert ACTION_NAME == "medzen-b6a-003c-c-deadline-scale-zero"


def test_cleanup_preserves_deadline_until_after_scale_zero():
    text = (ROOT / "scripts/b6a_003c_c_cleanup.sh").read_text()
    assert text.index("desiredSize=0") < text.index("003c_c_deadline.py disarm")
    assert "AWS_PROFILE=medzen" in text
