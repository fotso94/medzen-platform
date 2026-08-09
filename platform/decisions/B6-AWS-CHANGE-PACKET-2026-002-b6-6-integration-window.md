# B6 AWS change packet 2026-002 — bounded B6.6 integration window

Status: **DRAFT BLOCKED — NOT APPROVABLE OR EXECUTABLE**

This document presents the complete intended B6.6 window and the exact facts
that currently prevent authorization. It is not an AWS authorization. Owner
approval of this revision must be refused because four required image digests,
the deployed service adapters and the network boundary do not yet exist.

## Purpose

Run one bounded, synthetic-only EKS integration window after local release
engineering proves the five-service chain. Deploy all dependencies by immutable
digest, expose only the orchestrator through an internal ALB, prove one file
request and one WebSocket turn, run isolation/failure checks, then return CPU
and GPU to zero.

The window proves platform integration only. It does not approve a language,
promote a model, process PHI, publish clinical content or change the production
registry.

## Current immutable starting state

| Binding | Value |
|---|---|
| Starting commit | `9cfd499967d8aa015472c1f4c2a4c9f8c4962c54` |
| B6 plan SHA-256 | `3cfba1521281384aabbc91c4c5f04f7e2bec51444cfdf15f8a28b56a8f20418b` |
| Speech contract SHA-256 | `e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d` |
| B6A closure SHA-256 | `11a3c55f592387086556e85e882db6492b588cd0a5ee1be574566b707114ea51` |
| B6.5A closure SHA-256 | `4b8a6dc527001811b6e8dc05bbcd92d04c80893120015dca5ff8dc29e80f7ffd` |
| B6.5A snapshot | `a2486c03eb20b6fd3d30b5ea38eb4d29895c2e1ab26073d21282a9bbedacb8e6` |
| CPU node group | minimum `0`, desired `0`, maximum `4` |
| GPU node group | minimum `0`, desired `0`, maximum `1` |
| Production serving pointer | absent |
| B5 outcome | `BLOCKED`, unchanged |

Readiness evidence is
`platform/evidence/B6-6-PACKET-READINESS-2026-001.json`.

## Services and intended deployment order

| Order | Service | Exposure | Test mode |
|---:|---|---|---|
| 0 | NVIDIA DRA | node-local only | retained scan-passed driver |
| 1 | RAG index | `ClusterIP` only | synthetic non-clinical index |
| 2 | Model loader + ASR runtime | `ClusterIP` only | retained zero-shot Whisper large-v3 v0 platform test |
| 3 | Speech TTS gateway | `ClusterIP` only | text-only; Fish failure must preserve text |
| 4 | LLM gateway | `ClusterIP` only | fake Bedrock; no real AWS invocation |
| 5 | Speech orchestrator | internal ALB only | canonical file and WebSocket contracts |

Every dependency must be Ready and identity-bound before its consumer starts.
No runtime receives a public `LoadBalancer` service or public Ingress.

## Image digest table

### Already proven and reusable

