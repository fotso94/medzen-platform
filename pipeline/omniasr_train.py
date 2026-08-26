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
    multilingual_ack: str | None
    checkpoint_dir: Path
    output_dir: Path
    checkpoint_every_steps: int
    exclusions_ref: str | None
    expect_excluded: int | None
    adoption_key: str | None
    # Arm-2 preservation-aware distillation (Codex reviews #14-#17); KD is
    # OFF by default so non-KD runs are byte-identical to before.
    kd_enable: bool
    kd_alpha: float
    kd_temperature: float
    kd_teacher_card: str
    kd_teacher_mode: str
    kd_preservation_languages: tuple[str, ...]
    kd_language_weights: tuple[tuple[str, float], ...]
    # Strict Arm-2 execution-mode enum ('plain' | 'arm2_comparative'), bound
    # into the fingerprint so a run cannot resume across a different mode.
    execution_mode: str

    def fingerprint_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["languages"] = sorted(self.languages)
        payload["allowed_policies"] = sorted(self.allowed_policies)
        payload["kd_preservation_languages"] = sorted(
            self.kd_preservation_languages)
        payload["kd_language_weights"] = sorted(self.kd_language_weights)
        payload["checkpoint_dir"] = str(self.checkpoint_dir)
        payload["output_dir"] = str(self.output_dir)
        # Safe one-way legacy migration (Codex round 33): a PLAIN run keeps its
        # PRE-enum fingerprint (execution_mode omitted) so old plain checkpoints
        # still resume byte-identically; only arm2_comparative binds the mode
        # into the fingerprint. The two payload SHAPES differ (comparative has
        # the extra key), so a plain and a comparative run can never resume each
        # other's checkpoint — the mode-switch guard is preserved.
        if self.execution_mode == "plain":
            payload.pop("execution_mode", None)
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
        if len(languages) != 1:
            # Codex review #7: the ack alone was a magic string — a
            # multilingual full run must ALSO satisfy the pilot-profile
            # bounds (the packet supplies exact values; these are the
            # trainer-side hard walls).
            temp_value = _number("MEDZEN_TEMPERATURE", "0.5", float, 0.0)
            if temp_value > 0.5:
                raise TrainerRefusal(
                    f"multilingual full FT requires temperature <= 0.5, got "
                    f"{temp_value} (higher lets one language dominate)")
            if not env.get("MEDZEN_EXCLUSIONS_REF", "").strip() or not                     env.get("MEDZEN_EXPECT_EXCLUDED", "").strip():
                raise TrainerRefusal(
                    "multilingual full FT requires MEDZEN_EXCLUSIONS_REF + "
                    "MEDZEN_EXPECT_EXCLUDED (gb6 adoption binds DQ-2026-006; "
                    "a run without it refuses at mix time anyway)")
            steps_value = _number("MEDZEN_MAX_STEPS", "600", int, 1)
            every_value = _number("MEDZEN_CHECKPOINT_EVERY", "50", int, 1)
            if every_value < min(1000, steps_value):
                raise TrainerRefusal(
                    f"multilingual full FT requires checkpoint_every >= "
                    f"min(1000, max_steps): {every_value} would write "
                    f"~{(steps_value // every_value) * 2.6:.0f} GB of "
                    "checkpoints (the 40k/50 default is ~2 TB on a 250 GB "
                    "disk)")

    # --- Arm-2 KD knobs (bound into the fingerprint so a KD run cannot
    # resume a non-KD checkpoint directory). OFF unless explicitly enabled.
    # Codex review #18: STRICT boolean — an unrecognised value (e.g. "TRUE",
    # "yes") must NOT silently disable KD.
    kd_raw = env.get("MEDZEN_KD_ENABLE", "0").strip().lower()
    if kd_raw not in ("0", "false", "no", "off", "1", "true", "yes", "on"):
        raise TrainerRefusal(
            f"MEDZEN_KD_ENABLE={env.get('MEDZEN_KD_ENABLE')!r} is not a boolean")
    kd_enable = kd_raw in ("1", "true", "yes", "on")
    # Strict execution-mode enum (owner-directed), resolved WITH the same
    # fail-closed rules as scripts/b5_sagemaker_job.resolve_execution_mode
    # (a parity test locks the two together): unknown value refuses; 'plain'
    # with KD refuses; absent => KD-on is arm2_comparative, KD-off is plain.
    _mode_raw = env.get("MEDZEN_EXECUTION_MODE", "").strip()
    if _mode_raw == "":
        execution_mode = "arm2_comparative" if kd_enable else "plain"
    elif _mode_raw not in ("plain", "arm2_comparative", "arm2_scoring"):
        raise TrainerRefusal(
            f"MEDZEN_EXECUTION_MODE={_mode_raw!r} is not one of "
            "('plain', 'arm2_comparative', 'arm2_scoring') — unknown modes "
            "fail closed")
    elif _mode_raw == "plain" and kd_enable:
        raise TrainerRefusal(
            "MEDZEN_EXECUTION_MODE=plain with MEDZEN_KD_ENABLE truthy is "
            "contradictory — plain training runs no KD (fail closed)")
    elif _mode_raw == "arm2_scoring" and kd_enable:
        raise TrainerRefusal(
            "MEDZEN_EXECUTION_MODE=arm2_scoring with MEDZEN_KD_ENABLE truthy "
            "is contradictory — the evaluator decodes, it never trains")
    else:
        execution_mode = _mode_raw
    if kd_enable:
        # alpha is a POSITIVE weight — 0 would silently disable KD while the
        # run still claims to be a KD run (Codex review #18)
        kd_alpha = _number("MEDZEN_KD_ALPHA", "0.5", float, None)
        if not (0.0 < kd_alpha <= 1.0):
            raise TrainerRefusal(
                f"MEDZEN_KD_ALPHA={kd_alpha} must be in (0, 1] when KD is enabled")
        kd_temperature = _number("MEDZEN_KD_TEMPERATURE", "1.0", float, None)
        if kd_temperature <= 0.0:
            raise TrainerRefusal("MEDZEN_KD_TEMPERATURE must be > 0")
        kd_teacher_mode = env.get("MEDZEN_KD_TEACHER_MODE", "base").strip()
        if kd_teacher_mode != "base":
            raise TrainerRefusal(
                f"MEDZEN_KD_TEACHER_MODE={kd_teacher_mode!r} is not wired — "
                "Arm-2 calibration is base-teacher-only; a Kinyarwanda-v1 "
                "teacher requires a reviewed sha-verified card first")
        # Codex review #18: enforce EXACT teacher == student == pinned CTC_CARD.
        # A different card would break vocab/frame alignment; only the base is
        # byte-identical to the student's staged bytes.
        model_card = env.get("MEDZEN_MODEL_CARD", CTC_CARD)
        kd_teacher_card = env.get("MEDZEN_KD_TEACHER_CARD", CTC_CARD).strip()
        if not (kd_teacher_card == model_card == CTC_CARD):
            raise TrainerRefusal(
                "KD requires teacher card == student card == the pinned "
                f"{CTC_CARD} (got teacher={kd_teacher_card!r}, "
                f"student={model_card!r}) — alignment holds only for the base")
        sentinel_default = "english,french,swahili,lingala"
        pres = tuple(sorted({
            token.strip().lower() for token in
            env.get("MEDZEN_KD_PRESERVATION_LANGUAGES", sentinel_default).split(",")
            if token.strip()}))
        if not pres:
            raise TrainerRefusal(
                "KD is enabled but MEDZEN_KD_PRESERVATION_LANGUAGES is empty")
        unknown = [lang for lang in pres if lang not in languages]
        if unknown:
            raise TrainerRefusal(
                f"KD preservation languages {unknown} are not in the training "
                f"language set {sorted(languages)}")
        kd_preservation_languages = pres
        # Optional per-language KD weights (Codex review #18: one uniform
        # weight may suppress a real Arm-1 gain like French). Default uniform
        # 1.0; every named language must be a preservation language and the
        # weight finite > 0.
        weights: dict[str, float] = {lang: 1.0 for lang in pres}
        raw_weights = env.get("MEDZEN_KD_LANGUAGE_WEIGHTS", "").strip()
        if raw_weights:
            for pair in raw_weights.split(","):
                if not pair.strip():
                    continue
                if "=" not in pair:
                    raise TrainerRefusal(
                        f"MEDZEN_KD_LANGUAGE_WEIGHTS entry {pair!r} is not lang=weight")
                lang, _, val = pair.partition("=")
                lang = lang.strip().lower()
                if lang not in pres:
                    raise TrainerRefusal(
                        f"KD weight for {lang!r} which is not a preservation language")
                try:
                    weight = float(val)
                except ValueError as exc:
                    raise TrainerRefusal(
                        f"KD weight {val!r} for {lang} is not a number") from exc
                if not math.isfinite(weight) or weight <= 0.0:
                    raise TrainerRefusal(
                        f"KD weight for {lang} must be finite > 0, got {weight}")
                weights[lang] = weight
        kd_language_weights = tuple(sorted(weights.items()))
    else:
        kd_alpha = 0.0
        kd_temperature = 1.0
        kd_teacher_mode = "base"
        kd_teacher_card = ""
        kd_preservation_languages = ()
        kd_language_weights = ()

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
        multilingual_ack=env.get("MEDZEN_MULTILINGUAL_FULL_ACK", "").strip()
        or None,
        checkpoint_dir=Path(env.get("MEDZEN_CHECKPOINT_DIR", "/opt/ml/checkpoints")),
        output_dir=Path(env.get("MEDZEN_OUTPUT_DIR", "/opt/ml/model")),
        checkpoint_every_steps=_number("MEDZEN_CHECKPOINT_EVERY", "50", int, 1),
        exclusions_ref=env.get("MEDZEN_EXCLUSIONS_REF", "").strip() or None,
        expect_excluded=(int(env["MEDZEN_EXPECT_EXCLUDED"])
                         if env.get("MEDZEN_EXPECT_EXCLUDED", "").strip() else None),
        adoption_key=env.get("MEDZEN_ADOPTION_KEY", "").strip() or None,
        kd_enable=kd_enable,
        kd_alpha=kd_alpha,
        kd_temperature=kd_temperature,
        kd_teacher_card=kd_teacher_card,
        kd_teacher_mode=kd_teacher_mode,
        kd_preservation_languages=kd_preservation_languages,
        kd_language_weights=kd_language_weights,
        execution_mode=execution_mode,
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
# Calibration metrics (Codex review #19 F3): the Arm-2 calibration must PROVE
# separate CTC/KD/total loss, per-language KD coverage, peak GPU memory and
# throughput — the old loop returned one combined scalar and logged only
# mean_loss, so the acceptance criteria were unverifiable. This accumulator is
# PURE (host-testable, no torch) and produces the exact artifact schema
# scripts/verify_arm2_calibration.py machine-checks.
# --------------------------------------------------------------------------

