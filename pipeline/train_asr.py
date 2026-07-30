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
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BUCKET = os.environ.get("MEDZEN_BUCKET", "medzen-speech")
PROFILE = os.environ.get("AWS_PROFILE", "medzen")
REGION = os.environ.get("AWS_REGION", "eu-central-1")

BASE_MODEL = os.environ.get("BASE_MODEL", "openai/whisper-large-v3")
# Pinned to a commit SHA and ACTUALLY PASSED to from_pretrained(). Recording a
# revision without enforcing it is worse than not pinning: the run would claim
# a SHA it did not use.
BASE_REVISION = os.environ.get("BASE_REVISION", "06f233fe06e710322aca913c1bc4249a0d71fce1")
SMOKE_REVISION = os.environ.get("SMOKE_REVISION", "main")

# Whisper decoder tokens. Languages with no token train under the closest
# usable one; Pidgin uses `en` per the B3 experiment (provisional).
LANG_TOKEN = {
    "amharic": "am", "hausa": "ha", "lingala": "ln", "shona": "sn",
    "swahili": "sw", "yoruba": "yo",
    "pidgin": "en",                       # B3: en_token, provisional
    "acholi": "sw", "luganda": "sw", "fula": "sw",   # Bantu/regional neighbours
    "akan": "yo", "ewe": "yo", "igbo": "yo", "oromo": "sw",
}


def boto_session():
    """Prefer the instance role on EC2; fall back to a named local profile.

    Hardcoding profile_name works on a laptop and fails on EC2, where the
    credentials come from the instance profile and no such profile exists.
    """
    import boto3
    if os.environ.get("AWS_PROFILE") or os.environ.get("MEDZEN_FORCE_PROFILE"):
        try:
            return boto3.Session(profile_name=os.environ.get("AWS_PROFILE", PROFILE),
                                 region_name=REGION)
        except Exception:
            pass
    return boto3.Session(region_name=REGION)     # instance role / env creds


def s3():
    return boto_session().client("s3")


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
# spot recovery
# --------------------------------------------------------------------------- #
def s3_uri_parts(uri: str) -> tuple[str, str]:
    rest = uri[len("s3://"):]
    b, _, k = rest.partition("/")
    return b, k.rstrip("/")


def sync_up(local: Path, uri: str, skip_checkpoints: bool = False) -> None:
    """Upload a checkpoint directory. Runs on the SAVE step, not at the end:
    a spot reclaim gives ~2 minutes' notice, so anything only written at the
    end of training is lost.

    skip_checkpoints excludes nested checkpoint-N/ directories. The final
    adapter is saved into the Trainer's own output_dir, so an unfiltered sync
    copies every retained checkpoint inside final/ -- a duplicate of data
    already uploaded under its own prefix, and an ambiguous directory for
    anything loading the adapter. At 600 steps with --save-steps 100 that is
    over a gigabyte of redundant upload.
    """
    cli = s3()
    bucket, prefix = s3_uri_parts(uri)
    for f in local.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(local)
        if skip_checkpoints and any(p.startswith("checkpoint-") for p in rel.parts[:-1]):
            continue
        cli.upload_file(str(f), bucket, f"{prefix}/{rel}")


class BaseSource(NamedTuple):
    """Where to load the base checkpoint from, and how to prove it is the pin."""
    path: str
    kwargs: dict
    uri: str | None = None            # s3 cache prefix, None when loaded from the Hub
    manifest_sha256: str | None = None
    revision: str = ""
    n_files: int = 0

    @staticmethod
    def runtime_provenance() -> dict:
        """What artifact produced this run, and from which commit.

        MEDZEN_GIT_SHA is baked into the image at build time, so it describes
        the image itself rather than what a launcher claimed. MEDZEN_IMAGE_DIGEST
        is passed in, because a digest cannot be known until after the push --
        but the launcher verifies the pulled digest against the pin before the
        container starts, so what arrives here has already been checked.

        Empty values mean this is the EC2 venv path, not a container.
        """
        return {"image_digest": os.environ.get("MEDZEN_IMAGE_DIGEST", ""),
                "image_git_sha": os.environ.get("MEDZEN_GIT_SHA", ""),
                "ran_in_container": bool(os.environ.get("MEDZEN_IMAGE_DIGEST"))}

    def provenance(self) -> dict:
        """What gets recorded in MLflow and run.json.

        Without the manifest digest, "loaded from the cache at revision X" is
        unfalsifiable after the fact: the cache could be re-seeded and every
        past run would still claim the same thing. The digest pins which bytes
        the manifest itself described.
        """
        return {"base_source": "s3_cache" if self.uri else "hf_hub",
                "base_cache_uri": self.uri or "",
                "base_manifest_sha256": self.manifest_sha256 or "",
                "base_revision_verified": self.revision,
                "base_cache_files": self.n_files}


