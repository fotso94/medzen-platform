# B6 AWS change packet 2026-013 — B6.6 integration window attempt 4

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-10  
Account/region: `558069890522` / `eu-central-1`  
Required profile/operator: `medzen` / `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested

Authorize one final synthetic-only B6.6 integration window after the separately
verified secret restoration, full ALB lifecycle IAM correction and exact
post-create tag-mutation rule. This is attempt 4. It supersedes packet 2026-010
by reference only and does not alter any prior packet, authorization, refusal or
receipt.

This draft itself authorizes no AWS or Kubernetes mutation. Execution requires
an independent review bound to the final packet SHA-256 and prepared commit,
then a new owner-approved `B6-AWS-AUTH-2026-013` containing every exact source
binding below.

## Closed prerequisites

Packet 2026-012A completed with `VERIFIED_COMPLETE` and
`PASS_SECRET_ROTATION_ONLY`. Attempt 4 is bound to:

- secret ARN:
  `arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE`;
- new version `d09d567e-9bde-482a-b95a-3cab990a1006` at `AWSCURRENT`;
- historical version `f78c8aa8-2765-4788-9928-dd1ba7c406bf` with no stage;
- 43-byte bearer SHA-256
  `3a30b00fc96111490c2b471eec5eebe1c9d26bf991508428cf2f5511e306b84a`;
- exact local file `/private/tmp/medzen-b6-6-client-token`, mode `0600`, 44
  bytes including one final LF; and
- orchestrator as the only secret reader, with operator reads explicitly
  denied.

The runner refuses before its deadline or any capacity change if the version
map, file encoding, bearer hash, resource policy, KMS boundary, Terraform
state, test registry, production-pointer absence or zero state differs.

## Corrections carried forward from attempts 1–3

All previously proven controls remain active:

1. Worker readiness waits up to 1,200 seconds for exactly two CPU and one GPU
   Kubernetes resources before evaluating Ready, and refuses excess capacity.
2. Cleanup runs through `bash`, persists write-once `INCOMPLETE` and separate
   `cleanup_recovery` receipts, and keeps both AWS deadlines armed until exact
   CPU/GPU zero.
3. The token guard hashes only the exact 43-byte bearer and validates the
   newline-terminated 44-byte file without printing it.
4. The simulation-proven controller policy now covers CreateListener and
   CreateRule on their parent ARNs, Modify*, creation-time tagging and the
   Delete* cleanup path. No IAM change is part of this packet.
5. Every stage receipt is persisted immediately; later failure never voids an
   earlier success.

## Exact image identities

The six service/init images, DRA driver and controller are pinned to their
previously scan-passed linux/amd64 child manifests. Tags and OCI indexes are not
deployment identities.

| Image | Child-manifest digest |
|---|---|
| `medzen-asr-runtime` | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` |
| `medzen-model-loader` | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` |
| `medzen-rag-index` | `sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c` |
| `medzen-llm-gateway` | `sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a` |
| `medzen-speech-tts-gateway` | `sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d` |
| `medzen-orchestrator` | `sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa` |
| `medzen-nvidia-dra` | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` |
| `medzen-aws-load-balancer-controller` | `sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f` |

Any runtime digest mismatch refuses before a synthetic conversation.

## ALB lifecycle and bounded tag-warning rule

The ALB gate now verifies one active internal application load balancer named
`medzen-b6-window`, security group `sg-0f0f6c66852830013`, one HTTP listener on
port 80, one non-default rule, one healthy target group, exact required
creation-time tags on the load balancer/listener/rule, and a successful
isolated Fargate `/readyz` probe. It persists `alb_ready` first.

Only then may the owner-directed
`B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-001` classify an exact
`AddTags`/`RemoveTags` `AccessDenied` on a child listener or listener-rule ARN
for this account, region and ALB as `WARNING_NON_FATAL`. That classification is
persisted as the separate `alb_tag_mutation_warning` receipt, bound to the
`alb_ready` receipt hash. With no denial it is `PASS`.

CreateLoadBalancer/CreateListener/CreateRule denial, creation-time tag failure,
another action/resource/account/region, malformed evidence, unhealthy target,
failed probe, or any Delete* cleanup denial remains fatal. Unknown states fail
closed. Raw controller logs are not persisted.

