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
        if minimum is not None and value < minimum:
            raise TrainerRefusal(f"{name}={value} is below the minimum {minimum}")
        return value

    seed_raw = _require(env, "MEDZEN_SEED")
    try:
        seed = int(seed_raw)
    except ValueError as exc:
        raise TrainerRefusal(f"MEDZEN_SEED={seed_raw!r} is not an integer") from exc

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
        torch.nn.utils.clip_grad_norm_(
            [p for p in model.parameters() if p.requires_grad], 1.0)
        optimizer.step()
        step += 1
        losses.append(accumulated / config.grad_accum)
        if step % config.checkpoint_every_steps == 0 or step == config.max_steps:
            checkpoint(step)
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

    stage_model_artifacts(s3())

    import torch

    model, tokenizer, device = _load_model_and_tokenizer(config)
    wrap_audit = wrap_lora(
        model, rank=config.lora_rank, alpha=config.lora_alpha,
        dropout=config.lora_dropout, scope_prefix=CTC_SCOPE_PREFIX)
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad],
        lr=config.learning_rate)

    from pipeline.omniasr_data import make_batch_source
    batches = make_batch_source(
        mix, tokenizer, config, s3(),
        Path(os.environ.get("MEDZEN_AUDIO_CACHE", "/tmp/medzen-audio-cache")),
        device=device)

    stop_flag = {"stop": False}
    signal.signal(signal.SIGTERM, lambda *_: stop_flag.__setitem__("stop", True))

    def save_state(path: Path, step: int) -> None:
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

    export = export_merged_checkpoint(
        model,
        output_dir=config.output_dir / "export",
        base_model_card=config.model_card,
        tokenizer_reference=CTC_TOKENIZER,
        decode_config={"strategy": "ctc_greedy"},
        gate_report_reference=None,
        training_run_identity={
            "run_fingerprint": fingerprint,
            "steps": outcome["step"],
            "wrap_audit": {k: wrap_audit[k] for k in
                           ("rank", "alpha", "trainable_parameters")},
        })
    print(json.dumps({"status": "TRAINING_COMPLETE", "export": export,
                      "steps": outcome["step"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