def sha256_file(p: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def base_model_source(repo: str, rev: str, allow_hub: bool = False) -> BaseSource:
    """Resolve the base checkpoint. FAIL CLOSED: no automatic Hub fallback.

    A fallback is worse than an error here. If the cache is missing, stale or
    corrupt, falling back would silently train on weights nobody verified while
    the run reported the pinned SHA -- and it would do so on every Spot
    restart, unauthenticated, which is what the cache exists to stop.

    from_pretrained on a LOCAL DIRECTORY ignores revision= entirely, so the
    pin cannot be enforced by the library on the cache path. It is enforced
    here instead: the manifest's revision must equal the requested one, and
    every file must match its recorded size and sha256. Presence is not
    enough; a truncated or swapped file passes an existence check.

    allow_hub is for the laptop smoke test (SMOKE_REVISION is a branch, not a
    cached SHA) and for a deliberate MEDZEN_ALLOW_HUB=1 override. Both are
    explicit choices, never a silent consequence of a broken cache.
    """
    import hashlib
    import shutil

    if allow_hub:
        print(f"  base model  Hub {repo}@{rev} (explicitly allowed)")
        return BaseSource(repo, {"revision": rev}, revision=rev)

    name = repo.split("/")[-1]
    uri = f"s3://{BUCKET}/models/base/{name}/{rev}"
    root = Path(os.environ.get("MEDZEN_MODEL_DIR", "/tmp/medzen-base")) / name
    local = root / rev

    def refuse(why: str) -> None:
        raise SystemExit(
            f"REFUSING: base model cache unusable — {why}\n"
            f"  cache: {uri}\n"
            "  There is deliberately NO Hub fallback: it would train on\n"
            "  unverified weights while reporting the pinned revision.\n"
            "  Seed it:  python scripts/seed_base_model.py\n"
            "  Override: MEDZEN_ALLOW_HUB=1 (deliberate, not for training runs)")

    try:
        cli = s3()
        bucket, prefix = s3_uri_parts(uri)
        raw = cli.get_object(Bucket=bucket, Key=f"{prefix}/MANIFEST.json")["Body"].read()
        man = json.loads(raw)
    except Exception as e:
        refuse(f"no readable MANIFEST.json ({type(e).__name__}: {e})")

    man_sha = hashlib.sha256(raw).hexdigest()
    if man.get("revision") != rev:
        refuse(f"manifest revision {man.get('revision')!r} != pinned {rev!r}")

    def verify(d: Path) -> list[str]:
        bad: list[str] = []
        for rel, meta in sorted(man["files"].items()):
            f = d / rel
            if not f.exists():
                bad.append(f"{rel}: missing")
            elif f.stat().st_size != meta["bytes"]:
                bad.append(f"{rel}: {f.stat().st_size} bytes, expected {meta['bytes']}")
            elif (got := sha256_file(f)) != meta["sha256"]:
                bad.append(f"{rel}: sha256 {got[:12]}, expected {meta['sha256'][:12]}")
        return bad

    # ATOMIC: download into a scratch directory, verify it there, and only then
    # move it into place. A partial transfer must never be visible at the real
    # path, or a later run finds a plausible-looking cache and either trusts it
    # or refuses forever without self-healing. os.replace on the directory makes
    # the switch a single filesystem operation.
    if local.exists() and not verify(local):
        print(f"  base model  {local} (already local, verified)")
    else:
        if local.exists():
            print(f"  base model  {local} failed verification; refetching")
            shutil.rmtree(local, ignore_errors=True)
        for stale in root.glob(f"{rev}.partial-*"):       # abandoned by a killed run
            shutil.rmtree(stale, ignore_errors=True)
        tmp = root / f"{rev}.partial-{os.getpid()}"
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"  base model  fetching {uri} -> {tmp}")
        sync_down(uri, tmp)
        bad = verify(tmp)
        if bad:
            shutil.rmtree(tmp, ignore_errors=True)        # never leave it behind
            refuse(f"{len(bad)} file(s) failed verification: " + "; ".join(bad[:5]))
        tmp.replace(local)
        print(f"  base model  fetched and moved into {local}")

    print(f"  base model  revision {rev} verified, {len(man['files'])} files "
          f"sha256-checked, manifest {man_sha[:12]}, no network used")
    return BaseSource(str(local), {}, uri=uri, manifest_sha256=man_sha,
                      revision=rev, n_files=len(man["files"]))


