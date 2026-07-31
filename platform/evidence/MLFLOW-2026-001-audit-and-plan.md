# MLFLOW-2026-001 — read-only audit, and how the corrected run will link

**Nothing was modified.** The historical tracking database was opened
`file:…?mode=ro`. No run, param, metric, tag or model stage was changed, and no
model has ever been registered.

## Audit of run `23868bab2d8448759fc1b9ed26156952`

| | |
|---|---|
| Status | `FINISHED` · name `lora-r32-full` |
| Params | 44 |
| Tags | 14 |
| Metric keys | `train_loss`, `steps`, `train_runtime_s`, `samples_per_second`, `gpu_peak_mb` |
| Validation / eval metrics | **none** |
| Registered models | **0** |

**The record is accurate and is not being rewritten.** It says a 600-step
optimization run completed, that the loss fell 22.53 → 4.00, and that a GPU was
used. Every one of those statements is true. What it never claimed — and what
nothing in it implies — is that the resulting model transcribes well.

That is precisely the gap: five metric keys, all describing the optimizer, none
describing the model. A reader with only this run has no way to distinguish a
successful fine-tune from one that learned a misaligned objective, because the
two look identical in `train_loss`. The failure was not that MLflow recorded
something false; it was that nothing recorded the thing that mattered.

## What the corrected run will log — prospectively, not retroactively

### Parameters (at start)

Corpus and policy: `manifest_version`, `dataset_fingerprint`,
`eligible_rows_before_exclusions`, `eligible_rows_after_exclusions`,
`sampled_rows`, `exclusions_list_id`, `exclusions_policy_sha256`,
`exclusions_removed`, `over_limit_rows_remaining`.

Pins: `image_digest`, `code_git_sha`, `code_tar_sha256`, `base_revision`,
`base_manifest_sha256`, `adapter_task_type`, `lora_rank`, `lr`, `batch_size`,
`grad_accum`, `max_steps`, `seed`, `temperature_sampling`.

Alignment proof: `decoder_start_token_id`, `label_prefix_stripped`,
`collator_asserts_prefix` — so a run that trained on a misaligned objective is
identifiable from its parameters alone, without re-reading the code.

### Metrics (per checkpoint, not only at the end)

| Metric | Why it exists |
|---|---|
| `train_loss` | retained, but no longer load-bearing |
| `val_wer`, `val_cer` | quality, on a speaker/session-disjoint set |
| `val_eos_rate` | the measurement that would have caught this failure |
| `val_cap_hit_rate` | runaway generation, directly |
| `val_generated_tokens_median`, `_max` | length distribution vs the base |
| `val_output_length_ratio_vs_base` | the proposed hard gate |
| `grad_norm` | divergence, distinct from a wrong objective |

**Checkpoint selection will be by `val_wer` with `val_eos_rate` and
`val_cap_hit_rate` as hard gates — never by `train_loss` alone.** Selecting on
training loss is what made the failed run look successful for its entire
duration.

### Tags and lineage

- `parent_failed_run = 23868bab2d8448759fc1b9ed26156952`
- `parent_failure_record = EVAL-2026-001-b4-candidate-failed.json`
- `rca = RCA-2026-001-generation-collapse.md`
- `purpose` = `promotion_grade` or `training_system_validation` (see the
  retraining packet — this is an open choice)
- `git_sha`, `git_dirty`, `provenance_source`, `image_digest`

The corrected run will be linked **forward** from the failed one by tag. The
failed run is not edited to point at its successor: amending a completed record
to reference something that did not exist when it was written is how an audit
trail stops being one.

### Evaluation artifacts

Each checkpoint evaluation writes an `evaluation.json` under
`candidates/evaluations/<run-id>/`, hashed, and its sha256 is logged as an
MLflow param. The artifact is the evidence; MLflow holds a pointer to it, not a
copy — so a metric in MLflow can always be traced to the bytes that produced it.

## What this does not do

No model is registered, no stage is set, and no promotion path is opened.
Registration remains B5 and is blocked. This document describes logging, not
approval.
