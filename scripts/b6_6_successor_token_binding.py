#!/usr/bin/env python3
"""Verify a stage-0 token against its immutable dynamic receipt."""
from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.b6_6_token_binding import TOKEN_PATH, TokenBindingRefusal, verify_bytes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("token_file", type=Path)
    parser.add_argument("verification_receipt", type=Path)
    args = parser.parse_args()
    try:
        receipt = json.loads(args.verification_receipt.read_bytes())
        expected = receipt["bearer_token_sha256"]
        if args.token_file != TOKEN_PATH:
            raise TokenBindingRefusal("TOKEN_PATH_DIFFERS")
        if stat.S_IMODE(args.token_file.stat().st_mode) != 0o600:
            raise TokenBindingRefusal("TOKEN_MODE_DIFFERS")
        raw = args.token_file.read_bytes()
        value = verify_bytes(raw, expected_sha256=expected)
        value["dynamic_receipt_hash_verified"] = True
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(value, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
