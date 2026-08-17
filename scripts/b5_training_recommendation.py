#!/usr/bin/env python3
"""Per-language training recommendation (task E). FAILS CLOSED to HOLD.

Consumes the suite merge report (the eval ground truth) and a licence/hours
scan of the gb1 training zone, and emits the decision table the OWNER
chooses the campaign language list from. The tool recommends; it never
decides — the owner approves or extends the list (SA-blocked languages
cannot be added by approval alone; the ShareAlike legal review must
conclude first, and the table says so per language).

Recommendation rules, explicit and ordered (first match wins):
  BLOCKED_PENDING_LEGAL   trainable data exists but ALL of it is
                          sharealike_review
  NO_EVAL_BASELINE        no CTC-unconditioned row in the merge report —
                          nothing for the T6 gate to beat, so training
                          cannot be gated honestly
  INSUFFICIENT_DATA       < MINIMUM_TRAINABLE_HOURS of clear+attribution
                          audio
  TRAIN                   otherwise; ATTRIBUTION_REQUIRED flagged when
                          cc_by_4_0 hours are part of the pool
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.licence_filter import (  # noqa: E402
    KNOWN_POLICIES,
    LEGAL_REVIEW_PENDING,
    NEVER_TRAIN,
    TRAIN_ATTRIBUTION,
    TRAIN_CLEAR,
)

BASELINE_CANDIDATE = "omniASR_CTC_1B_v2"
BASELINE_MODE = "unconditioned"
MINIMUM_TRAINABLE_HOURS = 1.0  # below this a LoRA pass memorizes, not adapts


class RecommendationRefusal(RuntimeError):
    pass


def licence_hours_from_manifest_rows(rows: list[dict]) -> dict[str, float]:
    """Sum trainable audio seconds per licence bucket for one language."""
    buckets = {"clear": 0.0, "attribution": 0.0, "blocked_sharealike": 0.0,
               "never_train": 0.0}
    for row in rows:
        if row.get("split") != "train" or "asr_train" not in (row.get("allowed_use") or []):
            continue
        policy = row.get("license_policy")
        if not isinstance(policy, str) or policy not in KNOWN_POLICIES:
            raise RecommendationRefusal(
                f"row carries unusable license_policy {policy!r} — the gb1 "
                "byte-duplicate/licence guards should have refused this at ingest")
        seconds = float(row.get("duration_s", 0.0))
        if policy in TRAIN_CLEAR:
            buckets["clear"] += seconds
        elif policy in TRAIN_ATTRIBUTION:
            buckets["attribution"] += seconds
        elif policy in LEGAL_REVIEW_PENDING:
            buckets["blocked_sharealike"] += seconds
        elif policy in NEVER_TRAIN:
            buckets["never_train"] += seconds
    return {k: round(v / 3600, 3) for k, v in buckets.items()}


def recommend_language(
    language: str,
    eval_row: dict | None,
    hours: dict[str, float],
) -> dict:
    trainable = hours["clear"] + hours["attribution"]
    entry = {
        "language": language,
        "eval_baseline": (
            {"wer": eval_row["wer"], "cer": eval_row["cer"]} if eval_row else None
        ),
        "hours": hours,
        "trainable_hours": round(trainable, 3),
    }
    if trainable == 0.0 and hours["blocked_sharealike"] > 0:
        entry["recommendation"] = "BLOCKED_PENDING_LEGAL"
        entry["basis"] = (f"{hours['blocked_sharealike']}h exist but every "
                          "trainable second is sharealike_review; the "
                          "ShareAlike-on-weights legal review (Base v5 B9) "
                          "must conclude before this language can train")
    elif eval_row is None or eval_row.get("wer") is None:
        entry["recommendation"] = "NO_EVAL_BASELINE"
        entry["basis"] = ("no CTC-unconditioned baseline in the merge report; "
                          "the T6 gate would have nothing to beat")
    elif trainable < MINIMUM_TRAINABLE_HOURS:
        entry["recommendation"] = "INSUFFICIENT_DATA"
        entry["basis"] = (f"only {trainable}h of clear+attribution audio "
                          f"(minimum {MINIMUM_TRAINABLE_HOURS}h)")
    else:
        entry["recommendation"] = "TRAIN"
        entry["attribution_required"] = hours["attribution"] > 0
        entry["basis"] = (f"{trainable}h trainable against a "
                          f"{eval_row['wer']:.1%} WER / {eval_row['cer']:.1%} "
                          "CER baseline")
    return entry


def build_recommendation(merge_report: dict, rows_by_language: dict[str, list[dict]],
                         *, allow_incomplete: bool = False) -> dict:
    coverage = merge_report.get("coverage", {}).get("status")
    if coverage != "PASS_GAP_FREE_COVERAGE" and not allow_incomplete:
        raise RecommendationRefusal(
            f"merge report coverage is {coverage!r}; recommendations from an "
            "incomplete evaluation would misrank languages")
    per_language = merge_report["metrics"]["per_language"]
    eval_by_language = {}
    for key, entry in per_language.items():
        candidate, mode, language = key.split("|", 2)
        if candidate == BASELINE_CANDIDATE and mode == BASELINE_MODE:
            eval_by_language[language] = entry
    languages = sorted(set(eval_by_language) | set(rows_by_language))
    table = [
        recommend_language(language, eval_by_language.get(language),
                           licence_hours_from_manifest_rows(
                               rows_by_language.get(language, [])))
        for language in languages
    ]
    table.sort(key=lambda e: (-{"TRAIN": 2, "BLOCKED_PENDING_LEGAL": 1}.get(
        e["recommendation"], 0), -e["trainable_hours"]))
    summary = {}
    for entry in table:
        summary[entry["recommendation"]] = summary.get(entry["recommendation"], 0) + 1
    return {
        "record": "B5_TRAINING_RECOMMENDATION",
        "id": "B5-TRAINING-RECOMMENDATION-2026-001",
        "recorded_utc": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "coverage_status": coverage,
        "baseline_surface": f"{BASELINE_CANDIDATE}|{BASELINE_MODE}",
        "minimum_trainable_hours": MINIMUM_TRAINABLE_HOURS,
        "decision_contract": (
            "the OWNER approves or extends the campaign language list from "
            "this table; BLOCKED_PENDING_LEGAL languages cannot be added by "
            "approval alone"),
        "summary": summary,
        "languages": table,
    }


def markdown_table(recommendation: dict) -> str:
    lines = ["| language | recommendation | trainable h | base WER | base CER | note |",
             "|---|---|---|---|---|---|"]
    for e in recommendation["languages"]:
        base = e["eval_baseline"] or {}
        wer = f"{base['wer']:.1%}" if base.get("wer") is not None else "—"
        cer = f"{base['cer']:.1%}" if base.get("cer") is not None else "—"
        note = "attribution required" if e.get("attribution_required") else ""
        lines.append(f"| {e['language']} | {e['recommendation']} | "
                     f"{e['trainable_hours']} | {wer} | {cer} | {note} |")
    return "\n".join(lines)


def load_gb1_rows(cli, manifests: dict[str, dict]) -> dict[str, list[dict]]:
    """Fetch each gb1 manifest and bucket rows by language."""
    rows_by_language: dict[str, list[dict]] = {}
    for label, meta in manifests.items():
        language = label.split("/")[0]
        body = cli.get_object(Bucket="medzen-speech", Key=meta["key"])["Body"].read()
        if hashlib.sha256(body).hexdigest() != meta["sha256"]:
            raise RecommendationRefusal(
                f"{meta['key']} drifted from the gb1 COMPLETE record")
        rows = [json.loads(line) for line in body.decode().splitlines() if line.strip()]
        rows_by_language.setdefault(language, []).extend(rows)
    return rows_by_language


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--merge-report", type=Path, required=True)
    parser.add_argument("--gb1-complete", type=Path, required=True,
                        help="local copy of curated/_versions/gb1/COMPLETE.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    import boto3
    cli = boto3.Session(profile_name="medzen", region_name="eu-central-1").client("s3")
    merge_report = json.loads(args.merge_report.read_bytes())
    complete = json.loads(args.gb1_complete.read_bytes())
    rows_by_language = load_gb1_rows(cli, complete["manifests"])
    recommendation = build_recommendation(
        merge_report, rows_by_language, allow_incomplete=args.allow_incomplete)
    args.output.write_bytes(json.dumps(recommendation, indent=1, sort_keys=True,
                                       ensure_ascii=False).encode() + b"\n")
    if args.markdown:
        args.markdown.write_text(markdown_table(recommendation) + "\n")
    print(json.dumps(recommendation["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecommendationRefusal as exc:
        print(json.dumps({"status": "REFUSED", "detail": str(exc)}))
        raise SystemExit(2)
