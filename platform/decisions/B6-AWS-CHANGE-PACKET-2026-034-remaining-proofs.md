# B6 AWS change packet 2026-034 — partial-source-aligned remaining proofs

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL**

Required approval phrase:

> Approve B6 AWS change packet 2026-034 only, including two non-transferable
> 4,500-second attempts within the existing $10 reservation.

## Purpose

Run only the four B6 integration proofs that remain after the immutable file
proof passed:

1. streamed WebSocket conversation;
2. cancellation and barge-in;
3. controlled failure drills; and
4. network isolation.

Packet 2026-032A correctly refused at `websocket_proof` with close code `4503`
because the deployed orchestrator image lacked its required streaming partial-
source fixture. Its terminal result remains immutable at
`platform/evidence/B6-PACKET-2026-032A-ATTEMPT-2-TERMINAL-DEPENDENCY-REFUSAL.json`,
SHA-256 `94a67cfcd9dd5541acf721dc05634559930dfdf5d4b685c26da02b3c9a1cf67a`.
Cleanup passed and the prior allowance is exhausted.

The corrected image packages and hash-verifies the fixture, passed three full
local streamed conversations, and completed the authoritative packet-2026-033
ECR scan with zero findings. The scan result is
`platform/evidence/B6-PACKET-2026-033-SCAN-RESULT.json`, SHA-256
`fe7548a9555c35cb0dd1c0508c07f0eae028c809f370b36af95f9e66f7f7493d`.

No AWS, Terraform, Kubernetes, secret, worker or traffic mutation is authorized
before independent review PASS and the exact owner approval phrase above.

## Immutable image binding

The orchestrator must run only:

`558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-orchestrator@sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3`

The ECR child manifest, image configuration and runtime evidence bind:

| Binding | Exact value |
|---|---|
| Child manifest | `sha256:475ff8520e7ff78a52208a1bebe1de78c2a257de112424a837d0f5e1a73d2dc3` |
| Image configuration | `sha256:35e563713752a05418cde7dacaa50dd2939f787ac627e1d17471b1e271518e5a` |
| Packaged partial-source fixture | `f5e6c57c3d8a57d80980ee3741723b36ae810e03aea10d2057fa2c30776a90fc` |
| Probe/application pair | `f6c8eb872cbd80c5542350e0c4ac5c0b1cff82d820d94ab452ef12cba816a9d6` |
| Scan result | `PASS_SCAN_ONLY`, zero critical/high/total findings, no waiver |

All other seven digest-pinned image identities remain byte-identical to the
reviewed remaining-proofs platform. Tags are never used for deployment.

## Local dependency qualification

The immutable local qualification is
`platform/evidence/B6-WEBSOCKET-PARTIAL-SOURCE-LOCAL-QUALIFICATION-2026-001.json`,
SHA-256 `5cdd5ebd8b7159ac010f67565aad26d0aea5c6be8e6b35836f43ed2ae2673eb3`.
It proves both required sides of the safety boundary:

- deliberately absent source: readiness `503`, reason
  `STREAMING_PARTIAL_SOURCE_UNAVAILABLE`, fail closed; and
- aligned packaged source: real RFC 6455 `101`, then three identical full
  conversations ending in `final_transcript → reply_text → completed` with the
  final result preserved.

A legitimate dependency-gate refusal is a PASS for the negative drill only.
It does not satisfy the live streamed-conversation milestone, which requires
the dependency-ready positive path.

## Preserved milestones and reused qualification

The file proof is not rerun. Its immutable PASS receipt remains
`platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json`, SHA-256
`808d160e391998e3f534d8776342e58337ebb4a200ffaab58fcc43e586c60c89`.
It must not be rerun.

Packet-2026-026 Stage A remains qualified with three consecutive probe passes
and PASS cleanup. Its receipts are reused by hash and Stage A is not rerun.

## Authorization and refusal gates

After review PASS and exact approval, a new versioned
`B6-AWS-AUTH-2026-034` record must bind the reviewed commit, this packet hash,
the cold-rehearsal hash, every source hash, the packet-2026-033 scan result,
the local dual-path qualification, the prior terminal refusal, the preserved
file proof, Stage A receipts and the allowance. The runner refuses without the
committed record.

Before each attempt it requires:

