from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "platform/finance/COST-REGISTRY-2026-005.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _value() -> dict:
    return json.loads(REGISTRY.read_bytes())


def test_revision_005_binds_immutable_predecessor_and_reconciliation() -> None:
    value = _value()
    for key in ("supersedes", "reconciliation_evidence"):
        binding = value[key]
        assert _sha256(ROOT / binding["path"]) == binding["sha256"]
    assert value["controls"]["historical_budget_records_edited"] is False


def test_revision_005_guardrail_math_and_single_reservation() -> None:
    summary = _value()["guardrail_summary"]
    assert summary["recognized_committed_guardrail_usd"] == pytest.approx(
        63.5288 + 0.8998064216
    )
    assert summary["active_reservations_usd"] == 10.0
    assert summary["committed_plus_reserved_usd"] == pytest.approx(
        summary["recognized_committed_guardrail_usd"] + 10.0
    )
    assert summary["guardrail_headroom_after_reservations_usd"] == pytest.approx(
        300.0 - summary["committed_plus_reserved_usd"]
    )
    assert _value()["controls"]["current_active_billable_reservations"] == 1


def test_actual_worker_cost_is_separate_from_conservative_upper_bound() -> None:
    summary = _value()["guardrail_summary"]
    assert summary["actual_direct_worker_compute_gross_usd"] == pytest.approx(
        0.5141739888
    )
    assert summary["actual_direct_worker_compute_credits_usd"] == pytest.approx(
        -0.5141739888
    )
    assert summary["actual_direct_worker_compute_net_usd"] == 0.0
    assert summary["incremental_since_revision_004_recognized_guardrail_usd"] == pytest.approx(
        0.8998064216
    )
    assert _value()["controls"]["credits_used_to_expand_reservation"] is False


def test_attribution_limits_are_not_silently_presented_as_exact_project_cost() -> None:
    evidence = json.loads(
        (ROOT / _value()["reconciliation_evidence"]["path"]).read_bytes()
    )
    limits = evidence["attribution_limits"]
    assert limits["project_cost_allocation_tags_available"] is False
    assert limits["current_daily_results_are_estimated"] is True
    assert evidence["controls"]["conservative_upper_bound_used_instead_of_zero"] is True
    assert evidence["controls"]["qualified_financial_review_required_before_signoff"] is True
