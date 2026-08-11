# PLAN-2026-015 — Whisper versus Meta Omnilingual ASR

Status: **PROPOSED — INPUT FREEZE REFUSED ON TWO IDENTITIES — NO MODEL SCORING AUTHORIZED**

Owner purpose: evaluate the existing Whisper large-v3 base against production-realistic Meta Omnilingual ASR candidates on the expanded African-language evaluation inventory, without changing B4/B5 history, language scope, promotion gates, or production state.

This remains a separate decision track from B7, but both now share unified mainline commit `e04a4140491d7a5d0a389403bcc3c20eed3ca713`. Nothing in this plan is AWS authorization.

## 1. Preserved boundaries

- B5 remains `BLOCKED`. The absolute WER maximum remains `0.20`; this comparison cannot reinterpret it.
- All active-training and active-validation lists remain empty. No language is reactivated by being evaluated.
- Existing B4 manifests, reports, hashes, candidates, registry artifacts, and approved-version fields remain immutable.
- Evaluation is research evidence for the next base-model choice, not a B5 PASS, model adoption, production alias change, or deployment.
- No training, checkpoint conversion, model registration, approved artifact write, production SSM write, or GPU/AWS execution is authorized here.
- Any paid compute, model mirroring, ECR push, or new AWS resource requires a versioned packet and owner approval after the inputs pass their freeze.

## 2. Bound discovery evidence

The metadata-only inventory is bound to:

- unified starting commit: `e04a4140491d7a5d0a389403bcc3c20eed3ca713`;
- latest data-source commit: `2906ddd24e16f2e2c30d9ecd324e47733ba6ac78`;
- source inventory: `registry/data_sources/ingest_results.yaml`;
- source inventory SHA-256: `f89b9e432a88db7eebe618c617f9c36f49fa2678b291ce16bebafa085b68c953`;
- correction record: `registry/data_sources/eval-corrections-2026-08-11.json`, SHA-256 `91da523828a9d21d69b7f01a77c1edcce49ef4c5bab708696eb4e6177a1478ad`;
- discovery record: `platform/evidence/B6-ASR-BASE-MODEL-DISCOVERY-2026-001.json`;
- discovery record SHA-256: `dd6ca577b0394fae5ea274ac9da631b3d6e4ab7a5a8af748e876e4763be29d06`;
- model-source identity record: `platform/evidence/B6-ASR-BASE-MODEL-SOURCES-2026-001.json`;
- model-source identity record SHA-256: `adb88498d996ccafd7cb42beb2c72780d2593ca5b0e8bcd5d793b09be46c2794`;
- live read-only source prefix: `s3://medzen-speech/eval/`.

The audit observed 64 manifests, 24,232 rows, 60.830514 hours, and 49 language aliases. Excluding historical B4 `v1` and `v2-holdout` inputs leaves a prospective independent suite of 54 manifests, 23,770 rows, 58.313980 hours, and 47 language aliases. Acholi and Akan currently have no new independent evaluation version, so they cannot enter the prospective comparison merely through their historical B4 sets.

No audio was downloaded and no inference was started during discovery.

### 2.1 Current refusal

The audit prefers `manifest.r2.jsonl` beside a frozen original and otherwise selects `manifest.jsonl`; it never counts both. The live re-audit selected 12 ASR r2 manifests and 52 originals. The remaining input-freeze state is `REFUSED_INPUT_FREEZE` on exactly two intra-manifest identities:

- Ewondo SOREVA SHA-256 `6c472e2ab556b66022b048165929c7a9a6a0ff67d2b0370a5b17d1e1255a4d94`, first at row 111 and duplicated at row 112;
- Gbaya SOREVA SHA-256 `4dc52a35b08269e38f8c54627c09f0292bdf098ed6fbb19f0d7eee3d6e23bb3d`, first at row 4 and duplicated at row 15.

The r2 manifests cleared all permission, license-tier, and split findings: `asr_train` rows in the selected evaluation set are zero, missing tiers are zero, and non-test splits are zero. The correction record's `dupes: 0` describes its cross-manifest check; the two findings above are repeated rows inside individual SOREVA manifests and therefore remain visible.

Owner audit ruling: the evaluation boundary is the manifest namespace `eval/<language>/**`. An audio object may live elsewhere under `s3://medzen-speech/**` and remain bound by its SHA-256; object layout is not evidence of leakage. The prior 13,077-row path finding is withdrawn prospectively from this audit, not erased from its earlier evidence. Leakage is instead decided by complete train/eval checksum disjointness. The SOREVA source tally is already corrected to 39 languages / 5,483 clips on the unified mainline and is no longer an open finding.

Corrections must be prospective and content-addressed. They must not overwrite the historical B4 manifests or silently discard duplicates. The corrected freeze must prove disjoint train/evaluation audio checksums across the complete adopted inventory, exact row counts, licenses, uses, splits, source releases, and audio-object hashes.

## 3. Candidates and provenance

### 3.1 Operational control — Whisper

