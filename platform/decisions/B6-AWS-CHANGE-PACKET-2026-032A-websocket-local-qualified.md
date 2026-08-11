# B6 AWS change packet 2026-032A — locally qualified WebSocket continuity attempt

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL**

Required approval phrase:

> Approve B6 AWS change packet 2026-032A only, including continuity of the
> single unused non-transferable 4,500-second attempt within the existing $10
> reservation.

## Purpose and continuity

Carry only packet-2026-032 continuity attempt 2 after satisfying the owner's
local WebSocket qualification directive. This is not a new attempt or an
allowance reset. Packet 2026-032 authorized two attempts; attempt 1 refused at
`websocket_proof`, cleanup passed, and exactly one attempt remains.

The immutable attempt-1 result is
`platform/evidence/B6-PACKET-2026-032-ATTEMPT-1-TERMINAL-WEBSOCKET-FRAME-REFUSAL.json`,
SHA-256 `9d9efd1b561f859f324f0556f38432c91f77b2ec2124effb8f24d829b8582877`.
It records the refusal, all 17 preceding PASS stages and exact zero-state
cleanup. It is not regenerated or reinterpreted.

The file proof passed in packet 2026-030A and **must not be rerun**. Its
immutable receipt remains
`platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json`, SHA-256
`808d160e391998e3f534d8776342e58337ebb4a200ffaab58fcc43e586c60c89`.

This packet authorizes no AWS, Terraform, Kubernetes, secret, worker or
traffic mutation before independent review PASS and the exact owner approval
phrase above.

## Local qualification prerequisite — PASS

The exact window client now runs the entire synthetic stream against the real
containerized orchestrator with checksum-bound fake dependencies:

`open → auth → audio chunks → partials → final → close`

The resulting event sequence was:

`ready → 5 partial_transcript → final_transcript → reply_text → completed`

The immutable local receipt is
`platform/evidence/b6-websocket-runtime/medzen-orchestrator.full-conversation.json`,
SHA-256 `daeca28c81eb75e22f8fa9453e9b55f45c3fc7d06e86868da4442fcb021de56e`.
It binds:

- exact probe SHA-256
  `d0ab3f1d230a80814e5cbfa21a63bb422a534da5d30d86aeffef5a16ac01b1e2`;
- in-image application SHA-256
  `87e5c6ac2e334ba95b62294b6f91de6a0a703f1ee991d6bb15b985f6aef75289`;
- probe/application pair SHA-256
  `e68098b4d3b1722bb37c0851be770bcf51bf656a24476c264f141a5361866a9b`;
- the B6A-proven spoken fixture SHA-256
  `3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3`;
  and
- separate generated registry/ASR overlays mounted read-only only for this
  qualification.

The historical one-second tone, its ASR binding and the historical local
registry fixture remain byte-unchanged. Local qualification used no AWS,
Kubernetes or cloud spend.

## Diagnostic and standing-rule correction

The reviewed probe now classifies every received frame. A close frame persists
its frame type, numeric close code and bounded sanitized reason. A server
`error` event has its own assertion and exit code rather than being destroyed
by a later close. The runtime receipt parser retains these fields only for a
synthetic REFUSED proof receipt and rejects malformed or credential-like
diagnostics.

`runtime-image-hardening-v2.md` now makes this permanent: a real RFC 6455
handshake is necessary but insufficient. Every WebSocket serving image must
also pass the exact full synthetic deployment-window conversation, with the
passing probe/application pair bound by hash.

## Images and live scope

No image was rebuilt, pushed or substituted. The orchestrator remains the
packet-2026-031 scan-passed child:

`558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-orchestrator@sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed`

The packet-2026-031 scan result is SHA-256
`bc7b2a523114f692921492125574b712e65543f02e7dcb47d9f9e8c6fe6f427d`.
All other seven digest-pinned image identities, manifests, Terraform
resources, IAM boundaries, endpoint policies and ALB controls remain identical
to packet 2026-032.

The only live proofs are:

1. `websocket_proof`;
2. `cancellation_proof`;
3. `failure_drills`; and
4. `isolation_proof`.

## Authorization and preconditions

After review PASS and exact approval, a new versioned
`B6-AWS-AUTH-2026-032A` record must bind the reviewed commit, this packet hash,
the cold-rehearsal hash, all source hashes, the attempt-1 terminal result, the
local full-conversation receipt, and the continuity allowance. The runner
refuses without that committed record.

Before continuity attempt 2, it also requires:

