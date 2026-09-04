#!/usr/bin/env python3
"""Build the executable scoring manifest for the eleven-language candidate.

Two refusals, both from real defects in this corpus:

* DUPLICATE AUDIO. eval/gbaya/asr/soreva-v1/manifest.jsonl carries 101 rows
  over 100 distinct clips. Scoring both rows counts one clip twice and skews
  its language's rate. Rows are therefore deduplicated by
  `audio_checksum_sha256`.

* CONFLICTING REFERENCES. That same duplicated Gbaya clip carries TWO
  different references ("gee-mɔ" and "ninꞌam nɛ́ paul"). At most one can be
  right, so silently keeping either would invent a ground truth. A checksum
  that appears twice with two different normalised references is a REFUSAL,
  not a dedup — the corpus has to be fixed, not papered over.

The surfaces come from the pinned eval-surface record, and only its
`primary_surfaces` are read: partitions and identical-audio duplicates are
recorded there precisely so they are never pooled with their parent.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SURFACE_RECORD = (
    ROOT / "platform/evidence/GB10-ELEVEN-LANGUAGE-EVAL-SURFACE-2026-001.json")
BUCKET = "medzen-speech"


class ScoringManifestRefusal(RuntimeError):
    """A defect that must be fixed in the corpus, not worked around here."""


def _normalise(value: str) -> str:
    return " ".join(str(value).split())


def collect_rows(surfaces: list[dict], fetch) -> list[dict]:
    """Deduplicate by audio checksum; refuse on a conflicting reference.

    `fetch(manifest_key) -> list[dict]` returns the manifest's parsed rows, so
    the caller owns S3 access and this stays unit-testable.
    """
    by_checksum: dict[str, dict] = {}
    duplicates = 0
    for surface in surfaces:
        for row in fetch(surface["manifest"]):
            checksum = row["audio_checksum_sha256"]
            reference = _normalise(row["text_normalized"])
            seen = by_checksum.get(checksum)
            if seen is None:
                by_checksum[checksum] = {
                    "audio_checksum_sha256": checksum,
                    "audio_filepath": row["audio_filepath"],
                    "text_normalized": reference,
                    "language": surface["language"],
                    "surface": surface["surface"],
                }
                continue
            duplicates += 1
            if seen["text_normalized"] != reference:
                raise ScoringManifestRefusal(
                    f"audio {checksum[:20]}… carries two different references — "
                    f"{seen['surface']}: {seen['text_normalized']!r} vs "
                    f"{surface['surface']}: {reference!r}. At most one can be "
                    "right; fix the eval corpus rather than choosing here.")
    rows = sorted(by_checksum.values(),
                  key=lambda r: (r["language"], r["audio_checksum_sha256"]))
    print(json.dumps({"status": "DEDUPLICATED", "rows": len(rows),
                      "duplicate_rows_dropped": duplicates}, sort_keys=True))
    return rows


def primary_surfaces(record: dict) -> list[dict]:
    out = []
    for language, buckets in record["per_language"].items():
        for entry in buckets["primary_surfaces"]:
            out.append({**entry, "language": language})
    return sorted(out, key=lambda s: s["surface"])


def _fetch_s3(client, expected: dict):
    def fetch(manifest_key: str) -> list[dict]:
        pin = expected[manifest_key]
        body = client.get_object(
            Bucket=BUCKET, Key=manifest_key,
            VersionId=pin["version_id"])["Body"].read()
        actual = hashlib.sha256(body).hexdigest()
        if actual != pin["sha256"]:
            raise ScoringManifestRefusal(
                f"{manifest_key} at VersionId {pin['version_id']} hashes to "
                f"{actual[:16]}, the record pins {pin['sha256'][:16]}")
        return [json.loads(line) for line in body.decode().splitlines()
                if line.strip()]
    return fetch


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--record", type=Path, default=SURFACE_RECORD)
    args = ap.parse_args()
    record = json.loads(args.record.read_bytes())
    surfaces = primary_surfaces(record)
    import boto3
    client = boto3.Session(profile_name="medzen",
                           region_name="eu-central-1").client("s3")
    expected = {s["manifest"]: s for s in surfaces}
    rows = collect_rows(surfaces, _fetch_s3(client, expected))
    body = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n"
                   for r in rows).encode()
    args.out.write_bytes(body)
    print(json.dumps({"out": str(args.out), "rows": len(rows),
                      "sha256": hashlib.sha256(body).hexdigest()},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ScoringManifestRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        sys.exit(2)
