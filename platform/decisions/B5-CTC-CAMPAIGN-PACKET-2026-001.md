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

## 6. BOUND STATE (2026-08-17, owner decisions in)

- APPROVED LIST (owner, exact phrase recorded): kinyarwanda, swahili,
  ewe, fula, pulaar, french, wolof, lingala, yemba. akan/serer label
  fix explicitly deferred by owner.
- THRESHOLDS: owner-approved and wired; all 9 languages resolve.
  kinyarwanda carries an explicit 0.30 WER override (the 0.20 registry
  default was not what the owner approved — caught at wiring).
- REAL NUMBERS (from B5-TRAINING-RECOMMENDATION-2026-001): 574.3
  trainable hours -> ~154 GPU-h at the measured 7.48 audio-h/GPU-h ->
  ~$246 on-demand -> **$346 budgeted ceiling** (owner rules).
- REMAINING BEFORE LAUNCH (mechanical, in order): (1) gb3 training
  version — all 9 languages in one adopted dataset, byte-duplicate
  guard enforced, kallaama-'?' pulaar rows excluded by a recorded
  deferral policy until source repair, adoption contemplating that
  policy; (2) bindings + registry reservation at $346; (3) the standing
  per-run launch discipline (review with numbered phrase, dry
  validation, launch).

## 7. gb3 BOUND (2026-08-17/18)

Assembled, adopted, and live-validated end to end:
- 15 manifests, 9 languages, 378,047 rows; curation per the mechanical
  rules: fula dropped 514 conflicting cross-corpus pairs (both rows) +
  271 re-listings, kinyarwanda 28 pairs, ewe 1, french 39 re-listings;
  yemba inherits gb2 verbatim.
- The pulaar '?' defect is WORSE than the audit surface: generic
  normalization silently DELETES the '?' that stands in for hook
  letters, leaving clean-looking labels missing consonants. 1,579 rows
  (10.8%) stay in the manifests as factual record and are deferred at
  training by DQ-2026-006 (policy_deferral: nobody listened, nothing
  judged, promotion forbidden).
- Live gate chain PASS: exclusions applied before sampling (exactly
  1,579), licence gate excludes 32,919 sharealike rows that ride in
  fula's corpora, kinyarwanda capped at 100h, run fingerprint
  ad4960e1... (platform/evidence/B5-GB3-MIX-PROVENANCE-2026-001.json).
- COST UPDATE: with the kinyarwanda cap the effective training audio is
  ~374h -> ~100 GPU-h -> ~$160 on-demand. The $346 reservation stands
  as the conservative ceiling; the surplus releases at close.
