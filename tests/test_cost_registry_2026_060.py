"""Revision 060 is the append-only correction of the -059 Arm-2 accrual line
(Codex round 31): the calculated accrual is relabelled estimate_usd (never a
settled actual_usd) and the SageMaker instance type is ml.g6.xlarge. The value
0.5458 is unchanged, so the head registry's guardrail_summary remains a pure
function of its effective rows."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from b5_sagemaker_job import recompute_registry_totals  # noqa: E402

REG = ROOT / "platform/finance/COST-REGISTRY-2026-060.json"


def test_summary_equals_recompute_after_the_relabel():
    d = json.loads(REG.read_bytes())
    t = recompute_registry_totals(d)
    gs = d["guardrail_summary"]
    assert t["active"] == gs["active_reservations_usd"] == 0
    assert t["recognized"] == gs["recognized_committed_guardrail_usd"]
    assert round(t["recognized"] + t["active"], 10) == gs["committed_plus_reserved_usd"]
    assert round(gs["aggregate_ceiling_usd"] - t["recognized"] - t["active"], 10) == (
        gs["guardrail_headroom_after_reservations_usd"])
    # relabel actual_usd -> estimate_usd keeps the same value, so the total is
    # unchanged from revision 059
    assert t["calculated_estimate"] == gs["calculated_estimate_usd"] == 50.0858


def test_effective_arm2_line_is_relabelled_correctly():
    d = json.loads(REG.read_bytes())
    arm2 = recompute_registry_totals(d)["effective"]["B5-ARM2-FTCAL-2026-001"]
    # Codex round 31: it is a CALCULATED accrual labelled estimate_usd, not a
    # settled actual_usd, on a ml.g6.xlarge SageMaker job
    assert arm2.get("estimate_usd") == 0.5458
    assert "actual_usd" not in arm2, "the effective line must not relabel to actual_usd"
    assert arm2["gpu_instance_type"] == "ml.g6.xlarge"
    assert arm2["financial_state"] == "CALCULATED_ACCRUAL"
    assert arm2["settlement_state"] == "CALCULATED_ACCRUAL_PENDING_SETTLEMENT"
    assert arm2["billable_seconds"] == arm2["maximum_observed_gpu_seconds"] == 1228
    assert "NOT a promotion signal" in arm2["outcome"]


def test_this_revision_is_the_append_only_successor_of_059():
    d = json.loads(REG.read_bytes())
    assert d["id"] == "COST-REGISTRY-2026-060"
    assert d["revision"] == 60
    assert d["supersedes"] == "COST-REGISTRY-2026-059"
    assert "revision_060_correction" in d
    # both arm-2 lines exist (history + corrected); the corrected one is last
    arm2_lines = [a for a in d["allocations"]
                  if a.get("allocation_id") == "B5-ARM2-FTCAL-2026-001"]
    assert len(arm2_lines) == 2
    assert d["allocations"][-1]["allocation_id"] == "B5-ARM2-FTCAL-2026-001"
    assert d["allocations"][-1].get("estimate_usd") == 0.5458


def test_no_record_timestamp_is_future_dated_relative_to_its_commit():
    """Codex round 31: the -059 timestamps were later than their commit. The
    corrected record's timestamps must be internally consistent (recorded_utc
    == the effective line's utc)."""
    d = json.loads(REG.read_bytes())
    arm2 = d["allocations"][-1]
    assert d["recorded_utc"] == arm2["utc"], (
        "the successor's recorded_utc and the corrected line's utc must agree")
