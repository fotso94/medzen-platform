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

# Language -> Whisper token. Defined in pipeline/languages.py so the mapping can
# be imported without importing the trainer; re-exported here because it is part
# of this module's long-standing interface.
from pipeline.languages import LANG_TOKEN  # noqa: E402


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
             languages: list[str] | None, version: str = "v1",
             require_use: str = "asr_train") -> tuple[list[dict], dict]:
    """Build the training mix with temperature sampling. FAILS CLOSED.

    Three refusals, each of which was a real hole:

    * allowed_use is enforced. It is required by the manifest schema, was set at
      ingest, and was checked by nothing -- so an ASR run silently trained on
      2,305 TTS-licensed rows. A row must carry `require_use` explicitly; there
      is no default-permit.
    * exactly one manifest version. Mixing versions means a fingerprint that
      describes no single state of the corpus, and a v1 row and its v2
      counterpart differ only in metadata, so a mixed mix double-counts audio.
    * duplicate rows are rejected. The same audio appearing twice silently
      reweights a language relative to the temperature schedule.

    Returns (mix, provenance) where provenance records the exact manifest
    versions and hashes the mix was built from, so a run can state what it read.

    p_i proportional to n_i**temperature. At 0.5 a corpus ten times larger is
    sampled ~3x more, not 10x -- otherwise the biggest language dominates and
    the smallest ones contribute nothing.
    """
    import hashlib

    per_lang: dict[str, list[dict]] = {}
    sources: dict[str, dict] = {}
    rejected = {"wrong_split": 0, "not_permitted": 0}
    seen: dict[str, str] = {}
    duplicates: list[str] = []

    # A version is only usable if its migration COMPLETED. The completion record
    # is written last, so its absence means an interrupted migration -- and a
    # half-written version must never be mistaken for a finished one.
    comp_key = f"curated/_versions/{version}/COMPLETE.json"
    try:
        comp = json.loads(cli.get_object(Bucket=BUCKET, Key=comp_key)["Body"].read())
    except Exception as e:
        raise SystemExit(
            f"REFUSING: no completion record at s3://{BUCKET}/{comp_key} "
            f"({type(e).__name__}). Version {version!r} is not adopted; a migration "
            "that did not finish must not be trained from.")
    if comp.get("adopted") is not True:
        raise SystemExit(
            f"REFUSING: version {version!r} is published but NOT ADOPTED "
            f"(adopted={comp.get('adopted')!r}). Adoption is a reviewed decision, "
            "not a side effect of the files existing.")

    keys = [k for k in list_manifests(cli) if f"/{version}/manifest.jsonl" in k]
    if not keys:
        raise SystemExit(f"REFUSING: no manifests found at version {version!r}")
    other = [k for k in list_manifests(cli) if f"/{version}/manifest.jsonl" not in k]
    other_versions = sorted({k.split("/")[4] for k in other})

    for key in keys:
        _, lang, task, cfg, ver, _ = key.split("/")
        if ver != version:
            raise SystemExit(f"REFUSING: mixed manifest versions ({ver} vs {version})")
        if languages and lang not in languages:
            continue
        body = cli.get_object(Bucket=BUCKET, Key=key)["Body"].read()
        sha = hashlib.sha256(body).hexdigest()
        label = f"{lang}/{task}/{cfg}"
        # Every manifest must match the hash the completion record vouches for.
        declared = (comp.get("manifests") or {}).get(label, {}).get("sha256")
        if declared is None:
            raise SystemExit(f"REFUSING: {label} is not listed in {comp_key}")
        if declared != sha:
            raise SystemExit(
                f"REFUSING: {label} sha256 {sha[:16]} does not match the "
                f"{declared[:16]} recorded in {comp_key}; the manifest changed "
                "after the migration completed.")
        sources[label] = {"key": key, "version": ver, "manifest_sha256": sha}
        for line in body.decode().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r.get("split") != "train":
                rejected["wrong_split"] += 1
                continue
            if require_use not in (r.get("allowed_use") or []):
                rejected["not_permitted"] += 1
                continue
            sha = r["audio_checksum_sha256"]
            if sha in seen:
                duplicates.append(f"{sha[:16]} in {seen[sha]} and {lang}/{task}")
                continue
            seen[sha] = f"{lang}/{task}"
            r["_lang"] = lang
            r["_task"] = task
            per_lang.setdefault(lang, []).append(r)

    if duplicates:
        raise SystemExit(f"REFUSING: {len(duplicates)} duplicate row(s) across "
                         f"corpora, e.g. {duplicates[:3]}")
    if not per_lang:
        raise SystemExit(f"REFUSING: no rows permit {require_use!r} at version "
                         f"{version!r}; nothing to train on")

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

    provenance = {
        "manifest_version": version,
        "require_allowed_use": require_use,
        "manifests": sources,
        "eligible_rows": target,
        "rejected": rejected,
        "other_versions_present_but_unused": other_versions,
    }
    print(f"  manifests   {len(sources)} at version {version}; "
          f"eligible rows {target}; rejected "
          f"{rejected['not_permitted']} not permitted, "
          f"{rejected['wrong_split']} wrong split")
    return mix, provenance


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


