#!/usr/bin/env python3
"""Freeze the DEVELOPMENT-ONLY checkpoint-selection surface for arm-1.

Codex round 13: the post-training sweep must run against a frozen,
version-bound dev selection — never a sealed set. This script derives
that selection deterministically from the tier-2 DEV pools (which the
tier-2 records construct disjoint from their sealed pools) and from the
kinyarwanda dev-selection manifest bound in B5-IMMUTABILITY-BINDINGS.

Rule (fixed, reviewable): per language, ROWS_PER_LANGUAGE rows split
evenly across the language's dev pools in the order listed below; within
a pool, the first rows by ascending audio_checksum_sha256. Every source
manifest is fetched from S3, its served VersionId recorded, and its bytes
hashed and compared to the committed record's sha256 — a drifted pool
refuses. Output: platform/manifests/B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "medzen-speech"
OUTPUT = ROOT / "platform/manifests/B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001.json"
ROWS_PER_LANGUAGE = 60
# arm-1's MEDZEN_LANGUAGES (packet B5-UNIVERSAL-ARM1-SAGEMAKER-BINDINGS-2026-005)
POOLS = {
    "english": ["eval/english/asr/fleurs-v1-tier2-dev/manifest.jsonl"],
    "ewe": ["eval/ewe/asr/soreva-v1-tier2-dev/manifest.jsonl"],
    "french": ["eval/french/asr/aaf-test-v1-tier2-dev/manifest.jsonl",
               "eval/french/asr/fleurs-v1-tier2-dev/manifest.jsonl"],
    "kinyarwanda": ["eval/kinyarwanda/asr/cv17-test-v1-dev-selection/manifest.jsonl"],
    "lingala": ["eval/lingala/asr/fleurs-v1-tier2-dev/manifest.jsonl",
                "eval/lingala/asr/soreva-v1-tier2-dev/manifest.jsonl"],
    "pidgin": ["eval/pidgin/asr/av-heldout-dev-e1/manifest.jsonl"],
    "swahili": ["eval/swahili/asr/fleurs-v1-tier2-dev/manifest.jsonl",
                "eval/swahili/asr/soreva-v1-tier2-dev/manifest.jsonl"],
}


def committed_sha256() -> dict[str, tuple[str, str]]:
    """key -> (sha256, record) from the tier-2 records and the
    immutability bindings (kinyarwanda dev-selection)."""
    out: dict[str, tuple[str, str]] = {}
    for record_path in sorted((ROOT / "platform/evidence").glob(
            "B5-TIER2-HOLDOUTS-*.json")):
        record = json.loads(record_path.read_bytes())
        for pools in record["pools"].values():
            for pool in pools:
                dev = pool.get("tier2-dev") or {}
                if dev.get("key") and dev.get("sha256"):
                    out[dev["key"]] = (dev["sha256"], record_path.name)
    protocol = json.loads((ROOT / "platform/decisions/"
                           "B5-KW-V2-GATE-PROTOCOL-2026-001.json").read_bytes())
    out[protocol["dev_half"]["key"]] = (
        protocol["dev_half"]["manifest_sha256"],
        "B5-KW-V2-GATE-PROTOCOL-2026-001.json")
    for key, (sha, _) in out.items():
        if not sha:
            raise SystemExit(f"{key}: no committed sha256 — refusing an "
                             "unpinned dev pool")
    return out


def main() -> int:
    s3 = boto3.client("s3", region_name="eu-central-1")
    pinned = committed_sha256()
    sources: dict[str, dict] = {}
    rows: list[dict] = []
    for language, keys in POOLS.items():
        per_pool = ROWS_PER_LANGUAGE // len(keys)
        for key in keys:
            response = s3.get_object(Bucket=BUCKET, Key=key)
            body = response["Body"].read()
            digest = hashlib.sha256(body).hexdigest()
            expected, record = pinned.get(key, ("", "UNPINNED"))
            if expected and digest != expected:
                raise SystemExit(
                    f"{key}: served bytes hash {digest[:12]} but {record} "
                    f"pins {expected[:12]} — the dev pool drifted, refusing")
            candidates = [json.loads(line) for line in body.decode().splitlines()
                          if line.strip()]
            for c in candidates:
                if c.get("primary_language", language) != language:
                    raise SystemExit(f"{key}: row of language "
                                     f"{c.get('primary_language')!r} in a "
                                     f"{language} pool")
            candidates.sort(key=lambda c: c["audio_checksum_sha256"])
            chosen = candidates[:per_pool]
            for c in chosen:
                rows.append({
                    "language": language,
                    "pool": key,
                    "audio_s3_uri": c["audio_filepath"],
                    "audio_checksum_sha256": c["audio_checksum_sha256"],
                    "reference": c["text_normalized"],
                })
            sources[key] = {
                "version_id": response["VersionId"],
                "etag": response["ETag"].strip('"'),
                "sha256": digest,
                "pinned_by": record,
                "pool_rows": len(candidates),
                "selected_rows": len(chosen),
                "kms_key_arn": response.get("SSEKMSKeyId"),
            }
    rows.sort(key=lambda r: (r["language"], r["audio_checksum_sha256"]))
    rows_sha256 = hashlib.sha256(json.dumps(
        rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    document = {
        "record": "B5-UNIVERSAL-ARM1-DEV-SELECTION-2026-001",
        "purpose": ("DEVELOPMENT-ONLY checkpoint-selection surface for "
                    "b5-universal-arm1-2026-005. Disjoint from every sealed "
                    "set by the tier-2 records' own construction. NEVER "
                    "promotion evidence (PROMOTION-PROTOCOL-2026-004: "
                    "selection and promotion use disjoint sets)."),
        "rule": (f"per language {ROWS_PER_LANGUAGE} rows split evenly across "
                 "the listed dev pools in order; within a pool the first rows "
                 "by ascending audio_checksum_sha256; source bytes verified "
                 "against the committed tier-2 sha256 before selection"),
        "languages": sorted(POOLS),
        "sources": sources,
        "rows": rows,
        "rows_sha256": rows_sha256,
    }
    OUTPUT.write_text(json.dumps(document, indent=1, sort_keys=True) + "\n")
    counts = {}
    for r in rows:
        counts[r["language"]] = counts.get(r["language"], 0) + 1
    print(f"wrote {OUTPUT.name}: {len(rows)} rows {counts} rows_sha256={rows_sha256[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
