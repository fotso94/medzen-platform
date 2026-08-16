# ASR base-model AWS change packet 2026-003B-S5 — full-suite shard 5, attempt 37

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-003B-S1 only, authorizing numbered attempt 37 for one non-transferable 18,000-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

## Attempt-36 terminal result

Attempt 36 failed closed when the engineering host lost network
connectivity to AWS mid-poll; the cluster Job was unaffected and
completed all 1,785 row-inferences. The aggregate was salvaged via
chunked SSM readback with end-to-end hash verification and is bound in
the refusal-with-salvage record (commit 3b22696); manual cleanup reached
independently audited zero state. LESSON NOW LIVE-PROVEN: the Job cap
plus the deadline scheduled action carried the run safely through a
dead host — the backstop design worked exactly as reviewed. Amharic is
fully covered; suite coverage stands at 7,743/23,768 with the
salvage-aware merge proof.

## Scope: manifest shard 5 — 2,162 rows, 7 languages

Units derived from the committed manifest by shard number (the review-
029 control): shona[0:1312], ewondo[0:126], fefe[0:146],
malagasy[0:146], ngiemboon[0:150], nomaande[0:150], yangben[0:132].
Row-list SHA 37167a44…; bundle dadea527… (4 objects uploaded, meta
weights referenced); prestage proof-006 with 9-object checksum readback
in bundle-receipt order; fixtures capture 2026-006;
publication_required=false against the published pilot-0822ead;
MEDZEN_EXPECTED_ROWS=2162 via the live-proven sliced driver path.
Estimated job 3.58h inside the 16,200s cap; whisper-conditioned rows:
shona + malagasy per the pilot conditioning asset.

## Cost

Registry 033: attempt-36 closed (~$2.5 actual incl. salvage window),
attempt 37 reserved ($10), $344.43 recognized, $55.57 headroom under
the $400 aggregate ceiling.

## Boundaries unchanged

As the attempt-36 packet: $10/attempt; 18,000s/16,200s; risk-003;
pinned image pilot-0822ead; offline namespace; three-prefix policy;
VERIFY_ONLY; 16 GiB floor; write-once; dry validation; caffeinate -ims.
