from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_boundary_contracts import (
    DRA_WAIT_MAX_SECONDS,
    audit_bounded_helper_calls,
)
from scripts.asr_base_model_pilot_integrity import (
    SHARED_BOUNDARY_GATED_EXECUTOR_MODULE_PATHS,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_plan import exact_plan


BINDINGS = ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002M.json"
REFUSAL = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002L-ATTEMPT-13-DRA-WAIT-BOUNDARY-REFUSAL.json"
DIAGNOSIS = ROOT / "platform/evidence/ASR-BASE-MODEL-PACKET-2026-002L-ATTEMPT-13-DRA-BOUNDARY-DIAGNOSIS-2026-001.json"
KEEP = ROOT / "platform/evidence/ASR-EVAL-HOST-DOCKER-CLEANUP-KEEP-LIST-2026-002.json"
CLEANUP = ROOT / "platform/evidence/ASR-EVAL-HOST-DOCKER-CLEANUP-RESULT-2026-002.json"
QUALIFICATION = ROOT / "platform/evidence/ASR-BASE-MODEL-LOCAL-RESOURCE-QUALIFICATION-2026-002.json"
COST_OBSERVATION = ROOT / "platform/evidence/ASR-BASE-MODEL-ATTEMPT-13-COST-OBSERVATION-2026-001.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-009.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bound() -> dict:
    return json.loads(BINDINGS.read_bytes())


def test_attempt_fourteen_is_one_new_nontransferable_request() -> None:
    assert bound()["attempts"] == {
        "authorized_numbers": [14],
        "maximum": 1,
        "seconds_each": 10800,
        "non_transferable": True,
        "maximum_gpu_nodes": 1,
        "cost_ceiling_usd": 10,
        "attempts_1_through_13_reuse_permitted": False,
    }


def test_attempt_thirteen_history_and_successor_evidence_are_hash_bound() -> None:
    history = bound()["write_once_history"]
    assert history["attempt_13_refusal"]["sha256"] == sha(REFUSAL)
    assert history["attempt_13_diagnosis"]["sha256"] == sha(DIAGNOSIS)
    assert history["attempt_13_cost_observation"]["sha256"] == sha(COST_OBSERVATION)
    assert history["attempt_14_docker_cleanup_keep_list"]["sha256"] == sha(KEEP)
    assert history["attempt_14_docker_cleanup_result"]["sha256"] == sha(CLEANUP)


def test_shared_boundary_contract_is_bound_and_exhaustively_audited() -> None:
    value = bound()
    assert set(value["executor_modules"]) == set(
        SHARED_BOUNDARY_GATED_EXECUTOR_MODULE_PATHS
    )
    result = validate_executor_module_bindings(ROOT, value["executor_modules"])
    assert result["module_count"] == 19
    audit = audit_bounded_helper_calls(ROOT)
    assert audit["status"] == "PASS_ALL_BOUNDED_HELPER_CALLS"
    assert audit["call_site_count"] == 43
    assert audit["fake_may_bypass_validation"] is False
    contract = value["rehearsal_fidelity_boundary"]["shared_boundary_contract"]
    assert contract["dra_wait_timeout_seconds"] == DRA_WAIT_MAX_SECONDS == 300
    assert contract["dra_wait_contract_widened"] is False


def test_post_cleanup_capacity_passes_and_bound_image_survives() -> None:
    cleanup = json.loads(CLEANUP.read_bytes())
    qualification = json.loads(QUALIFICATION.read_bytes())
    assert cleanup["filesystem_measurement"]["target_met"] is True
    assert cleanup["keep_list_post_cleanup_verification"]["qualified_eval_image"]["result"] == "PASS_INTACT_LOCAL_AND_ECR"
    assert bound()["local_resource_qualification"]["sha256"] == sha(QUALIFICATION)
    assert qualification["validation"]["measured_available_bytes"] >= qualification["validation"]["required_available_bytes"]


def test_attempt_fourteen_plan_reuses_existing_image_and_bundle_read_only() -> None:
    value = bound()
    plan = exact_plan(value, 14)
    assert plan["permanent_create_only"] == []
    assert "ecr:repository/medzen-asr-eval-runtime" in plan["read_only_existing"]
    assert "s3:" + value["pilot_bundle"]["s3_prefix"].removeprefix("s3://") + "**" in plan["read_only_existing"]


def test_cost_registry_recognizes_full_attempt_thirteen_ceiling() -> None:
    value = json.loads(COST.read_bytes())
    assert bound()["cost_registry"]["sha256"] == sha(COST)
    assert value["guardrail_summary"]["recognized_committed_guardrail_usd"] == 104.4286064216
    assert value["guardrail_summary"]["guardrail_headroom_after_reservations_usd"] == 195.5713935784
    assert value["guardrail_summary"]["attempt_13_actual_direct_compute_gross_usd"] is None
    assert value["attempt_13_cost_observation"]["sha256"] == sha(COST_OBSERVATION)