| Image | Required deployable `linux/amd64` digest |
|---|---|
| `medzen-model-loader` | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` |
| `medzen-asr-runtime` | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` |
| `medzen-nvidia-dra` | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` |

### Hard blocker — no deployable image exists

| Image | Current ECR count | Bound digest |
|---|---:|---|
| `medzen-rag-index` | `0` | `NOT_AVAILABLE_PACKET_REFUSES` |
| `medzen-llm-gateway` | `0` | `NOT_AVAILABLE_PACKET_REFUSES` |
| `medzen-orchestrator` | `0` | `NOT_AVAILABLE_PACKET_REFUSES` |
| `medzen-speech-tts-gateway` | `0` | `NOT_AVAILABLE_PACKET_REFUSES` |

The four source directories also have no Dockerfile. Tags, source commits or
placeholder strings are not substitutes for digests. A successor revision
must bind each scan-passed ECR child-manifest digest and prove that exact child
is what Kubernetes runs.

## Additional hard blockers

1. The orchestrator currently refuses every mode except `local_fixture` and
   calls in-process synthetic dependencies. It cannot prove deployed
   ASR→RAG→LLM→TTS network integration.
2. The published B6.5A route truthfully names `v0-local-synthetic-asr` and
   `fake-bedrock-local-v1`. It cannot be reinterpreted as the retained B6A v0
   runtime. A new content-addressed test route requires its own prospective
   publication binding in the successor packet.
3. Generated manifests still contain `PLACEHOLDER_TAG`; no B6.6 overlay binds
   images, the exact SSM root, service URLs, security contexts or cleanup
   labels.
4. There is no reviewed internal-ALB/Ingress definition or exact MedZen
   backend security-group source. The shared production VPC must not be
   modified from inference or broad discovery.
5. The consolidated cost registry still records the prior `$15` B6A
   reservation as active pending reconciliation. The one-active-reservation
   rule must be resolved before reserving this window.

These are release-readiness blockers, not reasons to weaken the B6.6 exit.

## Required successor packet bindings

Before this packet can become approvable, revision 2 must add:

- exact Git commit and clean source tree;
- four new service child-manifest digests plus retained ASR/loader/DRA digests;
- ECR scan results of zero critical and zero high findings for every image;
- software bill of materials and base-image digest for every service;
- an exact content-addressed deployed test-registry snapshot and parameter
  versions, without production alias changes;
- digest-rendered Kubernetes manifests and their bundle SHA-256;
- exact service accounts and Pod Identity role ARNs;
- the internal ALB scheme, listener/target details, exact backend security
  group, and rollback ownership;
- exact synthetic request fixtures and expected response/stream event hashes;
- deadline and cleanup script hashes; and
- a closed prior reservation plus a new B6.6 allocation record.

## Intended network and IAM checks

The successor window must prove all of the following before traffic:

1. RAG, ASR, TTS and LLM are `ClusterIP`; they have no Ingress, external IP,
   `NodePort` or public target group.
2. Only the orchestrator has an **internal** ALB target. Its security group
   admits the exact MedZen backend security-group source and nothing broader.
3. Kubernetes NetworkPolicies allow only the required dependency edges:
   orchestrator→ASR/RAG/LLM/TTS and LLM→RAG if still required. Cross-service
   probes outside those edges refuse.
4. Each pod uses its own service account and expected Pod Identity role. The
   shared model-loader/ASR pod role is disclosed and remains read-only.
5. RAG cannot write S3; LLM alone can use the exact Bedrock profile only if a
   separately capped real-provider packet is approved; TTS cannot read user
   audio; the orchestrator cannot invoke Bedrock or read model artifacts.
6. Trainer and builder cannot assume runtime roles. No static AWS credential
   exists in a pod, image or manifest.

For the first integration, real Bedrock and Fish remain disabled. Their
absence/failure must produce the expected fake/text-only behavior without a
500 cascade.

## Intended synthetic traffic plan

No real voice, PHI or clinical content is permitted.

1. File request: exact generated WAV
   `platform/testdata/orchestrator/synthetic-file-request.wav`, SHA-256
   `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b`.
   Require HTTP 200, the exact request/session identities, transcript schema,
   three synthetic citations, `tts_backend=text_only`, and complete model and
   registry identities.
2. WebSocket request: send canonical `start`, bounded audio frames and `end`.
   Require ordered ready/partial/final events, final-result preservation,
   queue invariants (partial `4`, audio `8`) and a clean close.
3. Cancellation: cancel/barge-in must complete within `250 ms` without losing
   an already-final result.
4. Slow client: trigger bounded backpressure without unbounded memory growth or
   a 500 cascade.
5. Dependency drills: RAG unavailable, LLM timeout/open breaker, TTS timeout,
   registry hash mismatch, ASR unavailable and client disconnect each have a
   pre-bound status/event code and complete request-id trace.
6. Logs are reviewed for request IDs and version identities only; no audio,
   transcript, reply, citation text, bearer token or secret may be persisted.

Stage receipts are persisted immediately. A later measurement or drill failure
does not erase an earlier successful request/stream receipt.

## Intended window and cost boundary

- Maximum wall-clock window: `4 hours`, including provisioning and cleanup.
- Deadline and cleanup actions arm before either node group scales above zero.
- Maximum CPU: two `m6i.large` nodes.
- Maximum GPU: one `g6.xlarge` node.
- Recorded compute rate: `$1.2364/hour` combined.
- Four-hour compute estimate: `$4.9456`, excluding logs, storage, ALB and API
  requests.
- Proposed all-in reservation/ceiling: `$10.00` under allocation ID
  `B6-INTEGRATION-WINDOW-2026-001`.
- No Bedrock or Fish spend in this first window.
- If the deadline cannot leave enough time for cleanup, refuse before scale-up.

Exact allocation tags in the successor packet:

| Tag | Value |
|---|---|
| `Project` | `medzen-speech` |
| `Environment` | `dev` |
| `CostCenter` | `speech-platform` |
| `Stage` | `B6.6` |
| `Workstream` | `integration-window` |
| `BudgetRegistry` | successor cost-registry revision |

## Scale-to-zero and rollback proof

Cleanup runs on success, refusal, timeout, interruption or local-network loss.
The final receipt must prove:

- all B6.6 deployments, jobs, services, ingress objects, test secrets and
  NetworkPolicies removed or restored to the exact pre-window state;
- ALB/listener/target group/security-group rules created by the window removed;
- CPU minimum `0`, desired `0`, instances `0`, nodes `0`;
- GPU minimum `0`, desired `0`, instances `0`, nodes `0`;
- NVIDIA DRA test workloads/pods `0`;
- no scheduled deadline action remains;
- the B6.5A test snapshot remains byte-identical unless the successor packet
  explicitly creates a second content-addressed test snapshot;
- `/medzen/registry/serving/current` unchanged;
- approved ASR objects/versions and registered models unchanged; and
- post-run Terraform plan `NO_CHANGES`.

The control plane continues billing; scale-to-zero refers to worker compute and
window-created resources.

## Approval boundary

**This revision must not be approved or executed.** The next authorized action
is local-only B6.5B release engineering: hardened Dockerfiles, deployed
adapters, digest-rendered manifests, network tests and immutable local image
digests. After independent review, revise this packet with every missing exact
binding and a new SHA-256. No ECR push, IAM change, SSM write, scale-up, ALB or
deployment is authorized here.
