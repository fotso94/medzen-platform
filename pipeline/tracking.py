"""MLflow tracking for B4.

Starts as a local file store so training is never blocked on standing up a
server. MLFLOW_TRACKING_URI overrides it, so moving to a real server (or
SageMaker managed MLflow) later is an env var, not a code change.

Every run records the A5 reproducibility set: dataset version + checksum,
git SHA, base model revision, hyperparameters, and the resulting metrics.
A run missing any of these cannot support a promotion decision.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# SQLite, not a file store: MLflow 3.x put the filesystem backend in
# maintenance mode, and the Model Registry that B5 needs requires a database
# backend regardless. Still a single local file - no server to operate.
DEFAULT_URI = f"sqlite:///{ROOT / 'mlflow.db'}"

# Artifacts go to S3 by default. On a spot instance the local disk disappears
# with the box, so anything only on local disk is lost the moment training is
# reclaimed - including the record of the run that produced a candidate.
BUCKET = os.environ.get("MEDZEN_BUCKET", "medzen-speech")
ARTIFACT_ROOT = os.environ.get("MLFLOW_ARTIFACT_ROOT", f"s3://{BUCKET}/mlflow/artifacts")


def push_tracking_db(run_id: str | None = None) -> str | None:
    """Copy the SQLite tracking DB to S3.

    The DB itself is a local file; without this the run metadata dies with the
    instance even though the artifacts survived. Called at run end and after
    each checkpoint, so an interrupted run still leaves a queryable record.
    """
    db = ROOT / "mlflow.db"
    if not db.exists():
        return None
    import boto3
    # Run-specific key. A shared "latest" is a lost-update race the moment two
    # runs overlap, and it destroys the ability to recover the DB for one run.
    rid = run_id or os.environ.get("MEDZEN_RUN_ID")
    if not rid:
        try:
            import mlflow
            active = mlflow.active_run()
            rid = active.info.run_id if active else None
        except Exception:
            rid = None
    if not rid:
        rid = "unattributed"
    key = f"mlflow/db/{rid}/mlflow.db"
    sess = (boto3.Session(profile_name=os.environ["AWS_PROFILE"])
            if os.environ.get("AWS_PROFILE") else boto3.Session())
    sess.client("s3", region_name=os.environ.get("AWS_REGION", "eu-central-1")) \
        .upload_file(str(db), BUCKET, key)
    return f"s3://{BUCKET}/{key}"


def tracking_uri() -> str:
    return os.environ.get("MLFLOW_TRACKING_URI", DEFAULT_URI)


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def git_dirty() -> bool:
    try:
        out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                             capture_output=True, text=True).stdout.strip()
        return bool(out)
    except Exception:
        return True


def manifest_fingerprint(manifests: list[dict]) -> str:
    """Checksum the exact rows trained on. Two runs quoting the same dataset
    version but different fingerprints did not train on the same data."""
    h = hashlib.sha256()
    for m in manifests:
        h.update(m["audio_checksum_sha256"].encode())
    return h.hexdigest()


def start_run(experiment: str, run_name: str, params: dict, tags: dict | None = None):
    import mlflow
    mlflow.set_tracking_uri(tracking_uri())
    if mlflow.get_experiment_by_name(experiment) is None:
        mlflow.create_experiment(experiment, artifact_location=ARTIFACT_ROOT)
    mlflow.set_experiment(experiment)
    run = mlflow.start_run(run_name=run_name)
    base = {
        "git_sha": git_sha(),
        "git_dirty": str(git_dirty()),
        "reproducible": str(not git_dirty()),
    }
    mlflow.set_tags({**base, **(tags or {})})
    # params must be flat for MLflow; nest into JSON where needed
    flat = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v)
            for k, v in params.items()}
    mlflow.log_params(flat)
    return run
