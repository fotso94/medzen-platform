#!/usr/bin/env python3
"""Freeze a deterministic post-selection Lingala holdout.

No transcript, speaker, session or row identifier is printed. The manifest is
written only to a caller-supplied temporary directory; --confirm conditionally
publishes it to its immutable S3 key and verifies the complete readback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


BUCKET = "medzen-speech"
REGION = "eu-central-1"
TRAIN_KEY = "curated/lingala/asr/lin_asr/v2/manifest.jsonl"
SELECTION_KEY = "eval/lingala/asr/v1/manifest.jsonl"
HOLDOUT_KEY = "eval/lingala/asr/v2-holdout/manifest.jsonl"
TRAIN_SHA256 = "44eb68ab534a8fe60d150a5df055d183da89dda8fdd7dbf57ea919c8627143a6"
SELECTION_SHA256 = "a1e033bfd734a5b7838b4abfd058700b08f077a19d519bdb751ea94bbd8a8feb"
SEED = "B4-HOLDOUT-2026-001"
TARGET_ROWS = 70


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse(raw: bytes) -> list[dict]:
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def canonical(rows: list[dict]) -> bytes:
    ordered = sorted(rows, key=lambda row: row["audio_checksum_sha256"])
    return ("\n".join(json.dumps(row, sort_keys=True, separators=(",", ":"))
                      for row in ordered) + "\n").encode()


def choose(train: list[dict], selection: list[dict]) -> tuple[list[dict], list[dict]]:
    selection_speakers = {row["speaker_id"] for row in selection}
    selection_sessions = {row["session_id"] for row in selection}
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in train:
        if row.get("split") != "train":
            continue
        if (row.get("speaker_id") in selection_speakers
                or row.get("session_id") in selection_sessions):
            continue
        groups[(row["speaker_id"], row["session_id"])].append(row)
    ranked = sorted(groups.items(), key=lambda item: sha(
        (SEED + "\0" + item[0][0] + "\0" + item[0][1]).encode()))
    holdout: list[dict] = []
    for _, rows in ranked:
        holdout.extend(rows)
        if len(holdout) >= TARGET_ROWS:
            break
    if len(holdout) < TARGET_ROWS:
        raise SystemExit(
            f"REFUSING: only {len(holdout)} disjoint Lingala rows available")

    holdout_checksums = {row["audio_checksum_sha256"] for row in holdout}
    holdout_text = {row["text_normalized"] for row in holdout}
    # Exact normalized-text twins outside the holdout must also be excluded
    # from training even though they are not scored as holdout rows.
    text_twins = [
        row for row in train
        if row["audio_checksum_sha256"] not in holdout_checksums
        and row.get("text_normalized") in holdout_text
    ]
    return holdout, text_twins


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--confirm", action="store_true")
    args = ap.parse_args()
    import boto3
    from botocore.exceptions import ClientError

    s3 = boto3.Session(
        profile_name="medzen", region_name=REGION).client("s3")
    train_raw = s3.get_object(Bucket=BUCKET, Key=TRAIN_KEY)["Body"].read()
    selection_raw = s3.get_object(
        Bucket=BUCKET, Key=SELECTION_KEY)["Body"].read()
    if sha(train_raw) != TRAIN_SHA256 or sha(selection_raw) != SELECTION_SHA256:
        raise SystemExit("REFUSING: Lingala source manifest identity changed")
    train, selection = parse(train_raw), parse(selection_raw)
    holdout, text_twins = choose(train, selection)

    holdout_speakers = {row["speaker_id"] for row in holdout}
    holdout_sessions = {row["session_id"] for row in holdout}
    training_after = [
        row for row in train
        if row["audio_checksum_sha256"] not in {
            item["audio_checksum_sha256"] for item in holdout + text_twins}
    ]
    selection_speakers = {row["speaker_id"] for row in selection}
    selection_sessions = {row["session_id"] for row in selection}
    if holdout_speakers & selection_speakers:
        raise SystemExit("REFUSING: holdout overlaps selection speakers")
    if holdout_sessions & selection_sessions:
        raise SystemExit("REFUSING: holdout overlaps selection sessions")
    if holdout_speakers & {row["speaker_id"] for row in training_after}:
        raise SystemExit("REFUSING: holdout overlaps remaining training speakers")
    if holdout_sessions & {row["session_id"] for row in training_after}:
        raise SystemExit("REFUSING: holdout overlaps remaining training sessions")
    if {row["text_normalized"] for row in holdout} & {
            row["text_normalized"] for row in training_after}:
        raise SystemExit("REFUSING: holdout normalized text overlaps training")

    body = canonical(holdout)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "manifest.jsonl").write_bytes(body)
    exclusions = sorted({
        row["audio_checksum_sha256"] for row in holdout + text_twins})
    (args.output_dir / "exclusions.json").write_text(json.dumps(
        exclusions, indent=2) + "\n")
    summary = {
        "record": "B4-LINGALA-POST-SELECTION-HOLDOUT",
        "id": "B4-HOLDOUT-2026-001",
        "status": "PREPARED" if not args.confirm else "PUBLISHED",
        "source_manifest_key": TRAIN_KEY,
        "source_manifest_sha256": TRAIN_SHA256,
        "selection_manifest_key": SELECTION_KEY,
        "selection_manifest_sha256": SELECTION_SHA256,
        "holdout_manifest_key": HOLDOUT_KEY,
        "holdout_manifest_sha256": sha(body),
        "rows": len(holdout),
        "minutes": round(sum(float(row["duration_s"]) for row in holdout) / 60, 2),
        "speakers": len(holdout_speakers),
        "sessions": len(holdout_sessions),
        "source_id_count": len({row["source_id"] for row in holdout}),
        "same_source_domain_as_training": True,
        "different_source_domain_available": False,
        "selection_speaker_overlap": 0,
        "selection_session_overlap": 0,
        "remaining_training_speaker_overlap": 0,
        "remaining_training_session_overlap": 0,
        "remaining_training_normalized_text_overlap": 0,
        "training_exclusion_rows": len(exclusions),
        "exact_text_twin_exclusions": len(text_twins),
        "selection_use_forbidden": True,
        "evaluation_timing": "after checkpoint selection only",
        "content_policy": "No transcript, audio, speaker, session or row identifier is persisted in the evidence summary."
    }
    if args.confirm:
        try:
            s3.put_object(
                Bucket=BUCKET, Key=HOLDOUT_KEY, Body=body,
                ContentType="application/jsonl", IfNoneMatch="*",
                ServerSideEncryption="aws:kms")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "PreconditionFailed":
                raise
            existing = s3.get_object(
                Bucket=BUCKET, Key=HOLDOUT_KEY)["Body"].read()
            if existing != body:
                raise SystemExit(
                    "REFUSING: occupied holdout key contains different bytes")
        readback = s3.get_object(Bucket=BUCKET, Key=HOLDOUT_KEY)["Body"].read()
        if readback != body:
            raise SystemExit("REFUSING: holdout readback differs")
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if not args.confirm:
        print("DRY RUN - no S3 writes; pass --confirm after reviewing summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
