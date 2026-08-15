# ASR base-model AWS change packet 2026-003A — retrieval-corrected attempt 28

Status: **DRAFT — INDEPENDENT SELF-REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-003A only, authorizing numbered attempt 28 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

This draft authorizes nothing. Write-once `ASR-BASE-MODEL-AWS-AUTH-2026-003A`
must bind this packet's final SHA-256 after review PASS, followed by the
committed stage-1 dry validation, before any attempt envelope or AWS mutation.

## Attempt-27 terminal result

Attempt 27 is consumed (refusal record SHA-256 `5187742cdaf8456a542e6e6708d800ed53e95b72ad06e43a14320da539e7c648`).
It was the campaign's first complete run: the corrected image was published
in-attempt to ECR (index `sha256:8b93dc5b…`, tag pilot-5ebbaed), the dual scan
gates passed, and pilot_rows PASSED — all 540 rows across all three
candidates including Whisper (token-budget fix live-proven), the Job
Complete, the aggregate computed and written. The only failure was the
aggregate readback: one SSM GetCommandInvocation whose
StandardOutputContent AWS caps at 24,000 characters truncated the aggregate
mid-JSON; the fail-closed refusal destroyed the computed results with the
volume. Sixth bounded-read defect; the bound was the AWS API's own cap.

## Correction: chunked bounded aggregate readback (executor-side only)

Commit `57a4bba`: `_ssm_read_file_chunked` reads the node file as a size+sha
identity probe followed by 15,000-byte node-side slices (base64 20,000
characters, under the cap with margin), reassembles locally, and refuses
unless the byte count and SHA-256 match the node-side identity before any
parsing; a 32 MiB sanity bound applies. The rehearsal fake now models the
24,000-character cap on EVERY command output — a future one-shot large read
fails cold — serves the chunk command shapes from a multi-chunk synthetic
aggregate, and injects a chunk-integrity flip scenario expecting the typed
refusal. The SSM call-site inventory and async-observation audits record
the new classifications and fail closed on drift
(`ASR-BASE-MODEL-WAITER-FINALIZER-AUDIT-2026-004`).

## No image change; publication complete

The image is byte-identical to attempt 27's published identity: index
`sha256:8b93dc5ba723da365452c048f2ef3acbef6876dc1bea69bede9da9b9f7e494b6`,
child `sha256:74b847f6c2e703ca5825701672291db8ec39eaa087dd9f2cb080794fc3f0570c`.
publication_required=false: the attempt verifies the exact existing digest
(fresh read-only fixture capture
`ASR-BASE-MODEL-ECR-EXISTING-IMAGE-FIXTURE-CAPTURE-2026-002`) and re-runs
the dual scan gates only — restoring the fast ~30-minute path to the
workload and leaving the full window for the rows and retrieval.
Risk acceptance 003 continues unchanged.

## Cost

`COST-REGISTRY-2026-023` (SHA-256 `81cb86f6f5673a6ffb22e66c860c40eec298ac46a3efbf09ec081f131cf52730`) closes attempt 27: recognized
guardrail $244.4286064216, headroom $55.5713935784 before and
$45.5713935784 after the fresh $10 — inside the $300 ceiling.

## Exact execution scope

Identical to packet 2026-002Z except publication_required=false and the
chunked readback. Bindings SHA-256 `55518672b43e4d174cf793b9f88d4993e5d5d9274f097fb9e4a5c335c8e9168b`.

## Cold-rehearsal and review gates

Twice-run byte-identical rehearsal from the committed 003A bindings,
covering the multi-chunk clean path and the chunk-integrity refusal; full
self-review battery including the bindings self-reference check.

## Post-approval order

1. Write-once AUTH-2026-003A; commit alone at the reviewed head. 2.
Committed stage-1 dry validation. 3. Execute attempt 28 once — on
PASS_PILOT the aggregate is finally retrieved, verified, uploaded into
evidence, and reviewed as the campaign's primary deliverable.
