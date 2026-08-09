# B6 AWS change packet 2026-004 — bounded B6.6 integration window, revision 2

Status: **DRAFT BLOCKED — NOT APPROVABLE OR EXECUTABLE**

Prepared: `2026-08-09`

This is the immutable successor to packet 2026-002, SHA-256
`b285b34dd79ce781749877b93ab7aff97dc1dccf32660b6fb90e256700601e9f`.
It does not edit or reinterpret that historical blocked draft.

## Purpose and current conclusion

Run one bounded, synthetic-only EKS integration window: deploy every service by
scan-passed child digest, expose only the speech orchestrator through an
internal ALB, prove one file request and one WebSocket turn, run isolation and
failure checks, then return CPU and GPU capacity to zero.

Packet 2026-003 closed the four application-image gap as `PASS_SCAN_ONLY`.
All seven currently deployable workload images are now digest-bound below.
This revision remains blocked because the ALB controller image and installation
boundary are absent, the deployment registry snapshot has not been published,
the exact deployed client-key secret does not exist, and no final rendered
deployment/cleanup bundle binds those resources. Approval of this draft must
be refused until a successor revision closes those gaps prospectively.

The window is a platform proof only. It does not approve a language or model,
process PHI, publish clinical content, call real Bedrock or Fish, write
`approved/asr/`, or change `/medzen/registry/serving/current`.

## Immutable starting bindings

| Binding | Value |
|---|---|
| Starting commit | `06b601ebb8fcb77d0fb26025d8a066f4d5fdc9f4` |
| Starting tree | `fea2324122440393ca44feb1bf862487149f2f88` |
| B6 plan SHA-256 | `3cfba1521281384aabbc91c4c5f04f7e2bec51444cfdf15f8a28b56a8f20418b` |
| Speech contract SHA-256 | `e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d` |
| B6A closure SHA-256 | `11a3c55f592387086556e85e882db6492b588cd0a5ee1be574566b707114ea51` |
| B6.5A evidence SHA-256 | `4b8a6dc527001811b6e8dc05bbcd92d04c80893120015dca5ff8dc29e80f7ffd` |
| B6.5B scan result SHA-256 | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| Network design SHA-256 | `3de0fc48bdc44c83405b160be36bb196fc08c811746bfac811aeea1d4bdeca5d` |
| Cost registry SHA-256 | `a06fadee5a85ea763e80e333df1bd116dc0f184b5a3b02a9eafa820830d380f5` |
| CPU node group | `ACTIVE`, minimum `0`, desired `0`, maximum `4` |
| GPU node group | `ACTIVE`, minimum `0`, desired `0`, maximum `1` |
| Production serving pointer | absent |
| Approved ASR objects | `0` |
| B5 outcome | `BLOCKED`, unchanged |

## Exact scan-passed image table

Kubernetes must use the full ECR name plus the child digest below. A tag, OCI
index, rebuilt image, manifest substitution or different architecture refuses
the window.

