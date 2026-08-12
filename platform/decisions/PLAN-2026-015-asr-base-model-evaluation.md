# PLAN-2026-015 — Whisper versus Meta Omnilingual ASR

Status: **PROPOSED — INPUT FREEZE PASSED TWICE — CLEAN-SOURCE RUNTIME QUALIFIED — OWNER ACCEPTED EXACT FOUR-HIGH OFFLINE RISK — INDEPENDENT REVIEW AND PACKET APPROVAL PENDING — NO MODEL SCORING AUTHORIZED**

Owner purpose: evaluate the existing Whisper large-v3 base against production-realistic Meta Omnilingual ASR candidates on the expanded African-language evaluation inventory, without changing B4/B5 history, language scope, promotion gates, or production state.

This remains a separate decision track from B7, but both now share unified mainline commit `7a5040601fcd171c394aae679a9fad9d621c673b`. Nothing in this plan is AWS authorization.

## 1. Preserved boundaries

- B5 remains `BLOCKED`. The absolute WER maximum remains `0.20`; this comparison cannot reinterpret it.
- All active-training and active-validation lists remain empty. No language is reactivated by being evaluated.
- Existing B4 manifests, reports, hashes, candidates, registry artifacts, and approved-version fields remain immutable.
- Evaluation is research evidence for the next base-model choice, not a B5 PASS, model adoption, production alias change, or deployment.
- No training, checkpoint conversion, model registration, approved artifact write, production SSM write, or GPU/AWS execution is authorized here.
- Any paid compute, model mirroring, ECR push, or new AWS resource requires a versioned packet and owner approval after the inputs pass their freeze.

## 2. Bound discovery evidence

The metadata-only inventory is bound to:

- unified starting commit: `7a5040601fcd171c394aae679a9fad9d621c673b`;
- latest data-source commit: `46448c66e9068026552aa65262d689201c85fe7d`;
- source inventory: `registry/data_sources/ingest_results.yaml`;
- source inventory SHA-256: `f89b9e432a88db7eebe618c617f9c36f49fa2678b291ce16bebafa085b68c953`;
- correction record: `registry/data_sources/eval-corrections-2026-08-11.json`, SHA-256 `91da523828a9d21d69b7f01a77c1edcce49ef4c5bab708696eb4e6177a1478ad`;
- correction addendum: `registry/data_sources/eval-corrections-2026-08-11.json.note`, SHA-256 `4960d7611baf649ff4af484a1835c352ab95009ac2697268af6991d23219125f`;
- discovery record: `platform/evidence/B6-ASR-BASE-MODEL-DISCOVERY-2026-001.json`;
- discovery record SHA-256: `c5d4353b179b58d4f5c8f8770c04475ed7e2e45ef5b9518123973dc241ff930a`;
- reproduction record: `platform/evidence/B6-ASR-BASE-MODEL-INPUT-FREEZE-REPRODUCTION-2026-001.json`, SHA-256 `adba05093d830e3e6d56dec1e408a07e98a8e90074ab64aae2d93174928843b1`;
- model-source identity record: `platform/evidence/B6-ASR-BASE-MODEL-SOURCES-2026-001.json`;
- model-source identity record SHA-256: `34baae05d5bc74601a2228002fe6c2d86999fddfe1e152e49b4febf62e2817eb`;
- live read-only source prefix: `s3://medzen-speech/eval/`.

The audit observed 64 manifests, 24,230 rows, 60.829811 hours, and 49 language aliases. Excluding historical B4 `v1` and `v2-holdout` inputs leaves a prospective independent suite of 54 manifests, 23,768 rows, 58.313277 hours, and 47 language aliases. Acholi and Akan currently have no new independent evaluation version, so they cannot enter the prospective comparison merely through their historical B4 sets.

No audio was downloaded and no inference was started during discovery.

### 2.1 Passed input freeze

The audit prefers `manifest.r2.jsonl` beside a frozen original and otherwise selects `manifest.jsonl`; it never counts both. The final live re-audit selected 14 ASR r2 manifests and 50 originals. Two independent executions returned byte-identical `PASS_INPUT_FREEZE` results: canonical stdout SHA-256 `f59692a7ab5da0a9b257792e04813ec2c4c2317ffb1d68d7e5586789afa9a0ad` and normalized evidence SHA-256 `c5d4353b179b58d4f5c8f8770c04475ed7e2e45ef5b9518123973dc241ff930a` in both runs.

The two prior findings were resolved prospectively by the data-owned r2 manifests without modifying the frozen originals: Ewondo r2 SHA-256 `da26e21dbb45e4b118a6987fdc7aa63f217f0aeb4f04885c91c04243448756fd` and Gbaya r2 SHA-256 `585de6329739155aa4e87c77ef277547b884c9ec6a3e051034fedfdb14a54846`. The final selected set contains zero duplicate audio identities, zero `asr_train` permissions, zero missing tiers, and zero non-test splits.

