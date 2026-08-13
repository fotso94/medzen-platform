from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
from scripts.asr_base_model_pilot_runner import (
    AttemptContext,
    OperationRefusal,
    ReceiptStore,
)
from scripts.asr_base_model_pilot_integrity import validate_executor_module_bindings
from scripts.asr_base_model_boundary_contracts import audit_bounded_helper_calls


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002N.json"
POLICY = ROOT / "platform/k8s/asr-eval/nvidia-dra-api-egress.yaml"
LOCKED_DRA = ROOT / "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002N-attempt-15.md"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002N-COLD/cold-rehearsal.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-010.json"
QUALIFICATION = ROOT / "platform/evidence/ASR-BASE-MODEL-LOCAL-RESOURCE-QUALIFICATION-2026-003.json"
B6_CLOSURE_DRA_SHA256 = "0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_b6_proven_dra_manifest_is_byte_identical_not_rewritten() -> None:
    assert sha(LOCKED_DRA) == B6_CLOSURE_DRA_SHA256
    assert bound()["dra_deployment"]["locked_manifest_sha256"] == B6_CLOSURE_DRA_SHA256
    assert bound()["dra_deployment"]["manifest_byte_drift_from_b6_closure"] is False


def test_dra_network_policy_allows_only_exact_cluster_api() -> None:
    docs = list(yaml.safe_load_all(POLICY.read_text(encoding="utf-8")))
    assert [doc["kind"] for doc in docs] == ["Namespace", "NetworkPolicy"]
    namespace, policy = docs
    assert namespace["apiVersion"] == "v1"
    assert namespace["metadata"]["name"] == "nvidia-dra-driver"
    assert policy["metadata"]["namespace"] == "nvidia-dra-driver"
    assert policy["spec"]["podSelector"]["matchLabels"] == {
        "dra-driver-nvidia-gpu-component": "kubelet-plugin"
    }
    assert policy["spec"]["policyTypes"] == ["Egress"]
    assert policy["spec"]["egress"] == [{
        "to": [{"ipBlock": {"cidr": "10.100.0.1/32"}}],
        "ports": [{"protocol": "TCP", "port": 443}],
    }]
    assert bound()["dra_network_policy"]["sha256"] == sha(POLICY)


def test_attempt_fifteen_plan_binds_the_dra_policy_as_temporary() -> None:
    from scripts.asr_base_model_pilot_plan import exact_plan

    plan = exact_plan(bound(), 15)
    assert plan["permanent_create_only"] == []
    assert (
        "kubernetes:networkpolicy/nvidia-dra-driver/medzen-dra-kubernetes-api-egress"
        in plan["temporary_create_then_delete"]
    )


