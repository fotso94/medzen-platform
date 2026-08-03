#!/usr/bin/env python3
"""Audit the frozen validation labels against the exact Whisper contract.

The Option B base arm exhausted ``max_new_tokens=440`` on every Amharic row.
That can mean model non-termination, but it can also mean the reference itself
needs more generated tokens than the evaluator permits.  Those are different
failures and must not be guessed apart.

This audit uses the same pinned tokenizer, language mapping and prefix contract
as training.  It stores aggregate counts and manifest hashes only: no
transcript, audio, speaker, session or per-row identifier is printed or
persisted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.generation import MAX_NEW_TOKENS  # noqa: E402
from pipeline.label_length import decoder_start_id  # noqa: E402
from pipeline.languages import LANG_TOKEN  # noqa: E402
from pipeline.validation_runner import frozen_validation  # noqa: E402
from scripts.audit_label_lengths import (  # noqa: E402
    BASE_MODEL,
    BASE_REVISION,
    BUCKET,
    TOKENIZER_CACHE_FILES,
    client,
    pinned_tokenizer,
)

MODEL_LABEL_LIMIT = 448
EOT = "<|endoftext|>"


def _distribution(values: list[float | int]) -> dict:
    if not values:
        return {"min": None, "median": None, "max": None, "mean": None}
    return {
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
        "mean": round(statistics.fmean(values), 3),
    }


def audit_language_rows(tokenizer, language: str, rows: list[dict],
                        model_limit: int = MODEL_LABEL_LIMIT,
                        generation_cap: int = MAX_NEW_TOKENS) -> dict:
    """Return aggregate contract measurements without retaining row content."""
    token = LANG_TOKEN[language]
    tokenizer.set_prefix_tokens(language=token, task="transcribe")
    prefix = list(tokenizer.prefix_tokens)
    sot = decoder_start_id(tokenizer)
    eot = tokenizer.convert_tokens_to_ids(EOT)
    if not prefix or prefix[0] != sot:
        raise SystemExit(
            f"REFUSING: {language} prefix does not begin with decoder start")
    if eot is None or int(eot) < 0:
        raise SystemExit("REFUSING: tokenizer has no end-of-transcript token")

    raw_lengths: list[int] = []
    effective_lengths: list[int] = []
    reference_generated_lengths: list[int] = []
    durations: list[float] = []
    wrong_prefix = missing_eot = over_model = over_generation = 0

    for row in rows:
        # set_prefix_tokens is repeated because this is the exact mutable
        # tokenizer boundary used by the multilingual training Dataset.
        tokenizer.set_prefix_tokens(language=token, task="transcribe")
        ids = list(tokenizer(row["text_normalized"]).input_ids)
        raw = len(ids)
        effective = raw - 1 if ids and ids[0] == sot else raw
        generated = raw - len(prefix) if ids[:len(prefix)] == prefix else raw

        wrong_prefix += int(ids[:len(prefix)] != prefix)
        missing_eot += int(not ids or ids[-1] != eot)
        over_model += int(effective > model_limit)
        over_generation += int(generated > generation_cap)
        raw_lengths.append(raw)
        effective_lengths.append(effective)
        reference_generated_lengths.append(generated)
        durations.append(float(row["duration_s"]))

    return {
        "rows": len(rows),
        "whisper_language_token": token,
        "prefix_tokens": len(prefix),
        "raw_label_tokens": _distribution(raw_lengths),
        "effective_model_label_tokens": _distribution(effective_lengths),
        "reference_generated_tokens_including_eos":
            _distribution(reference_generated_lengths),
        "duration_s": _distribution(durations),
        "wrong_prefix_rows": wrong_prefix,
        "missing_eos_target_rows": missing_eot,
        "rows_over_model_label_limit": over_model,
        "rows_over_generation_cap": over_generation,
        "model_label_limit": model_limit,
        "generation_cap": generation_cap,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--out", default="/tmp/validation-label-contract-audit.json")
    args = ap.parse_args()

    cli = client()
    tokenizer, cache_manifest = pinned_tokenizer(cli)
    frozen, frozen_sha = frozen_validation()
    per_language = {}

    for language, info in frozen["sets"].items():
        key = info["key"].removeprefix(f"s3://{BUCKET}/")
        body = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        digest = hashlib.sha256(body).hexdigest()
        if digest != info["manifest_sha256"]:
            raise SystemExit(
                f"REFUSING: {language} validation manifest hashes "
                f"{digest[:16]}, frozen record requires "
                f"{info['manifest_sha256'][:16]}")
        rows = [
            json.loads(line) for line in body.decode().splitlines()
            if line.strip()
        ]
        if len(rows) != info["rows"]:
            raise SystemExit(
                f"REFUSING: {language} has {len(rows)} rows, frozen record "
                f"declares {info['rows']}")
        per_language[language] = {
            "manifest_sha256": digest,
            **audit_language_rows(tokenizer, language, rows),
        }

    root = Path(__file__).resolve().parent.parent
    git_sha = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True).stdout.strip()
    git_dirty = bool(subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True, text=True, check=True).stdout.strip())
    record = {
        "record": "VAL-2026-001-LABEL-CONTRACT-AUDIT",
        "recorded_utc":
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "content_policy": (
            "aggregate token lengths and manifest hashes only; no transcript, "
            "audio, checksum, speaker, session or per-row result persisted"),
        "tokenizer": {
            "repo": BASE_MODEL,
            "revision": BASE_REVISION,
            "source": "sha256-verified S3 base cache",
            "cache_files_verified": len(TOKENIZER_CACHE_FILES),
            "model_weights_downloaded": False,
        },
        "frozen_validation_record_sha256": frozen_sha,
        "model_label_limit": MODEL_LABEL_LIMIT,
        "generation_cap": MAX_NEW_TOKENS,
        "verifier": {
            "path": "scripts/audit_validation_label_contract.py",
            "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "git_sha": git_sha,
            "git_dirty": git_dirty,
        },
        "per_language": per_language,
        "totals": {
            "rows": sum(v["rows"] for v in per_language.values()),
            "wrong_prefix_rows":
                sum(v["wrong_prefix_rows"] for v in per_language.values()),
            "missing_eos_target_rows":
                sum(v["missing_eos_target_rows"] for v in per_language.values()),
            "rows_over_model_label_limit":
                sum(v["rows_over_model_label_limit"]
                    for v in per_language.values()),
            "rows_over_generation_cap":
                sum(v["rows_over_generation_cap"]
                    for v in per_language.values()),
        },
    }
    Path(args.out).write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "record": record["record"],
        "out": args.out,
        "totals": record["totals"],
        "amharic": record["per_language"]["amharic"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
