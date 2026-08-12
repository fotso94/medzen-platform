#!/usr/bin/env python3
"""Read-only qualification of the packet-002D digest-rescan gate."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.asr_eval_digest_rescan import (
    DigestRescanRefusal,
    scan_exact_ecr_child,
    validate_security_binding,
)
from scripts.asr_external_tool import run_external


ACCOUNT = "558069890522"
REPOSITORY = "medzen-asr-eval-runtime"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"


def _write_once(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(body)


def _git_head_and_clean(root: Path) -> str:
    head_result, head_diagnostic = run_external(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True, timeout=60
    )
    status_result, status_diagnostic = run_external(
        ["git", "status", "--porcelain"], cwd=root, text=True, timeout=60
    )
    if head_result.returncode != 0 or status_result.returncode != 0:
        raise DigestRescanRefusal(
            "QUALIFICATION_REPOSITORY_UNREADABLE",
            f"qualification git prerequisite refused: {head_diagnostic} {status_diagnostic}",
        )
    head = head_result.stdout.strip()
    status = status_result.stdout
    if status:
        raise DigestRescanRefusal("QUALIFICATION_WORKTREE_DIRTY", "qualification requires a clean worktree")
    return head


def qualify(
    *,
    root: Path,
    bindings_path: Path,
    output: Path,
    sarif_output: Path,
    profile: str,
    region: str,
) -> dict[str, Any]:
    import boto3

    source_commit = _git_head_and_clean(root)
    bindings = json.loads(bindings_path.read_bytes())
    session = boto3.Session(profile_name=profile, region_name=region)
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != ACCOUNT:
        raise DigestRescanRefusal("QUALIFICATION_ACCOUNT_DIFFERS", "AWS account differs")
    ecr = session.client("ecr")
    gate_binding = validate_security_binding(bindings.get("security_gate", {}))
    before = ecr.get_registry_scanning_configuration().get("scanningConfiguration", {})
    with tempfile.TemporaryDirectory(prefix="medzen-ecr-digest-rescan-") as temporary:
        result = scan_exact_ecr_child(
            ecr,
            REPOSITORY,
            bindings["image"],
            Path(temporary) / "scan",
        )
        sarif = Path(result["docker_scout"]["scanned_oci_layout"]).parent / "docker-scout.sarif.json"
        sarif_body = sarif.read_bytes()
        _write_once(sarif_output, sarif_body)
    after = ecr.get_registry_scanning_configuration().get("scanningConfiguration", {})
    if _canonical(before) != _canonical(after):
        raise DigestRescanRefusal(
            "QUALIFICATION_REGISTRY_CONFIGURATION_DRIFT",
            "registry scanning configuration changed during read-only qualification",
        )
    result["docker_scout"].pop("scanned_oci_layout", None)
    result["docker_scout"]["sarif_path"] = str(sarif_output.relative_to(root))
    result["docker_scout"]["sarif_sha256"] = hashlib.sha256(sarif_body).hexdigest()
    receipt = {
        "record": "ASR_EVAL_RUNTIME_ECR_DIGEST_RESCAN_QUALIFICATION",
        "id": "ASR-EVAL-RUNTIME-ECR-DIGEST-RESCAN-QUALIFICATION-2026-001",
        "status": "PASS_READ_ONLY_EXACT_ECR_DIGEST_RESCAN",
        "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": "Read-only reconstruction and dual scan of the immutable offline evaluation image already published by attempt 4.",
        "source_commit": source_commit,
        "bindings": {
            "path": str(bindings_path.relative_to(root)),
            "sha256": _sha(bindings_path),
        },
        "identity": {
            "account": identity["Account"],
            "arn": identity["Arn"],
            "region": region,
        },
        "image": bindings["image"],
        "security_gate": result,
        "security_gate_binding": gate_binding,
        "registry_scanning_configuration": {
            "before_sha256": hashlib.sha256(_canonical(before)).hexdigest(),
            "after_sha256": hashlib.sha256(_canonical(after)).hexdigest(),
            "unchanged": True,
            "inspector_enhanced_scanning_enabled": False,
        },
        "execution": {
            "aws_reads": True,
            "aws_mutations": 0,
            "image_uploads": 0,
            "gpu_started": False,
            "model_or_audio_executed": False,
            "production_touched": False,
        },
    }
    _write_once(output, _canonical(receipt))
    return {**receipt, "sha256": _sha(output)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sarif-output", type=Path, required=True)
    parser.add_argument("--profile", default="medzen")
    parser.add_argument("--region", default="eu-central-1")
    args = parser.parse_args()
    try:
        result = qualify(
            root=args.root.resolve(),
            bindings_path=args.bindings.resolve(),
            output=args.output.resolve(),
            sarif_output=args.sarif_output.resolve(),
            profile=args.profile,
            region=args.region,
        )
    except Exception as exc:
        reason = getattr(exc, "reason_code", type(exc).__name__)
        print(json.dumps({"status": "REFUSED", "reason_code": reason}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
