from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
from scripts.asr_base_model_pilot_live import (
    GPU_NODE_READY_POLL_INTERVAL_SECONDS,
    GPU_NODE_READY_STABLE_OBSERVATIONS,
    GPU_NODE_READY_TIMEOUT_SECONDS,
)
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002J.json"
FIXTURE = ROOT / "platform/evidence/ASR-BASE-MODEL-GPU-NODE-READINESS-FIXTURE-CAPTURE-2026-001.json"
FIXTURE_SHA256 = "34663d3ae7218f9423d15b4fa9aa11f4f4940022deaf87a409e6c0f4c91e5e56"


def _context(bindings: dict) -> AttemptContext:
    return AttemptContext(
        attempt=11,
        bindings=bindings,
        receipts=None,
        workdir=Path("/tmp/medzen-asr-readiness-unit"),
    )


def test_attempt_eleven_live_transition_capture_is_hash_bound_and_read_only() -> None:
    value = json.loads(FIXTURE.read_bytes())
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest() == FIXTURE_SHA256
    assert value["status"] == "PASS_READ_ONLY_LIVE_GPU_NODE_TRANSITION_CAPTURE"
    assert value["capture_method"]["aws_mutations"] == 0
    assert value["capture_method"]["gpu_started_for_capture"] is False
    assert value["attempt_11_empty_list"]["response"]["items"] == []
    assert value["attempt_11_node_objects"]["not_ready"]["node"]["status"]["conditions"][0]["status"] == "False"
    assert value["attempt_11_node_objects"]["ready"]["node"]["status"]["conditions"][0]["status"] == "True"
    assert value["causal_timeline"]["classification"] == "KUBELET_REGISTRATION_AND_READINESS_RACE_CONFIRMED"


def test_delayed_registration_then_two_ready_observations_passes() -> None:
    bindings = json.loads(BINDINGS.read_bytes())
    operations, state = build_rehearsal_operations(
        bindings, injection="gpu_node_delayed_ready"
    )
    result = operations._wait_gpu_node_ready(_context(bindings))
    assert result == {
        "status": "PASS_STABLE_GPU_NODE_READINESS",
        "node_name": "ip-172-31-20-53.eu-central-1.compute.internal",
        "observations": 4,
        "consecutive_ready_observations": 2,
        "required_consecutive_ready_observations": GPU_NODE_READY_STABLE_OBSERVATIONS,
        "poll_interval_seconds": GPU_NODE_READY_POLL_INTERVAL_SECONDS,
        "timeout_seconds": GPU_NODE_READY_TIMEOUT_SECONDS,
    }
    assert state.gpu_node_observation_sequence == [
        "CAPTURED_ATTEMPT_11_EMPTY",
        "CAPTURED_ATTEMPT_11_NOT_READY",
        "CAPTURED_ATTEMPT_11_READY",
        "CAPTURED_ATTEMPT_11_READY",
    ]


def test_never_ready_refuses_at_hard_timeout() -> None:
    bindings = json.loads(BINDINGS.read_bytes())
    operations, state = build_rehearsal_operations(
        bindings, injection="gpu_node_never_ready"
    )
    with pytest.raises(OperationRefusal) as captured:
        operations._wait_gpu_node_ready(_context(bindings))
    assert captured.value.reason_code == "GPU_NODE_READY_TIMEOUT"
    assert state.gpu_node_reads == GPU_NODE_READY_TIMEOUT_SECONDS // GPU_NODE_READY_POLL_INTERVAL_SECONDS
    assert state.monotonic_seconds == GPU_NODE_READY_TIMEOUT_SECONDS - GPU_NODE_READY_POLL_INTERVAL_SECONDS


def test_unstable_or_changed_node_resets_consecutive_count() -> None:
    bindings = json.loads(BINDINGS.read_bytes())
    operations, state = build_rehearsal_operations(bindings)
    fixture = json.loads(FIXTURE.read_bytes())
    ready = fixture["attempt_11_node_objects"]["ready"]["node"]
    empty = fixture["attempt_11_empty_list"]["response"]
    sequence = [
        {**empty, "items": [ready]},
        empty,
        {**empty, "items": [ready]},
        {**empty, "items": [ready]},
    ]

    def kubectl(*_: object, **__: object) -> dict:
        return sequence.pop(0)

    operations._kubectl_runner = kubectl
    result = operations._wait_gpu_node_ready(_context(bindings))
    assert result["observations"] == 4
    assert result["consecutive_ready_observations"] == 2
    assert state.monotonic_seconds == 30


def test_malformed_node_response_refuses_immediately() -> None:
    bindings = json.loads(BINDINGS.read_bytes())
    operations, _ = build_rehearsal_operations(bindings)
    operations._kubectl_runner = lambda *_args, **_kwargs: {"kind": "List"}
    with pytest.raises(OperationRefusal) as captured:
        operations._wait_gpu_node_ready(_context(bindings))
    assert captured.value.reason_code == "GPU_NODE_RESPONSE_MALFORMED"
