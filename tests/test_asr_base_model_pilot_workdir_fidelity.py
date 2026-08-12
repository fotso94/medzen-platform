from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_fake import FakeOperations
from scripts.asr_base_model_pilot_runner import (
    OperationRefusal,
    build_attempt_context,
    execute_attempt,
)


def _clean_repository(path: Path) -> None:
    path.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "MedZen Test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@medzen.invalid"], cwd=path, check=True)
    (path / "reviewed.txt").write_text("reviewed\n", encoding="utf-8")
    subprocess.run(["git", "add", "reviewed.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "reviewed"], cwd=path, check=True)


def test_workdir_inside_reviewed_tree_refuses_before_any_directory_is_created(
    tmp_path: Path,
) -> None:
    reviewed = tmp_path / "reviewed"
    _clean_repository(reviewed)
    forbidden = reviewed / "runtime-evidence"

    with pytest.raises(OperationRefusal) as refused:
        build_attempt_context(
            root=reviewed,
            workdir=forbidden,
            attempt=8,
            bindings={},
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        )

    assert refused.value.reason_code == "EXECUTION_WORKDIR_INSIDE_REVIEWED_WORKTREE"
    assert not forbidden.exists()


def test_shared_live_rehearsal_bootstrap_keeps_every_side_effect_external(
    tmp_path: Path,
) -> None:
    reviewed = tmp_path / "reviewed"
    external = tmp_path / "external-runtime"
    _clean_repository(reviewed)
    bindings = json.loads(
        (ROOT / "platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002F.json").read_bytes()
    )
    context = build_attempt_context(
        root=reviewed,
        workdir=external,
        attempt=4,
        bindings=bindings,
        packet_sha256="0" * 64,
        authorization_sha256="a" * 64,
    )

    assert not external.exists()
    assert context.receipts.directory == external.resolve() / "receipts"
    result = execute_attempt(FakeOperations(), context)

    assert result["outcome"] == "PASS_PILOT"
    assert result["filesystem_side_effect_order"][:4] == [
        "external_workdir_validated_before_side_effects",
        "reviewed_worktree_clean_before_side_effects",
        "external_workdir_created",
        "pre_envelope_prerequisites_passed",
    ]
    assert result["filesystem_side_effect_order"][4] == "attempt_envelope_persisted"
    assert result["filesystem_side_effect_order"][-1] == "terminal_result_persisted"
    assert (external / "attempt-envelope.json").is_file()
    assert (external / "result.json").is_file()
    assert len(list((external / "receipts").glob("*.json"))) == 11
    assert subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=reviewed,
        capture_output=True,
        text=True,
        check=True,
    ).stdout == ""


def test_dirty_reviewed_tree_refuses_before_external_runtime_creation(
    tmp_path: Path,
) -> None:
    reviewed = tmp_path / "reviewed"
    external = tmp_path / "external-runtime"
    _clean_repository(reviewed)
    (reviewed / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    context = build_attempt_context(
        root=reviewed,
        workdir=external,
        attempt=4,
        bindings={},
        packet_sha256="0" * 64,
        authorization_sha256="a" * 64,
    )

    with pytest.raises(OperationRefusal) as refused:
        execute_attempt(FakeOperations(), context)

    assert refused.value.reason_code == "REVIEWED_CLEAN_COMMIT_REQUIRED"
    assert not external.exists()
