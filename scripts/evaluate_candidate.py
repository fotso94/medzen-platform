#!/usr/bin/env python3
"""Score a LoRA candidate against the pinned base on a frozen eval set.

WHY BOTH ARMS RUN IN ONE PROCESS

The B4 candidate was compared against a baseline produced months earlier by a
different runtime (mlx_whisper) with its own decoding defaults. That comparison
happened to hold up, but it could not have proved anything on its own: a
difference between two arms is only attributable to the model if everything
else is identical. So this evaluator loads the base once, scores it, then wraps
the SAME model object in the adapter and scores again -- same audio, same
generate kwargs, same normalizer, same jiwer calls, same process. There is no
second code path for the candidate to be advantaged or disadvantaged by.

WHAT IT REFUSES TO DO

It never prints, logs or writes a transcript. Per-utterance rows are keyed by
audio checksum and carry numbers only: error rates, token counts, latency. A
transcript in an evaluation report is a content leak that no one notices,
because it looks like diligence.

WHAT IT RECORDS BEYOND THE HEADLINE

Output LENGTH statistics, because the failure that motivated this tool was a
termination failure, not a quality failure -- the model ran to the 448-token
cap without emitting <|endoftext|>. A WER number alone would have said "bad"
without saying "runaway", and the distinction is the whole diagnosis. Rows that
hit the cap are counted explicitly.

    python scripts/evaluate_candidate.py --language pidgin --task tts \
        --adapter s3://medzen-speech/candidates/asr/<run>/final
    python scripts/evaluate_candidate.py --language pidgin --task tts \
        --adapter s3://.../checkpoint-100 --adapter s3://.../checkpoint-200
"""
from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"
ROOT = Path(__file__).resolve().parent.parent
BASE_MODEL = "openai/whisper-large-v3"
BASE_REVISION = "06f233fe06e710322aca913c1bc4249a0d71fce1"
BASE_PREFIX = f"models/base/whisper-large-v3/{BASE_REVISION}"

# Pinned in ONE place and applied to both arms. The failure under investigation
# involved generation settings written to model.config while generation reads
# generation_config; naming them here removes the ambiguity from the comparison.
GEN = {
    "max_new_tokens": 440,
    "num_beams": 1,
    "do_sample": False,
}


def s3():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch_prefix(cli, prefix: str, dest: Path) -> dict[str, str]:
    """Download a prefix and return {relpath: sha256} of what arrived."""
    dest.mkdir(parents=True, exist_ok=True)
    got: dict[str, str] = {}
    tok = None
    while True:
        kw = {"Bucket": BUCKET, "Prefix": prefix}
        if tok:
            kw["ContinuationToken"] = tok
        page = cli.list_objects_v2(**kw)
        for o in page.get("Contents", []):
            rel = o["Key"][len(prefix):].lstrip("/")
            if not rel or rel.endswith("/"):
                continue
            body = cli.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read()
            p = dest / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(body)
            got[rel] = sha256_bytes(body)
        if not page.get("IsTruncated"):
            break
        tok = page.get("NextContinuationToken")
    return got


def load_eval(cli, language: str, task: str, version: str) -> tuple[list[dict], str]:
    key = f"eval/{language}/{task}/{version}/manifest.jsonl"
    raw = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    rows = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    return rows, sha256_bytes(raw)


