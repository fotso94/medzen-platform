"""omniASR CTC LoRA trainer (work item C1; consumes T1-T4, runs under B10).

Composition, in gate order — every refusal fires BEFORE torch is imported,
so a misconfigured SageMaker job dies in seconds, not after a GPU spin-up:

  1. configuration is read from environment variables only (B10: plain
     Docker, env-var config, no host assumptions; SageMaker passes
     hyperparameters the same way);
  2. the mix is built by train_asr.load_mix — the SAME fail-closed data
     machinery the B4 trainer proved (adoption binding, allowed_use,
     single-version, dedup, exclusions-before-sampling) — with two B5
     additions applied on the eligible pool BEFORE temperature sampling:
     the T3 licence-policy gate and the per-language audio-hour cap from
     the B4 adaptation design (§5: 100h default, kinyarwanda subsampled);
  3. only then does the model phase import torch/fairseq2, load the CTC
     card, wrap it with the T1 LoRA adapter, and train;
  4. checkpoints go to MEDZEN_CHECKPOINT_DIR (/opt/ml/checkpoints — the
     SageMaker managed-spot contract) atomically, resume refuses a
     checkpoint whose run fingerprint differs from this configuration;
  5. the terminal artifact is the T2 merged export: a plain fairseq2
     checkpoint with a signed manifest and NO adapter code at serving.

The LLM variant is refused by name until its own calibration run exists —
training it today would spend on unfalsified cost assumptions.

fairseq2 API contact is confined to _load_model_and_tokenizer and
_batch_loss below; both are exercised in-container (work item C3) because
the engineering host carries no torch by policy.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import signal
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from pipeline.licence_filter import (
    DEFAULT_ALLOWED,
    KNOWN_POLICIES,
    filter_training_rows,
)
from pipeline.train_asr import load_exclusions, load_mix, s3


class TrainerRefusal(RuntimeError):
    pass


CTC_CARD = "medzen_omniASR_CTC_1B_v2"
CTC_TOKENIZER = "medzen_omniASR_tokenizer_written_v2"
CTC_SCOPE_PREFIX = "encoder."  # wav2vec2 attention lives under the encoder;
# the llama_decoder default in wrap_lora belongs to the (refused) LLM variant.
SIGTERM_EXIT = 42  # named: spot reclaim checkpointed and left cleanly
DIVERGED_EXIT = 43  # named: non-finite loss/grad/params — poison NOT persisted

# The frozen base-model identity the evaluation suite live-proved. The
# artifacts live as PART files under the meta-source bundle prefix
# (r4 died on a 403 that was really a wrong-path 404: the b6a root holds
# the whisper CT2 tree, not the Meta checkpoints). Part inventory pinned
# from the meta-source pilot-bundle.json receipt; the assembled file is
# verified against the same identity the eval suite proved.
MODEL_ROOT_PREFIX = ("research/asr-base-model/pilot/"
                     "1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/"
                     "bundles/")
CTC_MODEL_ARTIFACTS = {
    "omniASR-CTC-1B-v2.pt": {
        "sha256": "354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c",
        "parts": 1,
    },
    "omniASR_tokenizer_written_v2.model": {
        "sha256": "8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e",
        "parts": 1,
    },
}


def stage_model_artifacts(cli, destination: Path = Path("/models"),
                          artifacts: dict[str, dict] | None = None) -> dict[str, str]:
    """Assemble the frozen base-model files from their bundle parts,
    verifying each assembled file against the evaluation-suite identity.
    A cached file is reverified, never trusted."""
    from pipeline.train_asr import BUCKET

    artifacts = CTC_MODEL_ARTIFACTS if artifacts is None else artifacts
    destination.mkdir(parents=True, exist_ok=True)
    staged = {}
    for name, spec in artifacts.items():
        expected_sha = spec["sha256"]
        local = destination / name
        if not local.exists():
            tmp = destination / (name + ".tmp")
            with tmp.open("wb") as stream:
                for index in range(spec["parts"]):
                    cli.download_fileobj(
                        BUCKET,
                        f"{MODEL_ROOT_PREFIX}{name}.parts/part-{index:04d}",
                        stream)
            tmp.replace(local)
        digest = hashlib.sha256()
        with local.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1 << 22), b""):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected_sha:
            local.unlink()
            raise TrainerRefusal(
                f"{name} hashes to {actual[:16]}, the evaluated identity is "
                f"{expected_sha[:16]} — refusing to train on drifted weights")
        staged[name] = actual
    return staged


@dataclass(frozen=True)
class TrainerConfig:
    variant: str
    model_card: str
    manifest_version: str
    languages: tuple[str, ...]
    seed: int
    temperature: float
    audio_cap_hours: float
    allowed_policies: frozenset[str]
    max_steps: int
    batch_size: int
    grad_accum: int
    learning_rate: float
    lora_rank: int
    lora_alpha: float
    lora_dropout: float
    train_mode: str
    warmup_steps: int
    lr_schedule: str
    checkpoint_dir: Path
    output_dir: Path
    checkpoint_every_steps: int
    exclusions_ref: str | None
    expect_excluded: int | None
    adoption_key: str | None

    def fingerprint_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["languages"] = sorted(self.languages)
        payload["allowed_policies"] = sorted(self.allowed_policies)
        payload["checkpoint_dir"] = str(self.checkpoint_dir)
        payload["output_dir"] = str(self.output_dir)
        return payload


def _require(env: dict[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise TrainerRefusal(f"{name} is required and absent — no default-permit")
    return value


def parse_config(env: dict[str, str]) -> TrainerConfig:
    variant = _require(env, "MEDZEN_VARIANT")
    if variant != "ctc":
        raise TrainerRefusal(
            f"variant {variant!r} is not trainable: the LLM variant is refused "
            "until its own calibration run exists (B4 design §5 prices it on "
            "unmeasured assumptions); only 'ctc' is authorized")
    languages = tuple(sorted({
        token.strip() for token in _require(env, "MEDZEN_LANGUAGES").split(",")
        if token.strip()
    }))
    if not languages:
        raise TrainerRefusal("MEDZEN_LANGUAGES named no languages")
    allowed_raw = env.get("MEDZEN_ALLOWED_LICENCE_POLICIES", "").strip()
    if allowed_raw:
        allowed = frozenset(t.strip() for t in allowed_raw.split(",") if t.strip())
        unknown = allowed - KNOWN_POLICIES
        if unknown:
            raise TrainerRefusal(
                f"MEDZEN_ALLOWED_LICENCE_POLICIES names unknown policies "
                f"{sorted(unknown)}; new policies are admitted in code review, "
                "never by configuration")
    else:
        allowed = DEFAULT_ALLOWED

    def _number(name: str, default: str, kind, minimum=None):
        raw = env.get(name, default)
        try:
            value = kind(raw)
        except ValueError as exc:
            raise TrainerRefusal(f"{name}={raw!r} is not a {kind.__name__}") from exc
        # NaN/Infinity pass every < comparison unscathed (Codex review #2:
        # MEDZEN_LR=nan was accepted) — no numeric knob may be non-finite
        if kind is float and not math.isfinite(value):
            raise TrainerRefusal(f"{name}={raw!r} is not a finite number")
        if minimum is not None and value < minimum:
            raise TrainerRefusal(f"{name}={value} is below the minimum {minimum}")
        return value

    seed_raw = _require(env, "MEDZEN_SEED")
    try:
        seed = int(seed_raw)
    except ValueError as exc:
        raise TrainerRefusal(f"MEDZEN_SEED={seed_raw!r} is not an integer") from exc

    # Mode is validated AT PARSE (Codex review #2: an unknown mode used to
    # pass parse and only refuse after mix building and model staging had
    # already spent time and bytes).
    train_mode = env.get("MEDZEN_TRAIN_MODE", "lora")
    if train_mode not in ("lora", "full"):
        raise TrainerRefusal(f"unknown MEDZEN_TRAIN_MODE {train_mode!r}")
    lr_schedule = env.get("MEDZEN_LR_SCHEDULE", "constant").strip() or "constant"
    if lr_schedule not in ("constant", "cosine"):
        raise TrainerRefusal(
            f"unknown MEDZEN_LR_SCHEDULE {lr_schedule!r} (constant|cosine)")

    # Full-mode guards (Codex finding 2026-08-20). The 1e-3 default is a
    # LoRA rate — it is the exact rate behind the wave-1 runaway
    # (B5-DIAG-2026-001) and must never reach a full fine-tune implicitly.
    # And full mode exists BECAUSE per-language isolation ended the shared
    # adapter's interference — a multi-language full job would rebuild the
    # failure the strategy pivot removed.
    if train_mode == "full":
        lr_raw = env.get("MEDZEN_LR", "").strip()
        if not lr_raw:
            raise TrainerRefusal(
                "full mode requires an EXPLICIT MEDZEN_LR — the 1e-3 default "
                "is a LoRA rate and destroyed wave-1 (B5-DIAG-2026-001)")
        # Codex review #4: the #2 bound was boundary-INCLUSIVE and blessed
        # the documented runaway rate. Evidence-backed cap: 1e-4 is the
        # highest rate our own probes proved stable (decisive LoRA run),
        # and the full surface is strictly more sensitive.
        lr_value = _number("MEDZEN_LR", lr_raw, float, None)
        if not (0.0 < lr_value <= 1e-4):
            raise TrainerRefusal(
                f"full mode requires 0 < MEDZEN_LR <= 1e-4, got {lr_raw!r} "
                "(1e-4 is the highest probe-proven stable rate; 1e-3 "
                "destroyed wave-1 on a far smaller surface)")
        if not env.get("MEDZEN_WARMUP_STEPS", "").strip():
            raise TrainerRefusal(
                "full mode requires an EXPLICIT MEDZEN_WARMUP_STEPS — a "
                "bounded schedule is part of the full-FT contract "
                "(Codex review #2)")
        warmup_value = _number("MEDZEN_WARMUP_STEPS", "", int, 1)
        max_steps_value = _number("MEDZEN_MAX_STEPS", "600", int, 1)
        if not (1 <= warmup_value < max_steps_value):
            raise TrainerRefusal(
                f"full mode requires 1 <= warmup < max_steps, got "
                f"warmup={warmup_value} max_steps={max_steps_value} "
                "(Codex review #4: 0 and absurd values passed)")
        if not env.get("MEDZEN_LR_SCHEDULE", "").strip():
            raise TrainerRefusal(
                "full mode requires an EXPLICIT MEDZEN_LR_SCHEDULE "
                "(constant|cosine) — no silent schedule (Codex review #4)")
        if len(languages) != 1 and env.get(
                "MEDZEN_MULTILINGUAL_FULL_ACK") != "ARCH-2026-001":
            raise TrainerRefusal(
                f"full mode trains one language per job unless the run "
                f"explicitly cites the one-model architecture: set "
                f"MEDZEN_MULTILINGUAL_FULL_ACK=ARCH-2026-001 for a "
                f"preservation-aware universal run; got {sorted(languages)}")

    return TrainerConfig(
        variant=variant,
        model_card=env.get("MEDZEN_MODEL_CARD", CTC_CARD),
        manifest_version=_require(env, "MEDZEN_MANIFEST_VERSION"),
        languages=languages,
        seed=seed,
        temperature=_number("MEDZEN_TEMPERATURE", "0.5", float, 0.0),
        audio_cap_hours=_number("MEDZEN_AUDIO_CAP_HOURS", "100", float, 0.001),
        allowed_policies=allowed,
        max_steps=_number("MEDZEN_MAX_STEPS", "600", int, 1),
        batch_size=_number("MEDZEN_BATCH_SIZE", "2", int, 1),
        grad_accum=_number("MEDZEN_GRAD_ACCUM", "8", int, 1),
        learning_rate=_number("MEDZEN_LR", "1e-3", float, 0.0),
        lora_rank=_number("MEDZEN_LORA_RANK", "16", int, 1),
        lora_alpha=_number("MEDZEN_LORA_ALPHA", "32", float, 0.0),
        lora_dropout=_number("MEDZEN_LORA_DROPOUT", "0.0", float, 0.0),
        train_mode=env.get("MEDZEN_TRAIN_MODE", "lora"),
        warmup_steps=_number("MEDZEN_WARMUP_STEPS", "0", int, 0),
        lr_schedule=lr_schedule,
        checkpoint_dir=Path(env.get("MEDZEN_CHECKPOINT_DIR", "/opt/ml/checkpoints")),
        output_dir=Path(env.get("MEDZEN_OUTPUT_DIR", "/opt/ml/model")),
        checkpoint_every_steps=_number("MEDZEN_CHECKPOINT_EVERY", "50", int, 1),
        exclusions_ref=env.get("MEDZEN_EXCLUSIONS_REF", "").strip() or None,
        expect_excluded=(int(env["MEDZEN_EXPECT_EXCLUDED"])
                         if env.get("MEDZEN_EXPECT_EXCLUDED", "").strip() else None),
        adoption_key=env.get("MEDZEN_ADOPTION_KEY", "").strip() or None,
    )


def run_fingerprint(config: TrainerConfig, mix_provenance: dict[str, Any]) -> str:
    """One hash naming the run: config + the exact corpus state it read.

    A resumed checkpoint carrying a different fingerprint is a different
    run wearing the same directory, and resuming it would splice two
    training histories into one artifact.
    """
    body = json.dumps(
        {"config": config.fingerprint_payload(), "mix_provenance": mix_provenance},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(body).hexdigest()


def build_gated_mix(config: TrainerConfig, client=None) -> tuple[list[dict], dict]:
    """The B4-proven mix machinery plus the two B5 pool gates."""
    cli = client if client is not None else s3()
    exclusions = exclusions_sha = exclusions_id = None
    if config.exclusions_ref:
        exclusions, doc, exclusions_sha = load_exclusions(
            config.exclusions_ref, config.expect_excluded, client=cli)
        exclusions_id = doc.get("list_id")

    def licence_gate(rows: list[dict]) -> tuple[list[dict], dict]:
        return filter_training_rows(rows, allowed=config.allowed_policies)

    mix, provenance = load_mix(
        cli,
        temperature=config.temperature,
        seed=config.seed,
        languages=list(config.languages),
        version=config.manifest_version,
        require_use="asr_train",
        exclusions=exclusions,
        exclusions_sha256=exclusions_sha,
        exclusions_id=exclusions_id,
        adoption_key=config.adoption_key,
        pool_gate=licence_gate,
        per_language_audio_cap_s=config.audio_cap_hours * 3600.0,
    )
    return mix, provenance


# --------------------------------------------------------------------------
# Checkpoint bookkeeping (pure logic; torch enters only at save/load time)
# --------------------------------------------------------------------------

LATEST_MARKER = "LATEST.json"


def read_resume_state(checkpoint_dir: Path, fingerprint: str) -> dict[str, Any] | None:
    """Return the confirmed resume marker, or None for a fresh start.

    The marker is written AFTER its checkpoint file (write-then-rename on
    both), so a marker that names a missing or short file is corruption,
    not a race — refuse rather than silently restart from zero and burn
    the budget re-treading steps.
    """
    marker_path = checkpoint_dir / LATEST_MARKER
    if not marker_path.exists():
        return None
    marker = json.loads(marker_path.read_text())
    if marker.get("run_fingerprint") != fingerprint:
        raise TrainerRefusal(
            "checkpoint directory belongs to a different run "
            f"(fingerprint {str(marker.get('run_fingerprint'))[:16]} != "
            f"{fingerprint[:16]}); refusing to splice training histories")
    checkpoint_path = checkpoint_dir / marker["checkpoint"]
    if not checkpoint_path.exists():
        raise TrainerRefusal(
            f"resume marker names {marker['checkpoint']} which is absent — "
            "the checkpoint state is corrupt, not merely stale")
    recorded = marker.get("checkpoint_sha256")
    actual = hashlib.sha256(checkpoint_path.read_bytes()).hexdigest()
    if recorded != actual:
        raise TrainerRefusal(
            f"{marker['checkpoint']} hash {actual[:16]} differs from the "
            f"marker's {str(recorded)[:16]}; refusing a torn checkpoint")
    return marker


def write_checkpoint_marker(checkpoint_dir: Path, *, step: int,
                            checkpoint_name: str, fingerprint: str) -> None:
    checkpoint_path = checkpoint_dir / checkpoint_name
    body = json.dumps({
        "step": step,
        "checkpoint": checkpoint_name,
        "checkpoint_sha256": hashlib.sha256(
            checkpoint_path.read_bytes()).hexdigest(),
        "run_fingerprint": fingerprint,
    }, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    tmp = checkpoint_dir / (LATEST_MARKER + ".tmp")
    tmp.write_bytes(body)
    tmp.replace(checkpoint_dir / LATEST_MARKER)


# --------------------------------------------------------------------------
# Generic training loop — model-agnostic so a CPU-sized stand-in exercises
# the exact step/accumulation/checkpoint/resume arithmetic that will run on
# the GPU. The fairseq2 pieces plug in via batch_loss.
# --------------------------------------------------------------------------

def scheduled_lr(base_lr: float, step: int, warmup_steps: int,
                 max_steps: int, schedule: str = "constant") -> float:
    """Linear warmup, then the DECLARED schedule (Codex review #4: the
    post-warmup shape must be explicit). cosine decays to a 10% floor over
    (warmup, max_steps]. Stateless in step, so a resumed run computes the
    same rate the uninterrupted run would have used."""
    if warmup_steps > 0 and step < warmup_steps:
        return base_lr * (step + 1) / warmup_steps
    if schedule == "cosine":
        span = max(1, max_steps - warmup_steps)
        progress = min(1.0, max(0.0, (step - warmup_steps) / span))
        return base_lr * (0.1 + 0.9 * 0.5 * (1.0 + math.cos(math.pi * progress)))
    return base_lr


def warmup_lr(base_lr: float, step: int, warmup_steps: int) -> float:
    """Constant-schedule convenience wrapper around scheduled_lr."""
    return scheduled_lr(base_lr, step, warmup_steps,
                        max_steps=step + warmup_steps + 1,
                        schedule="constant")


OPTIMIZER_SIDECAR = "optimizer-LATEST.pt"


def save_full_state(path: Path, *, model, optimizer, step: int) -> None:
    """Full-mode checkpoint: model weights + RNG per checkpoint file, and
    the AdamW state in a single rotating sidecar next to it (Codex finding
    2026-08-20: without optimizer moments a resume is not
    trajectory-equivalent; with them in EVERY file a 40k-step run writes
    ~200 GB — the sidecar keeps exactly the resumable state, once).

    Codex review #2 (2026-08-20): the sidecar is HASH-BOUND. It is written
    FIRST, its digest rides inside the model checkpoint, and the marker
    hashes the model checkpoint — so the trust chain is
    marker → model checkpoint → sidecar, and corrupted or swapped
    optimizer moments cannot ride a valid-looking step number."""
    import torch
    sidecar_path = path.parent / OPTIMIZER_SIDECAR
    sidecar_tmp = path.parent / (OPTIMIZER_SIDECAR + ".tmp")
    with sidecar_tmp.open("wb") as stream:
        torch.save({"step": step, "optimizer": optimizer.state_dict()}, stream)
    sidecar_tmp.replace(sidecar_path)
    sidecar_sha = hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
    with path.open("wb") as stream:
        torch.save({"step": step, "model": model.state_dict(),
                    "optimizer_sidecar_sha256": sidecar_sha,
                    "optimizer_sidecar_step": step,
                    "torch_rng": torch.get_rng_state(),
                    "cuda_rng": (torch.cuda.get_rng_state_all()
                                 if torch.cuda.is_available() else None)},
                   stream)


def load_full_state(path: Path, *, model, optimizer) -> int:
    """Resume a full-mode checkpoint. The sidecar's BYTES must hash to the
    digest the (marker-verified) model checkpoint carries — step equality
    alone let deliberately corrupted AdamW moments through (Codex review
    #2 reproduction, now the regression test). Refuse anything torn."""
    import torch
    state = torch.load(path, map_location="cpu", weights_only=False)
    sidecar_path = path.parent / OPTIMIZER_SIDECAR
    if not sidecar_path.exists():
        raise TrainerRefusal(
            f"full-mode resume needs {OPTIMIZER_SIDECAR} beside "
            f"{path.name}; without the optimizer moments the resumed "
            "trajectory is not the one already paid for")
    want_sha = state.get("optimizer_sidecar_sha256")
    if not want_sha:
        raise TrainerRefusal(
            f"{path.name} predates sidecar hash-binding — refusing an "
            "unverifiable optimizer pair")
    actual_sha = hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
    if actual_sha != want_sha:
        raise TrainerRefusal(
            f"optimizer sidecar hash {actual_sha[:16]} does not match the "
            f"{want_sha[:16]} bound into {path.name}; the pair is torn or "
            "corrupted — refusing a non-equivalent resume")
    sidecar = torch.load(sidecar_path, map_location="cpu", weights_only=False)
    if int(sidecar["step"]) != int(state["step"]):
        raise TrainerRefusal(
            f"optimizer sidecar is at step {sidecar['step']}, checkpoint at "
            f"{state['step']}; the checkpoint pair is torn — refusing a "
            "non-equivalent resume")
    model.load_state_dict(state["model"])
    optimizer.load_state_dict(sidecar["optimizer"])
    torch.set_rng_state(state["torch_rng"])
    if state.get("cuda_rng") is not None and torch.cuda.is_available():
        torch.cuda.set_rng_state_all(state["cuda_rng"])
    return int(state["step"])


def check_disk_envelope(config: TrainerConfig, mix: list[dict],
                        *, cache_root: Path,
                        staging_root: Path | None = None,
                        model_bytes_estimate: int = 2_600_000_000,
                        staging_bytes_estimate: int = 6_000_000_000,
                        free_bytes=None, device_of=None) -> dict[str, int]:
    """Measured disk envelope BEFORE any GPU-hour is spent (Codex finding
    2026-08-20: 1,442 h of cached audio plus ~20 full checkpoints cannot
    fit the historical 100 GB volume convention).

    Codex review #2: needs are GROUPED BY FILESYSTEM and summed — checking
    paths separately approved a 100 GB shared volume for a 110.8 GB total
    (38.0 audio + 72.8 checkpoints on the 300 h/12k-step shape). Model
    staging counts too, and main() calls this BEFORE staging or GPU load.

    Audio cache: 16 kHz mono PCM_16 wav = 32,000 B/s of duration. The cache
    only ever holds rows the sampler actually DRAWS: a run of max_steps at
    effective batch B×A can touch at most max_steps*B*A unique rows, so the
    worst case is the top-K longest rows, K = min(len(mix), draws) — NOT
    the whole mix (ml.g6.xlarge ground truth 2026-08-20: storage is a fixed
    250 GB NVMe; the whole-mix bound wrongly refused runs that fit).
    Checkpoint zone: one full-model file per checkpoint interval + the
    optimizer sidecar (~3x model for AdamW moments) + the export."""
    import shutil

    draws = config.max_steps * config.batch_size * config.grad_accum
    k = min(len(mix), draws)
    top_k_seconds = sum(sorted((r["duration_s"] for r in mix),
                               reverse=True)[:k])
    audio_need = int(top_k_seconds * 32_000 * 1.10)
    n_checkpoints = max(1, config.max_steps // config.checkpoint_every_steps)
    if config.train_mode == "full":
        ckpt_need = int((n_checkpoints + 1) * model_bytes_estimate
                        + 3 * model_bytes_estimate)
    else:
        ckpt_need = int(n_checkpoints * 200_000_000 + model_bytes_estimate)
    measure = free_bytes or (lambda p: shutil.disk_usage(p).free)

    def existing_ancestor(root: Path) -> Path:
        probe = root
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        return probe

    dev = device_of or (lambda p: os.stat(p).st_dev)
    needs = [(cache_root, audio_need, "audio cache"),
             (config.checkpoint_dir, ckpt_need, "checkpoints"),
             (staging_root or cache_root, staging_bytes_estimate,
              "model staging")]
    grouped: dict[object, dict] = {}
    for root, need, label in needs:
        probe = existing_ancestor(Path(root))
        bucket = grouped.setdefault(dev(probe),
                                    {"probe": probe, "need": 0, "labels": []})
        bucket["need"] += need
        bucket["labels"].append(f"{label} ~{need / 1e9:.0f} GB")
    for bucket in grouped.values():
        free = measure(bucket["probe"])
        if free < bucket["need"]:
            raise TrainerRefusal(
                f"disk envelope: filesystem of {bucket['probe']} needs "
                f"~{bucket['need'] / 1e9:.0f} GB total "
                f"({' + '.join(bucket['labels'])}), only {free / 1e9:.0f} GB "
                "free — provision the volume before spending GPU-hours")
    return {"audio_cache_bytes": audio_need, "checkpoint_bytes": ckpt_need,
            "staging_bytes": staging_bytes_estimate}


def run_training_loop(
    *,
    model,
    optimizer,
    batches: Callable[[int], Any],
    batch_loss: Callable[[Any, Any], Any],
    config: TrainerConfig,
    fingerprint: str,
    save_state: Callable[[Path, int], None],
    load_state: Callable[[Path], int],
    stop_flag: dict[str, bool] | None = None,
) -> dict[str, Any]:
    import torch

    stop_flag = stop_flag if stop_flag is not None else {"stop": False}
    config.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    marker = read_resume_state(config.checkpoint_dir, fingerprint)
    start_step = 0
    if marker is not None:
        start_step = load_state(config.checkpoint_dir / marker["checkpoint"])
        if start_step != marker["step"]:
            raise TrainerRefusal(
                f"checkpoint reports step {start_step}, marker says "
                f"{marker['step']}; refusing an inconsistent resume")

    def checkpoint(step: int) -> None:
        name = f"step-{step:07d}.pt"
        tmp = config.checkpoint_dir / (name + ".tmp")
        save_state(tmp, step)
        tmp.replace(config.checkpoint_dir / name)
        write_checkpoint_marker(config.checkpoint_dir, step=step,
                                checkpoint_name=name, fingerprint=fingerprint)

    losses: list[float] = []
    step = start_step
    while step < config.max_steps:
        if stop_flag["stop"]:
            checkpoint(step)
            return {"status": "INTERRUPTED_CHECKPOINTED", "step": step,
                    "resumed_from": start_step, "losses": losses}
        optimizer.zero_grad(set_to_none=True)
        accumulated = 0.0
        for micro in range(config.grad_accum):
            loss = batch_loss(model, batches(step * config.grad_accum + micro))
            (loss / config.grad_accum).backward()
            accumulated += float(loss.detach())
        step_loss = accumulated / config.grad_accum
        # Codex review #4 (reproduced): a NaN loss used to sail through to
        # COMPLETED with non-finite parameters. Fail closed BEFORE the
        # optimizer step, and never persist a poisoned state.
        if not math.isfinite(step_loss):
            return {"status": "TRAINING_DIVERGED_NONFINITE", "step": step,
                    "resumed_from": start_step, "losses": losses,
                    "nonfinite": "loss"}
        total_norm = torch.nn.utils.clip_grad_norm_(
            [p for p in model.parameters() if p.requires_grad], 1.0)
        if not bool(torch.isfinite(total_norm)):
            return {"status": "TRAINING_DIVERGED_NONFINITE", "step": step,
                    "resumed_from": start_step, "losses": losses,
                    "nonfinite": "grad_norm"}
        # stateless declared schedule: recomputed from step, so resume
        # matches the uninterrupted trajectory exactly
        rate = scheduled_lr(config.learning_rate, step, config.warmup_steps,
                            config.max_steps, config.lr_schedule)
        for group in optimizer.param_groups:
            group["lr"] = rate
        optimizer.step()
        step += 1
        losses.append(step_loss)
        if step % config.checkpoint_every_steps == 0 or step == config.max_steps:
            for parameter in model.parameters():
                if not bool(torch.isfinite(parameter).all()):
                    return {"status": "TRAINING_DIVERGED_NONFINITE",
                            "step": step, "resumed_from": start_step,
                            "losses": losses, "nonfinite": "parameters"}
            checkpoint(step)
            recent = losses[-config.checkpoint_every_steps:]
            print(json.dumps({"status": "TRAIN_PROGRESS", "step": step,
                              "lr": rate,
                              "mean_loss": sum(recent) / max(1, len(recent)),
                              "finite": True}, sort_keys=True), flush=True)
    return {"status": "COMPLETED", "step": step,
            "resumed_from": start_step, "losses": losses}


# --------------------------------------------------------------------------
# fairseq2 contact surface — small on purpose; verified in-container (C3)
# --------------------------------------------------------------------------

def _load_model_and_tokenizer(config: TrainerConfig):
    """Exactly the loading calls the pinned omnilingual-asr pipeline makes
    (pipeline.py at 145a12a6): load_model from fairseq2.models.hub with the
    card NAME, load_tokenizer resolving the tokenizer through the same card.
    Verified against fairseq2 v0.6.0 source 2026-08-17."""
    import torch
    from fairseq2.data.tokenizers.hub import load_tokenizer
    from fairseq2.models.hub import load_model

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = load_model(config.model_card, device=device, dtype=torch.bfloat16)
    tokenizer = load_tokenizer(config.model_card)
    return model, tokenizer, device


def _batch_loss(model, batch):
    """Wav2Vec2AsrModel.forward(seqs, seqs_layout, targets, targets_layout)
    returns the sum-reduced CTC loss directly (fairseq2 v0.6.0 model.py);
    normalize by batch size so the learning rate does not scale with it."""
    loss = model(batch["seqs"], batch["seqs_layout"],
                 targets=batch["targets"],
                 targets_layout=batch["targets_layout"])
    return loss / batch["seqs"].shape[0]


def main() -> int:
    from pipeline.omniasr_export import export_merged_checkpoint
    from pipeline.omniasr_lora import lora_state_dict, wrap_lora

    config = parse_config(dict(os.environ))
    mix, provenance = build_gated_mix(config)
    fingerprint = run_fingerprint(config, provenance)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "training-provenance.json").write_bytes(json.dumps({
        "run_fingerprint": fingerprint,
        "config": config.fingerprint_payload(),
        "mix_rows": len(mix),
        "mix_provenance": provenance,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode() + b"\n")

    # Disk envelope BEFORE staging or GPU load (Codex review #2: the gate
    # used to run after both, spending download bytes and GPU minutes a
    # doomed run could never use)
    cache_root = Path(os.environ.get("MEDZEN_AUDIO_CACHE",
                                     "/tmp/medzen-audio-cache"))
    envelope = check_disk_envelope(config, mix, cache_root=cache_root)
    print(json.dumps({"status": "DISK_ENVELOPE_OK", **envelope},
                     sort_keys=True))

    stage_model_artifacts(s3())

    import torch

    if config.train_mode not in ("lora", "full"):
        raise TrainerRefusal(f"unknown MEDZEN_TRAIN_MODE {config.train_mode!r}")
    # deterministic torch state (Codex finding 2026-08-20): the mix was
    # always seeded; the weights/dropout/sampling RNG was not
    torch.manual_seed(config.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(config.seed)
    model, tokenizer, device = _load_model_and_tokenizer(config)
    if config.train_mode == "lora":
        wrap_audit = wrap_lora(
            model, rank=config.lora_rank, alpha=config.lora_alpha,
            dropout=config.lora_dropout, scope_prefix=CTC_SCOPE_PREFIX)
    else:
        # FULL fine-tune (owner option B, B5-KW-DECISIVE-2026-001): every
        # parameter trains; parse enforces one language per job. Optimizer
        # state lives in a rotating sidecar (save_full_state).
        for parameter in model.parameters():
            parameter.requires_grad_(True)
        total = sum(p.numel() for p in model.parameters())
        wrap_audit = {"mode": "full", "merged_modules": [],
                      "trainable_parameters": total, "total_parameters": total,
                      "trainable_fraction": 1.0}
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=config.learning_rate)

    from pipeline.omniasr_data import make_batch_source
    batches = make_batch_source(
        mix, tokenizer, config, s3(),
        cache_root,
        device=device)

    stop_flag = {"stop": False}
    signal.signal(signal.SIGTERM, lambda *_: stop_flag.__setitem__("stop", True))

    def save_state(path: Path, step: int) -> None:
        if config.train_mode == "full":
            save_full_state(path, model=model, optimizer=optimizer, step=step)
            return
        with path.open("wb") as stream:
            torch.save({"step": step, "lora": lora_state_dict(model),
                        "optimizer": optimizer.state_dict(),
                        "torch_rng": torch.get_rng_state(),
                        # Without the device RNG, a resumed run with
                        # lora_dropout > 0 silently diverges from the
                        # uninterrupted trajectory on GPU.
                        "cuda_rng": (torch.cuda.get_rng_state_all()
                                     if torch.cuda.is_available() else None)},
                       stream)

    def load_state(path: Path) -> int:
        if config.train_mode == "full":
            return load_full_state(path, model=model, optimizer=optimizer)
        state = torch.load(path, map_location="cpu", weights_only=False)
        model.load_state_dict(state["lora"], strict=False)
        optimizer.load_state_dict(state["optimizer"])
        torch.set_rng_state(state["torch_rng"])
        if state.get("cuda_rng") is not None and torch.cuda.is_available():
            torch.cuda.set_rng_state_all(state["cuda_rng"])
        return int(state["step"])

    outcome = run_training_loop(
        model=model, optimizer=optimizer, batches=batches,
        batch_loss=_batch_loss, config=config,
        fingerprint=fingerprint, save_state=save_state,
        load_state=load_state, stop_flag=stop_flag)
    if outcome["status"] == "INTERRUPTED_CHECKPOINTED":
        return SIGTERM_EXIT
    if outcome["status"] == "TRAINING_DIVERGED_NONFINITE":
        print(json.dumps({"status": "TRAINING_DIVERGED_NONFINITE",
                          "step": outcome["step"],
                          "nonfinite": outcome["nonfinite"]}, sort_keys=True))
        return DIVERGED_EXIT

    # mode-aware identity (Codex finding 2026-08-20: full-mode audits have
    # no rank/alpha, and the old unconditional read was a guaranteed
    # KeyError after the paid loop finished)
    if config.train_mode == "full":
        audit_extract = {k: wrap_audit[k] for k in
                         ("mode", "trainable_parameters", "total_parameters")}
    else:
        audit_extract = {k: wrap_audit[k] for k in
                         ("rank", "alpha", "trainable_parameters")}
    export = export_merged_checkpoint(
        model,
        output_dir=config.output_dir / "export",
        base_model_card=config.model_card,
        tokenizer_reference=CTC_TOKENIZER,
        decode_config={"strategy": "ctc_greedy"},
        gate_report_reference=None,
        train_mode=config.train_mode,
        training_run_identity={
            "run_fingerprint": fingerprint,
            "steps": outcome["step"],
            "wrap_audit": audit_extract,
        })
    print(json.dumps({"status": "TRAINING_COMPLETE", "export": export,
                      "steps": outcome["step"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
