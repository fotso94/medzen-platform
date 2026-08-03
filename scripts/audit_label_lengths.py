#!/usr/bin/env python3
"""Tokenise every training transcript and find rows the decoder cannot accept.

Whisper's decoder takes at most max_target_positions (448) label tokens. A
single row above that kills a run mid-epoch: the first full B4 run died at step
59 of 600. No short preflight can find it, because 3 steps touch ~48 of 4,619
rows, so the first epoch is the first time every transcript is tokenised.

The interesting question is not "which rows are too long" but WHY. Two causes
need different fixes:

  misalignment    the transcript covers more audio than the clip contains, so
                  tokens-per-second is far outside the language's own
                  distribution. Dropping is right; the row is wrong.
  dense encoding  the transcript matches the audio, but the script tokenises
                  inefficiently -- Ge'ez and other non-Latin scripts cost many
                  BPE tokens per word -- so a legitimate long clip exceeds the
                  limit. Dropping loses real data; the fix is a segmentation
                  policy at ingest.

Truncating labels is not an option in either case: it teaches the model to stop
mid-utterance and silently corrupts the training target.

NO TRANSCRIPT CONTENT IS PRINTED OR STORED. Rows are identified by audio
checksum. The corpus is consented speech and its content does not belong in
operational output or in an evidence record.

    python scripts/audit_label_lengths.py            # report
    python scripts/audit_label_lengths.py --upload   # + durable record in S3
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.label_length import label_lengths  # noqa: E402

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
BASE_MODEL = "openai/whisper-large-v3"
BASE_REVISION = "06f233fe06e710322aca913c1bc4249a0d71fce1"
TOKENIZER_CACHE_FILES = (
    "added_tokens.json",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "normalizer.json",
    "preprocessor_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "vocab.json",
)

# A row this far outside its own language's tokens-per-second distribution is
# very unlikely to be a correct transcription of that clip.
MISALIGN_Z = 4.0


def client():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def pinned_tokenizer(cli):
    """Load the tokenizer from the VERIFIED S3 cache, at the pinned revision.

    An earlier ad-hoc check called from_pretrained on the bare repo name, which
    silently takes whatever `main` points at -- a token count measured against a
    different tokenizer than training uses is not evidence about training.
    """
    import tempfile
    from transformers import WhisperTokenizerFast

    prefix = f"models/base/whisper-large-v3/{BASE_REVISION}"
    man = json.loads(cli.get_object(Bucket=BUCKET, Key=f"{prefix}/MANIFEST.json")
                     ["Body"].read())
    assert man["revision"] == BASE_REVISION, "cache holds a different revision"
    d = Path(tempfile.mkdtemp(prefix="tok-"))
    missing = sorted(set(TOKENIZER_CACHE_FILES) - set(man["files"]))
    if missing:
        raise SystemExit(
            f"REFUSING: base manifest lacks tokenizer files {missing}")
    # Do not fetch model.safetensors. A metadata audit needs ~4.5 MB of
    # tokenizer/config files, not 3.1 GB of weights; downloading the weights
    # made the audit itself a resource-risk boundary.
    for rel in TOKENIZER_CACHE_FILES:
        meta = man["files"][rel]
        body = cli.get_object(Bucket=BUCKET, Key=f"{prefix}/{rel}")["Body"].read()
        got = hashlib.sha256(body).hexdigest()
        if got != meta["sha256"]:
            raise SystemExit(f"REFUSING: {rel} sha256 {got[:12]} != {meta['sha256'][:12]}")
        (d / rel).write_bytes(body)
    print(f"tokenizer: {BASE_MODEL}@{BASE_REVISION[:12]} "
          f"from the S3 cache, {len(TOKENIZER_CACHE_FILES)} tokenizer/config "
          "files sha256-verified (model weights not downloaded)")
    return WhisperTokenizerFast.from_pretrained(str(d)), man


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="label token limit; defaults to the model's max_target_positions")
    ap.add_argument("--version", default="v2",
                    help="exactly one curated manifest version to audit")
    ap.add_argument("--split", default="train")
    ap.add_argument("--require-allowed-use", default="asr_train",
                    help="only audit rows declaring this in allowed_use")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--out", default="/tmp/label_length_audit.json")
    a = ap.parse_args()

    cli = client()
    tok, cache_man = pinned_tokenizer(cli)

    limit = a.limit
    if limit is None:
        from transformers import WhisperConfig
        import tempfile
        cfgd = Path(tempfile.mkdtemp())
        prefix = f"models/base/whisper-large-v3/{BASE_REVISION}"
        (cfgd / "config.json").write_bytes(
            cli.get_object(Bucket=BUCKET, Key=f"{prefix}/config.json")["Body"].read())
        limit = WhisperConfig.from_pretrained(str(cfgd)).max_target_positions
    print(f"label limit: {limit} tokens (model max_target_positions)\n")

    print(f"scope: version={a.version} split={a.split} "
          f"require_allowed_use={a.require_allowed_use or 'ANY'}")
    # Same contract as the loader: a version without a completion record, or
    # whose manifests no longer match it, is not auditable as that version.
    comp_key = f"curated/_versions/{a.version}/COMPLETE.json"
    try:
        completion = json.loads(cli.get_object(Bucket=BUCKET, Key=comp_key)["Body"].read())
    except Exception as e:
        raise SystemExit(f"REFUSING: no completion record at {comp_key} "
                         f"({type(e).__name__})")
    print(f"completion: {comp_key}  adopted={completion.get('adopted')}  "
          f"manifests={len(completion.get('manifests') or {})}\n")
    keys, t = [], {"Bucket": BUCKET, "Prefix": "curated/"}
    while True:
        r = cli.list_objects_v2(**t)
        keys += [o["Key"] for o in r.get("Contents", [])
                 if o["Key"].endswith(f"/{a.version}/manifest.jsonl")]
        if not r.get("IsTruncated"):
            break
        t["ContinuationToken"] = r["NextContinuationToken"]

    per_lang_tps: dict[str, list[float]] = collections.defaultdict(list)
    rows_seen = collections.Counter()
    over: list[dict] = []
    all_rows: list[tuple] = []

    skipped_not_permitted = 0
    manifest_hashes: dict[str, dict] = {}
    for key in sorted(keys):
        _, lang, task, cfg, ver, _ = key.split("/")
        body = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        _sha = hashlib.sha256(body).hexdigest()
        _label = f"{lang}/{task}/{cfg}"
        _declared = (completion.get("manifests") or {}).get(_label, {}).get("sha256")
        if _declared != _sha:
            raise SystemExit(f"REFUSING: {_label} sha256 {_sha[:16]} != "
                             f"{str(_declared)[:16]} in {comp_key}")
        manifest_hashes[_label] = {"key": key, "version": ver,
                                   "manifest_sha256": _sha,
                                   "matches_completion_record": True}
        for line in body.decode().splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if rec["split"] != a.split:
                continue
            if a.require_allowed_use and \
                    a.require_allowed_use not in (rec.get("allowed_use") or []):
                skipped_not_permitted += 1
                continue
            # SHARED function: applies LANG_TOKEN + set_prefix_tokens and the
            # collator's BOS rule, exactly as training does. An earlier version
            # called the tokenizer with no prefix set and under-reported by two
            # tokens -- 534 against the 536 that actually crashed the run.
            raw, eff = label_lengths(tok, rec["text_normalized"], lang)
            tps = eff / rec["duration_s"] if rec["duration_s"] else 0.0
            rows_seen[lang] += 1
            per_lang_tps[lang].append(tps)
            all_rows.append((lang, task, rec, raw, eff, tps))

    stats = {}
    for lang, vals in per_lang_tps.items():
        mean = statistics.fmean(vals)
        sd = statistics.pstdev(vals) or 1e-9
        stats[lang] = {"mean_tokens_per_s": round(mean, 3),
                       "stdev": round(sd, 3),
                       "max_tokens_per_s": round(max(vals), 3),
                       "rows": len(vals)}

    outliers = []
    for lang, task, rec, raw, eff, tps in all_rows:
        mean = stats[lang]["mean_tokens_per_s"]
        sd = stats[lang]["stdev"]
        z = (tps - mean) / sd
        entry = {
            # identified by checksum ONLY: no transcript text, and no speaker or
            # session identifier -- this record is durable and shareable, and
            # neither belongs in it.
            "audio_checksum_sha256": rec["audio_checksum_sha256"],
            "language": lang, "task": task,
            "label_tokens_raw": raw,
            "label_tokens_effective": eff,
            "duration_s": rec["duration_s"],
            "tokens_per_s": round(tps, 3),
            "language_mean_tokens_per_s": mean,
            "z_score": round(z, 2),
            "over_limit": eff > limit,
            # A z-score is a REVIEW TRIGGER, not a finding. It says this row is
            # unusual for its language; it does not say why, and alignment has
            # not been listened to or otherwise confirmed.
            "review_trigger": ("token rate far outside this language's distribution"
                               if z >= MISALIGN_Z else None),
            "classification": "UNREVIEWED",
        }
        if eff > limit:
            over.append(entry)
        elif z >= MISALIGN_Z:
            # Extreme rate outliers UNDER the limit crash nothing, so nothing has
            # ever flagged them -- which is exactly why they are worth listing.
            outliers.append(entry)

    print(f"train rows tokenised: {sum(rows_seen.values())}")
    print(f"rows over {limit} effective tokens: {len(over)}")
    print(f"extreme rate outliers UNDER the limit: {len(outliers)}\n")
    print("tokens/second by language (why some scripts hit the limit sooner):")
    for lang, st in sorted(stats.items(), key=lambda kv: -kv[1]["mean_tokens_per_s"]):
        print(f"  {lang:<10} mean {st['mean_tokens_per_s']:>6.2f}  "
              f"sd {st['stdev']:>6.2f}  max {st['max_tokens_per_s']:>7.2f}  n={st['rows']}")

    def show(title, items):
        if not items:
            return
        print(f"\n{title} (checksum only; no transcript, speaker or session):")
        for o in sorted(items, key=lambda x: -x["z_score"]):
            print(f"  raw {o['label_tokens_raw']:>5} / eff {o['label_tokens_effective']:>5} tok  "
                  f"{o['duration_s']:>6.2f}s  {o['tokens_per_s']:>6.2f} tok/s  "
                  f"z={o['z_score']:>+6.2f}  {o['language']:<9}/{o['task']:<3} "
                  f"sha256={o['audio_checksum_sha256'][:16]}")
            if o["review_trigger"]:
                print(f"         REVIEW TRIGGER: {o['review_trigger']} "
                      f"(classification: {o['classification']})")

    show(f"OVER the {limit}-token limit — these block a run", over)
    show("UNDER the limit but extreme rate — nothing has ever flagged these", outliers)

    verifier_src = Path(__file__).read_bytes()
    import subprocess
    def git(*args):
        r = subprocess.run(["git", "-C", str(Path(__file__).resolve().parent.parent), *args],
                           capture_output=True, text=True)
        return r.stdout.strip() if r.returncode == 0 else ""

    record = {
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "content_policy": ("rows identified by audio_checksum_sha256 only; no transcript "
                           "text, speaker_id or session_id recorded"),
        "method": {
            "function": "pipeline.label_length.label_lengths",
            "applies": ("LANG_TOKEN mapping, set_prefix_tokens(language, task='transcribe'), "
                        "and the collator's BOS rule -- identical to training"),
            "reported": "raw (as tokenised) and effective (what the model receives)",
            "reconciliation": ("an earlier audit omitted set_prefix_tokens and reported 534 "
                               "for the row that crashed training at 536"),
        },
        "tokenizer": {"repo": BASE_MODEL, "revision": BASE_REVISION, "source": "s3_cache",
                      "cache_manifest_sha256": hashlib.sha256(
                          json.dumps(cache_man, sort_keys=True).encode()).hexdigest(),
                      "files_sha256_verified": len(TOKENIZER_CACHE_FILES),
                      "model_weights_downloaded": False},
        "verifier": {"file": "scripts/audit_label_lengths.py",
                     "sha256": hashlib.sha256(verifier_src).hexdigest(),
                     "git_commit": git("rev-parse", "HEAD") or "unknown",
                     "git_dirty": bool(git("status", "--porcelain"))},
        "manifests": manifest_hashes,
        "scope": {"manifest_version": a.version, "split": a.split,
                  "require_allowed_use": a.require_allowed_use,
                  "rows_skipped_not_permitted": skipped_not_permitted},
        "label_limit": limit,
        "eligible_source_pool_rows": sum(rows_seen.values()),
        "pool_note": ("This is the ELIGIBLE SOURCE POOL at this version/split/"
                      "allowed_use. The training mix is temperature-sampled from it "
                      "and is a different number -- 4,619 for seed 0 at "
                      "temperature 0.5 -- because per-language targets are rounded."),
        "completion_record": {"key": comp_key,
                              "adopted": completion.get("adopted"),
                              "decision_id": completion.get("decision_id")},
        "rows_over_limit": len(over),
        "rate_outliers_under_limit": len(outliers),
        "z_threshold": MISALIGN_Z,
        "z_meaning": ("REVIEW TRIGGER only. A z-score says a row is unusual for its "
                      "language; it does not establish misalignment. No row here has "
                      "been listened to, so every classification is UNREVIEWED."),
        "per_language_tokens_per_s": stats,
        "over_limit_rows": over,
        "rate_outlier_rows": outliers,
    }
    Path(a.out).write_text(json.dumps(record, indent=2) + "\n")
    print(f"\nrecord: {a.out}")
    if a.upload:
        k = f"evidence/corpus/label-lengths-{record['recorded_utc'].replace(':', '')}.json"
        cli.put_object(Bucket=BUCKET, Key=k,
                       Body=(json.dumps(record, indent=2) + "\n").encode())
        print(f"stored s3://{BUCKET}/{k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