def sync_up(local: Path, uri: str, skip_checkpoints: bool = False) -> list[str]:
    """Upload a checkpoint directory. Runs on the SAVE step, not at the end:
    a spot reclaim gives ~2 minutes' notice, so anything only written at the
    end of training is lost.

    skip_checkpoints excludes nested checkpoint-N/ directories. The final
    adapter is saved into the Trainer's own output_dir, so an unfiltered sync
    copies every retained checkpoint inside final/ -- a duplicate of data
    already uploaded under its own prefix, and an ambiguous directory for
    anything loading the adapter. At 600 steps with --save-steps 100 that is
    over a gigabyte of redundant upload.

    Returns the keys written, so the caller can VERIFY they are readable
    afterwards. upload_file returning without raising is not the same as the
    object being there, and a checkpoint nobody confirmed must not be reported
    as a checkpoint somebody can resume from.
    """
    cli = s3()
    bucket, prefix = s3_uri_parts(uri)
    keys: list[str] = []
    for f in local.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(local)
        if skip_checkpoints and any(p.startswith("checkpoint-") for p in rel.parts[:-1]):
            continue
        key = f"{prefix}/{rel}"
        cli.upload_file(str(f), bucket, key)
        keys.append(key)
    return keys


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


class CheckpointStatus:
    """What is actually in S3, as opposed to what was attempted.

    The uploader deliberately does not kill a run over a transient S3 error --
    losing hours of training to one failed PUT would be worse than continuing --
    but "we tried and warned" must not be mistaken for "it is safe in S3". The
    descent gate consults this before telling anyone a run is resumable.
    """

    def __init__(self) -> None:
        self.confirmed: dict[int, int] = {}     # step -> objects verified present
        self.failed: dict[int, str] = {}        # step -> why

    def is_confirmed(self, step: int) -> bool:
        return self.confirmed.get(step, 0) > 0

    def latest_confirmed(self) -> int | None:
        return max(self.confirmed) if self.confirmed else None


def load_exclusions(ref: str) -> dict[str, dict]:
    """Load a reviewed exclusion list: checksum -> {reason, category, decided}.

    An exclusion is a recorded decision about a specific row, not a runtime
    convenience. It carries a category so a data defect and a deferred
    model-limit incompatibility are never conflated in the record.
    """
    if ref.startswith("s3://"):
        b, k = s3_uri_parts(ref)
        raw = s3().get_object(Bucket=b, Key=k)["Body"].read()
    else:
        raw = Path(ref).read_bytes()
    doc = json.loads(raw)
    out = {e["audio_checksum_sha256"]: e for e in doc["exclusions"]}
    print(f"  exclusions  {len(out)} row(s) from {doc.get('list_id', ref)} "
          f"({doc.get('status')})")
    if doc.get("status") != "approved":
        raise SystemExit(f"REFUSING: exclusion list {doc.get('list_id')} is not approved")
    return out


