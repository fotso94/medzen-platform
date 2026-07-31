"""Container-side execution for one immutable B4 stage descriptor.

The EC2 operator is the only launcher.  This module cannot reserve budget or
launch another instance.  It trains/evaluates exactly one descriptor, writes a
container result, and exits; the operator later supplies AWS-observed lifecycle
evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pipeline import orchestrate, smoke, stage_descriptor
from pipeline.generation import (EOT_TOKEN, account, expected_prompt,
                                 extract_sequence, generation_kwargs)
from pipeline.label_length import decoder_start_id
from pipeline.languages import LANG_TOKEN
from pipeline.validation_runner import (ValidationRuntime, adapter_sha256,
                                         sha256_file)


ROOT = Path(__file__).resolve().parent.parent
BUCKET = os.environ.get("MEDZEN_BUCKET", "medzen-speech")
REGION = os.environ.get("AWS_REGION", "eu-central-1")
POLICY = ROOT / "platform/decisions/DQ-2026-003-policy-deferral-corrected.json"


def _s3():
    import boto3
    return boto3.Session(region_name=REGION).client("s3")


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def require_runtime_provenance(descriptor: dict) -> None:
    expected = {
        "MEDZEN_CODE_GIT_SHA": descriptor["git_sha"],
        "MEDZEN_CODE_TAR_SHA256": descriptor["bundle_tar_sha256"],
        "MEDZEN_IMAGE_DIGEST": descriptor["image_digest"],
    }
    bad = [
        f"{name}={os.environ.get(name)!r}, expected {want!r}"
        for name, want in expected.items()
        if os.environ.get(name) != want
    ]
    baked = os.environ.get("MEDZEN_GIT_SHA")
    if baked != descriptor["git_sha"]:
        bad.append(
            f"MEDZEN_GIT_SHA={baked!r}, expected baked "
            f"{descriptor['git_sha']!r}")
    if bad:
        raise SystemExit(
            "REFUSING: container provenance differs from the descriptor:\n  "
            + "\n  ".join(bad))


def require_environment() -> None:
    """Run the image's shared dependency gate and require a real CUDA device."""
    if os.environ.get("MEDZEN_TEST_SKIP_ENV_VERIFY") == "1":
        return
    env = {**os.environ, "MODE": "verify"}
    check = subprocess.run(
        ["bash", str(ROOT / "pipeline/bootstrap_trainer.sh"), str(ROOT)],
        env=env, check=False)
    if check.returncode:
        raise SystemExit(
            f"REFUSING: trainer environment gate exited {check.returncode}")
    import torch
    if not torch.cuda.is_available():
        raise SystemExit("REFUSING: B4 stage requires CUDA")
    print(f"CUDA device: {torch.cuda.get_device_name(0)}", flush=True)


def _require_empty(cli, prefix: str) -> None:
    page = cli.list_objects_v2(Bucket=BUCKET, Prefix=prefix, MaxKeys=1)
    if page.get("KeyCount", 0):
        raise SystemExit(
            f"REFUSING: s3://{BUCKET}/{prefix} is occupied; stage artifacts "
            "are write-once")


def put_immutable(cli, key: str, body: bytes,
                  content_type: str = "application/json") -> str:
    cli.put_object(
        Bucket=BUCKET, Key=key, Body=body, ContentType=content_type,
        IfNoneMatch="*", ServerSideEncryption="aws:kms")
    back = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    if back != body:
        raise SystemExit(
            f"REFUSING: artifact readback differs at s3://{BUCKET}/{key}")
    return _sha(back)


