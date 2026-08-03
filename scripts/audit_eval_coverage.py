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
DECODE_COMPARE = ROOT / "results/baseline/decode_compare_pidgin_tts.json"

# A baseline WER is only comparable to another number produced the same way.
# Pidgin alone has THREE circulating figures and they are not interchangeable:
#
#   0.5382  MLX native decode, whisper-large-v3-mlx@49e6aa28, pidgin-norm-v1
#   0.5257  MLX en_token decode, same model -- the SELECTED decode policy
#   0.5133  external torch run, openai/whisper-large-v3@06f233fe, language=en
#
# Quoting any of them as "the Pidgin baseline" without its runtime, revision,
# decode policy and normalization version invites a comparison that measures
# the harness rather than the model.
ARM_FILES = {"native": ROOT / "results/baseline/native/results.json",
             "en_token": DECODE_COMPARE,
             "auto": DECODE_COMPARE}
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

    # ---- baselines, each with the provenance that makes it comparable -----
    base: dict[tuple, list[dict]] = {}
    for arm, f in ARM_FILES.items():
        if not f.exists():
            continue
        doc = json.load(open(f))
        for r in doc.get("results", []):
            if r.get("decode") and r["decode"] != arm:
                continue
            base.setdefault((r.get("language"), r.get("task")), []).append({
                "decode_policy": r.get("decode", arm),
                "decode_token": r.get("decode_token"),
                "wer": r.get("wer"), "cer": r.get("cer"),
                "rows": r.get("rows"),
                "runtime": ("mlx_whisper" if "mlx" in str(r.get("model", ""))
                            else "transformers"),
                "model": r.get("model"),
                "model_revision": r.get("model_revision"),
                "normalization_version": r.get("normalization_version"),
                "artifact": str(f.relative_to(ROOT)),
            })

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
                "baselines": base.get((lang, task), []),
                "baseline_linked": (lang, task) in base,
                "baseline_comparability": (
                    "each entry carries runtime, model revision, decode policy "
                    "and normalization version. Figures from different arms are "
                    "NOT interchangeable and must not be differenced."),
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
        "circulating_pidgin_figures": {
            "warning": ("FIVE numbers now circulate for Pidgin on the same 44 "
                        "clips. They differ by runtime, model, decode policy "
                        "and normalization, not by model quality. Do not "
                        "difference them, and do not relabel any one of them "
                        "as 'the Pidgin baseline'."),
            "eval_manifest_sha256":
                "3f642616b691745ad80904d1436826ca3c27355ab81bcaa133febd2ad1178739",
            "figures": [
                {"wer": 0.5694, "cer": 0.3655, "decode_policy": "auto/native",
                 "runtime": "mlx_whisper",
                 "model": "mlx-community/whisper-large-v3-mlx",
                 "model_revision": "49e6aa286ad60c14352c404340ded53710378a11",
                 "normalization_version": "pidgin-norm-v1",
                 "artifact": "results/baseline/decode_compare_pidgin_tts.json"},
                {"wer": 0.5382, "cer": 0.3452, "decode_policy": "native",
                 "runtime": "mlx_whisper",
                 "model": "mlx-community/whisper-large-v3-mlx",
                 "model_revision": "49e6aa286ad60c14352c404340ded53710378a11",
                 "normalization_version": "pidgin-norm-v1",
                 "artifact": "results/baseline/native/results.json",
                 "note": ("what the coverage audit previously reported bare as "
                          "'the baseline'")},
                {"wer": 0.5257, "cer": 0.3759, "decode_policy": "en_token",
                 "runtime": "mlx_whisper",
                 "model": "mlx-community/whisper-large-v3-mlx",
                 "model_revision": "49e6aa286ad60c14352c404340ded53710378a11",
                 "normalization_version": "pidgin-norm-v1",
                 "artifact": "results/baseline/decode_compare_pidgin_tts.json",
                 "registry_artifact_sha256":
                     "551827a900f0b937fdaf4cdfc88ac2e081c8b0c861d93663794cc9b2cbb1fd1c",
                 "note": ("the SELECTED decode policy in "
                          "registry/languages/pidgin.yaml, itself marked "
                          "provisional on 44 read clips from 2 speakers")},
                {"wer": 0.5133, "cer": 0.3780, "decode_policy": "language=en forced",
                 "runtime": "transformers/torch",
                 "model": "openai/whisper-large-v3",
                 "model_revision": "06f233fe06e710322aca913c1bc4249a0d71fce1",
                 "normalization_version": "UNRECORDED by the external run",
                 "artifact": "EXTERNAL -- not produced in this repository",
                 "note": ("base arm of the failed-candidate evaluation, and the "
                          "nearest comparable figure because it shares this "
                          "runtime and model revision. It is a COMPARISON "
                          "TARGET, not a value the in-repo evaluator is "
                          "guaranteed to match: the external evaluator code, "
                          "library versions and normalization were not "
                          "recorded, and normalization alone moves WER by "
                          "several points.")},
                {"wer": 0.5117, "cer": 0.3780, "decode_policy": "language=en forced",
                 "runtime": "transformers/torch",
                 "model": "openai/whisper-large-v3",
                 "model_revision": "06f233fe06e710322aca913c1bc4249a0d71fce1",
                 "normalization_version": "pipeline.normalizers for_language('pidgin')",
                 "artifact": ("s3://medzen-speech/candidates/evaluations/"
                              "eval-1785483918-fd78a3c77e4b/evaluation.json"),
                 "artifact_sha256": ("8ecca6c132d923eae31b1ce914d617d076eaa47c"
                                     "8deb99fa10fc35886f4d1683"),
                 "evaluator_commit": "fd78a3c77e4b04b094c5c8579a37945acc427e83",
                 "generation_flags": {"max_new_tokens": 440, "num_beams": 1,
                                      "do_sample": False,
                                      "return_dict_in_generate": True,
                                      "force_unique_generate_call": True},
                 "eos_rate": 1.0, "cap_hit_rate": 0.0,
                 "note": ("REPRODUCED IN-REPOSITORY, content-addressed. The "
                          "base arm of the diagnostic reproduction. This is an "
                          "in-repo result with fully recorded provenance -- it "
                          "is NOT a promotion baseline, and it does not "
                          "supersede the B3 MLX figures, which measured a "
                          "different runtime and decode policy.")},
            ],
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
