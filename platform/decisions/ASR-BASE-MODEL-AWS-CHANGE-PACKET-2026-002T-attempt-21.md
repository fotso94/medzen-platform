# ASR base-model AWS change packet 2026-002T — stable receipt-gated attempt 21

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002T only, authorizing numbered attempt 21 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and that exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002T` must bind this
packet's final SHA-256. A committed read-only
`deadline_identity_and_acceptance` validation must then PASS against the
actual authorization, bindings and packet before any attempt envelope or AWS
mutation.

## Attempt-20 outcome and preserved milestone

Attempt 20 is consumed. It passed eight stages, including the exact image's
pull and unpack on the corrected 40 GiB GPU node. This live-proves that
attempt 19's ephemeral-storage failure class is fixed. DRA readiness, 120
numeric GPU samples and the versioned node-local input stage also passed.

The controller then performed one immediate, one-shot read of
`network-probe.json`. The receipt had not yet appeared, so `pilot_rows`
refused before the network proof, inference or any of the 540 rows ran. This
is a controller readiness race, not a model-quality or runtime result.
Cleanup and an independent AWS read-back proved CPU/GPU desired zero, no ASG
instances, no temporary endpoints, security group, volume, deadline action,
or evaluation/DRA namespace, and no production or approved-ASR change.

Immutable refusal:
`ASR-BASE-MODEL-PACKET-2026-002S-ATTEMPT-20-NETWORK-PROBE-RECEIPT-RACE-REFUSAL`,
SHA-256 `5e59589959e49702acc35fed77e28a821a474e05427d0e8535d594ac39fa5dfe`.

## Complete asynchronous-observation correction

The live and rehearsed composition now use the same shared receipt waiter:

1. a fixed 10-second poll interval and 300-second timeout;
2. two consecutive observations of the same immutable network-receipt hash,
   with the inbound-listener receipt present, before proceeding;
3. receipt or listener absence is readiness-only while the exact pilot pod
   remains non-terminal;
4. a terminal pod refuses immediately with
   `PILOT_POD_TERMINAL_BEFORE_NETWORK_RECEIPT`;
5. a live pod with no stable receipt before the bound refuses with
   `NETWORK_PROBE_RECEIPT_TIMEOUT`;
6. malformed content, a failed isolation status, receipt drift, receipt
   regression, unknown markers, SSM failure or malformed pod state refuses
   immediately and is never retried;
7. bounded sanitized pod/job/event/log diagnostics are persisted before
   cleanup on every refusal.

The observation command is read-only and returns a typed absence marker
instead of failing merely because an asynchronously written file is not yet
present. It never mutates the workload or its receipts.

## Class-wide observation audit

`ASR-BASE-MODEL-ASYNC-OBSERVATION-AUDIT-2026-001`, SHA-256
`bd7bf06f5b32351b196953bcfda90608cfba11404e6f979c7daa0c78c55e9150`,
enumerates all seven post-start observations remaining in `pilot_rows` and
`aggregate_report`:

- pilot pod identity/IP: bounded discovery, then exact-pod terminal checks;
- network receipt: shared stable poll;
- inbound-listener receipt: the same stable poll;
- cross-pod refusal: bounded terminal `Succeeded` wait;
- pilot job: bounded `condition=complete` wait;
- aggregate presence/hash: only after job completion;
- aggregate content: only after `PASS_PILOT_ROWS`, with a bounded SSM
  invocation poll plus schema and completeness checks.

A source-level machine guard requires the shared waiter exactly once and
refuses any direct one-shot network/listener read. It also requires every
synchronization contract above. Rehearsal uses the actual `LiveOperations`
stage implementation with fakes only at paid AWS/kubectl/tool boundaries.

Required injected paths are:

- delayed receipt, then two stable observations: PASS;
- non-terminal pod whose receipt never appears: timeout refusal;
- terminal pod before receipt: immediate diagnostic refusal.

## Cost reconciliation and request

Cost Explorer was queried read-only. Attempt 20's current-day billing is not
final, so the reported estimated zero is not treated as actual and the full
prior $10 ceiling is conservatively committed.

- reconciliation `ASR-BASE-MODEL-COST-RECONCILIATION-2026-006`, SHA-256
  `98679fd4fa96ddc3523b94a7af2078cb2608f6bc688f3b76b3b7db30b7ad56b8`;
- `COST-REGISTRY-2026-016`, SHA-256
  `dc607d547fabe467a3ad6933eb51d0f39cc4029252d4b058dae3e31c9b153c17`;
- project ceiling: $300;
- recognized committed guardrail: $174.4286064216;
- active reservations: $0;
- headroom before request: $125.5713935784;
- requested attempt-21 ceiling: $10;
- headroom if approved: $115.5713935784.

The reconciliation requires owner or qualified-finance review and a refresh
after current-day billing settles. No zero-cost claim expands headroom.

## Exact execution scope

Only after independent PASS and exact owner approval:

1. attempt 21 only; one GPU maximum; 10,800 seconds; fresh $10 ceiling;
2. unchanged local-resource and 40 GiB GPU-storage pre-envelope gates,
   followed by the committed real-artifact stage-1 dry validation;
3. unchanged exact-image risk acceptance, digest rescan, immutable image and
   verify-only pre-staged model bundle;
4. unchanged temporary endpoint, encrypted volume, namespace, DRA and network
   isolation resources; one bounded GPU scale-to-one-then-zero operation;
5. the frozen 540-row, 47-language comparison of Whisper large-v3, Meta
   Omnilingual CTC-1B-v2 and Meta Omnilingual LLM-1B-v2;
6. immediate stage receipts and mandatory status-keyed cleanup to zero.

The plan remains zero permanent creates or updates, 18 temporary
create-then-delete resources, and one bounded GPU capacity change.

Prohibited: attempt reuse or extension; IAM, KMS, ECR scanning or registry
changes; internet or inbound access; training; serving; promotion;
`approved/asr`; production SSM; MLflow registration; language-registry
changes; or anything not enumerated above.

## Qualification and evidence bindings

The successor binds source commit
`8ae6cdf89ae761627b38c27d1ffd1f7a1ba2cbdb`, all 30 executor modules, the
unchanged exact image and risk record, write-once attempts 1–20, the live
40 GiB response, async-observation audit, and cost registry 016.

- draft bindings SHA-256:
  `17a40f8349e011b927b76d5d6b025985042fa41a8a7ec44fe80f42e716fdadfd`;
- receipt-last cold-rehearsal SHA-256: **PENDING RECEIPT-LAST GENERATION**;
- deterministic comparison: two byte-identical generations required;
- current ASR base-model/evaluation suite: 277 passed, 0 failed before the
  successor packet assertions are added.

The final packet commit will replace only the pending rehearsal line with the
exact committed receipt hash and final test counts. No executor source edit is
permitted after receipt generation.

## Post-approval order

1. write and commit authorization 002T;
2. run and commit the complete real-artifact stage-1 dry validation;
3. run both pre-envelope gates; only PASS may create attempt 21's envelope;
4. execute once, persist every receipt and always clean up;
5. commit terminal evidence and reconcile billing when it lands.

## Deviations and limitations

No execution or safety deviation is taken. Risk acceptance remains scoped to
this unchanged offline image and bounded network-isolated evaluation window;
it is not precedent for serving. Current-day billing remains pending and is
therefore conservatively guarded. Local qualification does not claim that the
live 540-row evaluation has passed.
