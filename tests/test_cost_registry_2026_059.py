"""Revision 059 records the Arm-2 preservation-aware KD calibration's
compute accrual. Like COST-REGISTRY-2026-058, the head registry's
guardrail_summary must be a pure function of its rows — recompute_registry_totals
(which counts CALCULATED_ACCRUAL/CALCULATED_ESTIMATE amounts) must equal the
stored summary, and the new Arm-2 line must be a CALCULATED_ACCRUAL (pending
settlement), NOT a SETTLED_ACTUAL."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b5_sagemaker_job import recompute_registry_totals  # noqa: E402

REG = ROOT / "platform/finance/COST-REGISTRY-2026-059.json"


def test_summary_equals_recompute_including_the_arm2_accrual():
    d = json.loads(REG.read_bytes())
    t = recompute_registry_totals(d)
    gs = d["guardrail_summary"]
    assert t["active"] == gs["active_reservations_usd"] == 0
    assert t["recognized"] == gs["recognized_committed_guardrail_usd"]
    assert round(t["recognized"] + t["active"], 10) == gs["committed_plus_reserved_usd"]
    assert round(gs["aggregate_ceiling_usd"] - t["recognized"] - t["active"], 10) == (
        gs["guardrail_headroom_after_reservations_usd"])
    # the new $0.5458 Arm-2 calibration accrual is now visible in the total
    # (49.54 carried forward from revision 058 + 0.5458 = 50.0858)
    assert t["calculated_estimate"] == gs["calculated_estimate_usd"] == 50.0858


def test_arm2_calibration_is_a_calculated_accrual_not_a_settled_actual():
    d = json.loads(REG.read_bytes())
    eff = recompute_registry_totals(d)["effective"]
    arm2 = eff["B5-ARM2-FTCAL-2026-001"]
    assert arm2["financial_state"] == "CALCULATED_ACCRUAL"
    assert arm2["financial_state"] != "SETTLED_ACTUAL"
    assert arm2["settlement_state"] == "CALCULATED_ACCRUAL_PENDING_SETTLEMENT"
    # the accrual is the exact billable-seconds / padded on-demand computation
    assert arm2["billable_seconds"] == arm2["maximum_observed_gpu_seconds"] == 1228
    assert arm2["actual_usd"] == 0.5458
    assert arm2["gpu_instance_type"] == "g6.xlarge"
    # a below-tier mechanics/memory calibration, never a promotion signal
    assert "NOT a promotion signal" in arm2["outcome"]


def test_this_revision_is_the_append_only_successor_of_058():
    d = json.loads(REG.read_bytes())
    assert d["id"] == "COST-REGISTRY-2026-059"
    assert d["revision"] == 59
    assert d["supersedes"] == "COST-REGISTRY-2026-058"
    assert "revision_059_correction" in d
    # the Arm-2 line is the LAST line for its allocation_id (effective_state_rule)
    arm2_lines = [a for a in d["allocations"]
                  if a.get("allocation_id") == "B5-ARM2-FTCAL-2026-001"]
    assert len(arm2_lines) == 1
    assert d["allocations"][-1]["allocation_id"] == "B5-ARM2-FTCAL-2026-001"
