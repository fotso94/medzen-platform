#!/usr/bin/env python3
"""Freeze a LARGER, independent development-only lingala regression sentinel.

Codex review #14 finding 1: the sweep's 60-row lingala slice is
statistically inconclusive. This selection takes EVERY lingala dev-pool
row (fleurs + soreva tier2-dev), version-bound and sha-verified against
the committed tier2 records, and PREDECLARES the non-inferiority rule
BEFORE any evaluation. Development-only — disjoint from every sealed set
by the tier2 records' construction. NEVER promotion evidence.
"""
from __future__ import annotations
import hashlib, json
from pathlib import Path
import boto3

ROOT = Path(__file__).resolve().parents[1]
BUCKET = "medzen-speech"
OUT = ROOT / "platform/manifests/B5-ARM1-LINGALA-SENTINEL-2026-001.json"
POOLS = ["eval/lingala/asr/fleurs-v1-tier2-dev/manifest.jsonl",
         "eval/lingala/asr/soreva-v1-tier2-dev/manifest.jsonl"]

def committed_sha() -> dict[str, str]:
    out = {}
    for rp in sorted((ROOT / "platform/evidence").glob("B5-TIER2-HOLDOUTS-*.json")):
        rec = json.loads(rp.read_bytes())
        for pools in rec["pools"].values():
            for pool in pools:
                dev = pool.get("tier2-dev") or {}
                if dev.get("key") and dev.get("sha256"):
                    out[dev["key"]] = dev["sha256"]
    return out

def main() -> int:
    s3 = boto3.client("s3", region_name="eu-central-1")
    pinned = committed_sha()
    rows, sources = [], {}
    for key in POOLS:
        resp = s3.get_object(Bucket=BUCKET, Key=key)
        body = resp["Body"].read()
        digest = hashlib.sha256(body).hexdigest()
        if pinned.get(key) and digest != pinned[key]:
            raise SystemExit(f"{key}: sha {digest[:12]} != committed {pinned[key][:12]} — drift")
        pool_rows = [json.loads(l) for l in body.decode().splitlines() if l.strip()]
        for c in pool_rows:
            if c.get("primary_language", "lingala") != "lingala":
                raise SystemExit(f"{key}: non-lingala row")
            rows.append({"language": "lingala", "pool": key,
                         "audio_s3_uri": c["audio_filepath"],
                         "audio_checksum_sha256": c["audio_checksum_sha256"],
                         "reference": c["text_normalized"]})
        sources[key] = {"version_id": resp["VersionId"], "sha256": digest,
                        "rows": len(pool_rows), "kms_key_arn": resp.get("SSEKMSKeyId")}
    rows.sort(key=lambda r: r["audio_checksum_sha256"])
    rows_sha = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    doc = {
        "record": "B5-ARM1-LINGALA-SENTINEL-2026-001",
        "purpose": ("LARGER independent development-only lingala regression sentinel "
                    "for arm-1 (Codex review #14 finding 1). Dev-only, never sealed, never promotion evidence."),
        "authority": "B5-UNIVERSAL-PILOT-DESIGN-2026-001 (lingala = strong regression sentinel, any loss disqualifying)",
        "predeclared_noninferiority_rule": {
            "declared_before_evaluation": True,
            "candidate": "step-0014000 (arm-1 best-macro checkpoint)",
            "baseline": "arm-1 base model (frozen zero-shot)",
            "metric": "word error rate (pooled), clustered bootstrap",
            "cluster_by": "audio_checksum_sha256 (row-level; placeholder-speaker pools have no meaningful speaker clusters)",
            "margin_abs_wer": 0.01,
            "alpha": 0.05,
            "iterations": 2000,
            "seed": 20260823,
            "decision": "NON_INFERIOR iff the upper (1-alpha) CI bound of (candidate_wer - baseline_wer) <= margin_abs_wer; also report the raw point estimate and CI. A regression beyond the margin CONFIRMS the preservation-gate failure and routes to Arm 2.",
        },
        "rows": rows, "rows_sha256": rows_sha, "sources": sources,
    }
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"wrote {OUT.name}: {len(rows)} lingala rows, rows_sha256={rows_sha[:12]}")
    for k, v in sources.items(): print(" ", k.split('/')[3], v["rows"], "rows", v["version_id"])
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
