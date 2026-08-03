# PLAN-2026-005 — Amharic data/model compatibility investigation

**Status: PHASE A COMPLETE; HUMAN REVIEW AND INDEPENDENT-SOURCE GATES OPEN;
NO TRAINING OR GPU LAUNCH IS AUTHORISED.**

PLAN-2026-004 completed successfully but selected no decode strategy. Greedy,
Whisper fallback and five-beam decoding all failed the predeclared termination
and quality gates. This plan follows the required branch: determine whether
the remaining failure is primarily data/source compatibility, tokenizer
fragmentation, evaluation-domain mismatch or base-model behavior before any
new optimizer experiment.

## Facts already established

- The training pool has 275 Amharic rows from one source,
  `waxalnlp/digital_umuganda`; four high-token-count rows are deferred and have
  never received human review, leaving 271 eligible for the Option B run.
- Amharic averages 16.806 effective Whisper tokens per second. The next-highest
  language average is 7.217. This 2.33x difference is tokenizer fragmentation
  pressure, not by itself proof that a label is wrong.
- The frozen 25-row set is long and token-dense: 15.144–24.216 seconds and
  247–419 effective model-label tokens. Every target fits the model and
  generation limits and contains the correct prompt and EOS target.
- The untouched large-v3 base is poor in both the historical MLX native run
  (WER 1.0000) and the pinned Transformers greedy run (WER 1.0565). A
  Transformers-only failure is therefore not established.
- Whisper fallback helps termination and repetition but still fails, so it is
  evidence for the investigation rather than an authorized decode contract.

The aggregate source record is
`platform/evidence/COMPAT-2026-001-amharic-preaudit.json`.

## Phase A — aggregate data-contract audit

Implement one read-only tool that streams the pinned train and evaluation
manifests without persisting their rows and emits only aggregate values:

1. re-verify manifest SHA-256, source revision, allowed-use and the approved
   four-row deferral binding;
2. prove zero audio-checksum, speaker and session overlap between train and
   evaluation;
3. compare duration, effective label tokens, tokens/second, tokens/character,
   normalized-empty rate and Unicode-script proportions;
4. compare speaker/session counts and concentration without emitting an ID;
5. report whether the frozen evaluation is distributionally outside the
   eligible training pool;
6. inspect audio only through aggregate signal statistics: decode failures,
   sample rate/channels, RMS, near-silence, clipping and DC offset. Do not save,
   play or emit audio, URI, transcript, checksum or row-level values.

Phase A may use read-only S3 access. It may write only a versioned aggregate
evidence record in the repository. It creates no AWS resource and changes no
dataset, policy, candidate, registry or MLflow run.

## Human-review gate

A qualified Amharic reviewer must listen to the four deferred rows plus a
predeclared stratified sample spanning low/median/high token density in both
eligible training and frozen evaluation. The reviewer records classifications
and reason codes through the existing DQ review contract; private text or audio
does not enter a report or model prompt.

Until that review is complete, the four rows remain excluded and no claim may
be made that they are defective or valid. If systematic transcription,
language, alignment or audio defects are found, remediate and version the data
before any model experiment.

## Independent-source gate

The current train and validation evidence comes from one provider/domain. Add
or collect a small, licensed, speaker/session-disjoint Amharic evaluation set
from an independent source. Freeze its manifest and normalization before model
execution. Score the untouched pinned large-v3 base first using greedy and the
already-frozen fallback contract.

- If the base terminates on the independent source but not Digital Umuganda,
  treat this as data/domain compatibility and do not change training loss yet.
- If it fails comparably on both, propose a separate architecture/model
  compatibility decision; do not silently replace the Base v5 model.
- If the base passes and the adapter regresses, only then predeclare one
  exposure-bias mitigation experiment with the independent set kept out of
  selection.

## Stop rules

- No sweep, checkpoint selection, model registration, merge, conversion,
  promotion, deployment or B5 work under this plan.
- Do not increase the token cap, add repetition penalties, weaken the Amharic
  gate, or drop Amharic to manufacture a pass.
- Do not use the informed 25-row frozen set as an untouched holdout.
- Any later GPU experiment requires a new immutable plan, bundle, image scan,
  cost ledger and explicit execution packet.

## Exit

This plan exits only with (a) an aggregate Phase A evidence record, (b) the
qualified human-review outcome, and (c) a frozen independent-source evaluation
contract. It does not complete B4. B5 remains blocked.

## Phase A outcome

The read-only audit completed as
`platform/evidence/COMPAT-2026-002-amharic-aggregate-audit.json`. It streamed
271 eligible training and 25 evaluation audio objects, persisted no private
content, and created or modified no AWS resource.

The manifests, checksums, split, speaker/session disjointness, normalization,
audio format, durations and basic signal properties all passed. Evaluation is
modestly more token-dense (18.119 versus 16.713 effective tokens/second) but
remains within the eligible training range. Both sets require about three
Whisper tokens per Ethiopic letter, which confirms tokenizer fragmentation
pressure without establishing a bad transcript or model defect.

Phase A therefore does not authorize a model experiment. The open work is now
human rather than computational: qualified Amharic review and an independent,
licensed, frozen evaluation source. No additional AWS compute is justified
until those gates close.
