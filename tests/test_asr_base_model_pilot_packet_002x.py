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

from scripts.asr_base_model_async_observations import (
    volume_mount_command_template,
    volume_mount_commands,
)
from scripts.asr_base_model_pilot_integrity import (
    ATTEMPT_25_EXECUTOR_MODULE_PATHS,
)
from scripts.asr_base_model_pilot_plan import exact_plan, validate_plan


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002X.json"
PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002X-attempt-25.md"
RISK = ROOT / "platform/decisions/ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-020.json"
RECONCILIATION = ROOT / "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-010.json"
AUDIT = ROOT / "platform/evidence/ASR-BASE-MODEL-WAITER-FINALIZER-AUDIT-2026-002.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002W-ATTEMPT-24-VOLUME-DEVICE-RACE-REFUSAL.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002X-COLD/cold-rehearsal.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt25_is_one_fresh_nontransferable_request() -> None:
    assert bound()["attempts"] == {
        "attempts_1_through_24_reuse_permitted": False,
        "authorized_numbers": [25],
        "cost_ceiling_usd": 10,
        "maximum": 1,
        "maximum_gpu_nodes": 1,
        "non_transferable": True,
        "seconds_each": 10800,
    }
    text = PACKET.read_text()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002X only" in text
    assert sha(RISK) in text