- model: `openai/whisper-large-v3`;
- revision: `06f233fe06e710322aca913c1bc4249a0d71fce1`;
- license: Apache-2.0;
- role: the exact historical control, not a newly selected winner;
- existing serving evidence: B6A proved the zero-shot CTranslate2 float16 artifact could load and transcribe on L4;
- existing B6A resource observation: 4,180 MiB peak used, 3,988 MiB baseline, 23,034 MiB device total, 14 samples;
- existing artifact tree SHA-256: `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e`.

The B6A memory sample is an operational control only. It is not a full-suite resource comparison and must not be reused as Meta's measurement.

### 3.2 Meta candidates

The primary Meta screen contains two v2 1B candidates:

1. `omniASR_CTC_1B_v2` — 975,065,300 parameters, official FP32 download about 3.7 GiB, official A100 inference estimate about 3 GiB, unconditioned CTC decoding.
2. `omniASR_LLM_1B_v2` — 2,275,710,592 parameters, official FP32 download about 8.5 GiB, official A100 inference estimate about 6 GiB, audio-only or exact language-conditioned decoding.

Official source bindings discovered before model download:

- repository: `facebookresearch/omnilingual-asr`, release tag `0.2.0`, commit `145a12a668aace6c1d0d290128c1225571fc1955`;
- package: `omnilingual-asr==0.2.0`, wheel SHA-256 `6b8e811143603463c371c23464ff1946a52f876e6b6a62c5fb3deee6e39ab6d4`;
- v2 asset-card SHA-256: `af4d63febb0569831210e470b256ec70dc3a55065756c21c1f514d0001f283ed`;
- supported-language source SHA-256: `675b8a263aed48269020d4e9f06b3063d5b4e0d5399b2c3e0e06160e08d24f8e`;
- code and released models: Apache-2.0.

Checkpoint URLs and current observed object metadata:

| Candidate | URL | Content length | Object-version reference | Checkpoint SHA-256 |
|---|---|---:|---|---|
| CTC 1B v2 | `https://dl.fbaipublicfiles.com/mms/omniASR-CTC-1B-v2.pt` | 3,902,956,068 bytes | `VeYX5LLhqf9GXPIO7.2bDkHC4jYppX9F` | `354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c` |
| LLM 1B v2 | `https://dl.fbaipublicfiles.com/mms/omniASR-LLM-1B-v2.pt` | 9,118,733,852 bytes | `PL0knnqDMbS.sgl4wX4VZqXLrc8U3B7f` | `cceb4d9ebac3d168a6af6b26c62ce11bafc562b38976c6bfa87e7d60422c6da5` |

The shared v2 written tokenizer SHA-256 is `8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e`. These identities were measured from the official release objects using read-only HTTPS and exact byte-length verification; no model was retained in the repository and no inference occurred. An HTTP ETag, including a multipart ETag, is not a SHA-256. Each checkpoint and tokenizer must still be mirrored create-only into a controlled, non-serving staging area, verified by read-back, scanned with the evaluation image, and bound before compute.

`omniASR_LLM_7B_ZS` is excluded from the first comparison: it expects one to ten in-context audio/transcript examples, is roughly 30 GiB to download, and has an official A100 inference estimate near 20 GiB. That is not the same zero-shot operating condition and is a poor first fit for the existing 23,034 MiB L4.

## 4. Preliminary compatibility result

This is coverage analysis, not accuracy evidence:

- Whisper has exact built-in language tokens for 9 of the 49 discovered aliases: English, French, Amharic, Hausa, Lingala, Malagasy, Shona, Swahili, and Yoruba.
- Meta's published language-ID list has an exact reviewed identifier for 45 of 49 discovered aliases.
- The four aliases without a presently verified exact Meta identifier are Baka, Gbaya, Isu, and Kwasio.
- No proxy token may be invented. An alias without an exact, reviewed mapping remains unconditioned and is reported as such.

This makes Meta the stronger coverage hypothesis, while Whisper remains the stronger already-proven runtime control. Neither is the quality winner until WER/CER, reliability, latency, memory, and integration results are measured from the corrected frozen suite.

## 5. Evaluation protocol

### Phase 0 — repair and freeze, no GPU

1. Publish corrected versioned manifests and an adoption record; never mutate historical versions.
2. Prove train/eval checksum disjointness and resolve both SOREVA duplicates explicitly.
3. Re-run `scripts/audit_asr_base_model_eval_inputs.py` twice and require byte-identical `PASS_INPUT_FREEZE` records.
4. Review every alias-to-language-ID mapping and bind it by hash.
5. Build a deterministic evaluation bundle binding code, container, normalizer, manifests, object hashes, candidate checkpoints/tokenizers, and decode settings.

### Phase 1 — deterministic pilot

After a separate execution packet, select the first 10 rows per manifest by audio SHA-256 after the corrected global disjointness check. This caps the pilot at 540 rows for the present 54-manifest prospective suite while retaining every source/language slice.

Run all candidates on exactly the same pilot rows:

