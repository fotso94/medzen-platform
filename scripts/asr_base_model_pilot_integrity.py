#!/usr/bin/env python3
"""Unconditional source and governance integrity gates for the ASR pilot."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from scripts.asr_external_tool import run_external


LEGACY_EXECUTOR_MODULE_PATHS = (
    "pipeline/asr_base_model_pilot_receipts.py",
    "scripts/asr_base_model_pilot_assets.py",
    "scripts/asr_base_model_ecr_scanning.py",
    "scripts/asr_base_model_deadline_dry_run.py",
    "scripts/asr_base_model_pilot_integrity.py",
    "scripts/asr_base_model_pilot_k8s.py",
    "scripts/asr_base_model_pilot_live.py",
    "scripts/asr_base_model_pilot_plan.py",
    "scripts/asr_base_model_pilot_runner.py",
    "scripts/asr_eval_digest_rescan.py",
    "scripts/asr_eval_oci_publication.py",
)
EXECUTOR_MODULE_PATHS = (
    *LEGACY_EXECUTOR_MODULE_PATHS[:9],
    "scripts/asr_external_tool.py",
    *LEGACY_EXECUTOR_MODULE_PATHS[9:10],
    "scripts/asr_eval_scout_preflight.py",
    *LEGACY_EXECUTOR_MODULE_PATHS[10:],
)
ATTEMPT_9_EXECUTOR_MODULE_PATHS = (
    *EXECUTOR_MODULE_PATHS[:9],
    "scripts/asr_base_model_pilot_staging.py",
    *EXECUTOR_MODULE_PATHS[9:],
)


class PilotIntegrityRefusal(RuntimeError):
    def __init__(self, reason_code: str, detail: str):
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_committed_artifact(root: Path, path: Path) -> bytes:
    """Read an artifact only when its worktree bytes equal the committed bytes."""
    try:
        relative = str(path.resolve().relative_to(root.resolve()))
    except ValueError as exc:
        raise PilotIntegrityRefusal(
            "COMMITTED_ARTIFACT_OUTSIDE_REPOSITORY",
            "committed artifact is outside the reviewed repository",
        ) from exc
    if not path.is_file():
        raise PilotIntegrityRefusal(
            "COMMITTED_ARTIFACT_ABSENT",
            f"committed artifact is absent: {relative}",
        )
    completed, _ = run_external(
        ["git", "show", f"HEAD:{relative}"],
        cwd=root,
        timeout=60,
    )
    if completed.returncode != 0:
        raise PilotIntegrityRefusal(
            "COMMITTED_ARTIFACT_UNTRACKED",
            f"artifact is not committed at execution HEAD: {relative}",
        )
    body = path.read_bytes()
    if body != completed.stdout:
        raise PilotIntegrityRefusal(
            "COMMITTED_ARTIFACT_BYTES_DIFFER",
            f"worktree artifact differs from committed bytes: {relative}",
        )
    return body


def validate_executor_module_bindings(
    root: Path,
    bindings: Any,
) -> dict[str, Any]:
    """Require a complete, exact hash map for every live executor module."""
    allowed = EXECUTOR_MODULE_PATHS
    if isinstance(bindings, dict):
        for candidate in (
            LEGACY_EXECUTOR_MODULE_PATHS,
            EXECUTOR_MODULE_PATHS,
            ATTEMPT_9_EXECUTOR_MODULE_PATHS,
        ):
            if set(bindings) == set(candidate):
                allowed = candidate
                break
    if not isinstance(bindings, dict) or set(bindings) != set(allowed):
        raise PilotIntegrityRefusal(
            "EXECUTOR_MODULE_SET_DIFFERS",
            "executor module binding set is missing, extra or ambiguous",
        )
    measured: dict[str, str] = {}
    for relative in allowed:
        expected = bindings.get(relative)
        path = root / relative
        if not isinstance(expected, str) or len(expected) != 64 or not path.is_file():
            raise PilotIntegrityRefusal(
                "EXECUTOR_MODULE_BINDING_MALFORMED",
                f"executor module binding is malformed: {relative}",
            )
        actual = sha256_file(path)
        if actual != expected:
            raise PilotIntegrityRefusal(
                "EXECUTOR_SOURCE_HASH_DIFFERS",
                f"reviewed executor source hash differs: {relative}",
            )
        measured[relative] = actual
    return {
        "status": "PASS_ALL_EXECUTOR_MODULE_HASHES",
        "module_count": len(measured),
        "module_sha256": measured,
        "conditional_hash_omissions_permitted": False,
    }


def _git(root: Path, *arguments: str) -> str:
    completed, _ = run_external(
        ["git", *arguments],
        cwd=root,
        text=True,
        timeout=60,
    )
    if completed.returncode != 0:
        raise PilotIntegrityRefusal(
            "REPOSITORY_HISTORY_UNREADABLE",
            "repository history cannot be verified",
        )
    return completed.stdout


def validate_governance_commit_boundary(
    root: Path,
    *,
    reviewed_commit: str,
    authorization_path: Path,
    deadline_dry_run_path: Path,
) -> dict[str, Any]:
    """Allow only the reviewed authorization and dry-run receipt after review."""
    head = _git(root, "rev-parse", "HEAD").strip()
    ancestor, _ = run_external(
        ["git", "merge-base", "--is-ancestor", reviewed_commit, head],
        cwd=root,
        timeout=60,
    )
    if ancestor.returncode != 0:
        raise PilotIntegrityRefusal(
            "REVIEWED_COMMIT_NOT_ANCESTOR",
            "reviewed packet commit is not an ancestor of execution HEAD",
        )
    dirty = _git(root, "status", "--porcelain=v1")
    if dirty:
        raise PilotIntegrityRefusal(
            "REVIEWED_CLEAN_COMMIT_REQUIRED",
            "execution requires a clean worktree",
        )
    allowed = {
        str(authorization_path.resolve().relative_to(root.resolve())),
        str(deadline_dry_run_path.resolve().relative_to(root.resolve())),
    }
    changed = {
        line
        for line in _git(root, "diff", "--name-only", f"{reviewed_commit}..{head}").splitlines()
        if line
    }
    unexpected = sorted(changed - allowed)
    if unexpected:
        raise PilotIntegrityRefusal(
            "POST_REVIEW_PATH_DRIFT",
            "post-review commit changed a non-governance path",
        )
    return {
        "status": "PASS_REVIEWED_COMMIT_LINEAGE",
        "reviewed_commit": reviewed_commit,
        "execution_head": head,
        "allowed_post_review_paths": sorted(allowed),
        "observed_post_review_paths": sorted(changed),
        "worktree_clean": True,
    }
