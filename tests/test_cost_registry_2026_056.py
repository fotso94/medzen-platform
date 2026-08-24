"""Codex review #14 finding 5: the head registry's guardrail_summary must
be a pure function of its rows — recompute_registry_totals (which now
counts CALCULATED_ACCRUAL actuals) must equal the stored summary."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b5_sagemaker_job import recompute_registry_totals  # noqa: E402

REG = ROOT / "platform/finance/COST-REGISTRY-2026-056.json"


def test_summary_equals_recompute_including_actuals():
    d = json.loads(REG.read_bytes())
    t = recompute_registry_totals(d)
    gs = d["guardrail_summary"]
    assert t["active"] == gs["active_reservations_usd"] == 0
    assert t["recognized"] == gs["recognized_committed_guardrail_usd"]
    assert round(t["recognized"] + t["active"], 10) == gs["committed_plus_reserved_usd"]
    assert round(gs["aggregate_ceiling_usd"] - t["recognized"] - t["active"], 10) == (
        gs["guardrail_headroom_after_reservations_usd"])
    # actuals are now visible and reconcile arm-1 + sweep + smoke
    assert t["calculated_accrual"] == gs["calculated_accrual_usd"] == 49.44


def test_arm1_is_a_consumed_accrual_not_an_active_reservation():
    d = json.loads(REG.read_bytes())
    eff = recompute_registry_totals(d)["effective"]
    assert eff["B5-ARM1-2026-001"]["financial_state"] == "CALCULATED_ACCRUAL"
    assert "SETTLED_ACTUAL" not in eff["B5-ARM1-2026-001"]["financial_state"]
