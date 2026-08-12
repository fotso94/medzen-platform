#!/usr/bin/env python3
"""Run the complete stage-1 gate against committed successor artifacts at $0."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.asr_base_model_pilot_receipts import ReceiptStore, canonical_json, write_exclusive
from scripts.asr_base_model_pilot_live import CALLER, LiveOperations
from scripts.asr_base_model_pilot_integrity import read_committed_artifact
from scripts.asr_base_model_pilot_runner import AttemptContext, stage_deadline_identity_and_acceptance


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=True
    ).stdout.strip()


def dry_run(
    *,
    root: Path,
    bindings_path: Path,
    authorization_path: Path,
    packet_path: Path,
    attempt: int,
    output: Path,
    operations_factory: Any = LiveOperations,
) -> dict[str, Any]:
    if _git(root, "status", "--porcelain=v1"):
        raise RuntimeError("deadline dry run requires a clean worktree")
    head = _git(root, "rev-parse", "HEAD")
    bindings_body = read_committed_artifact(root, bindings_path)
    authorization_body = read_committed_artifact(root, authorization_path)
    packet_body = read_committed_artifact(root, packet_path)
    bindings = json.loads(bindings_body)
    authorization = json.loads(authorization_body)
    expected_paths = {
        "authorization": bindings["authorization"]["path"],
        "packet": bindings["successor_packet"]["path"],
    }
    observed_paths = {
        "authorization": str(authorization_path.relative_to(root)),
        "packet": str(packet_path.relative_to(root)),
    }
    if observed_paths != expected_paths:
        raise RuntimeError("deadline dry run artifact paths differ from the committed bindings")
    if authorization.get("reviewed_repository_commit") != authorization.get(
        "independent_review", {}
    ).get("reviewed_repository_commit"):
        raise RuntimeError("authorization reviewed commit is absent or ambiguous")
    packet_sha = hashlib.sha256(packet_body).hexdigest()
    authorization_sha = hashlib.sha256(authorization_body).hexdigest()
    bindings_sha = hashlib.sha256(bindings_body).hexdigest()
    context = AttemptContext(
        attempt=attempt,
        bindings=bindings,
        receipts=ReceiptStore(
            output.parent / ".dry-run-unused-receipts",
            packet_sha256=packet_sha,
            authorization_sha256=authorization_sha,
        ),
        workdir=output.parent / ".dry-run-unused-workdir",
        authorization_path=authorization_path,
        packet_path=packet_path,
        bindings_sha256=bindings_sha,
    )
    result = stage_deadline_identity_and_acceptance(
        operations_factory(root),
        context,
        dry_run=True,
        caller_arn=CALLER,
    )
    if result.get("aws_calls") != 0 or result.get("aws_mutations") != 0:
        raise RuntimeError("deadline dry run did not remain read-only")
    receipt = {
        "schema_version": 1,
        "record": "ASR_BASE_MODEL_COMMITTED_DEADLINE_IDENTITY_DRY_RUN",
        "id": bindings["authorization"]["deadline_dry_run_id"],
        "status": "PASS_COMMITTED_DEADLINE_IDENTITY_DRY_RUN",
        "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "attempt": attempt,
        "execution_head": head,
        "packet_path": str(packet_path.relative_to(root)),
        "packet_sha256": packet_sha,
        "authorization_path": str(authorization_path.relative_to(root)),
        "authorization_sha256": authorization_sha,
        "bindings_path": str(bindings_path.relative_to(root)),
        "bindings_sha256": bindings_sha,
        "committed_artifacts": {
            "authorization": {"path": observed_paths["authorization"], "sha256": authorization_sha},
            "bindings": {
                "path": str(bindings_path.relative_to(root)),
                "sha256": bindings_sha,
            },
            "packet": {"path": observed_paths["packet"], "sha256": packet_sha},
        },
        "result": result,
        "execution": {
            "aws_calls": 0,
            "aws_mutations": 0,
            "kubectl_calls": 0,
            "attempt_started": False,
            "gpu_started": False,
        },
    }
    write_exclusive(output, canonical_json(receipt))
    return {**receipt, "sha256": _sha(output)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--authorization", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = dry_run(
            root=args.root.resolve(),
            bindings_path=args.bindings.resolve(),
            authorization_path=args.authorization.resolve(),
            packet_path=args.packet.resolve(),
            attempt=args.attempt,
            output=args.output.resolve(),
        )
    except Exception as exc:
        print(json.dumps({"status": "REFUSED", "reason_code": getattr(exc, "reason_code", type(exc).__name__)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