def make_upload_callback(run_prefix: str, status: "CheckpointStatus | None" = None):
    from transformers import TrainerCallback

    class UploadCheckpoint(TrainerCallback):
        def on_save(self, args, state, control, **kw):
            step = state.global_step
            local = Path(args.output_dir) / f"checkpoint-{step}"
            if not local.exists():
                if status is not None:
                    status.failed[step] = "no local checkpoint directory"
                return control
            dest = f"{run_prefix.rstrip('/')}/checkpoint-{step}"
            try:
                keys = sync_up(local, dest)
                # Verify rather than infer: upload_file raising nothing is not
                # the same as the objects being readable afterwards.
                cli = s3()
                bucket, _ = s3_uri_parts(dest)
                missing = []
                for k in keys:
                    try:
                        if cli.head_object(Bucket=bucket, Key=k)["ContentLength"] <= 0:
                            missing.append(k)
                    except Exception:                        # noqa: BLE001
                        missing.append(k)
                if missing:
                    raise RuntimeError(
                        f"{len(missing)}/{len(keys)} objects not readable after upload, "
                        f"e.g. {missing[0]}")
                from pipeline.tracking import push_tracking_db
                # metadata must survive alongside the checkpoint
                push_tracking_db(run_prefix.rstrip("/").split("/")[-1])
                if status is not None:
                    status.confirmed[step] = len(keys)
                print(f"    checkpoint {step} -> {dest} ({len(keys)} objects verified)",
                      flush=True)
            except Exception as e:                           # noqa: BLE001
                if status is not None:
                    status.failed[step] = str(e)
                print(f"    WARNING checkpoint upload FAILED at step {step}: {e}",
                      flush=True)
            return control

    return UploadCheckpoint()


GATE_WINDOW = 5          # losses per comparison window; see make_descent_gate