def upload_tree(cli, local: Path, prefix: str,
                skip_nested_checkpoints: bool = False) -> dict:
    """Upload one previously empty artifact tree and verify every object."""
    prefix = prefix.rstrip("/") + "/"
    _require_empty(cli, prefix)
    files: dict[str, dict] = {}
    for path in sorted(Path(local).rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(local).as_posix()
        if skip_nested_checkpoints and any(
                part.startswith("checkpoint-")
                for part in Path(rel).parts[:-1]):
            continue
        key = prefix + rel
        digest = sha256_file(path)
        cli.upload_file(
            str(path), BUCKET, key,
            ExtraArgs={"ServerSideEncryption": "aws:kms"})
        head = cli.head_object(Bucket=BUCKET, Key=key)
        if head["ContentLength"] != path.stat().st_size:
            raise SystemExit(
                f"REFUSING: uploaded {key} has {head['ContentLength']} bytes, "
                f"expected {path.stat().st_size}")
        files[rel] = {"sha256": digest, "bytes": path.stat().st_size}
    if not files:
        raise SystemExit(f"REFUSING: no artifact files found under {local}")
    tree_sha = _sha(json.dumps(
        files, sort_keys=True, separators=(",", ":")).encode())
    manifest = {"prefix": f"s3://{BUCKET}/{prefix}", "files": files,
                "tree_sha256": tree_sha}
    put_immutable(cli, prefix + "ARTIFACT.json", _json_bytes(manifest))
    return manifest


def load_base_result(cli, descriptor: dict) -> dict:
    key = descriptor["base_artifact_key"]
    raw = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    got = _sha(raw)
    if got != descriptor["base_artifact_sha256"]:
        raise SystemExit(
            f"REFUSING: base artifact {key} hashes {got[:16]}, descriptor "
            f"authorises {descriptor['base_artifact_sha256'][:16]}")
    doc = json.loads(raw)
    summary = doc.get("summary") or {}
    orchestrate.validate_metric_map(summary.get("wer"), "base WER")
    return doc


def _training_command(descriptor: dict, out: Path, *, lr: float,
                      max_steps: int, stop_at_step: int | None = None,
                      resume: Path | None = None,
                      fixed_batch: bool = False) -> list[str]:
    cmd = [
        sys.executable, "-m", "pipeline.train_asr",
        "--manifest-version", "v2",
        "--exclusions", str(POLICY),
        "--expect-excluded", "19",
        "--adoption-key", descriptor["adoption_key"],
        "--max-steps", str(max_steps),
        "--save-steps", "100" if not fixed_batch else str(max_steps),
        "--descent-gate-step", "0",
        "--batch-size", "2",
        "--grad-accum", "8",
        "--rank", "32",
        "--temperature", "0.5",
        "--seed", str(descriptor["seed"]),
        "--lr", str(lr),
        "--out", str(out),
    ]
    if fixed_batch:
        cmd.append("--fixed-batch")
    if stop_at_step is not None:
        cmd += ["--stop-at-step", str(stop_at_step)]
    if resume is not None:
        cmd += ["--resume", str(resume)]
    return cmd


def run_training(descriptor: dict, out: Path, *, lr: float,
                 max_steps: int, stop_at_step: int | None = None,
                 resume: Path | None = None,
                 fixed_batch: bool = False) -> dict:
    env = dict(os.environ)
    env.setdefault(
        "MLFLOW_TRACKING_URI",
        "sqlite:////cache/stage-mlflow.db")
    env.setdefault(
        "MLFLOW_ARTIFACT_ROOT",
        f"s3://{BUCKET}/mlflow/artifacts")
    cmd = _training_command(
        descriptor, out, lr=lr, max_steps=max_steps,
        stop_at_step=stop_at_step, resume=resume,
        fixed_batch=fixed_batch)
    print("training command:", " ".join(cmd), flush=True)
    completed = subprocess.run(cmd, env=env, check=False)
    if completed.returncode:
        raise SystemExit(
            f"REFUSING: trainer exited {completed.returncode}")
    record = json.loads((out / "run.json").read_bytes())
    expected_step = stop_at_step or max_steps
    if record.get("steps") != expected_step:
        raise SystemExit(
            f"REFUSING: trainer reports {record.get('steps')} steps, "
            f"stage boundary is {expected_step}")
    return record


def _finite_training(record: dict) -> dict:
    history = record.get("loss_history") or []
    losses = [float(row["loss"]) for row in history if "loss" in row]
    gradients = [
        float(row["grad_norm"]) for row in history if "grad_norm" in row
    ]
    reasons = []
    if not losses:
        reasons.append("no logged training loss")
    if not gradients:
        reasons.append("no logged gradient norms")
    if any(not math.isfinite(v) for v in losses):
        reasons.append("a logged loss is non-finite")
    if any(not math.isfinite(v) for v in gradients):
        reasons.append("a logged gradient norm is non-finite")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "train_loss": losses[-1] if losses else None,
        "grad_norm": gradients[-1] if gradients else None,
        "losses_logged": len(losses),
        "gradients_logged": len(gradients),
    }


