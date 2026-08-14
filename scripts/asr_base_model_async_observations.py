#!/usr/bin/env python3
"""Fail-closed contracts for asynchronous pilot observations.

The live executor owns the polling loop.  This module owns the immutable
markers, validation rules and the source audit that prevents a future direct
one-shot read from bypassing that loop.
"""

from __future__ import annotations

import ast
import json
import re
import shlex
from collections import Counter
from pathlib import Path
from typing import Any


PILOT_RECEIPT_POLL_INTERVAL_SECONDS = 10
PILOT_RECEIPT_TIMEOUT_SECONDS = 300
PILOT_RECEIPT_STABLE_OBSERVATIONS = 2

VOLUME_ATTACHMENT_POLL_INTERVAL_SECONDS = 5
VOLUME_ATTACHMENT_TIMEOUT_SECONDS = 300
VOLUME_ATTACHMENT_STABLE_OBSERVATIONS = 2
VOLUME_DEVICE_POLL_INTERVAL_SECONDS = 2
VOLUME_DEVICE_TIMEOUT_SECONDS = 120

NETWORK_RECEIPT_ABSENT = "MEDZEN_NETWORK_PROBE_RECEIPT_ABSENT"
LISTENER_RECEIPT_ABSENT = "MEDZEN_INBOUND_LISTENER_RECEIPT_ABSENT"
NETWORK_AND_LISTENER_PRESENT = "MEDZEN_NETWORK_AND_LISTENER_PRESENT"
VOLUME_DEVICE_READY = "MEDZEN_EBS_DEVICE_READY"
VOLUME_DEVICE_TIMEOUT = "MEDZEN_EBS_DEVICE_TIMEOUT"
VOLUME_SERIAL_PARAMETER = "__MEDZEN_EBS_VOLUME_SERIAL__"


class AsyncObservationRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _command(*values: str) -> str:
    return shlex.join(values)


def observe_volume_attachment(
    value: dict[str, Any], *, volume_id: str, instance_id: str
) -> dict[str, Any]:
    """Validate one DescribeVolumes observation for the exact attachment."""

    volumes = value.get("Volumes") if isinstance(value, dict) else None
    if not isinstance(volumes, list) or len(volumes) != 1:
        raise AsyncObservationRefusal(
            "VOLUME_ATTACHMENT_RESPONSE_MALFORMED",
            "DescribeVolumes did not return exactly one volume",
        )
    volume = volumes[0]
    if not isinstance(volume, dict) or volume.get("VolumeId") != volume_id:
        raise AsyncObservationRefusal(
            "VOLUME_ATTACHMENT_IDENTITY_DIFFERS",
            "DescribeVolumes returned a different volume identity",
        )
    attachments = volume.get("Attachments")
    if not isinstance(attachments, list) or len(attachments) > 1:
        raise AsyncObservationRefusal(
            "VOLUME_ATTACHMENT_SHAPE_DIFFERS",
            "the exact volume attachment collection is malformed or ambiguous",
        )
    if not attachments:
        return {
            "status": "WAIT_ATTACHMENT",
            "attachment_state": "absent",
            "volume_state": volume.get("State"),
            "device": None,
        }
    attachment = attachments[0]
    if (
        not isinstance(attachment, dict)
        or attachment.get("VolumeId") != volume_id
        or attachment.get("InstanceId") != instance_id
    ):
        raise AsyncObservationRefusal(
            "VOLUME_ATTACHMENT_TARGET_DIFFERS",
            "the volume attachment targets a different volume or instance",
        )
    state = attachment.get("State")
    if state not in {"attaching", "attached"}:
        raise AsyncObservationRefusal(
            "VOLUME_ATTACHMENT_STATE_REFUSED",
            f"the exact volume attachment entered a non-progress state: {state}",
        )
    return {
        "status": "READY" if state == "attached" else "WAIT_ATTACHMENT",
        "attachment_state": state,
        "volume_state": volume.get("State"),
        "device": attachment.get("Device"),
    }


