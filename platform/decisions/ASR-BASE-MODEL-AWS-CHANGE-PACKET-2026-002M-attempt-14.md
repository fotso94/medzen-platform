# ASR base-model AWS change packet 2026-002M — shared-boundary-gated attempt 14

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002M only, authorizing numbered attempt 14 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002M` must bind this
packet. The committed, read-only `deadline_identity_and_acceptance` dry run
must then PASS against the real authorization, bindings and packet before any
attempt envelope or AWS call.

## Attempt-13 outcome and immutable history

Attempt 13 is consumed. Six stages passed, including security, verify-only
artifact staging, private endpoints and stable GPU-node readiness. The node
became Ready in about 90 seconds, proving the attempt-11 readiness correction.
The GPU/DRA stage then refused immediately because the pilot passed 600 seconds
to a helper whose public maximum is 300 seconds. Cleanup returned CPU/GPU,
endpoints, volumes, security groups, namespaces and deadline actions to zero.
Observed GPU lifetime to termination request was 188 seconds.

- refusal SHA-256: `8126590e36ff13653d3db60733bb9cfd603db40ccb275ea2bbe5b221449485f6`;
- diagnosis SHA-256: `6aeb14659bb68d7e66c24ba1ec0033c1f7f3342a8eaa0775f47cbc79dc0f6ad4`;
- authorization SHA-256: `f23a9746f244b116ba2466910e8bc7ef1afb78ad4013c678096e2e89a2765804`;
- stage-1 dry validation SHA-256: `50f07c0ba3a3640806d753e9851679b1cb515da460c7be1c893afc9ce57ff3f1`;
- packet SHA-256: `67d0df9827372e1fd97c308fd572a7b7b85a5eaaa5b37bb982e52679695724fd`.

These records remain write-once and cannot authorize attempt 14.

## Complete helper-contract correction

### DRA contract decision

The DRA wait is aligned to the reused helper's existing 300-second maximum.
The contract is not widened: the live node was Ready in about 90 seconds and
there is no evidence-based reason to extend a proven five-minute boundary.

### One validator for live and rehearsal

`scripts/asr_base_model_boundary_contracts.py` is the sole bounded-helper
contract source. Live execution and injected rehearsal helpers enter through
the same validation wrapper. A fake can no longer accept an argument the real
wrapper refuses.

The static audit enumerates 43 bounded call sites across the pilot and reused
B6A DRA helpers. It validates external command, kubectl, SSM, node-group
capacity/time, GPU-node readiness, registry-scan stability and DRA waits. It
found zero out-of-range or unresolved calls and zero injected-boundary bypasses.
The 600-second historical failure is a permanent negative regression test.

Diagnosis record:
`platform/evidence/ASR-BASE-MODEL-PACKET-2026-002L-ATTEMPT-13-DRA-BOUNDARY-DIAGNOSIS-2026-001.json`,
SHA-256 `6aeb14659bb68d7e66c24ba1ec0033c1f7f3342a8eaa0775f47cbc79dc0f6ad4`.

## Host headroom restored conservatively

A new keep-list was committed before deletion. The exact packet-bound eval
image and pinned CUDA base were retained and reverified; the ECR digest was
also verified read-only using explicit profile `medzen`. Only four exact,
unbound local qualification image tags were removed. Docker Desktop's
documented host-compaction helper reclaimed the sparse disk blocks; its exited
temporary container and image were then removed.

- keep-list SHA-256: `2c54dec0b8f430f2767b9ab949a727d7e1bb5f33b3027e01037e2227da9f0021`;
- cleanup result SHA-256: `e6d274a5ddfa9a2e97fd7cf122846c7f5f9599e5bff4c196987c0ec8e332f680`;
- free space: 33.01 GiB before, 40.78 GiB after;
- qualified OCI index: `sha256:506d6dd5...e4a6b2aa`;
- qualified linux/amd64 child: `sha256:85a82f34...7d50d14e`;
- unrelated images, running containers, active volumes and AWS objects removed: zero.

The fresh pre-envelope qualification measured 43,781,480,448 available bytes,
831,807,488 bytes above the 40-GiB floor, and passed memory, CPU, process-limit,
tool, Docker and Scout-credential checks without consuming an attempt:
`platform/evidence/ASR-BASE-MODEL-LOCAL-RESOURCE-QUALIFICATION-2026-002.json`,
SHA-256 `6aef3a2b3fc77c4763d72ee62934ebb13eb7f7cec0cbee6a976e8f149faf7d6b`.
The gate remeasures immediately before live execution; this receipt is not a bypass.

## Rehearsal and execution completeness

All 20 executor modules, including the new shared boundary source and the
byte-preserved historical DRA helper, are bound in
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002M.json`, SHA-256
`364d85d6f468a4ecbbb2a616241eb18a17e49b5b0fa00f15500ce46c2d0218a4`.

