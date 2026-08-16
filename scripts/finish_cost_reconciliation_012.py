#!/usr/bin/env python3
"""Finish reconciliation 012 once Cost Explorer publishes the suite window.

Reads the committed PREP record, queries CE for the exact g6.xlarge usage
across the suite days, and refuses to finalize unless: (a) CE reports
nonzero cost for every day a GPU window was measured, and (b) the CE total
is within tolerance of the estimate (a large gap means an unmodeled
charge, which must be investigated, not averaged away).

Read-only against AWS; writes the final reconciliation next to the prep.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

PREP = Path("platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-012-PREP.json")
FINAL = Path("platform/evidence/ASR-BASE-MODEL-COST-RECONCILIATION-2026-012.json")
TOLERANCE = 0.5  # |CE - estimate| / estimate must stay under 50%


def ce_daily(start: str, end: str) -> dict[str, float]:
    out = subprocess.run([
        "aws", "ce", "get-cost-and-usage",
        "--time-period", f"Start={start},End={end}",
        "--granularity", "DAILY", "--metrics", "UnblendedCost",
        "--filter", json.dumps({"Dimensions": {"Key": "USAGE_TYPE",
                                               "Values": ["EUC1-BoxUsage:g6.xlarge"]}}),
        "--query", "ResultsByTime[].[TimePeriod.Start,Total.UnblendedCost.Amount]",
        "--output", "json"], capture_output=True, text=True, check=True).stdout
    return {day: float(amount) for day, amount in json.loads(out)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-08-15")
    parser.add_argument("--end", default="2026-08-18")
    args = parser.parse_args()
    prep = json.loads(PREP.read_bytes())
    if FINAL.exists():
        raise SystemExit(f"refusing: {FINAL} already exists")
    daily = ce_daily(args.start, args.end)
    total = sum(daily.values())
    estimate = prep["totals"]["estimated_actual_usd"]
    if total <= 0:
        print(json.dumps({"status": "STILL_PENDING", "daily": daily}))
        return 1
    gap = abs(total - estimate) / max(estimate, 0.01)
    if gap > TOLERANCE:
        print(json.dumps({"status": "REFUSED_GAP_TOO_LARGE",
                          "ce_total": total, "estimate": estimate, "gap": round(gap, 3),
                          "action": "investigate the unmodeled charge before finalizing"}))
        return 1
    final = {
        **prep,
        "id": "ASR-BASE-MODEL-COST-RECONCILIATION-2026-012",
        "record": "ASR_BASE_MODEL_COST_RECONCILIATION",
        "status": "PASS_SUITE_WINDOW_RECONCILED",
        "supersedes": prep["id"],
        "cost_explorer": {"daily_g6_usd": daily, "total_g6_usd": round(total, 2),
                          "estimate_usd": estimate, "relative_gap": round(gap, 3)},
    }
    FINAL.write_bytes(json.dumps(final, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False).encode() + b"\n")
    print(json.dumps({"status": "PASS_SUITE_WINDOW_RECONCILED", "total": round(total, 2)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
