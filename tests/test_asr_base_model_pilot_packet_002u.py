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
    ATTEMPT_22_EXECUTOR_MODULE_PATHS,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002U.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002U-attempt-22.md"
RISK = ROOT / "platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003.json"
QUALIFICATION = ROOT / "platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-007.json"
SCAN = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-LOCAL-SCAN-2026-004.sarif.json"
SCAN_SUBJECT = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-LOCAL-SCAN-SUBJECT-2026-005.json"
SCOUT_PREFLIGHT = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-SCOUT-PREFLIGHT-2026-002.json"
RESOURCE_POLICY = ROOT / "platform/manifests/ASR-BASE-MODEL-LOCAL-RESOURCE-POLICY-2026-002.json"
RESOURCE_QUALIFICATION = ROOT / "platform/evidence/ASR-BASE-MODEL-LOCAL-RESOURCE-QUALIFICATION-2026-005.json"
GPU_STORAGE_QUALIFICATION = ROOT / "platform/evidence/ASR-EVAL-RUNTIME-GPU-EPHEMERAL-STORAGE-QUALIFICATION-2026-002.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-017.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002U-COLD/cold-rehearsal.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt22_is_one_fresh_nontransferable_request() -> None:
    assert bound()["attempts"] == {
        "attempts_1_through_21_reuse_permitted": False,
        "authorized_numbers": [22],
        "cost_ceiling_usd": 10,
        "maximum": 1,
        "maximum_gpu_nodes": 1,
        "non_transferable": True,
        "seconds_each": 10800,
    }
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002U only" in text
    assert sha(RISK) in text


def test_all_attempt22_modules_are_bound_to_the_exact_source_commit() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(ATTEMPT_22_EXECUTOR_MODULE_PATHS)
    assert len(value["executor_modules"]) == len(ATTEMPT_22_EXECUTOR_MODULE_PATHS) == 31
    assert "services/asr-eval-runtime/medzen_asr_eval/network_probe.py" in value[
        "executor_modules"
    ]
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_corrected_image_scan_qualification_and_risk_are_exact() -> None:
    value = bound()
    assert value["image"] == {
        "attestation_digest": "sha256:8d96d7c4b5b6f4a3c1677dc93301e2829afd0923b20eb269272b5e19dbf57e23",
        "config_digest": "sha256:2938427027f22b10f9dc5c89b3305b5689ea5c44b088839b85583e1575feeda3",
        "image_context_changed": True,
        "linux_amd64_digest": "sha256:4d1ccde955f5ae074ed6470d7edb6d74f9d49cc6a6f44f9f0a2b7397a0cd3841",
        "local_scan_sha256": sha(SCAN),
        "local_scan_subject_sha256": sha(SCAN_SUBJECT),
        "local_tag": "medzen-asr-eval-runtime:pilot-7efa6e8c",
        "oci_index_digest": "sha256:f14fe88a7ebb2c68bf2ed772ad2ce8913c1fa8117b2da5305af55298f1d15505",
        "publication_required": True,
        "repository": "medzen-asr-eval-runtime",
        "source_commit": "7efa6e8c4be378e754e9edb8b64151aa89c0a366",
        "spdx_attestation_digest": "sha256:3da28c95deef0aa97466904c2222481c4698b1fc53c0b539b9447d1f9d90af12",
        "tag": "pilot-7efa6e8c",
    }
    assert value["qualification"]["sha256"] == sha(QUALIFICATION)
    assert value["risk_acceptance_sha256"] == sha(RISK)
    preflight = json.loads(SCOUT_PREFLIGHT.read_bytes())
    assert value["scout_real_execution_preflight"]["sha256"] == sha(
        SCOUT_PREFLIGHT
    )
    assert preflight["scan"]["critical"] == 0
    assert preflight["scan"]["high"] == 4
    assert preflight["execution_environment"]["authentication_mode"] == (
        "DOCKER_CREDENTIAL_STORE"
    )


def test_network_convergence_and_local_resource_gates_are_bound() -> None:
    value = bound()
    convergence = value["network_policy_convergence"]
    assert convergence["positive_probe_retry_interval_seconds"] == 5
    assert convergence["positive_probe_hard_timeout_seconds"] == 120
    assert convergence[
        "full_positive_and_negative_checks_start_only_after_convergence"
    ] is True
    assert convergence["torch_imported_before_pass_permitted"] is False
    assert value["local_resource_policy"]["sha256"] == sha(RESOURCE_POLICY)
    assert value["local_resource_qualification"]["sha256"] == sha(
        RESOURCE_QUALIFICATION
    )
    assert value["local_resource_qualification"]["measured_available_bytes"] >= (
        40 * 1024**3
    )
    assert value["local_resource_qualification"]["scout_authentication_mode"] == (
        "DOCKER_CREDENTIAL_STORE"
    )
    assert value["gpu_storage_policy"]["capacity_qualification"]["sha256"] == sha(
        GPU_STORAGE_QUALIFICATION
    )
    assert value["gpu_storage_policy"]["image"] == {
        "oci_index_digest": value["image"]["oci_index_digest"],
        "linux_amd64_digest": value["image"]["linux_amd64_digest"],
    }