def load_audio(cli, rec: dict, cache: Path):
    """Fetch the clip and VERIFY its checksum. An eval set whose audio silently
    changed would move the score without moving the manifest hash."""
    import numpy as np
    import soundfile as sf

    sha = rec["audio_checksum_sha256"]
    local = cache / f"{sha}.wav"
    if not local.exists():
        key = rec["audio_filepath"].split(f"{BUCKET}/", 1)[1]
        body = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        got = sha256_bytes(body)
        if got != sha:
            raise SystemExit(f"REFUSING: audio checksum mismatch for {sha[:16]} "
                             f"(got {got[:16]}); the eval set is not what the "
                             "manifest describes")
        local.write_bytes(body)
    audio, sr = sf.read(local, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sr


def score_arm(model, processor, rows, audios, language: str, device: str,
              lang_token: str | None) -> dict:
    """Decode every row and score. Identical for base and candidate."""
    import jiwer
    import torch

    from pipeline.normalizers import for_language
    from scripts.run_baseline import bootstrap_ci, wer_cer

    norm = for_language(language)
    refs, hyps, per = [], [], []
    n_cap = 0

    for rec, (audio, sr) in zip(rows, audios):
        feats = processor.feature_extractor(
            audio, sampling_rate=sr, return_tensors="pt").input_features.to(device)
        if model.dtype != feats.dtype:
            feats = feats.to(model.dtype)
        kw = dict(GEN)
        if lang_token:
            kw["language"] = lang_token
            kw["task"] = "transcribe"
        t0 = time.perf_counter()
        with torch.no_grad():
            out = model.generate(feats, **kw)
        dt = time.perf_counter() - t0

        ids = out[0].tolist()
        n_new = len(ids)
        if n_new >= GEN["max_new_tokens"]:
            n_cap += 1
        text = processor.tokenizer.decode(ids, skip_special_tokens=True)

        ref, hyp = norm(rec["text_normalized"]), norm(text)
        refs.append(ref)
        hyps.append(hyp)
        # checksum and NUMBERS only -- never the text
        per.append({
            "audio_checksum_sha256": rec["audio_checksum_sha256"],
            "output_tokens": n_new,
            "hit_length_cap": bool(n_new >= GEN["max_new_tokens"]),
            "ref_words": len(ref.split()), "hyp_words": len(hyp.split()),
            "latency_s": round(dt, 4),
            "wer": round(jiwer.wer(ref, hyp), 4) if ref.strip() else None,
        })

    w, c = wer_cer(refs, hyps)
    lo, hi = bootstrap_ci(refs, hyps)
    lens = [p["output_tokens"] for p in per]
    lats = [p["latency_s"] for p in per]
    return {
        "rows": len(rows),
        "wer": round(w, 4), "cer": round(c, 4),
        "wer_ci95": [round(lo, 4), round(hi, 4)],
        "wer_median_utt": round(statistics.median(
            [p["wer"] for p in per if p["wer"] is not None]), 4),
        "output_tokens": {
            "median": statistics.median(lens), "max": max(lens),
            "mean": round(statistics.mean(lens), 2), "min": min(lens)},
        "rows_hitting_length_cap": n_cap,
        "latency_s": {"median": round(statistics.median(lats), 4),
                      "max": round(max(lats), 4)},
        "normalization_version": norm.version,
        "per_utterance": per,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--language", required=True)
    ap.add_argument("--task", required=True)
    ap.add_argument("--eval-version", default="v1")
    ap.add_argument("--adapter", action="append", default=[],
                    help="s3:// prefix or local dir of a LoRA adapter; repeatable")
    ap.add_argument("--lang-token", default=None,
                    help="Whisper language token to force for BOTH arms")
    ap.add_argument("--device", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    import torch
    from transformers import WhisperForConditionalGeneration, WhisperProcessor

    device = a.device or ("cuda" if torch.cuda.is_available()
                          else "mps" if torch.backends.mps.is_available() else "cpu")
    cli = s3()
    work = ROOT / ".eval_cache"
    work.mkdir(exist_ok=True)

    rows, man_sha = load_eval(cli, a.language, a.task, a.eval_version)
    print(f"eval set   {a.language}/{a.task}/{a.eval_version}: {len(rows)} rows, "
          f"manifest {man_sha[:16]}")

    base_dir = work / "base"
    base_files = fetch_prefix(cli, BASE_PREFIX, base_dir) if not base_dir.exists() else {}
    print(f"base model {BASE_MODEL}@{BASE_REVISION[:12]} on {device}")

    audios = [load_audio(cli, r, work / "audio") for r in rows]
    total_min = round(sum(len(x) / sr for x, sr in audios) / 60, 3)
    print(f"audio      {len(audios)} clips, {total_min} min, checksums verified")

    processor = WhisperProcessor.from_pretrained(str(base_dir))
    model = WhisperForConditionalGeneration.from_pretrained(
        str(base_dir), dtype=torch.float16 if device == "cuda" else torch.float32)
    model.to(device).eval()

    print("scoring    base ...")
    arms = {"base": score_arm(model, processor, rows, audios, a.language, device,
                              a.lang_token)}
    print(f"  base WER {arms['base']['wer']} CER {arms['base']['cer']} "
          f"median tokens {arms['base']['output_tokens']['median']}")

    adapters_meta = {}
    for uri in a.adapter:
        name = uri.rstrip("/").rsplit("/", 1)[-1]
        if uri.startswith("s3://"):
            pref = uri[len("s3://"):].split("/", 1)[1]
            d = work / "adapters" / name
            files = fetch_prefix(cli, pref, d)
        else:
            d = Path(uri)
            files = {p.name: sha256_bytes(p.read_bytes())
                     for p in d.iterdir() if p.is_file()}
        adapters_meta[name] = {
            "uri": uri,
            "adapter_sha256": files.get("adapter_model.safetensors"),
            "files": sorted(files),
        }
        from peft import PeftModel
        # Wrap the SAME base object, then unload, so no arm sees a differently
        # constructed model.
        merged = PeftModel.from_pretrained(model, str(d))
        merged.eval()
        print(f"scoring    {name} ...")
        arms[name] = score_arm(merged, processor, rows, audios, a.language,
                               device, a.lang_token)
        print(f"  {name} WER {arms[name]['wer']} CER {arms[name]['cer']} "
              f"median tokens {arms[name]['output_tokens']['median']} "
              f"cap-hits {arms[name]['rows_hitting_length_cap']}")
        model = merged.unload()

    rec = {
        "record": "CANDIDATE-EVALUATION",
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "language": a.language, "task": a.task, "eval_version": a.eval_version,
        "eval_manifest_sha256": man_sha,
        "eval_rows": len(rows), "eval_minutes": total_min,
        "base_model": BASE_MODEL, "base_revision": BASE_REVISION,
        "device": device, "torch": torch.__version__,
        "generation": {**GEN, "forced_language": a.lang_token},
        "identical_decoding": ("both arms scored in one process with the same "
                               "generate kwargs, normalizer and jiwer calls"),
        "adapters": adapters_meta,
        "arms": arms,
        "content_policy": ("no transcript is printed, logged or stored; "
                           "per-utterance rows carry checksums and numbers only"),
    }
    out = Path(a.out) if a.out else (
        ROOT / f"platform/evidence/eval-{a.language}-{a.task}-"
               f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.json")
    out.write_text(json.dumps(rec, indent=2) + "\n")
    print(f"\nwrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
