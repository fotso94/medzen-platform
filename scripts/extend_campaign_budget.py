#!/usr/bin/env python3
"""Conditionally extend the existing B4 ledger without resetting spend."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, ParamValidationError

ROOT = Path(__file__).resolve().parent.parent
BUCKET = "medzen-speech"
KEY = "candidates/budget/b4-scoped/ledger.json"
DECISION = ROOT / "platform/decisions/B4-BUDGET-2026-001-ceiling-extension.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    decision_bytes = DECISION.read_bytes()
    decision = json.loads(decision_bytes)
    old = float(decision["previous_ceiling_usd"])
    new = float(decision["new_cumulative_ceiling_usd"])
    decision_sha = hashlib.sha256(decision_bytes).hexdigest()

    s3 = boto3.Session(profile_name="medzen", region_name="eu-central-1").client("s3")
    obj = s3.get_object(Bucket=BUCKET, Key=KEY)
    ledger = json.loads(obj["Body"].read())
    if ledger.get("campaign") != "b4-scoped":
        raise SystemExit("REFUSING: unexpected campaign ledger")
    if float(ledger.get("ceiling_usd", -1)) not in (old, new):
        raise SystemExit("REFUSING: ledger ceiling is neither the approved old nor new value")
    unresolved = [key for key, row in ledger["reservations"].items()
                  if row.get("state") == "reserved"]
    if unresolved:
        raise SystemExit(f"REFUSING: unresolved reservations exist: {unresolved}")
    committed = round(sum(
        row.get("actual_usd", 0.0) for row in ledger["reservations"].values()
        if row.get("state") == "reconciled"), 4)
    if committed != float(decision["reconciled_spend_at_authorization_usd"]):
        raise SystemExit(
            f"REFUSING: reconciled spend changed: expected "
            f"{decision['reconciled_spend_at_authorization_usd']}, got {committed}")

    print(json.dumps({
        "ready": True,
        "ledger_key": KEY,
        "current_ceiling_usd": ledger["ceiling_usd"],
        "new_ceiling_usd": new,
        "committed_usd": committed,
        "unresolved_reservations": 0,
        "decision_sha256": decision_sha,
    }, indent=2))
    if not args.confirm:
        print("DRY RUN - pass --confirm to conditionally update the ledger")
        return 0
    if float(ledger["ceiling_usd"]) == new:
        print("ALREADY APPLIED - verified existing $9 cumulative ceiling")
        return 0

    ledger["ceiling_usd"] = new
    ledger["ceiling_extension"] = {
        "decision": str(DECISION.relative_to(ROOT)),
        "decision_sha256": decision_sha,
        "previous_ceiling_usd": old,
        "new_cumulative_ceiling_usd": new,
        "reconciled_spend_at_extension_usd": committed,
        "spend_reset": False,
    }
    body = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode()
    try:
        s3.put_object(Bucket=BUCKET, Key=KEY, Body=body,
                      ContentType="application/json",
                      IfMatch=obj["ETag"])
    except ParamValidationError as exc:
        raise SystemExit("REFUSING: botocore lacks conditional S3 writes") from exc
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "PreconditionFailed":
            raise SystemExit("REFUSING: ledger changed concurrently") from exc
        raise

    readback = s3.get_object(Bucket=BUCKET, Key=KEY)["Body"].read()
    if readback != body:
        raise SystemExit("REFUSING: ledger readback differs after update")
    print("APPLIED AND VERIFIED - cumulative ceiling is $9.00; prior spend preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
