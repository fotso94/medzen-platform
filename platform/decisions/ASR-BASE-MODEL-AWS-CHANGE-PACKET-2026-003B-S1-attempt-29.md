# ASR base-model AWS change packet 2026-003B-S1 — full-suite shard 1, attempt 29

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-003B-S1 only, authorizing numbered attempt 29 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

## Scope: the first of eight full-suite shards

Attempt 28 achieved PASS_PILOT with every stage live-proven. This packet
begins the full evaluation suite under the committed scoping
(ASR-FULL-EVAL-SUITE-SCOPING-2026-001) and shard manifest v3: 47 languages,
23,768 validated rows, eight ~1.9h-job shards. Shard 1 evaluates 3,616 rows
(kinyarwanda 3,329 with its full conditioned coverage, baka 147, lamso 140;
deterministic row-list SHA-256 bound in the bindings' suite_selection).

## What changed since 003A (all committed and test-locked)

1. Deterministic suite selection: checksum-sorted per-language slices by
   the shard manifest's row ranges; the pilot selection is proven
   byte-identical to its history-bound SHA.
2. Audio-only shard bundles: the 13 GB of omniASR weights are referenced
   verbatim as the pilot bundle's hash-bound part objects under the
   declared pilot root prefix (the whisper read-only-source pattern);
   shard-1's bundle is 608 MB across 4 objects at identity 92ff3ba2…,
   prestage-proven (PRESTAGE-PROOF-2026-002) with S3 checksum read-back.
3. The freeze stage validates the shard row list (exact count and SHA)
   through the bindings-driven suite_selection branch; aggregate
   completeness arithmetic follows the same bindings values.
4. The rehearsal pins a refreshed recorded eval-manifest archive
   (2026-08-15, kinyarwanda included) per packet bindings, and suite
   scenarios derive their fake aggregates from the SAME shard selection
   the freeze produces — end-to-end consistency proven cold.
5. Image, risk acceptance 003, chunked readback, and every 003A control
   carry forward unchanged. publication_required=false.

## Cost

COST-REGISTRY-2026-025: aggregate ceiling $360 (owner budget delegation,
scoping record); recognized $264.43; headroom $95.57 before and $85.57
after this shard's fresh $10. Net campaign cost remains $0.00 via credits.

## Execution scope

Identical to 003A except the shard bundle prefix (with the meta and
whisper read-only source prefixes in the S3 policy derivation), the
3,616-row workload (~1.9h projected), and attempt number 29. The host
runner is caffeinate-guarded against sleep.

## Gates

Twice-run byte-identical cold rehearsal from the committed bindings; the
full self-review battery including the bindings self-reference check and
write-once history through attempt 28.

## Post-approval order

1. Write-once AUTH-2026-003B-S1 committed alone at the reviewed head.
2. Committed stage-1 dry validation. 3. Execute attempt 29; per-shard
terminal review gates shard 2.
