# ASR base-model AWS change packet 2026-002L — pre-envelope resource-gated attempt 13

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002L only, authorizing numbered attempt 13 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, a write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002L` must bind this
packet. A committed, read-only `deadline_identity_and_acceptance` dry run
against the real authorization, bindings and packet must PASS before any
attempt envelope or AWS call.

## Attempt-12 result and immutable history

Attempt 12 is consumed. Deadline/identity, input freeze and cost/zero-state
passed. Image/security refused locally before Docker Scout started because the
host held a 7.3-GB OCI layout and a second 7.3-GB archive concurrently with only
about 13 GiB free. No endpoints or GPU started; cleanup proved zero state.

- refusal: `platform/evidence/ASR-BASE-MODEL-PACKET-2026-002K-ATTEMPT-12-LOCAL-DISK-REFUSAL.json`;
- refusal SHA-256: `1ab4dfea19feb4924b9171d137d352d439c174db99d53d54bf958c2c3cb9dd6c`;
- attempt-12 authorization SHA-256: `d72771abce31ace400c44fbc6b3c4cc3acd15165fd71586f926979605ccd03e7`;
- attempt-12 dry-run SHA-256: `b7bfed97d82d76ae0b0700631ac3c17becc2227709610fefb307b01464f6dd54`;
- attempt-12 packet SHA-256: `1d72575e153f1606254e428a55324c66276ddeedcfee24ce37e8255d2186be9f`.

All are write-once and cannot authorize attempt 13.

## Conservative Docker cleanup completed first

The keep-list was committed before deletion at commit `b89ae9f` and the result
at `1efbd87`:

- keep-list SHA-256: `7782ec57fdbe0a2135ca435bff8b323a8559f77bdd939248f30d1945a3834284`;
- cleanup result SHA-256: `0e7aec8a9d91a356e72f34c7f0ac8273b4cc74a134383f2d9e624c65425d6a50`;
- free space: 13.35 GiB before, 44.70 GiB after, +31.35 GiB;
- deleted: the three owner-named superseded eval images, one stopped container,
  all 248 build-cache records and 16 dangling images; no unused volume existed;
- retained locally and reverified after cleanup: packet image OCI index
  `sha256:506d6dd5...e4a6b2aa` and pinned CUDA base
  `sha256:ac55d124...a1eb34bc`;
- the ECR copy was read-only verified before and after cleanup: OCI index
  `sha256:506d6dd5...e4a6b2aa`, linux/amd64 child
  `sha256:85a82f34...7d50d14e`;
- AWS mutations, GPU hours and cleanup cost: zero.

## Class-level execution corrections

### One full local image representation

The live security gate now streams ECR config/layers directly into one Docker
archive. Every descriptor is size- and SHA-256-verified while written. The
source index, Linux child and attestation remain byte-verified. No OCI layout is
materialized, and a partial archive is deleted on any stream mismatch. Docker
Scout reads the sole archive; ECR Basic remains the supplemental 0-critical /
0-high OS gate.

### Non-consuming pre-envelope local-resource gate

Before creating the external workdir or attempt envelope, the runner measures
all enumerable host resources it consumes:

- disk: exact archive 7,296,860,160 bytes + 2 GiB scanner reserve + 512 MiB
  evidence reserve + 2 GiB margin = 12,128,698,368 calculated bytes;
- enforced disk floor: max(calculated need, owner 40-GiB floor) =
  42,949,672,960 available bytes;
- physical memory >= 16 GiB; logical CPUs >= 4; open files >= 1,024; processes
  >= 512;
- exact local tools `aws`, `docker`, `git`, `kubectl`; writable/searchable
  external parent; HOME; Scout credentials without recording values; reachable
  Docker daemon.

Any missing/malformed/insufficient value refuses before workdir creation,
before the attempt envelope, with zero AWS/kubectl calls and attempt 13
unconsumed. Policy:
`platform/manifests/ASR-BASE-MODEL-LOCAL-RESOURCE-POLICY-2026-001.json`,
SHA-256 `16c7e4d778dca7badfad7fef02f94b86d011ab0a37c2162b37d140e456f9a77c`.

The real host qualification measured 48,208,621,568 free bytes (44.9 GiB),
5,258,948,608 bytes above the bound floor. It also passed memory, CPU, limits,
tools, environment and Docker checks without consuming an attempt:
`platform/evidence/ASR-BASE-MODEL-LOCAL-RESOURCE-QUALIFICATION-2026-001.json`,
SHA-256 `903a503751571dc533ec24581b937dd3b3890a4f0fa3f643196d6f512dab518f`.
The gate re-measures immediately before any live attempt; this qualification is
evidence, not a bypass.

## Rehearsal and execution completeness

The sole stage implementation remains `LiveOperations`; rehearsal fakes only
AWS/kubectl/Scout boundaries and executes the real pre-envelope filesystem
ordering. All 18 executor modules are unconditionally bound in
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002L.json`, SHA-256
`7ac57fccb3dfed577f06b7c7fd74877e899c8e1ed1e8d83ba40ffdb4fc8b4177`.

