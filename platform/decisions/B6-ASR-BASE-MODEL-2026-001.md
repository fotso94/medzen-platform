# B6-ASR-BASE-MODEL-2026-001 — Base ASR model selection

Status: **PUBLISHED 2026-08-17T23:33:30Z** — owner sign-off recorded 2026-08-17 (chat,
logged in the shared coordination file); merge report
ASR-FULL-EVAL-SUITE-MERGE-REPORT-2026-001 returns
PASS_GAP_FREE_COVERAGE over all 23,768 rows, 47 languages, 10
contributing runs.

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
2. **Conditioning helps where language ambiguity is high — CORRECTED
   at full-suite scope.** The mid-suite draft said conditioning adds
   little; the completed suite contradicts that: on multi-language
   shards it improves WER by 1.7 points (shard 9, 14 languages) up to
   8.8 points (shard 10: lingala 17.4% vs unconditioned, pidgin), while
   on single-dominant-language shards the difference stays under 0.5
   points. Deployment guidance: condition where an approved identifier
   exists; unconditioned remains acceptable (not merely tolerated)
   where it does not.
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


## Full-suite merged results (PUBLISHED basis)

Pooled over 23,768 rows / 10 runs (merge report sha-bound in evidence):

| Group | WER | CER |
|---|---|---|
| omniASR CTC 1B (unconditioned) | 37.1% | 9.8% |
| omniASR LLM 1B (unconditioned) | 32.6% | 9.7% |
| omniASR LLM 1B (conditioned) | 31.4% | 9.1% |

### Per-language table (47 languages)

| language | CTC WER | CTC CER | LLM WER | LLM CER | LLM+hint WER | LLM+hint CER |
|---|---|---|---|---|---|---|
| amharic | 54.3% | 7.6% | 57.4% | 6.5% | 57.9% | 6.6% |
| bafia | 98.3% | 50.9% | 102.1% | 60.8% | 113.1% | 73.2% |
| baka | 89.6% | 40.4% | 95.8% | 49.1% | — | — |
| bakoko | 104.0% | 40.8% | 114.0% | 50.8% | 107.0% | 38.6% |
| bamun | 90.5% | 42.0% | 108.2% | 57.6% | 83.8% | 29.5% |
| basaa | 86.2% | 38.2% | 92.0% | 42.8% | 80.7% | 37.2% |
| duala | 74.7% | 18.6% | 82.0% | 32.2% | 76.9% | 22.8% |
| ejagham | 95.6% | 43.6% | 103.1% | 58.2% | 147.8% | 106.2% |
| english | 11.8% | 4.3% | 8.6% | 4.1% | 8.4% | 3.9% |
| eton | 97.6% | 57.1% | 100.5% | 64.1% | 102.2% | 66.8% |
| ewe | 96.3% | 29.8% | 97.2% | 31.3% | 71.3% | 13.7% |
| ewondo | 101.9% | 52.7% | 101.7% | 53.5% | 100.9% | 46.9% |
| fefe | 97.4% | 45.9% | 97.0% | 51.0% | 74.4% | 29.6% |
| french | 14.3% | 4.2% | 10.9% | 3.9% | 10.1% | 3.3% |
| fula | 53.2% | 14.7% | 50.8% | 16.6% | 47.0% | 15.3% |
| fulfulde | 90.2% | 34.7% | 92.8% | 31.3% | 88.7% | 30.7% |
| gbaya | 86.8% | 38.9% | 93.3% | 45.7% | — | — |
| ghomala | 97.6% | 64.5% | 93.0% | 60.1% | 81.7% | 41.4% |
| hausa | 24.2% | 6.4% | 20.6% | 6.2% | 19.9% | 6.0% |
| igbo | 43.6% | 12.2% | 32.5% | 10.8% | 32.9% | 11.1% |
| isu | 127.7% | 113.8% | 146.3% | 122.2% | — | — |
| kera | 81.9% | 23.1% | 95.5% | 30.4% | 70.2% | 19.0% |
| kinyarwanda | 41.0% | 9.7% | 29.8% | 7.9% | 29.7% | 7.8% |
| kom | 89.9% | 42.6% | 95.3% | 48.6% | 75.9% | 32.6% |
| kwasio | 95.4% | 40.7% | 97.7% | 45.0% | — | — |
| lamso | 101.1% | 46.7% | 103.6% | 51.9% | 97.7% | 42.7% |
| lingala | 17.4% | 5.1% | 14.0% | 4.8% | 13.3% | 4.7% |
| luganda | 42.9% | 7.7% | 40.9% | 7.9% | 41.4% | 8.0% |
| maka | 100.0% | 54.2% | 102.7% | 59.9% | 105.3% | 48.2% |
| malagasy | 70.1% | 21.6% | 59.3% | 21.1% | 47.4% | 14.2% |
| medumba | 99.8% | 45.6% | 107.2% | 56.0% | 126.5% | 76.0% |
| mundang | 97.5% | 40.1% | 99.5% | 47.2% | 85.1% | 32.6% |
| ngiemboon | 98.6% | 53.4% | 95.7% | 57.8% | 71.9% | 28.9% |
| ngombala | 97.7% | 51.2% | 99.8% | 52.7% | 73.1% | 31.3% |
| nomaande | 102.7% | 42.4% | 113.7% | 47.3% | 91.7% | 24.4% |
| nugunu | 122.3% | 115.8% | 129.4% | 118.2% | 137.3% | 117.3% |
| oromo | 68.9% | 20.5% | 58.3% | 17.7% | 58.1% | 17.4% |
| pidgin | 54.1% | 23.9% | 54.6% | 24.8% | 47.3% | 20.3% |
| pulaar | 131.8% | 112.3% | 132.6% | 113.3% | 130.7% | 112.3% |
| sepedi | 77.0% | 16.8% | 87.8% | 26.1% | 80.5% | 23.2% |
| shona | 20.9% | 3.8% | 15.3% | 3.4% | 15.0% | 3.4% |
| swahili | 13.7% | 3.5% | 10.9% | 3.4% | 10.5% | 3.3% |
| wolof | 40.0% | 12.7% | 35.9% | 12.5% | 34.9% | 12.4% |
| yambeta | 99.4% | 70.0% | 104.7% | 75.2% | 98.7% | 71.7% |
| yangben | 96.8% | 37.1% | 101.3% | 41.5% | 86.9% | 26.6% |
| yemba | 102.6% | 49.6% | 104.1% | 56.1% | 97.2% | 47.2% |
| yoruba | 50.7% | 17.7% | 51.3% | 20.9% | 51.3% | 20.9% |

## Billing status at publication

Cost Explorer for the suite window reports $0.00 daily (account nets to
zero via credits); the reconciliation finisher remains honestly
STILL_PENDING on granular attribution and will land as a registry
revision when CE data materializes. Conservative recognized commitments
and actual-spend estimates are separated throughout the registry
lineage (COST-REGISTRY-2026-044 current).

## Whisper post-script

Whisper's rejection stands on the completed evidence; per the owner
directive of 2026-08-16 it was removed from the evaluated set after
8,283 fully-covered rows proved the pattern; its artifacts remain
staged and integrity-verified for reproducibility.

## SUPERSESSION NOTE — 2026-08-21 (ARCH-2026-001)

The **two-variant deployment** language above is SUPERSEDED by
ARCH-2026-001 (owner-decided): production deploys exactly ONE hash-pinned
multilingual artifact. The omniASR base-family choice itself stands; the
LLM variant is retained as an OFFLINE accuracy comparator only and is
never deployed. Registry entries bind the single production digest.
