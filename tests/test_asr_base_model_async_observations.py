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
    network_receipt_observation_command,
    parse_network_receipt_observation,
    pilot_pod_terminal_observation,
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
            f'{NETWORK_AND_LISTENER_PRESENT}\n{{"status":"FAIL","torch_imported":false}}\n'
        )
    assert refused.value.reason_code == "NETWORK_PROBE_REFUSED"


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


def test_pod_shape_and_entire_async_site_audit_fail_closed() -> None:
    with pytest.raises(AsyncObservationRefusal) as captured:
        pilot_pod_terminal_observation({"status": {}})
    assert captured.value.reason_code == "PILOT_POD_RESPONSE_MALFORMED"
    result = audit_async_observation_sites(ROOT)
    assert result["status"] == "PASS_ALL_POST_START_OBSERVATIONS_BOUNDED"
    assert result["site_count"] == 7
    assert result["direct_one_shot_network_or_listener_reads"] == 0
    assert result["malformed_drift_or_terminal_state_retried"] is False
