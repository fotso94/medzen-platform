"""Worst-case spend RESERVED before launch, reconciled after termination.

Recording spend after a stage finishes cannot bound anything: the process that
would do the recording is the one that dies. A crash between `run-instances`
and the ledger write leaves an instance burning money that no ledger knows
about, and the next pre-launch check happily authorises another.

So the worst case is reserved BEFORE the instance exists and only reduced to
actual once termination is confirmed. A crash after launch leaves the full
worst case counted, which is the conservative direction: the budget shrinks by
more than was really spent until someone reconciles it.

Writes are conditional. Two operators running this concurrently would otherwise
read-modify-write over each other and both see room for one more instance.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

BUCKET = "medzen-speech"
# A new dataset fingerprint is a new cost decision.  Keep the reconciled
# b4-corrected ledger immutable and account the language-scoped continuation
# under a fresh ceiling rather than pretending the earlier spend never
# happened or making a clean scoped run impossible under the old ledger.
CAMPAIGN = "b4-scoped"
LEDGER_KEY = f"candidates/budget/{CAMPAIGN}/ledger.json"

RATES = {"g6.xlarge": 1.0064, "c6i.2xlarge": 0.34}
# STAGE TOPOLOGY — three GPU instances plus one builder.
#
#   builder             c6i.2xlarge   image build
#   base_and_preflight  g6.xlarge     base arm on 6 sets, THEN overfit+smoke
#   sweep_run x1        g6.xlarge     100 steps + 6-set evaluation
#   final_run           g6.xlarge     600 steps with interleaved checkpoints
#
# Base evaluation and preflight share ONE instance: both need the pinned base
# loaded and preflight builds a fresh LoRA on it. One instance, one
# reservation, reconciled once when both have finished.
WATCHDOG_S = {"builder": 1800, "base_and_preflight": 2000,
              "sweep_run": 2000, "final_run": 6600}
# The watchdog starts inside the launched instance, while AWS billing starts
# earlier and ends only after EC2 reaches `terminated`.  Attempt 5 measured
# 540--570 seconds outside the container on every GPU stage.  Reserving only
# WATCHDOG_S therefore understated the amount that a hung stage could bill.
# Keep the watchdog unchanged, but reserve a conservative ten-minute EC2
# lifecycle envelope for boot, image pull and termination on every instance.
EC2_LIFECYCLE_OVERHEAD_S = 600
STAGE_INSTANCE = {"builder": "c6i.2xlarge", "base_and_preflight": "g6.xlarge",
                  "sweep_run": "g6.xlarge", "final_run": "g6.xlarge"}
MAX_GPU_INSTANCES = 3
MAX_INSTANCES = 4

# The ceiling must cover the whole sequence hanging to its watchdogs plus the
# measured EC2 lifecycle envelope:
#   0.227 + 0.839 + 0.839 + 2.013 = 3.917
# An earlier table used a 10800s final watchdog, which made the worst-case
# sequence $6.21 -- over its own $6 ceiling.  After three fail-closed campaign
# attempts, the final watchdog is 6600s: still 20 minutes beyond the
# predeclared ~90-minute expected run.  After attempt 4, observed
# base+preflight work still left 11 minutes inside a 2400s boundary; sweeps do
# fewer optimisation steps against the same validation surface.  The last
# corrected base lifecycle reconciled at 1354.8 seconds INCLUDING boot and
# termination, and the 1e-4 sweep at 2130 seconds including the separately
# reserved 600-second lifecycle envelope.  A 2000-second in-instance watchdog
# therefore retains measured margin without removing any work or gate.  The
# first scoped attempt reconciled at
# $2.876 and failed closed on a scheduler-horizon mismatch.  The platform
# owner explicitly authorised a $9 cumulative ceiling on 2026-07-31 so the
# corrected full sweep.  That run reconciled at $5.1846 and found 1e-4 to be
# the only configuration compatible with the retained six-language validation
# surface after Acholi is deferred.  The targeted continuation therefore uses
# one fresh 1e-4 confirmation and one final run.  After an unreserved launch
# was terminated and reconciled, its complete GPU worst case is $3.4664.
# The builder is reserved and reconciled first; if its actual cost leaves less
# than the complete GPU worst case, the GPU campaign refuses before launch.
# This remains an extension of the same durable ledger, never a spend reset.
CEILING_USD = 9.00


def worst_case_usd(stage: str) -> float:
    if stage not in WATCHDOG_S:
        raise ValueError(f"unknown stage {stage!r}")
    reserved_seconds = WATCHDOG_S[stage] + EC2_LIFECYCLE_OVERHEAD_S
    return round(
        RATES[STAGE_INSTANCE[stage]] * reserved_seconds / 3600.0, 4)


def reservation_id(stage: str, attempt: str) -> str:
    """Idempotent: the same stage and attempt always yield the same id, so a
    retry after an ambiguous failure re-reserves the same slot instead of
    double-counting it."""
    return hashlib.sha256(f"{CAMPAIGN}/{stage}/{attempt}".encode()).hexdigest()[:24]


def _empty() -> dict:
    return {"campaign": CAMPAIGN, "ceiling_usd": CEILING_USD, "reservations": {}}


def committed_usd(ledger: dict) -> float:
    """Reserved-but-unreconciled counts at WORST CASE; reconciled at actual."""
    total = 0.0
    for r in ledger["reservations"].values():
        if r["state"] == "cancelled":
            continue
        total += r["actual_usd"] if r["state"] == "reconciled" else r["worst_case_usd"]
    return round(total, 4)


def remaining_usd(ledger: dict) -> float:
    return round(CEILING_USD - committed_usd(ledger), 4)


def unresolved(ledger: dict) -> list[str]:
    return [k for k, r in ledger["reservations"].items() if r["state"] == "reserved"]


# --------------------------------------------------------------------------- #
# durable IO, conditional
# --------------------------------------------------------------------------- #
def load(cli) -> tuple[dict, str | None]:
    """Return (ledger, etag). An unreadable ledger REFUSES -- it is not empty."""
    from botocore.exceptions import ClientError
    try:
        o = cli.get_object(Bucket=BUCKET, Key=LEDGER_KEY)
        ledger = json.loads(o["Body"].read())
        if (ledger.get("campaign") != CAMPAIGN
                or ledger.get("ceiling_usd") != CEILING_USD):
            raise SystemExit(
                "REFUSING: campaign ledger identity or authorised ceiling "
                "differs from this executable")
        return ledger, o.get("ETag")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404", "NotFound"):
            return _empty(), None
        raise SystemExit(
            f"REFUSING: cannot read the spend ledger ({code}). An unreadable "
            "ledger is not an empty one, and proceeding would restart the "
            "budget at zero.")


def _put(cli, ledger: dict, etag: str | None) -> None:
    """Conditional write, then READ BACK. A put that returns 200 has not been
    verified; the reservation only counts once it can be read."""
    from botocore.exceptions import ClientError
    body = (json.dumps(ledger, indent=2, sort_keys=True) + "\n").encode()
    kw = {"Bucket": BUCKET, "Key": LEDGER_KEY, "Body": body,
          "ContentType": "application/json"}
    kw["IfMatch"] = etag if etag else None
    if etag is None:
        kw.pop("IfMatch")
        kw["IfNoneMatch"] = "*"
    try:
        cli.put_object(**kw)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("PreconditionFailed", "ConditionalRequestConflict"):
            raise SystemExit(
                "REFUSING: the ledger changed under this operation. Another "
                "operator is running, and two concurrent read-modify-writes "
                "would each see room for one more instance.")
        raise
    back, _ = load(cli)
    if back != ledger:
        raise SystemExit("REFUSING: ledger read-back differs from what was "
                         "written; the reservation is not durable.")


def reserve(cli, stage: str, attempt: str) -> dict:
    """Reserve the worst case BEFORE launching. Refuses if it would breach the
    ceiling, or if any earlier reservation is still unresolved."""
    ledger, etag = load(cli)
    rid = reservation_id(stage, attempt)

    existing = ledger["reservations"].get(rid)
    if existing:
        # Idempotency is valid only while the SAME lifecycle is still covered
        # by its worst-case hold. A reconciled or cancelled slot is terminal:
        # reusing it would launch a new instance while the ledger continued to
        # count the old actual (or zero), which is an unreserved launch.
        if existing["state"] == "reserved":
            return {"reservation_id": rid, "already_held": True, **existing}
        raise SystemExit(
            f"REFUSING: reservation {rid} for {stage}/{attempt} is already "
            f"{existing['state']}. A completed reservation cannot authorise "
            "a new instance; use a campaign-scoped unique attempt key.")

    stale = unresolved(ledger)
    if stale:
        raise SystemExit(
            f"REFUSING: {len(stale)} reservation(s) are still unresolved "
            f"({stale[:3]}). An unresolved reservation means an instance may "
            "still be running or may have died without reconciliation; "
            "launching another would spend against a budget nobody has "
            "verified.")

    wc = worst_case_usd(stage)
    if committed_usd(ledger) + wc > CEILING_USD:
        raise SystemExit(
            f"REFUSING to launch {stage}: worst case ${wc:.2f} on top of "
            f"${committed_usd(ledger):.2f} committed would reach "
            f"${committed_usd(ledger) + wc:.2f}, over the ${CEILING_USD:.2f} "
            "ceiling. A launch that cannot afford to fail must not start.")

    ledger["reservations"][rid] = {
        "stage": stage, "attempt": attempt,
        "instance_type": STAGE_INSTANCE[stage],
        "watchdog_s": WATCHDOG_S[stage],
        "ec2_lifecycle_overhead_s": EC2_LIFECYCLE_OVERHEAD_S,
        "reserved_seconds": WATCHDOG_S[stage] + EC2_LIFECYCLE_OVERHEAD_S,
        "worst_case_usd": wc, "actual_usd": None,
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
    """After confirmed termination, replace the worst case with the actual."""
    ledger, etag = load(cli)
    rid = reservation_id(stage, attempt)
    r = ledger["reservations"].get(rid)
    if r is None:
        raise SystemExit(f"REFUSING: no reservation {rid} for {stage}/{attempt}; "
                         "an instance ran without one.")
    if r["state"] == "reconciled":
        return {"reservation_id": rid, **r}
    actual = round(RATES[r["instance_type"]] * actual_seconds / 3600.0, 4)
    r.update({"state": "reconciled", "actual_seconds": round(actual_seconds, 1),
              "actual_usd": actual, "instance_id": instance_id,
              "reconciled_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    _put(cli, ledger, etag)
    return {"reservation_id": rid, **r,
            "committed_after": committed_usd(ledger),
            "remaining_after": remaining_usd(ledger)}


def cancel(cli, stage: str, attempt: str, why: str) -> dict:
    """Release a reservation for an instance that provably never launched."""
    ledger, etag = load(cli)
    rid = reservation_id(stage, attempt)
    r = ledger["reservations"].get(rid)
    if r is None:
        raise SystemExit(f"REFUSING: no reservation {rid} to cancel")
    if r["state"] == "reconciled":
        raise SystemExit("REFUSING: cannot cancel a reconciled reservation")
    r.update({"state": "cancelled", "cancel_reason": why,
              "cancelled_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    _put(cli, ledger, etag)
    return {"reservation_id": rid, **r}
