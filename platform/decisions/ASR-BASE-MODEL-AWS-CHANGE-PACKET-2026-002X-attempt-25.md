# ASR base-model AWS change packet 2026-002X — attachment-hardened attempt 25

Status: **DRAFT — INDEPENDENT REVIEW AND DELEGATED APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002X only, authorizing numbered attempt 25 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-003 at SHA-256 43321489ce6f9f3a33f86445601b5bd65d99d2f1746747944b2a185742341034.

This draft authorizes nothing. After independent review PASS and the exact
delegated approval phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002X`
must bind this packet's final SHA-256. A committed, read-only
`deadline_identity_and_acceptance` validation must then PASS against the
actual authorization, bindings, packet, all 33 executor-module hashes and
every write-once predecessor before an attempt envelope or AWS mutation.

## Attempt-24 terminal result

Attempt 24 is consumed. Six of eleven stages passed. The GPU instance existed
for at most 553 seconds; Torch was not imported and zero evaluation rows
started. After `AttachVolume` returned, the executor immediately submitted a
remote one-shot `test -b` command. CloudTrail records the volume still in
`attaching` state one second before that command, whose empty stdout and exit
1 prove the guest block device had not appeared yet. The byte-identical
attempt-23 bundle happened to win the same latent race; this is an
asynchronous observation defect, not a 002W regression.

The immutable refusal record is SHA-256
`76e1d786f1ecd3d075d7c07cdbe6cca8c1c2eacddae50115ed1b82e9a06fb1a1`.
The primary SSM refusal remained authoritative, cleanup ran separately, and
the 42-file live-evidence set hashes to
`e2c1e0418c415aaee0a2d071bbc799476aaf7c63443e0150afbff889b8c440cf`.
Status-keyed cleanup and independent read-back prove both node groups at
desired zero, no temporary endpoint, security group, volume, deadline action,
evaluation namespace or DRA namespace, prior scan configuration and VPC-CNI
mode restored, production untouched and no write under `approved/asr`.

## Complete attachment and device correction

The executor now treats host attachment and guest device appearance as two
separate asynchronous boundaries:

1. After `AttachVolume`, it polls `DescribeVolumes` every five seconds for at
   most 300 seconds. Only the exact requested volume, in `in-use`, attached to
   the exact instance with attachment state `attached`, counts. Two
   consecutive matching observations are required. Stale absence and
   `attaching` are retryable; malformed, ambiguous or wrong-instance shapes
   refuse immediately; timeout refuses before SSM mount submission.
2. The remote mount bundle polls the NVMe by-id path every two seconds for at
   most 120 seconds before resolving it. It emits typed
   `MEDZEN_EBS_DEVICE_READY` or `MEDZEN_EBS_DEVICE_TIMEOUT` markers and retains
   `set -euo pipefail`; no one-shot `test -b` remains.
3. The volume-independent SSM bundle template, the volume-ID parameter and the
   rendered command bundle each receive distinct SHA-256 values in the stage
   receipt. Historical command provenance compares the stable template hash;
   per-attempt identity remains bound by the parameter and rendered hashes.

The boundary rehearsal replays the recorded-real `DescribeVolumes` shape as
stale absence, `attaching`, `attached`, `attached`, and the remote SSM device
shape as `ABSENT`, `PRESENT`. It separately proves bounded refusal when the
attachment or guest device never becomes ready.

## Systemic remote-observation audit

`ASR-BASE-MODEL-WAITER-FINALIZER-AUDIT-2026-002`, SHA-256
`31a26bfa90df78c67684d0dd9b711c6e0a5be9fd498e1e820c0a18b6a0c56b17`,
extends the previous 15-site Python waiter/finalizer audit across all nine
remote SSM observation sites. Every site is classified as a bounded
controller poll, bounded in-script poll, synchronous postcondition,
diagnostic-only read, mutation without an async success claim, or best-effort
cleanup. It reports zero remote asynchronous one-shot success gates, zero
unclassified SSM sites, zero instant-terminal-only rehearsal fakes, zero
blocking stage-Pod deletes, and zero undefined sleeper calls.

The shared boundary-contract audit now enumerates 66 call sites, including the
new attachment waiter, and applies identical validation in live execution and
rehearsal. Boundary fakes remain unable to accept arguments the live wrapper
would refuse.

## Execution asset map

Each claimed stage maps to the exact executable composition below. The cold
rehearsal invokes the same `LiveOperations`; only AWS, kubectl and Docker Scout
boundaries are faked with hash-bound recorded-real response shapes.

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

Bindings `ASR-BASE-MODEL-PILOT-BINDINGS-2026-002X` pin source commit
`16ea9ad8dec76070ce6c7b63495fa2c632820f39` and all 33 executor modules.

## Exact unchanged image and risk continuation

There is no image rebuild, upload or registry-scanning mutation. Attempt 25
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

`COST-REGISTRY-2026-020`, SHA-256
`285df935557d8f62ee106047ac86cb30c0d6d279ef1bc3b26ed51b77e4feb2d3`,
incorporates the read-only attempt-24 reconciliation. Current-day Cost
Explorer remains estimated, retains the earlier 0.336944 aggregate
g6.xlarge-hours and has not ingested or isolated attempt 24, so no zero-dollar
actual is claimed and attempt 24's full ceiling is conservatively closed:

- aggregate project ceiling: $300;
- recognized committed guardrail: $214.4286064216;
- active reservations before this request: $0;
- headroom before request: $85.5713935784;
- requested attempt-25 ceiling: $10;
- headroom if approved: $75.5713935784.

No pending charge or credit expands headroom. Attempt 25 receives one fresh,
non-transferable allowance; no earlier attempt may be reused.

## Exact execution scope

Only after independent PASS and exact delegated approval:

1. validate all pre-envelope local resources and the committed real-artifact
   stage-one receipt;
2. create the deadline action first and execute attempt 25 once, for at most
   10,800 seconds and one existing `g6.xlarge` GPU node;
3. verify the exact existing ECR image and dual scan gate, without upload or
   registry mutation;
4. verify the 9-object, 13,116,686,091-byte frozen bundle by hash and version;
5. create only the temporary encrypted volume, endpoint/security-group,
   strict-network, namespace, DRA, claim and workload resources already in the
   exact machine plan; scale the existing GPU ASG one then zero;
6. pass stable GPU-node readiness, stable exact volume attachment, bounded
   guest-device appearance, DRA and sampler gates;
7. pass numeric-UID node staging, exact image pre-pull and node-inventory
   qualification;
8. pass VPC-resolver consistency and positive/negative pre-Torch isolation;
9. run the frozen 540-row, 47-language comparison of Whisper large-v3, Meta
   Omnilingual CTC-1B-v2 and Meta Omnilingual LLM-1B-v2;
10. persist every stage receipt immediately and execute status-keyed cleanup
    on every terminal path.

Machine-plan counts remain zero permanent creates, zero permanent updates,
eighteen temporary create-then-delete resources and one bounded capacity
change. All AWS CLI and monitoring invocations hard-pin profile `medzen` and
region `eu-central-1`.

Prohibited: reuse or extension of attempts 1-24; IAM or KMS changes; Inspector
Enhanced or registry-wide scan changes; internet or inbound access; training;
serving; promotion; `approved/asr`; production SSM; MLflow/model registration;
language-registry changes; image rebuild/upload; or resources outside the
exact machine plan.

## Cold-rehearsal and review gates

The receipt-last rehearsal must run twice byte-identically from the committed
002X bindings after all source, bindings, packet, audit and cost artifacts are
committed. It must prove the standing scenarios plus:

- `DescribeVolumes` stale absence, then `attaching`, then two stable exact
  `attached` observations before SSM submission;
- remote device `ABSENT` then `PRESENT` before mount;
- typed bounded refusal for attachment-never-ready and device-never-present;
- separate stable bundle-template, volume-parameter and rendered-bundle hashes;
- every bounded waiter crosses at least one non-terminal observation before
  terminal success;
- zero temporary state on every PASS and refusal path.

The committed receipt path is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002X-COLD/cold-rehearsal.json`.
Cold-rehearsal SHA-256:
`40fb1d18e5887bfad0888882339a0b672a20aa7c93205b307080454e6170c7fd`.
No source or binding edit is permitted after receipt generation.

## Post-approval order

1. write and commit authorization 002X quoting the exact approval phrase;
2. run and commit the complete real-artifact stage-one dry validation;
3. run all pre-envelope prerequisites; only PASS may consume attempt 25;
4. execute once, persist all receipts and always clean up;
5. commit terminal evidence and reconcile actual billing when it lands.

No AWS execution has occurred under this packet. Local qualification does not
claim that any of the 540 evaluation rows has passed.
