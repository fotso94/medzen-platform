from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from scripts.b6_6_deadline import DeadlineControl, DeadlineRefusal, GROUPS


ROOT = Path(__file__).resolve().parents[1]


class FakeAutoscaling:
    def __init__(self, actions: dict[str, list[dict]] | None = None) -> None:
        self.actions = actions or {name: [] for name in GROUPS}
        self.deleted: list[tuple[str, str]] = []

    def describe_auto_scaling_groups(self, AutoScalingGroupNames: list[str]) -> dict:
        name = next(
            key for key, binding in GROUPS.items() if binding["asg"] == AutoScalingGroupNames[0]
        )
        binding = GROUPS[name]
        return {
            "AutoScalingGroups": [
                {
                    "MinSize": 0,
                    "DesiredCapacity": 0,
                    "MaxSize": binding["maximum"],
                    "Instances": [],
                }
            ]
        }

    def describe_scheduled_actions(self, AutoScalingGroupName: str) -> dict:
        name = next(
            key for key, binding in GROUPS.items() if binding["asg"] == AutoScalingGroupName
        )
        return {"ScheduledUpdateGroupActions": deepcopy(self.actions[name])}

    def delete_scheduled_action(
        self, AutoScalingGroupName: str, ScheduledActionName: str
    ) -> None:
        name = next(
            key for key, binding in GROUPS.items() if binding["asg"] == AutoScalingGroupName
        )
        self.deleted.append((name, ScheduledActionName))
        self.actions[name] = []


class FakeEks:
    def describe_nodegroup(self, clusterName: str, nodegroupName: str) -> dict:
        assert clusterName == "medzen-speech"
        binding = GROUPS[nodegroupName]
        return {
            "nodegroup": {
                "status": "ACTIVE",
                "health": {"issues": []},
                "scalingConfig": {
                    "minSize": 0,
                    "desiredSize": 0,
                    "maxSize": binding["maximum"],
                },
            }
        }


def _action(name: str) -> dict:
    return {"ScheduledActionName": GROUPS[name]["action"]}


@pytest.mark.parametrize("receipt_status", ["ABSENT", "REFUSED"])
def test_pre_deadline_refusal_with_no_actions_is_immediately_clean(
    receipt_status: str,
) -> None:
    autoscaling = FakeAutoscaling()
    result = DeadlineControl(autoscaling, FakeEks()).cleanup_after_zero(receipt_status)
    assert result == {
        "status": "PASS",
        "cpu_zero": True,
        "gpu_zero": True,
        "deadline_receipt_status": receipt_status,
        "deadline_actions_before": 0,
        "deadline_actions_removed": 0,
        "deadline_actions_after": 0,
        "pre_deadline_refusal_supported": True,
    }
    assert autoscaling.deleted == []


def test_partial_exact_deadline_from_refused_arm_is_removed_after_zero() -> None:
    autoscaling = FakeAutoscaling({"cpu": [_action("cpu")], "gpu": []})
    result = DeadlineControl(autoscaling, FakeEks()).cleanup_after_zero("REFUSED")
    assert result["deadline_actions_before"] == 1
    assert result["deadline_actions_removed"] == 1
    assert autoscaling.deleted == [("cpu", GROUPS["cpu"]["action"])]


def test_passing_deadline_receipt_removes_both_actions_after_zero() -> None:
    autoscaling = FakeAutoscaling(
        {"cpu": [_action("cpu")], "gpu": [_action("gpu")]}
    )
    result = DeadlineControl(autoscaling, FakeEks()).cleanup_after_zero("PASS")
    assert result["deadline_actions_before"] == 2
    assert result["deadline_actions_removed"] == 2
    assert result["pre_deadline_refusal_supported"] is False


def test_unknown_or_unexpected_deadline_state_fails_closed() -> None:
    control = DeadlineControl(FakeAutoscaling(), FakeEks())
    with pytest.raises(DeadlineRefusal, match="status is unknown"):
        control.cleanup_after_zero("UNKNOWN")
    autoscaling = FakeAutoscaling(
        {"cpu": [{"ScheduledActionName": "unexpected"}], "gpu": []}
    )
    with pytest.raises(DeadlineRefusal, match="boundary differs"):
        DeadlineControl(autoscaling, FakeEks()).cleanup_after_zero("ABSENT")


def test_cleanup_shell_keys_deadline_reconciliation_to_receipt_status() -> None:
    source = (ROOT / "scripts/b6_6_cleanup.sh").read_text()
    assert 'deadline_receipt_status="ABSENT"' in source
    assert '"$receipts_dir/deadline.json"' in source
    assert "b6_6_deadline.py cleanup" in source
    assert '--deadline-receipt-status "$deadline_receipt_status"' in source
    assert "b6_6_deadline.py disarm" not in source