## Deadline-first execution sequence

1. Validate authorization, source hashes, exact secret version and token,
   registry snapshot, absent production pointer and zero workers/resources.
2. Arm and verify matching CPU/GPU scale-to-zero actions for no more than
   `11,243 seconds` before scaling either group.
3. Raise at most two `m6i.large` CPU nodes and one `g6.xlarge` GPU node; persist
   `workers_ready` only after exact resource existence and readiness.
4. Generate a fresh Terraform plan. The machine guard must show exactly
   `7 add / 0 change / 0 destroy`, then apply only that saved plan.
5. Install the digest-pinned controller and DRA; deploy RAG → ASR/model-loader
   → text-only TTS → fake LLM → orchestrator from zero replicas.
6. Prove the internal ALB and tag boundary, then run the Fargate readiness
   probe and persist the two ALB receipts in order.
7. Run synthetic file, WebSocket, 250 ms cancellation, refusal,
   RAG-unavailable and ClusterIP-isolation proofs. No real provider is called.
8. Always invoke the independent cleanup path, remove the Ingress first,
   remove workloads/DRA/window Terraform resources, schedule seven-day
   recoverable secret deletion, remove the local token, scale CPU/GPU to zero,
   prove zero, then disarm both deadlines.

Every receipt is write-once, fsync-persisted and PHI-safe. An interruption or
local-network loss leaves the AWS deadlines active independently.

## Time, capacity and cost continuity

- Original cumulative allowance: `14,400 seconds`.
- Packet 2026-008 conservatively charged: `1,784 seconds`.
- Packet 2026-010 conservatively charged: `1,373 seconds`.
- Charged before attempt 4: `3,157 seconds`.
- Maximum attempt-4 window: `11,243 seconds`; cumulative maximum remains
  exactly `14,400 seconds`.
- Existing single reservation: `$10` under
  `B6-INTEGRATION-WINDOW-2026-001`; new reservation: `$0`.
- Aggregate ceiling: `$300`; recognized committed guardrail: `$63.5288`;
  committed plus reservation: `$73.5288`.
- Maximum capacity: two `m6i.large` nodes, one `g6.xlarge`, one short-lived
  `0.25-vCPU / 0.5-GiB` Fargate probe and one internal ALB.

The reservation is a ceiling, not a claim of actual AWS spend. Cleanup and
attributable billing must be recorded before it is closed.

## Exact source bindings required