CALIBRATION_METRICS_SCHEMA = "b5-arm2-calibration-metrics/1"
CALIBRATION_METRICS_FILE = "calibration-metrics.json"
# Codex review #20 (F5): the per-step accumulator is checkpointed here so a
# resumed (spot-reclaimed) run keeps the FULL trajectory, not just post-resume
# steps. Lives beside LATEST.json in the checkpoint dir; write-then-rename.
CALIBRATION_METRICS_PROGRESS = "calibration-metrics-progress.json"


class CalibrationMetrics:
    """Aggregates the KD loss decomposition across grad-accumulation micro
    batches into per-step means and running per-language coverage. `finalize`
    stamps the run-level facts (status, peak GPU memory, throughput) the
    verifier enforces. The serve (readyz) and dev-sentinel-WER fields are
    filled by the in-image calibration wrapper AFTER training; the verifier
    requires them, so a run that skips them fails closed."""

    def __init__(self) -> None:
        import time
        self._micro: list[dict[str, Any]] = []
        self.per_step: list[dict[str, Any]] = []
        self.coverage: dict[str, dict[str, int]] = {}
        # Codex review #21 F5: wall time must be CUMULATIVE across resume — a
        # resumed run dividing all steps by only its own process runtime
        # overstated throughput. prior_wall_seconds carries the pre-reclaim
        # elapsed time (restored from the progress sidecar); _proc_start times
        # this process for checkpoint-time persistence.
        self.prior_wall_seconds: float = 0.0
        self._proc_start: float = time.perf_counter()

    def record_micro(self, sink: dict[str, Any]) -> None:
        """Capture one micro-batch's decomposed components (a copy of the
        sink the KD closure just wrote). No-op if the closure produced none
        (e.g. a non-KD run)."""
        if sink:
            self._micro.append(dict(sink))

    def commit_step(self, step: int, lr: float) -> None:
        """Fold the accumulated micro-batches into one per-step record
        (mean CTC/KD/total) and roll per-language coverage forward."""
        if not self._micro:
            return
        n = len(self._micro)
        ctc = sum(m["ctc"] for m in self._micro) / n
        kd = sum(m["kd"] for m in self._micro) / n
        total = sum(m["total"] for m in self._micro) / n
        alpha = sum(m["alpha"] for m in self._micro) / n
        for m in self._micro:
            for language, bucket in m.get("kd_coverage", {}).items():
                run = self.coverage.setdefault(language, {"rows": 0, "frames": 0})
                run["rows"] += int(bucket.get("rows", 0))
                run["frames"] += int(bucket.get("frames", 0))
        self.per_step.append({"step": int(step), "ctc": ctc, "kd": kd,
                              "total": total, "alpha": alpha, "lr": float(lr)})
        self._micro = []

    # Codex review #20 (F5): the accumulator restarted EMPTY on resume, so a
    # spot-reclaimed run wrote a short per_step and failed its own verifier.
    # Persist/restore the committed record + coverage across a checkpoint so
    # per_step reflects the FULL trajectory (the micro buffer is transient and
    # intentionally not persisted — a reclaim happens between steps).
    def to_state(self) -> dict[str, Any]:
        import time
        return {"per_step": self.per_step, "coverage": self.coverage,
                # cumulative elapsed at this checkpoint: prior runs' time plus
                # this process's own (Codex review #21 F5)
                "wall_seconds": self.prior_wall_seconds
                + (time.perf_counter() - self._proc_start)}

    def restore(self, state: dict[str, Any]) -> None:
        import time
        self.per_step = list(state.get("per_step", []))
        self.coverage = {k: dict(v) for k, v in state.get("coverage", {}).items()}
        self.prior_wall_seconds = float(state.get("wall_seconds", 0.0))
        self._proc_start = time.perf_counter()
        self._micro = []

    def finalize(self, *, status: str, steps_completed: int, max_steps: int,
                 peak_gpu_bytes: int | None, wall_seconds: float,
                 samples_per_step: int | None = None,
                 identity: dict[str, Any] | None = None,
                 serve: dict[str, Any] | None = None,
                 dev_sentinel_wer: dict[str, Any] | None = None) -> dict[str, Any]:
        kd_values = [s["kd"] for s in self.per_step]
        positive_finite = sum(
            1 for v in kd_values if math.isfinite(v) and v > 0.0)
        # Codex review #21 F5: total wall = prior processes' elapsed (restored
        # across resume) + this process's elapsed — steps_completed covers the
        # FULL trajectory, so the denominator must too.
        wall_seconds = self.prior_wall_seconds + float(wall_seconds)
        steps_per_min = (steps_completed / (wall_seconds / 60.0)
                         if wall_seconds > 0 else 0.0)
        # Codex review #20 (F5): samples/s was promised but never recorded.
        samples_per_sec = (
            (steps_completed * int(samples_per_step) / wall_seconds)
            if (wall_seconds > 0 and samples_per_step) else 0.0)
        # per-step contiguity 1..N is asserted by the verifier; expose it so a
        # torn/resumed accumulator is caught rather than silently short.
        step_sequence = [int(s["step"]) for s in self.per_step]
        return {
            "schema": CALIBRATION_METRICS_SCHEMA,
            "status": status,
            "steps_completed": int(steps_completed),
            "max_steps": int(max_steps),
            "per_step": self.per_step,
            "step_sequence": step_sequence,
            "kd_min": min(kd_values) if kd_values else None,
            "kd_max": max(kd_values) if kd_values else None,
            "kd_positive_finite_steps": positive_finite,
            "kd_coverage": self.coverage,
            "peak_gpu_bytes": (int(peak_gpu_bytes)
                               if peak_gpu_bytes is not None else None),
            "throughput": {"steps_per_min": round(steps_per_min, 4),
                           "samples_per_sec": round(float(samples_per_sec), 4),
                           "wall_seconds": round(float(wall_seconds), 4)},
            # identity bindings (Codex review #20 F5): prove the metrics came
            # from the declared run/export/scorer/packet/verifier. Filled by
            # the calibration wrapper; the verifier requires them.
            "identity": identity,
            # filled post-training by the in-image calibration wrapper; the
            # verifier requires both, so omission fails closed
            "serve": serve,
            "dev_sentinel_wer": dev_sentinel_wer,
        }


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
    metrics: "CalibrationMetrics | None" = None,
    metrics_sink: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import torch

    stop_flag = stop_flag if stop_flag is not None else {"stop": False}
    config.checkpoint_dir.mkdir(parents=True, exist_ok=True)
    marker = read_resume_state(config.checkpoint_dir, fingerprint)
    start_step = 0
    progress_path = config.checkpoint_dir / CALIBRATION_METRICS_PROGRESS
    if marker is not None:
        start_step = load_state(config.checkpoint_dir / marker["checkpoint"])
        if start_step != marker["step"]:
            raise TrainerRefusal(
                f"checkpoint reports step {start_step}, marker says "
                f"{marker['step']}; refusing an inconsistent resume")
        # Codex review #20 (F5): restore the metrics trajectory so per_step is
        # not silently truncated to the post-resume steps.
        if metrics is not None and progress_path.exists():
            metrics.restore(json.loads(progress_path.read_text()))

    def checkpoint(step: int) -> None:
        name = f"step-{step:07d}.pt"
        tmp = config.checkpoint_dir / (name + ".tmp")
        save_state(tmp, step)
        tmp.replace(config.checkpoint_dir / name)
        write_checkpoint_marker(config.checkpoint_dir, step=step,
                                checkpoint_name=name, fingerprint=fingerprint)
        # persist the metrics trajectory alongside the marker (write-then-
        # rename) so a resume after this checkpoint keeps every recorded step
        if metrics is not None:
            tmp_m = config.checkpoint_dir / (CALIBRATION_METRICS_PROGRESS + ".tmp")
            tmp_m.write_bytes(json.dumps(metrics.to_state(), sort_keys=True,
                                         separators=(",", ":")).encode() + b"\n")
            tmp_m.replace(progress_path)

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
            if metrics is not None and metrics_sink is not None:
                metrics.record_micro(metrics_sink)
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
        if metrics is not None:
            metrics.commit_step(step, rate)
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