def sync_down(uri: str, local: Path) -> Path:
    cli = s3()
    bucket, prefix = s3_uri_parts(uri)
    local.mkdir(parents=True, exist_ok=True)
    tok = {"Bucket": bucket, "Prefix": prefix + "/"}
    n = 0
    while True:
        r = cli.list_objects_v2(**tok)
        for o in r.get("Contents", []):
            rel = o["Key"][len(prefix) + 1:]
            if not rel:
                continue
            dest = local / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            cli.download_file(bucket, o["Key"], str(dest))
            n += 1
        if not r.get("IsTruncated"):
            break
        tok["ContinuationToken"] = r["NextContinuationToken"]
    if n == 0:
        raise SystemExit(f"no checkpoint objects under {uri}")
    print(f"  resumed {n} files from {uri}")
    return local


def latest_checkpoint(uri_prefix: str) -> str | None:
    """Newest checkpoint-N under a run prefix, for unattended spot restart."""
    cli = s3()
    bucket, prefix = s3_uri_parts(uri_prefix)
    r = cli.list_objects_v2(Bucket=bucket, Prefix=prefix + "/", Delimiter="/")
    cps = [p["Prefix"].rstrip("/").split("/")[-1] for p in r.get("CommonPrefixes", [])]
    cps = [c for c in cps if c.startswith("checkpoint-")]
    if not cps:
        return None
    best = max(cps, key=lambda c: int(c.split("-")[1]))
    return f"{uri_prefix.rstrip('/')}/{best}"


