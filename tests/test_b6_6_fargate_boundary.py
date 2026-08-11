from __future__ import annotations

import copy
import json
import re
import textwrap
from pathlib import Path

import pytest

from scripts.b6_6_fargate_probe import (
    COMMAND,
    ENTRY_POINT,
    IMAGE,
    PROBE_ATTEMPTS,
    PROBE_BAD_STATUS_EXIT_CODE,
    PROBE_CONNECT_EXIT_CODE,
    PROBE_DNS_EXIT_CODE,
    PROBE_INTERVAL_SECONDS,
    PROBE_PROGRAM,
    ROLE_ARN,
    TASK_FAMILY,
    ProbeRefusal,
    _described_task_or_pending,
    _task_definition,
    run_isolated_probe,
    _safe_task_result,
)
from scripts.b6_6_runner import safe_fargate_refusal


ROOT = Path(__file__).resolve().parents[1]


def _definition() -> dict:
    return {
        "family": TASK_FAMILY,
        "status": "ACTIVE",
        "networkMode": "awsvpc",
        "cpu": "256",
        "memory": "512",
        "executionRoleArn": ROLE_ARN,
        "requiresCompatibilities": ["FARGATE"],
        "taskDefinitionArn": (
            "arn:aws:ecs:eu-central-1:558069890522:"
            "task-definition/medzen-b6-window-probe:6"
        ),
        "containerDefinitions": [
            {
                "name": "probe",
                "image": IMAGE,
                "essential": True,
                "entryPoint": ENTRY_POINT,
                "command": COMMAND,
                "environment": [
                    {"name": "TARGET_URL", "value": "http://not-set.invalid/readyz"}
                ],
                "readonlyRootFilesystem": True,
                "linuxParameters": {
                    "capabilities": {"add": [], "drop": ["ALL"]},
                    "initProcessEnabled": True,
                },
            }
        ],
    }


class FakeEcs:
    def __init__(self, definition: dict):
        self.definition = definition

    def describe_task_definition(self, **_: str) -> dict:
        return {"taskDefinition": self.definition}


def test_live_hardened_task_definition_shape_is_accepted() -> None:
    value = _definition()
    assert _task_definition(FakeEcs(value)) == value["taskDefinitionArn"]


def test_terraform_and_verifier_bind_exact_retry_program() -> None:
    terraform = (ROOT / "infra/b6_integration_window.tf").read_text()
    match = re.search(
        r"b6_probe_runtime_program = trimspace\(<<-PY\n(?P<program>.*?)\n\s+PY\n",
        terraform,
        re.DOTALL,
    )
    assert match is not None
    assert textwrap.dedent(match.group("program")).strip() == PROBE_PROGRAM
    assert PROBE_ATTEMPTS == 24
    assert PROBE_INTERVAL_SECONDS == 10
    assert "sys.exit(0)" in PROBE_PROGRAM


@pytest.mark.parametrize(
    "capabilities",
    [
        {"add": [], "drop": []},
        {"add": ["NET_ADMIN"], "drop": ["ALL"]},
        {"add": [], "drop": ["NET_RAW"]},
    ],
)
def test_missing_or_different_capability_hardening_refuses(capabilities: dict) -> None:
    value = copy.deepcopy(_definition())
    value["containerDefinitions"][0]["linuxParameters"]["capabilities"] = capabilities
    with pytest.raises(ProbeRefusal, match="PROBE_CONTAINER_BOUNDARY_DIFFERS"):
        _task_definition(FakeEcs(value))


def test_incidental_linux_parameter_shape_does_not_refuse_hardening() -> None:
    value = copy.deepcopy(_definition())
    value["containerDefinitions"][0]["linuxParameters"] = {
        "capabilities": {"drop": ["ALL", "NET_RAW"]},
        "initProcessEnabled": True,
        "sharedMemorySize": 64,
    }
    assert _task_definition(FakeEcs(value)) == value["taskDefinitionArn"]