The receipt-last cold rehearsal is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002L-COLD/cold-rehearsal.json`,
SHA-256 `21308cc370efb02db914e63344cad963e3d79290ac068972c73be33dccfd36d7`.
It records:

- two full PASS paths, including delayed GPU registration;
- nine failure checks plus a low-disk refusal before workdir/envelope with zero
  boundary calls and the attempt unconsumed;
- the sole-image representation with no OCI layout;
- stable node-readiness timeout, isolation, security, deadline, cleanup,
  pre-stage and window-budget refusal paths;
- 22/22 recorded-real AWS read shapes, no invented fields, and zero residual
  state in all stage scenarios.

## Cost registry and requested ceiling

`COST-REGISTRY-2026-008`, SHA-256
`0313f8b73589dfe124e537693837b25c451f8045d419ca0f9d0208f3cb920ce4`,
conservatively recognizes the full attempt-12 ceiling even though receipts show
zero GPU seconds and Cost Explorer has no g6.xlarge row. August 13 is still
estimated; no zero-total-cost claim is made.

- project ceiling: $300;
- recognized committed guardrail: $94.4286064216;
- active reservations: $0;
- headroom before this request: $205.5713935784;
- requested attempt-13 ceiling: $10;
- headroom if approved: $195.5713935784.

## Unchanged subject and exact scope

Image, build context, four accepted PyTorch HIGH tuples, risk record, frozen
540-row/47-language input, pre-staged 13,116,686,091-byte model bundle and
network isolation are unchanged. Attempt 13 is verify/read-only for ECR and S3.

Only after review and authorization:

1. attempt 13 only, 10,800 seconds, one GPU node maximum, fresh $10 ceiling;
2. pre-envelope local resource gate must PASS before any workdir/envelope;
3. existing exact ECR image digest-rescan, single local archive, no upload;
4. existing pre-staged S3 bundle read/verify only, no upload;
5. temporary S3/ECR endpoints, strict network policy, one encrypted 60-GiB
   volume, one GPU node and the offline 540-row pilot;
6. stable node readiness before volume/DRA/sampler/model work;
7. immediate receipts, status-keyed cleanup, CPU/GPU zero and no temporary
   residue after every outcome.

Prohibited: attempt reuse/extension; IAM/KMS changes; registry-wide scanning;
internet egress; inbound routes; PHI/untrusted inputs; training; serving;
promotion; `approved/asr`; production SSM; MLflow registration; registry
language mutation.

## Post-approval order

1. write and commit authorization 002L;
2. commit the real-artifact stage-1 dry validation;
3. run the live pre-envelope local-resource measurement;
4. only on PASS create attempt 13's envelope and execute once;
5. persist every stage receipt and always clean up;
6. commit terminal evidence after zero-state proof; reconcile billing later.

## Deviations

None. Historical records remain byte-unchanged. No AWS execution is authorized
by this draft.
