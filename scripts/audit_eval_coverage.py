#!/usr/bin/env python3
"""Read-only: what evaluation coverage exists for each trained language.

The question this answers is not "is there a manifest" but "is there a manifest
that could support a claim about this model". Those differ in ways that are easy
to miss:

  * A manifest under `eval/<lang>/tts/` is a SOURCE PATH, not a purpose. The
    Pidgin ASR result everyone has been quoting was scored on a /tts/ manifest.
    That is legitimate -- read speech is still speech -- but the record must say
    so rather than let the path imply an ASR set exists.
  * Zero exact audio/text overlap with training is necessary and not sufficient.
    If the same speakers and sessions appear on both sides, the score measures
    memorisation of voices as much as of language, and cannot select a
    checkpoint or support promotion.

So each language gets a verdict, not a tick: VALIDATION (speaker- and
session-disjoint), DIAGNOSTIC_ONLY (overlapping speakers or already used to
inform an investigation), or NONE.

Writes one local record. Uploads nothing, changes nothing.

    python scripts/audit_eval_coverage.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BUCKET = "medzen-speech"
ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "results/baseline/native/results.json"
# Already used to diagnose the 23868bab failure, so it can never again be an
# untouched holdout regardless of what its overlap numbers say.
INFORMED_INVESTIGATION = {("pidgin", "tts")}


def cli():
    import boto3
    return boto3.Session(profile_name="medzen", region_name="eu-central-1").client("s3")


def read_jsonl(c, key: str) -> tuple[list[dict], str]:
    raw = c.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return ([json.loads(l) for l in raw.decode().splitlines() if l.strip()],
            hashlib.sha256(raw).hexdigest())


def list_keys(c, prefix: str) -> list[str]:
    out, tok = [], None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": prefix}
        if tok:
            kw["ContinuationToken"] = tok
        page = c.list_objects_v2(**kw)
        out += [o["Key"] for o in page.get("Contents", [])]
        if not page.get("IsTruncated"):
            break
        tok = page.get("NextContinuationToken")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v2")
    ap.add_argument("--require-allowed-use", default="asr_train")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    c = cli()

    # ---- what training actually used -------------------------------------
    train: dict[str, list[dict]] = {}
    for key in sorted(list_keys(c, "curated/")):
        if not key.endswith(f"/{a.version}/manifest.jsonl"):
            continue
        _, lang, task, _cfg, _v, _ = key.split("/")
        rows, _ = read_jsonl(c, key)
        for r in rows:
            if r.get("split") == "train" and \
                    a.require_allowed_use in (r.get("allowed_use") or []):
                r["_task"] = task
                train.setdefault(lang, []).append(r)
    print(f"training pool: {len(train)} languages, "
          f"{sum(len(v) for v in train.values())} rows at {a.version}")

    # ---- baselines --------------------------------------------------------
    base = {}
    if BASELINE.exists():
        for r in json.load(open(BASELINE)).get("results", []):
            base[(r.get("language"), r.get("task"))] = r.get("wer")

    # ---- eval manifests ---------------------------------------------------
    eval_keys = [k for k in list_keys(c, "eval/") if k.endswith("manifest.jsonl")]

    report = {}
    for lang in sorted(train):
        trows = train[lang]
        t_sha = {r["audio_checksum_sha256"] for r in trows}
        t_txt = {r["text_normalized"] for r in trows}
        t_spk = {r.get("speaker_id") for r in trows if r.get("speaker_id")}
        t_ses = {r.get("session_id") for r in trows if r.get("session_id")}
        t_src = sorted({r["_task"] for r in trows})

        sets = []
        for key in [k for k in eval_keys if k.startswith(f"eval/{lang}/")]:
            _, _l, task, ver, _ = key.split("/")
            rows, sha = read_jsonl(c, key)
            e_sha = {r["audio_checksum_sha256"] for r in rows}
            e_txt = {r["text_normalized"] for r in rows}
            e_spk = {r.get("speaker_id") for r in rows if r.get("speaker_id")}
            e_ses = {r.get("session_id") for r in rows if r.get("session_id")}
            spk_ov, ses_ov = t_spk & e_spk, t_ses & e_ses
            informed = (lang, task) in INFORMED_INVESTIGATION

            if spk_ov or ses_ov or informed:
                verdict = "DIAGNOSTIC_ONLY"
            elif e_sha & t_sha or e_txt & t_txt:
                verdict = "CONTAMINATED"
            else:
                verdict = "VALIDATION_CANDIDATE"

            sets.append({
                "key": f"s3://{BUCKET}/{key}",
                "manifest_sha256": sha,
                "source_path_task": task,
                "usable_for": "ASR scoring (source path is not the purpose)",
                "version": ver,
                "rows": len(rows),
                "minutes": round(sum(r.get("duration_s", 0) for r in rows) / 60, 2),
                "speakers": len(e_spk), "sessions": len(e_ses),
                "exact_audio_overlap": len(e_sha & t_sha),
                "exact_text_overlap": len(e_txt & t_txt),
                "speaker_overlap": sorted(spk_ov)[:5],
                "speaker_overlap_count": len(spk_ov),
                "session_overlap_count": len(ses_ov),
                "informed_an_investigation": informed,
                "baseline_wer": base.get((lang, task)),
                "baseline_linked": (lang, task) in base,
                "verdict": verdict,
            })

        report[lang] = {
            "train_rows": len(trows),
            "train_minutes": round(
                sum(r.get("duration_s", 0) for r in trows) / 60, 2),
            "train_source_tasks": t_src,
            "train_speakers": len(t_spk), "train_sessions": len(t_ses),
            "eval_sets": sets,
            "has_any_eval": bool(sets),
            "has_validation_candidate": any(
                s["verdict"] == "VALIDATION_CANDIDATE" for s in sets),
            "has_untouched_holdout": False,      # none exists yet, by definition
        }

    rec = {
        "record": "EVAL-COVERAGE-AUDIT",
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "read_only": True,
        "manifest_version": a.version,
        "require_allowed_use": a.require_allowed_use,
        "languages_trained": len(report),
        "languages_with_any_eval": sum(1 for v in report.values() if v["has_any_eval"]),
        "languages_with_validation_candidate": sum(
            1 for v in report.values() if v["has_validation_candidate"]),
        "languages_with_untouched_holdout": 0,
        "definitions": {
            "VALIDATION_CANDIDATE": ("no exact overlap AND no shared speaker or "
                                     "session; could support checkpoint selection "
                                     "once frozen"),
            "DIAGNOSTIC_ONLY": ("shares speakers/sessions with training, or has "
                                "already informed an investigation; may study "
                                "behaviour, may not select or promote"),
            "CONTAMINATED": "exact audio or text appears in training",
            "source_path_vs_purpose": ("a manifest under eval/<lang>/tts/ is a "
                                       "source path. It can still be scored for "
                                       "ASR; the path never implies purpose"),
        },
        "languages": report,
        "content_policy": ("counts, checksum-derived overlaps and identifiers "
                           "only; no transcript or audio content"),
    }
    out = Path(a.out) if a.out else ROOT / "platform/evidence/eval-coverage-audit.json"
    out.write_text(json.dumps(rec, indent=2) + "\n")

    print(f"\n{'language':<10} {'train':>6} {'evalsets':>9} {'rows':>5} "
          f"{'spk_ov':>7} {'base':>6}  verdict")
    for lang, v in sorted(report.items()):
        if not v["eval_sets"]:
            print(f"{lang:<10} {v['train_rows']:>6} {0:>9} {'-':>5} {'-':>7} "
                  f"{'-':>6}  NO EVAL SET")
            continue
        for s in v["eval_sets"]:
            print(f"{lang:<10} {v['train_rows']:>6} "
                  f"{s['source_path_task']:>9} {s['rows']:>5} "
                  f"{s['speaker_overlap_count']:>7} "
                  f"{'yes' if s['baseline_linked'] else 'no':>6}  {s['verdict']}")
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
