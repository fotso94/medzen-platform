from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_integrity import (
    LOCAL_RESOURCE_GATED_EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_plan import exact_plan


PACKET = ROOT / "platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-002L-attempt-13.md"
BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002L.json"
COLD = ROOT / "platform/evidence/receipts/ASR-BASE-MODEL-2026-002L-COLD/cold-rehearsal.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002K-ATTEMPT-12-LOCAL-DISK-REFUSAL.json"
KEEP = ROOT / "platform/evidence/ASR-EVAL-HOST-DOCKER-CLEANUP-KEEP-LIST-2026-001.json"
CLEANUP = ROOT / "platform/evidence/ASR-EVAL-HOST-DOCKER-CLEANUP-RESULT-2026-001.json"
POLICY = ROOT / "platform/manifests/ASR-BASE-MODEL-LOCAL-RESOURCE-POLICY-2026-001.json"
QUALIFICATION = ROOT / "platform/evidence/ASR-BASE-MODEL-LOCAL-RESOURCE-QUALIFICATION-2026-001.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-008.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bindings() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_packet_is_non_executable_attempt_thirteen_request() -> None:
    text = PACKET.read_text(encoding="utf-8")
    value = bindings()
    assert "NOT EXECUTABLE" in text
    assert "Approve ASR base-model AWS change packet 2026-002L only" in text
    assert value["attempts"] == {
        "authorized_numbers": [13],
        "maximum": 1,
        "seconds_each": 10800,
        "non_transferable": True,
        "maximum_gpu_nodes": 1,
        "cost_ceiling_usd": 10,
        "attempts_1_through_12_reuse_permitted": False,
    }


def test_cleanup_history_and_keep_list_are_exactly_bound() -> None:
    value = bindings()["write_once_history"]
    assert value["attempt_12_refusal"]["sha256"] == sha(REFUSAL)
    assert value["docker_cleanup_keep_list"]["sha256"] == sha(KEEP)
    assert value["docker_cleanup_result"]["sha256"] == sha(CLEANUP)
    result = json.loads(CLEANUP.read_bytes())
    assert result["filesystem_measurement"]["target_met"] is True
    assert result["keep_list_post_cleanup_verification"]["qualified_eval_image"]["result"] == "PASS_INTACT_LOCAL_AND_ECR"


def test_pre_envelope_resource_policy_is_computed_and_qualified() -> None:
    value = bindings()
    policy = json.loads(POLICY.read_bytes())
    qualification = json.loads(QUALIFICATION.read_bytes())
    assert value["local_resource_policy"]["sha256"] == sha(POLICY)
    assert value["local_resource_qualification"]["sha256"] == sha(QUALIFICATION)
    assert policy["disk"]["calculated_peak_requirement_bytes"] == 12_128_698_368
    assert policy["disk"]["required_available_bytes"] == 42_949_672_960
    assert qualification["validation"]["measured_available_bytes"] >= policy["disk"]["required_available_bytes"]
    assert qualification["validation"]["simultaneous_full_image_representations"] == 1
    assert qualification["validation"]["attempt_number_consumed"] is False


def test_all_eighteen_executor_modules_are_unconditionally_bound() -> None:
    value = bindings()
    assert set(value["executor_modules"]) == set(LOCAL_RESOURCE_GATED_EXECUTOR_MODULE_PATHS)
    result = validate_executor_module_bindings(ROOT, value["executor_modules"])
    assert result["status"] == "PASS_ALL_EXECUTOR_MODULE_HASHES"
    assert result["module_count"] == 18
    for relative, expected in value["executor_modules"].items():
        assert hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() == expected


def test_cold_rehearsal_covers_both_disk_outcomes_and_single_representation() -> None:
    receipt = json.loads(COLD.read_bytes())
    assert receipt["status"] == "PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS"
    assert receipt["bindings_source"]["sha256"] == sha(BINDINGS)
    resource = receipt["pre_envelope_resource_gate"]
    assert resource["sufficient_capacity"] is True
    assert resource["insufficient_capacity"] == {
        "status": "PASS_PRE_ENVELOPE_RESOURCE_REFUSAL_REHEARSAL",
        "reason_code": "LOCAL_DISK_CAPACITY_INSUFFICIENT",
        "attempt_envelope_created": False,
        "attempt_number_consumed": False,
        "workdir_created": False,
        "aws_boundary_calls": 0,
        "kubectl_boundary_calls": 0,
    }
    representation = receipt["single_representation_security_scan"]
    assert representation["status"] == "PASS_SINGLE_REPRESENTATION_EXACT_DOCKER_ARCHIVE"
    assert representation["simultaneous_full_image_representations"] == 1
    assert representation["oci_layout_materialized"] is False


def test_attempt_thirteen_plan_is_read_only_for_existing_image_and_bundle() -> None:
    value = bindings()
    plan = exact_plan(value, 13)
    assert plan["permanent_create_only"] == []
    assert "ecr:repository/medzen-asr-eval-runtime" in plan["read_only_existing"]
    assert "s3:" + value["pilot_bundle"]["s3_prefix"].removeprefix("s3://") + "**" in plan["read_only_existing"]


def test_cost_registry_conservatively_closes_attempt_twelve() -> None:
    value = json.loads(COST.read_bytes())
    assert bindings()["cost_registry"]["sha256"] == sha(COST)
    summary = value["guardrail_summary"]
    assert summary["recognized_committed_guardrail_usd"] == 94.4286064216
    assert summary["active_reservations_usd"] == 0
    assert summary["guardrail_headroom_after_reservations_usd"] == 205.5713935784
    assert summary["attempt_12_actual_direct_compute_gross_usd"] is None
    assert value["controls"]["attempt_11_and_12_actual_billing_must_be_reconciled_when_landed"] is True


def test_packet_binds_every_successor_evidence_hash() -> None:
    text = PACKET.read_text(encoding="utf-8")
    for path in (BINDINGS, COLD, REFUSAL, KEEP, CLEANUP, POLICY, QUALIFICATION, COST):
        assert sha(path) in text
