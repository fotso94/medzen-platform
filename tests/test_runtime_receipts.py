from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.runtime_receipts import ReceiptRefusal, ReceiptStore


POLICY = ROOT / "platform/runtime-receipt-policy-v1.yaml"


def test_stage_receipts_are_exclusive_durable_and_policy_bound(tmp_path):
    store = ReceiptStore(tmp_path, policy_path=POLICY)
    first = store.persist(
        "local_bindings", "PASS", {"binding": "exact"},
        dependencies=(), recorded_utc="2026-08-08T20:00:00Z",
    )
    raw = store.path("local_bindings").read_bytes()
    assert first["receipt_sha256"] == hashlib.sha256(raw).hexdigest()
    assert json.loads(raw)["policy"]["sha256"] == hashlib.sha256(
        POLICY.read_bytes()
    ).hexdigest()
    assert store.path("local_bindings").stat().st_mode & 0o777 == 0o600
    with pytest.raises(ReceiptRefusal, match="overwrite"):
        store.persist("local_bindings", "PASS", {"binding": "changed"}, dependencies=())


def test_missing_malformed_unknown_and_out_of_order_receipts_fail_closed(tmp_path):
    store = ReceiptStore(tmp_path, policy_path=POLICY)
    with pytest.raises(ReceiptRefusal, match="absent or malformed"):
        store.require_pass("sampler_self_test")
    with pytest.raises(ReceiptRefusal, match="unknown runtime receipt status"):
        store.persist("local_bindings", "MAYBE", {}, dependencies=())
    with pytest.raises(ReceiptRefusal, match="unknown runtime receipt stage"):
        store.persist("invented", "PASS", {}, dependencies=())
    with pytest.raises(ReceiptRefusal, match="absent or malformed"):
        store.persist("transcription", "PASS", {})


def test_memory_incomplete_never_mutates_successful_transcription(tmp_path):
    store = ReceiptStore(tmp_path, policy_path=POLICY)
    for stage in (
        "local_bindings", "deadline", "dra_stable_readiness", "sampler_self_test"
    ):
        store.persist(stage, "PASS", {"stage": stage})
    transcription = store.persist(
        "transcription", "PASS", {"response_sha256": "a" * 64}
    )
    before = store.path("transcription").read_bytes()
    memory = store.persist(
        "gpu_memory_measurement",
        "INCOMPLETE_MEASUREMENT",
        {"transcription_receipt_preserved": True},
    )
    summary = store.persist(
        "proof_summary",
        "INCOMPLETE_MEASUREMENT",
        {
            "transcription_status": "PASS",
            "transcription_receipt_sha256": transcription["receipt_sha256"],
            "memory_receipt_sha256": memory["receipt_sha256"],
            "transcription_voided": False,
        },
    )
    assert store.path("transcription").read_bytes() == before
    assert store.require_pass("transcription")["status"] == "PASS"
    assert summary["status"] == "INCOMPLETE_MEASUREMENT"
