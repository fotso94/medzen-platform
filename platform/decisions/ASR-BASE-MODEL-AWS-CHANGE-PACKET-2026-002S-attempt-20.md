# ASR base-model AWS change packet 2026-002S — 40 GiB capacity-gated attempt 20

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002S only, authorizing numbered attempt 20 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and that exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002S` must bind this
packet's final SHA-256. The committed read-only
`deadline_identity_and_acceptance` validation must then PASS against the
actual authorization, bindings and packet before any attempt envelope or AWS
mutation.

## Attempt-19 finding and completed infrastructure correction

Attempt 19 is consumed. Eight of eleven stages passed; the pilot pod was then
evicted while the exact 7.3 GB evaluation image was being pulled and unpacked
on the 20 GiB GPU-node root volume. No container, model inference, network
probe, pilot row or aggregate report ran. Cleanup returned all temporary state
to zero. The immutable refusal record is
`ASR-BASE-MODEL-PACKET-2026-002R-ATTEMPT-19-EPHEMERAL-STORAGE-REFUSAL`,
SHA-256 `09c8d917f4bab9151c316e602ec94685c5c2a3eeef1fa78d4f110f9427e1f739`.

The capacity qualification measured:

- archive: 7,296,808,960 bytes;
- unpacked root filesystem: 12,456,463,360 bytes;
- system and kubelet-eviction reserve: 3,292,966,534 bytes;
- workload reserve: 2,147,483,648 bytes;
- 25% safety margin: 4,938,318,080 bytes;
- calculated requirement: 30,132,040,582 bytes, rounded to 29 GiB;
- reviewed operational floor: 40 GiB.

Packet 2026-003 changed only the zero-sized GPU node group's root volume from
20 to 40 GiB. Its immutable completion evidence, SHA-256
`d283df056f49f53340531f13d94a8c5ea1a016c2cf32477bcdd0a96600acc7c2`,
proves: node group ACTIVE, scaling min=0/desired=0/max=1, no health issues,
no EC2 instances, no Kubernetes GPU nodes, and a residual Terraform
`NO_CHANGES` plan. Packet 2026-003 does not authorize attempt 20.

## Pre-envelope GPU-storage gate

Attempt 20 adds `scripts/asr_base_model_gpu_storage.py` to the unconditional
executor hash set. Before a work directory or attempt envelope exists, it:

1. hash-verifies the capacity qualification, packet-003 apply evidence and a
   recorded real 40 GiB `DescribeNodegroup` response;
2. recomputes all capacity arithmetic and requires the exact reviewed 40 GiB
   operational floor;
3. binds the capacity measurement to OCI index
   `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`
   and linux/amd64 child
   `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`;
4. performs one read-only live node-group check and requires ACTIVE, disk at
   least 40 GiB, scaling 0/0/1, zero health issues, `g6.xlarge`, AL2023 NVIDIA,
   and the exact current ASG
   `eks-gpu-14cfff59-42c6-46ad-8d59-37cd02daefa8`;
5. refuses fail-closed on missing/malformed evidence, hash or identity drift,
   arithmetic drift, ambiguous node-group state, an ASG mismatch, or capacity
   below the floor.

Refusal is non-consuming: one AWS read, zero AWS mutations, zero GPU nodes,
no work directory and no attempt envelope. The cold rehearsal must exercise
both the aligned 40 GiB PASS and injected 20 GiB refusal through the actual
`LiveOperations` composition.

## Cost reconciliation and request

Cost Explorer was queried read-only. Attempt 19's current-day billing is not
final, so no inferred actual cost is reported and the full prior $10 ceiling
is conservatively committed. Packet 2026-003 started no compute.

- reconciliation `ASR-BASE-MODEL-COST-RECONCILIATION-2026-005`, SHA-256
  `4e087a2f80eaae293843b86e6b83f8f54a188f1ba843856542cde13480ed8e3e`;
- `COST-REGISTRY-2026-015`, SHA-256
  `04c8edf03afa592b5865c7d0bfe1255485cb22e58f00e4ef63992d413f39f6be`;
- project ceiling: $300;
- recognized committed guardrail: $164.4286064216;
- active reservations: $0;
- headroom before request: $135.5713935784;
- requested attempt-20 ceiling: $10;
- headroom if approved: $125.5713935784.

The reconciliation remains pending owner or qualified-finance review and a
future refresh after current-day billing settles.

## Exact execution scope

Only after independent PASS and exact owner approval:

1. attempt 20 only; one GPU maximum; 10,800 seconds; fresh $10 ceiling;
2. local-resource and GPU-storage pre-envelope prerequisites, followed by the
   committed real-artifact stage-1 dry validation;
3. unchanged exact-image risk acceptance, digest rescan, immutable image and
   verify-only pre-staged model bundle;
4. unchanged temporary endpoint, encrypted volume, namespace, DRA and network
   isolation resources; one bounded GPU scale-to-one-then-zero operation;
5. the frozen 540-row, 47-language comparison of Whisper large-v3, Meta
   Omnilingual CTC-1B-v2 and Meta Omnilingual LLM-1B-v2;
6. immediate stage receipts and mandatory status-keyed cleanup to zero.

The exact mutation inventory remains: zero permanent creates or updates, 18
temporary create-then-delete resources, and one bounded GPU capacity change.
The current ASG name is a reviewed binding; the superseded ASG is not used.

Prohibited: attempt reuse or extension; IAM, KMS, ECR scanning or registry
changes; internet or inbound access; training; serving; promotion;
`approved/asr`; production SSM; MLflow registration; language-registry
changes; or anything not enumerated above.

## Qualification and evidence bindings

The successor binds source commit
`73541bbfd534403a6edffa6299f9ea28e4e05866`, all 29 executor modules, the
unchanged exact image and risk record, write-once attempts 1–19, packet 2026-003
and its completion evidence, the real 40 GiB response fixture, and cost
registry 015.

- final bindings SHA-256: `94af6d6db5e13f498cc02f4de8f9e8a07394b255eb667408d82d796d4bceec90`;
- receipt-last cold-rehearsal SHA-256: `55218c0f3078cfc1c591fe8899fc6238d12ad3c700dae34cd9f2d253c0cf40dd`;
- deterministic comparison: two byte-identical generations required;
- focused ASR base-model/evaluation suite: 330 passed, 0 failed, 0 skipped,
  1 deselected. The deselected test is the long Docker-backed reproduction of
  the unchanged, immutable node-equivalent qualification; this successor does
  not alter that qualified command path.

The complete stage mapping remains the eleven-stage runner-to-`LiveOperations`
mapping already reviewed for packet 002R. Rehearsal executes that exact live
composition and fakes only paid external AWS, kubectl and tool boundaries.

## Post-approval order

1. write and commit authorization 002S;
2. run and commit the complete real-artifact stage-1 dry validation;
3. run both pre-envelope gates; only PASS may create attempt 20's envelope;
4. execute once, persist every receipt and always clean up;
5. commit terminal evidence and reconcile billing when it lands.

## Deviations and limitations

No execution or safety deviation is taken. Risk acceptance remains scoped to
this unchanged offline image and bounded network-isolated evaluation window;
it is not precedent for serving. Current-day billing remains pending and is
therefore conservatively guarded. Local qualification does not claim that the
live 540-row evaluation has passed.
