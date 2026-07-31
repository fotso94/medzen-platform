# PLAN-2026-002 — Corrected B4 retraining

**Status: PREPARED, NOT EXECUTED.** No training, registration, promotion,
deployment or B5. Requires separate approval.

---

## The decision that comes first

Pidgin has **no speaker/session-disjoint validation set and no untouched
holdout**. The 44-clip diagnostic set shares both speakers and sessions with
training and has already informed this investigation, so it cannot select a
checkpoint or support promotion — under any option below.

| | **A — promotion-grade** | **B — training-system validation** |
|---|---|---|
| Prerequisite | collect new Pidgin speakers/sessions first | none |
| Blocked on | data collection of unknown duration; the registry records Cameroon Pidgin as absent from every public source | nothing |
| What a pass means | the candidate may enter B5 gating | the training system is fixed; the model is not evaluated |
| Checkpoint selection | on the new disjoint set | on the **9 languages that already have disjoint ASR sets** |
| Promotion | possible after gates | **impossible by construction** |
| Cost | collection + ~$11 GPU | ~$11 GPU |

**Recommendation: B.** The defect under repair is in the *training path*, and B
tests exactly that without waiting on data collection. A promotion-grade run
launched before the fix is proven would risk spending the new Pidgin data on a
run that fails for the old reason. B also has a real validation surface today:
the coverage audit found **9 languages with speaker- and session-disjoint ASR
sets** — acholi, akan, amharic, ewe, fula, lingala, luganda, oromo, shona — none
of which have informed any investigation.

Under B the run is tagged `purpose = training_system_validation`, no model is
registered, and success means *"the objective is now correctly specified"*, not
*"this model is good"*.

**This choice is yours; everything below is written to work under either.**

---

## 1 · Corrected alignment — already landed, to be re-proven

| | |
|---|---|
| Collator | takes `model.config.decoder_start_token_id`, asserts every row begins with it, strips exactly one |
| Preserved | `<\|lang\|>`, `<\|transcribe\|>`, `<\|notimestamps\|>`, trailing `<\|endoftext\|>` |
| `label_length.py` | same decoder-start definition; fails closed on empty or wrong-prefix labels |
| Proof | `tests/test_collator_alignment.py` runs the real collator through HuggingFace's own `shift_tokens_right` |

## 2 · Regenerated manifest and fingerprint

The corrected effective-length calculation **changes the corpus**:

| | Old (bos defect) | Corrected |
|---|---|---|
| Rows over the 448 limit | 6 | **5** |
| Deferred total | 20 | **19** |
| Eligible after exclusions | 4600 | **4601** |

Row `d0ffd52881d0b074` (amharic) is raw 449 → effective **448**, exactly at the
limit and never over it. **Fingerprint `77c7ce61edba96c8` is therefore invalid
for the corrected run** and must be recomputed.

## 3 · Revised deferral policy — new, not reused

`DQ-2026-002` defers 20 rows and **must not be reused**. A new policy is
required covering **19** rows (5 over-limit + 14 rate outliers), with a new
`ADOPTION.json` binding the raw `COMPLETE.json` bytes and the new policy
sha256. The current adoption binds the 20-row policy and no longer describes a
correct mix.

`DQ-2026-001` — the human review — stays **draft with all 20 entries
unclassified**, and its scope must be re-derived, since one of its rows is no
longer decoder-incompatible.

## 4 · Immutable pins

Base `openai/whisper-large-v3@06f233fe…`, manifest `6a1987d462fc3330…`.
Image: **a new build is required** — `sha256:fc6972a5…` bakes commit `202b005`,
which predates every fix. Dependencies stay `torch 2.13.0+cu130`,
`transformers 5.14.1`, `peft 0.20.0`, pinned by the image and recorded in the
run.

## 5 · Explicit generation config

