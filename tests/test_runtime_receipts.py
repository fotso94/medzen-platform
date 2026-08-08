from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.runtime_receipts import ReceiptRefusal, ReceiptStore
from pipeline.runtime_receipts_v2 import (
    ReceiptRefusal as ReceiptRefusalV2,
    ReceiptStore as ReceiptStoreV2,
)


POLICY = ROOT / "platform/runtime-receipt-policy-v1.yaml"
POLICY_V2 = ROOT / "platform/runtime-receipt-policy-v2.yaml"


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


def test_v2_allows_bounded_raw_output_only_before_artifacts(tmp_path):
    facts = {
        "model_artifact_present_on_node": False,
        "audio_artifact_present_on_node": False,
        "model_or_audio_workload_applied": False,
    }
    store = ReceiptStoreV2(tmp_path, policy_path=POLICY_V2)
    receipt = store.persist(
        "sampler_self_test",
        "REFUSED",
        {
            "pre_artifact_facts": facts,
            "command_path": "scripts/probe.sh",
            "command_sha256": "a" * 64,
            "raw_stdout": "safe\n",
            "raw_stderr": "",
        },
        dependencies=(),
    )
    assert receipt["policy"]["path"] == "platform/runtime-receipt-policy-v2.yaml"
    assert store.load("sampler_self_test")["payload"]["raw_stdout"] == "safe\n"


def test_raw_output_fails_closed_for_v1_post_artifact_and_missing_facts(tmp_path):
    with pytest.raises(ReceiptRefusalV2, match="policy v2"):
        ReceiptStoreV2(tmp_path / "v1", policy_path=POLICY).persist(
            "sampler_self_test", "REFUSED", {"raw_stdout": "safe"}, dependencies=()
        )
    v2 = ReceiptStoreV2(tmp_path / "v2", policy_path=POLICY_V2)
    with pytest.raises(ReceiptRefusalV2, match="facts are absent"):
        v2.persist("sampler_self_test", "REFUSED", {"raw_stdout": "safe"}, dependencies=())
    with pytest.raises(ReceiptRefusalV2, match="prohibited after"):
        v2.persist(
            "transcription",
            "REFUSED",
            {
                "pre_artifact_facts": {
                    "model_artifact_present_on_node": False,
                    "audio_artifact_present_on_node": False,
                    "model_or_audio_workload_applied": False,
                },
                "command_path": "scripts/probe.sh",
                "command_sha256": "a" * 64,
                "raw_stdout": "safe",
            },
            dependencies=(),
        )


def test_v2_raw_output_requires_exact_command_binding(tmp_path):
    store = ReceiptStoreV2(tmp_path, policy_path=POLICY_V2)
    with pytest.raises(ReceiptRefusalV2, match="command path is absent"):
        store.persist(
            "sampler_self_test",
            "REFUSED",
            {
                "pre_artifact_facts": {
                    "model_artifact_present_on_node": False,
                    "audio_artifact_present_on_node": False,
                    "model_or_audio_workload_applied": False,
                },
                "raw_stderr": "failure",
            },
            dependencies=(),
        )
