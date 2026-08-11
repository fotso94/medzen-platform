from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.b6_6_registry_rag_alignment import (
    AlignmentRefusal,
    PROOF_AUDIO_SHA256,
    RAG_INDEX_SHA256,
    REGISTRY_ROOT,
    audit,
    evaluate_contract,
    rehearsal,
)


ROOT = Path(__file__).resolve().parents[1]


def test_exact_deployed_image_registry_and_spoken_proof_input_align() -> None:
    result = audit()
    assert result["status"] == "PASS_ALIGNED_RAG_PROOF_PATH"
    assert result["rag_index_sha256"] == RAG_INDEX_SHA256
    assert result["registry_root"] == REGISTRY_ROOT
    assert result["proof_audio_sha256"] == PROOF_AUDIO_SHA256
    assert result["citation_count"] == 3
    assert result["citation_document_ids"] == [
        "synthetic-card", "synthetic-hours", "synthetic-support"
    ]
    assert result["aws_calls"] == 0
    assert result["kubernetes_calls"] == 0
    assert result["mutations"] == 0


def test_registry_mismatch_refuses_and_aligned_contract_passes() -> None:
    with pytest.raises(AlignmentRefusal, match="RAG_INDEX_IDENTITY_MISMATCH"):
        evaluate_contract(
            expected_index_sha256=RAG_INDEX_SHA256,
            observed_index_sha256="0" * 64,
            citation_count=3,
        )
    assert evaluate_contract(
        expected_index_sha256=RAG_INDEX_SHA256,
        observed_index_sha256=RAG_INDEX_SHA256,
        citation_count=3,
    )["status"] == "PASS_ALIGNED_RAG_PROOF_PATH"


def test_empty_or_incomplete_retrieval_refuses_even_when_identity_matches() -> None:
    for count in (0, 1, 2):
        with pytest.raises(
            AlignmentRefusal, match="RAG_PROOF_CITATION_COUNT_MISMATCH"
        ):
            evaluate_contract(
                expected_index_sha256=RAG_INDEX_SHA256,
                observed_index_sha256=RAG_INDEX_SHA256,
                citation_count=count,
            )


def test_rehearsal_contains_both_mismatch_refusal_and_aligned_pass() -> None:
    result = rehearsal()
    assert result["status"] == "PASS"
    assert result["aligned_pass"]["status"] == "PASS_ALIGNED_RAG_PROOF_PATH"
    assert result["mismatch_injection"] == {
        "outcome": "REFUSED",
        "reason_code": "RAG_INDEX_IDENTITY_MISMATCH",
    }
    assert result["real_aws_calls"] == 0
    assert result["real_kubectl_calls"] == 0
    assert result["mutations"] == 0


def test_window_uses_prior_live_proven_spoken_fixture_not_local_mock_tone() -> None:
    operations = (ROOT / "scripts/b6_6_operations.sh").read_text()
    spoken = ROOT / "platform/testdata/b6a-003c-b-synthetic.wav"
    tone = ROOT / "platform/testdata/orchestrator/synthetic-file-request.wav"
    assert 'wav="platform/testdata/b6a-003c-b-synthetic.wav"' in operations
    assert 'wav="platform/testdata/orchestrator/synthetic-file-request.wav"' not in operations
    assert hashlib.sha256(spoken.read_bytes()).hexdigest() == PROOF_AUDIO_SHA256
    assert hashlib.sha256(tone.read_bytes()).hexdigest() != PROOF_AUDIO_SHA256
    assert operations.index("b6_6_registry_rag_alignment.py audit") < operations.index(
        "b6_6_credential.py"
    )


def test_deployment_manifest_binds_proof_audio_registry_and_rag_identity() -> None:
    manifest = (ROOT / "platform/k8s/b6-6/integration-window.yaml").read_text()
    assert f"MEDZEN_B6_PROOF_AUDIO_SHA256: {PROOF_AUDIO_SHA256}" in manifest
    assert f"MEDZEN_B6_RAG_INDEX_SHA256: {RAG_INDEX_SHA256}" in manifest
    assert f"MEDZEN_REGISTRY_ROOT: {REGISTRY_ROOT}" in manifest
    source = json.loads(
        (ROOT / "registry/deployment/b6-v0-synthetic.json").read_bytes()
    )
    assert source["routes"]["english"]["rag"] == {
        "alias": "current",
        "snapshot_sha256": RAG_INDEX_SHA256,
        "query_language": "en",
    }
