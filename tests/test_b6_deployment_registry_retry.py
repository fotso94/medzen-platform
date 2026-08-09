from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SERVICE = ROOT / "services/speech-orchestrator"
for path in (SCRIPTS, SERVICE):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import run_b6_deployment_registry_publication_v2 as retry  # noqa: E402
from medzen_speech_orchestrator.registry import (  # noqa: E402
    DEPLOYED_CLASSIFICATION,
    LocalParameterStore,
    RegistryRefusal,
    RegistryRouter,
)


FIXTURE = ROOT / "platform/generated/registry-ssm/b6-v0-synthetic.json"
FAILURE = ROOT / "platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-FAILED-CLOSED.json"
V1 = ROOT / "scripts/run_b6_deployment_registry_publication.py"
V2 = ROOT / "scripts/run_b6_deployment_registry_publication_v2.py"
PACKET = ROOT / "platform/decisions/B6-AWS-CHANGE-PACKET-2026-007A-deployment-registry-classification-retry.md"
MANIFEST = ROOT / "platform/manifests/B6-DEPLOYMENT-REGISTRY-2026-001.json"


def fixture_root() -> str:
    value = json.loads(FIXTURE.read_text())
    return value["parameters"][0]["Name"].rsplit("/", 1)[0]


def test_v1_default_policy_reproduces_the_recorded_refusal() -> None:
    with pytest.raises(RegistryRefusal, match="manifest identity"):
        RegistryRouter(LocalParameterStore(FIXTURE), fixture_root())


def test_retry_selects_deployed_classification_and_accepts_exact_fixture() -> None:
    router = retry.deployed_router(LocalParameterStore(FIXTURE), fixture_root())
    assert router.classification == DEPLOYED_CLASSIFICATION
    assert router.snapshot_sha256 == fixture_root().rsplit("/", 1)[1]
    assert router.resolve("en").alias == "english"


def test_retry_refuses_repository_relative_receipt_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--authorization",
            "platform/decisions/owner.json",
            "--receipt",
            "platform/evidence/receipt.json",
            "--apply",
        ],
    )
    assert retry.main() == 2


def test_retry_preserves_failed_runner_and_evidence_bindings() -> None:
    failure = json.loads(FAILURE.read_text())
    assert failure["status"] == "BLOCKED_RUNTIME_REGISTRY_CLASSIFICATION"
    assert failure["publication_attempt"]["runner_sha256"] == hashlib.sha256(
        V1.read_bytes()
    ).hexdigest()
    assert failure["automatic_rollback"]["exact_root_after"] == "EMPTY"
    assert failure["automatic_rollback"]["production_pointer_after"] == "ABSENT"


def test_retry_packet_binds_exact_unchanged_inputs() -> None:
    packet = PACKET.read_text()
    for path in (FAILURE, V1, V2, MANIFEST, FIXTURE):
        assert hashlib.sha256(path.read_bytes()).hexdigest() in packet
    assert "zero Terraform, IAM, KMS, ECR, EKS" in packet
    assert "Approve B6 AWS change packet 2026-007A only." in packet
