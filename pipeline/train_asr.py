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
import hashlib
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
             require_use: str = "asr_train",
             exclusions: dict[str, dict] | None = None,
             exclusions_sha256: str | None = None,
             exclusions_id: str | None = None,
             adoption_key: str | None = None,
             pool_gate=None,
             per_language_audio_cap_s: float | None = None) -> tuple[list[dict], dict]:
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

    Exclusions are applied to the ELIGIBLE POOL, before per-language counts are
    taken and before temperature sampling. Removing rows afterwards is wrong in
    two ways: the excluded rows have already influenced the sampling weights, and
    because sampling draws with replacement when a target exceeds the pool, one
    excluded row can appear in the mix any number of times -- so "remove 20 rows"
    silently becomes "remove some number of mix entries". Filtering first makes
    the count exact and the weights honest.

    Returns (mix, provenance) where provenance records the exact manifest
    versions and hashes the mix was built from, so a run can state what it read.

    p_i proportional to n_i**temperature. At 0.5 a corpus ten times larger is
    sampled ~3x more, not 10x -- otherwise the biggest language dominates and
    the smallest ones contribute nothing.
    """
    per_lang: dict[str, list[dict]] = {}
    sources: dict[str, dict] = {}
    rejected = {"wrong_split": 0, "not_permitted": 0}
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    declared_excl_keys = set(exclusions or {})
    selected_languages = set(languages or ())
    malformed_scope_rows = [
        sha for sha, row in (exclusions or {}).items()
        if selected_languages and not row.get("language")
    ]
    if malformed_scope_rows:
        raise SystemExit(
            "REFUSING: a language-scoped run cannot determine whether "
            f"{len(malformed_scope_rows)} policy row(s) apply because their "
            "language is absent")
    out_of_scope_excl_keys = {
        sha for sha, row in (exclusions or {}).items()
        if selected_languages and row.get("language") not in selected_languages
    }
    # The policy remains intact and fully adopted.  A language-scoped run only
    # requires the policy rows that can occur in its selected manifests.  Rows
    # belonging to deliberately deferred languages are recorded below as
    # out-of-scope; requiring them to be found would make it impossible to
    # exclude a whole language without weakening or rewriting the policy.
    excl_keys = declared_excl_keys - out_of_scope_excl_keys
    excl_hits: dict[str, int] = {}

    # A version is only usable if its migration COMPLETED. The completion record
    # is written last, so its absence means an interrupted migration -- and a
    # half-written version must never be mistaken for a finished one.
    # COMPLETE.json proves ONE thing: the migration finished writing. It is
    # written last, so its absence means an interrupted migration. It carries no
    # opinion about whether anyone approved training -- a record cannot
    # meaningfully attest to a decision taken after it was written.
    comp_key = f"curated/_versions/{version}/COMPLETE.json"
    try:
        comp_raw = cli.get_object(Bucket=BUCKET, Key=comp_key)["Body"].read()
        comp = json.loads(comp_raw)
    except Exception as e:
        raise SystemExit(
            f"REFUSING: no completion record at s3://{BUCKET}/{comp_key} "
            f"({type(e).__name__}). A migration that did not finish must not be "
            "trained from.")

    # ADOPTION.json is the approval, and it is a separate immutable object.
    # It binds the RAW BYTES of the completion record it approved. Hashing a
    # re-serialisation (json.dumps of the parsed dict) would prove nothing: key
    # order, separators and float formatting all change the bytes, so the digest
    # would describe Python's output rather than the object in the bucket.
    adopt_key = (
        adoption_key
        or f"curated/_versions/{version}/ADOPTION.json")
    expected_prefix = f"curated/_versions/{version}/"
    if not adopt_key.startswith(expected_prefix) or "/" in adopt_key[len(expected_prefix):]:
        raise SystemExit(
            f"REFUSING: adoption key {adopt_key!r} is outside "
            f"{expected_prefix} or is not a direct child")
    try:
        adopt = json.loads(cli.get_object(Bucket=BUCKET, Key=adopt_key)["Body"].read())
    except Exception as e:
        raise SystemExit(
            f"REFUSING: no adoption record at s3://{BUCKET}/{adopt_key} "
            f"({type(e).__name__}). A completed migration is not an approved one.")
    if adopt.get("status") != "approved":
        raise SystemExit(f"REFUSING: {adopt_key} status is {adopt.get('status')!r}, "
                         "not 'approved'")
    comp_raw_sha = hashlib.sha256(comp_raw).hexdigest()
    if adopt.get("complete_raw_sha256") != comp_raw_sha:
        raise SystemExit(
            f"REFUSING: {adopt_key} approved COMPLETE raw sha256 "
            f"{str(adopt.get('complete_raw_sha256'))[:16]}, bucket now holds "
            f"{comp_raw_sha[:16]}; the version changed after it was adopted.")

    # If the adoption was granted on the basis of a deferral policy, that exact
    # policy must be the one in force. Otherwise an approval obtained for one
    # set of deferred rows would silently license a different set.
    want_policy = adopt.get("deferral_policy_sha256")
    if want_policy and want_policy != exclusions_sha256:
        raise SystemExit(
            f"REFUSING: {adopt_key} was adopted with deferral policy "
            f"{want_policy[:16]}, this run supplies "
            f"{(exclusions_sha256 or 'none')[:16]}. Adoption does not transfer "
            "between policies.")
    if exclusions and not want_policy:
        raise SystemExit(
            f"REFUSING: exclusions supplied but {adopt_key} records no deferral "
            "policy; the adoption did not contemplate removing rows.")

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
            # Eligible, but deferred: drop it BEFORE it can influence counts.
            if sha in excl_keys:
                excl_hits[sha] = excl_hits.get(sha, 0) + 1
                continue
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

    # Every deferred row must have been found exactly once in the eligible pool.
    # Absent means the policy describes a corpus this is not; twice means the
    # row is duplicated and the removal count would be ambiguous.
    if excl_keys:
        missing = sorted(excl_keys - set(excl_hits))
        if missing:
            raise SystemExit(
                f"REFUSING: {len(missing)} deferred row(s) are not in the eligible "
                f"pool at version {version!r}, e.g. {[m[:16] for m in missing[:3]]}. "
                "The policy does not describe this corpus.")
        repeated = {k: n for k, n in excl_hits.items() if n != 1}
        if repeated:
            raise SystemExit(
                f"REFUSING: deferred row(s) appear more than once: "
                f"{[(k[:16], n) for k, n in list(repeated.items())[:3]]}")
    if not per_lang:
        raise SystemExit(f"REFUSING: no rows permit {require_use!r} at version "
                         f"{version!r}; nothing to train on")

    # Optional pool-level gates (omniASR/B5 trainer). Both operate on the
    # ELIGIBLE POOL, before per-language counts and temperature sampling, for
    # the same reason exclusions do: a row removed after weights are taken has
    # already distorted the schedule, and with replacement-sampling its removal
    # count is not even well defined. Both default off; the B4 path is
    # byte-identical when they are absent.
    gate_report = None
    if pool_gate is not None:
        flat = [row for rows in per_lang.values() for row in rows]
        gated, gate_report = pool_gate(flat)
        per_lang = {}
        for r in gated:
            per_lang.setdefault(r["_lang"], []).append(r)
        if not per_lang:
            raise SystemExit(
                "REFUSING: the pool gate removed every eligible row; "
                "nothing to train on")
    cap_report = None
    if per_language_audio_cap_s is not None:
        if per_language_audio_cap_s <= 0:
            raise SystemExit("REFUSING: a non-positive audio cap is not a cap")
        cap_report = {}
        for lang, rows in sorted(per_lang.items()):
            total_s = sum(float(r["duration_s"]) for r in rows)
            if total_s <= per_language_audio_cap_s:
                continue
            # Deterministic per-language subsample: order by (seed, lang) so
            # the kept set is a pure function of the run identity, then keep
            # rows until the cap is reached. At least one row always remains.
            pool = sorted(rows, key=lambda r: hashlib.sha256(
                f"{seed}/{lang}/{r['audio_checksum_sha256']}".encode()).hexdigest())
            kept, kept_s = [], 0.0
            for r in pool:
                if kept and kept_s + float(r["duration_s"]) > per_language_audio_cap_s:
                    continue
                kept.append(r)
                kept_s += float(r["duration_s"])
            per_lang[lang] = kept
            cap_report[lang] = {
                "hours_before": round(total_s / 3600, 3),
                "hours_after": round(kept_s / 3600, 3),
                "rows_before": len(rows),
                "rows_after": len(kept),
            }

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

    by_trigger: dict[str, int] = {}
    for k in excl_hits:
        t = (exclusions or {}).get(k, {}).get("trigger", "unspecified")
        by_trigger[t] = by_trigger.get(t, 0) + 1

    provenance = {
        "manifest_version": version,
        "require_allowed_use": require_use,
        "manifests": sources,
        "eligible_rows_before_exclusions": target + len(excl_hits),
        "eligible_rows": target,
        "rejected": rejected,
        "other_versions_present_but_unused": other_versions,
        "complete_key": comp_key,
        "complete_raw_sha256": comp_raw_sha,
        "adoption_key": adopt_key,
        "exclusions": {
            "list_id": exclusions_id,
            "policy_sha256": exclusions_sha256,
            "policy_declared": len(declared_excl_keys),
            "declared": len(excl_keys),
            "out_of_scope_declared": len(out_of_scope_excl_keys),
            "removed_from_eligible_pool": len(excl_hits),
            "by_trigger": by_trigger,
            "applied": "before temperature sampling",
        },
    }
    if gate_report is not None:
        provenance["pool_gate"] = dict(gate_report,
                                       applied="before temperature sampling")
    if cap_report is not None:
        provenance["per_language_audio_cap"] = {
            "cap_seconds": per_language_audio_cap_s,
            "capped_languages": cap_report,
            "applied": "before temperature sampling",
        }
    print(f"  manifests   {len(sources)} at version {version}; "
          f"eligible rows {target}; rejected "
          f"{rejected['not_permitted']} not permitted, "
          f"{rejected['wrong_split']} wrong split")
    if excl_keys:
        print(f"  deferred    {len(excl_hits)} row(s) removed from the eligible "
              f"pool BEFORE sampling ({by_trigger}); "
              f"{target + len(excl_hits)} -> {target}")
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


def collate(processor, decoder_start_token_id: int):
    """Batch features and labels, stripping exactly one start-of-transcript.

    The previous version compared `labels[:, 0]` against
    `tokenizer.bos_token_id`. In Whisper `bos_token` is <|endoftext|> (50257)
    while labels begin with <|startoftranscript|> (50258), so the condition was
    always false and NOTHING was ever stripped. HuggingFace then built
    decoder_input_ids = shift_tokens_right(labels, decoder_start_token_id),
    prepending a SECOND start-of-transcript and shifting every target one
    position away from where it appears at inference. Run 23868bab trained that
    way: loss fell, generation collapsed.

    So the token is identified the way HuggingFace identifies it -- by
    `model.config.decoder_start_token_id` -- and the batch must AGREE. A row
    that does not begin with it is not a row this collator understands, and
    guessing would reintroduce exactly the silent-mismatch failure above.

    Only that one token is removed. The language, task and no-timestamps tokens
    remain targets: predicting them is standard Whisper fine-tuning, not an
    error. The trailing <|endoftext|> also remains -- it is what teaches the
    model to stop, and its absence is what let generation run to the cap.
    """
    import torch

    def fn(batch):
        feats = torch.stack([b["input_features"] for b in batch])
        lab = processor.tokenizer.pad(
            [{"input_ids": b["labels"]} for b in batch], return_tensors="pt")
        labels = lab["input_ids"].masked_fill(lab.attention_mask.ne(1), -100)

        first = labels[:, 0]
        if not bool((first == decoder_start_token_id).all().item()):
            bad = [int(x) for x in first.tolist() if x != decoder_start_token_id]
            raise ValueError(
                f"REFUSING: {len(bad)} label row(s) do not begin with "
                f"decoder_start_token_id={decoder_start_token_id}; saw "
                f"{sorted(set(bad))[:5]}. Labels must start with "
                "<|startoftranscript|> so exactly one can be stripped before "
                "the decoder re-adds it.")
        labels = labels[:, 1:]
        return {"input_features": feats, "labels": labels}
    return fn


def prepare_manual_forward_batch(model, batch: dict, device: str) -> dict:
    """Move a one-off batch to the model's device and feature dtype.

    ``Seq2SeqTrainer`` owns mixed-precision autocast during ``train()``.  The
    fixed-batch L0 probe runs before that loop, so simply moving its float32
    log-mel features to CUDA leaves them incompatible with Whisper checkpoints
    whose first convolution is stored as float16.  Bind the manual probe to
    the actual input layer instead of guessing from the requested AMP mode.
    Labels remain integer tensors.
    """
    # Trainer/Accelerate normally performs this move inside ``train()``.  This
    # probe deliberately runs before ``train()``, so it must establish both
    # sides of the device boundary itself.
    model.to(device)
    prepared = {name: value.to(device) for name, value in batch.items()}
    try:
        base = (model.get_base_model()
                if hasattr(model, "get_base_model") else model)
        expected = base.model.encoder.conv1.weight
    except (AttributeError, TypeError) as exc:
        raise ValueError(
            "REFUSING: cannot resolve Whisper encoder conv1 dtype for the "
            "manual fixed-batch probe") from exc
    features = prepared.get("input_features")
    if features is None or not features.is_floating_point():
        raise ValueError(
            "REFUSING: fixed-batch probe needs floating input_features")
    prepared["input_features"] = features.to(dtype=expected.dtype)
    return prepared


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


def load_exclusions(ref: str, expect: int | None = None, *, client=None
                    ) -> tuple[dict[str, dict], dict, str]:
    """Load an exclusion list. Returns (by_checksum, doc, raw_sha256).

    An exclusion is a recorded decision about a specific row, not a runtime
    convenience. It carries a category so a data defect and a deferred
    model-limit incompatibility are never conflated in the record.

    Two kinds are accepted and they are NOT interchangeable:

    * `human_review`   -- a person listened and classified each row.
    * `policy_deferral` -- nobody listened; rows are set aside for one
      experiment. Such a list may not call any row defective and may not
      exclude anything permanently, because no evidence for either exists.
      Enforced here so a policy record can never be quietly upgraded into a
      finding about the data.
    """
    # Governance verification and training must use the caller's already
    # authenticated S3 client.  Falling back to a new default-session client
    # can silently select different credentials from the campaign session.
    storage = client or s3()
    if ref.startswith("s3://"):
        b, k = s3_uri_parts(ref)
        raw = storage.get_object(Bucket=b, Key=k)["Body"].read()
    else:
        raw = Path(ref).read_bytes()
    doc = json.loads(raw)
    entries = list(doc.get("exclusions") or [])
    inherited = doc.get("inherits_policy")
    if inherited:
        inherited_path = (ROOT / inherited["path"]).resolve()
        try:
            inherited_path.relative_to(ROOT)
        except ValueError:
            raise SystemExit("REFUSING: inherited policy escapes repository")
        inherited_raw = inherited_path.read_bytes()
        if hashlib.sha256(inherited_raw).hexdigest() != inherited.get("sha256"):
            raise SystemExit("REFUSING: inherited policy bytes changed")
        inherited_doc = json.loads(inherited_raw)
        inherited_entries = inherited_doc.get("exclusions") or []
        if (inherited_doc.get("status") != "approved"
                or inherited_doc.get("decision_type") != "policy_deferral"
                or len(inherited_entries) != inherited.get("rows")):
            raise SystemExit("REFUSING: inherited policy is not the approved input")
        entries.extend(inherited_entries)
    holdout = doc.get("holdout_exclusion")
    if holdout:
        body = storage.get_object(
            Bucket=BUCKET, Key=holdout["manifest_key"])["Body"].read()
        if hashlib.sha256(body).hexdigest() != holdout.get("manifest_sha256"):
            raise SystemExit("REFUSING: holdout manifest bytes changed")
        holdout_rows = [json.loads(line) for line in body.splitlines()
                        if line.strip()]
        if len(holdout_rows) != holdout.get("rows"):
            raise SystemExit("REFUSING: holdout manifest row count changed")
        for row in holdout_rows:
            if row.get("primary_language") != holdout.get("language"):
                raise SystemExit("REFUSING: holdout contains another language")
            entries.append({
                "audio_checksum_sha256": row["audio_checksum_sha256"],
                "language": holdout["language"],
                "task": holdout["task"],
                "trigger": holdout["trigger"],
                "classification": "held_out_for_post_selection_evaluation",
                "action": "defer_pending_review",
                "defect": False,
                "reason_code": "owner_approved_untouched_holdout",
                "human_reviewed": False,
            })
    out = {e["audio_checksum_sha256"]: e for e in entries}
    kind = doc.get("decision_type", "human_review")
    print(f"  exclusions  {len(out)} row(s) from {doc.get('list_id', ref)} "
          f"({doc.get('status')}, {kind})")
    if doc.get("status") != "approved":
        raise SystemExit(f"REFUSING: exclusion list {doc.get('list_id')} is not approved")
    if len(out) != len(entries):
        raise SystemExit("REFUSING: duplicate checksums in the exclusion list")

    if kind == "policy_deferral":
        if doc.get("human_review_performed") is not False:
            raise SystemExit(
                "REFUSING: a policy_deferral must record "
                "human_review_performed=false; it is the claim that makes the "
                "rest of the record honest")
        bad = [e["audio_checksum_sha256"][:16] for e in doc["exclusions"]
               if e.get("defect") is not False
               or e.get("action") != "defer_pending_review"]
        if bad:
            raise SystemExit(
                f"REFUSING: {len(bad)} entr(y/ies) in a policy_deferral claim a "
                f"defect or a permanent action, e.g. {bad[:3]}. Nobody listened "
                "to these rows, so nothing about their content is known.")
        if doc.get("scope", {}).get("promotion_permitted") is not False:
            raise SystemExit("REFUSING: a policy_deferral must forbid promotion")
    elif kind != "human_review":
        raise SystemExit(f"REFUSING: unknown decision_type {kind!r}")

    if expect is not None and len(out) != expect:
        raise SystemExit(f"REFUSING: expected exactly {expect} excluded row(s), "
                         f"the list declares {len(out)}")
    return out, doc, hashlib.sha256(raw).hexdigest()


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


def make_stop_at_step(stop_at_step: int):
    """Pause a final run immediately after its durable checkpoint is saved.

    ``max_steps`` remains the final 600-step horizon on every resumed segment,
    so the optimizer and scheduler state are restored against one unchanged
    schedule.  The stage runner evaluates the saved checkpoint before it is
    allowed to resume toward the next boundary.
    """
    from transformers import TrainerCallback

    class StopAtStep(TrainerCallback):
        def on_save(self, args, state, control, **kw):
            if state.global_step >= stop_at_step:
                control.should_training_stop = True
                print(
                    f"  interleaved boundary checkpoint-{state.global_step}: "
                    "training paused for evaluation", flush=True)
            return control

    return StopAtStep()


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
    ap.add_argument("--expect-excluded", type=int, default=None,
                    help="require the policy document to contain exactly this "
                         "many rows")
    ap.add_argument(
        "--expect-applied-exclusions", type=int, default=None,
        help="require exactly this many policy rows to apply to the selected "
             "language scope and be removed from its eligible pool")
    ap.add_argument("--adoption-key", default=None,
                    help="immutable adoption object for this experiment; "
                         "defaults to ADOPTION.json for legacy runs")
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
    ap.add_argument("--stop-at-step", type=int, default=None,
                    help="pause immediately after saving this checkpoint; used "
                         "by the final stage for interleaved evaluation")
    ap.add_argument("--fixed-batch", action="store_true",
                    help="repeat one deterministic batch for the bounded "
                         "preflight only; never use for a candidate run")
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
    if a.stop_at_step is not None:
        if not (0 < a.stop_at_step <= a.max_steps):
            raise SystemExit("REFUSING: --stop-at-step must be in "
                             f"1..{a.max_steps}")
        if a.stop_at_step % a.save_steps:
            raise SystemExit(
                "REFUSING: --stop-at-step must be a save boundary; otherwise "
                "the run could pause without a resumable checkpoint")
    if a.fixed_batch and a.max_steps > 200:
        raise SystemExit(
            "REFUSING: --fixed-batch is the bounded preflight and may not "
            "exceed 200 steps")

    cli = s3()
    print(f"base model  {a.base_model}")
    # Loaded BEFORE the mix: the exclusion set is an input to sampling, not a
    # filter applied to its output.
    excluded, excl_doc, excl_sha = ({}, {}, None)
    if a.exclusions:
        excluded, excl_doc, excl_sha = load_exclusions(
            a.exclusions, expect=a.expect_excluded, client=cli)
    elif a.expect_excluded or a.expect_applied_exclusions:
        raise SystemExit(
            "REFUSING: an expected exclusion count was given without "
            "--exclusions")

    print("building mix...")
    mix, mix_provenance = load_mix(cli, a.temperature, a.seed, a.languages,
                                   version=a.manifest_version,
                                   require_use=a.require_allowed_use,
                                   exclusions=excluded,
                                   exclusions_sha256=excl_sha,
                                   exclusions_id=excl_doc.get("list_id"),
                                   adoption_key=a.adoption_key)
    removed = mix_provenance["exclusions"]["removed_from_eligible_pool"]
    expected_applied = (
        a.expect_applied_exclusions
        if a.expect_applied_exclusions is not None else a.expect_excluded)
    if expected_applied is not None and removed != expected_applied:
        raise SystemExit(f"REFUSING: expected exactly {expected_applied} row(s) "
                         f"removed from the eligible pool, removed {removed}")
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
    from pipeline.generation import config_fingerprint
    from pipeline.label_length import decoder_start_id, label_lengths

    GEN_FINGERPRINT = config_fingerprint()
    print(f"  generation  config fingerprint {GEN_FINGERPRINT[:16]} "
          "(shared by training, smoke and evaluation)")

    # One definition, cross-checked between tokenizer and model. If these ever
    # disagree the run stops here rather than training on a shifted objective.
    sot_id = decoder_start_id(processor.tokenizer, model.config)
    print(f"  decoder start token id {sot_id} "
          f"(tokenizer and model.config agree)")

    max_labels = model.config.max_target_positions
    over = []
    for rec in mix:
        _raw, eff = label_lengths(processor.tokenizer, rec["text_normalized"],
                                  rec["_lang"], model.config)
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

    # Nothing is dropped here. Exclusions were applied to the eligible pool, so
    # the mix is already final and the fingerprint printed above describes the
    # data that will actually be trained on. This block only VERIFIES that no
    # over-limit row survived -- a non-empty `over` at this point would mean the
    # policy and the tokenizer disagree about which rows exceed the limit.
    survivors = [r for r in mix if r["audio_checksum_sha256"] in excluded]
    if survivors:
        raise SystemExit(f"REFUSING: {len(survivors)} excluded row(s) are still in "
                         "the mix after pool filtering")
    if over:
        raise SystemExit(f"REFUSING: {len(over)} row(s) exceed the {max_labels}-token "
                         "decoder limit after exclusions were applied")
    print(f"  label check {len(mix)} rows, 0 over the {max_labels}-token limit")

    # Flat scalars, not a nested blob. manifest_provenance is JSON-encoded into a
    # single MLflow param and long values can be truncated at the server; the
    # facts a reader most needs -- what was removed, under which policy, and
    # whether anything over the limit survived -- must each be their own param.
    xp = mix_provenance["exclusions"]
    deferral_evidence = {
        "exclusions_list_id": xp["list_id"],
        "exclusions_decision_type": excl_doc.get("decision_type"),
        "exclusions_policy_sha256": xp["policy_sha256"],
        "exclusions_human_review_performed": excl_doc.get("human_review_performed"),
        "exclusions_policy_declared": xp["policy_declared"],
        "exclusions_applicable_to_scope": xp["declared"],
        "exclusions_out_of_scope": xp["out_of_scope_declared"],
        "exclusions_declared": xp["declared"],
        "exclusions_removed": xp["removed_from_eligible_pool"],
        "exclusions_over_decoder_limit":
            xp["by_trigger"].get("over_decoder_limit", 0),
        "exclusions_extreme_token_rate":
            xp["by_trigger"].get("extreme_token_rate_under_limit", 0),
        "exclusions_applied": "before_temperature_sampling",
        "eligible_rows_before_exclusions":
            mix_provenance["eligible_rows_before_exclusions"],
        "eligible_rows_after_exclusions": mix_provenance["eligible_rows"],
        "sampled_rows": len(mix),
        "over_limit_rows_remaining": len(over),
        "decoder_label_limit": max_labels,
        "v2_complete_raw_sha256": mix_provenance["complete_raw_sha256"],
        "adoption_key": mix_provenance["adoption_key"],
        "promotion_permitted": bool(excl_doc.get("scope", {})
                                    .get("promotion_permitted", False)),
        "artifacts_scope": "candidates_only",
    }

    # UNCONDITIONAL. An earlier edit left this inside `if excluded:`, so a run
    # without an exclusion list never cleared the forced decoder tokens -- the
    # model would emit a fixed prefix regardless of the language token set per
    # row, silently training against the wrong targets.
    model.config.forced_decoder_ids = None
    model.config.suppress_tokens = []

    # IMPORTANT: leave task_type unset for Whisper on the pinned PEFT stack.
    # PeftModelForSeq2SeqLM.forward() always calls the base with input_ids=...
    # and inputs_embeds=..., while Whisper's encoder consumes input_features.
    # Supplying TaskType.SEQ_2_SEQ_LM therefore passes both input_ids and
    # input_features through incompatible paths and the first real batch raises
    # "got multiple values for keyword argument input_ids".  The generic
    # PeftModel forwards input_features unchanged; Whisper itself performs the
    # decoder label shift.  A real tiny-Whisper save/reload test guards this
    # boundary.
    peft_cfg = LoraConfig(r=a.rank, lora_alpha=a.rank * 2, lora_dropout=0.05,
                          bias="none", target_modules=["q_proj", "v_proj"],
                          task_type=None)
    model = get_peft_model(model, peft_cfg)
    trainable = sum(p.numel() for p in model.parameters() if p.requires_grad)
    total = sum(p.numel() for p in model.parameters())
    print(f"  LoRA r={a.rank}: {trainable/1e6:.1f}M trainable / {total/1e6:.0f}M "
          f"({100*trainable/total:.2f}%)")

    params = {
        "base_model": a.base_model, "base_revision": rev,
        "lora_rank": a.rank, "lora_target": "q_proj,v_proj",
        "lora_task_type": "WHISPER_GENERIC_INPUT_FEATURES",
        "generation_config_fingerprint": GEN_FINGERPRINT,
        "lr": a.lr, "batch_size": a.batch_size, "grad_accum": a.grad_accum,
        "max_steps": a.max_steps, "temperature_sampling": a.temperature,
        "seed": a.seed, "device": device, "bf16": use_bf16,
        "dataset_fingerprint": fingerprint,
        **deferral_evidence,
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

    training_mix = mix[:1] if a.fixed_batch else mix
    if a.fixed_batch:
        print("  fixed batch preflight: one deterministic row repeated")
    train_cache = Path(os.environ.get(
        "MEDZEN_TRAIN_CACHE", ROOT / ".cache" / "audio"))
    ds = build_dataset(training_mix, cli, processor, train_cache)
    # The descent gate needs 2*GATE_WINDOW logged losses by its step. At the
    # default cadence (max_steps//20 = 30 for a 600-step run) the tenth loss does
    # not exist until step 300, so the gate would evaluate there while claiming
    # step 100. Tighten the cadence so the losses exist when the gate needs them.
    log_every = max(1, a.max_steps // 20)
    if a.fixed_batch:
        log_every = 1
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
    if a.stop_at_step is not None and a.stop_at_step < a.max_steps:
        callbacks.append(make_stop_at_step(a.stop_at_step))
    data_collator = collate(processor, decoder_start_token_id=sot_id)
    fixed_batch_l0 = None
    if a.fixed_batch:
        one = data_collator([ds[0]])
        one = prepare_manual_forward_batch(model, one, device)
        model.eval()
        with torch.no_grad():
            fixed_batch_l0 = float(model(**one).loss.detach())
        model.train()
        print(f"  fixed batch L0={fixed_batch_l0:.6f}")
    trainer = Seq2SeqTrainer(model=model, args=args, train_dataset=ds,
                             data_collator=data_collator,
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
    # NOT the processor that was used for training. Dataset.__getitem__ calls
    # set_prefix_tokens() per row, mutating shared tokenizer state, so the
    # in-memory processor ends the run pinned to whichever language happened to
    # be sampled last -- run 23868bab saved one stuck at language="yo". A
    # multilingual adapter shipped with a monolingual processor would decode
    # every language as that one. Save a clean copy loaded from the pinned base.
    clean = WhisperProcessor.from_pretrained(src.path, **src.kwargs)
    clean.save_pretrained(a.out)
    print(f"  processor   saved clean from {src.path} "
          "(not the prefix-mutated training copy)")
    import math as _math
    loss_history = [
        {k: row[k] for k in ("step", "loss", "grad_norm", "learning_rate")
         if k in row}
        for row in trainer.state.log_history
        if "loss" in row
    ]
    (a.out / "run.json").write_text(json.dumps({
        "mlflow_run_id": run.info.run_id, "dataset_fingerprint": fingerprint,
        "deferral": deferral_evidence,
        "params": params, "train_loss": result.training_loss,
        "loss_is_finite": bool(_math.isfinite(result.training_loss)),
        "steps": result.global_step,
        "max_steps": a.max_steps,
        "stop_at_step": a.stop_at_step,
        "fixed_batch": a.fixed_batch,
        "fixed_batch_l0": fixed_batch_l0,
        "loss_history": loss_history,
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
