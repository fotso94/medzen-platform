# B5-CTC-CAMPAIGN-PACKET-2026-001 — language-parameterized, launch gated

Status: READY PENDING TWO INPUTS — (1) the owner-approved language list
chosen from B5-TRAINING-RECOMMENDATION-2026-001 (generated from the
merge report at suite completion), (2) the published
B6-ASR-BASE-MODEL-2026-001 decision record. Nothing here launches
without both plus the per-run review discipline.

## 1. Shape of the campaign

ONE fine-tuned CTC checkpoint over the WHOLE approved language list —
never per-language forks (Base v5 A1 §3: code-switched audio cannot be
routed before transcription, and low-resource languages depend on
cross-lingual transfer). Mechanically:

1. `MEDZEN_LANGUAGES=<owner-approved list>` into the C1 trainer
   (pipeline/omniasr_train.py) on SageMaker via scripts/b5_sagemaker_job.py
   — the T5-proven path end to end (staging, licence gate, temperature
   mix, checkpoint/resume, signed merged export).
2. Steps sized from data: steps = ceil(2 epochs x trainable_hours x 3600
   / (batch 2 x accum 8 x ~1.01s mean row)) — computed at bind time from
   the approved list's actual hours, never assumed.
3. Post-training: re-run the evaluation-suite machinery on the frozen
   eval pool against the fine-tuned checkpoint, then the T6 gate
   (pipeline/b5_suite_baseline_gate.py): per language, STRICT WER
   improvement over the suite baseline AND the A5 absolute WER/CER
   ceilings. Absence never passes; a language missing a wired
   thresholds_ref refuses.
4. On PASS: signed export promotes via the approved/ path (A3
   immutable; registry points at it). On FAIL: the checkpoint is a
   working artifact only; a diagnosis packet precedes any retry.

## 2. Cost, under the owner's standing rules (on-demand, +$100 max)

Formula at the measured T5 calibration (7.48 audio-h/GPU-h whole-job):
GPU-hours = 2 x trainable_hours / 7.48; cost = GPU-hours x $1.60
(deliberately-high on-demand bound). Reference points:

| Approved list (illustrative) | Trainable audio | GPU-h | Estimate | BUDGETED CEILING |
|---|---|---|---|---|
| ~466h (the 11 currently-clear languages, capped) | 932 audio-h eff. | ~125 | ~$200 | **$300** |
| Larger list after owner extension/legal clearances | scales linearly | — | formula above | estimate + $100 |

The bind-time packet revision computes the REAL number from the
approved list and quotes estimate + $100 as the registry reservation.

## 3. Preconditions (mechanical, verified at bind)

1. Owner-approved language list recorded in the shared file (exact
   phrase: "approving campaign language list: <languages>").
2. Every listed language: TRAIN in the recommendation record, a wired
   `thresholds_ref` in its registry document (4 languages currently
   await owner threshold sign-off: kinyarwanda, serer, pulaar, yemba),
   and rows carrying license_policy in the training zone.
3. B6-ASR-BASE-MODEL-2026-001 published.
4. gb-zone version for training adopted per the A3/adoption machinery
   (a fresh gb3 adoption if the list extends beyond current versions;
   the byte-duplicate guard now runs at ingest).
5. Spot quota note: the filed increase (d7bef45d…) may land; spot may
   then be used OPERATIONALLY but never in cost reporting (owner rule:
   on-demand basis always).

## 4. Mutations (per run; the T5 set, already live-proven)

ECR image push if the trainer changed (scan-on-push, zero-critical
policy, waivers void when fixes exist); one CreateTrainingJob under the
pinned entrypoint/root/NVMe rules; S3 writes under
research/b5-training/<job>/** only; the T5 VPC endpoint + SG lifecycle.

## 5. The LLM variant

Remains trainer-refused until its own T5-class calibration run (owner
go required; ~$5 on-demand, ceiling $105 under the +$100 rule).
