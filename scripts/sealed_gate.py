#!/usr/bin/env python3
"""Mandatory pre-evaluation gate for sealed holdouts (Codex review #11:
the ledger was not called by any live evaluator).

Usage, BEFORE any sealed selection or audio is read:
    python scripts/sealed_gate.py acquire --holdout <key> --sha <sha> \
        --consumer "<run description>"
Refuses (exit 1) if the holdout is spent or quarantined; otherwise appends
the CONSUMED entry and prints it. Launch scripts must run this and abort
on nonzero exit — evaluating a sealed set without a fresh CONSUMED entry
violates PROMOTION-PROTOCOL-2026-001."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from holdout_ledger import LedgerRefusal, record_consumption


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    acq = sub.add_parser("acquire")
    acq.add_argument("--holdout", required=True)
    acq.add_argument("--sha", required=True)
    acq.add_argument("--consumer", required=True)
    args = parser.parse_args()
    try:
        entry = record_consumption(args.holdout, args.sha, args.consumer)
    except LedgerRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        return 1
    print(json.dumps({"status": "ACQUIRED", "entry": entry}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
