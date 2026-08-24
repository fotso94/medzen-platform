# B5-UNIVERSAL-ARM2-DESIGN-2026-001 — preservation-aware distillation

Status: DESIGNED. Implementation landed (host-safe). Calibration packet is a
DRAFT pending the Arm-2 image digest. **No Arm-2 compute, no sealed data, no
promotion/deployment** until the calibration packet is reviewed and the
budget is approved (Codex reviews #14-#17).

## Why Arm-2

Arm-1 (`b5-universal-arm1-2026-005`) delivered a ~7.1% aggregate macro-WER
improvement (base 0.3352 → best step-14000 0.3114) with large Pidgin
(−28% rel) and Kinyarwanda (−10% rel) gains, but **regressed Lingala** on
every one of the 20 checkpoints (base 0.1845 → 0.199; confirmed on the
386-row sentinel, non-inferiority not established). Lingala and Swahili are
**strong regression sentinels** where the design's rule is *any loss is
disqualifying* (`B5-UNIVERSAL-PILOT-DESIGN-2026-001`). Arm-1 was therefore
rejected and routed here.

## Approach: KD anchoring on the preservation languages only

Train all 7 languages as before (full-FT, gb9, ARCH-2026-001 ACK), but add a
**knowledge-distillation term that anchors the student to a FROZEN teacher on
the preservation/sentinel languages** (english, french, swahili, lingala).
The student stays free to move on the target languages (pidgin, kinyarwanda,
ewe) where Arm-1's gains came from; the KD term penalises drift away from
what the base already serves well, preventing the Lingala-style regression.

Combined per-batch loss (`pipeline/omniasr_train.py::_batch_loss_kd`):

```
(loss_ctc  +  alpha * KD(teacher || student))  /  batch_size
```

kept batch-size-normalized so the LR calibration walls (≤1e-4 full-mode) hold.
`alpha` absorbs the KD reduction scale and is fixed by the calibration run.

## Teacher eligibility (alignment-constrained)

KD-by-KL over CTC frame log-probs requires the teacher and student to share
the **exact vocabulary ordering and frame-subsampling grid**. Proven ONLY for
the CTC family under unconditioned `ctc_greedy`:

- **Base teacher (used for calibration):** card `medzen_omniASR_CTC_1B_v2`,
  tokenizer `medzen_omniASR_tokenizer_written_v2` (sha `8aa11a10…`) — the same
  card and bytes the student stages, so alignment is by construction.
- **Kinyarwanda v1:** alignment-eligible (a rank-32 encoder-scoped LoRA merged
  onto that exact base with the identical tokenizer), but **not yet wired** —
  it needs its own reviewed, sha-verified fairseq2 card and research-prefix
  staging (its S3 path contains the screen-forbidden `approved/asr`, so it can
  only ever be named by card). `MEDZEN_KD_TEACHER_MODE=kw_v1` refuses today.
- **Barred:** Kinyarwanda v2 (governance: `B5-KW-V2-FUTILITY-2026-001/-002`),
  the LLM variant, and any language-conditioned decode path. Tokenizer-
  compatible ≠ approved teacher.

`assert_kd_alignment` re-checks identical (frames, vocab) at the first batch
and refuses before any GPU-hours if a teacher is misaligned.

## Teacher freezing & determinism

The teacher is a **second, independent** base instance
(`omniasr_distill.load_teacher`): `eval()` + `requires_grad_(False)`, obtained
**before** the student's full-mode unfreeze so the student's updates cannot
mutate it, forward-only under `no_grad`. It holds no optimizer state and is
nothing in the checkpoint (deterministically reconstructed from its
sha-verified card on resume). `eval()` disables dropout so it draws no RNG,
preserving kill/resume trajectory equivalence. `teacher_freeze_audit` asserts
every teacher parameter is non-trainable.

## Resume & non-finite (unchanged guards, extended coverage)

- The KD knobs (`MEDZEN_KD_ENABLE/ALPHA/TEMPERATURE/TEACHER_CARD/TEACHER_MODE/
  PRESERVATION_LANGUAGES`) enter `TrainerConfig.fingerprint_payload`, so a KD
  run **cannot resume a non-KD checkpoint directory** (fail-closed at
  `read_resume_state`).
- The three existing non-finite guards (step-loss, grad-norm, parameters)
  cover a diverging KD term automatically → `TRAINING_DIVERGED_NONFINITE`
  (exit 43), no poisoned checkpoint. Mitigation is numeric: fp32 KL, T² scaling,
  finite temperature/alpha rejected at parse time.

## Files

- `pipeline/omniasr_distill.py` — KD numerics (host-safe reference +
  differentiable torch), alignment, mask, teacher load/freeze.
- `pipeline/omniasr_train.py` — KD knobs in `TrainerConfig`/`parse_config`,
  `_batch_loss_kd`, `make_batch_loss`, teacher load in `main()`.
- `pipeline/omniasr_data.py` — per-row language tag in the batch (KD mask).
- `tests/test_omniasr_distill.py` — 5 required tests (deterministic loss,
  teacher-freezing, alignment, non-finite, resume/fingerprint), host-safe +
  torch-marked in-image.

## Open items before compute

1. **Logits contract (in-image):** confirm fairseq2 v0.6.0 `Wav2Vec2AsrModel`
   exposes per-frame CTC log-probs without targets, for both models — the only
   substantive fairseq2 change, validated only in the trainer image (C3).
2. **Frame-alignment mid-run:** confirm the CTC frame count is a function of
   input length/stride (positional), not of learned weights, so a full-FT
   student keeps identical frame counts to the un-adapted teacher.
3. **KD hyperparameters** (alpha 0.5, temperature 1.0 are draft placeholders):
   the calibration run fixes them; a sweep would need multiple packets.
4. **Image + calibration packet:** build the Arm-2 trainer image, run the
   in-image distillation tests, pin the image digest into
   `B5-UNIVERSAL-ARM2-FTCAL-SAGEMAKER-BINDINGS-2026-001.json`, then the
   independent reviewer issues `reviews/b5-universal-arm2-ftcal-2026-001.json`.

## Round 18 corrections (Codex review #18)

The first implementation had real defects, fixed here and validated with real
torch (`tests/test_omniasr_distill.py`: 19 passed, peak-memory needs CUDA):

- **fairseq2 contract:** `_batch_loss_kd` now UNPACKS `(loss, logits, layout)`
  from one student call with `return_logits=True` and `(logits, layout)` from
  the teacher — the round-17 code treated the return as a bare tensor and would
  have crashed on the first batch.
- **KD reduction:** the term is a MEAN over only the VALID, preservation-
  weighted encoder frames. The previous code summed over frames but divided by
  rows (frames-times too large) and included padded frames (making the weight
  depend on clip length). Student and teacher output lengths must match.
- **One clear objective:** `CTC_mean + alpha * KD_mean` (both terms cleanly
  normalized), not the previous double-normalization.
- **Config hardening:** strict boolean `MEDZEN_KD_ENABLE` (a garbage value
  refuses, not silently disables); `alpha ∈ (0, 1]` (0 refused); teacher card
  == student card == pinned `CTC_CARD`; authoritative language tags (a missing
  tag refuses rather than silently dropping preservation).
- **Per-language KD weights** (`MEDZEN_KD_LANGUAGE_WEIGHTS`): Arm-1 improved
  French/English, so a uniform preservation weight could suppress those gains;
  weights let the comparison put heavier pressure on the regressed sentinels
  (lingala, swahili) and lighter on the anchors (english, french).
- **Trainer image:** `Dockerfile.trainer-omniasr` COPYs the module + tests into
  both stages and RUNS all distillation tests at build.

## Calibration is a two-step gate

1. **Mechanics + memory** (this DRAFT packet, one 30-step run): validates the
   KD numerics in-image, separate CTC/KD/total loss, per-language KD coverage,
   peak GPU memory, throughput, export/serve, and a directional dev-sentinel
   read. Acceptance criteria are enumerated in the packet.
2. **Hyperparameter selection** (separate, predeclared UNSEALED comparison): a
   single run cannot select alpha/temperature/weights scientifically — a small
   predeclared set of KD settings is compared on the frozen dev sentinels,
   authored and reviewed before any full Arm-2 training.
