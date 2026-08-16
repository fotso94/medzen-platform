# ASR base-model AWS change packet 2026-003B-S3 — full-suite shard 3, attempt 35

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-003B-S1 only, authorizing numbered attempt 35 for one non-transferable 18,000-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

## Attempt-34 terminal result

Attempt 34 achieved PASS_PILOT: shard 2 (amharic, 357 rows, 1,785
row-inferences) complete, the corrected termination protocol live-proven
(3 whisper capped decodes scored and counted; run completed), and the
corrected image pilot-0822ead durably published to ECR in-attempt.
Headline: omniASR LLM CER 6.4%, CTC 7.3% at 41ms; whisper CER 118-122%
(worse than empty output) at 10-12s median. Evidence at d577421,
terminal review 027.

## Scope: shard 3 of 10 — amharic[714:739] + kinyarwanda[0:3328] + oromo[0:60]

Same machinery and window; new frozen inputs and one posture change:

1. **Selection**: 3,413 rows, row-list SHA 3bed3c85…; the driver's
   sliced image-native validation path (MEDZEN_EXPECTED_ROWS=3413,
   live-proven at shard 1's 3,616).
2. **Bundle**: identity 07ee2e4b…, 4 shard-local objects uploaded with
   the meta weights referenced from the pilot prefix; prestage proof-004
   committed with S3 checksum readback over all 9 objects, object order
   following the bundle-receipt contract; validators PASS at 18,000s.
3. **publication_required=false**: the corrected image now exists in
   ECR (live hit captured as the existing-image fixture, capture 004 of
   the read-fixture record) — no publication phase, as in attempts 28-33.
4. **Estimated job 3.58h** (kinyarwanda 3,328 rows dominate; same
   material as live-measured in attempts 31/32) inside the 16,200s cap.

## Cost

Registry 031: attempt-34 reservation closed as PASS (~$1.5 actual),
attempt 35 reserved ($10, one active reservation), $324.43 recognized,
$75.57 headroom under the $400 aggregate ceiling.

## Boundaries unchanged

$10/attempt; 18,000s window / 16,200s Job cap; risk-acceptance-003;
image digest pinned (pilot-0822ead, index sha256:9958d0dd…); offline
namespace, default-deny + scoped egress; three-prefix S3 policy;
VERIFY_ONLY artifact stage; 16 GiB suite disk floor; write-once receipts
and history; committed dry validation; caffeinate -ims host guard.
