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


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002N.json"
POLICY = ROOT / "platform/k8s/asr-eval/nvidia-dra-api-egress.yaml"
LOCKED_DRA = ROOT / "platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml"
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
