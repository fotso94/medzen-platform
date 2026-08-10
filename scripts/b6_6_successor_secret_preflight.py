#!/usr/bin/env python3
"""Verify the dynamic stage-0 credential before workers can start."""
from __future__ import annotations

import argparse
import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_b6_client_secret_restoration_2026_015 as proven
from scripts.b6_6_successor_credential_stage import (
    CURRENT_VERSION,
    LEGACY_VERSIONS,
    TOKEN_PATH,
)


class SecretPreflightRefusal(RuntimeError):
    pass


def verify(secret_client, iam_client, verification_receipt: Path) -> dict:
    value = json.loads(verification_receipt.read_bytes())
    if (
        value.get("stage") != "verification"
        or value.get("status") != proven.RECEIPT_STATUSES["verification"]
        or value.get("authorization_id") != "B6-AWS-AUTH-2026-017"
        or value.get("all_prior_version_ids") != [CURRENT_VERSION, *LEGACY_VERSIONS]
        or value.get("all_prior_versions_unstaged") is not True
    ):
        raise SecretPreflightRefusal("credential verification receipt differs")
    fresh = value.get("fresh_version_id")
    expected = {
        fresh: ["AWSCURRENT"],
        CURRENT_VERSION: [],
        LEGACY_VERSIONS[0]: [],
        LEGACY_VERSIONS[1]: [],
    }
    state = proven.validate_secret(secret_client, pending=False, expected_versions=expected)
    proven._validate_live_policies(secret_client, iam_client)
    raw = TOKEN_PATH.read_bytes()
    if (
        stat.S_IMODE(TOKEN_PATH.stat().st_mode) != 0o600
        or len(raw) != 44
        or raw[-1:] != b"\n"
        or proven.sha(raw[:-1]) != value.get("bearer_token_sha256")
    ):
        raise SecretPreflightRefusal("fresh local material differs")
    return {
        "status": "PASS",
        "fresh_version_id": fresh,
        "fresh_material_sha256": value["bearer_token_sha256"],
        "secret_value_sha256": value["secret_value_sha256"],
        "prior_version_count": 3,
        "plaintext_read": False,
        "pending_deletion": state["description"].get("DeletedDate") is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verification-receipt", type=Path, required=True)
    parser.add_argument("--profile", default="medzen")
    args = parser.parse_args()
    try:
        session = proven._session(args.profile)
        result = verify(
            session.client("secretsmanager"),
            session.client("iam"),
            args.verification_receipt,
        )
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": type(exc).__name__}))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
