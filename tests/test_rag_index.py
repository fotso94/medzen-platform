from __future__ import annotations

import hashlib
import json
import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = ROOT / "services/rag-index"
sys.path.insert(0, str(SERVICE_ROOT))

from medzen_rag_index.app import create_app  # noqa: E402
from medzen_rag_index.index import IndexRefusal, IndexRepository  # noqa: E402


TEST_INDEX = ROOT / "platform/testdata/rag-index"
RESPONSE_SCHEMA = json.loads((
    ROOT / "platform/contracts/schemas/speech-v2/rag-response.schema.json"
).read_bytes())
REQUEST_ID = "55555555-5555-4555-8555-555555555555"


def canonical(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def document(document_id: str, text: str) -> dict:
    return {
        "document_id": document_id,
        "title": f"Title {document_id}",
        "source_uri": f"medzen://synthetic/{document_id}",
        "section": "test",
        "language": "en",
        "text": text,
        "content_sha256": hashlib.sha256(text.encode()).hexdigest(),
    }


def write_version(root: Path, version: str, documents: list[dict]) -> tuple[str, str]:
    manifest = {
        "schema_version": 1,
        "index_version": version,
        "classification": "SYNTHETIC_NON_CLINICAL",
        "documents": documents,
    }
    path = root / "indexes" / f"{version}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(manifest)
    path.write_bytes(raw)
    return str(path.relative_to(root)), hashlib.sha256(raw).hexdigest()


def point(root: Path, relative: str, digest: str, alias: str = "current") -> None:
    path = root / "aliases" / f"{alias}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical({
        "schema_version": 1,
        "alias": alias,
        "manifest_path": relative,
        "manifest_sha256": digest,
    }))


def test_local_index_is_checksum_bound_and_retrieval_is_deterministic():
    repo = IndexRepository(TEST_INDEX)
    first = repo.search("When does the fictional training desk open?", top_k=3)
    second = repo.search("When does the fictional training desk open?", top_k=3)
    assert first == second
    assert first[0]["document_id"] == "synthetic-hours"
    assert first[0]["content_sha256"] == hashlib.sha256(
        first[0]["excerpt"].encode()
    ).hexdigest()
    assert repo.loaded.snapshot_sha256 == (
        "6dc2a9217b44a8cd9523ee051f19a7e20d1cab447ad0029a42796c5211797160"
    )


def test_hash_mismatch_and_empty_index_refuse_readiness(tmp_path: Path):
    relative, digest = write_version(
        tmp_path, "tamper-v1", [document("doc", "safe synthetic text")]
    )
    point(tmp_path, relative, "0" * 64)
    with pytest.raises(IndexRefusal, match="hash mismatch"):
        IndexRepository(tmp_path)
    relative, digest = write_version(tmp_path, "empty-v1", [])
    point(tmp_path, relative, digest)
    with pytest.raises(IndexRefusal, match="empty index"):
        IndexRepository(tmp_path)


def test_alias_switch_and_rollback_restore_the_exact_prior_results(tmp_path: Path):
    old_relative, old_digest = write_version(
        tmp_path, "old-v1", [document("old", "orange synthetic handbook")]
    )
    new_relative, new_digest = write_version(
        tmp_path, "new-v2", [document("new", "purple synthetic handbook")]
    )
    point(tmp_path, old_relative, old_digest)
    repo = IndexRepository(tmp_path)
    old_result = repo.search("orange handbook")
    point(tmp_path, new_relative, new_digest)
    repo.reload()
    assert repo.loaded.version == "new-v2"
    assert repo.search("purple handbook")[0]["document_id"] == "new"
    point(tmp_path, old_relative, old_digest)
    repo.reload()
    assert repo.loaded.snapshot_sha256 == old_digest
    assert repo.search("orange handbook") == old_result


def test_rag_http_contract_readiness_and_zero_match_success():
    repo = IndexRepository(TEST_INDEX)
    with TestClient(create_app(repo)) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["index"]["snapshot_sha256"] == repo.loaded.snapshot_sha256
        response = client.post("/internal/v1/retrievals", json={
            "request_id": REQUEST_ID,
            "query": "When does the fictional training desk open?",
            "language": "en",
            "top_k": 3,
        })
        assert response.status_code == 200
        payload = response.json()
        Draft202012Validator(
            RESPONSE_SCHEMA, format_checker=FormatChecker()
        ).validate(payload)
        assert payload["citations"][0]["document_id"] == "synthetic-hours"
        empty = client.post("/internal/v1/retrievals", json={
            "request_id": REQUEST_ID,
            "query": "quuxxyzz",
        })
        assert empty.status_code == 200
        assert empty.json()["citations"] == []


def test_rag_rejects_invalid_requests_and_never_logs_query_content(caplog):
    secret_query = "PRIVATE-SYNTHETIC-QUERY-DO-NOT-LOG"
    repo = IndexRepository(TEST_INDEX)
    caplog.set_level(logging.INFO, logger="medzen.rag")
    with TestClient(create_app(repo, max_body_bytes=128)) as client:
        invalid = client.post("/internal/v1/retrievals", json={
            "request_id": REQUEST_ID,
            "query": secret_query,
            "top_k": 99,
        })
        assert invalid.status_code == 400
        oversized = client.post(
            "/internal/v1/retrievals",
            content=json.dumps({"request_id": REQUEST_ID, "query": "x" * 256}),
            headers={"content-type": "application/json"},
        )
        assert oversized.status_code == 413
    assert secret_query not in caplog.text
    assert "fictional training desk opens" not in caplog.text


def test_missing_index_fails_closed_without_exposing_path(tmp_path: Path):
    with TestClient(create_app(index_root=tmp_path)) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 503
        assert ready.json()["error_code"] == "IndexRefusal"
        response = client.post("/internal/v1/retrievals", json={
            "request_id": REQUEST_ID,
            "query": "synthetic",
        })
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"
        assert str(tmp_path) not in response.text
