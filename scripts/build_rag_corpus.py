#!/usr/bin/env python3
"""Build, publish and ingest the MedZen speech RAG corpus (Bedrock KB backend).

  python3 scripts/build_rag_corpus.py --languages en,fr,kin,pcm,swa --upload --ingest

Reads platform/rag-corpus/product-v1/<lang>/*.md, turns every "## " section
into its own S3 object (one Bedrock chunk each: the data source uses NONE
chunking) with a metadata sidecar for corpus/language filtering, lists the
curated WHO PDFs under speech-rag/v1/clinical/, and writes the corpus manifest
the rag-index service binds to (platform/testdata/rag-bedrock-dev). The
manifest sha256 is the RAG snapshot identity the registry route must name.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "platform/rag-corpus/product-v1"
OUT = ROOT / "platform/testdata/rag-bedrock-dev"
BUCKET = "medzen-knowledge-eu"
PREFIX = "speech-rag/v1"
REGION = "eu-central-1"
KB_ID = "A1CWHQJN6W"
VECTOR_INDEX_ARN = ("arn:aws:s3vectors:eu-central-1:558069890522:bucket/"
                    "medzen-speech-vectors/index/medzen-rag-v1")
DATA_SOURCES = {"product": "JYQ3K4U1NC", "clinical": "CSE1AWYXGI"}
INDEX_VERSION = "medzen-kb-v1"
CLASSIFICATION = "NONPROD_REAL_CONTENT_V1"
CLINICAL_TITLES = {
    "hearts/hearts-cvd-risk-management.pdf":
        "WHO HEARTS: cardiovascular disease risk management (2020)",
    "hearts/hearts-technical-package.pdf":
        "WHO HEARTS technical package for cardiovascular disease management in primary care (2020)",
    "hiv/hiv-consolidated-art.pdf":
        "WHO consolidated guidelines on HIV prevention, testing, treatment and service delivery (2021)",
    "malaria/malaria-case-management.pdf":
        "WHO malaria case management operational manual (2021)",
    "malaria/who-malaria-guidelines.pdf": "WHO guidelines for malaria (2023)",
    "maternal/anc-positive-pregnancy-2016.pdf":
        "WHO recommendations on antenatal care for a positive pregnancy experience (2016)",
    "maternal/intrapartum-care-2018.pdf":
        "WHO recommendations on intrapartum care for a positive childbirth experience (2018)",
    "maternal/postnatal-care-2022.pdf":
        "WHO recommendations on maternal and newborn care for a positive postnatal experience (2022)",
    "pen/who-pen-primary-care.pdf":
        "WHO package of essential noncommunicable disease interventions for primary health care (2020)",
    "pen/who-pen-protocols-low-resource.pdf":
        "WHO PEN protocols for primary health care in low-resource settings (2018)",
    "tb/tb-consolidated-treatment.pdf":
        "WHO consolidated guidelines on tuberculosis treatment (2022)",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def split_document(text: str) -> tuple[str, list[tuple[str, str]]]:
    lines = text.strip().splitlines()
    if not lines or not lines[0].startswith("# "):
        raise SystemExit("document must start with a '# ' title line")
    title = lines[0][2:].strip()
    sections = []
    for block in re.split(r"\n(?=## )", text.strip())[1:]:
        heading, _, body = block.partition("\n")
        heading = heading[3:].strip()
        if not heading or not body.strip():
            raise SystemExit(f"empty section in document '{title}'")
        sections.append((heading, body.strip()))
    if not sections:
        raise SystemExit(f"document '{title}' has no '## ' sections")
    return title, sections


def build_product(languages: list[str]) -> tuple[list[dict], list[dict]]:
    objects: list[dict] = []
    documents: list[dict] = []
    for lang in languages:
        paths = sorted((CORPUS / lang).glob("*.md"))
        if not paths:
            raise SystemExit(f"no documents for language '{lang}' under {CORPUS}")
        for path in paths:
            doc = path.stem
            title, sections = split_document(path.read_text(encoding="utf-8"))
            for index, (heading, body) in enumerate(sections, start=1):
                stem = f"{doc}--s{index:02d}"
                key = f"{PREFIX}/product/{lang}/{stem}.md"
                content = f"# {title}\n\n## {heading}\n\n{body}\n"
                document_id = f"product/{lang}/{doc}/s{index:02d}"
                objects.append({
                    "key": key,
                    "body": content.encode("utf-8"),
                    "metadata": {"metadataAttributes": {
                        "corpus": "product", "language": lang, "doc": doc,
                        "section_index": index, "document_id": document_id,
                        "title": title, "section": heading}},
                })
                documents.append({
                    "document_id": document_id,
                    "corpus": "product",
                    "language": lang,
                    "title": f"{title} - {heading}",
                    "section": heading,
                    "source_uri": f"s3://{BUCKET}/{key}",
                    "citation_uri": f"medzen://corpus/product/{lang}/{stem}",
                    "content_sha256": sha256_text(content.strip()),
                })
    return objects, documents


def list_clinical(s3) -> list[dict]:
    documents = []
    prefix = f"{PREFIX}/clinical/"
    paginator = s3.get_paginator("list_objects_v2")
    keys = [o["Key"] for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix)
            for o in page.get("Contents", [])]
    for key in sorted(k for k in keys if k.endswith(".pdf")):
        relative = key[len(prefix):]
        sidecar = key + ".metadata.json"
        language = "en"
        if sidecar in keys:
            meta = json.loads(s3.get_object(Bucket=BUCKET, Key=sidecar)["Body"].read())
            language = str(meta.get("metadataAttributes", {}).get("language", "en"))
        body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        documents.append({
            "document_id": "clinical/who/" + re.sub(r"[^a-z0-9]+", "-", relative[:-4].lower()).strip("-"),
            "corpus": "clinical",
            "language": language,
            "title": CLINICAL_TITLES.get(relative, relative),
            "section": "guideline",
            "source_uri": f"s3://{BUCKET}/{key}",
            "citation_uri": f"medzen://corpus/clinical/{relative}",
            "content_sha256": hashlib.sha256(body).hexdigest(),
        })
    return documents


def upload_product(s3, objects: list[dict]) -> None:
    wanted = set()
    for item in objects:
        wanted.add(item["key"])
        wanted.add(item["key"] + ".metadata.json")

    def put(item: dict) -> None:
        s3.put_object(Bucket=BUCKET, Key=item["key"], Body=item["body"],
                      ContentType="text/markdown; charset=utf-8")
        s3.put_object(Bucket=BUCKET, Key=item["key"] + ".metadata.json",
                      Body=json.dumps(item["metadata"]).encode("utf-8"),
                      ContentType="application/json")

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(put, objects))
    prefix = f"{PREFIX}/product/"
    existing = [o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(
        Bucket=BUCKET, Prefix=prefix) for o in page.get("Contents", [])]
    stale = [k for k in existing if k not in wanted]
    for start in range(0, len(stale), 1000):
        s3.delete_objects(Bucket=BUCKET, Delete={
            "Objects": [{"Key": k} for k in stale[start:start + 1000]]})
    print(f"uploaded {len(objects)} sections (+ sidecars); removed {len(stale)} stale objects")


def write_manifest(documents: list[dict]) -> str:
    manifest = {
        "schema_version": 1,
        "backend": "bedrock",
        "classification": CLASSIFICATION,
        "index_version": INDEX_VERSION,
        "knowledge_base": {
            "id": KB_ID, "region": REGION,
            "embedding_model": "amazon.titan-embed-text-v2:0",
            "vector_index_arn": VECTOR_INDEX_ARN,
        },
        "corpora": {
            "product": {"data_source_id": DATA_SOURCES["product"],
                        "filter": {"corpus": "product"},
                        "language_filter": True, "chunking": "NONE"},
            "clinical": {"data_source_id": DATA_SOURCES["clinical"],
                         "filter": {"source": "who"},
                         "language_filter": False,
                         "chunking": "FIXED_SIZE_250_TOKENS_20_PERCENT_OVERLAP"},
        },
        "documents": sorted(documents, key=lambda d: d["document_id"]),
    }
    raw = (json.dumps(manifest, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    (OUT / "indexes").mkdir(parents=True, exist_ok=True)
    (OUT / "aliases").mkdir(parents=True, exist_ok=True)
    (OUT / "indexes" / f"{INDEX_VERSION}.json").write_bytes(raw)
    (OUT / "aliases" / "current.json").write_text(json.dumps({
        "alias": "current", "manifest_path": f"indexes/{INDEX_VERSION}.json",
        "manifest_sha256": digest, "schema_version": 1}, indent=1, sort_keys=True) + "\n")
    return digest


def ingest(agent, corpus: str) -> None:
    job = agent.start_ingestion_job(knowledgeBaseId=KB_ID, dataSourceId=DATA_SOURCES[corpus],
                                    description=f"build_rag_corpus {corpus}")["ingestionJob"]
    job_id = job["ingestionJobId"]
    for _ in range(180):
        job = agent.get_ingestion_job(knowledgeBaseId=KB_ID, dataSourceId=DATA_SOURCES[corpus],
                                      ingestionJobId=job_id)["ingestionJob"]
        if job["status"] in ("COMPLETE", "FAILED", "STOPPED"):
            break
        time.sleep(10)
    stats = job.get("statistics", {})
    print(f"ingestion {corpus} {job_id}: {job['status']} scanned={stats.get('numberOfDocumentsScanned')} "
          f"indexed={stats.get('numberOfNewDocumentsIndexed')} modified={stats.get('numberOfModifiedDocumentsIndexed')} "
          f"deleted={stats.get('numberOfDocumentsDeleted')} failed={stats.get('numberOfDocumentsFailed')} "
          f"reasons={job.get('failureReasons')}")
    if job["status"] != "COMPLETE" or stats.get("numberOfDocumentsFailed"):
        raise SystemExit(f"ingestion {corpus} did not complete cleanly")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--languages", default="en,fr,kin,pcm,swa")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--ingest", action="store_true", help="ingest the product data source after upload")
    parser.add_argument("--ingest-clinical", action="store_true")
    args = parser.parse_args()
    languages = [item.strip() for item in args.languages.split(",") if item.strip()]
    objects, documents = build_product(languages)
    print(f"product sections: {len(objects)} across {len(languages)} language(s): "
          + ", ".join(f"{lang}={sum(1 for d in documents if d['language'] == lang)}" for lang in languages))
    import boto3
    s3 = boto3.client("s3", region_name=REGION)
    if args.upload:
        upload_product(s3, objects)
    clinical = list_clinical(s3)
    print(f"clinical documents: {len(clinical)}")
    digest = write_manifest(documents + clinical)
    print(f"manifest {INDEX_VERSION}: {len(documents) + len(clinical)} documents, sha256={digest}")
    if args.ingest or args.ingest_clinical:
        agent = boto3.client("bedrock-agent", region_name=REGION)
        if args.ingest:
            ingest(agent, "product")
        if args.ingest_clinical:
            ingest(agent, "clinical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