def _layout_valid_lengths(layout, rows: int) -> list[int]:
    """Per-row count of VALID (non-padded) frames from a fairseq2 BatchLayout
    (Codex review #18: padded frames must not contribute to KD). Falls back
    to the full frame count only if the layout exposes no seq_lens."""
    lengths = getattr(layout, "seq_lens", None)
    if lengths is None:
        raise TrainerRefusal(
            "encoder output layout exposes no seq_lens — cannot exclude "
            "padded frames from the KD term")
    return [int(v) for v in lengths]


def _batch_loss_kd(model, batch, *, teacher, alpha, temperature,
                   preservation_languages, language_weights,
                   known_languages, metrics_sink=None):
    """Arm-2 preservation-aware distillation loss (in-image / C3).

    ONE clean objective (Codex review #18): CTC_mean + alpha * KD_mean.
    fairseq2 v0.6 Wav2Vec2AsrModel.forward returns (loss, logits, layout) with
    return_logits=True, and (logits, layout) without targets — so both calls
    are UNPACKED (a bare tensor was the round-17 crash). KD is a MEAN over
    only the VALID, preservation-weighted encoder frames; the student and
    teacher encoder output lengths must match."""
    import torch

    from pipeline.omniasr_distill import (DistillationRefusal, kd_loss,
                                          preservation_mask)

    loss_ctc, student_logits, student_layout = model(
        batch["seqs"], batch["seqs_layout"],
        targets=batch["targets"], targets_layout=batch["targets_layout"],
        return_logits=True)
    with torch.no_grad():
        teacher_logits, teacher_layout = teacher(
            batch["seqs"], batch["seqs_layout"])
    rows = int(student_logits.shape[0])
    student_lengths = _layout_valid_lengths(student_layout, rows)
    teacher_lengths = _layout_valid_lengths(teacher_layout, rows)
    if student_lengths != teacher_lengths:
        raise DistillationRefusal(
            "student and teacher encoder output lengths differ — KD frames "
            "are not aligned")
    weights = preservation_mask(
        batch["languages"], preservation_languages,
        weights=dict(language_weights), strict=True,
        known_languages=known_languages)
    kd = kd_loss(student_logits, teacher_logits, temperature=temperature,
                 row_weights=weights, valid_lengths=student_lengths)
    ctc_mean = loss_ctc / batch["seqs"].shape[0]
    total = ctc_mean + alpha * kd
    # Codex review #19 (F3): the calibration must PROVE the KD term is live
    # and covers each preservation language. Stash the decomposed components
    # and per-language KD coverage (rows + valid frames) so the training loop
    # can write a machine-checkable metrics artifact. Detached: reporting only.
    if metrics_sink is not None:
        coverage: dict[str, dict[str, int]] = {}
        for language, weight, length in zip(
                batch["languages"], weights, student_lengths):
            if weight > 0:
                bucket = coverage.setdefault(
                    str(language).strip().lower(), {"rows": 0, "frames": 0})
                bucket["rows"] += 1
                bucket["frames"] += int(length)
        metrics_sink.clear()
        metrics_sink.update({
            "ctc": float(ctc_mean.detach()),
            "kd": float(kd.detach()),
            "total": float(total.detach()),
            "alpha": float(alpha),
            "kd_coverage": coverage,
        })
    return total


