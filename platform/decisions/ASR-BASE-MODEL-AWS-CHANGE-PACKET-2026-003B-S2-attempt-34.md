# ASR base-model AWS change packet 2026-003B-S2 — full-suite shard 2, attempt 34

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-003B-S1 only, authorizing numbered attempt 34 for one non-transferable 18,000-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

## Attempt-33 terminal result

Attempt 33 refused at row 1,203 of 1,785 (whisper unconditioned, amharic
row ~132): one clip produced a capped decode without EOS and the
pilot-era termination gate raised a fatal refusal on the first flagged
row, although the aggregate schema has always carried cap_hits and
eos_failures counters designed to count exactly these events. CTC and
both omniASR-LLM passes had completed every row. Cleanup PASS with clean
deadline-action deletion; zero state independently audited; ~45 minutes
GPU (~$1) against the $10 ceiling. Evidence at commit fcd78e4.

## What changed since attempt 33 (all committed and test-locked)

1. **Termination gate corrected in the runtime image** (commit 0822ead).
   A capped or EOS-less decode on properly sized token bounds is scored
   as the model's measured failure: the truncated output is scored
   against the reference and the row carries cap_hit/eos_failure flags
   feeding the aggregate counters. A >20% flagged fraction in any
   (candidate, mode) pass of five or more rows still fails closed as a
   misconfiguration guard. Three regression tests; shard-1 results are
   unaffected (zero flagged rows).
2. **Image rebuilt at pilot-0822ead** (layer-cached; only the Python
   application layer changed). OCI index sha256:9958d0dd…, provenance
   labels verified. Local exact-image Scout scan LOCAL-SCAN-2026-005:
   0 critical, the same 4 accepted torch HIGH CVEs, nothing new; digest
   rescan bindings 2026-004; scan subject 2026-006.
   **publication_required=true** — the runner uploads only the changed
   layers in-attempt (the 7GB of CUDA/model layers are already in ECR),
   exactly the live-proven attempt-27 publication path.
3. **Everything else is unchanged from the approved attempt-33 packet**:
   shard-2 selection (amharic 0:357, row sha 2423d9c7…), bundle
   fe7f1d1a… with prestage proof-003, 18,000s window / 16,200s Job cap,
   fixtures capture 2026-003.

## Cost

Registry 030: attempt-33 reservation closed (~$1 actual, $10
conservative retained), attempt 34 reserved ($10, one active
reservation), $314.43 recognized, $85.57 headroom under the $400
aggregate ceiling.

## Boundaries unchanged

$10/attempt ceiling; 18,000s window / 16,200s Job cap (live-proven);
risk-acceptance-003; offline-only namespace, default-deny + scoped
egress; three-prefix S3 policy; VERIFY_ONLY artifact stage; 16 GiB suite
disk floor; write-once receipts and history; committed stage-1 dry
validation before execution; caffeinate -ims host guard.
