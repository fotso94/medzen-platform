# B6 AWS change packet 2026-031 — orchestrator WebSocket successor scan-only qualification

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL**

Required approval phrase:

> Approve B6 AWS change packet 2026-031 only.

## Purpose and boundary

Push and qualify exactly one rebuilt orchestrator image against the existing
authoritative ECR scan-on-push gate. The image adds the missing exact runtime
dependency `websockets==17.0.1` and has already passed a real-container TCP
RFC 6455 handshake against `/v1/conversations/stream`.

This packet is scan-only. It authorizes no deployment, worker scale-up,
Kubernetes change, IAM change, ECR configuration change, SSM publication,
secret rotation, test traffic, model promotion or production-serving change.
`PASS_SCAN_ONLY` is not a B6 window pass.

Local evidence:
`platform/evidence/B6-WEBSOCKET-RUNTIME-LOCAL-QUALIFICATION-2026-001.json`.

## Immutable subject

| Binding | Exact value |
|---|---|
| Source commit | `f631d70ca093b39af27f073a91e297c045e88353` |
| Repository | `medzen-orchestrator` |
| Local tag | `medzen-orchestrator:b6-ws-f631d70` |
| Destination tag | `b6-ws-f631d70` |
| Platform | `linux/amd64` |
| Local deployable child | `sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed` |
| Config digest | `sha256:7fe1c01dcbac9272bde87f5b7ba83c9441b2a65bec2dea0051c6903875ca4961` |
| OCI archive SHA-256 | `99805d9418d0dc55dae4b6bd2c92e8088a2d6031ddc7c316690965fd124aca6c` |
| Runtime receipt SHA-256 | `08bae0b6b23b97d7d7eab459a6529c72f166172c53469b0d4dd87db6078d360e` |
| Local scan receipt SHA-256 | `8c188b753f906160f3f204a9586e1cd3964d06027a7763f945e71781d0524ab7` |
| Local critical / high | `0 / 0` |
| Runtime WebSocket proof | real TCP RFC 6455 `101`; final read-only container; loopback-only |

No rebuild, alternate tag, digest substitution or waiver is allowed during
execution. A local image or archive identity mismatch refuses before ECR
authentication.

## Required live preconditions

Use profile `medzen`, account `558069890522`, region `eu-central-1`. Refuse
before Docker authentication or mutation unless all of these are true:

- caller is exactly `arn:aws:iam::558069890522:user/s.fotso`;
- `medzen-orchestrator` exists, is tag-immutable, has repository scan-on-push
  enabled, and uses KMS key
  `arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57`;
- the regional scanner is `BASIC` with one `SCAN_ON_PUSH` rule whose exact
  repository-name filters already include `medzen-orchestrator`;
- destination tag `b6-ws-f631d70` is absent;
- the local image label, platform, child, config, runtime receipt, local scan
  receipt and OCI archive match the immutable table;
- the final container qualification still reports `websockets 17.0.1`, zero
  package-manager records, fixed UID/GID `10001:10001`, and a real `101`
  handshake on the exact route; and
- CPU and GPU desired capacity remain zero.

No scanner-rule update is authorized or needed.

## Exact execution

1. Re-run every read-only live and local precondition.
2. Authenticate Docker only to
   `558069890522.dkr.ecr.eu-central-1.amazonaws.com`.
3. Add exactly the remote reference
   `558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-orchestrator:b6-ws-f631d70`
   to the bound local image.
4. Push exactly that immutable tag to exactly that existing repository.
5. Resolve the pushed `linux/amd64` child. It must equal
   `sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed`.
6. Wait for the automatic scan to reach `COMPLETE`, querying by the child
   digest rather than the tag or OCI index.
7. Require zero critical and zero high findings. No waiver is permitted.
8. Persist one immutable result record containing precondition reads, local
   and ECR identities, the full severity summary and outcome.

## Deterministic outcomes

- `PASS_SCAN_ONLY`: the pushed child equals the bound child, the automatic
  child scan is `COMPLETE`, and critical/high counts are `0 / 0`.
- `BLOCKED_IMAGE_SCAN`: the authoritative child scan completes with a critical
  or high finding, or a required child scan is absent.
- `FAILED_CLOSED_EXECUTION`: any identity, tag, repository, scanner, push or
  evidence mismatch prevents trustworthy evaluation.

Only `PASS_SCAN_ONLY` permits the successor deployment manifest to bind this
child and a separate integration-window packet to be prepared. It authorizes
no deployment by itself.

## Cost boundary

`COST-REGISTRY-2026-005` remains current:

| Control | Value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Recognized committed guardrail | `$64.4286064216` |
| Existing active B6 reservation | `$10.00` |
| New reservation | `$0.00` |
| Scan-only maximum allocation inside existing reservation | `$0.25` |
| Headroom after the existing reservation | `$225.5713935784` |

ECR storage, scan and API charges are not described as zero. The scan-only
allocation does not increase or duplicate the active reservation. CPU and GPU
desired capacity remain zero throughout.

## Prohibited operations

- Any ECR scanner configuration, repository, encryption or mutability change.
- Any image other than the exact orchestrator subject or any destination other
  than its exact immutable tag.
- Image deletion, overwrite, vulnerability waiver or manual scan substitution.
- IAM, KMS, S3, SSM, Secrets Manager, EKS, Kubernetes, ALB or security-group
  mutation.
- Worker scale-up, deployment, probe task, synthetic conversation or provider
  call.
- Writing `approved/asr/`, a production registry pointer, registered model or
  language serving field.

## Next packet boundary

After `PASS_SCAN_ONLY`, the separately reviewed successor window must:

- pin the deployment to the scan-passed child, never this tag;
- preserve the immutable 2026-030A file-proof PASS without rerunning it;
- execute only the remaining live proof milestones: streaming, cancellation,
  failure drills and isolation, plus the infrastructure readiness and cleanup
  stages necessary to host those proofs;
- request two fresh, non-transferable 4,500-second attempts within the existing
  `$10` reservation; and
- retain deadline-first execution, receipt-per-stage persistence and exact
  zero-state cleanup.

No operation in this packet is authorized until independent review passes and
the owner uses the exact approval phrase at the top against the committed
packet SHA-256.
