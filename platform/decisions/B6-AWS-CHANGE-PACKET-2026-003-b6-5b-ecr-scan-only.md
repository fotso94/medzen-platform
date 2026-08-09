# B6 AWS change packet 2026-003 — B6.5B four-image ECR scan-only qualification

Status: **BLOCKED — NOT AUTHORIZED**

Prepared: `2026-08-09`

Required approval phrase:
`Approve B6 AWS change packet 2026-003 only.`

## Purpose and boundary

Qualify the four new full-B6 service images against the authoritative ECR
automatic scanner before preparing a deployable B6.6 packet. This follows the
B6A 003C-A pattern: scan qualification is independent from deployment.

This packet authorizes no SSM publication, IAM change, EKS/Kubernetes action,
ALB/security-group change, node scale-up, service deployment, model promotion,
Bedrock/Fish call or production-serving change. `PASS_SCAN_ONLY` is not B6.6
and is not full-B6 completion.

Local engineering evidence:
`platform/evidence/B6-5B-LOCAL-RELEASE-ENGINEERING-2026-001.json`.

## Required live preconditions

Use profile `medzen`, account `558069890522`, region `eu-central-1`. Refuse
before Docker authentication or mutation unless:

- caller is `arn:aws:iam::558069890522:user/s.fotso`;
- the four repositories exist, remain tag-immutable, have repository-level
  scan-on-push enabled, and use KMS key
  `arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57`;
- each exact destination tag below is absent and each repository still has
  zero images;
- the regional scanner is `BASIC` with one `SCAN_ON_PUSH` rule and the known
  three B6A filters; and
- local image labels, config digests, deployable children, runtime smokes and
  local scan hashes still match the immutable table below.

Any mismatch stops before mutation. No image is rebuilt or substituted.

## Exact immutable subjects

All four images are `linux/amd64`, use base image
`python:3.12-alpine3.22@sha256:a190708a2dec1bd18b1decb539f8e8f5407abaa9bf39cacda583f7f8c11db322`,
run as `10001:10001`, and bind source commit
`7ec176b2b69a3a552c6f135c36a8a1fc51cedc69`.

| Repository | Exact immutable tag | Local deployable child | Config digest | Local C/H |
|---|---|---|---|---:|
| `medzen-rag-index` | `b6-5b-7ec176b` | `sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c` | `sha256:6328b1046a986782f529d511bf92bec07105206d98ad879f7479b5474fd214f9` | `0 / 0` |
| `medzen-llm-gateway` | `b6-5b-7ec176b` | `sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a` | `sha256:3adea43ed479032ef3ef244d19f5f2b60fa84dc13d6ffbaaea88746cac0b560e` | `0 / 0` |
| `medzen-orchestrator` | `b6-5b-7ec176b` | `sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa` | `sha256:441cde5e33956692932f12b42343bcf4cb53af01d27df80bd9bfccdc79315d5c` | `0 / 0` |
| `medzen-speech-tts-gateway` | `b6-5b-7ec176b` | `sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d` | `sha256:ff6ee26ce64cc84ef2052baa4f87e4de7ef2b39188496860dada14ab4bf13a87` | `0 / 0` |

Local scans are prerequisites, not authoritative substitutes. The automatic
ECR result must be queried by the deployable child digest. Querying an OCI
index or tag can return `ScanNotFoundException` and is not a pass or failure.

## Exact scanner change

The current single `SCAN_ON_PUSH` rule has exactly these filters:

1. `medzen-model-loader`
2. `medzen-nvidia-dra`
3. `medzen-asr-runtime`

The packet may make one `ecr:PutRegistryScanningConfiguration` call that
preserves those three and adds exactly:

4. `medzen-rag-index`
5. `medzen-llm-gateway`
6. `medzen-orchestrator`
7. `medzen-speech-tts-gateway`

The result must remain one `BASIC` / `SCAN_ON_PUSH` rule with exactly seven
`WILDCARD` filters and no wildcard broader than a complete repository name.
Read it back before authentication. Any other delta stops the packet. The
expanded rule is retained because these are standing serving repositories;
removal is not part of this packet.

## Allowed execution

1. Reverify every live and local precondition.
2. Apply and read back only the exact seven-filter scanning configuration.
3. Authenticate Docker to
   `558069890522.dkr.ecr.eu-central-1.amazonaws.com`.
4. Tag and push only the four exact local identities to the exact immutable
   repository tags in the table.
5. Resolve each pushed `linux/amd64` child and require exact equality with the
   locally bound child before accepting its scan.
6. Wait for all four automatic scan-on-push results to reach `COMPLETE`.
7. Query each result by child digest; record every severity count and finding.
8. Persist one immutable result record with the before/after scanner config,
   ECR child identities and independent outcomes for all four subjects.

A completed critical/high result for one image does not prevent collecting
the other three scan results because no deployment is authorized. An identity,
repository, push, scanner-configuration or scanner-execution failure stops the
packet because the evidence boundary cannot be trusted.

## Deterministic outcomes

- `PASS_SCAN_ONLY`: all four child scans are `COMPLETE`, identities match, and
  every image has zero critical and zero high findings.
- `BLOCKED_IMAGE_SCAN`: any completed authoritative child scan has a critical
  or high finding, or a required authoritative scan is absent.
- `FAILED_CLOSED_EXECUTION`: identity, repository, configuration, push or
  scanner execution prevents trustworthy evaluation.

Only `PASS_SCAN_ONLY` permits preparation of a successor B6.6 packet revision.
It does not authorize SSM publication or deployment.

## Explicitly prohibited

- Rebuild, alternate tag, digest substitution or vulnerability waiver.
- Manual `ecr:StartImageScan` or treating a tag/index lookup as the child scan.
- Any repository creation, mutability/encryption change, image deletion or
  overwrite.
- IAM, KMS, S3, SSM, EKS, Kubernetes, ALB or security-group mutation.
- Node scale-up, real provider call, test traffic or service deployment.
- Writing `approved/asr/`, registering a model, changing any language serving
  field or writing a production registry pointer.
- Describing scan success as B6.6 or full-B6 success.

## Cost and reservation

- Aggregate ceiling: `$300`.
- Current conservative committed guardrail: `$62.5288`.
- Current active reservations before approval: `$0`.
- Current headroom: `$237.4712`.
- Owner approval of this exact packet activates a maximum `$1.00` allocation
  `B6-5B-ECR-SCAN-ONLY-2026-001`; headroom after reservation is `$236.4712`.
- Allocation tags are `Project=medzen-speech`, `Environment=dev`,
  `CostCenter=speech-platform`, `Stage=B6.5B`, `Workstream=ecr-scan-only`,
  `BudgetRegistry=COST-REGISTRY-2026-002`.
- GPU and CPU desired capacity remain zero; compute cost is zero. ECR storage,
  scanning and API charges are not asserted as zero.
- The result record must conservatively close or carry this allocation into a
  new cost-registry revision; no second active reservation is allowed.

No operation in this packet is authorized until independent review passes and
the owner uses the exact approval phrase at the top against the committed
packet SHA-256.
