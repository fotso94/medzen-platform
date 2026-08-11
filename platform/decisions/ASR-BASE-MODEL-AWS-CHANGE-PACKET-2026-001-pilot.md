# ASR base-model AWS change packet 2026-001 — deterministic three-model pilot

Status: **DRAFT — INPUT FREEZE PASSED — BLOCKED_LOCAL_IMAGE_SCAN — NOT EXECUTABLE — NO APPROVAL REQUESTED**

The approval phrase is intentionally unavailable. It may be added only after
the passed input freeze and local qualification evidence are bound, an authoritative
ECR scan passes, the packet is rebound to the resulting immutable identities,
and independent review passes.

## Purpose

Run one bounded research pilot comparing the existing Whisper large-v3
operational control with Meta Omnilingual ASR CTC-1B-v2 and LLM-1B-v2 on the
expanded prospective evaluation suite. This is a base-model decision aid. It
is not training, B5 promotion, model adoption, serving publication or a
production deployment.

## Input-freeze result

Stage 0 remains mandatory and runs before model download, ECR push, worker
scale-up or inference. It must select `manifest.r2.jsonl` beside a
frozen `manifest.jsonl` when r2 exists, otherwise select the original, and
must never count both.

Two fresh executions produced byte-identical `PASS_INPUT_FREEZE` records:
canonical stdout SHA-256 `f59692a7ab5da0a9b257792e04813ec2c4c2317ffb1d68d7e5586789afa9a0ad`
and normalized evidence SHA-256
`c5d4353b179b58d4f5c8f8770c04475ed7e2e45ef5b9518123973dc241ff930a`
in both runs. The selected 14 r2 and 50 original manifests contain 24,230
rows across 49 languages, with zero duplicate identities, zero `asr_train`
permissions, zero missing tiers and zero non-test splits.

The owner ruling defines the evaluation boundary as manifest
namespace `eval/<language>/**`; audio object layout is not a leakage signal,
but each object must remain under `s3://medzen-speech/**` and match its bound
SHA-256. In short, the owner ruling is manifest namespace
`eval/<language>/**`; an orphan r2, missing provenance or hash mismatch
refuses.

## Immutable planning bindings

| Binding | Exact value |
|---|---|
| Unified master | `7a5040601fcd171c394aae679a9fad9d621c673b` |
| Data correction commit | `46448c66e9068026552aa65262d689201c85fe7d` |
| Data inventory SHA-256 | `f89b9e432a88db7eebe618c617f9c36f49fa2678b291ce16bebafa085b68c953` |
| Correction record SHA-256 | `91da523828a9d21d69b7f01a77c1edcce49ef4c5bab708696eb4e6177a1478ad` |
| Correction addendum SHA-256 | `4960d7611baf649ff4af484a1835c352ab95009ac2697268af6991d23219125f` |
| PASS audit SHA-256 | `c5d4353b179b58d4f5c8f8770c04475ed7e2e45ef5b9518123973dc241ff930a` |
| Audit-reproduction record SHA-256 | `adba05093d830e3e6d56dec1e408a07e98a8e90074ab64aae2d93174928843b1` |
| Model-source record SHA-256 | `34baae05d5bc74601a2228002fe6c2d86999fddfe1e152e49b4febf62e2817eb` |
| Cost registry | `COST-REGISTRY-2026-006`, SHA-256 `d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da` |

The executable successor must additionally bind a clean source commit, the
two PASS audit hashes, deterministic pilot-row manifest, evaluation bundle,
container image, scan-passed `linux/amd64` child digest, normalizer, decode
configuration and executable receipt runner. None is implied by this draft.

## Local runtime result and current refusal

Local functional and hardening qualification passed for image index
`sha256:5249b40dac223d8394718b9b75f04810636fd987f323bb9b2c0cf2128580c2a1`
and its `linux/amd64` manifest
`sha256:0ebf1cc58ae0a82bd3a57435a3393f84528b651ff3aafe7ab7c516553003c7b9`.
The evidence record is
`platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-001.json`,
SHA-256 `737bb22ec59c88f6d2daf20f688bfd17021c20053711db891c2ae2a8168a86a5`.
The local-build attestation points to pre-commit source `79d2a25f35950e74ac07bf93f909651edcaafa9c`;
the diagnostic image is therefore publication-ineligible even apart from its
scan findings and may not be reused as a packet-bound artifact.