The receipt-last cold rehearsal is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002M-COLD/cold-rehearsal.json`,
SHA-256 `6731376fd1db7a5adf37bd57229bb519c650bf448089c7d24a4128cb2e4eaecf`.
It records:

- two full PASS paths, including delayed GPU registration;
- nine injected refusal scenarios with status-keyed cleanup and zero state;
- 20/20 executor hashes;
- 43/43 bounded helper calls inside the shared contract;
- DRA live and rehearsal timeout = 300 seconds;
- no fake boundary more permissive than its live wrapper;
- 22/22 recorded-real AWS read shapes with no invented fields;
- low-disk refusal before envelope, one local image representation, verify-only
  artifact staging and no image/model upload.

## Cost reconciliation and fresh request

Cost Explorer was queried read-only after attempt 13 under
`arn:aws:iam::558069890522:user/s.fotso`. The current day remains estimated and
still has no `g6.xlarge` row, even though immutable receipts prove 188 GPU
seconds. Zero is therefore not claimed as actual cost. Shared service rows are
not attributed to this attempt. The full $10 ceiling is conservatively
recognized pending billing ingestion.

- observation SHA-256: `212f9ebd10b48f7a78d06a76f77a62ad078980eec0c6770e49a645aa49b906bc`;
- `COST-REGISTRY-2026-009` SHA-256: `e364cabcb06a9b341fccd449ba0139be5fee42c6a265a19b5006d811de30217b`;
- project ceiling: $300;
- recognized committed guardrail: $104.4286064216;
- active reservations before this request: $0;
- headroom before this request: $195.5713935784;
- requested attempt-14 ceiling: $10;
- headroom if approved: $185.5713935784.

## Unchanged subject and exact execution scope

Image/build context, four accepted offline-only PyTorch HIGH tuples, risk
record, frozen 540-row/47-language inputs, pre-staged 13,116,686,091-byte model
bundle and strict network isolation are unchanged. Attempt 14 is verify/read-only
for the existing ECR image and S3 bundle.

Only after review and exact authorization:

1. attempt 14 only; 10,800 seconds; one GPU node maximum; fresh $10 ceiling;
2. pre-envelope local resource gate before workdir, envelope or AWS call;
3. exact digest rescan using one local archive, with no image upload;
4. verify-only pre-staged S3 model bundle, with no model upload;
5. temporary S3/ECR endpoints, strict network policy, one encrypted 60-GiB
   volume, one GPU node and the offline 540-row pilot;
6. stable GPU-node readiness, then shared-contract DRA wait capped at 300 seconds;
7. immediate receipts, status-keyed cleanup and zero-state proof after every outcome.

Prohibited: attempt reuse or extension; IAM/KMS changes; registry-wide scanning;
internet egress; inbound routes; PHI or untrusted input; training; serving;
promotion; `approved/asr`; production SSM; MLflow registration; registry language
mutation.

## Post-approval order

1. write and commit authorization 002M;
2. commit the real-artifact stage-1 dry validation;
3. remeasure the pre-envelope local resource gate;
4. only on PASS create attempt 14's envelope and execute once;
5. persist every stage receipt and always clean up;
6. commit terminal evidence after zero-state proof and reconcile actual billing when it lands.

## Deviations

Actual attempt-13 AWS billing is not yet available. The packet does not infer a
price from elapsed seconds or claim zero; it carries the full prior $10 ceiling
as committed guardrail. There are no implementation deviations from the
reviewer's five requested corrections. Historical records remain byte-unchanged.
