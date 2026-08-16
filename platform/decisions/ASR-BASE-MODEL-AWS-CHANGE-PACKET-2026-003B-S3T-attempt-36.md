# ASR base-model AWS change packet 2026-003B-S3T — full-suite manifest shard 3, attempt 36

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-003B-S1 only, authorizing numbered attempt 36 for one non-transferable 18,000-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

## Attempt-35 terminal result

Attempt 35 achieved PASS_PILOT over manifest shard-4 units (13,677
row-inferences, ~50 min ahead of model; omniASR LLM CER 8.3%, whisper
56-111%; kinyarwanda fully covered across attempts 32+35). The packet
label deviation (shard-3 label over shard-4 units) is recorded in the
PASS record and terminal review 029; this packet's units are derived
from the committed manifest BY SHARD NUMBER inside the build script —
the corrective control.

## Scope: manifest shard 3 — amharic[357:714], 357 rows

Identical machinery to the approved attempt-34/35 packets; new frozen
inputs only: selection row sha a44ae91d…, bundle 4075dda2… (4 objects
uploaded, meta weights referenced), prestage proof-005 with 9-object S3
checksum readback in bundle-receipt order; fixtures capture 2026-005;
publication_required=false against the published pilot-0822ead.
Estimated job 3.58h (amharic whisper-conditioned pace as measured live
in attempt 34) inside the 16,200s cap.

## Cost

Registry 032: attempt-35 closed as PASS (~$2.5 actual), attempt 36
reserved ($10), $334.43 recognized, $65.57 headroom under $400.

## Boundaries unchanged

As the attempt-35 packet: $10/attempt; 18,000s/16,200s; risk-003;
pinned image; offline namespace; three-prefix policy; VERIFY_ONLY;
16 GiB floor; write-once; dry validation; caffeinate -ims.
