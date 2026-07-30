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
ARTIFACT_ROOT = str(ROOT / "mlruns")


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
