# ASR base-model AWS change packet 2026-002R — typed transient-read successor attempt 19

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002R only, authorizing numbered attempt 19 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002R` must bind this
packet's final SHA-256. The committed read-only
`deadline_identity_and_acceptance` validation must PASS against the actual
authorization, bindings and packet before any AWS call or attempt envelope.

## Attempt-18 outcome and diagnosis

Attempt 18 is consumed. The deadline, input freeze and zero-state stages
passed; the read-only exact ECR child pull-back then received
`ConnectionResetError` (`errno 54`) after writing 5,035,653,120 of the expected
7,296,860,160 local archive bytes. It stopped before Scout produced a result,
before artifact verification, before endpoint creation and before GPU start.
Cleanup independently verified zero temporary state.

The reviewer classified this as the campaign's first stochastic transport
failure and not a design defect. The write-once refusal record is
`platform/evidence/ASR-BASE-MODEL-PACKET-2026-002Q-ATTEMPT-18-IMAGE-STREAM-REFUSAL.json`,
SHA-256 `567b1ecc81dc3018ed4a03b76245be971e8f268c663cfebffa810a80b968d855`.
Packet 002Q, its authorization, dry validation and receipts remain unchanged.

## Typed retry boundary

The successor adds one shared retry primitive. Only these idempotent reads can
enter it:

- exact ECR pull-back and immutable image metadata;
- Docker Scout database access while scanning the exact pulled archive;
- S3 reads: frozen-manifest synchronization, pre-staged bundle verification,
  versioned model bindings, and node-local versioned/unversioned downloads.

Only `CONNECTION_RESET`, `TIMEOUT` and `DNS_BLIP` are retryable. Each operation
has at most three attempts with 1- then 2-second backoff. The image-scan
composition has a 7,200-second hard cap, local S3 reads 3,600 seconds, and each
node download 903 seconds inside the existing 1,800-second staging bound.
Retry diagnostics retain only operation, safe label, attempt, classification,
backoff, cap and result—never a URL, credential, model bytes, audio or PHI.

The boundary accepts a typed transport exception only. Digest or image
identity mismatch, finding drift, malformed evidence, policy or contract
violations, unknown exceptions, writes, mutations and ambiguous operations
remain immediate fail-closed outcomes with zero retries. Docker Scout and ECR
faults retain their distinct operation type even though they share the scan
composition.

Qualification record:
`platform/evidence/ASR-BASE-MODEL-IDEMPOTENT-READ-RETRY-QUALIFICATION-2026-001.json`,
SHA-256 `ca3922b362d5d505d8d45a0696b167ff40e094b0a027382f94da8a2d31804477`.

## Executable stage mapping

The one live implementation remains `scripts.asr_base_model_pilot_live.py`.
The runner maps every claimed stage as follows:

| Stage | Runner wrapper | Live implementation |
|---|---|---|
| deadline/identity | `stage_deadline_identity_and_acceptance` | `LiveOperations.deadline_identity_and_acceptance` |
| input freeze | `stage_input_freeze_and_no_phi` | `LiveOperations.input_freeze_and_no_phi` |
| cost/zero state | `stage_cost_and_zero_state` | `LiveOperations.cost_and_zero_state` |
| image/scan | `stage_image_publication_and_scan` | `LiveOperations.image_publication_and_scan` |
| artifact verify | `stage_artifact_stage` | `LiveOperations.artifact_stage` |
| endpoints/isolation | `stage_private_endpoint_and_policy_gate` | `LiveOperations.private_endpoint_and_policy_gate` |
| GPU/sampler | `stage_gpu_and_sampler_gate` | `LiveOperations.gpu_and_sampler_gate` |
| node inputs | `stage_node_local_input_stage` | `LiveOperations.node_local_input_stage` |
| 540 pilot rows | `stage_pilot_rows` | `LiveOperations.pilot_rows` |
| aggregate | `stage_aggregate_report` | `LiveOperations.aggregate_report` |
| cleanup | `stage_cleanup_and_expiry` | `LiveOperations.cleanup_and_expiry` |

`scripts/asr_idempotent_read_retry.py` implements classification and retry;
`scripts/asr_eval_digest_rescan.py` types ECR-stream and Scout transport
failures; `scripts/asr_base_model_node_staging.py` applies the exact three-code
curl boundary. Rehearsal runs the live stage class and fakes only AWS, kubectl
and external paid boundaries.

## Cost reconciliation and request

Cost Explorer was queried read-only. Attempt 18 used zero GPU seconds and left
zero temporary AWS resources, but current-day request or transfer billing is
not final, so no zero-total-cost claim is made. Its entire $10 ceiling is
conservatively committed.

- reconciliation SHA-256:
  `babc2f745a8b1743c88797f4c5649c3ebf7d6523387e29a0186b75ae440e7c1d`;
- `COST-REGISTRY-2026-014` SHA-256:
  `d7130907559de5b86197e905f7e6431ca95327861dad2bf93a2a138e9795d496`;
- project ceiling: $300;
- recognized committed guardrail: $154.4286064216;
- active reservations: $0;
- headroom before request: $145.5713935784;
- requested attempt-19 ceiling: $10;
- headroom if approved: $135.5713935784.

The reconciliation still requires owner or qualified-finance review and a
future refresh when August 14 billing settles.

## Exact execution scope and unchanged controls

Only after independent PASS and exact owner approval:

1. attempt 19 only; one GPU maximum; 10,800 seconds; fresh $10 ceiling;
2. pre-envelope resource checks and committed real-artifact stage-1 dry run;
3. unchanged exact-image risk acceptance, digest rescan, immutable image and
   verify-only pre-staged bundle;
4. unchanged temporary endpoint/volume/namespace/DRA resources, one bounded
   scale-to-one-then-zero GPU change, and status-keyed cleanup;
5. the frozen 540-row, 47-language comparison of Whisper large-v3, Meta
   Omnilingual CTC-1B-v2 and Meta Omnilingual LLM-1B-v2;
6. immediate per-stage receipts and terminal zero-state proof on every result.

The image, accepted four offline-only PyTorch findings, network-isolated
execution, no-PHI inputs, frozen data and destruction-after-window rule are
unchanged. The risk acceptance remains offline-evaluation-only and is never a
serving-image precedent.

Prohibited: attempt reuse or extension; IAM/KMS or registry-scanning changes;
internet or inbound access; training; serving; promotion; `approved/asr`;
production SSM; MLflow registration; language-registry changes; or any action
not enumerated by this packet.

## Local qualification and cold rehearsal

The final bindings pin source commit `96ba9dfd92ef7375545f2cc5c29bec021e334daf`,
all 28 executor modules, the retry qualification, cost registry and immutable
attempt-18 history. The receipt-last cold rehearsal must prove:

- clean full PASS;
- a connection reset followed by success, with exactly two scan reads;
- persistent reset refusal after exactly three scan reads;
- digest mismatch and finding drift with zero retry;
- all standing deadline, isolation, cleanup, staging, GPU-readiness, policy and
  workload refusal paths through the actual `LiveOperations` composition.

- final bindings SHA-256: `f05f0b3e013a6f14f83ce9c011ed19d6ed1ebed5faa259d3c8cb9f86a49003ad`;
- receipt-last cold-rehearsal SHA-256: `PENDING_COLD_REHEARSAL_SHA256`;
- deterministic comparison: pending two identical generations;
- complete ASR base-model/evaluation suite: pending final count.

## Post-approval order

1. write and commit authorization 002R;
2. run and commit the complete real-artifact stage-1 dry validation;
3. remeasure the pre-envelope resource gate;
4. only on PASS create attempt 19's envelope and execute once;
5. preserve every receipt and always clean up;
6. commit terminal evidence and reconcile billing when available.

## Deviations and limitations

No requested execution or safety deviation is taken. The historical ECR
pull-back is restarted as an idempotent read after a typed transport failure;
no byte-range resume is introduced. Current-day billing remains pending, so
the conservative full-ceiling guardrail is retained. Local qualification does
not claim the live 540-row pilot has passed.