- profile `medzen`, account `558069890522`, region `eu-central-1`;
- a clean descendant of the independently reviewed commit;
- packet-2026-032 attempt-1 REFUSED plus PASS cleanup and exact zero state;
- the local full-conversation PASS and exact probe/application pair;
- the packet-2026-031 zero-finding child scan;
- the immutable file-proof PASS with `file_proof_rerun=false`;
- passing packet-2026-026 Stage A receipts without rerunning Stage A;
- exact registry/RAG read-back and an absent production serving pointer;
- zero workers, workload nodes, synthetic pods, controller, DRA, window
  endpoints and window ALB; and
- a fresh deterministic rehearsal byte-equal in payload to the reviewed cold
  receipt.

Only `--attempt 2` and receipt directory
`platform/evidence/receipts/B6-2026-032A-A2-LIVE` are accepted.

## Exact stage sequence

Every stage writes a durable PASS or REFUSED receipt before cleanup:

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

`file_proof` remains deliberately absent. Stage 0 binds its preserved receipt.

## Allowance

`COST-REGISTRY-2026-005` remains the bound reconciled ledger.

| Control | Bound value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Recognized committed guardrail | `$64.4286064216` |
| Existing active B6 reservation | `$10.00` |
| New reservation | `$0.00` |
| Original packet attempts authorized / consumed | `2 / 1` |
| Continuity attempt number | `2` |
| Attempts carried by this packet | `1` |
| Maximum worker window | `4,500 seconds` |
| Maximum requested worker seconds | `4,500` |
| Estimated compute | approximately `$1.60` |
| Time transfer or rollover | prohibited |

The single unused non-transferable 4,500-second attempt remains within the
existing $10 reservation. Any further attempt, duration extension or allowance
transfer requires a fresh owner decision.

## Deterministic outcomes and cleanup

- `PASS_REMAINING_PROOFS`: all four remaining proofs and cleanup PASS;
- `REFUSED_CLEAN`: one stage refuses, its enriched diagnostic is durable, and
  cleanup proves exact zero; or
- `REFUSED_CLEANUP_INCOMPLETE`: cleanup cannot prove zero and the automatic
  deadline remains armed as the backstop.

Cleanup stays status-keyed: stop the private probe, remove Ingress and ALB,
delete synthetic workloads and DRA, destroy only the reviewed temporary
Terraform resources, wait for endpoint absence, scale CPU/GPU to zero, remove
deadline actions and local material, retain the operator-denied synthetic
secret, and prove three stable zero observations.

## Prohibited operations

- Rerunning or altering the immutable file proof, attempt-1 receipts, terminal
  result, scan result or historical local fixtures.
- Any attempt other than continuity attempt 2.
- Any new attempt, time transfer, duration extension or added reservation.
- Any image rebuild, push, tag, unscanned digest or child substitution.
- Production traffic, PHI, real provider calls or real client credentials.
- Production SSM, `approved/asr/`, model registration, MLflow transition,
  serving-field change or B7 transition.
- Any unreviewed IAM, KMS, S3, SSM, secret, network, source, scope or safety
  boundary change.

## Cold rehearsal and prepared verification

The write-once cold receipt will be
`platform/evidence/receipts/B6-2026-032A-COLD/cold_rehearsal.json`. It must run
one full PASS and one refusal at each of the 22 receipt stages, prove cleanup
after every refusal, create no file-proof receipt, bind the local conversation
receipt, and make zero AWS/kubectl calls.

Prepared local verification:

| Check | Result |
|---|---:|
| Canonical repository suite | `1,521 passed, 0 failed, 0 skipped, 7 deselected` |
| Focused B6/orchestrator/streaming suite | `608 passed, 0 failed` |
| Exact full container conversation | `PASS` |
| Fresh cold PASS / injected refusals | `1 / 22` |
| File-proof receipts created by rehearsal | `0` |
| Real AWS / kubectl calls in rehearsal | `0 / 0` |
| Terraform fmt / validate | `PASS / PASS` |
| Python compile / shell syntax / YAML parse / generated-fixture check | `PASS` |
| Known warning | `1` existing Starlette/httpx deprecation warning |

## Deviations

The owner described this as packet-2026-032 attempt 2. The executable source
hashes reviewed for packet 2026-032 are immutable and necessarily changed to
add the required qualification and diagnostics. Therefore `032A` is a
numbering-only governance successor. It carries the same original attempt 2,
adds no attempt, time or reservation, and does not loosen any live boundary.
