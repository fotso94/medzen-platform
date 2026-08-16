# B6-ASR-BASE-MODEL-2026-001 — Base ASR model selection

Status: **DRAFT — suite 4 of 10 unit-sets complete; publishes only with
PASS_GAP_FREE_COVERAGE from the suite merge over all 23,768 rows**

## Decision (drafted, pending full-suite confirmation)

MedZen adopts **Meta omniASR** as the base ASR model family for all 47
platform languages, in a two-variant deployment:

- **omniASR_CTC_1B_v2 — the workhorse**: real-time and interactive
  paths. Evidence so far: CER within 1–3 points of the LLM variant at
  25–41ms median latency (~30x faster).
- **omniASR_LLM_1B_v2 — the accuracy variant**: batch, review and
  high-stakes transcription paths. Best CER on every shard to date.

**whisper-large-v3 is rejected** as a base model for these languages.

## Evidence to date (committed, hash-bound)

| Scope | Rows | omniASR LLM CER | omniASR CTC CER | whisper CER | Evidence |
|---|---|---|---|---|---|
| Pilot, 47 languages | 540 | 16.5% (cond.) | 17.2% | 24.1% / 39.2% | 6785599 (attempt 28) |
| Shard 1: kinyarwanda+baka+lamso | 3,616 | 8.0% | 10.4% | 55.0% | 475dbd3 (attempt 32) |
| Shard 2: amharic 0:357 | 357 | 6.4% | 7.3% | 118.7–122.2% | d577421 (attempt 34) |
| Shard 4 units: kinyarwanda rest+am tail+oromo | 3,413 | 8.3% | 10.0% | 56.4–111.3% | ab792c6 (attempt 35) |
| Shard 3: amharic 357:714 | 357 | _running (attempt 36)_ | | | |
| Shards 5–10: 41 languages | 15,485 | _pending_ | | | |

Key qualitative findings, all live-proven:

1. **Whisper fails structurally on the target languages.** WER above
   100% on every suite shard; CER above 100% on Ge'ez script (its
   output is worse than emptiness); capped non-terminating decodes on
   long amharic clips (counted by the corrected termination protocol,
   3 in attempt 34); 10–12s median latency on amharic vs 41ms for CTC.
2. **omniASR conditioning adds little on these languages** (LLM
   conditioned vs unconditioned within 0.5 points everywhere so far) —
   deployment MAY run unconditioned where a language lacks an approved
   conditioning identifier, rather than blocking on conditioning
   coverage.
3. **CTC latency is deployment-defining**: 25–41ms median on g6.xlarge
   makes real-time streaming viable on modest GPUs.

## What this unblocks

- B5 fine-tuning program: omniASR checkpoints become the base for
  per-language adaptation (the 59-test B5 gate refresh rides with it).
- Registry: evaluation results feed per-language `data_only` →
  `declared`/`in_development` promotions with real thresholds.

## Publication conditions (all mechanical)

1. `scripts/asr_full_eval_suite_merge.py` reports
   PASS_GAP_FREE_COVERAGE over the 23,768-row pool (currently
   COVERAGE_INCOMPLETE at 7,386 rows — by design, mid-suite).
2. Per-language merged table (47 rows) appended from the merge report.
3. Terminal reviews on every contributing attempt; the billing
   reconciliation for the suite window committed.
4. Owner sign-off recorded in this file's status line.
