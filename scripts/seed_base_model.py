#!/usr/bin/env python3
"""Seed a pinned base checkpoint into s3://medzen-speech/models/base/.

Why the cache exists: the B4 preflight pulled ~3 GB from the HF Hub
unauthenticated on every start. That is rate-limitable, and after a Spot
reclaim it is paid again. Caching it in the KMS-encrypted bucket removes the
Hub from the training path entirely.

Why this is an ADMIN action: the trainer role has s3:GetObject on models/* and
an explicit Deny on writes, so base weights are immutable inputs to training,
in the same way eval sets are. Nothing automated can seed or overwrite them.

The revision is not merely recorded, it is ENFORCED downstream: a REVISION
marker and a sha256 manifest are written alongside the files, and the loader
refuses a cache whose marker does not match the revision it was asked for. A
cache that silently holds different weights than the pin claims is worse than
no cache, in exactly the way a recorded-but-unenforced model revision was.

    python scripts/seed_base_model.py                      # seed if absent
    python scripts/seed_base_model.py --verify             # sizes, upload nothing
    python scripts/seed_base_model.py --verify --checksums # stream + hash every object
    python scripts/seed_base_model.py --force              # re-upload
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

BUCKET = "medzen-speech"
PROFILE = "medzen"
REGION = "eu-central-1"

REPO = "openai/whisper-large-v3"
REVISION = "06f233fe06e710322aca913c1bc4249a0d71fce1"   # must match train_asr.BASE_REVISION

# Only what the trainer loads. The repo also ships pytorch_model.bin (~6.2 GB)
# plus TF and Flax weights; fetching those would triple the transfer for files
# transformers will never open when safetensors are present.
ALLOW = ["config.json", "generation_config.json", "preprocessor_config.json",
         "model.safetensors", "tokenizer.json", "tokenizer_config.json",
         "vocab.json", "merges.txt", "normalizer.json",
         "special_tokens_map.json", "added_tokens.json"]


def prefix() -> str:
    return f"models/base/whisper-large-v3/{REVISION}"


def cli():
    import boto3
    return boto3.Session(profile_name=PROFILE, region_name=REGION).client("s3")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def remote_manifest(c) -> dict | None:
    try:
        return json.loads(c.get_object(Bucket=BUCKET, Key=f"{prefix()}/MANIFEST.json")
                          ["Body"].read())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", action="store_true", help="report state, upload nothing")
    ap.add_argument("--force", action="store_true", help="re-upload even if present")
    ap.add_argument("--checksums", action="store_true",
                    help="stream every object from S3 and verify sha256 (slow, thorough)")
    a = ap.parse_args()

    c = cli()
    print(f"repo     {REPO}")
    print(f"revision {REVISION}")
    print(f"target   s3://{BUCKET}/{prefix()}/\n")

    existing = remote_manifest(c)
    if existing:
        total = sum(v["bytes"] for v in existing["files"].values())
        print(f"cache present: {len(existing['files'])} files, {total/1e9:.2f} GB")
        print(f"  revision marker: {existing.get('revision')}")
        if existing.get("revision") != REVISION:
            print("  MISMATCH — the cached revision is not the pinned one")
            return 1

        # Check the manifest against S3 rather than against itself. Sizes come
        # from HeadObject and are cheap; --checksums additionally streams every
        # object and hashes it, which is the only way to catch a file that is
        # the right length but the wrong bytes.
        problems: list[str] = []
        for rel, meta in sorted(existing["files"].items()):
            try:
                head = c.head_object(Bucket=BUCKET, Key=f"{prefix()}/{rel}")
            except Exception as e:
                problems.append(f"{rel}: MISSING in S3 ({type(e).__name__})")
                continue
            if head["ContentLength"] != meta["bytes"]:
                problems.append(f"{rel}: S3 has {head['ContentLength']} bytes, "
                                f"manifest says {meta['bytes']}")
            elif a.checksums:
                h = hashlib.sha256()
                body = c.get_object(Bucket=BUCKET, Key=f"{prefix()}/{rel}")["Body"]
                for chunk in iter(lambda: body.read(1 << 22), b""):
                    h.update(chunk)
                got = h.hexdigest()
                mark = "ok" if got == meta["sha256"] else "MISMATCH"
                print(f"  {meta['bytes']/1e6:>9.1f} MB  {rel}  {got[:12]}  {mark}")
                if got != meta["sha256"]:
                    problems.append(f"{rel}: sha256 {got[:12]} != {meta['sha256'][:12]}")
        if problems:
            print(f"\n{len(problems)} PROBLEM(S) — cache is NOT usable:")
            for p in problems:
                print(f"  {p}")
            return 1
        print(f"  all {len(existing['files'])} objects present with correct sizes"
              + (" and sha256" if a.checksums else " (sizes only; --checksums to hash)"))

        if not a.force:
            print("\nOK — cache holds the pinned revision and verifies")
            return 0
    elif a.verify:
        print("NOT SEEDED — no MANIFEST.json at the target prefix")
        return 1

    if a.verify:
        return 0

    from huggingface_hub import snapshot_download
    print("downloading (safetensors only; pytorch_model.bin/TF/Flax skipped)...")
    local = Path(snapshot_download(REPO, revision=REVISION, allow_patterns=ALLOW))
    files = sorted(p for p in local.rglob("*") if p.is_file())
    print(f"  {len(files)} files, {sum(p.stat().st_size for p in files)/1e9:.2f} GB\n")

    manifest = {"repo": REPO, "revision": REVISION, "files": {}}
    print("uploading...")
    for p in files:
        rel = p.relative_to(local).as_posix()
        digest = sha256(p)
        manifest["files"][rel] = {"sha256": digest, "bytes": p.stat().st_size}
        c.upload_file(str(p), BUCKET, f"{prefix()}/{rel}")
        print(f"  {p.stat().st_size/1e6:>9.1f} MB  {rel}  {digest[:12]}")

    # MANIFEST last: its presence is the "seeding completed" signal, so a
    # transfer that dies halfway does not look like a usable cache.
    c.put_object(Bucket=BUCKET, Key=f"{prefix()}/MANIFEST.json",
                 Body=(json.dumps(manifest, indent=2) + "\n").encode())
    print(f"\nseeded s3://{BUCKET}/{prefix()}/  ({len(manifest['files'])} files + MANIFEST.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