def make_upload_callback(run_prefix: str):
    from transformers import TrainerCallback

    class UploadCheckpoint(TrainerCallback):
        def on_save(self, args, state, control, **kw):
            local = Path(args.output_dir) / f"checkpoint-{state.global_step}"
            if local.exists():
                dest = f"{run_prefix.rstrip('/')}/checkpoint-{state.global_step}"
                try:
                    sync_up(local, dest)
                    from pipeline.tracking import push_tracking_db
                    # metadata must survive alongside the checkpoint
                    push_tracking_db(run_prefix.rstrip("/").split("/")[-1])
                    print(f"    checkpoint {state.global_step} -> {dest}", flush=True)
                except Exception as e:                       # noqa: BLE001
                    print(f"    WARNING checkpoint upload failed: {e}", flush=True)
            return control

    return UploadCheckpoint()


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
    ap.add_argument("--resume",
                    help="local dir, s3:// checkpoint, or 'auto' with --resume-run")
    ap.add_argument("--resume-run", help="mlflow run id whose checkpoints to resume")
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

    from pipeline.tracking import (manifest_fingerprint, push_tracking_db,
                                   start_run)

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

    # A real run on CPU/MPS would appear to work and take days, quietly burning
    # a spot instance or a laptop. Only --smoke may run off-GPU.
    if not a.smoke and device != "cuda" and not os.environ.get("MEDZEN_ALLOW_NO_CUDA"):
        raise SystemExit(
            f"REFUSING: non-smoke training requires CUDA, found device={device}.\n"
            "  whisper-large-v3 LoRA on CPU/MPS is orders of magnitude too slow "
            "and would silently waste the run.\n"
            "  Use --smoke for a laptop check, or set MEDZEN_ALLOW_NO_CUDA=1 to "
            "override deliberately.")

    rev = SMOKE_REVISION if a.smoke else BASE_REVISION
    print(f"  revision    {rev}")
    # Only the laptop smoke path or a deliberate override may touch the Hub.
    allow_hub = bool(a.smoke or os.environ.get("MEDZEN_ALLOW_HUB"))
    src = base_model_source(a.base_model, rev, allow_hub=allow_hub)
    processor = WhisperProcessor.from_pretrained(src.path, **src.kwargs)
    model = WhisperForConditionalGeneration.from_pretrained(src.path, **src.kwargs)
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
        "base_model": a.base_model, "base_revision": rev,
        "lora_rank": a.rank, "lora_target": "q_proj,v_proj",
        "lr": a.lr, "batch_size": a.batch_size, "grad_accum": a.grad_accum,
        "max_steps": a.max_steps, "temperature_sampling": a.temperature,
        "seed": a.seed, "device": device, "bf16": use_bf16,
        "dataset_fingerprint": fingerprint,
        "trainable_params": trainable, "total_params": total,
        "mix": mixinfo, "lang_tokens": LANG_TOKEN,
        # where the base weights actually came from, and proof of which bytes
        **src.provenance(),
        # and which artifact, from which commit, produced this run
        **BaseSource.runtime_provenance(),
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
    run_prefix = f"s3://{BUCKET}/candidates/asr/{run.info.run_id}"
    callbacks = [make_upload_callback(run_prefix)] if a.push_s3 else []
    trainer = Seq2SeqTrainer(model=model, args=args, train_dataset=ds,
                             data_collator=collate(processor),
                             callbacks=callbacks)

    resume = a.resume
    if resume == "auto":
        if not a.resume_run:
            raise SystemExit(
                "--resume auto needs --resume-run <mlflow_run_id>.\n"
                "  A FIRST launch passes neither: there is no prior run to resume from.\n"
                "  A RECOVERY passes both, naming the interrupted run whose checkpoints\n"
                "  are to be picked up.")
        found = latest_checkpoint(f"s3://{BUCKET}/candidates/asr/{a.resume_run}")
        if not found:
            print("  --resume auto: no checkpoint found, starting fresh")
        resume = found
    # MLflow lineage: a resumed run is a NEW run, so without this the chain from
    # the interrupted run to its continuation is lost and the loss curve looks
    # like it starts mid-training for no reason.
    if a.resume_run:
        mlflow.set_tags({"resumed_from": a.resume_run,
                         "resume_checkpoint": str(resume or "none")})
        print(f"  lineage     resumed_from {a.resume_run}")
    if resume and str(resume).startswith("s3://"):
        resume = str(sync_down(resume, a.out / "resumed"))

    t0 = time.time()
    result = trainer.train(resume_from_checkpoint=resume)
    wall = time.time() - t0

    # Peak GPU memory MUST be read in the training process. A fresh process
    # reports 0 because the allocator stats are per-process.
    gpu_peak_mb = (round(torch.cuda.max_memory_allocated() / 1e6, 1)
                   if torch.cuda.is_available() else 0.0)
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
    mlflow.set_tags({"gpu_name": str(gpu_name), "torch_version": torch.__version__,
                     "cuda_version": str(torch.version.cuda)})
    mlflow.log_metrics({
        "gpu_peak_mb": gpu_peak_mb,
        "train_loss": result.training_loss,
        "train_runtime_s": round(wall, 1),
        "steps": result.global_step,
        "samples_per_second": round(result.metrics.get("train_samples_per_second", 0), 3),
    })

    a.out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(a.out)
    processor.save_pretrained(a.out)
    import math as _math
    (a.out / "run.json").write_text(json.dumps({
        "mlflow_run_id": run.info.run_id, "dataset_fingerprint": fingerprint,
        "params": params, "train_loss": result.training_loss,
        "loss_is_finite": bool(_math.isfinite(result.training_loss)),
        "steps": result.global_step,
        "gpu_peak_mb": gpu_peak_mb, "gpu_name": gpu_name,
        "torch_version": torch.__version__, "cuda_version": torch.version.cuda,
        "device_used": device,
        **src.provenance(),
        **BaseSource.runtime_provenance(),
    }, indent=2) + "\n")
    print(f"\n  adapter -> {a.out}")
    print(f"  loss {result.training_loss:.4f}  steps {result.global_step}  {wall:.0f}s")

    if a.push_s3:
        # boto with the shared session, NOT `aws --profile`: on EC2 the profile
        # does not exist and the CLI would fail after a completed run.
        dest = f"s3://{BUCKET}/candidates/asr/{run.info.run_id}/final"
        # checkpoints already live under .../checkpoint-N/; final/ holds only
        # the adapter and processor a loader actually needs
        sync_up(a.out, dest, skip_checkpoints=True)
        mlflow.set_tag("artifact_s3", dest)
        print(f"  pushed {dest}")

    # capture the id BEFORE ending the run: afterwards there is no active run
    # and the DB would be filed under "unattributed"
    rid = run.info.run_id
    mlflow.end_run()
    db = push_tracking_db(rid)
    if db:
        print(f"  tracking db -> {db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
