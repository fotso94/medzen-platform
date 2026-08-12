from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.asr_base_model_pilot_receipts import ReceiptStore
from scripts.asr_base_model_pilot_fake import FakeOperations
from scripts.asr_base_model_pilot_integrity import (
    EXECUTOR_MODULE_PATHS,
    PilotIntegrityRefusal,
    read_committed_artifact,
    validate_executor_module_bindings,
)
from scripts.asr_base_model_pilot_runner import AttemptContext, OperationRefusal, execute_attempt


def _module_tree(root: Path) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for index, relative in enumerate(EXECUTOR_MODULE_PATHS):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"module-{index}\n", encoding="utf-8")
        bindings[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return bindings


def test_executor_module_gate_requires_the_complete_exact_set(tmp_path: Path) -> None:
    bindings = _module_tree(tmp_path)
    result = validate_executor_module_bindings(tmp_path, bindings)
    assert result["status"] == "PASS_ALL_EXECUTOR_MODULE_HASHES"
    assert result["module_count"] == len(EXECUTOR_MODULE_PATHS)
    assert result["conditional_hash_omissions_permitted"] is False

    missing = dict(bindings)
    missing.pop("scripts/asr_eval_oci_publication.py")
    with pytest.raises(PilotIntegrityRefusal) as missing_refusal:
        validate_executor_module_bindings(tmp_path, missing)
    assert missing_refusal.value.reason_code == "EXECUTOR_MODULE_SET_DIFFERS"

    extra = {**bindings, "scripts/unreviewed.py": "0" * 64}
    with pytest.raises(PilotIntegrityRefusal) as extra_refusal:
        validate_executor_module_bindings(tmp_path, extra)
    assert extra_refusal.value.reason_code == "EXECUTOR_MODULE_SET_DIFFERS"


def test_executor_module_gate_refuses_one_changed_module(tmp_path: Path) -> None:
    bindings = _module_tree(tmp_path)
    (tmp_path / "scripts/asr_base_model_pilot_plan.py").write_text(
        "changed-after-review\n", encoding="utf-8"
    )
    with pytest.raises(PilotIntegrityRefusal) as captured:
        validate_executor_module_bindings(tmp_path, bindings)
    assert captured.value.reason_code == "EXECUTOR_SOURCE_HASH_DIFFERS"


def test_committed_artifact_reader_uses_git_bytes_not_a_fixture(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "MedZen Test"], cwd=tmp_path, check=True)
    artifact = tmp_path / "platform/manifests/bindings.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text('{"committed":true}\n', encoding="utf-8")
    subprocess.run(["git", "add", str(artifact.relative_to(tmp_path))], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=tmp_path, check=True)
    assert read_committed_artifact(tmp_path, artifact) == b'{"committed":true}\n'

    artifact.write_text('{"fixture":true}\n', encoding="utf-8")
    with pytest.raises(PilotIntegrityRefusal) as captured:
        read_committed_artifact(tmp_path, artifact)
    assert captured.value.reason_code == "COMMITTED_ARTIFACT_BYTES_DIFFER"


def test_attempt_six_refuses_missing_committed_stage_one_dry_run_before_scout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DOCKER_SCOUT_HUB_USER", raising=False)
    monkeypatch.delenv("DOCKER_SCOUT_HUB_PASSWORD", raising=False)
    context = AttemptContext(
        attempt=6,
        bindings={
            "image": {"linux_amd64_digest": "sha256:" + "1" * 64, "tag": "pilot"},
            "pilot_bundle": {"sha256": "2" * 64},
        },
        receipts=ReceiptStore(
            tmp_path / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=tmp_path,
        dry_run_path=tmp_path / "missing-committed-dry-run.json",
        bindings_sha256="b" * 64,
    )
    with pytest.raises(OperationRefusal) as captured:
        execute_attempt(FakeOperations(), context)
    assert captured.value.reason_code == "COMMITTED_STAGE_ONE_DRY_RUN_ABSENT"
    assert not (tmp_path / "attempt-envelope.json").exists()


def test_attempt_six_refuses_dry_run_bound_to_different_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_SCOUT_HUB_USER", "synthetic")
    monkeypatch.setenv("DOCKER_SCOUT_HUB_PASSWORD", "synthetic")
    dry_run = tmp_path / "dry-run.json"
    dry_run.write_text(
        json.dumps({
            "status": "PASS_COMMITTED_DEADLINE_IDENTITY_DRY_RUN",
            "attempt": 6,
            "packet_sha256": "different",
            "authorization_sha256": "a" * 64,
            "bindings_sha256": "b" * 64,
            "result": {"status": "PASS_DEADLINE_IDENTITY_AND_ACCEPTANCE"},
        }),
        encoding="utf-8",
    )
    context = AttemptContext(
        attempt=6,
        bindings={
            "image": {"linux_amd64_digest": "sha256:" + "1" * 64, "tag": "pilot"},
            "pilot_bundle": {"sha256": "2" * 64},
        },
        receipts=ReceiptStore(
            tmp_path / "receipts",
            packet_sha256="0" * 64,
            authorization_sha256="a" * 64,
        ),
        workdir=tmp_path,
        dry_run_path=dry_run,
        bindings_sha256="b" * 64,
    )
    with pytest.raises(OperationRefusal) as captured:
        execute_attempt(FakeOperations(), context)
    assert captured.value.reason_code == "COMMITTED_STAGE_ONE_DRY_RUN_BINDING_DIFFERS"
    assert not (tmp_path / "attempt-envelope.json").exists()


def test_attempt_seven_refuses_missing_committed_scout_preflight_before_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOCKER_SCOUT_HUB_USER", "synthetic")
    monkeypatch.setenv("DOCKER_SCOUT_HUB_PASSWORD", "synthetic")
    context = AttemptContext(
        attempt=7,
        bindings={
            "image": {"linux_amd64_digest": "sha256:" + "1" * 64, "tag": "pilot"},
            "pilot_bundle": {"sha256": "2" * 64},
        },
        receipts=ReceiptStore(
            tmp_path / "receipts", packet_sha256="0" * 64, authorization_sha256="a" * 64
        ),
        workdir=tmp_path,
    )
    with pytest.raises(OperationRefusal) as captured:
        execute_attempt(FakeOperations(), context)
    assert captured.value.reason_code == "COMMITTED_SCOUT_PREFLIGHT_ABSENT"
    assert not (tmp_path / "attempt-envelope.json").exists()