Owner audit ruling: the evaluation boundary is the manifest namespace `eval/<language>/**`. An audio object may live elsewhere under `s3://medzen-speech/**` and remain bound by its SHA-256; object layout is not evidence of leakage. The prior 13,077-row path finding is withdrawn prospectively from this audit, not erased from its earlier evidence. Leakage is instead decided by complete train/eval checksum disjointness. The SOREVA source tally is already corrected to 39 languages / 5,483 clips on the unified mainline and is no longer an open finding.

Corrections remain prospective and content-addressed; historical B4 manifests and earlier refusal evidence remain unchanged. The passed freeze binds the complete selected inventory, exact row counts, licenses, uses, splits, source releases, and audio-object hashes. It authorizes local runtime qualification only, not scoring or AWS execution.

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
- installation identity: repository release tag `0.2.0` at the exact commit above, installed from source without dependency resolution; the package declares internal version `0.1.0`, which the image verifies at build and startup;
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

### 3.3 Local runtime qualification and security result

The local `linux/amd64` image qualifies functionally and structurally: it runs read-only as UID/GID 10001, imports the exact three adapters offline, binds the Meta source commit and internal version, and contains no compiler, source-control client, or Python package installer. The result is bound by `platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-001.json`, SHA-256 `737bb22ec59c88f6d2daf20f688bfd17021c20053711db891c2ae2a8168a86a5`. Its local-build attestation identifies pre-commit source `79d2a25f35950e74ac07bf93f909651edcaafa9c`; because the qualification changes were uncommitted at build time, this diagnostic image is explicitly ineligible for publication even apart from its scan findings.

The clean-source image at commit `bd8e14c8c4401916412b00ac899a64a03b2514ef`
passes its functional and structural qualification but reports exactly four
high findings in `torch==2.8.0+cu128` and zero critical findings. The v2 local
record is
`platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-002.json`,
SHA-256 `ee5b07d20d5eee91b3224987848c562793bff70788d6f8ca76ff3691e8a57de1`.
Updating PyArrow removed its finding. A tested upgrade to PyTorch 2.11 remains
incompatible with `fairseq2n 0.6`, which pins and enforces PyTorch 2.8; no
unsupported override is used.

The owner has prospectively accepted only these four findings for the exact
offline pilot, subject to the CVE-specific, expiring, non-precedential and
network-isolated controls in
`ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-001`. The decision does not make the
image scan-clean and does not affect the serving-image zero-critical/high
rule. Execution remains prohibited until independent review PASS, an
authoritative child scan returns zero critical plus exactly the accepted four
high tuples, and the owner uses the exact pilot approval phrase.

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

The current pilot packet is
`platform/decisions/ASR-BASE-MODEL-AWS-CHANGE-PACKET-2026-001-pilot.md`. It is
presented for independent review but remains non-executable until review PASS
and exact owner approval. Its first billable-predecessor gates require the
authoritative ECR child scan to return zero critical and exactly the four
owner-accepted high tuples, and require strict S3/ECR-only network isolation
before PyTorch starts.

## 9. Exit semantics

Planning can conclude as `BASE_MODEL_EVALUATION_INPUT_FREEZE_PASSED`.

Scoring is `AWAITING_INDEPENDENT_RISK_AND_PACKET_REVIEW` today. A later evaluation may conclude `WHISPER_RECOMMENDED`, `OMNILINGUAL_CTC_RECOMMENDED`, `OMNILINGUAL_LLM_RECOMMENDED`, `NO_CANDIDATE_ELIGIBLE`, or `OWNER_DECISION_REQUIRED`. None of those states changes B5, activates a training language, promotes a model, or authorizes production use.

## 10. Immediate next actions

1. Independently review the CVE-specific risk-acceptance record and updated pilot packet.
2. If review passes, obtain the exact owner approval phrase and publish a separate immutable signature/authorization record; do not edit the reviewed risk record.
3. Execute the packet's precompute scan stage and require an authoritative ECR child result of zero critical and exactly the accepted four highs; any drift stops before GPU.
4. Prove strict-mode no-inbound and private S3/ECR-only egress before importing PyTorch.
5. Only then may the two bounded, non-transferable pilot attempts run measured inference. A full-suite run requires a new current scan, risk decision, budget and packet.

Official references:

- Meta release: <https://ai.meta.com/blog/omnilingual-asr-advancing-automatic-speech-recognition/>
- Meta paper: <https://ai.meta.com/research/publications/omnilingual-asr-open-source-multilingual-speech-recognition-for-1600-languages/>
- Meta implementation: <https://github.com/facebookresearch/omnilingual-asr>
- Whisper pinned model: <https://huggingface.co/openai/whisper-large-v3/tree/06f233fe06e710322aca913c1bc4249a0d71fce1>
