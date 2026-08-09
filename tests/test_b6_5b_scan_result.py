from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT = ROOT / "platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json"
COST = ROOT / "platform/finance/COST-REGISTRY-2026-003.json"


def sha(relative: str) -> str:
    return hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()


def test_scan_result_is_pass_only_for_four_exact_clean_children():
    result = json.loads(RESULT.read_bytes())
    assert result["status"] == "VERIFIED_COMPLETE"
    assert result["outcome"] == "PASS_SCAN_ONLY"
    assert result["authorization"]["sha256"] == sha(
        result["authorization"]["path"]
    )
    assert result["packet"]["sha256"] == sha(result["packet"]["path"])
    assert result["local_engineering"]["sha256"] == sha(
        result["local_engineering"]["path"]
    )
    subjects = result["automatic_scan_subjects"]
    assert set(subjects) == {
        "medzen-rag-index",
        "medzen-llm-gateway",
        "medzen-orchestrator",
        "medzen-speech-tts-gateway",
    }
    assert all(subject["scan_status"] == "COMPLETE" for subject in subjects.values())
    assert all(subject["identity_match"] is True for subject in subjects.values())
    assert all(subject["critical"] == subject["high"] == 0 for subject in subjects.values())
    assert all(subject["findings"] == [] for subject in subjects.values())


def test_scan_result_preserves_the_approved_aws_boundary():
    result = json.loads(RESULT.read_bytes())
    post = result["post_execution_verification"]
    assert post["manual_start_image_scan_events"] == 0
    assert post["security_waiver_used"] is False
    assert post["cpu_desired"] == post["gpu_desired"] == 0
    assert post["approved_asr_objects"] == 0
    assert post["production_registry_parameters"] == 0
    assert post["deployment_attempted"] is False
    assert result["next_boundary"]["b6_6_execution_authorized"] is False


def test_cost_revision_closes_the_scan_reservation_conservatively():
    cost = json.loads(COST.read_bytes())
    assert cost["supersedes"]["sha256"] == sha(cost["supersedes"]["path"])
    summary = cost["guardrail_summary"]
    assert summary["aggregate_ceiling_usd"] == 300.0
    assert summary["recognized_committed_guardrail_usd"] == 63.5288
    assert summary["active_reservations_usd"] == 0.0
    assert summary["guardrail_headroom_after_reservations_usd"] == 236.4712
    allocations = {item["allocation_id"]: item for item in cost["allocations"]}
    scan = allocations["B6-5B-ECR-SCAN-ONLY-2026-001"]
    assert scan["recognized_committed_usd"] == 1.0
    assert scan["active_reservation_usd"] == 0.0
    assert scan["actual_cost_usd"] is None
    assert cost["controls"]["current_active_billable_reservations"] == 0
