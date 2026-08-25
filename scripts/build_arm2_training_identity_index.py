"""Producer of the Arm-2 TRAINING IDENTITY INDEX (Codex round 35 findings 1/5).

Builds the STRUCTURED, HASH-BOUND artifact
(B5-UNIVERSAL-ARM2-TRAINING-IDENTITY-INDEX-2026-001) that
scripts/mint_arm2_nomination_split.validate_training_index requires before any
FROZEN nomination mint: the exact per-row audio_checksum_sha256 set of the
gb9/gb8/gb3 training corpora, derived from listings whose raw bytes are
verified against the COMMITTED adoption records' complete_raw_sha256 — never
an arbitrary caller list.

PURE CORE, FAIL-CLOSED TO OFFLINE: :func:`build_training_identity_index`
operates only on in-memory corpus documents the caller passes in; it imports
NO AWS SDK and touches NO network. The live production runs ONLY inside the
owner-approved protected workflow under the DEDICATED training-index role
(medzen-arm2-training-index-role — scoped to curated/*, with eval/* reads
DENIED, so this producer can NEVER touch an eval or sealed object; the mint
role conversely cannot read curated/*). Training manifests contain transcript
text: the producer keeps ONLY audio_checksum_sha256 in memory and emits ONLY
identities + counts + aggregates — raw manifests never persist and never reach
a workstation.

This module PRODUCES NOTHING and reads NO S3 on import.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mint_arm2_nomination_split import (TRAINING_INDEX_RECORD,
                                        TRAINING_SOURCE_RECORDS, MintRefusal,
                                        _agg, _is_identity,
                                        committed_training_source_digests,
                                        load_packet,
                                        validate_caller_identity,
                                        validate_training_index)


class ProducerRefusal(RuntimeError):
    """Fail-closed: the training identity index could not be derived from the
    exact pinned corpus sources."""


def build_training_identity_index(corpus_documents: dict, *,
                                  committed=None) -> dict:
    """Build the structured artifact from in-memory corpus documents.

    ``corpus_documents`` maps each dataset ('gb9' | 'gb8' | 'gb3') to::

        {"complete_raw_bytes": bytes,          # the corpus COMPLETE listing
         "manifest_rows": {manifest_key: [row dict, ...], ...}}

    The listing bytes MUST hash to the committed adoption record's
    complete_raw_sha256 (the content-hash authority — stronger than a
    VersionId), every row must carry a well-formed audio_checksum_sha256, and
    the emitted artifact is self-validating: it round-trips through
    validate_training_index before being returned."""
    if committed is None:
        committed = committed_training_source_digests()
    if set(corpus_documents) != set(committed):
        raise ProducerRefusal(
            f"corpus documents {sorted(corpus_documents)} != the required "
            f"pinned corpora {sorted(committed)} — ALL pinned corpora are "
            "required (a partial index would report false zeros)")
    source_manifests = []
    identities: set[str] = set()
    row_count = 0
    for dataset in sorted(committed):
        doc = corpus_documents[dataset]
        raw = doc.get("complete_raw_bytes")
        if not isinstance(raw, (bytes, bytearray)) or not raw:
            raise ProducerRefusal(
                f"{dataset}: no complete-listing bytes were supplied")
        actual = hashlib.sha256(bytes(raw)).hexdigest()
        if actual != committed[dataset]:
            raise ProducerRefusal(
                f"{dataset}: complete listing hashes to {actual[:16]}, the "
                f"committed adoption record pins {committed[dataset][:16]} — "
                "refusing an unpinned corpus source")
        manifest_rows = doc.get("manifest_rows")
        if not isinstance(manifest_rows, dict) or not manifest_rows:
            raise ProducerRefusal(
                f"{dataset}: no manifest rows were supplied — an empty corpus "
                "contribution would report false zeros")
        dataset_rows = 0
        for key in sorted(manifest_rows):
            rows = manifest_rows[key]
            if not rows:
                raise ProducerRefusal(
                    f"{dataset}: manifest {key!r} contributed no rows")
            for row in rows:
                value = (row or {}).get("audio_checksum_sha256")
                if not _is_identity(value):
                    raise ProducerRefusal(
                        f"{dataset}: manifest {key!r} row carries a malformed "
                        f"audio_checksum_sha256 {str(value)[:24]!r}")
                identities.add(value)
                dataset_rows += 1
        source_manifests.append({
            "dataset": dataset,
            "source_record": TRAINING_SOURCE_RECORDS[dataset],
            "complete_raw_sha256": committed[dataset],
            "manifest_count": len(manifest_rows),
            "rows": dataset_rows,
        })
        row_count += dataset_rows
    if not identities:
        raise ProducerRefusal("no training identities were derived — refusing "
                              "an empty index")
    unique, aggregate = _agg(identities)
    artifact = {
        "record": TRAINING_INDEX_RECORD,
        "identity_key": "audio_checksum_sha256",
        "source_manifests": source_manifests,
        "producer": {
            "script": "scripts/build_arm2_training_identity_index.py",
            "role": "medzen-arm2-training-index-role",
            "environment": "arm2-nomination-mint",
            "note": "identities only — raw training manifests (which contain "
                    "transcript text) exist solely in the protected job's "
                    "memory and are never persisted or logged",
        },
        "row_count": row_count,
        "unique_count": unique,
        "aggregate_sha256": aggregate,
        "identities": sorted(identities),
    }
    try:
        validate_training_index(artifact, committed=committed)
    except MintRefusal as exc:                      # pragma: no cover - guard
        raise ProducerRefusal(
            f"internal error: the produced artifact fails its own consumer "
            f"validation: {exc}") from exc
    return artifact


def enumerate_corpus(get_object, dataset: str, *, committed=None) -> dict:
    """HASH-CHAINED corpus enumeration mirroring pipeline/train_asr.load_mix:
    fetch curated/_versions/<dataset>/COMPLETE.json, verify its RAW BYTES
    against the committed adoption record's complete_raw_sha256, derive every
    manifest key from the VERIFIED listing's own manifests table
    (curated/<lang/task/cfg>/<dataset>/manifest.jsonl), verify each manifest's
    bytes against the sha the listing declares, and keep ONLY the
    audio_checksum_sha256 of each row. ``get_object(key) -> bytes`` is
    INJECTED — this function has no AWS client and no ListBucket dependency
    (keys are derived from the verified listing, never from a bucket walk)."""
    if committed is None:
        committed = committed_training_source_digests()
    if dataset not in committed:
        raise ProducerRefusal(f"{dataset!r} is not a pinned training corpus")
    comp_key = f"curated/_versions/{dataset}/COMPLETE.json"
    comp_raw = get_object(comp_key)
    if not isinstance(comp_raw, (bytes, bytearray)) or not comp_raw:
        raise ProducerRefusal(f"{dataset}: no bytes for {comp_key}")
    actual = hashlib.sha256(bytes(comp_raw)).hexdigest()
    if actual != committed[dataset]:
        raise ProducerRefusal(
            f"{dataset}: {comp_key} hashes to {actual[:16]}, the committed "
            f"adoption record pins {committed[dataset][:16]} — refusing an "
            "unpinned corpus listing")
    comp = json.loads(bytes(comp_raw).decode())
    manifests = comp.get("manifests") or {}
    if not manifests:
        raise ProducerRefusal(f"{dataset}: the completion record lists no "
                              "manifests")
    manifest_rows: dict[str, list] = {}
    for label in sorted(manifests):
        declared_sha = str((manifests[label] or {}).get("sha256") or "")
        if len(label.split("/")) != 3:
            raise ProducerRefusal(
                f"{dataset}: manifest label {label!r} is not lang/task/cfg")
        key = f"curated/{label}/{dataset}/manifest.jsonl"
        body = get_object(key)
        if not isinstance(body, (bytes, bytearray)):
            raise ProducerRefusal(f"{dataset}: no bytes for {key}")
        got_sha = hashlib.sha256(bytes(body)).hexdigest()
        if got_sha != declared_sha:
            raise ProducerRefusal(
                f"{dataset}: {key} hashes to {got_sha[:16]}, the VERIFIED "
                f"completion record declares {declared_sha[:16] or '<absent>'}")
        rows = []
        for line in bytes(body).decode().splitlines():
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            # keep ONLY the identity — transcript text never leaves this scope
            rows.append({"audio_checksum_sha256":
                         row.get("audio_checksum_sha256")})
        manifest_rows[key] = rows
    return {"complete_raw_bytes": bytes(comp_raw),
            "manifest_rows": manifest_rows}


def produce_live(get_object) -> dict:
    """Enumerate ALL pinned corpora through the injected reader and build the
    artifact. Pure orchestration — no AWS import; the protected workflow wires
    the role-scoped reader."""
    documents = {dataset: enumerate_corpus(get_object, dataset)
                 for dataset in sorted(committed_training_source_digests())}
    return build_training_identity_index(documents)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--live", action="store_true",
                    help="produce the index from S3 — only inside the "
                         "owner-approved protected workflow under the "
                         "dedicated training-index role")
    args = ap.parse_args(argv)
    if not args.live:
        raise SystemExit(
            "offline invocation produces nothing: the pure core "
            "(build_training_identity_index) is driven by fixtures in "
            "tests/test_arm2_training_identity_index.py; the live production "
            "is the owner-approved protected-workflow step in the live-mint "
            "packet")
    # in-path exact identity assertion (Codex round 35 finding 7 pattern):
    # only the dedicated producer role may run the live production
    packet = load_packet()
    account = str((packet.get("aws") or {}).get("account") or "")
    producer = ((packet.get("training_identity_index") or {})
                .get("producer_role") or {})
    role_name = str(producer.get("role_name") or "")
    try:
        import boto3  # lazy: never imported on the offline/test path
    except ImportError as exc:
        raise SystemExit(
            "boto3 is unavailable — the live producer runs only inside the "
            "approved protected workflow with the dedicated role; refusing "
            f"({exc})")
    caller = boto3.client("sts").get_caller_identity()
    try:
        validate_caller_identity({"Account": caller.get("Account"),
                                  "Arn": caller.get("Arn")},
                                 account=account, role_name=role_name)
    except MintRefusal as exc:
        raise SystemExit(f"refusing the live production: {exc}")
    bucket = str((packet.get("aws") or {}).get("bucket") or "")
    s3 = boto3.client("s3")

    def get_object(key: str) -> bytes:
        return s3.get_object(Bucket=bucket, Key=key,
                             ExpectedBucketOwner=account)["Body"].read()

    artifact = produce_live(get_object)
    out = Path("training-identity-index.json")
    out.write_text(json.dumps(artifact, indent=1, sort_keys=True) + "\n")
    # print counts + aggregate ONLY — identities stay in the artifact file
    print(json.dumps({"status": "TRAINING_INDEX_PRODUCED",
                      "row_count": artifact["row_count"],
                      "unique_count": artifact["unique_count"],
                      "aggregate_sha256": artifact["aggregate_sha256"],
                      "file": str(out)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