def test_all_attempt25_modules_are_bound_to_the_exact_source_commit() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(ATTEMPT_25_EXECUTOR_MODULE_PATHS)
    assert len(value["executor_modules"]) == len(ATTEMPT_25_EXECUTOR_MODULE_PATHS) == 33
    for relative, expected in value["executor_modules"].items():
        body = subprocess.run(
            ["git", "show", f"{value['executor_source_commit']}:{relative}"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert hashlib.sha256(body).hexdigest() == expected


def test_attachment_and_device_controls_are_exact() -> None:
    policy = bound()["volume_attachment_and_device_policy"]
    assert policy["attachment_api"] == "ec2:DescribeVolumes"
    assert policy["attachment_poll_interval_seconds"] == 5
    assert policy["attachment_timeout_seconds"] == 300
    assert policy["attachment_required_stable_observations"] == 2
    assert policy["guest_device_poll_interval_seconds"] == 2
    assert policy["guest_device_timeout_seconds"] == 120
    assert policy["delayed_observation_rehearsal_required"] is True


def test_mount_template_parameter_and_rendered_hashes_are_distinct() -> None:
    first = "\n".join(volume_mount_commands("vol-01111111111111111"))
    second = "\n".join(volume_mount_commands("vol-02222222222222222"))
    template = "\n".join(volume_mount_command_template())
    assert "__MEDZEN_EBS_VOLUME_SERIAL__" in template
    assert first != second
    assert "vol01111111111111111" in first
    assert "vol02222222222222222" in second
    assert sha_bytes(template.encode()) != sha_bytes(b"vol-01111111111111111")
    assert sha_bytes(first.encode()) != sha_bytes(second.encode())


def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def test_systemic_audit_is_hash_bound_and_covers_remote_ssm() -> None:
    binding = bound()["waiter_finalizer_audit"]
    assert binding["sha256"] == sha(AUDIT)
    audit = json.loads(AUDIT.read_bytes())
    assert audit["status"] == (
        "PASS_SYSTEMIC_WAITER_FINALIZER_AND_REMOTE_SSM_OBSERVATION_AUDIT"
    )
    assert audit["python_waiter_site_count"] == 15
    assert audit["remote_ssm_observation_site_count"] == 9
    assert audit["controls"]["remote_asynchronous_one_shot_success_gates"] == 0
    assert audit["controls"]["remote_unclassified_ssm_call_sites"] == 0
    assert audit["mount_bundle_provenance"] == {
        "volume_independent_template_hash_required": True,
        "volume_parameter_hash_required": True,
        "rendered_command_hash_required": True,
        "historical_comparison_uses_template_hash": True,
        "parameter_token": "__MEDZEN_EBS_VOLUME_SERIAL__",
    }


def test_attempt25_machine_plan_has_no_permanent_or_image_mutation() -> None:
    value = bound()
    assert validate_plan(exact_plan(value, 25), value, 25) == {
        "status": "PASS_EXACT_EXECUTION_PLAN",
        "attempt": 25,
        "permanent_create_only": 0,
        "permanent_bounded_update": 0,
        "temporary_create_then_delete": 18,
        "bounded_capacity_change": 1,
    }
    assert value["image"]["publication_required"] is False
    assert value["security_gate"]["registry_scanning_mutation_permitted"] is False


def test_cost_registry_020_is_conservative_and_request_fits() -> None:
    value = bound()
    assert value["cost_registry"]["sha256"] == sha(COST)
    registry = json.loads(COST.read_bytes())
    assert registry["reconciliation"]["sha256"] == sha(RECONCILIATION)
    summary = registry["guardrail_summary"]
    committed = Decimal(str(summary["recognized_committed_guardrail_usd"]))
    reserved = Decimal(str(summary["active_reservations_usd"]))
    ceiling = Decimal(str(summary["aggregate_ceiling_usd"]))
    assert ceiling - committed - reserved == Decimal("85.5713935784")
    assert ceiling - committed - reserved - Decimal("10") == Decimal(
        "75.5713935784"
    )


def test_write_once_attempt24_history_is_bound() -> None:
    history = bound()["write_once_history"]
    expected = {
        "attempt_24_packet": "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002W-attempt-24.md",
        "attempt_24_authorization": "platform/decisions/ASR-BASE-MODEL-AWS-AUTH-2026-002W.json",
        "attempt_24_bindings": "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002W.json",
        "attempt_24_dry_validation": "platform/evidence/ASR-BASE-MODEL-DEADLINE-IDENTITY-DRY-RUN-2026-002W.json",
        "attempt_24_refusal": "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002W-ATTEMPT-24-VOLUME-DEVICE-RACE-REFUSAL.json",
        "attempt_24_cost_reconciliation": "platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-010.json",
        "attempt_24_cost_registry": "platform/finance/COST-REGISTRY-2026-020.json",
    }
    for key, relative in expected.items():
        assert history[key]["sha256"] == sha(ROOT / relative)
    assert history["attempt_24_live_receipts"]["commit"] == (
        "133050082c3c2a2fac396872a01fd38328eff97f"
    )
    assert history["attempt_24_live_receipts"]["file_set_sha256"] == (
        "e2c1e0418c415aaee0a2d071bbc799476aaf7c63443e0150afbff889b8c440cf"
    )
    assert history["attempt_24_refusal"]["sha256"] == sha(REFUSAL)


def test_receipt_last_rehearsal_covers_both_async_boundaries() -> None:
    value = json.loads(COLD.read_bytes())
    assert value["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert value["bindings_source"]["sha256"] == sha(BINDINGS)
    assert value["executor_module_integrity"]["module_count"] == 33
    fidelity = value["bounded_waiter_rehearsal_fidelity"]
    assert fidelity["status"] == (
        "PASS_ALL_BOUNDED_WAITER_FAKES_EXERCISE_NONTERMINAL_STATE"
    )
    assert fidelity["site_count"] == 15
    lifecycle = value["scenarios"]["clean_pass"]["stage_pod_lifecycle"]
    assert lifecycle["volume_attachment_observation_sequence"][:4] == [
        "absent",
        "attaching",
        "attached",
        "attached",
    ]
    assert lifecycle["volume_device_observation_sequence"][:2] == [
        "ABSENT",
        "PRESENT",
    ]
    assert value["scenarios"]["volume_device_delayed_ready"]["outcome"] == (
        "PASS_PILOT"
    )
    assert value["scenarios"]["volume_attachment_never_attached"][
        "failure_reason_code"
    ] == "VOLUME_ATTACHMENT_TIMEOUT"
    assert value["scenarios"]["volume_device_never_present"][
        "failure_reason_code"
    ] == "SSM_COMMAND_REFUSED"
    assert all(item["zero_state"] is True for item in value["scenarios"].values())