| Workload | Exact deployable `linux/amd64` child digest | Authoritative scan |
|---|---|---|
| `medzen-nvidia-dra` | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` | `COMPLETE`, 0 critical / 0 high |
| `medzen-model-loader` | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` | `COMPLETE`, 0 / 0 |
| `medzen-asr-runtime` | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` | `COMPLETE`, 0 / 0 |
| `medzen-rag-index` | `sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c` | `COMPLETE`, 0 / 0 |
| `medzen-speech-tts-gateway` | `sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d` | `COMPLETE`, 0 / 0 |
| `medzen-llm-gateway` | `sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a` | `COMPLETE`, 0 / 0 |
| `medzen-orchestrator` | `sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa` | `COMPLETE`, 0 / 0 |

The retained B6A scan evidence is
`platform/evidence/B6A-PACKET-2026-003C-A-SCAN-RESULT.json`, SHA-256
`1b1ed84205fe9a71c3b21b2a2658814855fd5fdcf6af00c5590bb4205e8dc70b`.
The four new results are in the B6.5B scan record bound above. No waiver is
permitted. A critical or high finding discovered before deployment blocks the
affected image and the entire window.

## Intended dependency-first deployment order

| Order | Component | Exposure | Test behavior |
|---:|---|---|---|
| 0 | Deadline and cleanup controller | none | armed before scale-up |
| 1 | NVIDIA DRA | node-local only | retained scan-passed driver |
| 2 | RAG index | `ClusterIP` only | embedded synthetic, non-clinical index |
| 3 | Model loader + ASR runtime | `ClusterIP` only | B6A zero-shot Whisper large-v3 v0 |
| 4 | Speech TTS gateway | `ClusterIP` only | text-only; Fish disabled |
| 5 | LLM gateway | `ClusterIP` only | fake provider; real Bedrock disabled |
| 6 | Speech orchestrator | `ClusterIP` plus one internal ALB target | deployed HTTP/SSM mode |
| 7 | Synthetic probes and drills | caller is exact MedZen backend SG | no PHI or production traffic |

Each dependency must be Ready, digest-matched and identity-bound before its
consumer starts. A readiness, registry, authentication, checksum or network
failure persists its stage receipt and stops later deployment stages.

## Registry binding

The intended deployment snapshot is
`platform/generated/registry-ssm/b6-v0-synthetic.json`, SHA-256
`33433626b0f2070a714df31e16306d6a511652b0870b0ba3cb5ec701847c9821`.
It contains exactly three create-only KMS-encrypted `SecureString` parameters
beneath:

`/medzen/registry/test/b6/d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81`

The root is currently absent and therefore blocks this packet. It must be
published and read back through a separately reviewed small packet using the
existing `medzen-registry-publisher-role`; no production pointer may be
created. The successor B6.6 packet must bind the publication receipt and exact
parameter versions rather than treating this local file as live state.

## ALB and ClusterIP isolation boundary

The approved local design selects VPC `vpc-051aa9df8b64bf141` and candidate
MedZen backend source security group `sg-0a83abae6ab954543`. The backend owner
must reconfirm that group immediately before any rule is created.

- Only `speech-orchestrator:8080` may be an ALB target.
- ALB scheme is `internal`; target type is `ip`; production DNS is unchanged.
- The bounded synthetic listener is HTTP port `80`; any production use must
  introduce TLS through a future decision.
- ALB ingress source is only `sg-0a83abae6ab954543`; CIDR ingress is empty.
- RAG, ASR, LLM and TTS remain `ClusterIP`, with no Ingress, `NodePort`,
  external IP or target group.
- NetworkPolicies permit only orchestrator to the four dependency endpoints.
  Cross-service and namespace-external probes must refuse.

Hard blocker: no AWS Load Balancer Controller is modeled or installed in this
repository, and no controller image has passed the MedZen image gate. A future
local release record must bind its exact upstream version, image child digest,
IAM policy, service account, Helm values, runtime scan and uninstall path. Its
scan qualification requires a separate scan-only packet. No public/upstream
tag may be deployed directly from this draft.

## IAM and authentication

The application service accounts and Pod Identity associations already exist.
The ASR pod must reuse service account `asr-runtime-b6a`, association
`a-ajbhedkszqlnrrjk4`, and role
`arn:aws:iam::558069890522:role/medzen-b6a-asr-role` so both its init container
and runtime share the same narrow B6A artifact read boundary. No per-container
IAM isolation is claimed.

Other required service-account/role pairs are:

| Service account | Role |
|---|---|
| `speech-orchestrator` | `arn:aws:iam::558069890522:role/medzen-orch-role` |
| `rag-index` | `arn:aws:iam::558069890522:role/medzen-rag-role` |
| `llm-gateway` | `arn:aws:iam::558069890522:role/medzen-llm-role` |
| `tts-gateway` | `arn:aws:iam::558069890522:role/medzen-tts-role` |

Real Bedrock and Fish providers remain disabled even though standing roles
contain prospective permissions. The runtime proof must show no provider API
calls occurred.

Hard blocker: the digest-bound orchestrator requires secret
`medzen/client-api-keys`, but that secret does not exist. The existing
Terraform secret is differently named `medzen/speech/client-api-keys` and may
not be reinterpreted. A successor must either qualify a rebuilt orchestrator
that uses the existing exact secret or itemize a temporary integration-secret
creation, hash-only value, KMS key, role access, rotation and deletion receipt.

## Synthetic traffic and stage receipts

No real voice, PHI or clinical content is permitted.

1. File request uses
   `platform/testdata/orchestrator/synthetic-file-request.wav`, SHA-256
   `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b`.
   Require HTTP 200, a transcript, three synthetic citations,
   `tts_backend=text_only`, and every model/registry identity.
2. WebSocket sends canonical start, bounded frames and end. Require ordered
   ready/partial/final events, partial queue `4`, audio queue `8`, final-result
   preservation and clean close.
3. Cancellation/barge-in finishes within `250 ms`; an existing final result is
   never discarded.
4. Slow-client pressure stays bounded and never drops the final result.
5. Failure drills cover RAG unavailable, LLM timeout/open breaker, TTS timeout,
   registry mismatch, ASR unavailable and client disconnect with pre-bound
   codes and no 500 cascade.
6. Network probes prove only the internal orchestrator route is reachable from
   the exact backend source and every dependency rejects external reachability.
7. Logs contain request IDs and version identities only—never audio,
   transcript, reply, citation text, bearer token or secret.

Every stage receipt is fsync-persisted immediately. A later drill or
measurement failure cannot void an earlier successful receipt.

## Intended duration, cost and reservation

- Maximum wall-clock window: `4 hours`, including provisioning and cleanup.
- Maximum nodes: two `m6i.large` CPU and one `g6.xlarge` GPU.
- Recorded combined rate: `$1.2364/hour`; four-hour compute estimate `$4.9456`.
- Intended all-in ceiling: `$10.00`, allocation
  `B6-INTEGRATION-WINDOW-2026-001`.
- Current committed guardrail: `$63.5288`; active reservations: `$0`;
  headroom: `$236.4712`.
- Approval of this draft creates no reservation. A successor approvable packet
  must bind `COST-REGISTRY-2026-003` and activate exactly one `$10` reservation.
- No Bedrock or Fish spend. ALB, logs, storage and API charges are not asserted
  to be zero.

## Deadline-first cleanup and zero-state proof

The deadline and independently invocable cleanup paths must be hash-bound and
armed before either node group scales above zero. On success, refusal, timeout,
interruption or local-network loss, cleanup must prove:

- every window deployment, job, service, Ingress, NetworkPolicy, ConfigMap and
  test secret removed;
- controller-created ALB/listener/target group and exact security-group rules
  removed, and any controller installed by a predecessor packet returned to
  that packet's approved standing state;
- CPU minimum `0`, desired `0`, instances `0`, nodes `0`;
- GPU minimum `0`, desired `0`, instances `0`, nodes `0`;
- NVIDIA DRA workload/pods `0` and no deadline action remains;
- the new content-addressed test snapshot either remains immutable evidence or
  is deleted exactly as its publication packet specifies;
- `/medzen/registry/serving/current` absent;
- `approved/asr/`, registered models and language approved versions unchanged;
- no real Bedrock/Fish calls; and
- post-run Terraform plan `NO_CHANGES` except resources explicitly created and
  fully destroyed by the approved window.

The EKS control plane and retained ECR evidence continue billing; scale-to-zero
refers to worker compute and window-created resources.

## Required successor work before approval

1. Publish/read back the exact deployment registry under its own small packet.
2. Qualify an exact AWS Load Balancer Controller release and its installation
   boundary through local review and a scan-only packet.
3. Resolve the exact client-key secret boundary without weakening auth.
4. Produce digest-rendered Kubernetes, NetworkPolicy, Ingress, deadline,
   synthetic-probe and cleanup assets; bind every file SHA and validate them
   against a server-side or version-matched Kubernetes schema.
5. Bind exact Terraform/Helm plans, IAM deltas, controller role, ALB security
   group creation and rollback ownership.
6. Reissue this packet with a new SHA-256, independent review, exact approval
   phrase and one active `$10` reservation.

## Approval boundary

**Do not approve or execute this revision.** It authorizes no AWS or
Kubernetes action. Its purpose is to record that application image
qualification is complete while making the remaining integration-control gaps
explicit. The next AWS action must be a separately versioned, minimal packet;
no resource, SSM value, secret, controller, ALB, node or workload may be
created from this draft.