def volume_device_poll_command_template() -> list[str]:
    """Return the volume-independent bounded guest-device poll template."""

    attempts = VOLUME_DEVICE_TIMEOUT_SECONDS // VOLUME_DEVICE_POLL_INTERVAL_SECONDS
    return [
        (
            "device_path=/dev/disk/by-id/"
            f"nvme-Amazon_Elastic_Block_Store_{VOLUME_SERIAL_PARAMETER}"
        ),
        'device=""',
        "device_observation=0",
        (
            f'while [ "$device_observation" -lt {attempts} ]; do '
            'candidate="$(/usr/bin/readlink -f "$device_path" 2>/dev/null || true)"; '
            'if [ -n "$candidate" ] && /usr/bin/test -b "$candidate"; then '
            'device="$candidate"; '
            f'/usr/bin/printf \'%s\\n\' {VOLUME_DEVICE_READY}; break; fi; '
            'device_observation=$((device_observation + 1)); '
            f"/usr/bin/sleep {VOLUME_DEVICE_POLL_INTERVAL_SECONDS}; done"
        ),
        (
            'if [ -z "$device" ]; then '
            f'/usr/bin/printf \'%s\\n\' {VOLUME_DEVICE_TIMEOUT} >&2; '
            "exit 42; fi"
        ),
        '/usr/bin/test -b "$device"',
    ]


def volume_mount_command_template() -> list[str]:
    """Return the complete volume-independent mount SSM bundle template."""

    return [
        "#!/bin/bash",
        "set -euo pipefail",
        "export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "unset CDPATH ENV BASH_ENV USER LOGNAME",
        *volume_device_poll_command_template(),
        '/usr/bin/sudo /usr/sbin/mkfs.ext4 -F "$device" >/dev/null',
        "/usr/bin/sudo /usr/bin/mkdir -p /var/lib/medzen-asr-eval",
        '/usr/bin/sudo /usr/bin/mount "$device" /var/lib/medzen-asr-eval',
        "/usr/bin/sudo /usr/bin/chown 10001:10001 /var/lib/medzen-asr-eval",
    ]


def volume_mount_commands(volume_id: str) -> list[str]:
    """Render the reviewed mount template with one validated volume parameter."""

    if re.fullmatch(r"vol-[0-9a-f]+", volume_id) is None:
        raise AsyncObservationRefusal(
            "VOLUME_ID_MALFORMED", "the EBS volume identifier is malformed"
        )
    serial = volume_id.replace("-", "")
    template = volume_mount_command_template()
    rendered = [value.replace(VOLUME_SERIAL_PARAMETER, serial) for value in template]
    if any(VOLUME_SERIAL_PARAMETER in value for value in rendered):
        raise AsyncObservationRefusal(
            "VOLUME_TEMPLATE_PARAMETER_UNRESOLVED",
            "the rendered mount command retains its volume parameter token",
        )
    return rendered


def volume_device_poll_commands(volume_id: str) -> list[str]:
    """Compatibility helper returning only the rendered guest poll fragment."""

    rendered = volume_mount_commands(volume_id)
    return rendered[4:-4]


def network_receipt_observation_command(staging_path: str) -> str:
    """Build one read-only observation that never fails merely for absence."""
    if not staging_path.startswith("/var/lib/medzen-asr-eval/attempt-"):
        raise AsyncObservationRefusal(
            "PILOT_STAGING_PATH_DIFFERS",
            "network-receipt staging path is outside the attempt directory",
        )
    network = f"{staging_path}/output/network-probe.json"
    listener = f"{staging_path}/output/inbound-listener-ready"
    test_network = _command("/usr/bin/test", "-s", network)
    test_listener = _command("/usr/bin/test", "-s", listener)
    print_network_absent = _command(
        "/usr/bin/printf", "%s\\n", NETWORK_RECEIPT_ABSENT
    )
    print_listener_absent = _command(
        "/usr/bin/printf", "%s\\n", LISTENER_RECEIPT_ABSENT
    )
    print_ready = _command(
        "/usr/bin/printf", "%s\\n", NETWORK_AND_LISTENER_PRESENT
    )
    read_network = _command("/usr/bin/cat", network)
    return (
        "set -eu; "
        f"if {test_network}; then "
        f"if {test_listener}; then {print_ready}; "
        f"else {print_listener_absent}; fi; "
        f"{read_network}; "
        f"else {print_network_absent}; fi"
    )


