# ASR base-model AWS change packet 2026-002W — lifecycle-hardened attempt 24

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002W only, authorizing numbered attempt 24 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

This draft authorizes nothing. After independent review PASS and that exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002W` must bind this
packet's final SHA-256. A committed, read-only
`deadline_identity_and_acceptance` validation must then PASS against the
actual authorization, bindings, packet, all 33 executor-module hashes and
every write-once predecessor before an attempt envelope or AWS mutation.

## Attempt-23 terminal result

Attempt 23 is consumed. Eight of eleven stages passed. The GPU instance
existed for at most 1,431 seconds; Torch was not imported and zero evaluation
rows started. The DNS-control Pod reached `Pending` while pulling the exact
image, then the poll called nonexistent `self._sleep` rather than the injected
`self._sleeper`. A blocking Pod delete in `finally` timed out and replaced the
primary exception. The stage refused fail-closed before the pilot Job.

The immutable refusal record is SHA-256
`fb90d200b10d8fbe1294461c2dba6539ed5df28231c2766779f5d631af94d8fb`.
Status-keyed cleanup and independent read-back prove both node groups at
desired zero, no temporary endpoint, security group, volume, deadline action,
evaluation namespace or DRA namespace, prior scan configuration and VPC-CNI
mode restored, production untouched and no write under `approved/asr`.

## Complete lifecycle correction

All stage-local Pods now use one shared lifecycle module,
`scripts/asr_base_model_pod_lifecycle.py`:

- every terminal poll uses the injected sleeper and a fixed hard deadline;
- Pod deletion is nonblocking, followed by two stable absence observations;
- a primary stage exception remains authoritative; cleanup failure is
  separately persisted with bounded, sanitized diagnostics;
- the same primary-preservation rule is applied to every remaining executor
  finalizer, including archive, staging, socket and command-journal cleanup;
- every bounded waiter fake crosses at least one non-terminal observation
  before terminal success; no instant-success fake is accepted.

The systemic audit enumerates 15 waiters and all remaining `finally` sites in
`ASR-BASE-MODEL-WAITER-FINALIZER-AUDIT-2026-001`, SHA-256
`6d0c1bdbf3d4f0a2950b8aee4bec2e630bda8c3cd19b9337f264514b5f7562c2`.
It finds zero blocking stage-Pod deletes, zero undefined sleep calls, and zero
instant-terminal-only rehearsal waiters.

## Exact image pre-pull before DNS

Before the DNS-control Pod or pilot Job, a dedicated Pod is bound to the exact
GPU node and exact scan-qualified linux/amd64 digest with
`imagePullPolicy: Always`. It executes only `/opt/venv/bin/python -c pass`.
The controller requires:

1. bounded `Pending`/pull progress observations and terminal `Succeeded`;
2. a 1,200-second hard timeout and 600-second zero-progress stall refusal;
3. two stable exact-digest observations in that node's image inventory;
4. a write-once qualification receipt with terminal sequence, pull duration,
   exact node, digest, inventory sequence and cleanup outcome.

The limits are conservative against the live attempt-20 pull of 197.027
seconds. Any post-prepull image pull failure is fatal and receipted. DNS and
inbound-control Pods use the same digest and cannot start until pre-pull PASS.

## Execution asset map

Each claimed stage maps to the exact executable composition below. The cold
rehearsal invokes the same `LiveOperations`; only AWS, kubectl and Docker Scout
boundaries are faked with hash-bound response shapes.

| Stage | Runner function | Live implementation |
|---|---|---|
| deadline_identity_and_acceptance | `stage_deadline_identity_and_acceptance` | `LiveOperations.deadline_identity_and_acceptance` |
| input_freeze_and_no_phi | `stage_input_freeze_and_no_phi` | `LiveOperations.input_freeze_and_no_phi` |
| cost_and_zero_state | `stage_cost_and_zero_state` | `LiveOperations.cost_and_zero_state` |
| image_publication_and_scan | `stage_image_publication_and_scan` | `LiveOperations.image_publication_and_scan` |
| artifact_stage | `stage_artifact_stage` | `LiveOperations.artifact_stage` |
| private_endpoint_and_policy_gate | `stage_private_endpoint_and_policy_gate` | `LiveOperations.private_endpoint_and_policy_gate` |
| gpu_and_sampler_gate | `stage_gpu_and_sampler_gate` | `LiveOperations.gpu_and_sampler_gate` |
| node_local_input_stage | `stage_node_local_input_stage` | `LiveOperations.node_local_input_stage` |
| pilot_rows | `stage_pilot_rows` | `LiveOperations.pilot_rows` |
| aggregate_report | `stage_aggregate_report` | `LiveOperations.aggregate_report` |
| cleanup_and_expiry | `stage_cleanup_and_expiry` | `LiveOperations.cleanup_and_expiry` |

Bindings `ASR-BASE-MODEL-PILOT-BINDINGS-2026-002W` pin source commit
`b8643a571409ef9eab93b3c94e0c44e285dc1574` and all 33 executor modules.

## Exact unchanged image and risk continuation

There is no image rebuild, upload or registry-scanning mutation. Attempt 24
uses the already-published scan-qualified image by immutable child digest:

- tag `pilot-7efa6e8c`;
- OCI index `sha256:f14fe88a7ebb2c68bf2ed772ad2ce8913c1fa8117b2da5305af55298f1d15505`;
- linux/amd64 child `sha256:4d1ccde955f5ae074ed6470d7edb6d74f9d49cc6a6f44f9f0a2b7397a0cd3841`;
- config `sha256:2938427027f22b10f9dc5c89b3305b5689ea5c44b088839b85583e1575feeda3`;
- attestation `sha256:8d96d7c4b5b6f4a3c1677dc93301e2829afd0923b20eb269272b5e19dbf57e23`.

The unchanged security gate requires ECR Basic at zero critical/high and a
digest-verified Docker Scout 1.18.3 rescan at zero critical and exactly the
four accepted PyTorch high tuples. `ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003`,
SHA-256 `43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034`,
continues only for this offline, frozen-input, no-PHI, no-untrusted-input,
no-inbound-network, S3/ECR-only, destroyed-after-use window. It remains
non-precedential for serving, production, traffic, promotion or training.

## Cost reconciliation and fresh allowance request

`COST-REGISTRY-2026-019`, SHA-256
`ed2109dea142c867b141cad1f7b1ee09b00d75a9775f475ec81afd8d0920fc4e`,
reconciles attempts 22 and 23. Current-day Cost Explorer data remains estimated
and not attempt-attributable, so no zero-dollar actual is claimed and attempt
23's full ceiling is conservatively closed:

- aggregate project ceiling: $300;
- recognized committed guardrail: $204.4286064216;
- active reservations before this request: $0;
- headroom before request: $95.5713935784;
- requested attempt-24 ceiling: $10;
- headroom if approved: $85.5713935784.

No pending charge or credit expands headroom. Attempt 24 receives one fresh,
non-transferable allowance; no earlier attempt may be reused.

## Exact execution scope

Only after independent PASS and exact owner approval:

1. validate all pre-envelope local resources and the committed real-artifact
   stage-one receipt;
2. create the deadline action first and execute attempt 24 once, for at most
   10,800 seconds and one existing `g6.xlarge` GPU node;
3. verify the exact existing ECR image and dual scan gate, without upload or
   registry mutation;
4. verify the 9-object, 13,116,686,091-byte frozen bundle by hash and version;
5. create only the temporary encrypted volume, endpoint/security-group,
   strict-network, namespace, DRA, claim and workload resources already in the
   exact machine plan; scale the existing GPU ASG one then zero;
6. pass stable GPU/DRA/sampler and numeric-UID node staging;
7. pass exact image pre-pull and node-inventory qualification;
8. pass VPC-resolver consistency and positive/negative pre-Torch isolation;
9. run the frozen 540-row, 47-language comparison of Whisper large-v3, Meta
   Omnilingual CTC-1B-v2 and Meta Omnilingual LLM-1B-v2;
10. persist every stage receipt immediately and execute status-keyed cleanup
    on every terminal path.

Machine-plan counts remain zero permanent creates, zero permanent updates,
eighteen temporary create-then-delete resources and one bounded capacity
change. All AWS CLI and monitoring invocations hard-pin profile `medzen` and
region `eu-central-1`.

Prohibited: reuse or extension of attempts 1-23; IAM or KMS changes; Inspector
Enhanced or registry-wide scan changes; internet or inbound access; training;
serving; promotion; `approved/asr`; production SSM; MLflow/model registration;
language-registry changes; image rebuild/upload; or resources outside the
exact machine plan.

## Cold-rehearsal and review gates

The receipt-last rehearsal must run twice byte-identically from the committed
002W bindings after all source, bindings, packet, audit and cost artifacts are
committed. It must prove the standing scenarios plus:

- image pre-pull `Pending` then `Succeeded`, exact node inventory absent then
  present twice, and nonblocking stable-absence cleanup;
- typed refusal on a stalled pre-pull;
- DNS-control `Pending` then `Succeeded` using the injected sleeper;
- a genuine DNS refusal plus injected delete timeout retains the DNS reason as
  primary and persists cleanup timeout separately;
- endpoint creation/deletion, pilot discovery, network receipt, Job, SSM, GPU
  and DRA waiters each cross a non-terminal observation before terminal;
- zero temporary state on every PASS and refusal path.

The committed receipt path is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002W-COLD/cold-rehearsal.json`.
Its SHA-256 and this packet's final SHA-256 must be published in the review
request. No source or binding edit is permitted after receipt generation.

## Post-approval order

1. write and commit authorization 002W quoting the exact approval phrase;
2. run and commit the complete real-artifact stage-one dry validation;
3. run all pre-envelope prerequisites; only PASS may consume attempt 24;
4. execute once, persist all receipts and always clean up;
5. commit terminal evidence and reconcile actual billing when it lands.

No AWS execution has occurred under this packet. Local qualification does not
claim that any of the 540 evaluation rows has passed.
