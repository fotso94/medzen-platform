#!/usr/bin/env python3
"""B3 — zero-shot Whisper baseline on the FROZEN eval sets.

Produces the numbers every later decision rests on: whether to fine-tune, how
much a fine-tune must add, and what the promotion gates are measured against.
It therefore only ever reads `eval/`, never `curated/`.

Design points that matter for the numbers being trustworthy:

  * MODEL REVISION IS PINNED. An unpinned model makes two runs incomparable
    for reasons invisible in the output.
  * Results are written per (decode strategy), never to a shared file, and the
    runner REFUSES to overwrite an existing result unless --force is given.
  * Per-utterance detail (reference, hypothesis, per-row WER, latency) is
    persisted alongside the aggregate. An aggregate you cannot drill into is
    not diagnosable.
  * ASR and TTS corpora are reported SEPARATELY. TTS corpora are read speech
    from a handful of speakers; averaging them with spontaneous ASR speech
    produces a number that describes neither.
  * Hypothesis and reference are normalised identically before scoring, and
    tonal languages additionally get a tone-stripped view.

    python scripts/run_baseline.py --all --decode native
    python scripts/run_baseline.py --language pidgin --task tts --decode en_token
    python scripts/run_baseline.py --compare-decode --language pidgin --task tts
"""
from __future__ import annotations

import argparse
import io
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"

MODEL = "mlx-community/whisper-large-v3-mlx"
# Pinned 2026-07-30. Two runs of an unpinned model are not comparable.
MODEL_REVISION = "49e6aa286ad60c14352c404340ded53710378a11"

# Whisper's own language tokens, where one exists for our language.
WHISPER_TOKEN = {
    "amharic": "am", "hausa": "ha", "lingala": "ln", "shona": "sn",
    "swahili": "sw", "yoruba": "yo", "english": "en", "french": "fr",
}
TONAL = {"igbo", "yoruba", "akan", "ewe", "twi"}
DECODES = ("native", "auto", "en_token")


def s3():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


_MODEL_PATH: str | None = None


def pinned_model_path() -> str:
    """Resolve MODEL@MODEL_REVISION to a local snapshot directory.

    mlx_whisper.transcribe() takes no `revision` argument, so passing the bare
    repo name would silently load whatever `main` points at while the output
    still claimed the pinned SHA — worse than not pinning, because the record
    would lie. Resolving the snapshot ourselves makes the pin real.
    """
    global _MODEL_PATH
    if _MODEL_PATH is None:
        from huggingface_hub import snapshot_download
        _MODEL_PATH = snapshot_download(MODEL, revision=MODEL_REVISION)
        if MODEL_REVISION[:8] not in _MODEL_PATH:
            print(f"  WARNING: resolved path does not contain the revision: {_MODEL_PATH}")
        print(f"  model    resolved to {_MODEL_PATH}")
    return _MODEL_PATH


def load_eval(cli, language: str, task: str, version: str = "v1") -> list[dict]:
    key = f"eval/{language}/{task}/{version}/manifest.jsonl"
    body = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
    return [json.loads(l) for l in body.splitlines() if l.strip()]


def wer_cer(refs: list[str], hyps: list[str]) -> tuple[float, float]:
    import jiwer
    pairs = [(r, h) for r, h in zip(refs, hyps) if r.strip()]
    if not pairs:
        return float("nan"), float("nan")
    r, h = [p[0] for p in pairs], [p[1] for p in pairs]
    return jiwer.wer(r, h), jiwer.cer(r, h)


def bootstrap_ci(refs, hyps, n: int = 200, seed: int = 0) -> tuple[float, float]:
    """Eval sets here are 15-72 rows. A point WER without an interval invites
    over-reading noise as signal."""
    import random

    import jiwer
    rng = random.Random(seed)
    k = len(refs)
    if k < 5:
        return float("nan"), float("nan")
    vals = []
    for _ in range(n):
        idx = [rng.randrange(k) for _ in range(k)]
        r, h = [refs[i] for i in idx], [hyps[i] for i in idx]
        if any(x.strip() for x in r):
            vals.append(jiwer.wer(r, h))
    vals.sort()
    return vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals))]


def decode_language_arg(decode: str, language: str) -> str | None:
    if decode == "en_token":
        return "en"
    if decode == "native":
        return WHISPER_TOKEN.get(language)      # None -> auto if no token
    return None                                  # "auto"


