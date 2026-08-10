from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.b6_6_fargate_probe import (
    COMMAND,
    ENTRY_POINT,
    IMAGE,
    ROLE_ARN,
    TASK_FAMILY,
    ProbeRefusal,
    _task_definition,
)
from scripts.b6_6_runner import safe_fargate_refusal


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


@pytest.mark.parametrize(
    "capabilities",
    [
        {"add": [], "drop": []},
        {"add": ["NET_ADMIN"], "drop": ["ALL"]},
        {"drop": ["ALL"]},
    ],
)
def test_missing_or_different_capability_hardening_refuses(capabilities: dict) -> None:
    value = copy.deepcopy(_definition())
    value["containerDefinitions"][0]["linuxParameters"]["capabilities"] = capabilities
    with pytest.raises(ProbeRefusal, match="PROBE_CONTAINER_BOUNDARY_DIFFERS"):
        _task_definition(FakeEcs(value))


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
