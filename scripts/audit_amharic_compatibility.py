#!/usr/bin/env python3
"""Aggregate-only Phase A audit for PLAN-2026-005.

Private manifests and audio are streamed into memory, reduced immediately, and
never written or printed.  Output contains aggregate counts and distributions
only: no URI, checksum, transcript, speaker/session ID, token sequence or row.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import statistics
import sys
import tempfile
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.label_length import label_lengths  # noqa: E402
from pipeline.normalizers import for_language  # noqa: E402
from scripts import evaluate_candidate  # noqa: E402
from scripts import run_termination_diagnostic as prior  # noqa: E402

BUCKET = "medzen-speech"
TRAIN_KEY = "curated/amharic/asr/amh_asr/v2/manifest.jsonl"
TRAIN_SHA256 = "d560df103e211e0331aca105dd118bb2d287141f1de4be1f62ff5f8ef7dab606"
EVAL_KEY = "eval/amharic/asr/v1/manifest.jsonl"
EVAL_SHA256 = "7935560ca958dfb8aff1985829108cc2a5fde9b1cf49c0690d96299b3800643a"
POLICY = ROOT / "platform/decisions/DQ-2026-003-policy-deferral-corrected.json"
ADOPTION_KEY = prior.ADOPTION_KEY
BASE_MANIFEST_SHA256 = evaluate_candidate.BASE_MANIFEST_SHA256
TOKENIZER_FILES = (
    "added_tokens.json", "merges.txt", "normalizer.json",
    "special_tokens_map.json", "tokenizer.json", "tokenizer_config.json",
    "vocab.json",
)


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def s3_parts(uri: str) -> tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError("not an S3 URI")
    bucket, separator, key = uri[5:].partition("/")
    if not bucket or not separator or not key:
        raise ValueError("incomplete S3 URI")
    return bucket, key


def quantiles(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "min": None, "p25": None, "median": None,
                "p75": None, "max": None, "mean": None}
    ordered = sorted(float(value) for value in values)

    def percentile(fraction: float) -> float:
        position = fraction * (len(ordered) - 1)
        lower, upper = math.floor(position), math.ceil(position)
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1 - weight) + ordered[upper] * weight

    return {
        "count": len(ordered),
        "min": round(ordered[0], 6),
        "p25": round(percentile(0.25), 6),
        "median": round(statistics.median(ordered), 6),
        "p75": round(percentile(0.75), 6),
        "max": round(ordered[-1], 6),
        "mean": round(statistics.fmean(ordered), 6),
    }


def script_counts(text: str) -> dict[str, int]:
    counts = {"ethiopic": 0, "latin": 0, "other_letters": 0,
              "digits": 0}
    for char in text:
        if char.isdigit():
            counts["digits"] += 1
            continue
        if not char.isalpha():
            continue
        name = unicodedata.name(char, "")
        if "ETHIOPIC" in name:
            counts["ethiopic"] += 1
        elif "LATIN" in name:
            counts["latin"] += 1
        else:
            counts["other_letters"] += 1
    return counts


def parse_manifest(raw: bytes, expected_sha: str) -> list[dict]:
    if sha256(raw) != expected_sha:
        raise SystemExit("REFUSING: pinned manifest hash changed")
    rows = []
    for line in raw.decode().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def tokenizer(cli):
    from transformers import WhisperTokenizerFast

    raw_manifest = cli.get_object(
        Bucket=BUCKET,
        Key=f"{evaluate_candidate.BASE_PREFIX}/MANIFEST.json",
    )["Body"].read()
    if sha256(raw_manifest) != BASE_MANIFEST_SHA256:
        raise SystemExit("REFUSING: pinned base-model manifest changed")
    manifest = json.loads(raw_manifest)
    with tempfile.TemporaryDirectory(prefix="medzen-amharic-tokenizer-") as td:
        root = Path(td)
        for name in TOKENIZER_FILES:
            meta = manifest["files"].get(name)
            if not meta:
                raise SystemExit(
                    "REFUSING: pinned base manifest lacks a tokenizer file")
            body = cli.get_object(
                Bucket=BUCKET,
                Key=f"{evaluate_candidate.BASE_PREFIX}/{name}",
            )["Body"].read()
            if sha256(body) != meta["sha256"]:
                raise SystemExit("REFUSING: pinned tokenizer file changed")
            (root / name).write_bytes(body)
        loaded = WhisperTokenizerFast.from_pretrained(
            root, local_files_only=True)
        # The temporary source files may disappear; the loaded vocabulary is
        # fully resident and does not lazily reopen them.
        return loaded


def manifest_summary(rows: list[dict], tokenizer_obj) -> dict:
    norm = for_language("amharic")
    durations, token_lengths, tokens_per_s, tokens_per_char = [], [], [], []
    scripts = {"ethiopic": 0, "latin": 0, "other_letters": 0, "digits": 0}
    normalized_mismatch = 0
    empty_after_normalization = 0
    speaker_counts: dict[str, int] = {}
    session_counts: dict[str, int] = {}
    sources, releases, licences = set(), set(), set()
    for row in rows:
        text = row["text_normalized"]
        normalized = norm(row["text_verbatim"])
        normalized_mismatch += int(normalized != text)
        empty_after_normalization += int(not text.strip())
        _, effective = label_lengths(tokenizer_obj, text, "amharic")
        duration = float(row["duration_s"])
        letters = script_counts(text)
        for key, count in letters.items():
            scripts[key] += count
        letter_count = sum(letters.values())
        durations.append(duration)
        token_lengths.append(effective)
        tokens_per_s.append(effective / duration)
        if letter_count:
            tokens_per_char.append(effective / letter_count)
        speaker_counts[row["speaker_id"]] = (
            speaker_counts.get(row["speaker_id"], 0) + 1)
        session_counts[row["session_id"]] = (
            session_counts.get(row["session_id"], 0) + 1)
        sources.add(row["source_id"])
        releases.add(row["dataset_release"])
        licences.add(row["license_policy"])
    script_total = sum(scripts.values())
    return {
        "rows": len(rows),
        "minutes": round(sum(durations) / 60.0, 4),
        "speakers": len(speaker_counts),
        "sessions": len(session_counts),
        "largest_speaker_row_share": round(
            max(speaker_counts.values(), default=0) / max(len(rows), 1), 6),
        "largest_session_row_share": round(
            max(session_counts.values(), default=0) / max(len(rows), 1), 6),
        "duration_s": quantiles(durations),
        "effective_label_tokens": quantiles(token_lengths),
        "effective_tokens_per_s": quantiles(tokens_per_s),
        "effective_tokens_per_letter": quantiles(tokens_per_char),
        "unicode_letter_share": {
            key: round(value / max(script_total, 1), 6)
            for key, value in scripts.items()
        },
        "normalized_mismatch_rows": normalized_mismatch,
        "empty_after_normalization_rows": empty_after_normalization,
        "source_count": len(sources),
        "dataset_release_count": len(releases),
        "licence_policy_count": len(licences),
    }


def audio_summary(cli, rows: list[dict], workers: int) -> dict:
    import numpy as np
    import soundfile as sf

    def one(row: dict) -> dict:
        result = {
            "decoded": False, "checksum_match": False,
            "declared_format_match": False,
        }
        try:
            bucket, key = s3_parts(row["audio_filepath"])
            if bucket != BUCKET:
                return result
            body = cli.get_object(Bucket=bucket, Key=key)["Body"].read()
            result["checksum_match"] = (
                sha256(body) == row["audio_checksum_sha256"])
            audio, rate = sf.read(
                io.BytesIO(body), dtype="float32", always_2d=True)
            channels = int(audio.shape[1])
            mono = audio.mean(axis=1, dtype=np.float64)
            result.update({
                "decoded": True,
                "declared_format_match": (
                    rate == row["sample_rate"] == 16000
                    and channels == row["channels"] == 1),
                "decoded_duration_s": len(mono) / float(rate),
                "declared_duration_s": float(row["duration_s"]),
                "rms_dbfs": 20.0 * math.log10(max(
                    float(np.sqrt(np.mean(np.square(mono)))), 1e-12)),
                "near_silence_fraction": float(np.mean(np.abs(mono) < 0.001)),
                "clipping_fraction": float(np.mean(np.abs(mono) >= 0.999)),
                "dc_offset_abs": abs(float(np.mean(mono))),
            })
        except Exception:  # noqa: BLE001
            # Aggregate the failure; never include the private row or URI.
            pass
        return result

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        measured = list(pool.map(one, rows))
    decoded = [row for row in measured if row["decoded"]]
    return {
        "rows": len(rows),
        "decode_failures": len(rows) - len(decoded),
        "checksum_mismatches": sum(
            not row["checksum_match"] for row in measured),
        "declared_format_mismatches": sum(
            not row["declared_format_match"] for row in measured),
        "duration_absolute_error_s": quantiles([
            abs(row["decoded_duration_s"] - row["declared_duration_s"])
            for row in decoded]),
        "rms_dbfs": quantiles([row["rms_dbfs"] for row in decoded]),
        "near_silence_fraction": quantiles([
            row["near_silence_fraction"] for row in decoded]),
        "clipping_fraction": quantiles([
            row["clipping_fraction"] for row in decoded]),
        "dc_offset_abs": quantiles([
            row["dc_offset_abs"] for row in decoded]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", default="medzen")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--skip-audio", action="store_true")
    args = parser.parse_args()

    import boto3
    import transformers

    session = boto3.Session(
        profile_name=args.profile, region_name=prior.REGION)
    cli = session.client("s3", region_name=prior.REGION)
    policy_raw = POLICY.read_bytes()
    policy = json.loads(policy_raw)
    adoption = json.loads(cli.get_object(
        Bucket=BUCKET, Key=ADOPTION_KEY)["Body"].read())
    if adoption.get("deferral_policy_sha256") != sha256(policy_raw):
        raise SystemExit("REFUSING: adoption and deferral policy differ")
    exclusions = {
        row["audio_checksum_sha256"]
        for row in policy["exclusions"] if row["language"] == "amharic"}
    if len(exclusions) != 4:
        raise SystemExit("REFUSING: expected four Amharic policy deferrals")

    train_raw = cli.get_object(Bucket=BUCKET, Key=TRAIN_KEY)["Body"].read()
    eval_raw = cli.get_object(Bucket=BUCKET, Key=EVAL_KEY)["Body"].read()
    train_all = parse_manifest(train_raw, TRAIN_SHA256)
    eval_rows = parse_manifest(eval_raw, EVAL_SHA256)
    eligible_train = [
        row for row in train_all
        if row.get("split") == "train"
        and "asr_train" in (row.get("allowed_use") or [])
        and row["audio_checksum_sha256"] not in exclusions]
    source_test = [row for row in train_all if row.get("split") == "test"]
    exclusion_hits = sum(
        row["audio_checksum_sha256"] in exclusions for row in train_all)
    if exclusion_hits != 4 or len(eligible_train) != 271:
        raise SystemExit(
            "REFUSING: Amharic eligible/deferral counts changed")
    if len(eval_rows) != 25:
        raise SystemExit("REFUSING: frozen Amharic evaluation count changed")

    train_checksums = {row["audio_checksum_sha256"] for row in eligible_train}
    eval_checksums = {row["audio_checksum_sha256"] for row in eval_rows}
    source_test_checksums = {
        row["audio_checksum_sha256"] for row in source_test}
    train_speakers = {row["speaker_id"] for row in eligible_train}
    eval_speakers = {row["speaker_id"] for row in eval_rows}
    train_sessions = {row["session_id"] for row in eligible_train}
    eval_sessions = {row["session_id"] for row in eval_rows}
    token = tokenizer(cli)
    record = {
        "record": "COMPAT-2026-002-AMHARIC-AGGREGATE-AUDIT",
        "recorded_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "COMPLETED" if not args.skip_audio else "MANIFEST_ONLY",
        "purpose": "training_system_validation",
        "promotable": False,
        "training_steps": 0,
        "pins": {
            "train_manifest_sha256": TRAIN_SHA256,
            "eval_manifest_sha256": EVAL_SHA256,
            "policy_sha256": sha256(policy_raw),
            "adoption_key": ADOPTION_KEY,
            "base_manifest_sha256": BASE_MANIFEST_SHA256,
            "tokenizer_repo": evaluate_candidate.BASE_MODEL,
            "tokenizer_revision": evaluate_candidate.BASE_REVISION,
            "transformers": transformers.__version__,
        },
        "counts": {
            "source_manifest_rows": len(train_all),
            "source_train_split_rows_before_deferral": (
                len(eligible_train) + exclusion_hits),
            "source_test_split_rows": len(source_test),
            "train_policy_deferred_rows": exclusion_hits,
            "train_eligible_rows": len(eligible_train),
            "eval_rows": len(eval_rows),
        },
        "overlap": {
            "eligible_train_eval_audio_checksums": len(
                train_checksums & eval_checksums),
            "eligible_train_eval_speaker_ids": len(
                train_speakers & eval_speakers),
            "eligible_train_eval_session_ids": len(
                train_sessions & eval_sessions),
            "frozen_eval_equals_source_test_checksums": (
                source_test_checksums == eval_checksums),
        },
        "train": manifest_summary(eligible_train, token),
        "eval": manifest_summary(eval_rows, token),
        "audio": None,
        "content_policy": (
            "aggregate values only; no URI, checksum, transcript, token "
            "sequence, speaker/session ID or audio persisted or printed"),
    }
    if not args.skip_audio:
        record["audio"] = {
            "train": audio_summary(cli, eligible_train, args.workers),
            "eval": audio_summary(cli, eval_rows, args.workers),
        }
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