def parse_network_receipt_observation(stdout: str) -> dict[str, Any]:
    lines = stdout.splitlines()
    if not lines:
        raise AsyncObservationRefusal(
            "NETWORK_RECEIPT_OBSERVATION_EMPTY",
            "network-receipt observation returned no marker",
        )
    marker = lines[0]
    if marker == NETWORK_RECEIPT_ABSENT:
        if len(lines) != 1:
            raise AsyncObservationRefusal(
                "NETWORK_RECEIPT_ABSENCE_PAYLOAD_DIFFERS",
                "absent receipt observation unexpectedly carried a payload",
            )
        return {
            "status": "WAIT_NETWORK_RECEIPT",
            "network_receipt": None,
            "listener_ready": False,
        }
    if marker not in {LISTENER_RECEIPT_ABSENT, NETWORK_AND_LISTENER_PRESENT}:
        raise AsyncObservationRefusal(
            "NETWORK_RECEIPT_OBSERVATION_MARKER_UNKNOWN",
            "network-receipt observation marker is unknown",
        )
    if len(lines) < 2:
        raise AsyncObservationRefusal(
            "NETWORK_PROBE_RECEIPT_MALFORMED",
            "present network receipt has no JSON payload",
        )
    try:
        receipt = json.loads("\n".join(lines[1:]))
    except Exception as exc:
        raise AsyncObservationRefusal(
            "NETWORK_PROBE_RECEIPT_MALFORMED",
            "present network receipt is not JSON",
        ) from exc
    if not isinstance(receipt, dict):
        raise AsyncObservationRefusal(
            "NETWORK_PROBE_RECEIPT_MALFORMED",
            "present network receipt is not an object",
        )
    if receipt.get("status") != "PASS_NETWORK_ISOLATION_PRE_TORCH":
        reason_code = receipt.get("reason_code")
        if not isinstance(reason_code, str) or not reason_code:
            reason_code = "NETWORK_PROBE_REASON_ABSENT"
        raise AsyncObservationRefusal(
            "NETWORK_PROBE_REFUSED",
            f"pre-torch private-endpoint probe did not pass; reason_code={reason_code}",
        )
    if receipt.get("torch_imported") is not False:
        raise AsyncObservationRefusal(
            "NETWORK_PROBE_REFUSED",
            "pre-torch private-endpoint probe imported torch",
        )
    return {
        "status": (
            "READY" if marker == NETWORK_AND_LISTENER_PRESENT
            else "WAIT_INBOUND_LISTENER"
        ),
        "network_receipt": receipt,
        "listener_ready": marker == NETWORK_AND_LISTENER_PRESENT,
    }