def test_dra_refusal_persists_bounded_diagnostics_before_cleanup(tmp_path: Path) -> None:
    bindings = bound()
    operations, state = build_rehearsal_operations(bindings, injection="dra_not_ready")
    context = AttemptContext(
        attempt=15,
        bindings=bindings,
        receipts=ReceiptStore(
            tmp_path / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=tmp_path,
    )
    with pytest.raises(OperationRefusal) as captured:
        operations.gpu_and_sampler_gate(context)
    assert captured.value.reason_code == "DRA_STABLE_READINESS_TIMEOUT"
    diagnostic_path = tmp_path / "dra-refusal-diagnostics.json"
    diagnostic = json.loads(diagnostic_path.read_bytes())
    assert diagnostic["status"] == "CAPTURED_BEFORE_DRA_CLEANUP"
    assert diagnostic["contains_model_audio_transcript_prediction_credentials_or_phi"] is False
    assert diagnostic["daemonset"]["ready"] == 0
    assert diagnostic["pods"][0]["phase"] == "Running"
    assert diagnostic["pods"][0]["conditions"][0]["status"] == "False"
    assert diagnostic["events"][0]["reason"] == "Unhealthy"
    assert diagnostic["logs"][0]["status"] == "CAPTURED"
    assert state.dra_installed is True
    operations.cleanup_and_expiry(context)
    assert diagnostic_path.is_file()
    assert state.zero_state()


def test_b6_dra_receipt_audit_does_not_widen_timeout() -> None:
    audit = json.loads(
        (ROOT / "platform/evidence/ASR-BASE-MODEL-DRA-READINESS-AUDIT-2026-001.json").read_bytes()
    )
    assert audit["live_b6_receipt_inventory"]["requested_count"] == 25
    assert audit["live_b6_receipt_inventory"]["independent_dra_ready_receipts_found"] == 13
    assert audit["timing_summary_seconds"]["maximum"] == 61
    assert audit["decision"]["dra_wait_contract_seconds"] == 300
    assert audit["decision"]["contract_widened"] is False


def test_attempt_fourteen_retained_diagnostics_gap_is_named() -> None:
    diagnosis = json.loads(
        (ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002M-ATTEMPT-14-DRA-DIAGNOSIS-2026-001.json").read_bytes()
    )
    assert diagnosis["attempt_14_diagnostics_mining"]["readiness_probe_specifics_retained"] is False
    assert diagnosis["root_cause_assessment"]["classification"] == "EVIDENCE_BACKED_LEADING_CAUSE_NOT_LIVE_CONFIRMED"
    assert diagnosis["successor_correction"]["bounded_refusal_diagnostics_before_cleanup"] is True


def test_attempt_fifteen_is_one_review_only_nontransferable_request() -> None:
    text = PACKET.read_text(encoding="utf-8")
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002N only" in text
    assert bound()["attempts"] == {
        "authorized_numbers": [15],
        "maximum": 1,
        "seconds_each": 10800,
        "non_transferable": True,
        "maximum_gpu_nodes": 1,
        "cost_ceiling_usd": 10,
        "attempts_1_through_14_reuse_permitted": False,
    }


def test_all_executor_modules_and_helper_calls_are_bound() -> None:
    value = bound()
    for relative, expected in value["executor_modules"].items():
        completed = __import__("subprocess").run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        assert hashlib.sha256(completed.stdout).hexdigest() == expected
    audit = audit_bounded_helper_calls(ROOT)
    assert audit["status"] == "PASS_ALL_BOUNDED_HELPER_CALLS"
    # The write-once 002N receipt preserves the historical 48-site count;
    # successor source may add bound call sites without rewriting that record.
    assert audit["call_site_count"] >= 48
    assert audit["fake_may_bypass_validation"] is False


def test_resource_and_cost_records_are_conservatively_bound() -> None:
    bindings = bound()
    qualification = json.loads(QUALIFICATION.read_bytes())
    cost = json.loads(COST.read_bytes())
    assert bindings["local_resource_qualification"]["sha256"] == sha(QUALIFICATION)
    assert qualification["validation"]["measured_available_bytes"] >= 40 * 1024**3
    assert bindings["cost_registry"]["sha256"] == sha(COST)
    summary = cost["guardrail_summary"]
    assert summary["recognized_committed_guardrail_usd"] == 114.4286064216
    assert summary["active_reservations_usd"] == 0.0
    assert summary["attempt_14_actual_direct_compute_gross_usd"] is None


def test_receipt_last_rehearsal_covers_dra_refusal_diagnostics() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert receipt["bindings_source"]["sha256"] == sha(BINDINGS)
    assert receipt["executor_module_integrity"]["module_count"] == 20
    assert receipt["bounded_helper_contract_audit"]["call_site_count"] == 48
    dra = receipt["scenarios"]["dra_not_ready"]
    assert dra["outcome"] == "FAILED_CLOSED_EXECUTION"
    assert dra["failure_reason_code"] == "DRA_STABLE_READINESS_TIMEOUT"
    assert dra["dra_refusal_diagnostics"]["persisted_before_cleanup"] is True
    assert dra["zero_state"] is True
