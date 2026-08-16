# ASR base-model AWS change packet 2026-003B-S6 — full-suite shard 6, attempt 38 (Meta-only)

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-003B-S1 only, authorizing numbered attempt 38 for one non-transferable 18,000-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

## Attempt-37 terminal result

Attempt 37 failed closed when ONE transient host-network failure of the
single-shot kubectl Job poll refused the run at 5,380 of ~8,700
row-inferences (62%); the clean teardown then destroyed the Job and
volume, so shard 5's partial Meta results did not survive. Second
occurrence of the class (attempt 36). Refusal record and zero-state
audit committed (f906231); ~$1.5 actual.

## Two reviewed changes in this packet

1. **Poll tolerance (executor)**: the Job poll now absorbs up to three
   CONSECUTIVE transient kubectl failures on its bounded cadence,
   recording each in the state sequence; a fourth — or the deadline —
   still fails closed; non-transient refusals pass straight through.
   5 regression tests including deadline-during-blips.
2. **Meta-only protocol (image, OWNER DIRECTIVE 2026-08-16)**: whisper
   is removed from the evaluated CANDIDATES. Basis: 8,283 fully-covered
   rows across four unit-sets already prove whisper structurally
   unusable on the target languages (CER 55-127%, WER above 100%
   everywhere, capped decodes on Ge'ez script) while consuming ~75% of
   every shard's wall time. Whisper artifacts remain staged and
   integrity-verified (verify_model_root, bundles, prestage proofs all
   unchanged) so the historical five-arm protocol stays reproducible.
   The decision record documents whisper's rejection from the four
   completed unit-sets; remaining shards evaluate omniASR CTC + LLM
   (three arms). Rebuilt image pilot-8f63996, publication_required=true
   (changed layers only, the live-proven attempt-34 path).

## Scope: manifest shard 6 — 2,878 rows, 5 languages

Units manifest-derived: english[0:1040], igbo[0:1393], maka[0:148],
pulaar[0:149], sepedi[0:148]. Row sha d834852a…; bundle 753a23f6…;
prestage proof-007 with 9-object checksum readback in receipt order.
Meta-only estimated job ~55 minutes inside the 16,200s cap
(CTC ~2 min + LLM two arms at measured rates), an ~11x margin.

## Shard-5 coverage note

Shard 5 (shona + six Cameroonian languages) is re-covered by a cheap
Meta-only attempt after shard 10, restoring gap-free pool coverage for
the merge proof. Whisper's absence for shards 5-10 is a DOCUMENTED
protocol change by owner directive, not a coverage gap: the merge and
decision record treat whisper's four completed unit-sets as its final,
sufficient evidence base.

## Cost

Registry 034: attempt-37 closed (~$1.5), attempt 38 reserved ($10),
$354.43 recognized, $45.57 headroom under $400.

## Boundaries unchanged

$10/attempt; 18,000s/16,200s; risk-003; offline namespace; three-prefix
policy; VERIFY_ONLY; 16 GiB floor; write-once; dry validation;
caffeinate -ims.
