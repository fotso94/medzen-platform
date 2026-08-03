# PLAN-2026-003 — B4 Amharic termination diagnostic

**Status: COMPLETED 2026-07-31.**
**The bounded no-training diagnostic ran once on direct on-demand EC2. It
registered and promoted nothing. B4 remains incomplete and B5 remains
blocked.**

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

## Execution outcome

The approved stage completed as
`b4-amharic-termination-e2aeb77e5ecc/attempt-1/diagnostic` on one direct
on-demand `g6.xlarge`. It used code `e2aeb77e5ecc6a256aa7f79658e3e8a3cabe9a21`,
image `sha256:e427452660d2ffac7e68df3a8e2f602f7835260ccbe3b942dae06f165d8d2526`
and descriptor `32abbe906b51c495cabacc030ba512a733442c4ee7fc45e966c49aa487f27ee6`.
The immutable aggregate artifact is
`candidates/evaluations/b4-amharic-termination-e2aeb77e5ecc/attempt-1/diagnostic/evaluations/termination-diagnostic.json`
with SHA-256 `4fb15697930dc3bab9a8d9f85fc9666ba170d4cfd29e83609ca8d1b8f353dbd5`.

The diagnostic selected the plan's second decision branch. Untouched base
Amharic generation terminated on 0/25 rows and hit the 440-token cap on 25/25;
the retained 1e-4 adapter terminated on 3/25 and hit the cap on 22/25. Typical
failed rows were almost entirely repeated n-grams. Yet the adapter improved
Amharic content NLL from 1.779130 to 1.715294 and median EOS probability from
0.008301914 to 0.035384879. The failure is therefore an Amharic-specific
autoregressive decode collapse already present in the untouched base, not a
missing EOS target, prompt mismatch, inert adapter or global training failure.

The next action is a separately predeclared, aggregate-only, no-training
Amharic/large-v3 decoding-compatibility investigation. The gate must not be
weakened and Amharic must not be dropped. No further sweep is authorised by
this completed plan.

AWS-observed lifecycle was 3226.4 seconds and $0.902 for the GPU stage. The
whole diagnostic campaign cost $1.0317 against its $1.50 ceiling. The instance
terminated, its root volume was deleted, all budget entries reconciled, and an
independent audit found no active MedZen instances, unattached volumes or
active Spot requests. The linked MLflow snapshot contains two finished runs,
163 latest aggregate metrics and zero registered models. See
`platform/evidence/DIAG-2026-001-amharic-termination.json`.
