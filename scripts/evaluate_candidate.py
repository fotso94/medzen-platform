#!/usr/bin/env python3
"""Score a LoRA candidate against the pinned base on a frozen eval set.

WHY BOTH ARMS RUN IN ONE PROCESS

The B4 candidate was compared against a baseline produced months earlier by a
different runtime (mlx_whisper) with its own decoding defaults. That comparison
happened to hold up, but it could not have proved anything on its own: a
difference between two arms is only attributable to the model if everything
else is identical. So every arm here is built from FRESHLY LOADED, checksum-
verified base weights and scored by the same function -- same audio, same
generate kwargs, same forced language and task, same normalizer, same jiwer
calls, same process. Reusing one model object and unwrapping it between arms
would have been cheaper, but it assumes the unwrap is perfect; reloading
removes the assumption, which matters most in a checkpoint sweep where weights
should be the only difference that exists.

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
        --lang-token en \
        --adapter s3://medzen-speech/candidates/asr/<run>/final \
        --expect-adapter-sha256 <64 hex>

    python scripts/evaluate_candidate.py --language pidgin --task tts \
        --lang-token en \
        --adapter s3://.../checkpoint-100 --adapter s3://.../checkpoint-200
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BUCKET = "medzen-speech"
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

# Pinned artifact identities. These are not defaults to be overridden -- they
# are what this reproduction is defined as running against. A run that cannot
# match them is not this reproduction and must not produce a record that looks
# like one.
BASE_MANIFEST_SHA256 = \
    "6a1987d462fc3330bb9eeeb488726bd7a16fd7d67f5aa08f0907eaa59d0913f1"
EVAL_MANIFEST_SHA256 = {
    ("pidgin", "tts", "v1"):
        "3f642616b691745ad80904d1436826ca3c27355ab81bcaa133febd2ad1178739",
}
HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Provenance the launcher must supply. Read from the environment rather than
# from git: the published bundle contains no .git, so `git rev-parse` inside it
# returns nothing and a record built on it would silently claim no commit.
REQUIRED_ENV = ("MEDZEN_IMAGE_DIGEST", "MEDZEN_CODE_GIT_SHA",
                "MEDZEN_CODE_TAR_SHA256")


def require_cuda(device: str) -> None:
    """Refuse anything but CUDA.

    The figures being reproduced were produced on CUDA. CPU and MPS differ in
    kernel selection and reduction order, so a discrepancy introduced there
    would be indistinguishable from a real disagreement about the candidate --
    which is the one question this run exists to answer.
    """
    if device != "cuda":
        raise SystemExit(
            f"REFUSING: this reproduction requires CUDA, found device={device!r}. "
            "A CPU or MPS result cannot be compared with the CUDA figures under "
            "investigation.")


def require_provenance() -> dict[str, str]:
    """Every identity this run claims, supplied by the launcher and validated.

    Absent or malformed values REFUSE. An evaluation whose own provenance is
    unknown cannot support a conclusion about anything else, and a record with
    empty provenance fields reads, later, exactly like one that was never
    checked.
    """
    out, bad = {}, []
    for name in REQUIRED_ENV:
        v = (os.environ.get(name) or "").strip()
        if not v:
            bad.append(f"{name} is not set")
            continue
        if name == "MEDZEN_IMAGE_DIGEST":
            if not v.startswith("sha256:") or not HEX64.match(v[7:]):
                bad.append(f"{name}={v!r} is not sha256:<64 lowercase hex>")
        elif name == "MEDZEN_CODE_GIT_SHA":
            if not re.match(r"^[0-9a-f]{40}$", v):
                bad.append(f"{name}={v!r} is not a 40-char commit sha")
        elif not HEX64.match(v):
            bad.append(f"{name}={v!r} is not 64 lowercase hex")
        out[name] = v
    if bad:
        raise SystemExit("REFUSING — provenance is incomplete:\n  "
                         + "\n  ".join(bad))
    return out


def s3():
    """A client that works on the instance AND on a laptop.

    profile_name=PROFILE was hardcoded, which is fine locally and wrong on EC2:
    there is no ~/.aws/credentials there, so boto3 raises ProfileNotFound and
    never reaches the instance role. Passing nothing lets the default chain do
    its job -- it honours AWS_PROFILE when the environment sets it, and falls
    through to the instance profile when it does not.
    """
    import boto3
    return boto3.Session(region_name=REGION).client("s3")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def fetch_prefix(cli, prefix: str, dest: Path) -> dict[str, str]:
    """Download every object under a prefix; return {relpath: sha256} of the
    bytes on disk. Used for adapters, which are small."""
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
            f = dest / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(body)
            got[rel] = sha256_bytes(body)
        if not page.get("IsTruncated"):
            break
        tok = page.get("NextContinuationToken")
    return got


def ensure_base(cli, dest: Path) -> tuple[dict, str]:
    """Verify the local base against the PINNED MANIFEST. Returns (manifest,
    raw manifest sha256).

    The manifest is the authority, so the check is against its declared
    checksums rather than against S3 object bytes. That matters twice over:
    it is the same provenance definition the training run recorded, and it
    means a 3 GB model is not re-downloaded on every evaluation merely to be
    compared with itself. Only genuinely missing files are fetched.
    """
    raw = cli.get_object(Bucket=BUCKET,
                         Key=f"{BASE_PREFIX}/MANIFEST.json")["Body"].read()
    man = json.loads(raw)
    man_sha = sha256_bytes(raw)

    if man_sha != BASE_MANIFEST_SHA256:
        raise SystemExit(
            f"REFUSING: base MANIFEST.json hashes {man_sha[:16]}, this "
            f"reproduction is pinned to {BASE_MANIFEST_SHA256[:16]}. The base "
            "model is not the one the failed run trained from.")
    if man.get("repo") != BASE_MODEL or man.get("revision") != BASE_REVISION:
        raise SystemExit(
            f"REFUSING: manifest declares {man.get('repo')}@"
            f"{str(man.get('revision'))[:12]}, this evaluator is pinned to "
            f"{BASE_MODEL}@{BASE_REVISION[:12]}")

    dest.mkdir(parents=True, exist_ok=True)
    fetched = 0
    for rel, meta in man["files"].items():
        f = dest / rel
        if not f.exists():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(cli.get_object(
                Bucket=BUCKET, Key=f"{BASE_PREFIX}/{rel}")["Body"].read())
            fetched += 1
        got = sha256_bytes(f.read_bytes())
        if got != meta["sha256"]:
            raise SystemExit(
                f"REFUSING: base file {rel} hashes {got[:16]}, manifest "
                f"declares {meta['sha256'][:16]}; these are not the pinned "
                "weights")
    # MANIFEST.json itself is kept beside the files it describes.
    (dest / "MANIFEST.json").write_bytes(raw)
    print(f"base model {BASE_MODEL}@{BASE_REVISION[:12]}: "
          f"{len(man['files'])} files verified against MANIFEST "
          f"{man_sha[:16]} ({fetched} fetched, "
          f"{len(man['files']) - fetched} already local)")
    return man, man_sha


def load_eval(cli, language: str, task: str, version: str) -> tuple[list[dict], str]:
    key = f"eval/{language}/{task}/{version}/manifest.jsonl"
    raw = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    sha = sha256_bytes(raw)
    want = EVAL_MANIFEST_SHA256.get((language, task, version))
    if want is None:
        raise SystemExit(
            f"REFUSING: no pinned hash for eval set {language}/{task}/{version}. "
            "An evaluation set that is not pinned is not frozen, and a score "
            "against it cannot be compared with anything later.")
    if sha != want:
        raise SystemExit(
            f"REFUSING: eval manifest {key} hashes {sha[:16]}, pinned as "
            f"{want[:16]}; the frozen set has changed.")
    rows = [json.loads(l) for l in raw.decode().splitlines() if l.strip()]
    return rows, sha


def load_audio(cli, rec: dict, cache: Path):
    """Fetch the clip and VERIFY its checksum. An eval set whose audio silently
    changed would move the score without moving the manifest hash."""
    import numpy as np
    import soundfile as sf

    sha = rec["audio_checksum_sha256"]
    # The caller passes work/"audio", which nothing has created. Without this
    # the very first clip fails with FileNotFoundError on any fresh cache --
    # i.e. on every run of a disposable instance, which is all of them.
    cache.mkdir(parents=True, exist_ok=True)
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


def expected_prompt(processor, lang_token: str, task: str,
                    timestamps: bool = False) -> list[int]:
    """The exact decoder prompt this configuration must produce.

    Built from the tokenizer's own prefix machinery -- the same call training
    uses -- so the evaluator and the trainer cannot disagree about what a
    prompt is. An earlier version counted "leading special tokens" instead,
    which is a guess: it would absorb any control token the model emitted
    first, hiding exactly the degenerate behaviour under investigation.
    """
    tk = processor.tokenizer
    tk.set_prefix_tokens(language=lang_token, task=task,
                         predict_timestamps=timestamps)
    return list(tk.prefix_tokens)


def split_prompt(ids: list[int], prompt: list[int]) -> int:
    """Assert the sequence starts with the expected prompt; return its length.

    Everything after it is generated output, INCLUDING any control token the
    model chose to emit. Treating a stray control token as prompt is how a
    runaway decode gets scored as a short one.
    """
    if ids[:len(prompt)] != prompt:
        raise SystemExit(
            f"REFUSING: sequence begins {ids[:len(prompt)]} but the pinned "
            f"configuration requires the prompt {prompt}. The decode did not "
            "run under the configuration this evaluation claims.")
    return len(prompt)


def score_arm(model, processor, rows, audios, language: str, device: str,
              lang_token: str, prompt: list[int]) -> dict:
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
        n_prompt = split_prompt(ids, prompt)
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
        "expected_prompt_ids": prompt,
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
    ap.add_argument("--expect-adapter-sha256", action="append", default=[],
                    help="required sha256 of adapter_model.safetensors, "
                         "positionally matched to --adapter. A mismatch refuses: "
                         "scoring a different artifact under the name of the one "
                         "being investigated is worse than not scoring at all.")
    ap.add_argument("--lang-token", required=True,
                    help="Whisper language token forced for BOTH arms (e.g. 'en'). "
                         "Required: auto-detection could decode the two arms as "
                         "different languages and blame the difference on the "
                         "adapter.")
    ap.add_argument("--device", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    prov = require_provenance()

    import peft
    import torch
    import transformers
    from transformers import WhisperForConditionalGeneration, WhisperProcessor
    libvers = {"torch": torch.__version__, "transformers": transformers.__version__,
               "peft": peft.__version__}
    print("provenance " + " ".join(f"{k.replace('MEDZEN_', '').lower()}="
                                   f"{v[:20]}" for k, v in prov.items()))
    print(f"libraries  {libvers}")

    device = a.device or ("cuda" if torch.cuda.is_available()
                          else "mps" if torch.backends.mps.is_available() else "cpu")
    require_cuda(device)
    cli = s3()
    # NOT under ROOT: the verified bundle is mounted read-only, so a cache
    # inside it cannot be created and, if it could, would let the run write
    # into the tree whose hashes it just checked.
    work = Path(os.environ.get("MEDZEN_EVAL_CACHE", ROOT / ".eval_cache"))
    work.mkdir(parents=True, exist_ok=True)
    print(f"cache      {work}")

    rows, man_sha = load_eval(cli, a.language, a.task, a.eval_version)
    print(f"eval set   {a.language}/{a.task}/{a.eval_version}: {len(rows)} rows, "
          f"manifest {man_sha[:16]}")

    # Verified on EVERY run against the manifest's declared checksums.
    base_dir = work / "base"
    base_manifest, base_manifest_sha = ensure_base(cli, base_dir)
    print(f"device     {device}")

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

    prompt = expected_prompt(processor, a.lang_token, TASK)
    print(f"prompt     {prompt} "
          f"({processor.tokenizer.convert_ids_to_tokens(prompt)})")

    print("scoring    base ...")
    model = fresh_base()
    arms = {"base": score_arm(model, processor, rows, audios, a.language, device,
                              a.lang_token, prompt)}
    b = arms["base"]
    print(f"  base WER {b['wer']} CER {b['cer']} "
          f"median gen {b['generated_tokens']['median']} "
          f"EOS {b['eos_rate']} cap-hits {b['rows_hitting_length_cap']}")

    del model                                   # base arm is finished with it

    # EXACTLY one expected hash per adapter. Optional hashes would mean the
    # check silently does nothing whenever someone forgets it, which is the
    # same as not having it.
    if len(a.expect_adapter_sha256) != len(a.adapter):
        raise SystemExit(
            f"REFUSING: {len(a.expect_adapter_sha256)} expected hash(es) for "
            f"{len(a.adapter)} adapter(s). Exactly one --expect-adapter-sha256 "
            "is required per --adapter; they are matched positionally and an "
            "off-by-one would check the wrong artifact.")
    malformed = [h for h in a.expect_adapter_sha256 if not HEX64.match(h)]
    if malformed:
        raise SystemExit(f"REFUSING: malformed expected hash(es) "
                         f"{[h[:16] for h in malformed]}; want 64 lowercase hex")

    adapters_meta = {}
    for i, uri in enumerate(a.adapter):
        # Keyed by the FULL uri. Every run's final/ and every run's
        # checkpoint-100 share a basename, so a name built from the last path
        # segment would collide across runs and one arm would overwrite another
        # in the results -- silently, and with a plausible-looking number.
        key = sha256_bytes(uri.encode())[:16]
        parts = [x for x in uri.rstrip("/").split("/") if x]
        stem = "-".join(parts[-2:]) if len(parts) >= 2 else parts[-1]
        name = f"{stem}@{key}"
        if uri.startswith("s3://"):
            pref = uri[len("s3://"):].split("/", 1)[1]
            d = work / "adapters" / key
            files = fetch_prefix(cli, pref, d)
        else:
            d = Path(uri)
            files = {f.name: sha256_bytes(f.read_bytes())
                     for f in d.iterdir() if f.is_file()}
        got_sha = files.get("adapter_model.safetensors")
        want = a.expect_adapter_sha256[i]
        if got_sha != want:
            raise SystemExit(
                f"REFUSING: {uri} adapter_model.safetensors hashes "
                f"{str(got_sha)[:16]}, expected {want[:16]}. This is not "
                "the artifact this evaluation was authorised to score.")
        if name in adapters_meta or name in arms:
            raise SystemExit(f"REFUSING: duplicate result key {name!r}; a second "
                             "arm would overwrite the first")
        adapters_meta[name] = {
            "uri": uri,
            "cache_key": key,
            "adapter_sha256": got_sha,
            "expected_sha256": (a.expect_adapter_sha256[i]
                                if a.expect_adapter_sha256 else None),
            "files": {k: v for k, v in sorted(files.items())},
        }
        from peft import PeftModel
        base = fresh_base()                     # identical unmodified weights
        merged = PeftModel.from_pretrained(base, str(d)).eval()
        print(f"scoring    {name} ...")
        arms[name] = score_arm(merged, processor, rows, audios, a.language,
                               device, a.lang_token, prompt)
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
        "base_manifest_repo": base_manifest["repo"],
        "base_manifest_revision": base_manifest["revision"],
        "base_files_verified": len(base_manifest["files"]),
        "device": device, "torch": torch.__version__,
        "purpose": "DIAGNOSTIC_ONLY",
        "purpose_note": (
            "The Pidgin set shares both speakers and sessions with training and "
            "has informed this investigation. It can reproduce the failure. It "
            "cannot select a checkpoint or support promotion."),
        "image_digest": prov["MEDZEN_IMAGE_DIGEST"],
        "code_git_commit": prov["MEDZEN_CODE_GIT_SHA"],
        "code_tar_sha256": prov["MEDZEN_CODE_TAR_SHA256"],
        "library_versions": libvers,
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
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, indent=2) + "\n")
    # relative_to() RAISES when the target is outside ROOT, and the launcher
    # writes to /out -- a separate mount by design. Raising here would fail the
    # run AFTER the results were safely written, turning a successful
    # evaluation into a non-zero exit.
    try:
        shown = out.relative_to(ROOT)
    except ValueError:
        shown = out.resolve()
    print(f"\nwrote {shown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
