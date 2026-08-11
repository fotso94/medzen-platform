# B6 AWS change packet 2026-032 — remaining live proofs

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL**

Required approval phrase:

> Approve B6 AWS change packet 2026-032 only, including two non-transferable
> 4,500-second attempts within the existing $10 reservation.

## Purpose and predecessor

Run only the four B6 live proofs that remain after the immutable file proof:

1. WebSocket streaming;
2. cancellation and barge-in within 250 ms;
3. controlled dependency-failure drills; and
4. network isolation.

Packet 2026-031 completed `PASS_SCAN_ONLY`. Its immutable result is
`platform/evidence/B6-PACKET-2026-031-SCAN-RESULT.json`, SHA-256
`bc7b2a523114f692921492125574b712e65543f02e7dcb47d9f9e8c6fe6f427d`.
The authoritative child scan completed with zero findings and no waiver.

The file proof already passed in packet 2026-030A attempt 2. Its receipt is
`platform/evidence/receipts/B6-2026-030A-A2-LIVE/file_proof.json`, SHA-256
`808d160e391998e3f534d8776342e58337ebb4a200ffaab58fcc43e586c60c89`.
It is preserved as an immutable prerequisite and **must not be rerun**.

This draft authorizes no AWS, Terraform, Kubernetes, secret, worker, traffic
or production mutation before independent review and exact owner approval.

## Exact image rebind

Only the orchestrator image identity changes from the 2026-030A deployment.
The replacement child is the exact image qualified by packet 2026-031:

`558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-orchestrator@sha256:a3bd7170dbef4541ff6286324974a79d0b0da2287dcdcaf8f77a20654c7befed`

It contains `websockets==17.0.1` and passed a real-container TCP RFC 6455
handshake with HTTP `101` against `/v1/conversations/stream`. The deployment,
readiness and pre-endpoint-residency checks all bind the child digest, never
the tag. The previous orchestrator digest is absent from every successor
executable projection.

All other scan-passed child manifests remain unchanged:

