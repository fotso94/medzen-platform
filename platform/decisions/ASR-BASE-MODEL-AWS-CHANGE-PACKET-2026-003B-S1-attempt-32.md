# ASR base-model AWS change packet 2026-003B-S1 — full-suite shard 1, attempt 32

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-003B-S1 only, authorizing numbered attempt 32 for one non-transferable 18,000-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

## Attempt-31 terminal result

Attempt 31 is consumed: the machinery ran flawlessly for the full 9,000s
Job cap and completed 13,780 of 14,464 row-inferences (95%) before the
Job's activeDeadlineSeconds fired — roughly eleven minutes short. Root
cause is the v3 shard-time model, which priced every shard at ~1.35h from
uniform row counts; measured wall rates (CTC ~1,864 rows/min, whisper
unconditioned ~63 rows/min on kinyarwanda audio) put the true shard-1 job
near 2.7h. The refusal record, measured rates and independently verified
zero state are committed (the cleanup receipt's REFUSED status is the
already-fired deadline action's deletion returning ClientError — benign).

## What changed since attempt 31 (all committed and test-locked)

1. **Bindings-bound attempt window.** `bound_attempt_window` resolves an
   optional `attempt_window` bindings block (10,800-21,600s, fixed 1,800s
   host reserve between window and Job cap; absent block keeps the pilot
   10,800/9,000 pair so all history stays valid). Threaded through the
   k8s render and verifier, the runner boundary and context, the
   authorization schema, the committed dry run, the rehearsal fixtures
   and injections, and the live Job waiter. The static call-site audit
   resolves the waiter's bindings-driven bound to its worst case (the
   contract maximum, 19,800s) while validate_boundary_parameters still
   checks the exact runtime value. 16 new hardening tests.
2. **Shard manifest v4 (2026-002, schema 4).** Prices every language from
   the attempt-28 pilot's per-language latency medians x1.35 (the factor
   is calibrated against attempt-31's measured wall rates and stays above
   both observed ratios), then packs first-fit-decreasing under a 12,000s
   inference budget. The suite is 30.7h of priced inference — 2.8x the v3
   model — across ten shards, every one at or under a 3.58h estimated job
   inside the 16,200s Job cap (~22% cap margin on top of the ~18%
   conservative pricing). Amharic alone is 6.9h (whisper-conditioned
   decode loops on Ge'ez script) and splits across three shards by the
   same checksum-sorted row ranges. The generator validates exact,
   gap-free row coverage of the 23,768-row pool and regenerates
   byte-identically. Shard 1 is preserved verbatim: its 3,616-row
   selection (row-list SHA unchanged), frozen 608MB bundle and prestage
   proof-002 all stand.
3. **This packet binds an 18,000-second window** with a 16,200-second Job
   cap for shard 1 (estimated job 3.34h including the measured ~15-minute
   load-and-verify overhead). The committed prestage proof remains valid:
   the window-budget validator checks feasibility against the bound
   window, which only widened.

## Cost

Registry 028 reserves attempt 32 ($10, one active billable reservation)
at $294.43 recognized. The aggregate ceiling moves from $360 to $400
under the owner's standing budget delegation: ten conservative $10 shard
ceilings on top of $284.43 would peak at $384.43 if every shard consumed
its ceiling. Measured actual spend remains a few dollars per attempt and
the campaign nets $0.00 via credits; credits never expand headroom.

## Boundaries unchanged

$10/attempt ceiling; risk-acceptance-003; image digest pinned (index
sha256:8b93dc5b…, commit 5ebbaed, zero drift required); offline-only
namespace with default-deny + scoped egress; three-prefix S3 policy;
VERIFY_ONLY artifact stage; suite-mode 16 GiB disk floor; write-once
receipts and history; committed stage-1 dry validation before execution;
caffeinate -ims host guard with the keep-lid-open advisory.
