from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import ReceiptStore
from scripts.asr_base_model_async_observations import (
    AsyncObservationRefusal,
    NETWORK_AND_LISTENER_PRESENT,
    NETWORK_RECEIPT_ABSENT,
    audit_async_observation_sites,
    audit_remote_ssm_observation_sites,
    network_receipt_observation_command,
    observe_volume_attachment,
    parse_network_receipt_observation,
    pilot_pod_terminal_observation,
    volume_device_poll_commands,
    volume_mount_command_template,
    volume_mount_commands,
)
from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002S.json"


def context(tmp_path: Path, *, injection: str):
    bindings = json.loads(BINDINGS.read_bytes())
    operations, boundary = build_rehearsal_operations(
        bindings, injection=injection
    )
    value = AttemptContext(
        attempt=20,
        bindings=bindings,
        receipts=ReceiptStore(
            tmp_path / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=tmp_path,
    )
    return operations, boundary, value


def test_observation_command_is_read_only_and_absence_safe() -> None:
    command = network_receipt_observation_command(
        "/var/lib/medzen-asr-eval/attempt-21"
    )
    assert "/usr/bin/test -s" in command
    assert "/usr/bin/cat" in command
    assert NETWORK_RECEIPT_ABSENT in command
    assert NETWORK_AND_LISTENER_PRESENT in command
    assert not any(value in command for value in ("rm ", "touch ", "chmod ", "chown "))


def test_receipt_parser_refuses_unknown_malformed_and_failed_values() -> None:
    with pytest.raises(AsyncObservationRefusal) as unknown:
        parse_network_receipt_observation("UNKNOWN\n")
    assert unknown.value.reason_code == "NETWORK_RECEIPT_OBSERVATION_MARKER_UNKNOWN"
    with pytest.raises(AsyncObservationRefusal) as malformed:
        parse_network_receipt_observation(f"{NETWORK_AND_LISTENER_PRESENT}\nnot-json\n")
    assert malformed.value.reason_code == "NETWORK_PROBE_RECEIPT_MALFORMED"
    with pytest.raises(AsyncObservationRefusal) as refused:
        parse_network_receipt_observation(
            f'{NETWORK_AND_LISTENER_PRESENT}\n'
            '{"status":"REFUSED_NETWORK_ISOLATION_PRE_TORCH",'
            '"reason_code":"POSITIVE_NETWORK_CONVERGENCE_TIMEOUT",'
            '"torch_imported":false}\n'
        )
    assert refused.value.reason_code == "NETWORK_PROBE_REFUSED"
    assert "POSITIVE_NETWORK_CONVERGENCE_TIMEOUT" in refused.value.detail


def test_delayed_receipt_reaches_two_stable_observations(tmp_path: Path) -> None:
    operations, boundary, value = context(
        tmp_path, injection="network_receipt_delayed"
    )
    result = operations._wait_pilot_network_receipts(
        value,
        pod_name="asr-pilot-rehearsal",
        instance_id="i-rehearsal-gpu",
        staging_path="/var/lib/medzen-asr-eval/attempt-20",
    )
    assert result["status"] == "PASS_STABLE_NETWORK_AND_LISTENER_RECEIPTS"
    assert result["stable_observations"] == 2
    assert result["network_absent_observations"] == 2
    assert boundary.pilot_receipt_observation_sequence == [
        "ABSENT",
        "ABSENT",
        "READY",
        "READY",
    ]


def test_never_arriving_receipt_refuses_on_bounded_timeout(tmp_path: Path) -> None:
    operations, boundary, value = context(
        tmp_path, injection="network_receipt_timeout"
    )
    with pytest.raises(OperationRefusal) as captured:
        operations._wait_pilot_network_receipts(
            value,
            pod_name="asr-pilot-rehearsal",
            instance_id="i-rehearsal-gpu",
            staging_path="/var/lib/medzen-asr-eval/attempt-20",
        )
    assert captured.value.reason_code == "NETWORK_PROBE_RECEIPT_TIMEOUT"
    assert captured.value.outcome == "BLOCKED_NETWORK_ISOLATION"
    assert boundary.monotonic_seconds < 300
    assert boundary.pilot_receipt_reads == 30


def test_terminal_pod_refuses_before_another_receipt_read(tmp_path: Path) -> None:
    operations, boundary, value = context(
        tmp_path, injection="network_receipt_pod_terminal"
    )
    with pytest.raises(OperationRefusal) as captured:
        operations._wait_pilot_network_receipts(
            value,
            pod_name="asr-pilot-rehearsal",
            instance_id="i-rehearsal-gpu",
            staging_path="/var/lib/medzen-asr-eval/attempt-20",
        )
    assert captured.value.reason_code == (
        "PILOT_POD_TERMINAL_BEFORE_NETWORK_RECEIPT"
    )
    assert captured.value.outcome == "BLOCKED_NETWORK_ISOLATION"
    assert boundary.pilot_pod_reads == 1
    assert boundary.pilot_receipt_reads == 0


def test_network_refusal_diagnostics_retain_receipt_and_policy_agent(
    tmp_path: Path,
) -> None:
    operations, _, value = context(tmp_path, injection="pilot_job_refused")
    state = operations._state(value)
    state.update({
        "instance_id": "i-rehearsal-gpu",
        "node_name": "ip-rehearsal-gpu",
        "staging_path": "/var/lib/medzen-asr-eval/attempt-20",
    })
    operations._save_state(value, state)

    diagnostic = operations._capture_pilot_workload_refusal_diagnostics(
        value,
        pod_name="asr-pilot-rehearsal",
        failure=OperationRefusal(
            "NETWORK_PROBE_REFUSED",
            "synthetic pre-torch refusal",
        ),
    )

    assert diagnostic["status"] == "CAPTURED_BEFORE_CLEANUP"
    assert diagnostic["network_probe_receipt"]["status"] == "CAPTURED"
    assert diagnostic["network_probe_receipt"]["reason_code"] == (
        "POSITIVE_NETWORK_CONVERGENCE_TIMEOUT"
    )
    assert diagnostic["network_policy_agent"]["status"] == "CAPTURED"
    assert diagnostic["network_policy_agent"]["pod"] == "aws-node-rehearsal"
    assert diagnostic["credentials_presigned_urls_or_environment_values_recorded"] is False


def test_pod_shape_and_entire_async_site_audit_fail_closed() -> None:
    with pytest.raises(AsyncObservationRefusal) as captured:
        pilot_pod_terminal_observation({"status": {}})
    assert captured.value.reason_code == "PILOT_POD_RESPONSE_MALFORMED"
    result = audit_async_observation_sites(ROOT)
    assert result["status"] == "PASS_ALL_POST_START_OBSERVATIONS_BOUNDED"
    assert result["site_count"] == 7
    assert result["direct_one_shot_network_or_listener_reads"] == 0
    assert result["malformed_drift_or_terminal_state_retried"] is False


def test_volume_attachment_shape_accepts_only_exact_progress_or_ready() -> None:
    base = {
        "Volumes": [
            {
                "VolumeId": "vol-a1",
                "State": "in-use",
                "Attachments": [
                    {
                        "VolumeId": "vol-a1",
                        "InstanceId": "i-a1",
                        "Device": "/dev/sdf",
                        "State": "attaching",
                    }
                ],
            }
        ]
    }
    waiting = observe_volume_attachment(
        base, volume_id="vol-a1", instance_id="i-a1"
    )
    assert waiting["status"] == "WAIT_ATTACHMENT"
    base["Volumes"][0]["Attachments"] = []
    absent = observe_volume_attachment(
        base, volume_id="vol-a1", instance_id="i-a1"
    )
    assert absent["status"] == "WAIT_ATTACHMENT"
    assert absent["attachment_state"] == "absent"
    base["Volumes"][0]["Attachments"] = [
        {
            "VolumeId": "vol-a1",
            "InstanceId": "i-a1",
            "Device": "/dev/sdf",
            "State": "attached",
        }
    ]
    assert observe_volume_attachment(
        base, volume_id="vol-a1", instance_id="i-a1"
    )["status"] == "READY"
    base["Volumes"][0]["Attachments"][0]["InstanceId"] = "i-wrong"
    with pytest.raises(AsyncObservationRefusal) as captured:
        observe_volume_attachment(base, volume_id="vol-a1", instance_id="i-a1")
    assert captured.value.reason_code == "VOLUME_ATTACHMENT_TARGET_DIFFERS"


def test_volume_device_poll_is_bounded_typed_and_has_no_private_literal() -> None:
    commands = volume_device_poll_commands("vol-0a1b2c3d")
    body = "\n".join(commands)
    assert "while [ \"$device_observation\" -lt 60 ]" in body
    assert "/usr/bin/sleep 2" in body
    assert "MEDZEN_EBS_DEVICE_READY" in body
    assert "MEDZEN_EBS_DEVICE_TIMEOUT" in body
    assert "exit 42" in body
    assert body.endswith('/usr/bin/test -b "$device"')
    with pytest.raises(AsyncObservationRefusal) as captured:
        volume_device_poll_commands("not-a-volume")
    assert captured.value.reason_code == "VOLUME_ID_MALFORMED"


def test_mount_template_hash_is_volume_independent_but_parameters_are_not() -> None:
    template = volume_mount_command_template()
    first = volume_mount_commands("vol-0a1b2c3d")
    second = volume_mount_commands("vol-0d4c3b2a")
    assert template == volume_mount_command_template()
    assert first != second
    assert len(template) == len(first) == len(second)
    assert "vol0a1b2c3d" in "\n".join(first)
    assert "vol0d4c3b2a" in "\n".join(second)
    assert "__MEDZEN_EBS_VOLUME_SERIAL__" in "\n".join(template)
    assert "__MEDZEN_EBS_VOLUME_SERIAL__" not in "\n".join(first + second)


def test_attachment_wait_crosses_attaching_then_two_stable_reads(tmp_path: Path) -> None:
    operations, boundary, _ = context(tmp_path, injection="volume_device_delayed_ready")
    volume = operations.ec2.create_volume()
    volume_id = volume["VolumeId"]
    operations.ec2.attach_volume(
        VolumeId=volume_id, InstanceId="i-rehearsal-gpu", Device="/dev/sdf"
    )
    result = operations._wait_volume_attachment(
        volume_id=volume_id, instance_id="i-rehearsal-gpu"
    )
    assert result["status"] == "PASS_STABLE_VOLUME_ATTACHMENT"
    assert result["observation_sequence"] == [
        "absent",
        "attaching",
        "attached",
        "attached",
    ]
    assert boundary.volume_attachment_observation_sequence == [
        "absent",
        "attaching",
        "attached",
        "attached",
    ]


def test_attachment_wait_refuses_when_attachment_never_stabilizes(tmp_path: Path) -> None:
    operations, boundary, _ = context(
        tmp_path, injection="volume_attachment_never_attached"
    )
    volume_id = operations.ec2.create_volume()["VolumeId"]
    operations.ec2.attach_volume(
        VolumeId=volume_id, InstanceId="i-rehearsal-gpu", Device="/dev/sdf"
    )
    with pytest.raises(OperationRefusal) as captured:
        operations._wait_volume_attachment(
            volume_id=volume_id, instance_id="i-rehearsal-gpu"
        )
    assert captured.value.reason_code == "VOLUME_ATTACHMENT_TIMEOUT"
    assert boundary.monotonic_seconds < 300
    assert set(boundary.volume_attachment_observation_sequence) == {
        "absent",
        "attaching",
    }


def test_remote_device_boundary_models_absent_then_present_and_timeout(
    tmp_path: Path,
) -> None:
    commands = ["#!/bin/bash", "set -euo pipefail", *volume_device_poll_commands("vol-a1")]
    passing, passed_boundary, _ = context(
        tmp_path / "pass", injection="volume_device_delayed_ready"
    )
    assert passing._ssm("i-rehearsal-gpu", commands)["status"] == "Success"
    assert passed_boundary.volume_device_observation_sequence == ["ABSENT", "PRESENT"]

    refusing, refused_boundary, _ = context(
        tmp_path / "refuse", injection="volume_device_never_present"
    )
    with pytest.raises(OperationRefusal) as captured:
        refusing._ssm("i-rehearsal-gpu", commands, pre_model_safe_output=True)
    assert captured.value.reason_code == "SSM_COMMAND_REFUSED"
    assert "MEDZEN_EBS_DEVICE_TIMEOUT" in captured.value.detail
    assert refused_boundary.volume_device_observation_sequence == ["ABSENT", "TIMEOUT"]


def test_remote_ssm_observation_audit_classifies_every_site() -> None:
    result = audit_remote_ssm_observation_sites(ROOT)
    assert result["status"] == "PASS_REMOTE_SSM_OBSERVATION_AUDIT"
    assert result["site_count"] == 10
    assert result["asynchronous_one_shot_success_gates"] == 0
    assert result["unclassified_ssm_call_sites"] == 0
