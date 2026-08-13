from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_node_staging import (
    STAGING_PRESIGNED_URL_SECONDS,
    STAGING_SSM_TIMEOUT_SECONDS,
    NodeStagingRefusal,
    audit_staging_commands,
    numeric_identity_command,
    staging_prelude,
)
from scripts.asr_base_model_pilot_k8s import render, verify
from scripts.asr_base_model_pilot_workload import (
    JOB_ACTIVE_DEADLINE_SECONDS,
    PILOT_ENVIRONMENT,
    audit_pilot_workload,
    workload_argv_sha256,
)
from scripts.asr_external_tool import sanitize_bytes


IMAGE = "sha256:" + "1" * 64


def bindings() -> dict:
    return {
        "image": {"linux_amd64_digest": IMAGE},
        "pilot_bundle": {"sha256": "2" * 64},
    }


def test_attempt16_name_lookup_failure_and_numeric_fix_are_distinct() -> None:
    old = "sudo -u '#10001' /usr/bin/id"
    with pytest.raises(NodeStagingRefusal) as captured:
        audit_staging_commands(["#!/bin/bash", "set -euo pipefail", old])
    assert captured.value.reason_code == "NODE_STAGING_ENVIRONMENT_ASSUMPTION"
    corrected = numeric_identity_command(
        "/usr/bin/test \"$(/usr/bin/id -u)\" = 10001"
    )
    assert "sudo -u" not in corrected
    assert "--userspec=10001:10001" in corrected
    assert "/usr/bin/env -i HOME=/tmp" in corrected


def test_staging_prelude_has_explicit_shell_tools_and_environment() -> None:
    commands, base = staging_prelude(17)
    commands.append(numeric_identity_command("/usr/bin/printf pass"))
    result = audit_staging_commands(commands)
    assert result["status"] == "PASS_NODE_STAGING_ASSUMPTION_AUDIT"
    assert result["passwd_or_group_name_lookups"] == 0
    assert result["relative_staging_executables"] == 0
    assert result["inherited_environment_values"] == 0
    assert base.endswith("attempt-17")
    assert STAGING_PRESIGNED_URL_SECONDS >= STAGING_SSM_TIMEOUT_SECONDS + 600


def test_staging_audit_refuses_ambient_or_relative_assumptions() -> None:
    for defect in (
        "sudo -u '#10001' /usr/bin/id",
        "/usr/bin/printf $HOME",
        "curl https://example.invalid",
    ):
        commands, _ = staging_prelude(17)
        commands.extend([numeric_identity_command("/usr/bin/printf pass"), defect])
        with pytest.raises(NodeStagingRefusal):
            audit_staging_commands(commands)


def test_presigned_query_values_are_sanitized() -> None:
    raw = (
        "https://bucket.s3.amazonaws.com/key?X-Amz-Credential=AKIA/20260813&"
        "X-Amz-Signature=abcdef&versionId=secret-version"
    )
    value = sanitize_bytes(raw)
    assert "AKIA" not in value
    assert "abcdef" not in value
    assert "secret-version" not in value
    assert "X-Amz-Credential=<REDACTED>" in value


def test_pilot_workload_is_absolute_bounded_and_explicit() -> None:
    result = audit_pilot_workload()
    assert result["status"] == "PASS_PILOT_WORKLOAD_STATIC_AUDIT"
    assert result["historical_live_pass"] is False
    assert result["absolute_python_invocations"] == 3
    assert result["account_name_lookups"] == 0
    assert result["ambient_environment_reads"] == 0
    assert result["listener_timeout_exit_code"] == 71
    assert result["job_active_deadline_seconds"] == 9000
    assert len(workload_argv_sha256()) == 64


def test_rendered_attempt17_workload_enforces_the_audit() -> None:
    rendered = render(
        bindings(), ["10.0.1.7", "10.0.2.8"], ["52.219.0.0/16"], 17
    )
    result = verify(rendered, IMAGE, 17)
    assert result["pilot_workload_audit"]["status"] == (
        "PASS_PILOT_WORKLOAD_STATIC_AUDIT"
    )
    job = list(yaml.safe_load_all(rendered))[-1]
    assert job["spec"]["activeDeadlineSeconds"] == JOB_ACTIVE_DEADLINE_SECONDS
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["env"] == list(PILOT_ENVIRONMENT)
    assert container["args"][0].endswith(
        "--aggregate-receipt /output/aggregate.json"
    )


def test_node_equivalent_qualification_is_reproducible_when_docker_available(
    tmp_path: Path,
) -> None:
    available = subprocess.run(
        ["docker", "info"], capture_output=True, check=False, timeout=30
    )
    if available.returncode != 0:
        pytest.skip("local Docker daemon unavailable")
    output = tmp_path / "qualification.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/qualify_asr_base_model_node_staging.py"),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=1200,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    value = json.loads(output.read_bytes())
    assert value["status"] == "PASS_FAILURE_AND_FIX_NODE_EQUIVALENT_QUALIFICATION"
    assert value["observed_failure"]["stderr_sanitized"] == (
        "sudo: unknown user #10001"
    )
    assert value["observed_failure"]["observed_inner_command_returncode"] == 1
    assert value["corrected_path"]["status"] == "PASS_NUMERIC_STAGING"
    assert value["local_equivalent"]["linux_amd64_child"].endswith(
        "sha256:47821fb77b737fb67c93e451c0953e7d3325ee9d41f8d3ecc799fd9b96e6ca9c"
    )


def test_attempt16_historical_evidence_remains_byte_identical() -> None:
    path = ROOT / (
        "platform/evidence/"
        "ASR-BASE-MODEL-PACKET-2026-002O-ATTEMPT-16-NODE-STAGING-REFUSAL.json"
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "4f32505301c25d57510465db35edd213c498834a7f1d12a7a12b0a3cf7d6f025"
    )
