"""Bedrock Knowledge Base retrieval backend (dev corpus v1, 2026-09-02).

Selected with ``RAG_BACKEND=bedrock``. The manifest under ``RAG_INDEX_ROOT``
(``aliases/<alias>.json`` -> ``indexes/<manifest>.json``) binds the corpus
identity exactly like the local synthetic index does: its sha256 is the
snapshot the registry route names, and a retrieved chunk is only cited when
its S3 source is a document the manifest lists. Retrieval itself is semantic
(Titan v2 embeddings behind the Bedrock ``Retrieve`` API) with a minimum
relevance score; below it the service returns zero citations so the
orchestrator's general-knowledge fallback answers instead.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .index import IndexRefusal, _json

LOGGER = logging.getLogger("medzen.rag.bedrock")
BEDROCK_CLASSIFICATION = "NONPROD_REAL_CONTENT_V1"
CONTRACT_SNAPSHOT = "local-contract:MEDZEN-SPEECH-CONTRACT-2026-001"
CITATION_URI_PREFIX = "medzen://corpus/"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# Bedrock Retrieve accepts at most 1000 characters of query text.
MAX_RETRIEVAL_QUERY_CHARACTERS = 1000
EXCERPT_CHARACTERS = 280
GROUNDING_CHARACTERS = 1200


@dataclass(frozen=True)
class CorpusDocument:
    document_id: str
    corpus: str
    language: str
    title: str
    section: str
    source_uri: str
    citation_uri: str
    content_sha256: str


@dataclass(frozen=True)
class CorpusSpec:
    name: str
    data_source_id: str
    filter: tuple[tuple[str, str], ...]
    language_filter: bool


@dataclass(frozen=True)
class LoadedCorpus:
    alias: str
    version: str
    snapshot_sha256: str
    classification: str
    knowledge_base_id: str
    region: str
    corpora: tuple[CorpusSpec, ...]
    documents: dict[str, CorpusDocument]

    @property
    def model_versions(self) -> dict[str, str | None]:
        return {
            "asr": None,
            "registry_snapshot": CONTRACT_SNAPSHOT,
            "llm": None,
            "rag": f"sha256:{self.snapshot_sha256}",
            "tts": None,
        }


def _string(value: dict[str, Any], key: str, label: str, *,
            allow_empty: bool = False) -> str:
    item = value.get(key)
    if not isinstance(item, str) or (not item and not allow_empty):
        raise IndexRefusal(f"{label} {key} is missing or malformed")
    return item


def load_corpus(root: Path, alias: str = "current") -> LoadedCorpus:
    root = Path(root)
    alias_value, _ = _json(root / "aliases" / f"{alias}.json", "corpus alias")
    if alias_value.get("schema_version") != 1:
        raise IndexRefusal("corpus alias schema is unsupported")
    if alias_value.get("alias") != alias:
        raise IndexRefusal("corpus alias identity is ambiguous")
    expected = alias_value.get("manifest_sha256")
    if not isinstance(expected, str) or SHA256_RE.fullmatch(expected) is None:
        raise IndexRefusal("corpus alias manifest hash is malformed")
    manifest_path = alias_value.get("manifest_path")
    if not isinstance(manifest_path, str) or not manifest_path:
        raise IndexRefusal("corpus alias manifest path is missing")
    try:
        resolved = (root / manifest_path).resolve()
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise IndexRefusal("corpus alias escapes its repository") from exc
    manifest, raw = _json(resolved, "corpus manifest")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise IndexRefusal("corpus alias manifest hash mismatch")
    if manifest.get("schema_version") != 1 or manifest.get("backend") != "bedrock":
        raise IndexRefusal("corpus manifest schema is unsupported")
    if manifest.get("classification") != BEDROCK_CLASSIFICATION:
        raise IndexRefusal("corpus manifest classification is not accepted")
    version = _string(manifest, "index_version", "corpus manifest")
    kb = manifest.get("knowledge_base")
    if not isinstance(kb, dict):
        raise IndexRefusal("corpus manifest knowledge base is missing")
    kb_id = _string(kb, "id", "knowledge base")
    region = _string(kb, "region", "knowledge base")
    corpora_value = manifest.get("corpora")
    if not isinstance(corpora_value, dict) or not corpora_value:
        raise IndexRefusal("corpus manifest lists no corpora")
    corpora: list[CorpusSpec] = []
    for name, spec in sorted(corpora_value.items()):
        if not isinstance(spec, dict):
            raise IndexRefusal("corpus spec must be an object")
        filt = spec.get("filter")
        if not isinstance(filt, dict) or not filt or not all(
            isinstance(k, str) and isinstance(v, str) and k and v
            for k, v in filt.items()
        ):
            raise IndexRefusal(f"corpus {name} filter is malformed")
        if not isinstance(spec.get("language_filter"), bool):
            raise IndexRefusal(f"corpus {name} language_filter is malformed")
        corpora.append(CorpusSpec(
            name=name,
            data_source_id=_string(spec, "data_source_id", f"corpus {name}"),
            filter=tuple(sorted(filt.items())),
            language_filter=spec["language_filter"],
        ))
    names = {c.name for c in corpora}
    raw_documents = manifest.get("documents")
    if not isinstance(raw_documents, list) or not raw_documents:
        raise IndexRefusal("empty corpus cannot become ready")
    documents: dict[str, CorpusDocument] = {}
    identities: set[str] = set()
    for item in raw_documents:
        if not isinstance(item, dict):
            raise IndexRefusal("corpus document must be an object")
        document = CorpusDocument(
            document_id=_string(item, "document_id", "corpus document"),
            corpus=_string(item, "corpus", "corpus document"),
            language=_string(item, "language", "corpus document"),
            title=_string(item, "title", "corpus document"),
            section=_string(item, "section", "corpus document"),
            source_uri=_string(item, "source_uri", "corpus document"),
            citation_uri=_string(item, "citation_uri", "corpus document"),
            content_sha256=_string(item, "content_sha256", "corpus document"),
        )
        if document.corpus not in names:
            raise IndexRefusal("corpus document names an unknown corpus")
        if not document.source_uri.startswith("s3://"):
            raise IndexRefusal("corpus document source is not an S3 object")
        if not document.citation_uri.startswith(CITATION_URI_PREFIX):
            raise IndexRefusal("corpus document citation uri is malformed")
        if SHA256_RE.fullmatch(document.content_sha256) is None:
            raise IndexRefusal("corpus document content hash is malformed")
        if document.document_id in identities or document.source_uri in documents:
            raise IndexRefusal("duplicate corpus document identity")
        identities.add(document.document_id)
        documents[document.source_uri] = document
    return LoadedCorpus(
        alias=alias,
        version=version,
        snapshot_sha256=expected,
        classification=BEDROCK_CLASSIFICATION,
        knowledge_base_id=kb_id,
        region=region,
        corpora=tuple(corpora),
        documents=documents,
    )


class BedrockRepository:
    """Drop-in peer of ``IndexRepository`` backed by a Bedrock Knowledge Base."""

    def __init__(self, root: Path, alias: str = "current", *, client: Any = None,
                 min_score: float = 0.45, candidates: int = 8,
                 corpora: set[str] | None = None,
                 timeout_seconds: float = 6.0,
                 min_score_by_language: dict[str, float] | None = None,
                 min_score_by_corpus: dict[str, float] | None = None):
        self.root = Path(root)
        self.alias = alias
        self.min_score = float(min_score)
        # Titan v2 similarity ranges differ by language (Kinyarwanda scores
        # compress upwards) and by corpus (dense WHO chunks sit higher than
        # short app sections for unrelated questions), so a language and a
        # corpus may each carry their own floor; the highest applicable
        # floor wins. Measured 2026-09-03 (rag_floor_eval): near-domain
        # distractors reach 0.66 on the product corpus and 0.67 on the
        # clinical corpus while relevant questions score >= 0.73 / 0.78.
        self.min_score_by_language = {
            str(k): float(v) for k, v in (min_score_by_language or {}).items()}
        self.min_score_by_corpus = {
            str(k): float(v) for k, v in (min_score_by_corpus or {}).items()}
        self.candidates = max(1, min(int(candidates), 25))
        self.enabled = set(corpora) if corpora else None
        self.timeout_seconds = float(timeout_seconds)
        self.loaded = self._load()
        self._client = client

    def _load(self) -> LoadedCorpus:
        loaded = load_corpus(self.root, self.alias)
        if self.enabled is not None:
            unknown = self.enabled - {c.name for c in loaded.corpora}
            if unknown:
                raise IndexRefusal("enabled corpus is not in the manifest")
        return loaded

    def reload(self) -> LoadedCorpus:
        self.loaded = self._load()
        return self.loaded

    @property
    def client(self) -> Any:
        if self._client is None:
            import boto3
            from botocore.config import Config

            self._client = boto3.client(
                "bedrock-agent-runtime",
                region_name=self.loaded.region,
                config=Config(
                    connect_timeout=2,
                    read_timeout=self.timeout_seconds,
                    retries={"max_attempts": 2, "mode": "standard"},
                ),
            )
        return self._client

    def search(self, query: str, *, language: str | None = None,
               top_k: int = 3) -> list[dict[str, Any]]:
        text = " ".join(query.split())[:MAX_RETRIEVAL_QUERY_CHARACTERS]
        if not text:
            return []
        specs = [c for c in self.loaded.corpora
                 if self.enabled is None or c.name in self.enabled]
        if not specs:
            return []
        with ThreadPoolExecutor(max_workers=len(specs)) as pool:
            batches = list(pool.map(
                lambda spec: self._retrieve(
                    spec, text, language, self.floor_for(spec.name, language)),
                specs))
        candidates = [item for batch in batches for item in batch]
        candidates.sort(key=lambda item: (-item["score"], item["document_id"]))
        # No forced mixing (Codex review 2026-09-03): corpora compete on
        # score alone above their own floors; a corpus that is merely
        # "least irrelevant" never fills a slot.
        citations: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in candidates:
            if item["document_id"] in seen:
                continue
            seen.add(item["document_id"])
            citation = {key: value for key, value in item.items() if key != "corpus"}
            citation["rank"] = len(citations) + 1
            citations.append(citation)
            if len(citations) >= top_k:
                break
        return citations

    def floor_for(self, corpus: str, language: str | None) -> float:
        return max(self.min_score,
                   self.min_score_by_corpus.get(corpus, self.min_score),
                   self.min_score_by_language.get(language or "", self.min_score))

    def _retrieve(self, spec: CorpusSpec, text: str,
                  language: str | None, floor: float) -> list[dict[str, Any]]:
        clauses = [{"equals": {"key": key, "value": value}}
                   for key, value in spec.filter]
        if spec.language_filter:
            if language is None:
                return []
            clauses.append({"equals": {"key": "language", "value": language}})
        filt = clauses[0] if len(clauses) == 1 else {"andAll": clauses}
        try:
            response = self.client.retrieve(
                knowledgeBaseId=self.loaded.knowledge_base_id,
                retrievalQuery={"text": text},
                retrievalConfiguration={"vectorSearchConfiguration": {
                    "numberOfResults": self.candidates, "filter": filt}},
            )
        except Exception as exc:  # botocore errors, timeouts: degrade to ungrounded
            LOGGER.error(json.dumps({
                "event": "bedrock_retrieve_failed", "corpus": spec.name,
                "error": type(exc).__name__}, sort_keys=True))
            return []
        results: list[dict[str, Any]] = []
        for item in response.get("retrievalResults") or []:
            if not isinstance(item, dict):
                continue
            content = (item.get("content") or {}).get("text")
            uri = ((item.get("location") or {}).get("s3Location") or {}).get("uri")
            score = item.get("score")
            if not isinstance(content, str) or not content.strip() \
                    or not isinstance(uri, str):
                continue
            document = self.loaded.documents.get(uri)
            if document is None or document.corpus != spec.name:
                LOGGER.warning(json.dumps({
                    "event": "unmanifested_result_dropped",
                    "corpus": spec.name}, sort_keys=True))
                continue
            # Codex review 2026-09-03 (round 2): the KB's metadata filter is
            # not trusted on its own - the manifest's language for the
            # document must match the request when this corpus is
            # language-filtered.
            if spec.language_filter and document.language != language:
                LOGGER.warning(json.dumps({
                    "event": "language_mismatch_dropped",
                    "corpus": spec.name, "expected": language,
                    "document_language": document.language}, sort_keys=True))
                continue
            if isinstance(score, bool) or not isinstance(score, (int, float)) \
                    or score <= 0 or score < floor:
                continue
            chunk = content.strip()
            chunk_sha256 = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
            whole_document = chunk_sha256 == document.content_sha256
            results.append({
                "corpus": spec.name,
                "rank": 0,
                "document_id": document.document_id if whole_document
                else f"{document.document_id}#{chunk_sha256[:12]}",
                "title": document.title,
                "source_uri": document.citation_uri,
                "section": document.section,
                "content_sha256": chunk_sha256,
                "excerpt": chunk[:EXCERPT_CHARACTERS],
                "grounding_text": chunk[:GROUNDING_CHARACTERS],
                "score": float(score),
            })
        return results
