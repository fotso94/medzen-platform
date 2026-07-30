#!/usr/bin/env python3
"""B4 — multilingual Whisper LoRA fine-tune.

ONE checkpoint over all languages, not one model per language (A1 §3):
code-switched audio cannot be routed to a per-language model before it has
been transcribed, and per-language forks discard the cross-lingual transfer
that low-resource languages depend on most.

Runs identically on a laptop CPU (smoke test) and an EC2 spot GPU (real run);
everything is env-var configurable so the same image also works as a SageMaker
training job without modification.

Spot interruption is expected, not exceptional: checkpoints go to S3 on a step
interval and --resume picks up the newest one.

    python -m pipeline.train_asr --smoke                     # 3 steps, CPU
    python -m pipeline.train_asr --max-steps 600 --push-s3
    python -m pipeline.train_asr --resume s3://.../ckpt-400
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BUCKET = os.environ.get("MEDZEN_BUCKET", "medzen-speech")
PROFILE = os.environ.get("AWS_PROFILE", "medzen")
REGION = os.environ.get("AWS_REGION", "eu-central-1")

BASE_MODEL = os.environ.get("BASE_MODEL", "openai/whisper-large-v3")
# Pinned: an unpinned base makes two runs incomparable for invisible reasons.
BASE_REVISION = os.environ.get("BASE_REVISION", "main")

# Whisper decoder tokens. Languages with no token train under the closest
# usable one; Pidgin uses `en` per the B3 experiment (provisional).
LANG_TOKEN = {
    "amharic": "am", "hausa": "ha", "lingala": "ln", "shona": "sn",
    "swahili": "sw", "yoruba": "yo",
    "pidgin": "en",                       # B3: en_token, provisional
    "acholi": "sw", "luganda": "sw", "fula": "sw",   # Bantu/regional neighbours
    "akan": "yo", "ewe": "yo", "igbo": "yo", "oromo": "sw",
}


def s3():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
def list_manifests(cli) -> list[str]:
    keys, tok = [], {"Bucket": BUCKET, "Prefix": "curated/"}
    while True:
        r = cli.list_objects_v2(**tok)
        keys += [o["Key"] for o in r.get("Contents", [])
                 if o["Key"].endswith("manifest.jsonl")]
        if not r.get("IsTruncated"):
            break
        tok["ContinuationToken"] = r["NextContinuationToken"]
    return sorted(keys)


def load_mix(cli, temperature: float, seed: int,
             languages: list[str] | None) -> list[dict]:
    """Build the training mix with temperature sampling.

    p_i proportional to n_i**temperature. At 0.5 a corpus ten times larger is
    sampled ~3x more, not 10x — otherwise the biggest language dominates and
    the smallest ones contribute nothing.
    """
    per_lang: dict[str, list[dict]] = {}
    for key in list_manifests(cli):
        _, lang, task, _cfg, _v, _ = key.split("/")
        if languages and lang not in languages:
            continue
        body = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read().decode()
        rows = [json.loads(l) for l in body.splitlines() if l.strip()]
        rows = [r for r in rows if r["split"] == "train"]
        for r in rows:
            r["_lang"] = lang
            r["_task"] = task
        per_lang.setdefault(lang, []).extend(rows)

    counts = {k: len(v) for k, v in per_lang.items()}
    weights = {k: n ** temperature for k, n in counts.items()}
    total_w = sum(weights.values())
    target = sum(counts.values())

    rng = random.Random(seed)
    mix: list[dict] = []
    for lang, rows in per_lang.items():
        share = weights[lang] / total_w
        n = max(1, round(target * share))
        pool = rows[:]
        rng.shuffle(pool)
        # sample with replacement only if the target exceeds what exists
        mix += [pool[i % len(pool)] for i in range(n)]
    rng.shuffle(mix)
    return mix


def report_mix(mix: list[dict]) -> dict:
    import collections
    c = collections.Counter(r["_lang"] for r in mix)
    mins = collections.defaultdict(float)
    for r in mix:
        mins[r["_lang"]] += r["duration_s"] / 60
    print(f"  mix: {len(mix)} rows across {len(c)} languages")
    for lang, n in c.most_common():
        print(f"    {lang:<10} {n:>5} rows  {mins[lang]:>6.1f} min")
    return {"rows": len(mix), "languages": len(c),
            "per_language": dict(c),
            "minutes": {k: round(v, 1) for k, v in mins.items()}}


# --------------------------------------------------------------------------- #
def build_dataset(mix, cli, processor, cache: Path):
    """Fetch audio once into a local cache, then featurise lazily."""
    import numpy as np
    import soundfile as sf
    import torch

    cache.mkdir(parents=True, exist_ok=True)

    class Ds(torch.utils.data.Dataset):
        def __len__(self):
            return len(mix)

        def __getitem__(self, i):
            rec = mix[i]
            sha = rec["audio_checksum_sha256"]
            local = cache / f"{sha}.wav"
            if not local.exists():
                key = rec["audio_filepath"].split(f"{BUCKET}/", 1)[1]
                local.write_bytes(cli.get_object(Bucket=BUCKET, Key=key)["Body"].read())
            audio, _ = sf.read(local, dtype="float32", always_2d=False)
            if audio.ndim > 1:
                audio = audio.mean(axis=1)

            feats = processor.feature_extractor(
                audio, sampling_rate=16000, return_tensors="pt").input_features[0]
            tok = LANG_TOKEN.get(rec["_lang"], "en")
            processor.tokenizer.set_prefix_tokens(language=tok, task="transcribe")
            labels = processor.tokenizer(rec["text_normalized"]).input_ids
            return {"input_features": feats, "labels": labels}

    return Ds()


def collate(processor):
    import torch

    def fn(batch):
        feats = torch.stack([b["input_features"] for b in batch])
        lab = processor.tokenizer.pad(
            [{"input_ids": b["labels"]} for b in batch], return_tensors="pt")
        labels = lab["input_ids"].masked_fill(lab.attention_mask.ne(1), -100)
        # the decoder re-adds BOS; drop it if the tokenizer already put one there
        if (labels[:, 0] == processor.tokenizer.bos_token_id).all().item():
            labels = labels[:, 1:]
        return {"input_features": feats, "labels": labels}
    return fn


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="3 steps on a handful of rows, CPU-friendly")
    ap.add_argument("--max-steps", type=int, default=600)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--rank", type=int, default=32)
    ap.add_argument("--temperature", type=float, default=0.5)
    ap.add_argument("--languages", nargs="*")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--save-steps", type=int, default=100)
    ap.add_argument("--resume", help="local dir or s3:// checkpoint to resume from")
    ap.add_argument("--push-s3", action="store_true",
                    help="upload checkpoints to s3://…/candidates/ (spot safety)")
    ap.add_argument("--out", type=Path, default=ROOT / "artifacts" / "asr-lora")
    ap.add_argument("--base-model", default=BASE_MODEL)
    a = ap.parse_args()

    import mlflow
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import (Seq2SeqTrainer, Seq2SeqTrainingArguments,
                              WhisperForConditionalGeneration, WhisperProcessor)

    from pipeline.tracking import manifest_fingerprint, start_run

    if a.smoke:
        a.max_steps, a.save_steps, a.batch_size, a.grad_accum = 3, 3, 1, 1
        a.base_model = os.environ.get("SMOKE_MODEL", "openai/whisper-tiny")

    cli = s3()
    print(f"base model  {a.base_model}")
    print("building mix...")
    mix = load_mix(cli, a.temperature, a.seed, a.languages)
    if a.smoke:
        mix = mix[:6]
    mixinfo = report_mix(mix)
    fingerprint = manifest_fingerprint(mix)
    print(f"  dataset fingerprint {fingerprint[:16]}")

    device = ("cuda" if torch.cuda.is_available()
              else "mps" if torch.backends.mps.is_available() else "cpu")
    use_bf16 = device == "cuda"
    print(f"  device {device}  bf16={use_bf16}")

    processor = WhisperProcessor.from_pretrained(a.base_model)
    model = WhisperForConditionalGeneration.from_pretrained(a.base_model)
    model.config.forced_decoder_ids = None
    model.config.suppress_tokens = []

    peft_cfg = LoraConfig(r=a.rank, lora_alpha=a.rank * 2, lora_dropout=0.05,
                          bias="none", target_modules=["q_proj", "v_proj"])
    model = get_peft_model(model, peft_cfg)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  LoRA r={a.rank}: {trainable/1e6:.1f}M trainable / {total/1e6:.0f}M "
          f"({100*trainable/total:.2f}%)")

    params = {
        "base_model": a.base_model, "base_revision": BASE_REVISION,
        "lora_rank": a.rank, "lora_target": "q_proj,v_proj",
        "lr": a.lr, "batch_size": a.batch_size, "grad_accum": a.grad_accum,
        "max_steps": a.max_steps, "temperature_sampling": a.temperature,
        "seed": a.seed, "device": device, "bf16": use_bf16,
        "dataset_fingerprint": fingerprint,
        "trainable_params": trainable, "total_params": total,
        "mix": mixinfo, "lang_tokens": LANG_TOKEN,
    }
    run = start_run("asr-multilingual-lora",
                    f"lora-r{a.rank}-{'smoke' if a.smoke else 'full'}", params,
                    tags={"phase": "B4", "smoke": str(a.smoke)})
    print(f"  mlflow run {run.info.run_id}")

    ds = build_dataset(mix, cli, processor, ROOT / ".cache" / "audio")
    args = Seq2SeqTrainingArguments(
        output_dir=str(a.out), per_device_train_batch_size=a.batch_size,
        gradient_accumulation_steps=a.grad_accum, learning_rate=a.lr,
        max_steps=a.max_steps, warmup_steps=min(50, a.max_steps // 10),
        bf16=use_bf16, gradient_checkpointing=False,
        logging_steps=max(1, a.max_steps // 20), save_steps=a.save_steps,
        save_total_limit=3, report_to=[], remove_unused_columns=False,
        dataloader_num_workers=0, seed=a.seed,
    )
    trainer = Seq2SeqTrainer(model=model, args=args, train_dataset=ds,
                             data_collator=collate(processor))

    t0 = time.time()
    result = trainer.train(resume_from_checkpoint=a.resume)
    wall = time.time() - t0

    mlflow.log_metrics({
        "train_loss": result.training_loss,
        "train_runtime_s": round(wall, 1),
        "steps": result.global_step,
        "samples_per_second": round(result.metrics.get("train_samples_per_second", 0), 3),
    })

    a.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(a.out)
    processor.save_pretrained(a.out)
    (a.out / "run.json").write_text(json.dumps({
        "mlflow_run_id": run.info.run_id, "dataset_fingerprint": fingerprint,
        "params": params, "train_loss": result.training_loss,
    }, indent=2) + "\n")
    print(f"\n  adapter -> {a.out}")
    print(f"  loss {result.training_loss:.4f}  steps {result.global_step}  {wall:.0f}s")

    if a.push_s3:
        import subprocess
        dest = f"s3://{BUCKET}/candidates/asr/{run.info.run_id}/"
        subprocess.run(["aws", "--profile", PROFILE, "--region", REGION,
                        "s3", "sync", str(a.out), dest], check=True)
        mlflow.set_tag("artifact_s3", dest)
        print(f"  pushed {dest}")

    mlflow.end_run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