The mandatory local scan refused the image: 0 critical and 4 high findings,
all in the Meta-required `torch==2.8.0+cu128`. The PyArrow upgrade removed
the fifth high finding. A no-waiver upgrade to PyTorch 2.11 was tested and
failed before image publication because `fairseq2n 0.6` pins and enforces
PyTorch 2.8 at import time. No unsupported runtime override is accepted.

This refusal occurs before any ECR push, authoritative scan, model download,
AWS mutation, GPU start, or inference. The pilot remains locked until a
scan-clean upstream-compatible Meta/fairseq2 runtime exists or a separate
owner decision authorizes an independently qualified source-built path.

## Exact candidate identities

| Candidate | Immutable identity | Pilot role |
|---|---|---|
| Whisper large-v3 | base revision `06f233fe06e710322aca913c1bc4249a0d71fce1`; existing CT2 float16 tree `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e` | operational control |
| Meta CTC-1B-v2 | checkpoint SHA-256 `354f981756aa8f41591ea363e45b9c4eba1ec5144c2273af82e747efbb08919c`; 3,902,956,068 bytes | unconditioned comparison |
| Meta LLM-1B-v2 | checkpoint SHA-256 `cceb4d9ebac3d168a6af6b26c62ce11bafc562b38976c6bfa87e7d60422c6da5`; 9,118,733,852 bytes | unconditioned and exact-ID comparison |
| Meta written tokenizer v2 | SHA-256 `8aa11a1092142ef472537476ef6e76541123e2f0d789b79f3ebd119008240b1e` | shared Meta tokenizer |

Meta source code is `facebookresearch/omnilingual-asr` tag `0.2.0`, commit
`145a12a668aace6c1d0d290128c1225571fc1955`, installed from that exact source
without dependency resolution. The bound release declares internal package
version `0.1.0`, which the image verifies at build and startup.
No HTTP ETag is accepted as a SHA-256.

## Required local and scan-only predecessors

Before this becomes executable:

1. build the deterministic evaluation harness and recorded fixtures locally;
2. prove row selection, normalization, decoding, per-row receipt durability,
   resume idempotency, cap/EOS classification and numeric GPU sampling with
   failure injections;
3. build a minimal runtime image using pinned dependencies and no compilers,
   source-control clients, installers or Python package managers in the final stage;
4. run the real container read-only as its non-root user and prove imports,
   exact source identity, candidate/mode dispatch, immutable receipts and
   fail-closed input validation; model-backed inference remains inside the
   separately authorized pilot because the multi-gigabyte weights are not
   downloaded during local qualification;
5. present a separate scan-only packet, push exactly one immutable tag to an
   existing or separately authorized ECR repository, query the platform child
   digest, and require `COMPLETE` with zero critical/high findings; and
6. run a cold rehearsal that proves Stage 0 refusal prevents every AWS
   mutation and that deadline-first cleanup returns worker desired size to 0.

No vulnerability waiver is permitted for this pilot.

## Deterministic pilot dataset

After complete adopted train/eval disjointness, sort eligible rows within each
prospective manifest by audio SHA-256 and select the first 10. For the current
54-manifest prospective inventory, the hard maximum is 540 distinct rows. The
executable row-list artifact must bind manifest SHA, language,
source, audio URI, audio SHA, duration, normalized reference and selection
ordinal. Duplicate audio identities are prohibited globally.

Every candidate receives exactly the same ordered rows:

- primary mode: unconditioned/audio-only for Whisper, Meta CTC and Meta LLM;
- secondary mode: exact reviewed language conditioning for Whisper and Meta
  LLM where the model has an exact identifier;
- Meta CTC conditioned mode: `NOT_APPLICABLE`;
- no prompts, context examples, proxy IDs, per-language tuning or
  outcome-informed decode change.

Missing output, provenance mismatch, OOM, malformed prediction, decode cap or
termination failure remains visible and fails the affected candidate/mode
closed. It may not be silently retried with a changed strategy.

## Proposed execution stages

1. **deadline_and_identity** — persist the deadline and refusal receipt before
   all AWS mutation; verify exact account, region, clean commit and packet.