def transcribe_all(rows, cli, decode, language) -> list[dict]:
    """Return one dict per utterance: hypothesis, latency, and identifiers."""
    import mlx_whisper
    import numpy as np
    import soundfile as sf

    from pipeline.normalizers import for_language
    norm = for_language(language)
    lang_arg = decode_language_arg(decode, language)

    out = []
    t0 = time.time()
    for i, rec in enumerate(rows, 1):
        key = rec["audio_filepath"].split(f"{BUCKET}/", 1)[1]
        blob = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        audio, _ = sf.read(io.BytesIO(blob), dtype="float32", always_2d=False)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        kw = {"path_or_hf_repo": pinned_model_path(), "fp16": True}
        if lang_arg:
            kw["language"] = lang_arg
        t = time.time()
        r = mlx_whisper.transcribe(np.asarray(audio, dtype=np.float32), **kw)
        lat = time.time() - t
        out.append({
            "audio_filepath": rec["audio_filepath"],
            "speaker_id": rec.get("speaker_id"),
            "duration_s": rec["duration_s"],
            "reference": norm(rec["text_verbatim"]),
            "reference_verbatim": rec["text_verbatim"],
            "hypothesis": norm(r.get("text", "")),
            "hypothesis_raw": r.get("text", ""),
            "detected_language": r.get("language"),
            "latency_s": round(lat, 3),
            "rtf": round(lat / max(rec["duration_s"], 1e-6), 3),
        })
        step = 5 if len(rows) <= 40 else 20
        if i % step == 0 or i == len(rows):
            print(f"      {i}/{len(rows)}  {time.time()-t0:.0f}s", flush=True)
    return out


def run_one(cli, language: str, task: str, decode: str, detail_dir: Path) -> dict | None:
    import jiwer

    from pipeline.normalizers import for_language, strip_tones
    try:
        rows = load_eval(cli, language, task)
    except Exception as e:
        print(f"  {language}/{task}: no eval set ({type(e).__name__})")
        return None

    norm = for_language(language)
    dur = sum(r["duration_s"] for r in rows) / 60
    lang_arg = decode_language_arg(decode, language)
    print(f"  {language}/{task}: {len(rows)} rows, {dur:.1f} min, decode={decode}"
          f" (token={lang_arg or 'auto-detect'})")

    utts = transcribe_all(rows, cli, decode, language)
    refs = [u["reference"] for u in utts]
    hyps = [u["hypothesis"] for u in utts]

    # per-utterance WER, so a bad aggregate can be drilled into
    for u in utts:
        try:
            u["wer"] = round(jiwer.wer(u["reference"], u["hypothesis"]), 4) \
                if u["reference"].strip() else None
        except Exception:
            u["wer"] = None

    w, c = wer_cer(refs, hyps)
    lo, hi = bootstrap_ci(refs, hyps)
    lat = [u["latency_s"] for u in utts]
    rtf = [u["rtf"] for u in utts]

    res = {
        "language": language, "task": task, "decode": decode,
        "decode_token": lang_arg, "rows": len(rows), "audio_min": round(dur, 1),
        "wer": round(w, 4), "cer": round(c, 4),
        "wer_ci95": [round(lo, 4), round(hi, 4)],
        "wer_median_utt": round(statistics.median([u["wer"] for u in utts
                                                   if u["wer"] is not None]), 4),
        "latency_s_median": round(statistics.median(lat), 3),
        "rtf_median": round(statistics.median(rtf), 3),
        "whisper_knows_language": language in WHISPER_TOKEN,
        "model": MODEL, "model_revision": MODEL_REVISION,
        "normalization_version": norm.version,
        "eval_set": f"s3://{BUCKET}/eval/{language}/{task}/v1/manifest.jsonl",
    }
    if language in TONAL:
        tw, tc = wer_cer([strip_tones(r) for r in refs], [strip_tones(h) for h in hyps])
        res["wer_tone_stripped"] = round(tw, 4)
        res["cer_tone_stripped"] = round(tc, 4)

    detail_dir.mkdir(parents=True, exist_ok=True)
    dpath = detail_dir / f"{language}_{task}.jsonl"
    dpath.write_text("\n".join(json.dumps(u) for u in utts) + "\n")
    res["detail"] = str(dpath.relative_to(ROOT))
    return res