| Pod / image | Child manifest digest |
|---|---|
| Load Balancer Controller | `sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f` |
| NVIDIA DRA | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` |
| RAG index | `sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c` |
| Model loader | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` |
| ASR runtime | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` |
| TTS gateway | `sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d` |
| LLM gateway | `sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a` |

## Authorization and preconditions

After review PASS and exact owner approval, execution first creates a
versioned `B6-AWS-AUTH-2026-032` record binding the reviewed commit, packet
SHA-256, cold-rehearsal SHA-256, source hashes, immutable predecessor evidence,
proof scope and allowance. The runner refuses without that committed record.

Before each attempt it also requires:

- profile `medzen`, account `558069890522`, region `eu-central-1`;
- a clean descendant of the independently reviewed commit;
- the packet-2026-031 `PASS_SCAN_ONLY` result and exact new child digest;
- the immutable file-proof `PASS` receipt and `file_proof_rerun=false`;
- the passing packet-2026-026 Stage A qualification and cleanup receipts;
- exact test-registry read-back and an absent production serving pointer;
- zero CPU/GPU desired capacity, workload nodes, synthetic pods, controller,
  DRA objects, window endpoints and window load balancers;
- the persistent synthetic test secret with operator plaintext denial; and
- a fresh deterministic cold rehearsal equal to the reviewed receipt.

Attempt 2 is locked unless attempt 1 has a durable REFUSED stage, a PASS
cleanup receipt and exact zero-state proof. A PASS attempt terminates this
packet. Seconds are not transferable between attempts.

## Exact stage sequence

Every stage writes a durable PASS or REFUSED receipt before cleanup proceeds:

1. `stage0` — validate all bindings, recheck registry/RAG identity, rotate one
   fresh synthetic token, and retain the exact created version;
2. `deadline` — arm the 4,500-second CPU/GPU automatic scale-down first;
3. `workers_ready`;
4. `dra_ready`;
5. `rag_ready`;
6. `asr_ready`;
7. `tts_ready`;
8. `llm_ready`;
9. `orchestrator_ready` — require the new scan-passed child;
10. `controller_window`;
11. `controller_ready`;
12. `pre_endpoint_images` — prove all seven pods and eight child manifests are
    Running, Ready and resident before private ECR DNS is introduced;
13. `terraform_window`;
14. `endpoints_ready`;
15. `alb_ready` — hostname, active ALB and stable healthy target;
16. `fargate_probe` — private readiness probe with bounded layered retries;
17. `alb_tag_mutation_warning`;
18. `websocket_proof`;
19. `cancellation_proof`;
20. `failure_drills`;
21. `isolation_proof`;
22. `cleanup` — status-keyed removal and three stable exact-zero observations.

`file_proof` is deliberately absent from the executable stage list and from
the dispatcher. The stage-0 receipt binds the preserved receipt hash instead.

## Allowed temporary mutations

Within each approved attempt only, the packet may:

- rotate the existing synthetic test secret once without reading an old value;
- arm and later remove exact CPU/GPU deadline actions;
- scale to at most two `m6i.large` CPU nodes and one `g6.xlarge` GPU node;
- install the already-reviewed DRA and Load Balancer Controller artifacts;
- deploy the digest-pinned synthetic service stack;
- create the previously reviewed temporary probe role, ECS task definition and
  cluster, endpoint-only security group/rules, three VPC endpoints and internal
  ALB path;
- run only the bounded synthetic readiness and remaining-proof traffic; and
- remove all temporary resources and scale both worker groups to zero.

No real Bedrock or Fish call is permitted. TTS remains text-only. The test
traffic contains no PHI and no real client credential.

## Deterministic outcomes

- `PASS_REMAINING_PROOFS`: all four remaining proof receipts and cleanup PASS;
- `REFUSED_CLEAN`: one stage refuses, its diagnostic receipt is durable, and
  cleanup proves exact zero state; or
- `REFUSED_CLEANUP_INCOMPLETE`: cleanup cannot prove zero and the automatic
  deadline remains the backstop. No later attempt may begin until a live
  read-only sweep proves zero and a separately reviewed correction exists.

The already-passed file proof remains PASS under every outcome and is never
voided by a later refusal.

## Allowance

`COST-REGISTRY-2026-005` is the bound reconciled ledger.

| Control | Bound value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Recognized committed guardrail | `$64.4286064216` |
| Existing active B6 reservation | `$10.00` |
| New reservation | `$0.00` |
| Requested attempts | `2` |
| Maximum per attempt | `4,500` seconds |
| Maximum requested worker seconds | `9,000` seconds |
| Estimated two-attempt compute | approximately `$3.20` |
| Unused-time transfer | prohibited |
| PASS behavior | terminates packet |

The two attempts are fresh and non-transferable. This packet neither enlarges
nor duplicates the existing reservation.

## Cleanup and zero-state closure

Cleanup is keyed to resources that actually exist, not to how far the happy
path was expected to run. It stops the probe, removes the Ingress and waits for
ALB absence, deletes synthetic Kubernetes workloads and DRA, destroys only the
reviewed temporary Terraform resources, waits for endpoint absence, scales CPU
and GPU to zero, removes deadline actions and local token/hostname material,
and proves three stable zero observations. The persistent synthetic secret is
retained with operator plaintext access denied.

## Prohibited operations

- Rerunning or altering the immutable file proof or any historical evidence.
- A third attempt, time transfer, duration extension or added reservation.
- Any image tag at deployment time or any unscanned/substituted child digest.
- Production traffic, PHI, real provider calls or real client credentials.
- Production SSM pointer updates, `approved/asr/` writes, model registration,
  MLflow transition, fine-tune adoption, registry serving-field change or B7
  transition.
- Any unreviewed IAM, KMS, S3, SSM, secret, network, image, source, scope or
  safety-boundary change.

## Cold rehearsal and review boundary

The fresh write-once receipt is
`platform/evidence/receipts/B6-2026-032-COLD/cold_rehearsal.json`. It runs one
complete PASS scenario and one refusal injection at every one of the 22
receipt stages, proves cleanup after every refusal, verifies zero AWS/kubectl
calls, confirms no `file_proof` receipt is created, and audits all three
successor digest projections against the packet-2026-031 scan result.

This packet deliberately does not embed the future cold-rehearsal hash.
Independent review must bind the prepared commit, this exact packet SHA-256
and the separately generated cold-rehearsal SHA-256. No execution may begin
until the reviewer reports PASS and the owner uses the exact approval phrase
at the top.

Prepared local verification:

| Check | Result |
|---|---:|
| Canonical repository suite | `1,516 passed, 0 failed, 0 skipped, 7 deselected` |
| Focused B6/runtime/successor suite | `173 passed, 0 failed` |
| Fresh cold PASS / injected refusals | `1 / 22` |
| File-proof receipts created by rehearsal | `0` |
| Real AWS / kubectl calls in rehearsal | `0 / 0` |
| Terraform fmt / validate | `PASS / PASS` |
| Python compile / shell syntax / YAML parse / diff check | `PASS` |
| Known warning | `1` existing Starlette/httpx deprecation warning |

## Deviations

None. The file-proof milestone stands exactly as directed; only the remaining
proofs are live. No Stage A, registry publication or image push is repeated.
