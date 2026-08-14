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
    ATTEMPT_23_EXECUTOR_MODULE_PATHS,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002V.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002V-attempt-23.md"
RISK = ROOT / "platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-018.json"
DIAGNOSIS = ROOT / "platform/evidence/ASR-BASE-MODEL-ATTEMPT-22-DNS-DIAGNOSIS-CORRECTION-2026-001.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002V-COLD/cold-rehearsal.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt23_is_one_fresh_nontransferable_request() -> None:
    assert bound()["attempts"] == {
        "attempts_1_through_22_reuse_permitted": False,
        "authorized_numbers": [23],
        "cost_ceiling_usd": 10,
        "maximum": 1,
        "maximum_gpu_nodes": 1,
        "non_transferable": True,
        "seconds_each": 10800,
    }
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002V only" in text
    assert sha(RISK) in text


def test_all_attempt23_modules_are_bound_to_the_exact_source_commit() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(ATTEMPT_23_EXECUTOR_MODULE_PATHS)
    assert len(value["executor_modules"]) == len(ATTEMPT_23_EXECUTOR_MODULE_PATHS) == 32
    assert "scripts/asr_base_model_pilot_dns.py" in value["executor_modules"]
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_exact_existing_image_is_verify_only_and_risk_is_unchanged() -> None:
    value = bound()
    assert value["image"]["publication_required"] is False
    assert value["image"]["oci_index_digest"] == (
        "sha256:f14fe88a7ebb2c68bf2ed772ad2ce8913c1fa8117b2da5305af55298f1d15505"
    )
    assert value["image"]["linux_amd64_digest"] == (
        "sha256:4d1ccde955f5ae074ed6470d7edb6d74f9d49cc6a6f44f9f0a2b7397a0cd3841"
    )
    assert value["risk_acceptance_sha256"] == sha(RISK)


def test_dns_policy_and_controller_consistency_gate_are_exact() -> None:
    value = bound()["pod_dns_isolation"]
    assert value["dns_policy"] == "None"
    assert value["dns_config_nameservers"] == ["172.31.0.2"]
    assert value["cluster_dns_dependency"] is False
    assert value["dns_egress_cidr"] == "172.31.0.2/32"
    assert value["resolve_as_pod_gate"] == {
        "allowed_ip_source": "RENDERED_ASR_EVAL_PRIVATE_EGRESS_NETWORK_POLICY",
        "hostname_source": "NETWORK_BINDING_ALLOWED_TCP_443_HOSTS",
        "policy_selected_control_pod": True,
        "resolved_ips_recorded": True,
        "torch_imported_before_pass_permitted": False,
    }
    assert {
        "DNS_RESOLVER_UNREACHABLE",
        "DNS_EFFECTIVE_RESOLVER_DIFFERS",
        "DNS_RESOLVED_IP_OUTSIDE_ALLOWLIST",
        "DNS_CONTROL_POD_TIMEOUT",
    } == set(value["typed_refusal_codes"])


def test_attempt23_machine_plan_has_no_permanent_or_image_publication_mutation() -> None:
    value = bound()
    result = validate_plan(exact_plan(value, 23), value, 23)
    assert result == {
        "status": "PASS_EXACT_EXECUTION_PLAN",
        "attempt": 23,
        "permanent_create_only": 0,
        "permanent_bounded_update": 0,
        "temporary_create_then_delete": 18,
        "bounded_capacity_change": 1,
    }
    text = PACKET.read_text()
    for boundary in (
        "IAM",
        "KMS",
        "internet",
        "training",
        "serving",
        "approved/asr",
        "image rebuild or upload",
    ):
        assert boundary in text


def test_cost_registry_018_is_conservative_and_request_fits() -> None:
    value = bound()
    assert value["cost_registry"]["sha256"] == sha(COST)
    summary = json.loads(COST.read_bytes())["guardrail_summary"]
    committed = Decimal(str(summary["recognized_committed_guardrail_usd"]))
    reserved = Decimal(str(summary["active_reservations_usd"]))
    ceiling = Decimal(str(summary["aggregate_ceiling_usd"]))
    assert ceiling - committed - reserved == Decimal("105.5713935784")
    assert ceiling - committed - reserved - Decimal("10") == Decimal(
        "95.5713935784"
    )


def test_write_once_attempt22_history_and_diagnosis_are_bound() -> None:
    value = bound()["write_once_history"]
    expected = {
        "attempt_22_packet": "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002U-attempt-22.md",
        "attempt_22_authorization": "platform/decisions/ASR-BASE-MODEL-AWS-AUTH-2026-002U.json",
        "attempt_22_dry_validation": "platform/evidence/ASR-BASE-MODEL-DEADLINE-IDENTITY-DRY-RUN-2026-002U.json",
        "attempt_22_refusal": "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002U-ATTEMPT-22-DNS-ISOLATION-REFUSAL.json",
        "attempt_22_diagnosis_correction": "platform/evidence/ASR-BASE-MODEL-ATTEMPT-22-DNS-DIAGNOSIS-CORRECTION-2026-001.json",
        "attempt_22_cost_reconciliation": "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-008.json",
        "attempt_22_cost_registry": "platform/finance/COST-REGISTRY-2026-018.json",
    }
    for key, relative in expected.items():
        assert value[key]["sha256"] == sha(ROOT / relative)
    assert value["attempt_22_live_receipts"]["commit"] == (
        "5b33528a16da5ba0ec333b0de2508f3d2359e561"
    )
    assert value["attempt_22_diagnosis_correction"]["sha256"] == sha(DIAGNOSIS)


def test_receipt_last_rehearsal_covers_exact_dns_specs_and_refusals() -> None:
    value = json.loads(COLD.read_bytes())
    assert value["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert value["bindings_source"]["sha256"] == sha(BINDINGS)
    assert value["executor_module_integrity"]["module_count"] == 32
    dns = value["pod_dns_alignment"]
    assert dns["status"] == "PASS_REAL_POD_SPEC_DNS_ALIGNMENT_REHEARSAL"
    assert dns["resolver"] == "172.31.0.2"
    assert dns["clean_pass_dns_control"] == {
        "dnsPolicy": "None",
        "dnsConfig": {"nameservers": ["172.31.0.2"]},
    }
    assert dns["clean_pass_inbound_control"] == {
        "dnsPolicy": "None",
        "dnsConfig": {"nameservers": ["172.31.0.2"]},
    }
    assert dns["resolver_unreachable_reason_code"] == "DNS_RESOLVER_UNREACHABLE"
    assert dns["outside_allowlist_reason_code"] == (
        "DNS_RESOLVED_IP_OUTSIDE_ALLOWLIST"
    )
    assert all(item["zero_state"] is True for item in value["scenarios"].values())
