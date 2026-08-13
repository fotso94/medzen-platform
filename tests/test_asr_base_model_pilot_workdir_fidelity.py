from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.asr_base_model_pilot_fake import build_rehearsal_operations
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


def test_boundary_harness_does_not_define_stage_methods() -> None:
    import scripts.asr_base_model_pilot_fake as boundary_module
    from pipeline.asr_base_model_pilot_receipts import STAGES
    from scripts.asr_base_model_pilot_live import LiveOperations

    for value in vars(boundary_module).values():
        if isinstance(value, type) and value is not LiveOperations:
            assert not set(STAGES).intersection(value.__dict__)


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
        execute_attempt(object(), context)

    assert refused.value.reason_code == "REVIEWED_CLEAN_COMMIT_REQUIRED"
    assert not external.exists()
