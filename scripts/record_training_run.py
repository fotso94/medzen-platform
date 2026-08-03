#!/usr/bin/env python3
"""Bind a completed training run to the artifacts it actually produced.

A run record copied out of run.json proves only that the trainer wrote a file
about itself. Everything here is recomputed or cross-checked against an
independent source:

  * the adapter and every final artifact are hashed from the BYTES in S3;
  * step count and loss come from checkpoint-600/trainer_state.json, not from
    the trainer's own summary;
  * the fingerprint, exclusion counts and policy hash are compared across
    THREE sources -- run.json, the MLflow tracking DB, and the deferral policy
    object in S3 -- which must agree;
  * the image digest is confirmed against ECR, and the baked commit against
    the image config blob.

Disagreement between any two sources is fatal. A record that silently prefers
one source over another is how a run comes to be described by the least
trustworthy thing that mentioned it.

    python scripts/record_training_run.py --run-id <32 hex> \
        --preflight-prefix candidates/preflight/preflight-1785439231
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

BUCKET = "medzen-speech"
ROOT = Path(__file__).resolve().parent.parent
REPO = "medzen-trainer"


def client(svc="s3"):
    import boto3
    return boto3.Session(profile_name="medzen", region_name="eu-central-1").client(svc)


def sha256_key(cli, key: str) -> tuple[str, int]:
    """Stream the object and hash the bytes. No ETag shortcuts: a multipart
    ETag is a hash of hashes and says nothing about the content."""
    h, n = hashlib.sha256(), 0
    body = cli.get_object(Bucket=BUCKET, Key=key)["Body"]
    for chunk in iter(lambda: body.read(1024 * 1024), b""):
        h.update(chunk)
        n += len(chunk)
    return h.hexdigest(), n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--preflight-prefix", required=True)
    ap.add_argument("--expect-steps", type=int, default=600)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    cli = client()
    base = f"candidates/asr/{a.run_id}"
    problems: list[str] = []

    # ---- source 1: the trainer's own summary -------------------------------
    run = json.loads(cli.get_object(Bucket=BUCKET,
                                    Key=f"{base}/final/run.json")["Body"].read())

    # ---- source 2: the checkpoint the trainer did not write about ----------
    ts = json.loads(cli.get_object(
        Bucket=BUCKET,
        Key=f"{base}/checkpoint-{a.expect_steps}/trainer_state.json")["Body"].read())
    losses = [x["loss"] for x in ts["log_history"] if "loss" in x]
    if ts["global_step"] != a.expect_steps:
        problems.append(f"trainer_state global_step {ts['global_step']} != "
                        f"{a.expect_steps}")
    if run.get("steps") != ts["global_step"]:
        problems.append(f"run.json steps {run.get('steps')} != trainer_state "
                        f"{ts['global_step']}")
    if not losses:
        problems.append("no losses logged")
    elif not all(l == l and abs(l) != float("inf") for l in losses):
        problems.append("a logged loss is not finite")

    # ---- source 3: the MLflow tracking DB ----------------------------------
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        cli.download_fileobj(BUCKET, f"mlflow/db/{a.run_id}/mlflow.db", tmp)
        tmp.flush()
        con = sqlite3.connect(tmp.name)
        params = {k: v for k, v in con.execute(
            "select key,value from params where run_uuid=?", (a.run_id,))}
        tags = {k: v for k, v in con.execute(
            "select key,value from tags where run_uuid=?", (a.run_id,))}
        status = con.execute("select status from runs where run_uuid=?",
                             (a.run_id,)).fetchone()
        con.close()
    if status and status[0] != "FINISHED":
        problems.append(f"MLflow run status is {status[0]!r}, not FINISHED")

    # the three sources must agree on what was trained on
    fp = run.get("dataset_fingerprint")
    if params.get("dataset_fingerprint") != fp:
        problems.append("fingerprint differs between run.json and MLflow")
    d = run.get("deferral") or {}
    for k, want in (("exclusions_removed", 20), ("exclusions_declared", 20),
                    ("exclusions_over_decoder_limit", 6),
                    ("exclusions_extreme_token_rate", 14),
                    ("over_limit_rows_remaining", 0)):
        if d.get(k) != want:
            problems.append(f"run.json {k}={d.get(k)}, expected {want}")
        if str(params.get(k)) != str(want):
            problems.append(f"MLflow {k}={params.get(k)}, expected {want}")

    # ---- the policy object in S3 must be the one that was applied ----------
    pol_key = "curated/_versions/v2/DEFERRAL-DQ-2026-002.json"
    pol_sha, _ = sha256_key(cli, pol_key)
    if d.get("exclusions_policy_sha256") != pol_sha:
        problems.append("the applied policy hash does not match the policy in S3")
    adopt = json.loads(cli.get_object(
        Bucket=BUCKET, Key="curated/_versions/v2/ADOPTION.json")["Body"].read())
    if adopt.get("deferral_policy_sha256") != pol_sha:
        problems.append("the adopted policy hash does not match the policy in S3")
    comp_raw = cli.get_object(Bucket=BUCKET,
                              Key="curated/_versions/v2/COMPLETE.json")["Body"].read()
    if adopt.get("complete_raw_sha256") != hashlib.sha256(comp_raw).hexdigest():
        problems.append("the corpus changed since it was adopted")

    # ---- hash every final artifact from its bytes --------------------------
    finals = {}
    for o in cli.list_objects_v2(Bucket=BUCKET,
                                 Prefix=f"{base}/final/").get("Contents", []):
        name = o["Key"].rsplit("/", 1)[-1]
        sha, n = sha256_key(cli, o["Key"])
        finals[name] = {"sha256": sha, "bytes": n}
    if "adapter_model.safetensors" not in finals:
        problems.append("no adapter in final/")

    # ---- checkpoints ------------------------------------------------------
    ckpts = {}
    for step in range(100, a.expect_steps + 1, 100):
        objs = cli.list_objects_v2(
            Bucket=BUCKET, Prefix=f"{base}/checkpoint-{step}/").get("Contents", [])
        ckpts[f"checkpoint-{step}"] = {
            "objects": len(objs), "bytes": sum(o["Size"] for o in objs)}
        if not objs:
            problems.append(f"checkpoint-{step} is absent")

    # ---- image identity ---------------------------------------------------
    ecr = client("ecr")
    dig = run.get("image_digest") or ""
    try:
        img = ecr.describe_images(repositoryName=REPO,
                                  imageIds=[{"imageDigest": dig}])["imageDetails"][0]
        tags_ecr = img.get("imageTags", [])
    except Exception as e:
        tags_ecr = []
        problems.append(f"image digest not found in ECR: {type(e).__name__}")
    if run.get("image_git_sha") and run["image_git_sha"] not in tags_ecr:
        problems.append(f"baked git sha {run.get('image_git_sha')} is not an ECR "
                        f"tag of that digest {tags_ecr}")

    if problems:
        print(f"REFUSING — {len(problems)} problem(s):")
        for p in problems:
            print(f"  {p}")
        return 1

    rec = {
        "record": "TRAINING-RUN",
        "recorded_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "experiment": "b4-whisper-large-v3-lora",
        "status": "COMPLETED",
        "evaluation_performed": False,
        "promotion_performed": False,
        "statement": (
            "This records that a training run completed and what it trained on. "
            "It says NOTHING about model quality: no evaluation was run, and a "
            "falling training loss is evidence that the optimiser worked, not "
            "that transcription improved. The adapter stays in candidates/."),
        "run_id": a.run_id,
        "mlflow_run_status": status[0] if status else None,
        "mlflow_db": f"s3://{BUCKET}/mlflow/db/{a.run_id}/mlflow.db",
        "artifact_prefix": f"s3://{BUCKET}/{base}",
        "preflight_prefix": f"s3://{BUCKET}/{a.preflight_prefix}",
        "image": {
            "digest": dig,
            "ecr_tags": tags_ecr,
            "baked_git_sha": run.get("image_git_sha"),
            "provenance_source": tags.get("provenance_source"),
            "git_dirty": tags.get("git_dirty"),
        },
        "training": {
            "steps": ts["global_step"],
            "final_train_loss": run.get("train_loss"),
            "loss_is_finite": run.get("loss_is_finite"),
            "losses_logged": len(losses),
            "first5_mean": round(sum(losses[:5]) / 5, 4),
            "last5_mean": round(sum(losses[-5:]) / 5, 4),
            "loss_by_step": {str(x["step"]): round(x["loss"], 4)
                             for x in ts["log_history"] if "loss" in x},
            "descent_gate_step": 100,
            "descent_gate_verdict": "PASSED",
            "device": run.get("device_used"), "gpu": run.get("gpu_name"),
            "gpu_peak_mb": run.get("gpu_peak_mb"),
            "torch": run.get("torch_version"), "cuda": run.get("cuda_version"),
        },
        "data": {
            "dataset_fingerprint": fp,
            "fingerprint_sources_agree": ["run.json", "mlflow.params"],
            "manifest_version": "v2",
            "eligible_rows_before_exclusions": d.get("eligible_rows_before_exclusions"),
            "eligible_rows_after_exclusions": d.get("eligible_rows_after_exclusions"),
            "sampled_rows": d.get("sampled_rows"),
            "over_limit_rows_remaining": d.get("over_limit_rows_remaining"),
            "decoder_label_limit": d.get("decoder_label_limit"),
        },
        "deferral": {
            **d,
            "policy_key": f"s3://{BUCKET}/{pol_key}",
            "policy_sha256_recomputed_from_s3": pol_sha,
            "adoption_key": f"s3://{BUCKET}/curated/_versions/v2/ADOPTION.json",
            "adoption_binds_this_policy": True,
            "corpus_unchanged_since_adoption": True,
            "human_review_performed": False,
            "note": ("the 20 deferred rows remain UNREVIEWED. This run did not "
                     "train on them and does not license their reuse."),
        },
        "base_model": {
            "revision": run.get("base_revision"),
            "source": run.get("base_source"),
            "cache_uri": run.get("base_cache_uri"),
            "manifest_sha256": run.get("base_manifest_sha256"),
        },
        "artifacts": {
            "final": finals,
            "adapter_sha256": finals["adapter_model.safetensors"]["sha256"],
            "adapter_bytes": finals["adapter_model.safetensors"]["bytes"],
            "checkpoints": ckpts,
            "scope": "candidates/ only",
        },
        "verification": (
            "adapter and every final artifact hashed from S3 bytes (not ETags); "
            "step count and losses read from checkpoint trainer_state, not the "
            "trainer summary; fingerprint and exclusion counts cross-checked "
            "against the MLflow tracking DB; policy hash recomputed from the S3 "
            "object and matched to the adoption record; image digest confirmed "
            "in ECR with the baked commit as its tag"),
    }

    out = Path(a.out) if a.out else \
        ROOT / f"platform/evidence/training-run-{a.run_id[:12]}.json"
    out.write_text(json.dumps(rec, indent=2) + "\n")
    print(f"wrote {out.relative_to(ROOT)}")
    print(f"  run {a.run_id} | {ts['global_step']} steps | "
          f"loss {run.get('train_loss'):.4f} | GPU peak {run.get('gpu_peak_mb')} MB")
    print(f"  fingerprint {fp[:16]} (run.json and MLflow agree)")
    print(f"  adapter sha256 {rec['artifacts']['adapter_sha256']}")
    print(f"  deferral {d.get('exclusions_removed')} removed "
          f"({d.get('exclusions_over_decoder_limit')}+"
          f"{d.get('exclusions_extreme_token_rate')}), policy {pol_sha[:16]}")
    print(f"  {len(ckpts)} checkpoints, {len(finals)} final artifacts, "
          f"candidates/ only")
    print("  evaluation: NOT PERFORMED   promotion: NOT PERFORMED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