def make_batch_loss(config, teacher, *, metrics_sink=None):
    """The loss callable run_training_loop consumes: the plain CTC loss
    unless KD is enabled, else a frozen-teacher-anchored closure. The
    KD-disabled selection is host-testable (no torch). When ``metrics_sink``
    is a dict, the KD closure writes its decomposed CTC/KD/total loss and
    per-language coverage into it each call (Codex review #19 F3)."""
    if not config.kd_enable:
        # KD-off comparative CONTROL (Codex round 33): the plain CTC path must
        # STILL emit the decomposed metrics so the wrapper can write + verify
        # the artifact — ctc=loss, kd=0, alpha=0, total=loss, empty coverage.
        # Plain training (execution_mode 'plain') records nothing, unchanged.
        if config.execution_mode == "arm2_comparative" \
                and metrics_sink is not None:
            def _ctc_only_closure(model, batch):
                loss = _batch_loss(model, batch)
                value = float(loss.detach())
                metrics_sink.clear()
                metrics_sink.update({
                    "ctc": value, "kd": 0.0, "alpha": 0.0,
                    "total": value, "kd_coverage": {}})
                return loss
            return _ctc_only_closure
        return _batch_loss

    def _kd_closure(model, batch):
        return _batch_loss_kd(
            model, batch, teacher=teacher, alpha=config.kd_alpha,
            temperature=config.kd_temperature,
            preservation_languages=config.kd_preservation_languages,
            language_weights=config.kd_language_weights,
            known_languages=config.languages,
            metrics_sink=metrics_sink)

    return _kd_closure