def pilot_pod_terminal_observation(pod: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(pod, dict):
        raise AsyncObservationRefusal(
            "PILOT_POD_RESPONSE_MALFORMED", "pilot pod response is not an object"
        )
    status = pod.get("status")
    if not isinstance(status, dict):
        raise AsyncObservationRefusal(
            "PILOT_POD_RESPONSE_MALFORMED", "pilot pod status is absent"
        )
    phase = status.get("phase")
    if not isinstance(phase, str):
        raise AsyncObservationRefusal(
            "PILOT_POD_RESPONSE_MALFORMED", "pilot pod phase is absent"
        )
    terminated: list[dict[str, Any]] = []
    for family in ("initContainerStatuses", "containerStatuses"):
        values = status.get(family, [])
        if not isinstance(values, list):
            raise AsyncObservationRefusal(
                "PILOT_POD_RESPONSE_MALFORMED",
                "pilot pod container-status family is malformed",
            )
        for value in values:
            if not isinstance(value, dict):
                continue
            state = value.get("state")
            body = state.get("terminated") if isinstance(state, dict) else None
            if isinstance(body, dict):
                terminated.append(
                    {
                        "name": value.get("name"),
                        "exit_code": body.get("exitCode"),
                        "reason": body.get("reason"),
                    }
                )
    return {
        "phase": phase,
        "terminal": phase in {"Failed", "Succeeded"} or bool(terminated),
        "reason": status.get("reason"),
        "terminated": terminated,
    }


def audit_async_observation_sites(root: Path) -> dict[str, Any]:
    """Guard every post-start observation in the remaining workload stages."""
    relative = Path("scripts/asr_base_model_pilot_live.py")
    source = (root / relative).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(relative))
    methods = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    pilot = methods.get("pilot_rows")
    aggregate = methods.get("aggregate_report")
    waiter = methods.get("_wait_pilot_network_receipts")
    inbound = methods.get("_cross_pod_refusal")
    if pilot is None or aggregate is None or waiter is None or inbound is None:
        raise AsyncObservationRefusal(
            "ASYNC_OBSERVATION_METHOD_ABSENT",
            "pilot observation method family is incomplete",
        )
    segment = ast.get_source_segment(source, pilot) or ""
    waiter_segment = ast.get_source_segment(source, waiter) or ""
    aggregate_segment = ast.get_source_segment(source, aggregate) or ""
    inbound_segment = ast.get_source_segment(source, inbound) or ""
    if segment.count("self._wait_pilot_network_receipts(") != 1:
        raise AsyncObservationRefusal(
            "NETWORK_RECEIPT_WAITER_BYPASSED",
            "pilot_rows must enter the shared network-receipt waiter exactly once",
        )
    forbidden = (
        'output/network-probe.json"),',
        'output/inbound-listener-ready"),',
    )
    if any(value in segment for value in forbidden):
        raise AsyncObservationRefusal(
            "ONE_SHOT_RECEIPT_READ_PRESENT",
            "pilot_rows still contains a direct network/listener receipt read",
        )
    waiter_requirements = (
        "PILOT_RECEIPT_TIMEOUT_SECONDS",
        "PILOT_RECEIPT_POLL_INTERVAL_SECONDS",
        "PILOT_RECEIPT_STABLE_OBSERVATIONS",
        "pilot_pod_terminal_observation",
        "network_receipt_observation_command",
        "NETWORK_PROBE_RECEIPT_TIMEOUT",
        "PILOT_POD_TERMINAL_BEFORE_NETWORK_RECEIPT",
    )
    if any(value not in waiter_segment for value in waiter_requirements):
        raise AsyncObservationRefusal(
            "NETWORK_RECEIPT_WAITER_CONTRACT_INCOMPLETE",
            "network-receipt waiter lacks a required bounded behavior",
        )
    if "self._wait_stage_pod_terminal(" not in inbound_segment:
        raise AsyncObservationRefusal(
            "INBOUND_CONTROL_TERMINAL_WAIT_ABSENT",
            "inbound control is not synchronized by the shared Pod terminal poll",
        )
    if "self._wait_pilot_job_complete(" not in segment:
        raise AsyncObservationRefusal(
            "PILOT_JOB_TERMINAL_WAIT_ABSENT",
            "pilot job is not synchronized by the shared Job completion poll",
        )
    if "output/aggregate.json" not in segment or "PASS_PILOT_ROWS" not in segment:
        raise AsyncObservationRefusal(
            "AGGREGATE_HANDOFF_CONTRACT_ABSENT",
            "pilot_rows does not verify the aggregate before its PASS handoff",
        )
    if "get_command_invocation" not in aggregate_segment or "time.monotonic() + 120" not in aggregate_segment:
        raise AsyncObservationRefusal(
            "AGGREGATE_READ_POLL_ABSENT",
            "aggregate_report is not protected by its bounded SSM poll",
        )
    sites = [
        {
            "site": "pilot_pod_identity_and_ip",
            "contract": "bounded discovery loop; exact one-pod identity; terminal state checked by receipt waiter",
        },
        {
            "site": "network_probe_receipt",
            "contract": "300-second bound; 10-second interval; two identical present observations; absence only is retryable",
        },
        {
            "site": "inbound_listener_receipt",
            "contract": "same shared receipt poll; listener absence is readiness-only while the pod is non-terminal",
        },
        {
            "site": "cross_pod_inbound_control",
            "contract": "shared Pod terminal poll; nonterminal observation required; 90-second hard bound",
        },
        {
            "site": "pilot_job_completion",
            "contract": "shared Job object poll; active observation before complete; 9000-second hard bound",
        },
        {
            "site": "aggregate_receipt_presence",
            "contract": "read only after job completion; test+hash required before PASS_PILOT_ROWS",
        },
        {
            "site": "aggregate_receipt_content",
            "contract": "depends on PASS_PILOT_ROWS; bounded 120-second SSM invocation poll; schema and completeness fail closed",
        },
    ]
    return {
        "status": "PASS_ALL_POST_START_OBSERVATIONS_BOUNDED",
        "audited_file": str(relative),
        "audited_methods": [
            "pilot_rows",
            "_wait_pilot_network_receipts",
            "aggregate_report",
            "_cross_pod_refusal",
        ],
        "site_count": len(sites),
        "sites": sites,
        "direct_one_shot_network_or_listener_reads": 0,
        "absence_is_only_retryable_receipt_state": True,
        "malformed_drift_or_terminal_state_retried": False,
    }


