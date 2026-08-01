# PLAN-2026-003 — Acholi-deferred B4 continuation

**Status: APPROVED, PREPARED, NOT YET EXECUTED.**  Revision 2,
2026-08-01.  This plan continues the same non-promotable B4
training-system validation after campaign `b4-scoped-047353961340` attempt 2
failed closed with no compatible learning rate across the former seven-set
validation surface.

## Decision and scope

No source data is deleted.  Acholi joins Amharic and Ewe as a reversible
campaign-scope deferral for later continuous training.  Oromo and Luganda stay
active because the corrected scheduler-horizon evidence contains viable
unchanged-gate results for both.

| Surface | Languages |
|---|---|
| Training (11) | akan, fula, hausa, igbo, lingala, luganda, oromo, pidgin, shona, swahili, yoruba |
| Validation (6) | akan, fula, lingala, luganda, oromo, shona |
| Deferred (3) | acholi, amharic, ewe |

The exact new mix is 3,321 eligible rows and 3,320 sampled rows at temperature
0.5 and seed 0.  Its fingerprint is
`a58b1d300980c467ff2aed3c21ada1d067d3ab4fc8d854b22ad9a43704afeb7d`.
The 19-row policy remains byte-identical: 15 rows apply to active languages and
four Amharic rows are out of scope.  A new immutable adoption key is required;
the previous adoption and every historical run remain untouched.

## Why exactly one learning rate

Attempt 2 already compared `1e-4`, `3e-4` and `5e-4` with the corrected
600-step scheduler horizon and stopped each sweep at checkpoint 100.  After
removing Acholi from the metric map, `1e-4` passes every unchanged hard gate on
all six retained validation languages:

- EOS rate 1.0 and cap-hit rate 0.0 for every language;
- candidate macro WER 0.949783 versus base 1.026250;
- worst per-language WER change -0.0145, below the +0.05 regression cap;
- saved-adapter smoke passed.

The higher rates already fail retained languages.  Repeating them would not
answer a new question.  Because the training mix changes when Acholi is
removed, the old 1e-4 adapter is not reused: a fresh 100-step 1e-4 confirmation
is mandatory before any final run.

## Execution topology and gates

The work is direct on-demand EC2, never EKS and never Spot, with at most one GPU
instance active at a time:

1. one c6i.2xlarge builder for a commit- and digest-pinned image;
2. one g6.xlarge base-and-preflight stage on the six frozen sets;
3. one g6.xlarge fresh 1e-4 sweep, `max_steps=600`, stopped at step 100;
4. if and only if the sweep passes, one g6.xlarge final run from scratch at
   1e-4, with evaluation interleaved at steps 100, 200, 300, 400, 500 and 600.

All existing controls remain hard: exact provenance, finite gradients, active
LoRA, saved/reloaded artifact hashes, EOS 1.0 per language, cap-hit 0.0 per
language, no WER regression above +0.05, and macro WER not worse than the
fresh in-run base.  A failing final checkpoint stops optimisation before the
next checkpoint.  No least-bad selection, gate weakening or old-adapter reuse
is permitted.

## Budget

The durable B4 scoped ledger records $5.1846 actual and $3.8154 remaining under
the approved $9.00 ceiling.  Reservations are sequential and fail closed:

| Stage | Worst-case reservation |
|---|---:|
| builder | $0.2267 |
| base and preflight | $0.7268 |
| one sweep | $0.7268 |
| final | $2.0128 |

The builder is reserved, terminated and reconciled before the GPU topology is
authorised.  A fail-closed launch exposed a collision in the old reservation
identity: it was terminated before training and its 472.1-second lifecycle was
retroactively recorded rather than hidden.  Reservation IDs now include the
campaign namespace and a terminal reservation can never be reused.  The
complete corrected GPU worst case is $3.4664.  If the reconciled builder cost
leaves less than $3.4664, validation refuses before launching a GPU.  A
launch that cannot afford its own worst case remains prohibited.

The base and sweep in-instance watchdogs are 2,000 seconds.  This is supported
by measured corrected-run lifecycles: base plus preflight used 1,354.8 seconds
including boot and termination, while the 1e-4 sweep used 2,130 seconds
including the separately reserved 600-second lifecycle envelope.  The change
does not remove training, validation, smoke, cleanup or any quality gate.

## Outcome contract

Expected registered models: **zero**.  This campaign remains
`training_system_validation`, `promotable=false`.  Even a complete pass proves
only that the corrected multilingual training path works for this scoped mix.
It does not establish promotion-grade quality, deploy anything, or authorise a
B5 production transition.
