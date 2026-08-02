"""Container-side execution for one immutable B4 stage descriptor.

The EC2 operator is the only launcher.  This module cannot reserve budget or
launch another instance.  It trains/evaluates exactly one descriptor, writes a
container result, and exits; the operator later supplies AWS-observed lifecycle
evidence.
"""
from __future__ import annotations

import argparse
import base64
import gc
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pipeline import language_scope, orchestrate, smoke, stage_descriptor
from pipeline.generation import (EOT_TOKEN, account, expected_prompt,
                                 extract_sequence, generation_kwargs)
from pipeline.label_length import decoder_start_id
from pipeline.languages import LANG_TOKEN
from pipeline.validation_runner import (ValidationRuntime, adapter_sha256,
                                         sha256_file,
                                         termination_failure_summary)


ROOT = Path(__file__).resolve().parent.parent
BUCKET = os.environ.get("MEDZEN_BUCKET", "medzen-speech")
REGION = os.environ.get("AWS_REGION", "eu-central-1")
POLICY = language_scope.POLICY_PATH


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


def require_ctranslate2_cuda_runtime(loader=None) -> tuple[str, ...]:
    """Prove the complete GPU runtime that the CT2 wheel loads dynamically.

    A successful ``import ctranslate2`` is insufficient: the wheel delays
    loading cuBLAS/cuDNN until the first GPU generation.  This preflight is run
    before holdout scoring so an image/runtime mismatch fails cheaply and
    cannot be mistaken for a model-quality failure.
    """
    if loader is None:
        import ctypes
        loader = ctypes.CDLL
    required = (
        "libcudart.so.12",
        "libcublas.so.12",
        "libcublasLt.so.12",
        "libcudnn.so.9",
    )
    failures = []
    for library in required:
        try:
            loader(library)
        except OSError as exc:
            failures.append(f"{library}: {exc}")
    if failures:
        raise SystemExit(
            "REFUSING: CTranslate2 CUDA runtime is incomplete: "
            + "; ".join(failures))
    return required


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
    """Conditionally upload one artifact tree and hash every readback.

    ``upload_file`` has no conditional-create contract.  A successful return
    plus a matching length can therefore accept bytes another writer replaced
    between upload and ``head_object``.  These LoRA artifacts are comfortably
    below PutObject's 5 GiB limit, so every object is created with
    ``IfNoneMatch='*'`` and then streamed back through SHA-256.
    """
    prefix = prefix.rstrip("/") + "/"
    _require_empty(cli, prefix)
    files: dict[str, dict] = {}
    for path in sorted(Path(local).rglob("*")):
        if not path.is_file():
            continue
        if path.is_symlink():
            raise SystemExit(
                f"REFUSING: artifact tree contains symlink {path}")
        rel = path.relative_to(local).as_posix()
        if skip_nested_checkpoints and any(
                part.startswith("checkpoint-")
                for part in Path(rel).parts[:-1]):
            continue
        key = prefix + rel
        digest = sha256_file(path)
        size = path.stat().st_size
        with path.open("rb") as source:
            cli.put_object(
                Bucket=BUCKET, Key=key, Body=source,
                ContentLength=size, IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(
                    bytes.fromhex(digest)).decode(),
                ServerSideEncryption="aws:kms")
        remote = cli.get_object(Bucket=BUCKET, Key=key)
        body = remote["Body"]
        readback = hashlib.sha256()
        readback_bytes = 0
        while True:
            chunk = body.read(8 * 1024 * 1024)
            if not chunk:
                break
            readback.update(chunk)
            readback_bytes += len(chunk)
        if readback_bytes != size or readback.hexdigest() != digest:
            raise SystemExit(
                f"REFUSING: uploaded {key} readback is "
                f"{readback_bytes} bytes/{readback.hexdigest()[:16]}, "
                f"expected {size} bytes/{digest[:16]}")
        files[rel] = {"sha256": digest, "bytes": size}
    if not files:
        raise SystemExit(f"REFUSING: no artifact files found under {local}")
    tree_sha = _sha(json.dumps(
        files, sort_keys=True, separators=(",", ":")).encode())
    manifest = {"prefix": f"s3://{BUCKET}/{prefix}", "files": files,
                "tree_sha256": tree_sha}
    put_immutable(cli, prefix + "ARTIFACT.json", _json_bytes(manifest))
    return manifest


