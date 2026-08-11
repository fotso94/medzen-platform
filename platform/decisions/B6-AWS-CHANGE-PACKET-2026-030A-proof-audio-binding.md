# B6 AWS change packet 2026-030A — proof-audio single-source binding

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL**

Packet 2026-030 attempt 1 is terminal. Seventeen stages passed, including the
registry/RAG alignment, all service readiness, private endpoints, stable ALB
target health and the private Fargate readiness probe. The `file_proof` stage
then refused locally before sending an HTTP request because the probe retained
the superseded mock-tone hash as a private literal. Automatic cleanup passed
at exact zero state. This narrow successor carries only the already-authorized
second attempt; it creates no new allowance and changes no AWS boundary.

This draft authorizes **no AWS, Terraform, Kubernetes, secret, worker, service,
SSM or production mutation**.

## Immutable predecessor and continuity

| Binding | Value |
|---|---|
| Packet 2026-030 | `e1689199f5f525e29e9ef42ee6796919db45d6d59a8f8f6f9341e5f567fe4605` |
| Authorization 2026-030 | `9c6f8e65ae72e4c1cf097dfeefdec527d33e422a352cd7a719dac56de9d98482` |
| Reviewed commit | `c1a47d7c0b6bdd5facc94f9a3ee03feb4f5d6da8` |
| Review/authorization merge | `e6565734a28cfb18c612d08875a72a658d6b0abf` |
| Attempt-1 evidence | `platform/evidence/B6-PACKET-2026-030-ATTEMPT-1-REFUSED-PROBE-AUDIO-BINDING.json` |
| Attempt-1 evidence SHA-256 | `194e878ae8c55146811fa036ecaad8b7d2ede4b59383c5b7510d0962aeeedf93` |
| Terminal result | `file_proof REFUSED`; probe exit `10`; no HTTP request sent |
| Cleanup receipt | `PASS`; SHA-256 `2cdf7870ae473d7c62e83ceb066bfa81562c42ac69b4e6c7d23e565ab9b8c20d` |
| Remaining continuity | packet-2026-030 attempt `2`; maximum `4,500` seconds |

Every packet-2026-030 receipt is immutable and source-bound. Before execution,
the runner requires the attempt-1 terminal evidence, its exact cleanup receipt,
all zero-state fields and the locked second-attempt allowance. Attempt 1 cannot
be rerun and the remaining seconds cannot be transferred or enlarged.

## Root cause and complete class fix

The selected audio, deployment manifest and packet were aligned, but
`scripts/b6_6_probe.py` separately hardcoded the older B6.3 tone hash. The
client therefore failed the `SYNTHETIC_WAV_SHA256_MATCHES` assertion before it
could exercise the service.

The corrected design has one executable source of truth:
`scripts/b6_6_proof_audio_binding.py`.

Proof-audio SHA-256: `3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3`

The operations dispatcher imports that value and passes it to the probe only
through `MEDZEN_B6_PROOF_AUDIO_SHA256`. The probe requires a well-formed value
from that environment variable and contains neither the current hash nor the
superseded hash as a private literal. The deployment ConfigMap and this packet
are reviewed projections of the same source. A missing, malformed or unequal
projection refuses before any AWS call.

## Drift-killing consistency gate

The fresh cold rehearsal audits the complete proof-audio binding family:

- the selected WAV bytes hash to the binding source;
- the deployment ConfigMap projection equals the binding source;
- the single packet projection equals the binding source;
- the probe contains no private proof-audio literal and reads the named
  environment variable;
- the operations dispatcher imports the source and supplies that variable;
- independent drift injections into the binding, manifest and packet each
  refuse with `PROOF_AUDIO_PROJECTION_HASH_DRIFT`.

This is a standing consistency rule. Adding a second private literal or
allowing any projection to drift makes the cold rehearsal fail closed.

## Unchanged window boundary

Apart from the proof-audio source correction and continuity enforcement, the
reviewed 2026-030 boundary is unchanged:

- successful 2026-026 Stage A is reused and cannot be rerun;
- credential rotation and exact registry/RAG verification remain stage zero;
- deadline is armed before worker scale-up;
- maximum workers remain two `m6i.large` CPU nodes and one `g6.xlarge` GPU node;
- all seven service images and the controller remain pinned to scan-passed
  child manifests;
- endpoint, ALB, Fargate, conversation, drill and isolation stages are
  unchanged;
- every PASS or REFUSED stage persists a durable receipt;
- cleanup remains status-keyed and must prove stable exact zero state;
- traffic is synthetic-only, with no PHI and no production traffic.

## Fresh cold rehearsal

The immutable receipt is written once at
`platform/evidence/receipts/B6-2026-030A-COLD/cold_rehearsal.json`. This packet
is deliberately an input to that rehearsal, so it does not embed the receipt's
future hash. Independent review must bind both this immutable packet SHA-256
and the separately generated cold-rehearsal SHA-256. The packet may not be
edited after the rehearsal is generated.

Prepared rehearsal and verification boundary:

| Check | Result |
|---|---:|
| Full simulated window PASS | `1` |
| Existing injected refusals | `43` |
| Proof-audio drift refusals | `3` |
| Total injected refusals | `46` |
| Stage A simulated PASS / refusals | `1 / 7` |
| Two independent canonical payloads | byte-identical |
| Real AWS / kubectl calls | `0 / 0` |
| AWS / Kubernetes mutations | `0 / 0` |
| Canonical repository suite | `1,492 passed, 0 failed, 0 skipped, 7 deselected` |
| Known warning | `1` Starlette/httpx deprecation warning |
| Terraform fmt / backend-disabled validate | `PASS / PASS` |
| Python compile / shell syntax / diff check | `PASS / PASS / PASS` |

## Remaining allowance continuity

`COST-REGISTRY-2026-005` remains the latest reconciled ledger. Packet 2026-030
authorized two non-transferable 4,500-second attempts within the existing
`$10` reservation. Attempt 1 consumed its slot and cleaned up. This successor
requests continuity of the one locked attempt only.

| Control | Bound value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Recognized committed guardrail | `$64.4286064216` |
| Existing active reservation | `$10.00` |
| New reservation | `$0.00` |
| Remaining attempts | `1` — packet-2026-030 attempt 2 only |
| Maximum duration | `4,500` seconds, non-transferable |
| Maximum requested worker seconds | `4,500` |
| Estimated remaining compute | approximately `$1.60` |
| Stage A runs | `0` |
| Fresh pre-attempt cold rehearsal | required |

## Prohibited operations

No production SSM pointer, `approved/asr/` object, model registration, MLflow
stage transition, fine-tune adoption, production traffic, PHI, real client
credential, real Bedrock call or Fish call is permitted. No unreviewed IAM,
Terraform, image, dependency, registry, model, source, scope or safety-boundary
change is permitted. No packet-2026-030 attempt 1 rerun and no attempt after
the carried attempt is permitted.

## Deviations

No deviation from the requested narrow successor is taken. The existing
packet-2026-030 no-publication deviation remains accepted historical context
and is not reopened or reinterpreted.

## Approval boundary

Independent review must bind the prepared clean commit, this exact packet
SHA-256, the fresh cold-rehearsal SHA-256, the proof-audio single-source audit,
the three drift refusals, the immutable attempt-1 evidence and the single
remaining-attempt arithmetic. Only after review `PASS` may the owner state
exactly:

> Approve B6 AWS change packet 2026-030A only, continuing the single remaining
> non-transferable 4,500-second attempt within the existing $10 reservation.
