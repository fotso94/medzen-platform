#!/usr/bin/env python3
"""Persist one sanitized B6.6 stage receipt from a canonical JSON payload."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.b6_integration_receipts import ReceiptStore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage")
    parser.add_argument("status")
    parser.add_argument("--receipts-dir", type=Path, required=True)
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.payload)
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        value = ReceiptStore(args.receipts_dir).persist(args.stage, args.status, payload)
        print(json.dumps(value, sort_keys=True))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "error": type(exc).__name__}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
