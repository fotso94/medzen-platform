from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import ReceiptStore
from scripts.asr_base_model_node_staging import download_file
from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal
from scripts.asr_eval_digest_rescan import DigestRescanRefusal
from scripts.asr_idempotent_read_retry import (
    IdempotentReadRetrier,
    ReadRetryConfigurationError,
    RetryPolicy,
    TransientReadFault,
    TransientReadRetryExhausted,
    classify_external_read_failure,
    classify_transport_exception,
)


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002Q.json"


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def retrier(clock: Clock, *, cap: float = 30.0) -> IdempotentReadRetrier:
    return IdempotentReadRetrier(
        RetryPolicy(hard_cap_seconds=cap),
        sleeper=clock.sleep,
        monotonic=clock.monotonic,
    )


def test_reset_then_success_retries_exactly_once() -> None:
    clock = Clock()
    calls = 0

    def read() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TransientReadFault("ECR_PULL_BACK", "CONNECTION_RESET")
        return "ok"

    value, audit = retrier(clock).run("ECR_PULL_BACK", "exact-child", read)
    assert value == "ok"
    assert calls == 2
    assert audit["attempts"] == 2
    assert audit["transient_events"] == [
        {"attempt": 1, "classification": "CONNECTION_RESET", "retryable": True}
    ]
    assert clock.value == 1.0


def test_persistent_transient_refuses_after_three_bounded_attempts() -> None:
    clock = Clock()
    calls = 0

    def read() -> None:
        nonlocal calls
        calls += 1
        raise TransientReadFault("S3_READ", "TIMEOUT")

    with pytest.raises(TransientReadRetryExhausted) as refused:
        retrier(clock).run("S3_READ", "versioned-object", read)
    assert calls == 3
    assert clock.value == 3.0
    assert refused.value.audit["status"] == "ATTEMPTS_EXHAUSTED"
    assert refused.value.audit["attempts"] == 3


def test_verification_failures_are_never_retried() -> None:
    clock = Clock()
    calls = 0

    def verify() -> None:
        nonlocal calls
        calls += 1
        raise DigestRescanRefusal("SCOUT_FINDINGS_DIFFER", "finding drift")

    with pytest.raises(DigestRescanRefusal, match="finding drift"):
        retrier(clock).run("SCOUT_DATABASE_READ", "scout-db", verify)
    assert calls == 1
    assert clock.value == 0.0


def test_only_allowlisted_reads_can_enter_retry_boundary() -> None:
    clock = Clock()
    with pytest.raises(ReadRetryConfigurationError):
        retrier(clock).run("S3_WRITE", "forbidden", lambda: None)


@pytest.mark.parametrize(
    ("exception", "category"),
    [
        (ConnectionResetError(54, "reset"), "CONNECTION_RESET"),
        (TimeoutError("late"), "TIMEOUT"),
        (socket.gaierror(-2, "dns"), "DNS_BLIP"),
    ],
)
def test_transport_exception_classification_is_typed(exception: Exception, category: str) -> None:
    fault = classify_transport_exception("ECR_PULL_BACK", exception)
    assert fault is not None
    assert fault.category == category


def test_scout_non_transport_failure_is_not_classified() -> None:
    assert (
        classify_external_read_failure(
            "SCOUT_DATABASE_READ",
            returncode=2,
            stdout=b"",
            stderr=b"four high findings differ",
        )
        is None
    )


def context(tmp_path: Path, bindings: dict) -> AttemptContext:
    return AttemptContext(
        attempt=18,
        bindings=bindings,
        receipts=ReceiptStore(
            tmp_path / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=tmp_path,
    )


def test_live_image_stage_retries_boundary_reset_then_passes(tmp_path: Path) -> None:
    bindings = json.loads(BINDINGS.read_bytes())
    operations, state = build_rehearsal_operations(
        bindings, injection="image_stream_reset_then_success"
    )
    payload = operations.image_publication_and_scan(context(tmp_path, bindings))
    assert payload["status"] == "PASS_IMAGE_PUBLICATION_AND_SCAN"
    assert payload["read_retry_audit"]["scan"]["attempts"] == 2
    assert state.monotonic_seconds == 1.0


def test_live_image_stage_persistent_reset_refuses_with_typed_audit(tmp_path: Path) -> None:
    bindings = json.loads(BINDINGS.read_bytes())
    operations, state = build_rehearsal_operations(
        bindings, injection="image_stream_persistent_reset"
    )
    with pytest.raises(OperationRefusal) as refused:
        operations.image_publication_and_scan(context(tmp_path, bindings))
    assert refused.value.reason_code == "TRANSIENT_IDEMPOTENT_READ_RETRY_EXHAUSTED"
    assert '"attempts":3' in refused.value.detail
    assert state.monotonic_seconds == 3.0


def test_node_s3_download_retries_only_typed_curl_transport_codes() -> None:
    command = download_file("https://example.invalid/object", "/tmp/object")
    assert "attempt=1" in command
    assert '6) category=DNS_BLIP' in command
    assert '28) category=TIMEOUT' in command
    assert '56) category=CONNECTION_RESET' in command
    assert '*) exit "$code"' in command
    assert "attempts=3" in command
    assert "--max-time 300" in command
