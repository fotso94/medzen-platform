#!/usr/bin/env python3
"""Run the actual pinned Scout command against the exact local pilot image at $0."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.asr_base_model_pilot_receipts import canonical_json, write_exclusive
from scripts.asr_external_tool import configure_external_tool_journal, run_external
from scripts.asr_base_model_pilot_integrity import read_committed_artifact
from scripts.asr_eval_digest_rescan import (
    DigestRescanRefusal,
    scan_archive_with_scout,
    validate_scout_prerequisites,
)
from scripts.asr_eval_oci_publication import OciLayout, export_exact_image, extract_oci_archive


def _sha(path: Path) -> str:
    measured = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            measured.update(block)
    return measured.hexdigest()


def _git(root: Path, *arguments: str) -> str:
    completed, diagnostic = run_external(
        ["git", *arguments], cwd=root, text=True, timeout=60
    )
    if completed.returncode != 0:
        raise DigestRescanRefusal(
            "SCOUT_PREFLIGHT_REPOSITORY_UNREADABLE",
            f"committed repository prerequisite refused: {diagnostic}",
        )
    return completed.stdout.strip()


def qualify(
    *,
    root: Path,
    bindings_path: Path,
    output: Path,
    sarif_output: Path,
) -> dict[str, Any]:
    """Prove the exact command, image, scanner and execution environment before AWS."""
    if _git(root, "status", "--porcelain=v1"):
        raise DigestRescanRefusal(
            "SCOUT_PREFLIGHT_WORKTREE_DIRTY", "Scout preflight requires a clean committed worktree"
        )
    source_commit = _git(root, "rev-parse", "HEAD")
    bindings_body = read_committed_artifact(root, bindings_path)
    bindings = json.loads(bindings_body)
    image = bindings["image"]
    prerequisites = validate_scout_prerequisites()
    usage = shutil.disk_usage(tempfile.gettempdir())
    if usage.free < 20 * 1024**3:
        raise DigestRescanRefusal(
            "SCOUT_PREFLIGHT_DISK_INSUFFICIENT", "less than 20 GiB is free for the exact-image Scout preflight"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="medzen-asr-scout-preflight-") as temporary:
        work = Path(temporary)
        configure_external_tool_journal(work / "external-tool-diagnostics")
        archive = work / "exact-image.tar"
        layout_path = work / "exact-image.oci"
        export_exact_image(image["local_tag"], archive)
        archive_sha256 = _sha(archive)
        archive_bytes = archive.stat().st_size
        layout_path.mkdir()
        extract_oci_archive(archive, layout_path)
        identity = OciLayout(
            layout_path,
            expected_index=image["oci_index_digest"],
            expected_child=image["linux_amd64_digest"],
            expected_config=image["config_digest"],
            expected_attestation=image["attestation_digest"],
        ).verify()
        sarif = work / "docker-scout.sarif.json"
        diagnostic_path = work / "docker-scout-diagnostic.json"
        scan = scan_archive_with_scout(
            archive,
            sarif,
            diagnostics_path=diagnostic_path,
        )
        shutil.copyfile(sarif, sarif_output)
        diagnostic = json.loads(diagnostic_path.read_bytes())
    configure_external_tool_journal(None)
    receipt = {
        "schema_version": 1,
        "record": "ASR_EVAL_RUNTIME_EXACT_SCOUT_REAL_EXECUTION_PREFLIGHT",
        "id": "ASR-EVAL-RUNTIME-SCOUT-PREFLIGHT-2026-001",
        "status": "PASS_EXACT_IMAGE_SCOUT_REAL_EXECUTION_PREFLIGHT",
        "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_commit": source_commit,
        "bindings": {
            "path": str(bindings_path.relative_to(root)),
            "sha256": hashlib.sha256(bindings_body).hexdigest(),
            "loaded_from_committed_head": True,
        },
        "image": image,
        "exact_local_export": {
            "archive_sha256": archive_sha256,
            "archive_bytes": archive_bytes,
            "identity": identity,
        },
        "execution_environment": {
            "home_present": bool(__import__("os").environ.get("HOME")),
            "tmp_writable": True,
            "free_bytes_before": usage.free,
            "minimum_free_bytes": 20 * 1024**3,
            "timeout_seconds": 1800,
            "credential_values_recorded": False,
        },
        "scanner_prerequisites": prerequisites,
        "scan": scan,
        "diagnostic": diagnostic,
        "sarif": {
            "path": str(sarif_output.relative_to(root)),
            "sha256": _sha(sarif_output),
            "bytes": sarif_output.stat().st_size,
        },
        "scope": {
            "aws_calls": 0,
            "aws_mutations": 0,
            "kubectl_calls": 0,
            "gpu_started": False,
            "cost_usd": 0.0,
            "temporary_files_destroyed": True,
        },
    }
    write_exclusive(output, canonical_json(receipt))
    return {**receipt, "sha256": _sha(output)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sarif-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = qualify(
            root=args.root.resolve(),
            bindings_path=args.bindings.resolve(),
            output=args.output.resolve(),
            sarif_output=args.sarif_output.resolve(),
        )
    except Exception as exc:
        print(json.dumps({
            "status": "REFUSED",
            "reason_code": getattr(exc, "reason_code", type(exc).__name__),
            "safe_error_text": " ".join(str(exc).split())[:1024],
        }, sort_keys=True))
        return 2
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