def summarise(results: list[dict], title: str) -> None:
    if not results:
        return
    print(f"\n  {title}")
    print(f"  {'LANG':<10} {'ROWS':>5} {'WER':>7} {'CI95':>13} {'CER':>7} {'RTF':>6}  KNOWN")
    print("  " + "-" * 62)
    for r in sorted(results, key=lambda x: x["wer"]):
        ci = f"{r['wer_ci95'][0]:.2f}-{r['wer_ci95'][1]:.2f}"
        print(f"  {r['language']:<10} {r['rows']:>5} {r['wer']:>7.3f} {ci:>13} "
              f"{r['cer']:>7.3f} {r['rtf_median']:>6.2f}  "
              f"{'yes' if r['whisper_knows_language'] else 'NO'}")
    med = statistics.median([r["wer"] for r in results])
    print(f"  median WER: {med:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--language")
    ap.add_argument("--task", choices=["asr", "tts"])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--decode", default="native", choices=DECODES)
    ap.add_argument("--compare-decode", action="store_true",
                    help="run all three decode strategies on one eval set")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing result file for this decode")
    a = ap.parse_args()

    cli = s3()
    if a.compare_decode:
        if not (a.language and a.task):
            print("--compare-decode needs --language and --task"); return 2
        out = ROOT / "results" / "baseline" / f"decode_compare_{a.language}_{a.task}.json"
        if out.exists() and not a.force:
            print(f"REFUSING: {out.relative_to(ROOT)} already exists.")
            print("A decode comparison is the evidence behind a registry entry;")
            print("silently replacing it would orphan the recorded chosen_by_run.")
            print("Use --force to replace it.")
            return 1
        rows = []
        for d in DECODES:
            r = run_one(cli, a.language, a.task, d,
                        ROOT / "results" / "baseline" / d / "detail")
            if r:
                rows.append(r)
                print(f"    -> {d:<9} WER {r['wer']:.3f} "
                      f"[{r['wer_ci95'][0]:.3f}-{r['wer_ci95'][1]:.3f}] CER {r['cer']:.3f}\n",
                      flush=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "model": MODEL, "model_revision": MODEL_REVISION,
            "language": a.language, "task": a.task, "results": rows,
        }, indent=2) + "\n")
        if rows:
            best = min(rows, key=lambda x: x["wer"])
            print("\n  DECODE COMPARISON  " + f"{a.language}/{a.task}")
            for r in sorted(rows, key=lambda x: x["wer"]):
                mark = "  <-- winner" if r is best else ""
                print(f"    {r['decode']:<9} token={str(r['decode_token']):<5} "
                      f"WER {r['wer']:.3f}  CER {r['cer']:.3f}{mark}")
            # An unpaired ranking is not evidence. The arms share utterances,
            # so the honest comparison is a PAIRED bootstrap of the difference;
            # a 44-clip eval set will rarely separate close arms.
            print(f"\n  Best observed: {best['decode']}. This is a RANKING, not a result.")
            print("  Before it goes in the registry, run scripts/compare_decode_paired.py")
            print("  for the paired interval. If it crosses zero, record the winner as")
            print("  PROVISIONAL and confirm on a larger, more representative set.")
        print(f"  wrote {out}")
        return 0

    targets: list[tuple[str, str]] = []
    if a.all:
        r = cli.list_objects_v2(Bucket=BUCKET, Prefix="eval/")
        for o in sorted(r.get("Contents", []), key=lambda x: x["Key"]):
            if o["Key"].endswith("manifest.jsonl"):
                _, lang, task, _, _ = o["Key"].split("/")
                targets.append((lang, task))
    elif a.language and a.task:
        targets = [(a.language, a.task)]
    else:
        print("need --all or (--language and --task)"); return 2

    outdir = ROOT / "results" / "baseline" / a.decode
    outfile = outdir / "results.json"
    if outfile.exists() and not a.force:
        print(f"REFUSING: {outfile.relative_to(ROOT)} already exists.")
        print("Each decode strategy owns its own results file so runs cannot")
        print("silently overwrite each other. Use --force to replace it.")
        return 1

    print(f"zero-shot baseline\n  model    {MODEL}@{MODEL_REVISION[:12]}\n"
          f"  decode   {a.decode}\n  eval sets {len(targets)}\n")
    results = []
    for lang, task in targets:
        res = run_one(cli, lang, task, a.decode, outdir / "detail")
        if res:
            results.append(res)
            tone = (f"  tone-stripped WER {res['wer_tone_stripped']:.3f}"
                    if "wer_tone_stripped" in res else "")
            print(f"    -> WER {res['wer']:.3f} [{res['wer_ci95'][0]:.3f}-"
                  f"{res['wer_ci95'][1]:.3f}]  CER {res['cer']:.3f}{tone}\n", flush=True)

    outdir.mkdir(parents=True, exist_ok=True)
    outfile.write_text(json.dumps({
        "model": MODEL, "model_revision": MODEL_REVISION,
        "decode": a.decode, "results": results,
    }, indent=2) + "\n")

    print("\n" + "=" * 70)
    # ASR and TTS corpora are not comparable: TTS is read speech from a few
    # speakers, ASR is spontaneous from many. A combined median describes neither.
    summarise([r for r in results if r["task"] == "asr"],
              "ASR CORPORA (spontaneous, many speakers)")
    summarise([r for r in results if r["task"] == "tts"],
              "TTS CORPORA (read speech, few speakers - not an ASR benchmark)")
    print(f"\n  wrote {outfile.relative_to(ROOT)}")
    print(f"  per-utterance detail in {(outdir / 'detail').relative_to(ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
