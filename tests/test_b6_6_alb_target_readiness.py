from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.b6_6_lbc_runtime import (
    ALB_NAME,
    ALB_SECURITY_GROUP,
    TargetReadinessRefusal,
    classify_target_health_response,
    wait_for_stable_target_health,
)
from scripts.b6_6_runner import safe_alb_refusal


ROOT = Path(__file__).resolve().parents[1]
HEALTHY_FIXTURE = (
    ROOT
    / "tests/fixtures/aws/elbv2-describe-target-health-medzen-ehrbase-healthy.json"
)
EMPTY_FIXTURE = (
    ROOT / "tests/fixtures/aws/elbv2-describe-target-health-cache-proxy-test.json"
)
ALB_ARN = (
    "arn:aws:elasticloadbalancing:eu-central-1:558069890522:"
    "loadbalancer/app/medzen-b6-window/0123456789abcdef"
)
TARGET_GROUP_ARN = (
    "arn:aws:elasticloadbalancing:eu-central-1:558069890522:"
    "targetgroup/k8s-medzen-speechor/0123456789abcdef"
)


def _descriptions(path: Path) -> list[dict]:
    return json.loads(path.read_bytes())["TargetHealthDescriptions"]


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


class FakeElb:
    def __init__(self, responses: list[list[dict]]) -> None:
        self.responses = list(responses)
        self.target_health_calls = 0

    def describe_load_balancers(self, **_: object) -> dict:
        return {
            "LoadBalancers": [
                {
                    "LoadBalancerName": ALB_NAME,
                    "LoadBalancerArn": ALB_ARN,
                    "Scheme": "internal",
                    "Type": "application",
                    "SecurityGroups": [ALB_SECURITY_GROUP],
                    "State": {"Code": "active"},
                }
            ]
        }

    def describe_target_groups(self, **_: object) -> dict:
        return {"TargetGroups": [{"TargetGroupArn": TARGET_GROUP_ARN}]}

    def describe_target_health(self, **_: object) -> dict:
        index = min(self.target_health_calls, len(self.responses) - 1)
        self.target_health_calls += 1
        return {"TargetHealthDescriptions": self.responses[index]}


def test_recorded_real_healthy_response_requires_three_stable_observations() -> None:
    healthy = _descriptions(HEALTHY_FIXTURE)
    clock = Clock()
    client = FakeElb([healthy, healthy, healthy])
    result = wait_for_stable_target_health(
        client,
        wait_seconds=30,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result["load_balancer_active"] is True
    assert result["stable_healthy_observations"] == 3
    assert result["target_count"] == 2
    assert result["polls"] == 3


def test_recorded_real_empty_response_times_out_fail_closed() -> None:
    empty = _descriptions(EMPTY_FIXTURE)
    clock = Clock()
    with pytest.raises(
        TargetReadinessRefusal, match="ALB_TARGET_STABLE_HEALTH_TIMEOUT"
    ):
        wait_for_stable_target_health(
            FakeElb([empty]),
            wait_seconds=20,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        )


def test_initial_registration_is_retryable_but_unhealthy_is_terminal() -> None:
    healthy = _descriptions(HEALTHY_FIXTURE)
    initial = copy.deepcopy(healthy)
    for item in initial:
        item["TargetHealth"] = {
            "State": "initial",
            "Reason": "Elb.RegistrationInProgress",
        }
    assert classify_target_health_response(initial) == {
        "classification": "RETRY",
        "reason_code": "ALB_TARGETS_INITIAL",
    }
    unhealthy = copy.deepcopy(healthy)
    unhealthy[0]["TargetHealth"] = {
        "State": "unhealthy",
        "Reason": "Target.FailedHealthChecks",
    }
    with pytest.raises(
        TargetReadinessRefusal, match="ALB_TARGET_TERMINAL_UNHEALTHY"
    ):
        classify_target_health_response(unhealthy)


def test_target_set_change_resets_stability_counter() -> None:
    healthy = _descriptions(HEALTHY_FIXTURE)
    one_target = healthy[:1]
    clock = Clock()
    client = FakeElb([one_target, healthy, healthy, healthy])
    result = wait_for_stable_target_health(
        client,
        wait_seconds=40,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    assert result["polls"] == 4
    assert result["target_count"] == 2
    assert result["stable_healthy_observations"] == 3


def test_allowlisted_target_gate_refusal_is_safe_for_receipt(tmp_path: Path) -> None:
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps(
            {
                "status": "REFUSED",
                "reason_code": "ALB_TARGET_STABLE_HEALTH_TIMEOUT",
            }
        )
    )
    assert safe_alb_refusal(payload) == {
        "reason_code": "ALB_TARGET_STABLE_HEALTH_TIMEOUT"
    }
    payload.write_text(
        json.dumps({"status": "REFUSED", "reason_code": "UNREVIEWED_REASON"})
    )
    assert safe_alb_refusal(payload) is None