def make_descent_gate(at_step: int, save_steps: int, max_steps: int,
                      window: int = GATE_WINDOW,
                      status: "CheckpointStatus | None" = None):
    """Abort if the loss is not clearly descending by `at_step`.

    The 14-language mix starts at a much higher loss than a three-language one
    (23.26 vs 3.85 at step 3), which is plausible -- several of these languages
    are ones Whisper handles badly zero-shot -- but plausible is not verified. A
    multi-hour run that never descends costs GPU hours AND leaves a checkpoint
    someone might later mistake for a trained model.

    Two timing subtleties, both found in review after a first version got them
    wrong:

    1. The gate needs 2*window logged losses before it can compare anything. The
       trainer logs every max_steps//20 steps -- 30 for a 600-step run -- so ten
       losses did not exist until step 300, and the gate silently evaluated
       there while claiming step 100. main() now tightens logging_steps so the
       losses exist by `at_step`; this asserts that rather than assuming it.
    2. Trainer logs BEFORE it saves within a step, so raising from on_log at the
       gate step pre-empts the very checkpoint that makes the abort recoverable.
       The verdict is therefore computed in on_log and raised from on_save, once
       the checkpoint for that step has been uploaded. Registration order alone
       does not achieve this: the upload runs in a different callback's on_save,
       which never gets called if on_log has already raised.

    Windows rather than points: single-step losses are noisy enough that
    comparing two individual values would fire or pass at random.
    """
    from transformers import TrainerCallback

    def next_save_at_or_after(step: int) -> int | None:
        if save_steps <= 0:
            return None
        nxt = ((step + save_steps - 1) // save_steps) * save_steps
        return nxt if nxt <= max_steps else None

    class DescentGate(TrainerCallback):
        def __init__(self):
            self.losses: list[float] = []
            self.verdict: tuple[bool, str] | None = None
            self.done = False
            # where the abort can safely happen, if it comes to that
            self.abort_at = next_save_at_or_after(at_step)

        def _judge(self, step: int) -> tuple[bool, str]:
            import math as _m
            first = sum(self.losses[:window]) / window
            last = sum(self.losses[-window:]) / window
            finite = all(_m.isfinite(x) for x in self.losses)
            print(f"\n  descent gate at step {step}: "
                  f"first{window}={first:.4f} last{window}={last:.4f} "
                  f"delta={last - first:+.4f} finite={finite} "
                  f"({len(self.losses)} losses)", flush=True)
            if not finite:
                return False, f"ABORTING at step {step}: a non-finite loss was logged."
            if last >= first:
                return False, (
                    f"ABORTING at step {step}: smoothed loss is not descending "
                    f"({first:.4f} -> {last:.4f}).\n"
                    "  Check the LANG_TOKEN mapping before spending more GPU time: SEVEN of\n"
                    "  fourteen languages train under an approximate Whisper token\n"
                    "  (acholi/luganda/fula/oromo -> sw, akan/ewe/igbo -> yo), and pidgin -> en\n"
                    "  is provisional from B3, so eight of fourteen are not exact matches.\n"
                    "  A wrong mapping presents exactly this way.")
            return True, f"  descent gate PASSED — continuing to {max_steps} steps"

        def on_log(self, args, state, control, logs=None, **kw):
            if self.done or not logs or "loss" not in logs:
                return control
            self.losses.append(float(logs["loss"]))
            if self.verdict is not None:
                return control
            if state.global_step < at_step or len(self.losses) < 2 * window:
                return control
            self.verdict = self._judge(state.global_step)
            # If no checkpoint will ever be written at or after this step, there
            # is nothing to wait for and deferring would let a doomed run finish.
            if not self.verdict[0] and self.abort_at is None:
                self.done = True
                raise SystemExit(self.verdict[1] +
                                 "\n  (no checkpoint step remains; aborting immediately)")
            return control

        def on_save(self, args, state, control, **kw):
            # Runs after the upload callback's on_save for the same step, so the
            # checkpoint has already been uploaded AND verified by the time this
            # sees it -- which is what makes the resumability claim checkable.
            if self.done or self.verdict is None:
                return control
            self.done = True
            ok, msg = self.verdict
            if ok:
                print(msg, flush=True)
                return control
            raise SystemExit(msg + "\n" + self._recovery_note(state.global_step))

        def _recovery_note(self, step: int) -> str:
            """Say what is actually recoverable, never what should have been.

            The uploader warns and continues on a failed upload rather than
            killing a run over a transient S3 error. That is the right default,
            but it means "we tried" must never be reported as "it is in S3".
            """
            if status is None:
                return ("  Resumability UNKNOWN: no checkpoint status was tracked for this run.")
            if status.is_confirmed(step):
                n = status.confirmed[step]
                return (f"  checkpoint-{step} is confirmed in S3 ({n} objects verified "
                        f"readable).\n"
                        f"  Resume with: --resume auto --resume-run <this run id>")
            latest = status.latest_confirmed()
            why = status.failed.get(step, "upload not confirmed")
            if latest is not None:
                return (f"  WARNING: checkpoint-{step} is NOT confirmed in S3 ({why}).\n"
                        f"  The newest confirmed checkpoint is {latest}; resuming from this\n"
                        f"  run recovers to step {latest}, not {step}.")
            return (f"  WARNING: NO checkpoint is confirmed in S3 for this run ({why}).\n"
                    f"  This run is NOT resumable; the {step} steps are lost.")

    return DescentGate()


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true",
                    help="3 steps on a handful of rows, CPU-friendly")
    ap.add_argument("--max-steps", type=int, default=600)
    ap.add_argument("--manifest-version", default="v1",
                    help="exactly one curated manifest version to train from")
    ap.add_argument("--require-allowed-use", default="asr_train",
                    help="a row must declare this in allowed_use to be eligible")
    ap.add_argument("--exclusions", default=None,
                    help="path or s3:// URI of a reviewed exclusion list")
    ap.add_argument("--descent-gate-step", type=int, default=100,
                    help="abort if the smoothed loss is not descending by this step; 0 disables")
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
    mix, mix_provenance = load_mix(cli, a.temperature, a.seed, a.languages,
                                   version=a.manifest_version,
                                   require_use=a.require_allowed_use)
    if a.smoke:
        mix = mix[:6]
    mixinfo = report_mix(mix)
    fingerprint = manifest_fingerprint(mix)   # provisional; recomputed after filtering
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
    # Label length is checked with the SHARED prefix-aware function, so the
    # audit, this guard and the tests cannot disagree. Rows are NOT dropped
    # dynamically: a run must train on a reviewed, explicitly listed set, or
    # refuse. Silently shrinking the corpus at runtime is how two runs quoting
    # the same fingerprint end up having trained on different data.
    from pipeline.label_length import label_lengths

    max_labels = model.config.max_target_positions
    excluded = load_exclusions(a.exclusions) if a.exclusions else {}
    over = []
    for rec in mix:
        _raw, eff = label_lengths(processor.tokenizer, rec["text_normalized"],
                                  rec["_lang"])
        if eff > max_labels:
            over.append((rec, eff))

    unlisted = [(r, n) for r, n in over
                if r["audio_checksum_sha256"] not in excluded]
    if unlisted:
        lines = "\n".join(
            f"    {n:>5} tokens  {r['_lang']:<9} "
            f"sha256={r['audio_checksum_sha256'][:16]}"
            for r, n in sorted(unlisted, key=lambda x: -x[1])[:10])
        raise SystemExit(
            f"REFUSING: {len(unlisted)} row(s) exceed the decoder limit of "
            f"{max_labels} label tokens and are not in a reviewed exclusion list.\n"
            f"{lines}\n"
            "  Dropping them here would silently change the training set. Review "
            "them\n  (scripts/audit_label_lengths.py) and either add them to an "
            "exclusion list\n  with a recorded reason, or re-segment them at "
            "ingest. Do not truncate labels.")

    if excluded:
        before = len(mix)
        mix = [r for r in mix if r["audio_checksum_sha256"] not in excluded]
        print(f"  exclusions  {before - len(mix)} row(s) removed per "
              f"{a.exclusions}")
        mixinfo = report_mix(mix)
        fingerprint = manifest_fingerprint(mix)
        print(f"  dataset fingerprint {fingerprint[:16]} (after exclusions)")

    # UNCONDITIONAL. An earlier edit left this inside `if excluded:`, so a run
    # without an exclusion list never cleared the forced decoder tokens -- the
    # model would emit a fixed prefix regardless of the language token set per
    # row, silently training against the wrong targets.
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
        # exactly which manifests, at which version, with which hashes
        "manifest_provenance": mix_provenance,
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
    # The descent gate needs 2*GATE_WINDOW logged losses by its step. At the
    # default cadence (max_steps//20 = 30 for a 600-step run) the tenth loss does
    # not exist until step 300, so the gate would evaluate there while claiming
    # step 100. Tighten the cadence so the losses exist when the gate needs them.
    log_every = max(1, a.max_steps // 20)
    if a.descent_gate_step and a.descent_gate_step < a.max_steps:
        log_every = min(log_every, max(1, a.descent_gate_step // (2 * GATE_WINDOW)))
    print(f"  logging every {log_every} steps")

    args = Seq2SeqTrainingArguments(
        output_dir=str(a.out), per_device_train_batch_size=a.batch_size,
        gradient_accumulation_steps=a.grad_accum, learning_rate=a.lr,
        max_steps=a.max_steps, warmup_steps=min(50, a.max_steps // 10),
        bf16=use_bf16, gradient_checkpointing=False,
        logging_steps=log_every, save_steps=a.save_steps,
        save_total_limit=3, report_to=[], remove_unused_columns=False,
        dataloader_num_workers=0, seed=a.seed,
    )
    run_prefix = f"s3://{BUCKET}/candidates/asr/{run.info.run_id}"
    ckpt_status = CheckpointStatus()
    callbacks = [make_upload_callback(run_prefix, ckpt_status)] if a.push_s3 else []
    # Ordered after the upload callback so the gate's abort cannot pre-empt the
    # checkpoint upload for the step it fires on.
    if a.descent_gate_step and a.descent_gate_step < a.max_steps:
        callbacks.append(make_descent_gate(a.descent_gate_step, a.save_steps, a.max_steps,
                                           status=ckpt_status))
        print(f"  descent gate at step {a.descent_gate_step} "
              f"(aborts from on_save so the checkpoint survives)")
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
        "manifest_provenance": mix_provenance,
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