def reseed_matched_rng(seed: int) -> None:
    """Reset the torch (and CUDA) global RNG to `seed`. Called AFTER every
    KD-conditional construction (teacher load) so KD-on and KD-off arms enter
    the training path with an IDENTICAL RNG trajectory under a matched seed
    (Codex stage-1 review 2026-08-25 finding 2). Kept as a tiny named helper
    so the alignment is directly regression-testable without running main()."""
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


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
    # Arm-2: obtain the FROZEN teacher BEFORE any wrap/unfreeze so the
    # student's updates can never mutate it (it is a distinct object).
    teacher = None
    if config.kd_enable:
        from pipeline.omniasr_distill import load_teacher, teacher_freeze_audit
        teacher = load_teacher(config.kd_teacher_card, device, torch.bfloat16)
        teacher_freeze_audit(teacher)
    # Codex stage-1 review (2026-08-25) finding 2: the teacher load above
    # consumes torch RNG ONLY on KD-enabled arms, so under the SAME seed a
    # KD-on candidate and the KD-off control would enter wrap/training with
    # DIFFERENT RNG trajectories — breaking the matched-seed comparison the
    # protocol requires. Re-seed after every conditional construction so both
    # arms start the training path at the identical state.
    reseed_matched_rng(config.seed)
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

    # Arm-2 calibration metrics (Codex review #19 F3): decomposed CTC/KD/total
    # loss + per-language coverage + peak GPU memory + throughput, written for
    # scripts/verify_arm2_calibration.py to machine-check the acceptance
    # criteria. Emitted for EVERY arm2_comparative run — the KD-on candidates
    # AND the KD-off control (Codex round 33). Plain training is byte-identical.
    import time as _time
    _emit_metrics = config.execution_mode == "arm2_comparative"
    metrics = CalibrationMetrics() if _emit_metrics else None
    metrics_sink: dict[str, Any] | None = {} if _emit_metrics else None
    if _emit_metrics and torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    _wall_start = _time.perf_counter()
    outcome = run_training_loop(
        model=model, optimizer=optimizer, batches=batches,
        batch_loss=make_batch_loss(config, teacher, metrics_sink=metrics_sink),
        config=config, fingerprint=fingerprint, save_state=save_state,
        load_state=load_state, stop_flag=stop_flag,
        metrics=metrics, metrics_sink=metrics_sink)
    if metrics is not None:
        peak = (int(torch.cuda.max_memory_allocated())
                if torch.cuda.is_available() else None)
        artifact = metrics.finalize(
            status=outcome["status"], steps_completed=outcome["step"],
            max_steps=config.max_steps, peak_gpu_bytes=peak,
            wall_seconds=_time.perf_counter() - _wall_start,
            samples_per_step=config.batch_size * config.grad_accum)
        (config.output_dir / CALIBRATION_METRICS_FILE).write_bytes(
            json.dumps(artifact, sort_keys=True,
                       separators=(",", ":")).encode() + b"\n")
        print(json.dumps({"status": "CALIBRATION_METRICS_WRITTEN",
                          "file": CALIBRATION_METRICS_FILE,
                          "kd_positive_finite_steps":
                          artifact["kd_positive_finite_steps"],
                          "peak_gpu_bytes": artifact["peak_gpu_bytes"]},
                         sort_keys=True))
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