def verify_saved_adapter(runtime: ValidationRuntime, adapter_dir: Path,
                         training_record: dict,
                         overfit_required: bool = False) -> dict:
    """Reload the saved bytes, then prove structure, effect, finiteness, EOS."""
    import torch
    from peft import PeftModel

    saved_sha = adapter_sha256(adapter_dir)
    base = runtime._fresh_base()
    model = PeftModel.from_pretrained(
        base, str(adapter_dir), is_trainable=True).to(runtime.device)
    reloaded_sha = adapter_sha256(adapter_dir)
    structure = smoke.lora_structure_verdict(model)

    language = orchestrate.VALIDATION_LANGUAGES[0]
    rows, audios = runtime._loaded[language]
    row, (audio, sr) = rows[0], audios[0]
    processor = runtime.processor
    processor.tokenizer.set_prefix_tokens(
        language=LANG_TOKEN[language], task="transcribe")
    features = processor.feature_extractor(
        audio, sampling_rate=sr, return_tensors="pt").input_features[0]
    labels = processor.tokenizer(row["text_normalized"]).input_ids
    from pipeline.train_asr import collate
    batch = collate(
        processor, decoder_start_id(
            processor.tokenizer, model.config))([
                {"input_features": features, "labels": labels}])
    batch = {k: v.to(runtime.device) for k, v in batch.items()}
    if model.dtype != batch["input_features"].dtype:
        batch["input_features"] = batch["input_features"].to(model.dtype)

    model.eval()
    with torch.no_grad():
        on_out = model(**batch)
        on = on_out.logits.detach()
        with model.disable_adapter():
            off = model(**batch).logits.detach()
    norms = {
        name: float(param.detach().norm())
        for name, param in model.named_parameters() if "lora_B" in name
    }
    effect = smoke.adapter_effect_verdict(
        on, off, norms, checkpoint_sha256=saved_sha,
        tested_artifact_sha256=reloaded_sha)

    prompt = expected_prompt(processor, LANG_TOKEN[language])
    eot = processor.tokenizer.convert_tokens_to_ids(EOT_TOKEN)
    with torch.no_grad():
        generated = model.generate(
            batch["input_features"][:1],
            **generation_kwargs(LANG_TOKEN[language]))
    ids = extract_sequence(generated)
    acct = account(ids, prompt, eot)
    finite = _finite_training(training_record)
    generation = smoke.generation_smoke_verdict(
        acct, logits_finite=bool(torch.isfinite(on).all()),
        loss_finite=bool(
            finite["train_loss"] is not None
            and math.isfinite(finite["train_loss"])))

    overfit = None
    if overfit_required:
        overfit = smoke.overfit_verdict(
            float(training_record.get("fixed_batch_l0", float("nan"))),
            float(finite["train_loss"])
            if finite["train_loss"] is not None else float("nan"),
            int(training_record.get("steps", 0)),
            finite["passed"])
    pieces = [structure, effect, generation, finite]
    if overfit is not None:
        pieces.append(overfit)
    reasons = [
        reason for verdict in pieces
        for reason in verdict.get("reasons", [])
    ]
    return {
        "passed": not reasons,
        "reasons": reasons,
        "lora_structure": structure,
        "adapter_effect": effect,
        "generation_smoke": generation,
        "training_finite": finite,
        "overfit": overfit,
        "saved_adapter_sha256": saved_sha,
        "reloaded_adapter_sha256": reloaded_sha,
    }


def _publish_evaluation(cli, descriptor: dict, local: Path, name: str) -> str:
    key = descriptor["output_prefix"].rstrip("/") + f"/evaluations/{name}.json"
    return put_immutable(cli, key, local.read_bytes())


def run_base_and_preflight(cli, descriptor: dict, work: Path) -> dict:
    runtime = ValidationRuntime(cli, descriptor, work / "validation")
    base_path = work / "base-evaluation.json"
    base = runtime.evaluate_base(base_path)
    base_key = (
        descriptor["output_prefix"].rstrip("/")
        + "/evaluations/base.json")
    base_sha = put_immutable(cli, base_key, base_path.read_bytes())
    if base_sha != base["artifact_sha256"]:
        raise SystemExit("REFUSING: base evaluation changed before publication")
    base["artifact_key"] = base_key

    preflight_dir = work / "preflight-adapter"
    train = run_training(
        descriptor, preflight_dir, lr=1e-3, max_steps=200,
        fixed_batch=True)
    preflight = verify_saved_adapter(
        runtime, preflight_dir, train, overfit_required=True)
    manifest = upload_tree(
        cli, preflight_dir,
        descriptor["output_prefix"].rstrip("/") + "/preflight-adapter",
        skip_nested_checkpoints=True)
    preflight["artifact_tree_sha256"] = manifest["tree_sha256"]
    return {"base": base, "preflight": preflight}


def run_sweep(cli, descriptor: dict, work: Path) -> dict:
    base_doc = load_base_result(cli, descriptor)
    base_wer = base_doc["summary"]["wer"]
    adapter = work / "sweep-adapter"
    train = run_training(
        descriptor, adapter, lr=float(descriptor["lr"]),
        max_steps=descriptor["max_steps"])
    runtime = ValidationRuntime(cli, descriptor, work / "validation")
    smoke_result = verify_saved_adapter(runtime, adapter, train)
    eval_path = work / "sweep-evaluation.json"
    result = runtime.evaluate_adapter(adapter, eval_path)
    eval_sha = _publish_evaluation(
        cli, descriptor, eval_path, "checkpoint-100")
    if eval_sha != result["artifact_sha256"]:
        raise SystemExit("REFUSING: sweep evaluation changed before publication")
    gate = orchestrate.apply_checkpoint_controls(
        orchestrate.evaluate_gates(
            result["wer"], base_wer,
            result["eos_rate"], result["cap_hit_rate"]),
        smoke_result)
    manifest = upload_tree(
        cli, adapter,
        descriptor["output_prefix"].rstrip("/") + "/asr/checkpoint-100",
        skip_nested_checkpoints=True)
    finite = _finite_training(train)
    return {
        **result, "artifact_sha256": eval_sha,
        "artifact_tree_sha256": manifest["tree_sha256"],
        "smoke": smoke_result, "gate": gate,
        "train_loss": finite["train_loss"],
        "grad_norm": finite["grad_norm"],
        "steps_completed": train["steps"],
    }


