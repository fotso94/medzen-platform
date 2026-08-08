from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
LOCAL_CLASSIFICATION = "SYNTHETIC_NON_CLINICAL"


class IndexRefusal(RuntimeError):
    """The index cannot be trusted or served."""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _tokens(value: str) -> frozenset[str]:
    return frozenset(re.findall(r"[^\W_]+", value.casefold(), flags=re.UNICODE))


def _json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise IndexRefusal(f"{label} is missing or malformed") from exc
    if not isinstance(value, dict):
        raise IndexRefusal(f"{label} must be an object")
    return value, raw


@dataclass(frozen=True)
class Document:
    document_id: str
    title: str
    source_uri: str
    section: str
    language: str
    text: str
    content_sha256: str
    title_tokens: frozenset[str]
    text_tokens: frozenset[str]


@dataclass(frozen=True)
class LoadedIndex:
    alias: str
    version: str
    snapshot_sha256: str
    classification: str
    documents: tuple[Document, ...]

    @property
    def model_versions(self) -> dict[str, str | None]:
        return {
            "asr": None,
            "registry_snapshot": (
                "local-contract:MEDZEN-SPEECH-CONTRACT-2026-001"
            ),
            "llm": None,
            "rag": f"sha256:{self.snapshot_sha256}",
            "tts": None,
        }


class IndexRepository:
    """Load one immutable manifest through a checksum-bound alias."""

    def __init__(self, root: Path, alias: str = "current"):
        self.root = root.resolve()
        self.alias = alias
        self.loaded = self._load()

    def reload(self) -> LoadedIndex:
        """Resolve the alias again; used for controlled publish/rollback."""
        self.loaded = self._load()
        return self.loaded

    def _load(self) -> LoadedIndex:
        if ALIAS_RE.fullmatch(self.alias) is None:
            raise IndexRefusal("index alias is malformed")
        alias_value, _ = _json(
            self.root / "aliases" / f"{self.alias}.json", "index alias"
        )
        if alias_value.get("schema_version") != 1:
            raise IndexRefusal("index alias schema is unsupported")
        if alias_value.get("alias") != self.alias:
            raise IndexRefusal("index alias identity is ambiguous")
        expected_sha = alias_value.get("manifest_sha256")
        if not isinstance(expected_sha, str) or SHA256_RE.fullmatch(expected_sha) is None:
            raise IndexRefusal("index alias manifest hash is malformed")
        relative = alias_value.get("manifest_path")
        if not isinstance(relative, str) or not relative:
            raise IndexRefusal("index alias manifest path is missing")
        manifest_path = (self.root / relative).resolve()
        try:
            manifest_path.relative_to(self.root)
        except ValueError as exc:
            raise IndexRefusal("index alias escapes its repository") from exc
        manifest, raw = _json(manifest_path, "index manifest")
        if _sha256(raw) != expected_sha:
            raise IndexRefusal("index alias manifest hash mismatch")
        if manifest.get("schema_version") != 1:
            raise IndexRefusal("index manifest schema is unsupported")
        if manifest.get("classification") != LOCAL_CLASSIFICATION:
            raise IndexRefusal("local B6.1 accepts synthetic non-clinical content only")
        version = manifest.get("index_version")
        if not isinstance(version, str) or not version:
            raise IndexRefusal("index version is missing")
        raw_documents = manifest.get("documents")
        if not isinstance(raw_documents, list) or not raw_documents:
            raise IndexRefusal("empty index cannot become ready")
        documents: list[Document] = []
        identities: set[str] = set()
        for raw_document in raw_documents:
            document = self._document(raw_document)
            if document.document_id in identities:
                raise IndexRefusal("duplicate document identity")
            identities.add(document.document_id)
            documents.append(document)
        return LoadedIndex(
            alias=self.alias,
            version=version,
            snapshot_sha256=expected_sha,
            classification=LOCAL_CLASSIFICATION,
            documents=tuple(sorted(documents, key=lambda item: item.document_id)),
        )

    @staticmethod
    def _document(value: Any) -> Document:
        if not isinstance(value, dict):
            raise IndexRefusal("index document must be an object")
        fields = (
            "document_id",
            "title",
            "source_uri",
            "section",
            "language",
            "text",
            "content_sha256",
        )
        if any(not isinstance(value.get(field), str) or not value[field]
               for field in fields):
            raise IndexRefusal("index document has a missing field")
        if not value["source_uri"].startswith("medzen://synthetic/"):
            raise IndexRefusal("local index contains a non-synthetic source")
        if SHA256_RE.fullmatch(value["content_sha256"]) is None:
            raise IndexRefusal("document content hash is malformed")
        if _sha256(value["text"].encode("utf-8")) != value["content_sha256"]:
            raise IndexRefusal("document content hash mismatch")
        return Document(
            document_id=value["document_id"],
            title=value["title"],
            source_uri=value["source_uri"],
            section=value["section"],
            language=value["language"],
            text=value["text"],
            content_sha256=value["content_sha256"],
            title_tokens=_tokens(value["title"]),
            text_tokens=_tokens(value["text"]),
        )

    def search(self, query: str, *, language: str | None = None,
               top_k: int = 3) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        if not query_tokens:
            return []
        scored: list[tuple[float, Document]] = []
        for document in self.loaded.documents:
            if language is not None and document.language != language:
                continue
            # A title match is stronger than a body-only match. Both token
            # sets and the document-id tie break are stable, so the same
            # immutable snapshot always produces the same ranking.
            score = float(
                2 * len(query_tokens.intersection(document.title_tokens))
                + len(query_tokens.intersection(document.text_tokens))
            )
            if score > 0:
                scored.append((score, document))
        scored.sort(key=lambda item: (-item[0], item[1].document_id))
        citations: list[dict[str, Any]] = []
        for rank, (score, document) in enumerate(scored[:top_k], start=1):
            citations.append({
                "rank": rank,
                "document_id": document.document_id,
                "title": document.title,
                "source_uri": document.source_uri,
                "section": document.section,
                "content_sha256": document.content_sha256,
                "excerpt": document.text[:280],
                "score": score,
            })
        return citations
