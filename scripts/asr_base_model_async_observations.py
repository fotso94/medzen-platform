#!/usr/bin/env python3
"""Fail-closed contracts for asynchronous pilot observations.

The live executor owns the polling loop.  This module owns the immutable
markers, validation rules and the source audit that prevents a future direct
one-shot read from bypassing that loop.
"""

from __future__ import annotations

import ast
import json
import shlex
from pathlib import Path
from typing import Any


PILOT_RECEIPT_POLL_INTERVAL_SECONDS = 10
PILOT_RECEIPT_TIMEOUT_SECONDS = 300
PILOT_RECEIPT_STABLE_OBSERVATIONS = 2

NETWORK_RECEIPT_ABSENT = "MEDZEN_NETWORK_PROBE_RECEIPT_ABSENT"
LISTENER_RECEIPT_ABSENT = "MEDZEN_INBOUND_LISTENER_RECEIPT_ABSENT"
NETWORK_AND_LISTENER_PRESENT = "MEDZEN_NETWORK_AND_LISTENER_PRESENT"


class AsyncObservationRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def _command(*values: str) -> str:
    return shlex.join(values)


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
    if "--for=jsonpath={.status.phase}=Succeeded" not in inbound_segment:
        raise AsyncObservationRefusal(
            "INBOUND_CONTROL_TERMINAL_WAIT_ABSENT",
            "inbound control is not synchronized by a terminal wait",
        )
    if "--for=condition=complete" not in segment:
        raise AsyncObservationRefusal(
            "PILOT_JOB_TERMINAL_WAIT_ABSENT",
            "pilot job is not synchronized by a completion wait",
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
            "contract": "kubectl terminal Succeeded wait with 60-second server timeout and 90-second client timeout",
        },
        {
            "site": "pilot_job_completion",
            "contract": "kubectl condition=complete wait with 9000-second server timeout and 9060-second client timeout",
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
