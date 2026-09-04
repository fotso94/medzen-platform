"""Bedrock Knowledge Base backend for the rag-index service (dev corpus v1).

The backend must be a drop-in peer of the local synthetic index: same response
shape (validated against the speech-v2 contract schema), same identity binding
(manifest sha256 == registry RAG snapshot), citations only from documents the
manifest lists, a minimum relevance score, and zero citations (never an error)
when Bedrock is unavailable so the orchestrator's fallback answers instead.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/rag-index"))

from medzen_rag_index import bedrock_backend as bb  # noqa: E402
from medzen_rag_index.app import create_app  # noqa: E402
from medzen_rag_index.index import IndexRefusal  # noqa: E402

RESPONSE_SCHEMA = json.loads((
    ROOT / "platform/contracts/schemas/speech-v2/rag-response-corpus.schema.json"
).read_bytes())
REQUEST_ID = "66666666-6666-4666-8666-666666666666"
BOOKING_EN = "# Booking\n\n## How do I book?\n\nOpen the app and go to Appointments."
BOOKING_FR = "# Réservation\n\n## Comment réserver ?\n\nOuvrez l'application."
URI_EN = "s3://b/speech-rag/v1/product/en/booking--s01.md"
URI_FR = "s3://b/speech-rag/v1/product/fr/booking--s01.md"
URI_WHO = "s3://b/speech-rag/v1/clinical/malaria/who.pdf"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def documents() -> list[dict]:
    return [
        {"document_id": "product/en/booking/s01", "corpus": "product", "language": "en",
         "title": "Booking - How do I book?", "section": "How do I book?",
         "source_uri": URI_EN, "citation_uri": "medzen://corpus/product/en/booking--s01",
         "content_sha256": sha(BOOKING_EN)},
        {"document_id": "product/fr/booking/s01", "corpus": "product", "language": "fr",
         "title": "Réservation - Comment réserver ?", "section": "Comment réserver ?",
         "source_uri": URI_FR, "citation_uri": "medzen://corpus/product/fr/booking--s01",
         "content_sha256": sha(BOOKING_FR)},
        {"document_id": "clinical/who/malaria-who", "corpus": "clinical", "language": "en",
         "title": "WHO guidelines for malaria (2023)", "section": "guideline",
         "source_uri": URI_WHO, "citation_uri": "medzen://corpus/clinical/malaria/who.pdf",
         "content_sha256": sha("pdf-bytes")},
    ]


def write_corpus(root: Path, docs: list[dict] | None = None, **overrides) -> Path:
    manifest = {
        "schema_version": 1, "backend": "bedrock",
        "classification": bb.BEDROCK_CLASSIFICATION, "index_version": "test-kb-v1",
        "knowledge_base": {"id": "KB123", "region": "eu-central-1"},
        "corpora": {
            "product": {"data_source_id": "DS1", "filter": {"corpus": "product"},
                        "language_filter": True},
            "clinical": {"data_source_id": "DS2", "filter": {"source": "who"},
                         "language_filter": False},
        },
        "documents": docs if docs is not None else documents(),
    }
    manifest.update(overrides)
    raw = json.dumps(manifest, sort_keys=True).encode()
    (root / "indexes").mkdir(parents=True, exist_ok=True)
    (root / "aliases").mkdir(parents=True, exist_ok=True)
    (root / "indexes" / "m.json").write_bytes(raw)
    (root / "aliases" / "current.json").write_text(json.dumps({
        "alias": "current", "manifest_path": "indexes/m.json",
        "manifest_sha256": hashlib.sha256(raw).hexdigest(), "schema_version": 1}))
    return root


def result(uri: str, text: str, score: float) -> dict:
    return {"content": {"text": text}, "location": {"type": "S3", "s3Location": {"uri": uri}},
            "score": score, "metadata": {}}


class FakeClient:
    def __init__(self, by_corpus: dict[str, list[dict]], *, fail: bool = False):
        self.by_corpus = by_corpus
        self.fail = fail
        self.calls: list[dict] = []

    def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("bedrock down")
        filt = kwargs["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
        clauses = filt.get("andAll", [filt])
        corpus = "clinical" if any(c["equals"]["key"] == "source" for c in clauses) else "product"
        return {"retrievalResults": list(self.by_corpus.get(corpus, []))}


def corpus_filter(call: dict) -> list[tuple[str, str]]:
    filt = call["retrievalConfiguration"]["vectorSearchConfiguration"]["filter"]
    return sorted((c["equals"]["key"], c["equals"]["value"]) for c in filt.get("andAll", [filt]))


def test_manifest_binds_the_snapshot_and_reports_the_shared_contract_identity(tmp_path):
    root = write_corpus(tmp_path)
    repo = bb.BedrockRepository(root, client=FakeClient({}))
    alias = json.loads((root / "aliases/current.json").read_text())
    assert repo.loaded.snapshot_sha256 == alias["manifest_sha256"]
    assert repo.loaded.classification == "NONPROD_REAL_CONTENT_V1"
    assert repo.loaded.model_versions == {
        "asr": None, "registry_snapshot": "local-contract:MEDZEN-SPEECH-CONTRACT-2026-001",
        "llm": None, "rag": f"sha256:{alias['manifest_sha256']}", "tts": None}
    assert repo.loaded.knowledge_base_id == "KB123"


def test_search_filters_by_language_applies_threshold_and_binds_to_manifest(tmp_path):
    fake = FakeClient({
        "product": [
            result(URI_EN, BOOKING_EN, 0.91),
            result(URI_EN + "x", "# Not in manifest", 0.99),   # unmanifested: dropped
            result(URI_FR, BOOKING_FR, 0.80),                    # French document for an English request: dropped
            result(URI_EN, "low", 0.30),                         # below threshold: dropped
        ],
        "clinical": [result(URI_WHO, "Give artesunate for severe malaria.", 0.62)],
    })
    repo = bb.BedrockRepository(write_corpus(tmp_path), client=fake, min_score=0.45)
    citations = repo.search("how do I book", language="en", top_k=3)
    assert [c["document_id"] for c in citations] == [
        "product/en/booking/s01",
        "clinical/who/malaria-who#" + sha("Give artesunate for severe malaria.")[:12]]
    assert [c["rank"] for c in citations] == [1, 2]
    assert citations[0]["content_sha256"] == sha(BOOKING_EN)
    assert citations[0]["source_uri"] == "medzen://corpus/product/en/booking--s01"
    assert citations[0]["grounding_text"] == BOOKING_EN and citations[0]["excerpt"] == BOOKING_EN[:280]
    filters = sorted(corpus_filter(call) for call in fake.calls)
    assert filters == [[("corpus", "product"), ("language", "en")], [("source", "who")]]
    assert all(call["knowledgeBaseId"] == "KB123" for call in fake.calls)
    assert all(call["retrievalConfiguration"]["vectorSearchConfiguration"]["numberOfResults"] == 8
               for call in fake.calls)


def test_a_document_in_another_language_is_never_cited_even_if_the_kb_filter_lets_it_through(tmp_path):
    fake = FakeClient({"product": [result(URI_FR, BOOKING_FR, 0.95), result(URI_EN, BOOKING_EN, 0.7)],
                       "clinical": []})
    repo = bb.BedrockRepository(write_corpus(tmp_path), client=fake, min_score=0.6)
    assert [c["document_id"] for c in repo.search("book", language="en")] == ["product/en/booking/s01"]
    assert [c["document_id"] for c in repo.search("réserver", language="fr")] == ["product/fr/booking/s01"]


def test_product_corpus_is_not_queried_without_a_language_and_top_k_dedupes_chunks(tmp_path):
    chunk = "Same chunk text"
    fake = FakeClient({"product": [result(URI_EN, BOOKING_EN, 0.9)],
                       "clinical": [result(URI_WHO, chunk, 0.7), result(URI_WHO, chunk, 0.69),
                                    result(URI_WHO, "other chunk", 0.66)]})
    repo = bb.BedrockRepository(write_corpus(tmp_path), client=fake)
    citations = repo.search("severe malaria treatment", language=None, top_k=2)
    assert len(fake.calls) == 1 and corpus_filter(fake.calls[0]) == [("source", "who")]
    assert [c["score"] for c in citations] == [0.7, 0.66]
    assert len({c["document_id"] for c in citations}) == 2


def test_corpora_compete_on_score_above_their_own_floors_without_forced_mixing(tmp_path):
    fake = FakeClient({"product": [result(URI_EN, BOOKING_EN, 0.69)],
                       "clinical": [result(URI_WHO, "chunk a", 0.72), result(URI_WHO, "chunk b", 0.71),
                                    result(URI_WHO, "chunk c", 0.69)]})
    repo = bb.BedrockRepository(write_corpus(tmp_path), client=fake, min_score=0.6,
                                min_score_by_corpus={"product": 0.68, "clinical": 0.70})
    citations = repo.search("chest pain", language="en", top_k=3)
    # clinical chunk c (0.69) is under the clinical floor; the product section
    # (0.69) is above the product floor -> no slot is forced for either corpus
    assert [(c["document_id"].split("#")[0], c["score"]) for c in citations] == [
        ("clinical/who/malaria-who", 0.72), ("clinical/who/malaria-who", 0.71),
        ("product/en/booking/s01", 0.69)]
    assert all("corpus" not in c for c in citations)
    assert repo.floor_for("clinical", "kin") == 0.70 and repo.floor_for("product", "en") == 0.68
    repo.min_score_by_language = {"kin": 0.72}
    assert repo.floor_for("clinical", "kin") == 0.72   # the highest applicable floor wins


def test_a_language_may_carry_its_own_relevance_floor(tmp_path):
    fake = FakeClient({"product": [result(URI_EN, BOOKING_EN, 0.65)], "clinical": []})
    repo = bb.BedrockRepository(write_corpus(tmp_path), client=fake, min_score=0.6,
                                min_score_by_language={"kin": 0.68})
    assert len(repo.search("q", language="en")) == 1      # default floor 0.60
    assert repo.search("q", language="kin") == []          # kin floor 0.68


def test_bedrock_failure_degrades_to_zero_citations_not_an_error(tmp_path):
    repo = bb.BedrockRepository(write_corpus(tmp_path), client=FakeClient({}, fail=True))
    assert repo.search("anything", language="en") == []


def test_query_text_is_capped_to_the_bedrock_limit_and_blank_queries_skip_bedrock(tmp_path):
    fake = FakeClient({})
    repo = bb.BedrockRepository(write_corpus(tmp_path), client=fake)
    repo.search("word " * 400, language="en")
    assert all(len(call["retrievalQuery"]["text"]) <= 1000 for call in fake.calls)
    fake.calls.clear()
    assert repo.search("   ", language="en") == [] and fake.calls == []


def test_enabled_corpora_restrict_retrieval(tmp_path):
    fake = FakeClient({"product": [result(URI_EN, BOOKING_EN, 0.9)],
                       "clinical": [result(URI_WHO, "chunk", 0.9)]})
    repo = bb.BedrockRepository(write_corpus(tmp_path), client=fake, corpora={"product"})
    assert [c["document_id"] for c in repo.search("book", language="en")] == ["product/en/booking/s01"]
    with pytest.raises(IndexRefusal):
        bb.BedrockRepository(write_corpus(tmp_path / "b"), client=fake, corpora={"pharmacy"})


@pytest.mark.parametrize("override, message", [
    ({"classification": "SYNTHETIC_NON_CLINICAL"}, "classification"),
    ({"backend": "local"}, "schema"),
    ({"documents": []}, "empty"),
    ({"documents": documents() + [documents()[0]]}, "duplicate"),
    ({"documents": [dict(documents()[0], citation_uri="s3://x")]}, "citation uri"),
    ({"documents": [dict(documents()[0], corpus="pharmacy")]}, "unknown corpus"),
    ({"documents": [dict(documents()[0], content_sha256="nope")]}, "hash"),
])
def test_manifest_refusals(tmp_path, override, message):
    with pytest.raises(IndexRefusal, match=message):
        bb.BedrockRepository(write_corpus(tmp_path, **override), client=FakeClient({}))


def test_corpus_schema_is_a_strict_superset_of_the_pinned_contract_schema():
    pinned = json.loads((ROOT / "platform/contracts/schemas/speech-v2/rag-response.schema.json").read_bytes())
    superset = json.loads(json.dumps(RESPONSE_SCHEMA))
    superset.pop("$comment", None); superset.pop("$id", None); pinned.pop("$id", None)
    superset["properties"]["index"]["properties"]["classification"] = {"const": "SYNTHETIC_NON_CLINICAL"}
    superset["properties"]["citations"]["items"]["properties"]["source_uri"] = {
        "type": "string", "pattern": "^medzen://synthetic/"}
    assert superset == pinned  # only the two widened fields differ


def test_app_response_matches_the_contract_schema_and_readiness_names_the_backend(tmp_path):
    fake = FakeClient({"product": [result(URI_EN, BOOKING_EN, 0.9)], "clinical": []})
    repo = bb.BedrockRepository(write_corpus(tmp_path), client=fake)
    with TestClient(create_app(repository=repo)) as client:
        ready = client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["classification"] == "NONPROD_REAL_CONTENT_V1"
        assert ready.json()["index"]["snapshot_sha256"] == repo.loaded.snapshot_sha256
        response = client.post("/internal/v1/retrievals", json={
            "request_id": REQUEST_ID, "query": "how do I book", "language": "en", "top_k": 3})
    assert response.status_code == 200
    body = response.json()
    Draft202012Validator(RESPONSE_SCHEMA, format_checker=FormatChecker()).validate(body)
    assert body["index"] == {"alias": "current", "version": "test-kb-v1",
                             "snapshot_sha256": repo.loaded.snapshot_sha256,
                             "classification": "NONPROD_REAL_CONTENT_V1"}
    assert body["model_versions"]["rag"] == f"sha256:{repo.loaded.snapshot_sha256}"
    assert [c["document_id"] for c in body["citations"]] == ["product/en/booking/s01"]


def test_environment_selects_the_bedrock_backend_without_network(tmp_path, monkeypatch):
    root = write_corpus(tmp_path)
    monkeypatch.setenv("RAG_BACKEND", "bedrock")
    monkeypatch.setenv("RAG_INDEX_ROOT", str(root))
    monkeypatch.setenv("RAG_BEDROCK_MIN_SCORE", "0.6")
    monkeypatch.setenv("RAG_BEDROCK_CANDIDATES", "12")
    monkeypatch.setenv("RAG_BEDROCK_CORPORA", "product")
    monkeypatch.setenv("RAG_BEDROCK_MIN_SCORE_BY_LANGUAGE", "kin=0.68, swa=0.62")
    monkeypatch.setenv("RAG_BEDROCK_MIN_SCORE_BY_CORPUS", "product=0.68,clinical=0.70")
    with TestClient(create_app()) as client:
        assert client.get("/readyz").json()["classification"] == "NONPROD_REAL_CONTENT_V1"
        repo = client.app.state.repository
    assert isinstance(repo, bb.BedrockRepository)
    assert (repo.min_score, repo.candidates, repo.enabled) == (0.6, 12, {"product"})
    assert repo.min_score_by_language == {"kin": 0.68, "swa": 0.62}
    assert repo.min_score_by_corpus == {"product": 0.68, "clinical": 0.70}
    monkeypatch.setenv("RAG_BACKEND", "cassandra")
    with TestClient(create_app()) as client:
        assert client.get("/readyz").status_code == 503
        assert client.get("/readyz").json()["error_code"] == "IndexRefusal"


def test_local_backend_stays_the_default(monkeypatch):
    monkeypatch.delenv("RAG_BACKEND", raising=False)
    with TestClient(create_app()) as client:
        assert client.get("/readyz").json()["classification"] == "SYNTHETIC_NON_CLINICAL"
