from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / "platform/decisions/B6-ORCHESTRATOR-2026-001-local-file-mode.json"
EVIDENCE = ROOT / "platform/evidence/B6-LOCAL-ENGINEERING-2026-003-orchestrator-file-mode.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_owner_decision_preserves_the_adopted_contract_and_cloud_boundary():
    decision = json.loads(DECISION.read_bytes())
    assert decision["status"] == "OWNER_AUTHORIZED_LOCAL_ONLY"
    assert decision["contract"]["parent_sha256"] == sha(
        ROOT / "platform/contracts/speech-v1.yaml"
    ) == "e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d"
    assert decision["contract"]["parent_contract_mutation_permitted"] is False
    assert decision["registry_boundary"]["ssm_publication_authorized_now"] is False
    assert decision["preserved_state"] == {
        "b5_gate_outcome": "BLOCKED_UNCHANGED",
        "cpu_desired_capacity": 0,
        "gpu_desired_capacity": 0,
        "ssm_registry_parameters": 0,
    }


def test_local_auth_fixture_stores_only_the_key_hash():
    path = ROOT / "platform/testdata/orchestrator/client-keys.json"
    raw = path.read_text()
    value = json.loads(raw)
    assert "medzen-b6-synthetic-client-key" not in raw
    assert value["clients"][0]["key_sha256"] == hashlib.sha256(
        b"medzen-b6-synthetic-client-key"
    ).hexdigest()


def test_exit_record_binds_every_named_source_and_preserves_cloud_zero():
    evidence = json.loads(EVIDENCE.read_bytes())
    assert evidence["status"] == "VERIFIED_LOCAL_COMPLETE"
    for relative, expected in evidence["source_bindings"].items():
        assert sha(ROOT / relative) == expected, relative
    assert evidence["outcome"]["request_body_logged"] is False
    assert evidence["aws_and_governance"] == {
        "aws_calls": 0,
        "aws_packets_authorized": 0,
        "new_aws_resources": 0,
        "iam_changes": 0,
        "ssm_writes": 0,
        "ecr_pushes": 0,
        "kubernetes_actions": 0,
        "cpu_or_gpu_scale_up": 0,
        "real_bedrock_calls": 0,
        "approved_artifact_writes": 0,
        "production_serving_changes": 0,
    }
