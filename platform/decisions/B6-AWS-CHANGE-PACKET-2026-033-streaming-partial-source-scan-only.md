# B6 AWS change packet 2026-033 — streaming partial-source successor scan-only qualification

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL**

Required approval phrase:

> Approve B6 AWS change packet 2026-033 only.

## Purpose and determination

Push and qualify exactly one rebuilt orchestrator image against the existing
authoritative ECR scan-on-push gate. The predecessor 2026-032A window proved
that the WebSocket handshake and session open worked, then correctly closed
with code `4503` because the deployed image lacked its streaming partial-source
fixture.

The file path passed because it never constructs that streaming-only source.
The earlier container qualification masked the packaging defect by mounting
the host testdata tree. The successor packages and hash-verifies the exact
fixture, reports its state in readiness, and clones the verified source for
each streaming session.

Code `4503` remains the correct fail-closed response when a required streaming
dependency is unavailable. It is a PASS only for an intentional dependency-
unavailable drill; it cannot satisfy the streamed-conversation milestone.
That milestone requires a dependency-ready full conversation to pass.

Local evidence:
`platform/evidence/B6-WEBSOCKET-PARTIAL-SOURCE-LOCAL-QUALIFICATION-2026-001.json`.

This packet is scan-only. It authorizes no deployment or integration attempt.

## Immutable subject

| Binding | Exact value |
|---|---|
| Source commit | `bfaad78f9de5e40ab0602244ece05007aed43f12` |
| Repository | `medzen-orchestrator` |
| Local tag | `medzen-orchestrator:b6-ws-bfaad78` |
| Destination tag | `b6-ws-bfaad78` |
| Platform | `linux/amd64` |
| Local deployable child | `sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3` |
| Config digest | `sha256:35e563713752a05418cde7dacaa50dd2939f787ac627e1d17471b1e271518e5a` |
| OCI archive SHA-256 | `399cbb9352619046e484c00f9e2836eaf065da6a43f1c3bd18e60e32c8628474` |
| Runtime receipt SHA-256 | `c7de1571d408cf1a7df9c002efb0fa1364c81b754668d34b4a4994e0892e63db` |
| Local scan receipt SHA-256 | `8c188b753f906160f3f204a9586e1cd3964d06027a7763f945e71781d0524ab7` |
| Packaged fixture SHA-256 | `f5e6c57c3d8a57d80980ee3741723b36ae810e03aea10d2057fa2c30776a90fc` |
| Probe/app pair SHA-256 | `f6c8eb872cbd80c5542350e0c4ac5c0b1cff82d820d94ab452ef12cba816a9d6` |
| Local critical / high | `0 / 0` |

No rebuild, alternate tag, digest substitution or waiver is allowed during
execution. An identity mismatch refuses before ECR authentication.

## Local qualification already passed

- The image starts read-only as UID/GID `10001:10001`, with zero package-
  manager records and `websockets==17.0.1`.
- A deliberately missing partial source returns readiness `503` with safe
  reason `STREAMING_PARTIAL_SOURCE_UNAVAILABLE`. This reproduces the live
  dependency class without AWS.
- The aligned image returns a real TCP RFC 6455 `101` handshake and passes the
  exact window WebSocket conversation three consecutive times: open, auth,
  audio chunks, five partials, final transcript, reply text and completion.
- The final result is preserved and the partial/audio queue limits remain 4/8.
- Docker Scout 1.18.3 indexed 25 packages with zero critical and zero high
  findings. No security waiver was used.
- Canonical local tests: 1,528 passed, 0 failed, 0 skipped, 7 deselected and
  1 warning.

## Required live preconditions

Use profile `medzen`, account `558069890522`, region `eu-central-1`. Refuse
before Docker authentication or mutation unless all of these are true:

- caller is exactly `arn:aws:iam::558069890522:user/s.fotso`;
- `medzen-orchestrator` exists, is tag-immutable, has repository scan-on-push
  enabled, and still uses its reviewed KMS encryption key;
- the regional BASIC scanner retains its existing `SCAN_ON_PUSH` rule for the
  repository;
- destination tag `b6-ws-bfaad78` is absent;
- the local image, archive, source hashes, receipt hashes, fixture hash and
  three-conversation qualification match this packet; and
- CPU and GPU desired capacity are both zero.

No scanner, repository, IAM, KMS or policy change is authorized.

## Exact execution

1. Re-run all read-only preconditions and immutable local checks.
2. Authenticate Docker only to the existing MedZen ECR registry.
3. Add only the exact destination reference to the bound local image.
4. Push exactly that immutable tag to `medzen-orchestrator`.
5. Resolve the pushed `linux/amd64` child and require it to equal the bound
   local deployable child.
6. Wait for the automatic scan to reach `COMPLETE`, querying the child digest,
   never the tag or OCI index.
7. Require zero critical and zero high findings. No waiver is permitted.
8. Persist an immutable result with every precondition, identity, scan summary
   and deterministic outcome.

## Deterministic outcomes

- `PASS_SCAN_ONLY`: exact child identity, automatic child scan `COMPLETE`, and
  critical/high counts `0 / 0`.
- `BLOCKED_IMAGE_SCAN`: any critical/high finding or absent authoritative child
  scan.
- `FAILED_CLOSED_EXECUTION`: any subject, repository, tag, scanner, push or
  evidence mismatch.

`PASS_SCAN_ONLY` only permits a later window packet to bind the scanned child.
It is not a deployment pass or a B6 streaming pass.

## Cost boundary

`COST-REGISTRY-2026-005` remains the current reconciled guardrail:

| Control | Value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Recognized committed guardrail | `$64.4286064216` |
| Existing active B6 reservation | `$10.00` |
| New reservation | `$0.00` |
| Scan-only maximum inside existing reservation | `$0.25` |
| Headroom after existing reservation | `$225.5713935784` |

ECR storage, scanning and API usage are not described as free. CPU and GPU
remain at zero throughout.

## Prohibited operations

- Any deployment, Kubernetes mutation, worker scale-up, probe task, ALB,
  endpoint, security-group or synthetic live conversation.
- IAM, KMS, S3, SSM, Secrets Manager, EKS, registry-pointer or secret change.
- Any image other than the exact bound orchestrator subject.
- Scanner/repository configuration change, image overwrite/deletion, waiver or
  manual scan substitution.
- Writing `approved/asr/`, production SSM, a registered model or any language
  serving field.

## Successor boundary

After `PASS_SCAN_ONLY`, a separately reviewed window packet must pin the
scan-passed child, preserve the immutable file-proof PASS, and run only the
remaining streaming, cancellation, failure-drill and isolation proofs plus
their required readiness and cleanup stages. Packet 2026-033 grants no window
attempt or compute allowance. A fresh owner allowance decision is required.

No operation in this packet is authorized until independent review passes and
the owner uses the exact approval phrase above against the committed packet
SHA-256.
