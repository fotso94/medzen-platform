"""Attempt-32 hardening: the bindings-bound attempt window and Job deadline."""

import json
from pathlib import Path

import pytest
import yaml

from scripts.asr_base_model_pilot_k8s import render, verify
from scripts.asr_base_model_pilot_runner import (
    OperationRefusal,
    build_attempt_context,
    validate_authorization_payload,
)
from scripts.asr_base_model_pilot_workload import (
    ATTEMPT_WINDOW_DEFAULT_SECONDS,
    ATTEMPT_WINDOW_JOB_RESERVE_SECONDS,
    ATTEMPT_WINDOW_MAXIMUM_SECONDS,
    JOB_ACTIVE_DEADLINE_SECONDS,
    PilotWorkloadRefusal,
    bound_attempt_window,
)

DIGEST = "sha256:" + "a" * 64


def _render_bindings(extra=None):
    bindings = {
        "image": {"linux_amd64_digest": DIGEST},
        "input_freeze": {"pilot_rows": 3616},
    }
    if extra:
        bindings.update(extra)
    return bindings


def test_absent_block_keeps_the_pilot_window():
    value = bound_attempt_window({})
    assert value == {
        "seconds_each": ATTEMPT_WINDOW_DEFAULT_SECONDS,
        "job_active_deadline_seconds": JOB_ACTIVE_DEADLINE_SECONDS,
        "source": "DEFAULT_PILOT_WINDOW",
    }


def test_bound_window_requires_the_fixed_reserve():
    value = bound_attempt_window(
        {"attempt_window": {"seconds_each": 18000, "job_active_deadline_seconds": 16200}}
    )
    assert value["seconds_each"] == 18000
    assert value["job_active_deadline_seconds"] == 16200
    assert value["source"] == "BINDINGS_ATTEMPT_WINDOW"
    assert 18000 - 16200 == ATTEMPT_WINDOW_JOB_RESERVE_SECONDS


@pytest.mark.parametrize(
    "block",
    [
        {"seconds_each": 18000},
        {"seconds_each": 18000, "job_active_deadline_seconds": 16200, "extra": 1},
        {"seconds_each": 18000, "job_active_deadline_seconds": 16000},
        {"seconds_each": 10799, "job_active_deadline_seconds": 8999},
        {"seconds_each": ATTEMPT_WINDOW_MAXIMUM_SECONDS + 1800, "job_active_deadline_seconds": ATTEMPT_WINDOW_MAXIMUM_SECONDS},
        {"seconds_each": True, "job_active_deadline_seconds": 16200},
        {"seconds_each": 18000.0, "job_active_deadline_seconds": 16200},
        {"seconds_each": "18000", "job_active_deadline_seconds": 16200},
        [],
    ],
)
def test_malformed_window_blocks_refuse(block):
    with pytest.raises(PilotWorkloadRefusal):
        bound_attempt_window({"attempt_window": block})


def test_render_binds_the_job_deadline_from_the_window():
    bindings = _render_bindings(
        {"attempt_window": {"seconds_each": 18000, "job_active_deadline_seconds": 16200}}
    )
    rendered = render(bindings, ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], 32)
    job = [doc for doc in yaml.safe_load_all(rendered) if doc][-1]
    assert job["spec"]["activeDeadlineSeconds"] == 16200
    result = verify(rendered, DIGEST, 32, expected_job_active_deadline_seconds=16200)
    assert result["status"] == "PASS_K8S_RENDER"
    with pytest.raises(ValueError, match="active deadline differs"):
        verify(rendered, DIGEST, 32)


def test_render_without_a_window_block_keeps_the_pilot_deadline():
    rendered = render(_render_bindings(), ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], 32)
    job = [doc for doc in yaml.safe_load_all(rendered) if doc][-1]
    assert job["spec"]["activeDeadlineSeconds"] == JOB_ACTIVE_DEADLINE_SECONDS
    assert verify(rendered, DIGEST, 32)["status"] == "PASS_K8S_RENDER"


def test_attempt_38_refuses_in_render():
    with pytest.raises(ValueError, match="attempt must be 1 through 37"):
        render(_render_bindings(), ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], 38)


def test_attempt_context_inherits_the_bound_window(tmp_path):
    context = build_attempt_context(
        root=tmp_path / "repo",
        workdir=tmp_path / "work",
        attempt=32,
        bindings={"attempt_window": {"seconds_each": 18000, "job_active_deadline_seconds": 16200}},
        packet_sha256="0" * 64,
        authorization_sha256="a" * 64,
    )
    assert context.deadline_seconds == 18000


def test_authorization_seconds_each_must_match_the_bound_window():
    payload = {
        "id": "AUTH-X",
        "status": "owner-approved",
        "packet": {"sha256": "0" * 64},
        "risk_acceptance": {"sha256": "3" * 64},
        "attempts": {
            "authorized_numbers": [32],
            "maximum": 1,
            "seconds_each": 18000,
            "non_transferable": True,
        },
    }
    result = validate_authorization_payload(
        payload,
        expected_id="AUTH-X",
        packet_sha256="0" * 64,
        risk_sha256="3" * 64,
        attempt=32,
        expected_seconds_each=18000,
    )
    assert result["seconds_each"] == 18000
    with pytest.raises(OperationRefusal):
        validate_authorization_payload(
            payload,
            expected_id="AUTH-X",
            packet_sha256="0" * 64,
            risk_sha256="3" * 64,
            attempt=32,
        )
