# PLAN-2026-011 — controlled conversion diagnostic

Status: **EXECUTED — PASSED ALL ACTIVE GATES**
Authorized: 2026-08-02  
Controls: `B4-SCOPE-2026-002` revision 3 and the unchanged
`A5-2026-001` gate matrix.

## Purpose

Attempt 4 proved that the complete CTranslate2 `int8_float16` serving path did
not retain the required Lingala gain.  It did **not** isolate whether the loss
came from LoRA merge, export/runtime behaviour or quantization.  This attempt
uses the already-selected, hash-pinned checkpoint 400 and performs zero
training steps.

## Fixed arms and order

The frozen Lingala/Luganda/Oromo selection manifests are scored in this order
with identical prompts, greedy decoding, normalization, token cap and gates:

1. merged Transformers/PyTorch float16;
2. CTranslate2 float16;
3. CTranslate2 int8_float16.

Every arm and gate is written once to the attempt-scoped S3 prefix immediately
after measurement.  Evidence contains aggregate metrics and checksum-only
numeric row records; it contains no transcript, audio, speaker, session or
signed URL.

## Predeclared selection and holdout rule

The untouched Lingala holdout is not opened while precision is selected.
`int8_float16` is preferred when it passes the unchanged 15% per-language WER
gain and termination gates.  If it fails and float16 passes, float16 is the
owner-approved non-promotable B4 fallback only when it also loads and completes
scoring on the authorized g6.xlarge L4 without OOM.  Artifact bytes and
per-language latency are reported.

Only the selected CTranslate2 precision is then evaluated on the 77-row
speaker/session/text-disjoint Lingala holdout.  Its base comparison is converted
and scored with the same CTranslate2 precision.  A holdout failure refuses the
artifact.  If neither CTranslate2 precision passes selection, no holdout is
read and no servable artifact is published.

## Non-outcomes

This diagnostic does not weaken a threshold, prove quantization causality,
register a model, permit promotion or unblock B5.  Spot interruption/resume is
allowed only after a converted artifact passes every active selection and
holdout gate.

## Executed outcome

Attempt 5 executed from checkpoint 400 with zero training steps. Merged
PyTorch float16 and CTranslate2 float16 passed all active selection gates.
CTranslate2 `int8_float16` failed the unchanged Lingala gain gate, isolating
the loss to that quantized serving arm rather than merge or CTranslate2 export.
The predeclared fallback selected CTranslate2 float16.

On the untouched 77-row Lingala holdout, the selected artifact scored WER
0.5996 against 0.7558 for the same-precision CTranslate2 base, a 20.67%
relative gain. Both arms emitted EOS on every row and had zero cap hits. The
float16 artifact was published under the immutable attempt-5 prefix. It
remains non-promotable, no model was registered, and B5 remains blocked.

The durable completion record is
`platform/evidence/CAMPAIGNRUN-2026-013-passed.json`.
