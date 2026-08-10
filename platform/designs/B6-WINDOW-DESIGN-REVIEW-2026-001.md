# B6.6 Integration Window — Independent Design Review (2026-001)

Reviewer: independent architecture reviewer (Claude)
Date: 2026-08-10
Scope: the integration-window apparatus as one artifact — runner, stage machinery,
credential lifecycle, receipts, guards, and packet family — based on the evidence
of packets 2026-008 through 2026-018 (seven windows, receipts and refusal records).

## Verdict on the current design

The **platform under test is sound**: no service, image, node, or ALB component has
failed since attempt 3. The last four stops (token encoding, credential cardinality,
receipt gaps, and two environmental interactions) were failures **of the window
apparatus**, three of them tripping over state created by the apparatus's own
previous runs. The apparatus — 23 stages, a shred-and-restore credential lifecycle,
two receipt generations, three forked script families — is now the dominant risk.
Consolidate it before spending again.

## Required changes

### R1 — Credential lifecycle: stop shredding the key
The synthetic secret is deliberately valueless; the restore→rotate→shred cycle
protecting it has caused four packets (012, 012A, 015, 2026-005-manifest) and two
window failures (token encoding, version cardinality).
- The secret **persists between windows**. Operator-deny stays permanently.
- Stage 0 rotates the value **in place**: 32 fresh random bytes, publish one new
  version, write the 0600 / 44-byte / single-LF token file, receipt the version ID
  and bearer SHA-256.
- The verifier checks **invariants only**: AWSCURRENT is fresh (created by this
  stage), operator access denied, token file shape exact, bearer hash matches the
  new version. It must not count historical versions, tags, or any incidental state.
- Delete the restore machinery from the window path. No future restore packets.

### R2 — One receipt engine for everything
Receipts v2 semantics (write-once, finally-guaranteed PASS/REFUSED/WARNING) become
the **only** path — stage 0, preflights, every window stage, cleanup, and the
runner's top-level exception handler. The runner must be structurally incapable of
exiting without either a complete trail or a terminal EXCEPTION receipt.
Regression test: for every enumerated stage name, assert a receipt exists in both
PASS mode and induced-failure mode.

### R3 — Cold rehearsal gate (the decisive change)
Before any window may be presented for approval, the **entire runner** must execute
against a faked AWS/kubectl layer:
1. one full PASS run — all 23 stages in order, receipt per stage, guards invoked;
2. **23 injected-failure runs** — failure injected at each stage in turn, asserting
   the REFUSED receipt persists and the cleanup path completes from that stage.
The cold-rehearsal receipt (hashes of runner + results) is attached to the packet.
The token-newline, cardinality, and receipt-gap failures would all have been caught
here for $0. This converts paid one-shot windows into free iterations.

### R4 — Consolidate the script families
Three generations coexist (`b6_6_*`, `b6_6_successor_*`,
`b6_6_images_before_endpoints_*`). Keep one canonical set; the packet binds that
set's hashes; delete the dead generations (git history preserves them). Dual/dead
paths are where the receipt gaps lived.

### R5 — Verifier policy (record as a standing rule)
Verify what matters for safety and function; never assert on incidental artifacts
(version counts, tag totals, historical receipt counts). Each stage's packet section
enumerates its invariant list explicitly.

### R6 — Do not refactor the settled physics
These are proven and stay as-is during consolidation: images-before-endpoints
ordering; principal-independent endpoint policies; the temporary self-isolated
endpoint SG; bounded worker registration poll; deadline-first arming; the
tag-mutation non-fatal rule with its always-fatal list.

### R7 — Allowance request shape
Request a fresh allowance for **two** window attempts (~9,000 seconds ≈ $3.20
compute) with the cold-rehearsal gate as a precondition for each. A single-attempt
allowance creates pressure to proceed on marginal signals; two attempts behind a
rehearsal gate is realistic and still tightly bounded.

## Acceptance criteria for the corrected packet
My review of the successor packet will verify:
1. cold-rehearsal receipt attached: 1 full PASS trail + 23 injected-failure trails;
2. single receipt engine across all paths, with the regression test;
3. credential stage 0 per R1, invariant list enumerated;
4. one consolidated script family, hash table complete;
5. proven orderings and policies unchanged (R6);
6. allowance statement per R7, arithmetic explicit.

— End of review. Questions or disagreement on any point: raise them in the packet's
"deviations" section rather than silently adapting; deviations will be reviewed on
their stated rationale.
