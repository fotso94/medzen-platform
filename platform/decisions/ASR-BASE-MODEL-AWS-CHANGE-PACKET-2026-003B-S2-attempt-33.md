# ASR base-model AWS change packet 2026-003B-S2 — full-suite shard 2, attempt 33

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-003B-S1 only, authorizing numbered attempt 33 for one non-transferable 18,000-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

## Attempt-32 terminal result

Attempt 32 achieved PASS_PILOT: the first complete suite shard (3,616
rows, 14,464 row-inferences) inside the bindings-bound 16,200s Job cap
with ~100 minutes of margin, cleanup PASS including clean deadline-action
deletion, zero state independently audited, evidence at commit 475dbd3.
The window parameterization and the v4 measured-rate manifest are both
now LIVE-PROVEN. Headline: omniASR LLM conditioned CER 8.0%/WER 30.1%,
CTC CER 10.4% at 25ms median, whisper CER 55.0%/WER 113.7%.

## Scope: shard 2 of 10 — amharic rows 0:357

Same machinery, same window (18,000s / 16,200s Job cap), same image
digest, new frozen inputs only:

1. **Selection**: deterministic `select_suite_rows` over the pinned
   manifest archive — amharic[0:357], 357 rows, row-list SHA-256
   2423d9c7…, bound in `suite_selection` (MEDZEN_EXPECTED_ROWS=357
   follows the image-native ≤540 passthrough path — no slicing needed).
2. **Bundle**: audio-only shard bundle, identity fe7f1d1a…, uploaded
   under the exact research prefix (4 objects, 125MB) with the 13GB of
   model weights referenced read-only from the hash-bound pilot prefix.
   Prestage proof-003 committed with S3 checksum readback over all 9
   objects (validators PASS at the 18,000s window).
3. **Fixtures**: the 9 HeadObject captures and 3 suite download fixtures
   recaptured live (capture record 2026-003); rehearsal replays the
   shard-2 aggregate from the same selection the freeze produces.
4. **Estimated job**: 3.58h — amharic carries the slowest per-row work
   in the pool (whisper-conditioned decode on Ge'ez script, pilot median
   12.6s/row for english as reference; amharic's own pilot latencies
   price this shard). The 12,000s inference budget plus overhead leaves
   ~20% cap margin on top of the model's measured ~18% conservatism.

## Cost

Registry 029: attempt-32 reservation closed as PASS ($10 recognized
retained until reconciliation; ~$3 actual), attempt 33 reserved ($10,
one active reservation), $304.43 recognized, $95.57 headroom under the
$400 aggregate ceiling (raised under the owner's standing delegation,
basis documented in registry 028).

## Boundaries unchanged

$10/attempt ceiling; 18,000s window / 16,200s Job cap (live-proven);
risk-acceptance-003; image digest pinned (index sha256:8b93dc5b…, commit
5ebbaed, zero drift); offline-only namespace, default-deny + scoped
egress; three-prefix S3 policy (shard-2 prefix + meta source + whisper);
VERIFY_ONLY artifact stage; 16 GiB suite disk floor; write-once receipts
and history; committed stage-1 dry validation before execution;
caffeinate -ims host guard.