| Source | SHA-256 |
|---|---|
| `infra/alb_controller.tf` | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| `infra/b6_6_window_override.tf` | `c9dc7ebfd17b4ea0e9bf9b50fee7af529405ab44ee4e08827d3a5bf06ef39962` |
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `infra/b6_integration_window.tf` | `73ec7282cf4f5b8e7dfbb3081ebdd689424d96260bb03564983f73a9b33ca205` |
| `infra/b6_planning_override.tf` | `296ccddd66a108f3667273f0b2683c62d48882340fe2585bf1069ae47b32d2bf` |
| `infra/eks.tf` | `37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b` |
| `infra/variables.tf` | `59c1226f9a797e13756575ef77b45ce9324e1f1fb4743bc7d84fa8bec4f272dd` |
| `pipeline/b6_integration_receipts.py` | `e340edcac79c12c850d108f15abadf66db1102f736acbe08d60c3da1d2054275` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-001.json` | `a77d229f97939d74d5a161a6c1bb7a0a2514a1870fd0e1b63d20445ec425e16c` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-6-LOCAL-CORRECTION-2026-001.json` | `00882ca9d28a867a46b9269144cf6567672415781b09cfc84153020479cdf881` |
| `platform/evidence/B6-6-LOCAL-CORRECTION-2026-002.json` | `2fc6bd357dcf6c111c0d6614a8f930a4f2cecb04915b141f152f60dc2c0b870b` |
| `platform/evidence/B6-CLIENT-API-KEYS-2026-001.json` | `6120c7a9b82dd51a2ceccd504156c8448c0322c5ba31e65334505caf3856c2e0` |
| `platform/evidence/B6-CLIENT-SECRET-RESTORATION-CONTINUATION-AWS-EXECUTION-2026-001.json` | `1d949f019ce0b2e69f1fba525d535d61fc19ed07e99f08d11729c1c099784c89` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-LBC-IAM-LIFECYCLE-AWS-EXECUTION-2026-001.json` | `da38f29ec5cd218620e2c649a19500b24db04b7ecd0b55a873b61bb1fce09236` |
| `platform/evidence/B6-LBC-QUALIFICATION-AWS-EXECUTION-2026-001.json` | `56265113cbfa3ebec85309ec9966dc5fb7a2dd28e1c5fad0b1a4dd6e946cb8f3` |
| `platform/evidence/B6-PACKET-2026-008-REFUSED-WORKER-REGISTRATION.json` | `f2b8acbabafb2642e5b70ddbae966930f2ba62201c7a2fb26f6e32bc3246d432` |
| `platform/evidence/B6-PACKET-2026-009-REFUSED-TOKEN-ENCODING.json` | `3295768ed6d326125f4c5098908a0b6e090c800a93b35c199ecadf0a574d8a49` |
| `platform/evidence/B6-PACKET-2026-010-REFUSED-ALB-LISTENER-IAM.json` | `4ea2234f6803049d6d4afd4a24a2f03f118c1c45c090b173f61cfef8506fdabf` |
| `platform/evidence/B6A-PACKET-2026-003C-A-SCAN-RESULT.json` | `1b1ed84205fe9a71c3b21b2a2658814855fd5fdcf6af00c5590bb4205e8dc70b` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `0281f5f05b414d39dda0c03854fc42bda410109b2274b49ca9519571d4ba6b32` |
| `scripts/b6_6_cleanup.sh` | `12be2ae0c297562fa20745e55fd5a571d4cec5226491b9c7589475e58f183506` |
| `scripts/b6_6_deadline.py` | `cb801c5611f297acde7bb647b8626c0fd94a248cece0935eea52a206f8beb5d1` |
| `scripts/b6_6_lbc_runtime.py` | `e2e9c337712280dcc43db32b62f69560f79a98e9b366eeb1878676a169f64ed9` |
| `scripts/b6_6_lbc_tag_warning.py` | `0cce4e39f960270976120987af57d809b1d871a6127f44fca12470aadd21fd10` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_receipt.py` | `3629ffc8b3b6c34ec1d3cfdcde5c8aa28a73796806826f8dce5084a8995c7280` |
| `scripts/b6_6_secret_preflight.py` | `ce5c011ab2774d52db2dc9fc60ab7df11df92abd88ac817ef76ae59b7b1f140f` |
| `scripts/b6_6_token_binding.py` | `55e920fd6c717340cc4028bf7a1d37f941f8ae1da6449bea3640ba0503f8f0df` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_window_plan.py` | `8c19f2a5bc236f37d07c99999e093e48927e503bf2b52f786d34447ed117509f` |
| `scripts/pin_aws_lbc_digest.py` | `bcd3b3e97e992f96457df392f76aa9da8d91f801a0610cc5533660c6400ce5cb` |
| `scripts/run_b6_6_integration_window.sh` | `81396e9e0779ba0c8b2154462924952c13dbbdd69a0f37b3eb49cb85fc435adc` |
| `scripts/run_b6_client_secret_restoration.py` | `baa58777cd05a3edad5f5236013ca5e3556dd654026790a6f2599022981422cc` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |

Any missing source, changed hash, stale secret version, unknown state, plan
delta, capacity excess or incomplete evidence refuses before the next stage.

## Explicit prohibitions

No public or production exposure, production SSM write, approved-artifact
write, model registration, MLflow transition, language approval, real
Bedrock/Fish call, training, green-bucket mutation, PHI, image push/rebuild,
scan waiver, IAM change, capacity/time extension, deadline disarm before proven
zero, force secret deletion, or reuse of the historical token is authorized.

B5 remains `BLOCKED`; this window validates platform integration only. A PASS
does not promote a model or approve any deferred language.

## Review and approval phrase

After independent review bound to the final packet SHA-256 and prepared commit,
the only valid owner phrase is:

`Approve B6 AWS change packet 2026-013 only.`