def download_artifact_tree(cli, descriptor: dict, destination: Path) -> dict:
    """Download exactly the immutable adapter tree pinned by the descriptor."""
    prefix = str(descriptor["input_prefix"]).rstrip("/") + "/"
    raw = cli.get_object(
        Bucket=BUCKET, Key=prefix + "ARTIFACT.json")["Body"].read()
    manifest = json.loads(raw)
    if manifest.get("prefix") != f"s3://{BUCKET}/{prefix}":
        raise SystemExit("REFUSING: adapter manifest prefix differs")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise SystemExit("REFUSING: adapter manifest has no files")
    computed_tree = _sha(json.dumps(
        files, sort_keys=True, separators=(",", ":")).encode())
    authorised_tree = descriptor["input_artifact_sha256"]
    if (manifest.get("tree_sha256") != computed_tree
            or computed_tree != authorised_tree):
        raise SystemExit(
            "REFUSING: adapter tree hash differs from the descriptor")
    if "adapter_model.safetensors" not in files:
        raise SystemExit("REFUSING: adapter tree has no model weights")
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    for rel, expected in sorted(files.items()):
        path = Path(rel)
        if (path.is_absolute() or ".." in path.parts
                or path.as_posix() != rel):
            raise SystemExit("REFUSING: unsafe path in adapter manifest")
        want_sha = expected.get("sha256")
        want_bytes = expected.get("bytes")
        if (not isinstance(want_bytes, int) or want_bytes < 0
                or not isinstance(want_sha, str) or len(want_sha) != 64):
            raise SystemExit("REFUSING: malformed adapter file binding")
        body = cli.get_object(
            Bucket=BUCKET, Key=prefix + rel)["Body"].read()
        if len(body) != want_bytes or _sha(body) != want_sha:
            raise SystemExit(
                f"REFUSING: retained adapter file {rel} differs from manifest")
        total += len(body)
        if total > 1_000_000_000:
            raise SystemExit("REFUSING: adapter artifact exceeds 1 GB boundary")
        local = destination / path
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(body)
    return {
        "manifest_sha256": _sha(raw),
        "tree_sha256": computed_tree,
        "files": len(files),
        "bytes": total,
        "adapter_sha256": files["adapter_model.safetensors"]["sha256"],
    }


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
        "--expect-excluded", str(language_scope.EXPECTED_POLICY_ROWS_TOTAL),
        "--expect-applied-exclusions",
        str(language_scope.EXPECTED_POLICY_ROWS_APPLICABLE),
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
        "--languages", *descriptor["training_languages"],
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
    if record.get("dataset_fingerprint") != descriptor["dataset_fingerprint"]:
        raise SystemExit(
            "REFUSING: trainer dataset fingerprint "
            f"{str(record.get('dataset_fingerprint'))[:16]} differs from "
            f"descriptor {descriptor['dataset_fingerprint'][:16]}")
    mix_record = ((record.get("params") or {}).get("mix") or {})
    trained_languages = set(mix_record.get("per_language", {}))
    if trained_languages != set(descriptor["training_languages"]):
        raise SystemExit(
            f"REFUSING: trainer used languages {sorted(trained_languages)}, "
            "descriptor authorises "
            f"{sorted(descriptor['training_languages'])}")
    if mix_record.get("rows") != language_scope.EXPECTED_SAMPLED_ROWS:
        raise SystemExit(
            f"REFUSING: trainer sampled {mix_record.get('rows')} rows, "
            f"scope authorises {language_scope.EXPECTED_SAMPLED_ROWS}")
    eligible = (record.get("manifest_provenance") or {}).get("eligible_rows")
    if eligible != language_scope.EXPECTED_ELIGIBLE_ROWS:
        raise SystemExit(
            f"REFUSING: trainer found {eligible} eligible rows, scope "
            f"authorises {language_scope.EXPECTED_ELIGIBLE_ROWS}")
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

    # This gate deliberately runs before the expensive full base evaluation.
    # Prepare its own verified input instead of relying on evaluation to have
    # populated ValidationRuntime's cache as an incidental side effect.
    runtime.ensure_prepared()
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
    # Prove that the training path can execute one bounded, overfit-capable
    # batch before spending ~20 minutes scoring the full base arm.  Attempt 2
    # exposed a mixed-precision failure only after all 385 base rows had been
    # decoded because these operations were reversed.
    preflight_dir = work / "preflight-adapter"
    train = run_training(
        descriptor, preflight_dir, lr=1e-3, max_steps=200,
        fixed_batch=True)
    preflight = verify_saved_adapter(
        runtime, preflight_dir, train, overfit_required=True)

    base_path = work / "base-evaluation.json"
    base = runtime.evaluate_base(base_path)
    base_key = (
        descriptor["output_prefix"].rstrip("/")
        + "/evaluations/base.json")
    base_sha = put_immutable(cli, base_key, base_path.read_bytes())
    if base_sha != base["artifact_sha256"]:
        raise SystemExit("REFUSING: base evaluation changed before publication")
    base["artifact_key"] = base_key

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
    comparison_step = descriptor["checkpoint_steps"][0]
    train = run_training(
        descriptor, adapter, lr=float(descriptor["lr"]),
        max_steps=descriptor["max_steps"], stop_at_step=comparison_step)
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
            result["eos_rate"], result["cap_hit_rate"],
            result["termination_failures"], descriptor["termination_gate"]),
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
    prior_termination_failures = {
        language: set() for language in descriptor["validation_languages"]}
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
                scored["eos_rate"], scored["cap_hit_rate"],
                scored["termination_failures"],
                descriptor["termination_gate"]),
            smoke_result)
        gate = orchestrate.apply_termination_recurrence(
            gate, prior_termination_failures)
        for language, evidence in scored["termination_failures"].items():
            prior_termination_failures[language].update(evidence["checksums"])
        finite = _finite_training(train)
        checkpoints.append({
            "step": step,
            **{k: scored[k] for k in (
                "wer", "cer", "eos_rate", "cap_hit_rate",
                "generated_tokens_median", "generated_tokens_max",
                "termination_failures")},
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


def _load_selected_evaluation(cli, descriptor: dict) -> dict:
    raw = cli.get_object(
        Bucket=BUCKET, Key=descriptor["input_evaluation_key"])["Body"].read()
    if _sha(raw) != descriptor["input_evaluation_sha256"]:
        raise SystemExit(
            "REFUSING: selected checkpoint evaluation differs from descriptor")
    doc = json.loads(raw)
    if doc.get("arm") != "candidate":
        raise SystemExit("REFUSING: selected checkpoint evaluation is not candidate")
    return doc


def _lingala_holdout_runtime(cli, descriptor: dict, cache: Path
                             ) -> ValidationRuntime:
    evidence_path = (
        ROOT / "platform/evidence/VAL-2026-003-lingala-post-selection-holdout.json")
    raw = evidence_path.read_bytes()
    if _sha(raw) != descriptor["holdout_evidence_sha256"]:
        raise SystemExit("REFUSING: Lingala holdout evidence hash changed")
    evidence = json.loads(raw)
    info = {
        "key": evidence["holdout_manifest_key"],
        "manifest_sha256": evidence["holdout_manifest_sha256"],
        "rows": evidence["rows"],
        "minutes": evidence["minutes"],
    }
    return ValidationRuntime(
        cli, descriptor, cache, languages=("lingala",),
        manifest_set={"lingala": info}, validation_record_sha256=_sha(raw))


def _relative_gain_gate(candidate: dict, base: dict, languages: tuple[str, ...]
                        ) -> dict:
    gains = {
        language: ((float(base[language]) - float(candidate[language]))
                   / float(base[language]))
        for language in languages
    }
    failures = [
        f"{language} relative WER gain {gain:.2%} is below 15%"
        for language, gain in gains.items() if gain < 0.15
    ]
    return {"passed": not failures, "minimum_relative_gain": 0.15,
            "relative_wer_gain": gains, "failures": failures}


def _termination_count_gate(evidence: dict, languages: tuple[str, ...],
                            max_unique: int) -> dict:
    """Apply a checksum-count gate without persisting any private content."""
    failures = []
    counts, checksums = {}, {}
    for language in languages:
        item = evidence.get(language) or {}
        rows = item.get("checksums")
        if not isinstance(rows, list) or rows != sorted(set(rows)):
            raise SystemExit(
                f"REFUSING: {language} termination checksums are malformed")
        counts[language] = len(rows)
        checksums[language] = rows
        if len(rows) > max_unique:
            failures.append(
                f"{language} has {len(rows)} unique termination failures, "
                f"over the {max_unique} limit")
    return {
        "passed": not failures,
        "max_unique_failures_per_language": max_unique,
        "counts": counts,
        "checksums": checksums,
        "failures": failures,
    }


def _post_conversion_termination_gate(before: dict, converted: dict,
                                      languages: tuple[str, ...],
                                      policy: dict) -> dict:
    """Conversion may not create or move a known termination failure."""
    max_unique = int(policy["max_unique_failures_per_language"])
    result = _termination_count_gate(converted, languages, max_unique)
    new_checksums, new_eos_missing, new_cap_hits, count_increases = {}, {}, {}, {}
    for language in languages:
        old_item = before.get(language) or {}
        new_item = converted.get(language) or {}
        old = set(old_item.get("checksums") or [])
        new = set(new_item.get("checksums") or [])
        introduced = sorted(new - old)
        if introduced:
            new_checksums[language] = introduced
        eos_introduced = sorted(
            set(new_item.get("eos_missing_checksums") or [])
            - set(old_item.get("eos_missing_checksums") or []))
        cap_introduced = sorted(
            set(new_item.get("cap_hit_checksums") or [])
            - set(old_item.get("cap_hit_checksums") or []))
        if eos_introduced:
            new_eos_missing[language] = eos_introduced
        if cap_introduced:
            new_cap_hits[language] = cap_introduced
        if len(new) > len(old):
            count_increases[language] = {"before": len(old), "after": len(new)}
    if ((new_checksums or new_eos_missing or new_cap_hits)
            and not policy["new_or_different_failure_checksums_allowed"]):
        result["passed"] = False
        result["failures"].append(
            "conversion introduced different termination evidence: "
            + json.dumps({
                "unique": new_checksums,
                "eos_missing": new_eos_missing,
                "cap_hits": new_cap_hits,
            }, sort_keys=True))
    if count_increases and not policy["failure_count_increase_allowed"]:
        result["passed"] = False
        result["failures"].append(
            "conversion increased termination-failure counts: "
            + json.dumps(count_increases, sort_keys=True))
    result["new_checksums"] = new_checksums
    result["new_eos_missing_checksums"] = new_eos_missing
    result["new_cap_hit_checksums"] = new_cap_hits
    result["count_increases"] = count_increases
    return result


def _score_ctranslate2(runtime: ValidationRuntime, model_dir: Path) -> dict:
    """Score the converted bytes directly, including termination accounting.

    CTranslate2 removes the terminal end token from Whisper results.  With
    greedy single-segment decoding and no other stop callback, a sequence below
    ``max_length`` therefore terminated on EOS; reaching the bound is the cap.
    This convention is recorded rather than pretending the removed token was
    observed.
    """
    import ctranslate2
    import jiwer
    from pipeline.normalizers import for_language
    from scripts.run_baseline import bootstrap_ci, wer_cer

    runtime.ensure_prepared()
    model = ctranslate2.models.Whisper(
        str(model_dir), device="cuda", compute_type="int8_float16")
    per_language = {}
    for language in runtime.languages:
        rows, audios = runtime._loaded[language]
        prompt = expected_prompt(runtime.processor, LANG_TOKEN[language])
        norm = for_language(language)
        refs, hyps, per = [], [], []
        for rec, (audio, sr) in zip(rows, audios):
            inputs = runtime.processor.feature_extractor(
                audio, sampling_rate=sr, return_tensors="np")
            features = ctranslate2.StorageView.from_array(inputs.input_features)
            t0 = time.perf_counter()
            generated = model.generate(
                features, [prompt], beam_size=1,
                max_length=440, sampling_topk=1,
                sampling_temperature=1, suppress_tokens=[-1])
            latency = time.perf_counter() - t0
            if len(generated) != 1 or len(generated[0].sequences_ids) != 1:
                raise SystemExit(
                    "REFUSING: CTranslate2 returned an ambiguous hypothesis set")
            ids = list(generated[0].sequences_ids[0])
            if ids[:len(prompt)] == prompt:
                ids = ids[len(prompt):]
            if len(ids) > 440:
                raise SystemExit(
                    "REFUSING: CTranslate2 exceeded the authorised token cap")
            cap_hit = len(ids) >= 440
            eos = not cap_hit
            text = runtime.processor.tokenizer.decode(
                ids, skip_special_tokens=True)
            ref, hyp = norm(rec["text_normalized"]), norm(text)
            refs.append(ref)
            hyps.append(hyp)
            per.append({
                "audio_checksum_sha256": rec["audio_checksum_sha256"],
                "prompt_tokens": len(prompt),
                "generated_tokens": len(ids),
                "total_tokens": len(prompt) + len(ids),
                "eos_emitted": eos,
                "hit_length_cap": cap_hit,
                "stop_reason": "max_length" if cap_hit else "eos_removed_by_runtime",
                "ref_words": len(ref.split()), "hyp_words": len(hyp.split()),
                "latency_s": round(latency, 4),
                "wer": round(jiwer.wer(ref, hyp), 4) if ref.strip() else None,
            })
        wer, cer = wer_cer(refs, hyps)
        lo, hi = bootstrap_ci(refs, hyps)
        lengths = [row["generated_tokens"] for row in per]
        per_language[language] = {
            "rows": len(rows), "wer": round(wer, 4), "cer": round(cer, 4),
            "wer_ci95": [round(lo, 4), round(hi, 4)],
            "eos_rate": round(sum(r["eos_emitted"] for r in per) / len(per), 4),
            "cap_hit_rate": round(sum(r["hit_length_cap"] for r in per) / len(per), 4),
            "generated_tokens": {
                "median": statistics.median(lengths), "max": max(lengths),
                "mean": round(statistics.mean(lengths), 2), "min": min(lengths)},
            "normalization_version": norm.version,
            "per_utterance": per,
        }
    return {
        "wer": {l: per_language[l]["wer"] for l in runtime.languages},
        "cer": {l: per_language[l]["cer"] for l in runtime.languages},
        "eos_rate": {l: per_language[l]["eos_rate"] for l in runtime.languages},
        "cap_hit_rate": {
            l: per_language[l]["cap_hit_rate"] for l in runtime.languages},
        "generated_tokens_median": {
            l: per_language[l]["generated_tokens"]["median"]
            for l in runtime.languages},
        "generated_tokens_max": {
            l: per_language[l]["generated_tokens"]["max"]
            for l in runtime.languages},
        "per_language": per_language,
        "termination_failures": termination_failure_summary(
            per_language, runtime.languages),
        "compute_type": model.compute_type,
        "runtime": f"ctranslate2-{ctranslate2.__version__}",
        "termination_accounting": (
            "Whisper generate removes terminal EOS; greedy output shorter than "
            "max_length=440 is EOS termination, length 440 is a cap hit"),
    }


def _save_processor_for_ctranslate2(processor, merged_dir: Path) -> None:
    """Persist every processor file required by the CT2 Whisper converter.

    ``WhisperProcessor.save_pretrained`` in the pinned Transformers runtime
    did not emit ``preprocessor_config.json`` for the merged directory.  The
    converter requires that file while loading the model, before ``--copy_files``
    is applied.  Save both components explicitly and then fail locally if the
    exact converter inputs are still absent.
    """
    merged_dir.mkdir(parents=True, exist_ok=True)
    processor.save_pretrained(merged_dir)
    feature_extractor = getattr(processor, "feature_extractor", None)
    tokenizer = getattr(processor, "tokenizer", None)
    if feature_extractor is None or tokenizer is None:
        raise SystemExit(
            "REFUSING: Whisper processor lacks a feature extractor or tokenizer")
    feature_extractor.save_pretrained(merged_dir)
    tokenizer.save_pretrained(merged_dir)
    required = ("preprocessor_config.json", "tokenizer.json")
    missing = [name for name in required if not (merged_dir / name).is_file()]
    if missing:
        raise SystemExit(
            "REFUSING: merged model lacks CT2 processor file(s): "
            + ", ".join(missing))


def run_artifactize(cli, descriptor: dict, work: Path) -> dict:
    """Holdout, merge, conversion, and scoring of the actual servable bytes."""
    import torch
    from peft import PeftModel

    require_ctranslate2_cuda_runtime()

    selected_eval = _load_selected_evaluation(cli, descriptor)
    adapter_dir = work / "selected-adapter"
    retained = download_artifact_tree(cli, descriptor, adapter_dir)

    holdout = _lingala_holdout_runtime(cli, descriptor, work / "holdout")
    holdout_base_path = work / "holdout-base.json"
    holdout_candidate_path = work / "holdout-candidate.json"
    holdout_base = holdout.evaluate_base(holdout_base_path)
    holdout_candidate = holdout.evaluate_adapter(
        adapter_dir, holdout_candidate_path,
        expected_adapter_sha256=retained["adapter_sha256"])
    holdout_gate = _relative_gain_gate(
        holdout_candidate["wer"], holdout_base["wer"], ("lingala",))
    holdout_termination_gate = _termination_count_gate(
        holdout_candidate["termination_failures"], ("lingala",),
        int(descriptor["termination_gate"]["holdout_max_unique_failures"]))
    if not holdout_termination_gate["passed"]:
        holdout_gate["passed"] = False
        holdout_gate["failures"].append(
            "Lingala holdout termination gate failed: "
            + "; ".join(holdout_termination_gate["failures"]))
    holdout_gate["termination"] = holdout_termination_gate
    if not holdout_gate["passed"]:
        raise SystemExit(
            "REFUSING: selected checkpoint failed post-selection Lingala holdout: "
            + "; ".join(holdout_gate["failures"]))

    selection = ValidationRuntime(cli, descriptor, work / "selection")
    selection.ensure_prepared()
    base = selection._fresh_base()
    peft_model = PeftModel.from_pretrained(
        base, str(adapter_dir), is_trainable=False).to(selection.device).eval()
    merged = peft_model.merge_and_unload(safe_merge=True)
    merged_dir = work / "merged-transformers"
    merged.save_pretrained(merged_dir, safe_serialization=True)
    _save_processor_for_ctranslate2(selection.processor, merged_dir)
    del peft_model, merged, base
    gc.collect()
    torch.cuda.empty_cache()

    converted_dir = work / "ctranslate2-int8-float16"
    completed = subprocess.run([
        "ct2-transformers-converter", "--model", str(merged_dir),
        "--output_dir", str(converted_dir), "--quantization", "int8_float16",
        "--copy_files", "tokenizer.json", "preprocessor_config.json",
    ], check=False)
    if completed.returncode:
        raise SystemExit(
            f"REFUSING: CTranslate2 converter exited {completed.returncode}")
    if not (converted_dir / "model.bin").is_file():
        raise SystemExit("REFUSING: converted artifact has no model.bin")

    converted = _score_ctranslate2(selection, converted_dir)
    before = selected_eval["summary"]
    languages = tuple(descriptor["validation_languages"])
    converted_gate = _relative_gain_gate(
        converted["wer"], load_base_result(cli, descriptor)["summary"]["wer"],
        languages)
    converted_termination_gate = _post_conversion_termination_gate(
        before["termination_failures"], converted["termination_failures"],
        languages, descriptor["termination_gate"]["post_conversion"])
    if not converted_termination_gate["passed"]:
        converted_gate["passed"] = False
        converted_gate["failures"].extend(
            converted_termination_gate["failures"])
    converted_gate["termination"] = converted_termination_gate
    if not converted_gate["passed"]:
        raise SystemExit(
            "REFUSING: converted artifact failed active quality gates: "
            + "; ".join(converted_gate["failures"]))
    deltas = {
        metric: {language: round(
            float(converted[metric][language])
            - float(before[metric][language]), 6)
            for language in languages}
        for metric in ("wer", "cer", "eos_rate", "cap_hit_rate")
    }
    merged_manifest = upload_tree(
        cli, merged_dir, descriptor["output_prefix"].rstrip("/") + "/merged")
    converted_manifest = upload_tree(
        cli, converted_dir,
        descriptor["output_prefix"].rstrip("/") + "/ctranslate2-int8-float16")
    record = {
        "record": "B4-SERVABLE-ARTIFACT-EVALUATION",
        "promotable": False, "registered_models": 0, "b5_allowed": False,
        "selected_input_tree_sha256": retained["tree_sha256"],
        "selected_evaluation_sha256": descriptor["input_evaluation_sha256"],
        "holdout": {"base": holdout_base, "candidate": holdout_candidate,
                    "gate": holdout_gate},
        "pre_conversion": before, "post_conversion": converted,
        "conversion_delta": deltas, "converted_gate": converted_gate,
        "merged_tree_sha256": merged_manifest["tree_sha256"],
        "converted_tree_sha256": converted_manifest["tree_sha256"],
    }
    record_key = descriptor["output_prefix"].rstrip("/") + "/artifact-evaluation.json"
    record_sha = put_immutable(cli, record_key, _json_bytes(record))
    return {**record, "artifact_evaluation_key": record_key,
            "artifact_evaluation_sha256": record_sha}


def run_spot_checkpoint(cli, descriptor: dict, work: Path) -> dict:
    """Publish checkpoint-100, then wait for the operator to interrupt Spot."""
    out = work / "spot-checkpoint"
    train = run_training(
        descriptor, out, lr=float(descriptor["lr"]), max_steps=100)
    checkpoint = out / "checkpoint-100"
    if not checkpoint.is_dir():
        raise SystemExit("REFUSING: Spot proof produced no checkpoint-100")
    manifest = upload_tree(
        cli, checkpoint,
        descriptor["output_prefix"].rstrip("/") + "/resume-checkpoint/checkpoint-100")
    marker = {
        "record": "B4-SPOT-CHECKPOINT-READY", "checkpoint_step": 100,
        "checkpoint_tree_sha256": manifest["tree_sha256"],
        "checkpoint_prefix": (
            descriptor["output_prefix"].rstrip("/")
            + "/resume-checkpoint/checkpoint-100"),
        "stage_descriptor_sha256": stage_descriptor.descriptor_hash(descriptor),
        "training_steps": train["steps"],
    }
    marker_key = descriptor["output_prefix"].rstrip("/") + "/spot-checkpoint-ready.json"
    marker_sha = put_immutable(cli, marker_key, _json_bytes(marker))
    if os.environ.get("MEDZEN_TEST_SPOT_NO_WAIT") == "1":
        return {**marker, "marker_key": marker_key, "marker_sha256": marker_sha}
    print("SPOT CHECKPOINT DURABLE; waiting for operator interruption", flush=True)
    while True:
        time.sleep(30)


def run_spot_resume(cli, descriptor: dict, work: Path) -> dict:
    resume = work / "resume-checkpoint-100"
    retained = download_artifact_tree(cli, descriptor, resume)
    state = json.loads((resume / "trainer_state.json").read_bytes())
    if state.get("global_step") != 100:
        raise SystemExit(
            "REFUSING: authorised Spot checkpoint is not at step 100")
    out = work / "spot-resumed"
    train = run_training(
        descriptor, out, lr=float(descriptor["lr"]), max_steps=200,
        resume=resume)
    checkpoint = out / "checkpoint-200"
    if train.get("steps") != 200 or not checkpoint.is_dir():
        raise SystemExit(
            "REFUSING: resumed Spot run did not reach checkpoint-200")
    manifest = upload_tree(
        cli, checkpoint,
        descriptor["output_prefix"].rstrip("/") + "/resumed/checkpoint-200")
    return {
        "resumed_from_step": 100, "steps_completed": 200,
        "resumed_from_tree_sha256": retained["tree_sha256"],
        "resumed_checkpoint_tree_sha256": manifest["tree_sha256"],
        "exact_checkpoint_match": (
            retained["tree_sha256"] == descriptor["input_artifact_sha256"]),
        "training_finite": _finite_training(train),
    }


def run_diagnostic(cli, descriptor: dict, work: Path) -> dict:
    """Compare base and retained 1e-4 adapter without optimisation or writes."""
    import peft
    import torch
    import transformers
    from peft import PeftModel
    from pipeline.termination_diagnostic import (
        diagnose_model, prompt_contract_sha256)

    base_doc = load_base_result(cli, descriptor)
    adapter_dir = work / "retained-adapter"
    retained = download_artifact_tree(cli, descriptor, adapter_dir)
    if adapter_sha256(adapter_dir) != retained["adapter_sha256"]:
        raise SystemExit("REFUSING: downloaded adapter identity changed")

    runtime = ValidationRuntime(cli, descriptor, work / "validation")
    runtime.ensure_prepared()
    base = runtime._fresh_base()
    base_metrics = diagnose_model(runtime, base, "base")
    del base
    gc.collect()
    torch.cuda.empty_cache()

    base = runtime._fresh_base()
    candidate = PeftModel.from_pretrained(
        base, str(adapter_dir), is_trainable=False).to(
            runtime.device).eval()
    candidate_metrics = diagnose_model(runtime, candidate, "retained-1e-4")

    tokenizer_files = {}
    for path in sorted(runtime.base_dir.rglob("*")):
        if (path.is_file() and any(part in path.name for part in (
                "tokenizer", "vocab", "merges", "normalizer",
                "preprocessor", "special_tokens"))):
            tokenizer_files[path.relative_to(runtime.base_dir).as_posix()] = {
                "sha256": sha256_file(path), "bytes": path.stat().st_size}
    if not tokenizer_files:
        raise SystemExit("REFUSING: no tokenizer files found in pinned base")
    tokenizer_sha = _sha(json.dumps(
        tokenizer_files, sort_keys=True, separators=(",", ":")).encode())
    record = {
        "record": "B4-TERMINATION-DIAGNOSTIC",
        "recorded_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "purpose": descriptor["purpose"],
        "promotable": False,
        "training_steps": 0,
        "stage_descriptor_sha256":
            stage_descriptor.descriptor_hash(descriptor),
        "provenance": {
            "git_sha": descriptor["git_sha"],
            "bundle_tar_sha256": descriptor["bundle_tar_sha256"],
            "image_digest": descriptor["image_digest"],
            "policy_sha256": descriptor["policy_sha256"],
            "dataset_fingerprint": descriptor["dataset_fingerprint"],
            "base_manifest_sha256": runtime.base_manifest_sha,
            "base_artifact_key": descriptor["base_artifact_key"],
            "base_artifact_sha256": descriptor["base_artifact_sha256"],
            "base_arm_key": descriptor["base_arm_key"],
            "validation_record_sha256": runtime.frozen_sha,
            "validation_manifests": {
                language: runtime.frozen["sets"][language]["manifest_sha256"]
                for language in orchestrate.VALIDATION_LANGUAGES
            },
            "retained_adapter_prefix": descriptor["input_prefix"],
            "retained_adapter_tree_sha256": retained["tree_sha256"],
            "retained_adapter_manifest_sha256": retained["manifest_sha256"],
            "retained_adapter_sha256": retained["adapter_sha256"],
            "tokenizer_files_sha256": tokenizer_sha,
            "prompt_contract_sha256": prompt_contract_sha256(runtime),
            "generation_config_fingerprint":
                descriptor["generation_config_fingerprint"],
            "evaluator_sha256": descriptor["evaluator_sha256"],
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "peft": peft.__version__,
            "device": runtime.device,
        },
        "metric_definitions": {
            "content_targets": (
                "targets after language/task/no-timestamps and before EOS"),
            "eos_rank": "one plus logits strictly greater than EOS",
            "repeated_ngram_rate": (
                "repeated generated pre-EOS ngrams divided by all such ngrams"),
            "unexpected_control_tokens": (
                "special tokens generated after prompt and before terminal EOS"),
            "all_languages_weighted": "weighted by validation rows",
        },
        "arms": {"base": base_metrics, "retained_1e_4": candidate_metrics},
        "content_policy": (
            "aggregate numeric metrics only; no transcript, token sequence, "
            "row identifier, speaker, session, or audio is persisted"),
    }
    local = work / "termination-diagnostic.json"
    local.write_bytes(_json_bytes(record))
    key = (descriptor["output_prefix"].rstrip("/")
           + "/evaluations/termination-diagnostic.json")
    artifact_sha = put_immutable(cli, key, local.read_bytes())
    return {
        "diagnostic_artifact_key": key,
        "diagnostic_artifact_sha256": artifact_sha,
        "retained_adapter_sha256": retained["adapter_sha256"],
        "training_steps": 0,
        "arms": record["arms"],
    }


def run_decode_compatibility(cli, descriptor: dict, work: Path) -> dict:
    """Compare three Amharic decode contracts without training or row output."""
    import peft
    import torch
    import transformers
    from peft import PeftModel
    from pipeline.decode_compatibility import (
        STRATEGIES, score_strategy, select_strategy, strategy_fingerprint)
    from pipeline.termination_diagnostic import prompt_contract_sha256

    load_base_result(cli, descriptor)
    adapter_dir = work / "retained-adapter"
    retained = download_artifact_tree(cli, descriptor, adapter_dir)
    if adapter_sha256(adapter_dir) != retained["adapter_sha256"]:
        raise SystemExit("REFUSING: downloaded adapter identity changed")

    runtime = ValidationRuntime(
        cli, descriptor, work / "validation", languages=("amharic",))
    runtime.ensure_prepared()
    rows, audios = runtime._loaded["amharic"]
    token = LANG_TOKEN["amharic"]
    prompt = expected_prompt(runtime.processor, token)

    by_arm = {"base": {}, "retained_1e_4": {}}
    base = runtime._fresh_base()
    for strategy in STRATEGIES:
        by_arm["base"][strategy] = score_strategy(
            base, runtime.processor, rows, audios, "amharic",
            runtime.device, token, prompt, strategy)
    del base
    gc.collect()
    torch.cuda.empty_cache()

    base = runtime._fresh_base()
    candidate = PeftModel.from_pretrained(
        base, str(adapter_dir), is_trainable=False).to(
            runtime.device).eval()
    for strategy in STRATEGIES:
        by_arm["retained_1e_4"][strategy] = score_strategy(
            candidate, runtime.processor, rows, audios, "amharic",
            runtime.device, token, prompt, strategy)

    results = {
        strategy: {
            "base": by_arm["base"][strategy],
            "retained_1e_4": by_arm["retained_1e_4"][strategy],
        }
        for strategy in STRATEGIES
    }
    selection = select_strategy(results)
    record = {
        "record": "B4-AMHARIC-DECODE-COMPATIBILITY",
        "recorded_utc": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "purpose": descriptor["purpose"],
        "promotable": False,
        "training_steps": 0,
        "language": "amharic",
        "rows": len(rows),
        "stage_descriptor_sha256":
            stage_descriptor.descriptor_hash(descriptor),
        "provenance": {
            "git_sha": descriptor["git_sha"],
            "bundle_tar_sha256": descriptor["bundle_tar_sha256"],
            "image_digest": descriptor["image_digest"],
            "policy_sha256": descriptor["policy_sha256"],
            "dataset_fingerprint": descriptor["dataset_fingerprint"],
            "base_manifest_sha256": runtime.base_manifest_sha,
            "base_artifact_key": descriptor["base_artifact_key"],
            "base_artifact_sha256": descriptor["base_artifact_sha256"],
            "base_arm_key": descriptor["base_arm_key"],
            "validation_record_sha256": runtime.frozen_sha,
            "amharic_manifest_sha256":
                runtime.frozen["sets"]["amharic"]["manifest_sha256"],
            "retained_adapter_prefix": descriptor["input_prefix"],
            "retained_adapter_tree_sha256": retained["tree_sha256"],
            "retained_adapter_manifest_sha256": retained["manifest_sha256"],
            "retained_adapter_sha256": retained["adapter_sha256"],
            "prompt_contract_sha256": prompt_contract_sha256(runtime),
            "strategy_fingerprint": strategy_fingerprint(),
            "evaluator_sha256": descriptor["evaluator_sha256"],
            "torch": torch.__version__,
            "transformers": transformers.__version__,
            "peft": peft.__version__,
            "device": runtime.device,
        },
        "strategies": results,
        "selection": selection,
        "content_policy": (
            "aggregate numeric metrics only; no transcript, token sequence, "
            "audio checksum, row identifier, speaker, session, or audio is "
            "persisted"),
    }
    local = work / "decode-compatibility.json"
    local.write_bytes(_json_bytes(record))
    key = (descriptor["output_prefix"].rstrip("/")
           + "/evaluations/decode-compatibility.json")
    artifact_sha = put_immutable(cli, key, local.read_bytes())
    return {
        "decode_artifact_key": key,
        "decode_artifact_sha256": artifact_sha,
        "retained_adapter_sha256": retained["adapter_sha256"],
        "training_steps": 0,
        "strategies": record["strategies"],
        "selection": record["selection"],
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
    elif descriptor["stage"] == "artifactize":
        payload = run_artifactize(cli, descriptor, work)
    elif descriptor["stage"] == "spot_checkpoint":
        payload = run_spot_checkpoint(cli, descriptor, work)
    elif descriptor["stage"] == "spot_resume":
        payload = run_spot_resume(cli, descriptor, work)
    elif descriptor["stage"] == "diagnostic":
        payload = run_diagnostic(cli, descriptor, work)
    elif descriptor["stage"] == "decode_compatibility":
        payload = run_decode_compatibility(cli, descriptor, work)
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