- mode A, comparable primary: unconditioned/audio-only inference for Whisper, Meta CTC, and Meta LLM;
- mode B, secondary: exact language-conditioned inference for Whisper and Meta LLM only where the model has an exact reviewed identifier;
- Meta CTC is reported `NOT_APPLICABLE` for conditioned mode;
- no prompts, context examples, proxy languages, per-language tuning, or outcome-informed decode changes.

A malformed result, missing prediction, provenance mismatch, cap/EOS failure, or out-of-memory condition fails that candidate/mode closed and remains visible.

### Phase 2 — full prospective suite

Only candidates that complete the pilot may run the entire corrected prospective suite. The full run order must be randomized deterministically and interleaved by model to reduce time/thermal bias. Run identity, resume behavior, raw predictions, timings, and resource samples must be persisted per stage.

### Phase 3 — decision

Produce `B6-ASR-BASE-MODEL-2026-001` with immutable per-row predictions and per-language/source results. The decision must preserve a Pareto result if one model wins quality but loses serving fit; it must not manufacture one blended score after observing results.

## 6. Required measurements

For every candidate and applicable decode mode:

- word error rate (WER), using the already versioned MedZen normalizer;
- character error rate (CER), especially for low-resource and tokenization-sensitive languages;
- micro totals and language-macro averages, plus per-language and per-source values;
- number of per-language wins, ties, missing outputs, errors, EOS/cap failures, and language-ID errors;
- median, p95, and real-time factor on identical hardware;
- numeric peak L4 GPU memory, idle baseline, sample count, and sampler provenance;
- checkpoint/tokenizer/artifact bytes and hashes;
- cold-start/load time, runtime/container dependency surface, and scan results;
- support for the B6 file and streaming contracts;
- training/fine-tuning recipe maturity, checkpoint/resume feasibility, and estimated integration effort;
- license and attribution obligations.

The original `absolute_wer_max: 0.20` remains the production gate. A model may be recommended as the better future training base while still being explicitly ineligible for promotion.

## 7. Prospective selection rules

The review must be decided in this order:

1. provenance and evaluation completeness;
2. no unresolved termination, missing-output, or resource-fit failure;
3. lower language-macro WER in unconditioned mode;
4. language-macro CER and per-language win distribution as the quality tie-breakers;
5. English/French replay behavior and code-switch limitations stated separately, never hidden in an aggregate;
6. measured L4 headroom, latency, runtime compatibility, security surface, and future fine-tuning cost.

If the criteria conflict materially, the result is `OWNER_DECISION_REQUIRED`, with the Pareto trade-off shown. Conditioned results may inform routing design but cannot replace the common unconditioned comparison.

## 8. Execution packet required

After `PASS_INPUT_FREEZE`, present a small pilot packet binding:

- the unified starting Git commit and clean worktree;
- exact corrected manifest/adoption hashes and audio-object verification result;
- all checkpoint, tokenizer, package, image, and code hashes;
- exact candidate/mode matrix and deterministic pilot row list;
- L4 instance/node type, scale-to-zero guarantee, deadline-first cleanup, and sampler self-test;
- a reconciled cost-registry revision, reservation, maximum GPU seconds, and maximum object-transfer/storage cost;
- receipt-per-stage behavior, failure injections, and no-PHI/synthetic diagnostic rules;
- zero writes to `approved/asr/`, production SSM, MLflow registry, or language approval fields.

The packet must stop before the full suite. The full run needs a second authorization based on pilot evidence and a new cost estimate.

The current draft pilot packet is `platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-001-pilot.md`. It is deliberately non-executable while the two duplicate identities remain and while the evaluation image has no scan-passed child digest.

## 9. Exit semantics

Planning can conclude as `BASE_MODEL_EVALUATION_READY_FOR_INPUT_REPAIR`.

Scoring remains `BLOCKED_INPUT_FREEZE` today. A later evaluation may conclude `WHISPER_RECOMMENDED`, `OMNILINGUAL_CTC_RECOMMENDED`, `OMNILINGUAL_LLM_RECOMMENDED`, `NO_CANDIDATE_ELIGIBLE`, or `OWNER_DECISION_REQUIRED`. None of those states changes B5, activates a training language, promotes a model, or authorizes production use.

## 10. Immediate next actions

1. Independently review this plan and the discovery record.
2. The data engineer publishes Ewondo and Gbaya SOREVA r2 manifests that resolve the named intra-manifest identities without mutating the originals.
3. Require a deterministic `PASS_INPUT_FREEZE` re-audit using r2 preference.
4. Prepare the local evaluation harness and fixtures without model downloads or AWS calls.
5. Reconcile cost, present the pilot packet, and only then run measured inference.

Official references:

- Meta release: <https://ai.meta.com/blog/omnilingual-asr-advancing-automatic-speech-recognition/>
- Meta paper: <https://ai.meta.com/research/publications/omnilingual-asr-open-source-multilingual-speech-recognition-for-1600-languages/>
- Meta implementation: <https://github.com/facebookresearch/omnilingual-asr>
- Whisper pinned model: <https://huggingface.co/openai/whisper-large-v3/tree/06f233fe06e710322aca913c1bc4249a0d71fce1>