def test_attempt22_plan_includes_new_image_publication_and_exact_prohibitions() -> None:
    value = bound()
    result = validate_plan(exact_plan(value, 22), value, 22)
    assert result == {
        "status": "PASS_EXACT_EXECUTION_PLAN",
        "attempt": 22,
        "permanent_create_only": 3,
        "permanent_bounded_update": 0,
        "temporary_create_then_delete": 19,
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
    ):
        assert boundary in text


def test_attempt22_rehearsal_uses_the_new_image_publication_path(tmp_path: Path) -> None:
    from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
    from scripts.asr_base_model_pilot_runner import AttemptContext
    from pipeline.asr_base_model_pilot_receipts import ReceiptStore

    value = bound()
    operations, state = build_rehearsal_operations(value)
    context = AttemptContext(
        attempt=22,
        bindings=value,
        receipts=ReceiptStore(
            tmp_path / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=tmp_path / "work",
    )
    payload = operations.image_publication_and_scan(context)
    assert payload["status"] == "PASS_IMAGE_PUBLICATION_AND_SCAN"
    assert payload["publication"]["status"] == "PASS_EXACT_MULTIPART_ECR_PUBLICATION"
    assert payload["ecr_basic"] == {
        "status": "PASS_ECR_BASIC_OS_GATE",
        "critical": 0,
        "high": 0,
        "high_tuples": [],
    }
    assert payload["security_gate"]["status"] == (
        "PASS_DIGEST_VERIFIED_DUAL_SCAN_GATE"
    )
    assert state.image_published is True


def test_cost_registry_017_is_conservative_and_request_fits() -> None:
    value = bound()
    assert value["cost_registry"]["sha256"] == sha(COST)
    summary = json.loads(COST.read_bytes())["guardrail_summary"]
    committed = Decimal(str(summary["recognized_committed_guardrail_usd"]))
    reserved = Decimal(str(summary["active_reservations_usd"]))
    ceiling = Decimal(str(summary["aggregate_ceiling_usd"]))
    assert ceiling - committed - reserved == Decimal("115.5713935784")
    assert ceiling - committed - reserved - Decimal("10") == Decimal(
        "105.5713935784"
    )


def test_write_once_attempt21_history_is_unchanged_and_bound() -> None:
    value = bound()["write_once_history"]
    expected = {
        "attempt_21_packet": "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002T-attempt-21.md",
        "attempt_21_authorization": "platform/decisions/ASR-BASE-MODEL-AWS-AUTH-2026-002T.json",
        "attempt_21_dry_validation": "platform/evidence/ASR-BASE-MODEL-DEADLINE-IDENTITY-DRY-RUN-2026-002T.json",
        "attempt_21_refusal": "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002T-ATTEMPT-21-ENDPOINT-ALLOWLIST-REFUSAL.json",
        "attempt_21_diagnosis_correction": "platform/evidence/ASR-BASE-MODEL-ATTEMPT-21-DIAGNOSIS-CORRECTION-2026-001.json",
        "attempt_21_cost_reconciliation": "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-007.json",
        "attempt_21_cost_registry": "platform/finance/COST-REGISTRY-2026-017.json",
    }
    for key, relative in expected.items():
        assert value[key]["sha256"] == sha(ROOT / relative)


def test_receipt_last_rehearsal_covers_four_direct_probe_paths() -> None:
    value = json.loads(COLD.read_bytes())
    assert value["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert value["bindings_source"]["sha256"] == sha(BINDINGS)
    assert value["executor_module_integrity"]["module_count"] == 31
    probe = value["network_probe_convergence"]
    assert set(probe["scenarios"]) == {
        "policy_already_converged_pass",
        "policy_propagation_delay_then_pass",
        "never_converges_timeout",
        "post_convergence_negative_check_failure",
    }
    assert probe["scenarios"]["policy_already_converged_pass"]["status"] == (
        "PASS_NETWORK_ISOLATION_PRE_TORCH"
    )
    assert probe["scenarios"]["policy_propagation_delay_then_pass"][
        "status"
    ] == "PASS_NETWORK_ISOLATION_PRE_TORCH"
    assert probe["scenarios"]["never_converges_timeout"]["reason_code"] == (
        "POSITIVE_NETWORK_CONVERGENCE_TIMEOUT"
    )
    assert probe["scenarios"]["post_convergence_negative_check_failure"][
        "reason_code"
    ] == "PROHIBITED_NETWORK_DESTINATION_ACCEPTED"
    assert all(item["zero_state"] is True for item in value["scenarios"].values())