def audit_remote_ssm_observation_sites(root: Path) -> dict[str, Any]:
    """Enumerate every SSM crossing and reject asynchronous one-shot gates."""

    relative = Path("scripts/asr_base_model_pilot_live.py")
    source = (root / relative).read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(relative))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]
    observed: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        kind = None
        if node.func.attr == "_ssm":
            kind = "_ssm"
        elif node.func.attr == "send_command":
            kind = "send_command"
        if kind is None:
            continue
        owners = [
            item
            for item in functions
            if item.lineno <= node.lineno <= (item.end_lineno or item.lineno)
        ]
        owner = max(owners, key=lambda item: item.lineno).name if owners else "<module>"
        observed.append((owner, kind))

    expected = Counter(
        {
            ("_ssm", "send_command"): 1,
            ("gpu_and_sampler_gate", "_ssm"): 1,
            ("node_local_input_stage", "_ssm"): 1,
            ("capture_network_receipt", "_ssm"): 1,
            ("_wait_pilot_network_receipts", "_ssm"): 1,
            ("pilot_rows", "_ssm"): 2,
            ("aggregate_report", "send_command"): 1,
            ("cleanup_and_expiry", "_ssm"): 1,
        }
    )
    if Counter(observed) != expected:
        raise AsyncObservationRefusal(
            "REMOTE_SSM_SITE_INVENTORY_DIFFERS",
            "the executor SSM call-site inventory changed without an audit decision",
        )
    gpu = next(
        node
        for node in functions
        if node.name == "gpu_and_sampler_gate"
    )
    gpu_source = ast.get_source_segment(source, gpu) or ""
    required_mount_contract = (
        "self._wait_volume_attachment(",
        "volume_mount_command_template()",
        "volume_mount_commands(volume)",
        "VOLUME_DEVICE_TIMEOUT",
    )
    if any(value not in gpu_source for value in required_mount_contract):
        raise AsyncObservationRefusal(
            "REMOTE_VOLUME_WAIT_CONTRACT_INCOMPLETE",
            "the volume mount path lacks its EC2 and guest-device bounded waits",
        )
    if 'device=$(/usr/bin/readlink -f' in gpu_source:
        raise AsyncObservationRefusal(
            "REMOTE_DEVICE_ONE_SHOT_PRESENT",
            "the historical one-shot EBS device observation is still present",
        )

    sites = [
        {
            "site": "ssm_invocation_completion",
            "owner": "_ssm",
            "disposition": "BOUNDED_CONTROLLER_POLL",
        },
        {
            "site": "volume_device_appearance_and_mount",
            "owner": "gpu_and_sampler_gate",
            "disposition": "EC2_STABLE_WAIT_PLUS_BOUNDED_REMOTE_POLL",
        },
        {
            "site": "node_local_input_transfer_and_verification",
            "owner": "node_local_input_stage",
            "disposition": "BOUNDED_TRANSFER_RETRIES_THEN_SYNCHRONOUS_POSTCONDITIONS",
        },
        {
            "site": "network_receipt_refusal_diagnostic",
            "owner": "capture_network_receipt",
            "disposition": "DIAGNOSTIC_ABSENCE_MARKER_NOT_A_SUCCESS_GATE",
        },
        {
            "site": "network_and_listener_receipt_readiness",
            "owner": "_wait_pilot_network_receipts",
            "disposition": "BOUNDED_STABLE_CONTROLLER_POLL",
        },
        {
            "site": "network_release_creation",
            "owner": "pilot_rows",
            "disposition": "MUTATION_WITHOUT_ASYNC_OBSERVATION",
        },
        {
            "site": "aggregate_presence_and_hash",
            "owner": "pilot_rows",
            "disposition": "SYNCHRONOUS_POSTCONDITION_AFTER_TERMINAL_JOB",
        },
        {
            "site": "aggregate_content_read",
            "owner": "aggregate_report",
            "disposition": "BOUNDED_SSM_COMPLETION_THEN_FAIL_CLOSED_SCHEMA_CHECK",
        },
        {
            "site": "cleanup_unmount",
            "owner": "cleanup_and_expiry",
            "disposition": "BEST_EFFORT_STATUS_KEYED_CLEANUP_NOT_A_PASS_GATE",
        },
    ]
    return {
        "status": "PASS_REMOTE_SSM_OBSERVATION_AUDIT",
        "audited_file": str(relative),
        "site_count": len(sites),
        "sites": sites,
        "asynchronous_one_shot_success_gates": 0,
        "unclassified_ssm_call_sites": 0,
    }