def run_final(cli, descriptor: dict, work: Path) -> dict:
    base_doc = load_base_result(cli, descriptor)
    base_wer = base_doc["summary"]["wer"]
    out = work / "final-adapter"
    runtime = ValidationRuntime(cli, descriptor, work / "validation")
    checkpoints = []
    resume = None
    for step in descriptor["checkpoint_steps"]:
        train = run_training(
            descriptor, out, lr=float(descriptor["lr"]),
            max_steps=descriptor["max_steps"],
            stop_at_step=step if step < descriptor["max_steps"] else None,
            resume=resume)
        checkpoint_dir = out / f"checkpoint-{step}"
        if not checkpoint_dir.is_dir():
            raise SystemExit(
                f"REFUSING: trainer did not save checkpoint-{step}")
        smoke_result = verify_saved_adapter(runtime, checkpoint_dir, train)
        eval_path = work / f"checkpoint-{step}-evaluation.json"
        scored = runtime.evaluate_adapter(checkpoint_dir, eval_path)
        eval_sha = _publish_evaluation(
            cli, descriptor, eval_path, f"checkpoint-{step}")
        if eval_sha != scored["artifact_sha256"]:
            raise SystemExit(
                f"REFUSING: checkpoint-{step} evaluation changed before "
                "publication")
        tree = upload_tree(
            cli, checkpoint_dir,
            descriptor["output_prefix"].rstrip("/")
            + f"/asr/checkpoint-{step}")
        gate = orchestrate.apply_checkpoint_controls(
            orchestrate.evaluate_gates(
                scored["wer"], base_wer,
                scored["eos_rate"], scored["cap_hit_rate"]),
            smoke_result)
        finite = _finite_training(train)
        checkpoints.append({
            "step": step,
            **{k: scored[k] for k in (
                "wer", "cer", "eos_rate", "cap_hit_rate",
                "generated_tokens_median", "generated_tokens_max")},
            "artifact_sha256": eval_sha,
            "artifact_tree_sha256": tree["tree_sha256"],
            "adapter_sha256": scored["adapter_sha256"],
            "smoke": smoke_result,
            "gate": gate,
            "train_loss": finite["train_loss"],
            "grad_norm": finite["grad_norm"],
        })
        if not gate["passed"]:
            break
        resume = checkpoint_dir
    return {
        "checkpoints": checkpoints,
        "steps_completed": checkpoints[-1]["step"] if checkpoints else 0,
        "train_loss": checkpoints[-1].get("train_loss")
        if checkpoints else None,
        "grad_norm": checkpoints[-1].get("grad_norm")
        if checkpoints else None,
    }


def execute(descriptor: dict, out: Path) -> dict:
    stage_descriptor.build(**descriptor)
    require_runtime_provenance(descriptor)
    require_environment()
    cli = _s3()
    work = Path(os.environ.get("MEDZEN_STAGE_WORK", "/cache/stage"))
    work.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    if descriptor["stage"] == "base_and_preflight":
        payload = run_base_and_preflight(cli, descriptor, work)
    elif descriptor["stage"] == "sweep":
        payload = run_sweep(cli, descriptor, work)
    elif descriptor["stage"] == "final":
        payload = run_final(cli, descriptor, work)
    else:  # stage_descriptor already refuses; keeps type checkers honest
        raise SystemExit(f"REFUSING: unknown stage {descriptor['stage']}")
    result = {
        "record": "B4-CONTAINER-STAGE-RESULT",
        "recorded_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stage_descriptor_sha256":
            stage_descriptor.descriptor_hash(descriptor),
        "campaign_run": descriptor["campaign_run"],
        "attempt": descriptor["attempt"],
        "stage": descriptor["stage"],
        "container_runtime_seconds": round(time.monotonic() - started, 1),
        "purpose": descriptor["purpose"],
        "promotable": descriptor["promotable"],
        **payload,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_json_bytes(result))
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--descriptor", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()
    descriptor = json.loads(args.descriptor.read_bytes())
    execute(descriptor, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
