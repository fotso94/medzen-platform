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


def snapshot_key(campaign_run: str, stage: str) -> str:
    """One immutable key per stage. Never `mlflow/db/<run>/mlflow.db`."""
    if "/" in stage or "/" in campaign_run:
        raise ValueError("stage and campaign_run must not contain '/'")
    return f"mlflow/snapshots/{campaign_run}/{stage}/mlflow.db"


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


def sync(cli, db: Path, campaign_run: str, stage: str,
         extra: dict | None = None) -> dict:
    """Snapshot, upload to an immutable key, verify by READ-BACK.

    Refuses if the key already exists: a stage that runs twice gets a new
    attempt name, never a silent overwrite of the earlier evidence.
    """
    from botocore.exceptions import ClientError

    key = snapshot_key(campaign_run, stage)
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

    rec = {"campaign_run": campaign_run, "stage": stage,
           "key": f"s3://{BUCKET}/{key}", "sha256": digest,
           "bytes": len(body),
           "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           **(extra or {})}
    idx = f"mlflow/snapshots/{campaign_run}/index.jsonl"
    try:
        prev = cli.get_object(Bucket=BUCKET, Key=idx)["Body"].read()
    except ClientError:
        prev = b""
    cli.put_object(Bucket=BUCKET, Key=idx,
                   Body=prev + (json.dumps(rec) + "\n").encode(),
                   ContentType="application/x-ndjson")
    return rec


def recover(cli, campaign_run: str) -> dict:
    """What survived an interruption: every stage snapshot, in order.

    An interrupted campaign is not a lost one. The last completed stage is the
    last entry here, and the run's failure state is whatever the trainer wrote
    before it stopped -- both readable without the instance.
    """
    from botocore.exceptions import ClientError
    idx = f"mlflow/snapshots/{campaign_run}/index.jsonl"
    try:
        raw = cli.get_object(Bucket=BUCKET, Key=idx)["Body"].read()
    except ClientError:
        return {"campaign_run": campaign_run, "stages": [],
                "last_completed_stage": None, "interrupted": True}
    stages = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    return {
        "campaign_run": campaign_run,
        "stages": stages,
        "stage_names": [s["stage"] for s in stages],
        "last_completed_stage": stages[-1]["stage"] if stages else None,
        "last_snapshot_sha256": stages[-1]["sha256"] if stages else None,
        "interrupted": True,
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
