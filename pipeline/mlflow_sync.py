"""Consistent MLflow snapshots, one immutable key per stage.

Two problems with what the failed run did.

It copied the live `mlflow.db` file. SQLite writes through a WAL and a
page cache; a byte copy taken while a writer is open can land mid-transaction
and produce a database that opens fine and is missing the last thing you cared
about. `sqlite3.Connection.backup()` takes a consistent snapshot through the
same locking the database uses for everything else.

And it wrote to one key, `mlflow/db/<run>/mlflow.db`, at the end. A run that
dies mid-training leaves either nothing or a partial overwrite of the only copy
there is. Here every stage gets its own immutable key, so an interrupted
campaign still has every snapshot up to the point it stopped, and the last
completed checkpoint is recoverable by listing.

Sync failure is fatal before the next stage. A stage whose evidence did not
persist has not really happened, and continuing would build on a record that
does not exist.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
from pathlib import Path

BUCKET = "medzen-speech"
CAMPAIGN = "b4-corrected"


def snapshot_prefix(campaign_run: str, attempt: str, stage: str) -> str:
    """ATTEMPT is part of the namespace.

    Retrying as attempt 2 must not overwrite attempt 1's evidence, and must not
    collide with `parent`, `base_eval` or any checkpoint key from the first
    try. Without this, a retry silently destroys the record of why the first
    attempt failed.
    """
    for part, name in ((campaign_run, "campaign_run"), (attempt, "attempt"),
                       (stage, "stage")):
        if not part or "/" in str(part):
            raise ValueError(f"{name} must be non-empty and contain no '/'")
    return f"mlflow/snapshots/{campaign_run}/attempt-{attempt}/{stage}/"


def snapshot_key(campaign_run: str, attempt: str, stage: str) -> str:
    """One immutable key per (campaign, attempt, stage)."""
    return snapshot_prefix(campaign_run, attempt, stage) + "mlflow.db"


def record_key(campaign_run: str, attempt: str, stage: str) -> str:
    """The immutable metadata written BESIDE every snapshot.

    Recovery must not depend on a mutable index: an index that failed to append
    would lose a stage that actually completed. Listing the immutable records
    reconstructs the truth from the objects themselves.
    """
    return snapshot_prefix(campaign_run, attempt, stage) + "record.json"


def consistent_snapshot(src: Path, dest: Path) -> None:
    """A snapshot taken through SQLite's own backup API, not a file copy."""
    con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        out = sqlite3.connect(dest)
        try:
            con.backup(out)
        finally:
            out.close()
    finally:
        con.close()


def sync(cli, db: Path, campaign_run: str, stage: str, attempt: str = "1",
         extra: dict | None = None) -> dict:
    """Snapshot, upload to an immutable key, verify by READ-BACK.

    Refuses if the key already exists: a stage that runs twice gets a new
    attempt name, never a silent overwrite of the earlier evidence.
    """
    from botocore.exceptions import ClientError

    key = snapshot_key(campaign_run, attempt, stage)
    with tempfile.TemporaryDirectory() as td:
        snap = Path(td) / "snapshot.db"
        consistent_snapshot(db, snap)
        body = snap.read_bytes()
    digest = hashlib.sha256(body).hexdigest()

    try:
        cli.put_object(Bucket=BUCKET, Key=key, Body=body,
                       ContentType="application/x-sqlite3", IfNoneMatch="*")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("PreconditionFailed", "ConditionalRequestConflict"):
            raise SystemExit(
                f"REFUSING: {key} already exists. Stage snapshots are "
                "write-once; overwriting would destroy the evidence for a "
                "stage that already ran.")
        raise SystemExit(f"REFUSING: MLflow snapshot upload failed ({code}). "
                         "A stage whose evidence did not persist has not "
                         "happened; the next stage must not start.")

    back = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    got = hashlib.sha256(back).hexdigest()
    if got != digest:
        raise SystemExit(
            f"REFUSING: snapshot read-back hashes {got[:16]}, expected "
            f"{digest[:16]}. The snapshot is not durable.")

    rec = {"campaign_run": campaign_run, "attempt": attempt, "stage": stage,
           "key": f"s3://{BUCKET}/{key}", "sha256": digest,
           "bytes": len(body),
           "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           # Ordering key. `utc` has second resolution, and stages routinely
           # complete inside the same second -- sorting on it returned an
           # arbitrary "last completed stage", which is the one thing recovery
           # exists to answer.
           "seq_ns": time.time_ns(),
           **(extra or {})}
    # An IMMUTABLE record beside the snapshot. Recovery reads these by listing,
    # so a lost index append cannot erase a stage that really happened.
    cli.put_object(Bucket=BUCKET,
                   Key=record_key(campaign_run, attempt, stage),
                   Body=(json.dumps(rec, indent=2, sort_keys=True) + "\n").encode(),
                   ContentType="application/json", IfNoneMatch="*")
    return rec


def recover(cli, campaign_run: str, attempt: str = "1") -> dict:
    """What survived an interruption: every stage snapshot, in order.

    An interrupted campaign is not a lost one. The last completed stage is the
    last entry here, and the run's failure state is whatever the trainer wrote
    before it stopped -- both readable without the instance.
    """
    prefix = f"mlflow/snapshots/{campaign_run}/attempt-{attempt}/"
    keys = []
    tok = None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": prefix}
        if tok:
            kw["ContinuationToken"] = tok
        page = cli.list_objects_v2(**kw)
        keys += [o["Key"] for o in page.get("Contents", [])
                 if o["Key"].endswith("/record.json")]
        if not page.get("IsTruncated"):
            break
        tok = page.get("NextContinuationToken")
    stages = sorted(
        (json.loads(cli.get_object(Bucket=BUCKET, Key=k)["Body"].read())
         for k in keys), key=lambda r: (r.get("seq_ns", 0), r["utc"]))
    last = stages[-1]["stage"] if stages else None
    terminal = last in {
        "campaign-failed", "final-complete", "final-failed",
        "final-selected"}
    return {
        "campaign_run": campaign_run,
        "attempt": attempt,
        "stages": stages,
        "stage_names": [s["stage"] for s in stages],
        "last_completed_stage": last,
        "last_snapshot_sha256": stages[-1]["sha256"] if stages else None,
        "interrupted": not terminal,
    }


# --------------------------------------------------------------------------- #
# run structure
# --------------------------------------------------------------------------- #
def stage_names(lr_candidates, checkpoints) -> list[str]:
    """The exact stage sequence, used for both syncing and recovery."""
    return (["parent", "base_eval"]
            + [f"sweep-lr-{lr:.0e}" for lr in lr_candidates]
            + ["selection", "final-start"]
            + [f"final-checkpoint-{s}" for s in checkpoints]
            + ["final-complete"])
