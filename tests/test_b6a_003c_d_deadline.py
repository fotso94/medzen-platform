from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import b6a_003c_b_deadline as base
from scripts.b6a_003c_d_deadline import ACTION_NAME, DeadlineControl, MAX_WINDOW_SECONDS
from tests.test_b6a_003c_c_deadline import AutoScaling, EKS, NOW, kubernetes


def test_003c_d_deadline_uses_only_confirmed_remaining_allowance():
    autoscaling = AutoScaling()
    result = DeadlineControl(autoscaling, EKS(), kubernetes).arm(
        now=NOW, window_seconds=MAX_WINDOW_SECONDS
    )
    assert MAX_WINDOW_SECONDS == 6520
    assert result["conservative_prior_billed_seconds"] == 680
    assert result["conservative_cumulative_maximum_seconds"] == 7200
    assert autoscaling.actions[0]["ScheduledActionName"] == ACTION_NAME
    with pytest.raises(base.DeadlineRefusal, match="6520"):
        DeadlineControl(AutoScaling(), EKS(), kubernetes).arm(
            now=NOW, window_seconds=6521
        )


def test_003c_d_cleanup_disarms_only_its_own_deadline_after_zero(tmp_path):
    autoscaling = AutoScaling()
    control = DeadlineControl(autoscaling, EKS(), kubernetes)
    control.arm(now=NOW, window_seconds=MAX_WINDOW_SECONDS)
    result = control.disarm_003c_d_after_zero(kubeconfig=tmp_path / "kubeconfig")
    assert result["status"] == "PASS"
    assert result["action"] == "medzen-b6a-003c-d-deadline-scale-zero"
    assert result["deadline_disarmed_after_zero"] is True


def test_003c_d_does_not_mutate_prior_deadline_constants():
    assert base.ACTION_NAME == "medzen-b6a-003c-b-deadline-scale-zero"
    assert ACTION_NAME == "medzen-b6a-003c-d-deadline-scale-zero"
