# PLAN-2026-011 — controlled conversion diagnostic

Status: **OWNER APPROVED — PREPARED, NOT YET EXECUTED**  
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
