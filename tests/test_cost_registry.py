from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "platform/finance/COST-REGISTRY-2026-001.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def value() -> dict:
    return json.loads(REGISTRY.read_bytes())


def test_cost_registry_binds_every_historical_source_without_editing_it():
    registry = value()
    for source in registry["source_records"]:
        assert sha(ROOT / source["path"]) == source["sha256"]
    assert registry["controls"]["historical_budget_records_edited"] is False


def test_cost_registry_guardrail_math_does_not_double_count_b6a_consumption():
    summary = value()["guardrail_summary"]
    assert summary["recognized_committed_guardrail_usd"] == pytest.approx(22.5288 + 25.0)
    assert summary["committed_plus_reserved_usd"] == pytest.approx(47.5288 + 15.0)
    assert summary["guardrail_headroom_after_reservations_usd"] == pytest.approx(300.0 - 62.5288)
    assert summary["b6a_conservative_gpu_consumption_inside_active_reservation_usd"] == 0.9401


def test_every_cost_allocation_has_the_complete_tag_set():
    registry = value()
    required = set(registry["allocation_tag_standard"]["required_keys"])
    for allocation in registry["allocations"]:
        assert set(allocation["allocation_tags"]) == required
        assert allocation["allocation_tags"]["BudgetRegistry"] == registry["id"]


def test_unreconciled_costs_are_not_silently_zeroed():
    allocations = {item["allocation_id"]: item for item in value()["allocations"]}
    assert allocations["PLATFORM-STANDING-INFRASTRUCTURE"]["recognized_committed_usd"] is None
    assert allocations["GREEN-BUCKET-DATA-AGGREGATION"]["recognized_committed_usd"] is None
    assert value()["guardrail_summary"]["actual_project_spend"] == "NOT_FULLY_RECONCILED"
