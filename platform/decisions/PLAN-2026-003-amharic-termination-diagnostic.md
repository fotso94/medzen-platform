# PLAN-2026-003 — B4 Amharic termination diagnostic

**Status: PREPARED, NOT EXECUTED.**  
**No new training, image build, EC2 launch, registration, promotion or B5
transition is authorised by this record.**

## Why the previous campaign stopped

Attempt 5 proved that the corrected LoRA path trains, saves, reloads and changes
model logits. All three 100-step candidates improved nine-language macro WER.
None passed the predeclared hard gates:

| LR | Macro WER | Amharic WER Δ | Amharic EOS | Amharic cap-hit |
|---:|---:|---:|---:|---:|
| base | 1.060233 | — | 0.00 | 1.00 |
| 1e-4 | 0.979511 | +0.1818 | 0.12 | 0.88 |
| 3e-4 | 0.963367 | +0.4283 | 0.00 | 1.00 |
| 5e-4 | 0.954267 | +0.5812 | 0.00 | 1.00 |

The higher learning rates improve the aggregate while progressively damaging
Amharic. Selecting the least-bad candidate would violate the declared
per-language and termination gates.

## What is now ruled out

`VAL-2026-002-label-contract-audit.json` used the pinned large-v3 tokenizer and
all nine frozen manifest hashes:

- 385/385 labels have the exact language/task prefix;
- 385/385 retain the EOS target;
- 0 exceed the 448-token model label limit;
- 0 need more than the evaluator's 440 generated-token budget;
- Amharic needs at most 416 generated tokens including EOS.

Therefore the all-row Amharic base cap-hit is genuine decode non-termination,
not an evaluation budget that cannot fit the reference. The duplicated-SOT
training defect remains fixed; the saved-adapter smoke proved the corrected
collator and LoRA boundary work.

## Bounded next investigation

Do not launch another full sweep yet. First add one immutable diagnostic stage
that uses the existing pinned base and the retained 1e-4 checkpoint; it performs
**no optimisation**.

The stage must report aggregates only, per language and for Amharic separately:

1. teacher-forced total, content-token and EOS-token negative log-likelihood;
2. EOS rank/probability at the reference terminal position;
3. generated-token uniqueness and repeated n-gram rates;
4. count of control tokens emitted after the four-token prompt;
5. the same metrics for untouched base and the saved 1e-4 adapter;
6. exact artifact, tokenizer, manifest, code, image and stage-descriptor hashes.

No transcript, token sequence, speaker, session, audio or per-row identifier may
be printed or persisted. The stage must use the existing frozen manifests and
read-only candidate artifacts. It must not write a model or register anything.

## Decision branches

- **EOS target likelihood is specifically poor while content likelihood is
  normal:** investigate EOS weighting/label masking and add a unit-level
  teacher-forced regression before training again.
- **Both base and adapter are repetition-dominated on Amharic:** investigate
  the frozen Amharic slice and large-v3 decoding compatibility; do not weaken
  the gate or silently drop the language.
- **The adapter degrades EOS likelihood relative to base:** add stratified
  per-language loss telemetry and test a lower/longer schedule. A future sweep
  should start below 1e-4 and evaluate early checkpoints; it must be separately
  budgeted and predeclared.
- **The diagnostic finds a prompt, cache or artifact mismatch:** fix that
  boundary and repeat only the diagnostic before any optimisation.

## Launch blockers

Before this diagnostic can run:

1. implement and behaviorally test the aggregate teacher-forced diagnostic;
2. define a new campaign/ledger namespace—the current campaign has reconciled
   $4.1081 of its $6 ceiling;
3. reserve EC2 watchdog **plus** the new 600-second lifecycle envelope;
4. produce a clean commit, deterministic bundle hash and image/code binding;
5. obtain an explicit packet approval for the bounded diagnostic spend.

## B4/B5 boundary

This diagnostic cannot complete B4. The Base v5 B4 exit still requires a
successful multilingual training run, Spot interruption/resume proof, adapter
merge, the declared English/French replay and code-switch mix, CTranslate2
`int8_float16`, and the full A5 gate suite with reproducible provenance. B5
remains blocked.