2. **input_freeze** — reproduce both byte-identical PASS audit records and the
   exact deterministic pilot row list. A refusal ends the packet at zero GPU.
3. **cost_and_zero_state** — require a new reconciled cost-registry revision,
   the approved reservation, CPU desired 0, GPU desired 0, and no other active
   billable packet.
4. **artifact_stage** — create-only mirror the two exact Meta checkpoints and
   tokenizer beneath a non-serving content-addressed research prefix; verify
   byte length and SHA by read-back. The existing Whisper artifact is
   read-only. No `approved/asr/` object is touched.
5. **image_and_scan** — reverify the exact scan-passed child and package/model
   bindings. Any drift refuses.
6. **sampler_self_test** — after one L4 worker registers, require 120 numeric
   samples from the exact execution context before model evaluation.
7. **pilot_rows** — execute all modes with receipt-per-row durability and
   deterministic resume; raw predictions are immutable research evidence.
8. **aggregate_report** — calculate WER/CER micro totals, language-macro and
   per-language/source values; errors, missing outputs, EOS/caps; median, p95,
   real-time factor, load time, peak/baseline/sample-count GPU memory; bytes,
   scan state and runtime compatibility.
9. **cleanup** — scale GPU desired size to 0 even after refusal or interruption,
   verify zero workers, close the attempt receipt and reconcile actual cost.

Receipt-per-stage is structural: PASS and REFUSED receipts are written and
fsync'd immediately. Pre-audio stages may retain bounded raw diagnostics; after
audio is available, diagnostics are sanitized and contain no audio or
transcript bodies.

## Proposed capacity and reservation request

The latest reconciled registry records `$74.4286064216` committed, `$0` active
reservations and `$225.5713935784` headroom under the `$300` ceiling. The
read-only AWS Price List observation for `g6.xlarge` Linux on-demand in
`eu-central-1`, effective 2026-08-01, is `$1.0064/hour` (SKU
`YM9WN4QE9HEHCXKP`).

The executable successor may request exactly:

- one new `$10.00` conservative reservation;
- at most two non-transferable attempts of 10,800 seconds each;
- one existing `g6.xlarge` worker maximum, no CPU worker requirement;
- maximum GPU compute: 21,600 seconds = 6 hours = `$6.0384` at the recorded
  rate; and
- the remaining `$3.9616` as a hard ceiling for ECR scanning/storage, S3
  staging/storage and API overhead, not a claim that those services are free.

Unused seconds cannot move between attempts. A third attempt, longer window,
additional instance, reservation increase or resource creation requires a new
packet and owner decision. The reservation itself is not AWS authorization.

## Deterministic outcomes

- `BLOCKED_INPUT_FREEZE`: input evidence is incomplete or fails; no compute is allowed.
- `BLOCKED_LOCAL_QUALIFICATION`: harness or functional qualification is incomplete.
- `BLOCKED_LOCAL_IMAGE_SCAN`: current outcome; an unwaived critical/high
  finding prevents ECR publication and compute.
- `PASS_PILOT`: all required candidate/mode rows and measurements complete.
- `INCOMPLETE_MEASUREMENT`: durable completed row receipts remain valid, but a
  required aggregate/resource measurement is missing.
- `FAILED_CLOSED_EXECUTION`: identity, provenance, cost, deadline, cleanup or
  evidence integrity cannot be proven.

`PASS_PILOT` authorizes only preparation of a full-suite packet. It does not
declare a winning model, pass B5, reactivate a language, or authorize training.

## Prohibited operations

- Training, fine-tuning, conversion, registry registration or MLflow stage
  transition.
- Writes to `approved/asr/`, production SSM, language `artifact`, or language `approved_version`.
- Serving-alias changes or deployments.
- Overwriting frozen manifests, originals, staged model objects or evidence.
- Proxy-language conditioning, prompt examples, hidden retries, threshold
  changes, outcome-informed decode changes or full-suite scoring.
- Any AWS operation before the exact executable successor is independently
  reviewed and approved with its then-published exact approval phrase.

## Next action

Resolve the Meta/PyTorch compatibility blocker without a vulnerability waiver,
then rebuild and require a zero-critical/high local scan. Only after that may
a separate authoritative ECR scan-only packet be presented. This pilot draft
is not an owner approval request.
