#!/usr/bin/env python3
"""Refuse unless a saved plan is exactly the 003C-B B6A identity phase."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from check_b6a_003a_tf_plan import load_saved_plan, validate_plan as validate_base_plan


def validate_plan(plan):
    result = validate_base_plan(plan, "identity")
    result["status"] = "PASS_EXACT_B6A_PACKET_2026_003C_B_IDENTITY_PHASE"
    result["phase"] = "identity"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    try:
        plan_bytes = args.plan.read_bytes()
        summary = validate_plan(load_saved_plan(args.plan))
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSING B6A PACKET 003C-B IDENTITY APPLY: {exc}", file=sys.stderr)
        return 2
    summary["saved_plan_sha256"] = hashlib.sha256(plan_bytes).hexdigest()
    summary["saved_plan_bytes"] = len(plan_bytes)
    print(json.dumps(summary, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