def test_safe_refusal_payload_preserves_only_allowlisted_fields(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    path.write_text(
        json.dumps(
            {
                "status": "REFUSED",
                "reason_code": "PROBE_CONTAINER_BOUNDARY_DIFFERS",
                "application_started": False,
                "readyz_request_completed": False,
                "request_body": "must-not-be-persisted",
                "stderr": "must-not-be-persisted",
            }
        )
    )
    assert safe_fargate_refusal(path) == {
        "reason_code": "PROBE_CONTAINER_BOUNDARY_DIFFERS",
        "application_started": False,
        "readyz_request_completed": False,
    }


def test_unknown_or_malformed_refusal_is_not_propagated(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    path.write_text(
        json.dumps(
            {
                "status": "REFUSED",
                "reason_code": "UNREVIEWED_REASON",
                "application_started": False,
                "readyz_request_completed": False,
            }
        )
    )
    assert safe_fargate_refusal(path) is None
    path.write_text("not-json")
    assert safe_fargate_refusal(path) is None


class IsolatedEcs(FakeEcs):
    def __init__(self, definition: dict):
        super().__init__(definition)
        self.run_request: dict | None = None

    def run_task(self, **kwargs: object) -> dict:
        self.run_request = kwargs
        return {"tasks": [{"taskArn": "arn:aws:ecs:eu-central-1:558069890522:task/one"}]}

    def describe_tasks(self, **_: object) -> dict:
        return {
            "tasks": [
                {
                    "taskArn": "arn:aws:ecs:eu-central-1:558069890522:task/one",
                    "lastStatus": "STOPPED",
                    "containers": [
                        {"lastStatus": "STOPPED", "exitCode": 0}
                    ],
                }
            ]
        }


def test_isolated_probe_uses_only_endpoint_sg_and_proves_image_start(monkeypatch) -> None:
    ecs = IsolatedEcs(_definition())
    monkeypatch.setattr(
        "scripts.b6_6_fargate_probe.verify_available",
        lambda _: {"endpoint_security_group_id": "sg-probe-only"},
    )
    result = run_isolated_probe(ecs, object(), 300, sleep=lambda _: None)
    assert result["status"] == "PASS"
    assert result["image_pull_proven"] is True
    assert result["readyz_request_completed"] is False
    assert result["probe_task_security_group_count"] == 1
    assert ecs.run_request is not None
    network = ecs.run_request["networkConfiguration"]["awsvpcConfiguration"]
    assert network["securityGroups"] == ["sg-probe-only"]
    assert network["assignPublicIp"] == "DISABLED"
    assert ecs.run_request["overrides"]["containerOverrides"][0]["command"] == [
        "import sys; assert sys.version_info[:2] >= (3, 12)"
    ]


def test_runtime_id_never_classifies_application_start_without_exit_code() -> None:
    result = _safe_task_result(
        {
            "taskArn": "arn:aws:ecs:eu-central-1:558069890522:task/one",
            "lastStatus": "STOPPED",
            "stopCode": "TaskFailedToStart",
            "stoppedReason": "ECR registry auth failed",
            "containers": [
                {
                    "lastStatus": "STOPPED",
                    "exitCode": None,
                    "runtimeId": "present-even-though-process-never-ran",
                }
            ],
        }
    )
    assert result["status"] == "REFUSED"
    assert result["reason_code"] == "ECR_IMAGE_PULL_FAILURE"
    assert result["application_started"] is False
    assert result["container_exit_code_present"] is False


def test_stopped_container_with_integer_exit_proves_application_started() -> None:
    result = _safe_task_result(
        {
            "taskArn": "arn:aws:ecs:eu-central-1:558069890522:task/one",
            "lastStatus": "STOPPED",
            "containers": [{"lastStatus": "STOPPED", "exitCode": 12}],
        }
    )
    assert result["status"] == "REFUSED"
    assert result["application_started"] is True
    assert result["container_exit_code_present"] is True


@pytest.mark.parametrize(
    ("exit_code", "reason_code"),
    [
        (PROBE_DNS_EXIT_CODE, "PROBE_DNS_RETRIES_EXHAUSTED"),
        (PROBE_CONNECT_EXIT_CODE, "PROBE_CONNECT_RETRIES_EXHAUSTED"),
        (PROBE_BAD_STATUS_EXIT_CODE, "PROBE_BAD_STATUS_OR_BODY_RETRIES_EXHAUSTED"),
    ],
)
def test_retry_exhaustion_exit_code_names_failing_layer(
    exit_code: int, reason_code: str
) -> None:
    result = _safe_task_result(
        {
            "taskArn": "arn:aws:ecs:eu-central-1:558069890522:task/one",
            "lastStatus": "STOPPED",
            "stopCode": "EssentialContainerExited",
            "containers": [{"lastStatus": "STOPPED", "exitCode": exit_code}],
        }
    )
    assert result["status"] == "REFUSED"
    assert result["reason_code"] == reason_code
    assert result["container_exit_code"] == exit_code
    assert result["application_started"] is True


def test_layer_specific_retry_refusal_is_safe_for_receipt(tmp_path: Path) -> None:
    path = tmp_path / "payload.json"
    path.write_text(
        json.dumps(
            {
                "status": "REFUSED",
                "reason_code": "PROBE_DNS_RETRIES_EXHAUSTED",
                "container_exit_code": PROBE_DNS_EXIT_CODE,
                "application_started": True,
                "readyz_request_completed": False,
            }
        )
    )
    assert safe_fargate_refusal(path) == {
        "reason_code": "PROBE_DNS_RETRIES_EXHAUSTED",
        "container_exit_code": PROBE_DNS_EXIT_CODE,
        "application_started": True,
        "readyz_request_completed": False,
    }


def test_running_container_proves_application_started_before_exit() -> None:
    result = _safe_task_result(
        {
            "taskArn": "arn:aws:ecs:eu-central-1:558069890522:task/one",
            "lastStatus": "RUNNING",
            "containers": [{"lastStatus": "RUNNING"}],
        }
    )
    assert result["status"] == "REFUSED"
    assert result["application_started"] is True
    assert result["container_exit_code_present"] is False


def test_eventually_consistent_missing_task_readback_is_retryable() -> None:
    assert _described_task_or_pending({"tasks": [], "failures": []}) is None
    assert _described_task_or_pending(
        {"tasks": [], "failures": [{"reason": "MISSING"}]}
    ) is None
    with pytest.raises(ProbeRefusal, match="PROBE_TASK_READBACK_DIFFERS"):
        _described_task_or_pending(
            {"tasks": [], "failures": [{"reason": "ACCESS_DENIED"}]}
        )
