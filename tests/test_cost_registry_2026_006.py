from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "platform/finance/COST-REGISTRY-2026-006.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _value() -> dict:
    return json.loads(REGISTRY.read_bytes())


def test_revision_006_binds_immutable_predecessor_and_reconciliation() -> None:
    value = _value()
    for key in ("supersedes", "reconciliation_evidence"):
        binding = value[key]
        assert _sha256(ROOT / binding["path"]) == binding["sha256"]
    assert value["controls"]["historical_budget_records_edited"] is False


def test_revision_006_closes_reservation_without_expanding_headroom() -> None:
    summary = _value()["guardrail_summary"]
    assert summary["recognized_committed_guardrail_usd"] == pytest.approx(
        64.4286064216 + 10.0
    )
    assert summary["active_reservations_usd"] == 0.0
    assert summary["committed_plus_reserved_usd"] == pytest.approx(74.4286064216)
    assert summary["guardrail_headroom_after_reservations_usd"] == pytest.approx(
        300.0 - 74.4286064216
    )
    assert _value()["controls"]["current_active_billable_reservations"] == 0
    assert _value()["controls"]["credits_used_to_expand_headroom"] is False


def test_current_aws_billing_is_separate_from_conservative_guardrail() -> None:
    summary = _value()["guardrail_summary"]
    assert summary["current_cost_explorer_cumulative_relevant_service_net_usd"] == pytest.approx(
        1.5186372174
    )
    assert summary["current_cost_explorer_delta_since_revision_005_usd"] == pytest.approx(
        0.6188307958
    )
    assert summary["actual_direct_worker_compute_since_revision_005_gross_usd"] == pytest.approx(
        0.3008295804
    )
    assert summary["actual_direct_worker_compute_since_revision_005_credits_usd"] == pytest.approx(
        -0.3008295804
    )
    assert summary["actual_direct_worker_compute_since_revision_005_net_usd"] == 0.0


def test_reconciliation_discloses_estimation_and_timing_margin() -> None:
    value = _value()
    evidence = json.loads(
        (ROOT / value["reconciliation_evidence"]["path"]).read_bytes()
    )
    assert evidence["attribution_limits"]["project_cost_allocation_tags_available"] is False
    assert evidence["attribution_limits"]["current_daily_results_are_estimated"] is True
    assert evidence["attribution_limits"]["current_day_ingestion_complete"] is False
    assert evidence["reservation_reconciliation"]["conservative_guardrail_margin_above_current_cumulative_net_usd"] == pytest.approx(
        9.3811692042
    )
    assert evidence["controls"]["daily_estimate_overlap_double_counted"] is False
    assert evidence["controls"]["qualified_financial_review_required_before_financial_signoff"] is True