- profile `medzen`, account `558069890522`, region `eu-central-1`;
- a clean descendant of the independently reviewed commit;
- a fresh cold rehearsal byte-equal in payload to the reviewed receipt;
- exact registry/RAG read-back and an absent production serving pointer;
- the scan-passed orchestrator child and every other reviewed child digest;
- the immutable file proof with `rerun=false`;
- zero workers, workload nodes, synthetic workloads, controller, DRA, window
  endpoints and window ALB;
- a fresh synthetic credential generated once at stage 0; and
- for attempt 2, an attempt-1 REFUSED receipt, PASS cleanup and exact zero state.

Only attempts 1 and 2 and receipt directories
`platform/evidence/receipts/B6-2026-034-A1-LIVE` and
`platform/evidence/receipts/B6-2026-034-A2-LIVE` are accepted. A PASS on attempt
1 terminates the packet and makes attempt 2 unavailable.

## Exact stage sequence

Every stage persists PASS or REFUSED before cleanup:

1. `stage0`;
2. `deadline`;
3. `workers_ready`;
4. `dra_ready`;
5. `rag_ready`;
6. `asr_ready`;
7. `tts_ready`;
8. `llm_ready`;
9. `orchestrator_ready`;
10. `controller_window`;
11. `controller_ready`;
12. `pre_endpoint_images`;
13. `terraform_window`;
14. `endpoints_ready`;
15. `alb_ready`;
16. `fargate_probe`;
17. `alb_tag_mutation_warning`;
18. `websocket_proof`;
19. `cancellation_proof`;
20. `failure_drills`;
21. `isolation_proof`;
22. `cleanup`.

`file_proof` is deliberately absent and only its immutable receipt is bound.

## Allowance request

`COST-REGISTRY-2026-005` remains the bound reconciled guardrail.
This packet requests two non-transferable 4,500-second attempts within the
existing `$10` reservation.

| Control | Bound value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Recognized committed guardrail | `$64.4286064216` |
| Existing active B6 reservation | `$10.00` |
| New reservation | `$0.00` |
| Fresh attempts requested | `2` |
| Maximum per attempt | `4,500 seconds` |
| Maximum requested worker seconds | `9,000` |
| Estimated compute for both attempts | approximately `$3.20` |
| Attempt transfer or rollover | prohibited |

The attempts are non-transferable. Unused seconds from one attempt cannot be
added to the other. A PASS terminates the packet. A third attempt, duration
extension or reservation increase requires a new owner decision.

## Deterministic outcomes and cleanup

- `PASS_REMAINING_PROOFS`: all four remaining proofs and cleanup PASS;
- `REFUSED_CLEAN`: one stage refuses, its safe diagnostics are durable, and
  cleanup proves exact zero; or
- `REFUSED_CLEANUP_INCOMPLETE`: cleanup cannot prove zero and the independent
  deadline remains the backstop.

Cleanup remains status-keyed: stop the private probe, remove Ingress/ALB,
delete only synthetic workloads and DRA, destroy only reviewed temporary
Terraform resources, wait for endpoint absence, scale CPU/GPU to zero, remove
deadline actions and local material, retain the operator-denied synthetic
secret, and prove three stable zero observations.

## Prohibited operations

- Rerunning or altering the file proof, Stage A, prior refusal, scan result or
  historical evidence.
- Any image rebuild, push, tag deployment, unscanned child or digest
  substitution.
- Any proof outside streaming, cancellation, drills and isolation.
- More than two attempts, more than 4,500 seconds per attempt, time transfer,
  allowance rollover or reservation expansion.
- Production traffic, PHI, real provider calls or real client credentials.
- Production SSM, `approved/asr/`, model registration, MLflow transition,
  serving-field change or B7 transition.
- Any unreviewed IAM, KMS, S3, SSM, secret, network, scope or safety-boundary
  change.

## Cold rehearsal acceptance

The write-once cold receipt is
`platform/evidence/receipts/B6-2026-034-COLD/cold_rehearsal.json`. It must:

- run one complete PASS;
- inject a refusal at each of the 22 receipt stages;
- additionally inject `STREAMING_PARTIAL_SOURCE_UNAVAILABLE` at
  `websocket_proof`, retaining HTTP `503`, close code `4503` and the exact safe
  reason;
- prove cleanup after every refusal;
- create zero file-proof receipts;
- verify both fresh attempts are bounded independently at 4,500 seconds;
- bind only the packet-2026-033 scan-passed orchestrator child; and
- make zero AWS or kubectl calls.

No operation in this packet is authorized until independent review passes and
the owner uses the exact approval phrase at the top against the committed
packet SHA-256 and cold-rehearsal SHA-256.

## Deviations

None.
