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

Termination behaviour, because the failure that motivated this tool was a
termination failure rather than a quality failure. A WER number alone says
"bad" without saying "runaway".

Token accounting is kept explicit and separate, because getting it wrong is
easy: `generate()` returns a sequence that INCLUDES the decoder prompt, while
`max_new_tokens` excludes it. An earlier draft of this file called the whole
returned length `n_new` and compared it against `max_new_tokens`, which would
have overstated generated length by the prompt size and mislabelled rows as
cap hits. So prompt, generated and total are recorded separately, and a cap hit
is defined by what actually happened: EOS absent AND generated tokens reaching
the limit.

    python scripts/evaluate_candidate.py --language pidgin --task tts \
        --adapter s3://medzen-speech/candidates/asr/<run>/final
    python scripts/evaluate_candidate.py --language pidgin --task tts \
        --adapter s3://.../checkpoint-100 --adapter s3://.../checkpoint-200
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import subprocess
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
TASK = "transcribe"          # pinned; never inferred from the eval set
EOT_TOKEN = "<|endoftext|>"


def _git_commit() -> str | None:
    r = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def s3():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch_prefix(cli, prefix: str, dest: Path, verify_only: bool = False
                 ) -> dict[str, str]:
    """Download a prefix (or re-verify an existing copy) and return
    {relpath: sha256} of the bytes on disk.

    A cache that is trusted because the directory exists is not a cache, it is
    an assumption. Local files are re-hashed against the object in S3 on every
    run; a mismatch refuses rather than scoring against unknown weights."""
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
            p = dest / rel
            remote = sha256_bytes(
                cli.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read())
            if p.exists():
                local = sha256_bytes(p.read_bytes())
                if local != remote:
                    raise SystemExit(
                        f"REFUSING: cached {rel} hashes {local[:16]} but S3 holds "
                        f"{remote[:16]}; the local copy is not the pinned artifact")
            else:
                if verify_only:
                    raise SystemExit(f"REFUSING: {rel} missing from cache")
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(
                    cli.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read())
            got[rel] = remote
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
        local.write_bytes(cli.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    # EVERY run, not only on download. A cached clip that was truncated or
    # replaced would otherwise move the score with the manifest hash unchanged.
    got = sha256_bytes(local.read_bytes())
    if got != sha:
        raise SystemExit(f"REFUSING: audio checksum mismatch for {sha[:16]} "
                         f"(got {got[:16]}); the eval set is not what the "
                         "manifest describes")
    audio, sr = sf.read(local, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sr


def _prompt_len(processor, ids: list[int]) -> int:
    """How many leading tokens are decoder prompt rather than generation.

    Whisper's prompt is the run of special tokens at the head of the sequence:
    <|startoftranscript|><|lang|><|transcribe|><|notimestamps|>. Counting it by
    assumption ("it is always 4") would break silently the moment timestamps
    or a different task are used, so it is measured.
    """
    special = set(processor.tokenizer.all_special_ids)
    n = 0
    for t in ids:
        if t in special and n < 8:
            n += 1
        else:
            break
    return n


def score_arm(model, processor, rows, audios, language: str, device: str,
              lang_token: str) -> dict:
    """Decode every row and score. Identical for base and candidate."""
    import jiwer
    import torch

    from pipeline.normalizers import for_language
    from scripts.run_baseline import bootstrap_ci, wer_cer

    norm = for_language(language)
    refs, hyps, per = [], [], []
    n_cap = n_eos = 0

    for rec, (audio, sr) in zip(rows, audios):
        feats = processor.feature_extractor(
            audio, sampling_rate=sr, return_tensors="pt").input_features.to(device)
        if model.dtype != feats.dtype:
            feats = feats.to(model.dtype)
        # Language and task are ALWAYS forced, identically for both arms. Left
        # to auto-detect, the two arms could silently decode as different
        # languages and the difference would be attributed to the adapter.
        kw = dict(GEN, language=lang_token, task=TASK)
        t0 = time.perf_counter()
        with torch.no_grad():
            out = model.generate(feats, **kw)
        dt = time.perf_counter() - t0

        ids = out[0].tolist()
        # The returned sequence INCLUDES the decoder prompt; max_new_tokens does
        # not. Counting the whole thing as "new" overstates generated length by
        # the prompt size and mislabels rows as cap hits.
        n_total = len(ids)
        n_prompt = _prompt_len(processor, ids)
        n_gen = n_total - n_prompt
        eot = processor.tokenizer.convert_tokens_to_ids(EOT_TOKEN)
        eos_pos = ids.index(eot, n_prompt) if eot in ids[n_prompt:] else None
        eos_emitted = eos_pos is not None
        # A cap hit is what actually happened, not an arithmetic coincidence:
        # the model never stopped AND it ran out of budget.
        cap_hit = (not eos_emitted) and n_gen >= GEN["max_new_tokens"]
        if cap_hit:
            n_cap += 1
        if eos_emitted:
            n_eos += 1
        text = processor.tokenizer.decode(ids, skip_special_tokens=True)

        ref, hyp = norm(rec["text_normalized"]), norm(text)
        refs.append(ref)
        hyps.append(hyp)
        # checksum and NUMBERS only -- never the text
        per.append({
            "audio_checksum_sha256": rec["audio_checksum_sha256"],
            "prompt_tokens": n_prompt,
            "generated_tokens": n_gen,
            "total_tokens": n_total,
            "eos_emitted": eos_emitted,
            "eos_position": eos_pos,
            "stop_reason": ("eos" if eos_emitted
                            else "max_new_tokens" if cap_hit else "other"),
            "hit_length_cap": cap_hit,
            "ref_words": len(ref.split()), "hyp_words": len(hyp.split()),
            "latency_s": round(dt, 4),
            "wer": round(jiwer.wer(ref, hyp), 4) if ref.strip() else None,
        })

    w, c = wer_cer(refs, hyps)
    lo, hi = bootstrap_ci(refs, hyps)
    gen = [p["generated_tokens"] for p in per]
    tot = [p["total_tokens"] for p in per]
    lats = [p["latency_s"] for p in per]
    return {
        "rows": len(rows),
        "wer": round(w, 4), "cer": round(c, 4),
        "wer_ci95": [round(lo, 4), round(hi, 4)],
        "wer_median_utt": round(statistics.median(
            [p["wer"] for p in per if p["wer"] is not None]), 4),
        "generated_tokens": {
            "median": statistics.median(gen), "max": max(gen),
            "mean": round(statistics.mean(gen), 2), "min": min(gen)},
        "total_tokens": {"median": statistics.median(tot), "max": max(tot)},
        "prompt_tokens": sorted({p["prompt_tokens"] for p in per}),
        "eos_emitted_rows": n_eos,
        "eos_rate": round(n_eos / len(per), 4) if per else None,
        "rows_hitting_length_cap": n_cap,
        "cap_hit_rate": round(n_cap / len(per), 4) if per else None,
        "stop_reasons": {r: sum(1 for p in per if p["stop_reason"] == r)
                         for r in ("eos", "max_new_tokens", "other")},
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
    ap.add_argument("--lang-token", required=True,
                    help="Whisper language token forced for BOTH arms (e.g. 'en'). "
                         "Required: auto-detection could decode the two arms as "
                         "different languages and blame the difference on the "
                         "adapter.")
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

    # Verified on EVERY run, not skipped because a directory exists.
    base_dir = work / "base"
    base_files = fetch_prefix(cli, BASE_PREFIX, base_dir)
    base_manifest_sha = sha256_bytes(
        json.dumps(base_files, sort_keys=True).encode())
    print(f"base model {BASE_MODEL}@{BASE_REVISION[:12]} on {device}: "
          f"{len(base_files)} files verified, manifest {base_manifest_sha[:16]}")

    audios = [load_audio(cli, r, work / "audio") for r in rows]
    total_min = round(sum(len(x) / sr for x, sr in audios) / 60, 3)
    print(f"audio      {len(audios)} clips, {total_min} min, checksums verified")

    processor = WhisperProcessor.from_pretrained(str(base_dir))
    dtype = torch.float16 if device == "cuda" else torch.float32

    def fresh_base():
        """A NEW model from the verified files for every arm. Reusing one
        object and calling unload() assumes the unwrap is perfect; reloading
        removes the assumption, which matters most when comparing several
        checkpoints whose only meaningful difference is their weights."""
        m = WhisperForConditionalGeneration.from_pretrained(str(base_dir),
                                                            dtype=dtype)
        return m.to(device).eval()

    print("scoring    base ...")
    model = fresh_base()
    arms = {"base": score_arm(model, processor, rows, audios, a.language, device,
                              a.lang_token)}
    b = arms["base"]
    print(f"  base WER {b['wer']} CER {b['cer']} "
          f"median gen {b['generated_tokens']['median']} "
          f"EOS {b['eos_rate']} cap-hits {b['rows_hitting_length_cap']}")

    del model                                   # base arm is finished with it

    adapters_meta = {}
    for uri in a.adapter:
        # Keyed by the FULL uri, not the last path segment: every run's final/
        # and every run's checkpoint-100 share a basename, and a shared cache
        # directory would score one adapter under another's name.
        key = sha256_bytes(uri.encode())[:16]
        name = f"{uri.rstrip('/').split('/')[-3]}-{uri.rstrip('/').rsplit('/', 1)[-1]}" \
            if uri.count("/") >= 3 else uri.rstrip("/").rsplit("/", 1)[-1]
        if uri.startswith("s3://"):
            pref = uri[len("s3://"):].split("/", 1)[1]
            d = work / "adapters" / key
            files = fetch_prefix(cli, pref, d)
        else:
            d = Path(uri)
            files = {f.name: sha256_bytes(f.read_bytes())
                     for f in d.iterdir() if f.is_file()}
        adapters_meta[name] = {
            "uri": uri,
            "cache_key": key,
            "adapter_sha256": files.get("adapter_model.safetensors"),
            "files": {k: v for k, v in sorted(files.items())},
        }
        from peft import PeftModel
        base = fresh_base()                     # identical unmodified weights
        merged = PeftModel.from_pretrained(base, str(d)).eval()
        print(f"scoring    {name} ...")
        arms[name] = score_arm(merged, processor, rows, audios, a.language,
                               device, a.lang_token)
        r = arms[name]
        print(f"  {name} WER {r['wer']} CER {r['cer']} "
              f"median gen {r['generated_tokens']['median']} "
              f"EOS {r['eos_rate']} cap-hits {r['rows_hitting_length_cap']}")
        del merged, base

    rec = {
        "record": "CANDIDATE-EVALUATION",
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "language": a.language, "task": a.task, "eval_version": a.eval_version,
        "eval_manifest_sha256": man_sha,
        "eval_rows": len(rows), "eval_minutes": total_min,
        "base_model": BASE_MODEL, "base_revision": BASE_REVISION,
        "base_manifest_sha256": base_manifest_sha,
        "base_files_verified": len(base_files),
        "device": device, "torch": torch.__version__,
        "image_digest": os.environ.get("MEDZEN_IMAGE_DIGEST"),
        "code_git_commit": _git_commit(),
        "generation": {**GEN, "forced_language": a.lang_token, "task": TASK,
                       "note": ("language and task forced identically for every "
                                "arm; max_new_tokens EXCLUDES the decoder prompt")},
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