Set in **one** place and recorded in the run record. The failed run wrote
`forced_decoder_ids=None` and `suppress_tokens=[]` to `model.config` while
generation reads `generation_config` — training-time intent and generation-time
behaviour lived in different objects. Not confirmed as contributing, but it must
not survive into the next run unresolved.

## 6 · LoRA `task_type=SEQ_2_SEQ_LM`

Currently unset. To be set explicitly and asserted in the run record.

## 7 · Pre-flight gauntlet — CPU first, then one GPU minute

| Check | Where | Pass condition |
|---|---|---|
| Tokenizer/alignment proof | CPU | exactly one SOT in decoder inputs; labels start with the language token; EOS retained |
| One-batch overfit | GPU, ~1 min | loss falls to near zero on a single batch — proves the objective is learnable |
| One-clip generation smoke | GPU, ~1 min | **EOS emitted, zero cap hits** |

**If the smoke test does not emit EOS, the run stops.** That single check is
what the previous 600 steps and $0.88 could not tell us.

## 8 · Learning-rate sweep instead of repeating 1e-3

`1e-3` at rank 32 is high for Whisper LoRA (typical 1e-4–5e-4) and was a
suspected aggravator. Proposed: **3 short runs at 1e-4, 3e-4, 5e-4, 100 steps
each**, selected on `val_wer` with EOS and cap-hit gates — roughly 25 minutes,
~$0.42 total. Blindly repeating `1e-3` after a failure would be reusing the one
hyperparameter already under suspicion.

## 9 · Checkpoint upload, recovery, Spot safety

Checkpoints every 100 steps to `candidates/asr/<run>/`, each verified by
read-back rather than by `upload_file` returning. **On-demand, not Spot** — at
~$11 the interruption risk is not worth the discount, and the previous 600-step
run completed on-demand without incident.

## 10 · MLflow linkage

Per `MLFLOW-2026-001`: alignment-proof params, per-checkpoint `val_wer`,
`val_cer`, `val_eos_rate`, `val_cap_hit_rate`, length ratios, forward-only
`parent_failed_run` tag, evaluation artifacts referenced by sha256. **Selection
by `val_wer` with EOS and cap-hit hard gates — never `train_loss` alone.**

## 11 · Validation and holdout coverage

| | Count | Languages |
|---|---|---|
| Speaker/session-disjoint ASR sets | **9** | acholi, akan, amharic, ewe, fula, lingala, luganda, oromo, shona |
| Diagnostic-only (overlapping) | **5** | hausa, igbo, **pidgin**, swahili, yoruba |
| Untouched final holdouts | **0** | — |

**Zero holdouts exist.** Under option B that is acceptable, because nothing is
promoted. Under A a holdout must be created and frozen before the run, not
after.

## 12 · Cost ceiling and termination

| Item | Estimate |
|---|---|
| LR sweep, 3 × 100 steps | ~$0.42 |
| Corrected 600-step run | ~$0.90 |
| Per-checkpoint validation, 6 × 9 languages | ~$0.60 |
| **Total** | **≈ $2** |
| Hard ceiling | **$5**, 4-hour watchdog, `shutdown-behavior=terminate` |

Stop rules: alignment assertion fails → refuse at step 0 · smoke test emits no
EOS → stop before training · `val_cap_hit_rate` > 0.05 at any checkpoint → stop
· non-finite loss → stop.

Loss magnitude stays a **warning, not a gate**, until a correctly aligned
per-language baseline distribution is measured. "Opens at 1–3, abort above 6"
was never calibrated for this 14-language mixture.

---

## Blocking prerequisites before any GPU spend

1. **Your choice of A or B.**
2. New 19-row deferral policy, reviewed.
3. New `ADOPTION.json` binding raw `COMPLETE.json` bytes + the new policy.
4. Recomputed dataset fingerprint.
5. New trainer image from a post-fix commit, scanned, digest verified.
6. `DQ-2026-001` scope re-derived.

Steps 2–4 are mechanical but need review; step 5 needs a builder run. None has
been started.
