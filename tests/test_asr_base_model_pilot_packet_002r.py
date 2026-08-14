from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_integrity import (
    ATTEMPT_19_EXECUTOR_MODULE_PATHS,
)


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002R.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002R-attempt-19.md"
QUALIFICATION = ROOT / "platform/evidence/ASR-BASE-MODEL-IDEMPOTENT-READ-RETRY-QUALIFICATION-2026-001.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002R-COLD/cold-rehearsal.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-014.json"
RECONCILIATION = ROOT / "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-004.json"
ATTEMPT_18 = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002Q-ATTEMPT-18-IMAGE-STREAM-REFUSAL.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt19_is_one_fresh_nontransferable_request() -> None:
    assert bound()["attempts"] == {
        "attempts_1_through_18_reuse_permitted": False,
        "authorized_numbers": [19],
        "cost_ceiling_usd": 10,
        "maximum": 1,
        "maximum_gpu_nodes": 1,
        "non_transferable": True,
        "seconds_each": 10800,
    }
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002R only" in text


def test_all_attempt19_modules_are_exactly_bound() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(ATTEMPT_19_EXECUTOR_MODULE_PATHS)
    assert len(value["executor_modules"]) == len(ATTEMPT_19_EXECUTOR_MODULE_PATHS) == 28
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_retry_policy_is_typed_bounded_and_read_only() -> None:
    policy = bound()["idempotent_read_retry_policy"]
    assert policy == {
        "allowlisted_operations": ["ECR_PULL_BACK", "SCOUT_DATABASE_READ", "S3_READ"],
        "allowlisted_transient_classes": ["CONNECTION_RESET", "TIMEOUT", "DNS_BLIP"],
        "maximum_attempts": 3,
        "backoff_seconds": [1, 2],
        "image_scan_hard_cap_seconds": 7200,
        "local_s3_read_hard_cap_seconds": 3600,
        "node_s3_read_hard_cap_seconds": 903,
        "verification_failures_retryable": False,
        "writes_mutations_or_ambiguous_operations_retryable": False,
        "sanitized_bounded_diagnostics_required": True,
    }
    qualification = json.loads(QUALIFICATION.read_bytes())
    assert qualification["status"] == "PASS_TYPED_TRANSIENT_READ_RETRY_QUALIFICATION"
    assert qualification["behavioral_tests"]["passed"] == 12
    assert qualification["behavioral_tests"]["verification_failure_calls"] == 1
    assert bound()["idempotent_read_retry_qualification"]["sha256"] == sha(QUALIFICATION)


def test_attempt18_and_cost_history_are_write_once_and_bound() -> None:
    assert sha(ATTEMPT_18) == "567b1ecc81dc3018ed4a03b76245be971e8f268c663cfebffa810a80b968d855"
    value = bound()
    assert value["write_once_history"]["attempt_18_refusal"]["sha256"] == sha(ATTEMPT_18)
    assert value["write_once_history"]["attempt_18_cost_reconciliation"]["sha256"] == sha(RECONCILIATION)
    assert value["cost_registry"]["sha256"] == sha(COST)
    for path in (ATTEMPT_18, RECONCILIATION, COST, QUALIFICATION):
        assert sha(path) in PACKET.read_text()


def test_cost_registry_014_arithmetic_is_exact() -> None:
    summary = json.loads(COST.read_bytes())["guardrail_summary"]
    committed = Decimal(str(summary["recognized_committed_guardrail_usd"]))
    reserved = Decimal(str(summary["active_reservations_usd"]))
    ceiling = Decimal(str(summary["aggregate_ceiling_usd"]))
    assert committed + reserved == Decimal(str(summary["committed_plus_reserved_usd"]))
    assert ceiling - committed - reserved == Decimal(str(summary["guardrail_headroom_after_reservations_usd"]))
    assert ceiling - committed - Decimal("10") == Decimal("135.5713935784")


def test_receipt_last_rehearsal_proves_both_transport_paths() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert receipt["bindings_source"]["sha256"] == sha(BINDINGS)
    recovered = receipt["scenarios"]["image_stream_reset_then_success"]
    assert recovered["outcome"] == "PASS_PILOT"
    assert recovered["read_retry_audit"]["scan"]["attempts"] == 2
    assert recovered["read_retry_audit"]["scan"]["transient_events"] == [
        {
            "attempt": 1,
            "operation": "ECR_PULL_BACK",
            "classification": "CONNECTION_RESET",
            "retryable": True,
        }
    ]
    refused = receipt["scenarios"]["image_stream_persistent_reset"]
    assert refused["outcome"] == "BLOCKED_IMAGE_SCAN"
    assert refused["failure_reason_code"] == "TRANSIENT_IDEMPOTENT_READ_RETRY_EXHAUSTED"
    assert '"attempts":3' in refused["failure_safe_error_text"]
    assert receipt["scenarios"]["security_wrong_digest"]["failure_reason_code"] == "ECR_RESCAN_CHILD_BINDING_DIFFERS"
    assert receipt["scenarios"]["security_extra_finding"]["failure_reason_code"] == "SCOUT_FINDINGS_DIFFER"
    assert all(item["zero_state"] is True for item in receipt["scenarios"].values())
