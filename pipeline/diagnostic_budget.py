"""Fresh bounded ledger for PLAN-2026-003 only.

The failed/reconciled training campaign keeps its own ledger.  This namespace
authorises one image builder and one no-training diagnostic GPU stage, and
cannot be spent by a sweep or final trainer.
"""
from __future__ import annotations

import hashlib
import json
import time

BUCKET = "medzen-speech"
CAMPAIGN = "b4-amharic-termination-diagnostic"
LEDGER_KEY = f"candidates/budget/{CAMPAIGN}/ledger.json"
RATES = {"g6.xlarge": 1.0064, "c6i.2xlarge": 0.34}
WATCHDOG_S = {"builder": 1800, "diagnostic": 3300}
STAGE_INSTANCE = {"builder": "c6i.2xlarge", "diagnostic": "g6.xlarge"}
EC2_LIFECYCLE_OVERHEAD_S = 600
CEILING_USD = 1.50


def worst_case_usd(stage: str) -> float:
    if stage not in WATCHDOG_S:
        raise ValueError(f"unknown diagnostic stage {stage!r}")
    seconds = WATCHDOG_S[stage] + EC2_LIFECYCLE_OVERHEAD_S
    return round(RATES[STAGE_INSTANCE[stage]] * seconds / 3600.0, 4)


def reservation_id(stage: str, attempt: str) -> str:
    return hashlib.sha256(
        f"{CAMPAIGN}/{stage}/{attempt}".encode()).hexdigest()[:24]


def _empty() -> dict:
    return {"campaign": CAMPAIGN, "ceiling_usd": CEILING_USD,
            "reservations": {}}


def committed_usd(ledger: dict) -> float:
    total = 0.0
    for reservation in ledger["reservations"].values():
        if reservation["state"] == "cancelled":
            continue
        total += (reservation["actual_usd"]
                  if reservation["state"] == "reconciled"
                  else reservation["worst_case_usd"])
    return round(total, 4)


def remaining_usd(ledger: dict) -> float:
    return round(CEILING_USD - committed_usd(ledger), 4)


def unresolved(ledger: dict) -> list[str]:
    return [key for key, value in ledger["reservations"].items()
            if value["state"] == "reserved"]


def load(cli) -> tuple[dict, str | None]:
    from botocore.exceptions import ClientError
    try:
        obj = cli.get_object(Bucket=BUCKET, Key=LEDGER_KEY)
        ledger = json.loads(obj["Body"].read())
        if (ledger.get("campaign") != CAMPAIGN
                or ledger.get("ceiling_usd") != CEILING_USD):
            raise SystemExit("REFUSING: diagnostic ledger identity changed")
        return ledger, obj.get("ETag")
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404", "NotFound"):
            return _empty(), None
        raise SystemExit(
            f"REFUSING: diagnostic ledger is unreadable ({code})") from exc


def _put(cli, ledger: dict, etag: str | None) -> None:
    from botocore.exceptions import ClientError
    body = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode()
    args = {"Bucket": BUCKET, "Key": LEDGER_KEY, "Body": body,
            "ContentType": "application/json",
            "ServerSideEncryption": "aws:kms"}
    if etag is None:
        args["IfNoneMatch"] = "*"
    else:
        args["IfMatch"] = etag
    try:
        cli.put_object(**args)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in (
                "PreconditionFailed", "ConditionalRequestConflict"):
            raise SystemExit(
                "REFUSING: diagnostic ledger changed concurrently") from exc
        raise
    back, _ = load(cli)
    if back != ledger:
        raise SystemExit("REFUSING: diagnostic ledger readback differs")


def reserve(cli, stage: str, attempt: str) -> dict:
    ledger, etag = load(cli)
    rid = reservation_id(stage, attempt)
    if rid in ledger["reservations"]:
        return {"reservation_id": rid, "already_held": True,
                **ledger["reservations"][rid]}
    stale = unresolved(ledger)
    if stale:
        raise SystemExit(
            f"REFUSING: unresolved diagnostic reservation(s) {stale}")
    worst = worst_case_usd(stage)
    if committed_usd(ledger) + worst > CEILING_USD:
        raise SystemExit(
            f"REFUSING: {stage} would exceed diagnostic ceiling")
    ledger["reservations"][rid] = {
        "stage": stage, "attempt": attempt,
        "instance_type": STAGE_INSTANCE[stage],
        "watchdog_s": WATCHDOG_S[stage],
        "ec2_lifecycle_overhead_s": EC2_LIFECYCLE_OVERHEAD_S,
        "reserved_seconds": WATCHDOG_S[stage] + EC2_LIFECYCLE_OVERHEAD_S,
        "worst_case_usd": worst, "actual_usd": None,
        "state": "reserved",
        "reserved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _put(cli, ledger, etag)
    return {"reservation_id": rid, "already_held": False,
            **ledger["reservations"][rid],
            "committed_after": committed_usd(ledger),
            "remaining_after": remaining_usd(ledger)}


def reconcile(cli, stage: str, attempt: str, actual_seconds: float,
              instance_id: str | None = None) -> dict:
    ledger, etag = load(cli)
    rid = reservation_id(stage, attempt)
    reservation = ledger["reservations"].get(rid)
    if reservation is None:
        raise SystemExit("REFUSING: diagnostic instance had no reservation")
    if reservation["state"] == "reconciled":
        return {"reservation_id": rid, **reservation}
    actual = round(
        RATES[reservation["instance_type"]] * actual_seconds / 3600.0, 4)
    reservation.update({
        "state": "reconciled", "actual_seconds": round(actual_seconds, 1),
        "actual_usd": actual, "instance_id": instance_id,
        "reconciled_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    _put(cli, ledger, etag)
    return {"reservation_id": rid, **reservation,
            "committed_after": committed_usd(ledger),
            "remaining_after": remaining_usd(ledger)}


def cancel(cli, stage: str, attempt: str, why: str) -> dict:
    ledger, etag = load(cli)
    rid = reservation_id(stage, attempt)
    reservation = ledger["reservations"].get(rid)
    if reservation is None or reservation["state"] == "reconciled":
        raise SystemExit("REFUSING: diagnostic reservation cannot be cancelled")
    reservation.update({
        "state": "cancelled", "cancel_reason": why,
        "cancelled_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })
    _put(cli, ledger, etag)
    return {"reservation_id": rid, **reservation}
