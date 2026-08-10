# B6 AWS change packet 2026-022 — isolated probe qualification and hardened window

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Packet 2026-021 is terminal. Its last authorized attempt reached the local
Fargate task-definition verifier and refused before `ecs:RunTask`; automatic
cleanup returned the platform to exact zero state. This successor adds the
review-required isolated probe qualification as Stage A, corrects the verifier,
records the complete R5 verifier audit, enriches Terraform receipts and requests
a fresh R7 allowance. This draft authorizes no AWS, Terraform, Kubernetes,
worker, secret or service mutation.

## Immutable predecessor and refusal

| Binding | Value |
|---|---|
| Packet 2026-021 | `a2354dafcd4d89ce9adf9b345ae23ee5364d53d5ca251deadf36a779d4dcae54` |
| Authorization 2026-021 | `9d46897a3a4375e1b245d2e239fcd7b93c9d3ecfc4f186d4dc2dbdab4edb71fe` |
| Attempt-2 evidence | `platform/evidence/B6-PACKET-2026-021-ATTEMPT-2-REFUSED-FARGATE-BOUNDARY.json` |
| Attempt-2 evidence SHA-256 | `6f40490f9f8496036235085ebdc3b5b6042b5108753b6833245be5c293ed5b3b` |
| Terminal stage | `fargate_probe: REFUSED` |
| Passed before refusal | `14` stages, through `endpoints_ready` |
| Fargate tasks started | `0` |
| Conversation proofs run | `0` |
| Cleanup | `PASS`, receipt `eade6838f707b5d187ec8e4d6785b7190dac24251f3620cec30fb7839a84fe54` |
| Prior allowance | `2 / 2` attempts consumed; `0` remain |

AWS returned the required `initProcessEnabled=true` and `drop=[ALL]`, plus the
incidental normalized field `add=[]`. The prior verifier compared the whole
`linuxParameters` object and rejected that harmless normalization. This was a
local R5 verifier defect, not an image-pull, endpoint, ALB, service or application
failure.

## Stage A — isolated private-probe qualification

Stage A is a mandatory qualification run before either full window attempt. It
creates only the probe's private endpoints, self-isolated endpoint security
group and rule, execution role and policy, ECS cluster and task definition.
It does **not** create or scale EKS workers, start a GPU, deploy Kubernetes
services, install the load-balancer controller, create an ALB or mutate the
persistent synthetic credential.

The Stage A Terraform guard permits exactly `9 add / 0 change / 0 destroy`:

- `aws_ecs_cluster.b6_probe[0]`
- `aws_ecs_task_definition.b6_probe[0]`
- `aws_iam_role.b6_probe_execution[0]`
- `aws_iam_role_policy.b6_probe_execution[0]`
- `aws_security_group.b6_probe_endpoints[0]`
- `aws_vpc_endpoint.b6_probe_ecr_api[0]`
- `aws_vpc_endpoint.b6_probe_ecr_dkr[0]`
- `aws_vpc_endpoint.b6_probe_s3[0]`
- `aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]`

After endpoint availability, the runner launches three consecutive isolated
Fargate tasks. Each task must use only the endpoint security group, have public
IP disabled, pull the already scan-passed probe image through the endpoints,
start the container and exit successfully. Each task gets its own write-once
receipt. “Stable” is defined here as exactly three consecutive PASS tasks; the
first refusal stops Stage A, so there are no hidden task retries.

The Stage A chain is:

1. `stage_a_preflight`
2. `stage_a_terraform`
3. `stage_a_endpoints`
4. `stage_a_probe_1`
5. `stage_a_probe_2`
6. `stage_a_probe_3`
7. `stage_a_cleanup`
8. `stage_a` aggregate

The operations deadline is 1,200 seconds, followed by a separately bounded
600-second cleanup allowance. The total ceiling is 1,800 seconds and `$0.50`.
Cleanup is always attempted. A cleanup refusal is persisted before bounded
recovery; the aggregate separately records whether recovery restored zero state.
Any stage refusal produces a REFUSED aggregate and leaves both full-window
attempts locked.

Only a complete PASS chain with three consecutive probe passes and verified zero
state unlocks the window. Its receipts must be committed unchanged after Stage A
and before attempt 1. The full runner verifies every receipt hash and dependency,
the packet binding and the required aggregate invariants. A Stage A retry is not
authorized by this packet; a refused Stage A requires a new owner decision.

## Full-window corrections

### Hardened task verifier

The verifier now checks only security/function invariants:

- `initProcessEnabled` is true;
- no capability is added; and
- `ALL` is present in the dropped-capability set.

It fails closed if any invariant is weaker. It tolerates harmless extra drops,
empty normalized fields and unrelated runtime metadata, as required by R5.

### Safe structured refusal

The probe helper emits a fixed allowlisted code for verifier refusals. The
dispatcher persists it before returning nonzero, and the receipt runner copies
only the code, `application_started` and `readyz_completed`. Bodies, credentials,
audio, transcripts, replies, citations, stdout and stderr remain prohibited.

### Named Terraform receipts

The controller and window receipts persist plan counts and exact changed resource
names before apply. The controller plan remains exactly `1 / 0 / 0` for
`helm_release.b6_load_balancer_controller[0]`. The window plan remains exactly
`11 / 0 / 0`: the nine Stage A resources plus the two reviewed ALB/node ingress
rules. A replacement, update, unknown address or count/name mismatch refuses
before apply. The packet-versioned temporary ALB-hostname file is removed with
the synthetic token during cleanup.

## R5 verifier audit

The complete audit is
`platform/evidence/B6-R5-VERIFIER-AUDIT-2026-001.json`, SHA-256
`f4c55e8d31a65a9d10aa8d8e581be56732b1a88632df4d0c6d6417241b43413a`.
It covers all 19 canonical verifiers reachable from Stage A, the full runner,
cleanup, receipts and Terraform plan guards.

Result: **PASS_WITH_CORRECTIONS**, seven corrections, zero unresolved findings.
The corrected incidental-shape checks are:

1. Fargate `linuxParameters`: named security invariants, not whole-object equality.
2. Credential versions: fresh-version `AWSCURRENT` membership, not historical cardinality.
3. Worker zero state: only `minSize`, `maxSize` and `desiredSize`.
4. Terraform receipt summary: semantic JSON comparison, not serialized key order.
5. Endpoint boundary: order-independent SG membership and one bounded Allow;
   extra Deny-only statements cannot expand access.
6. ALB routes: exact membership without list-order dependence.
7. Stage A unlock: required aggregate fields, receipt schema and dependency
   hashes, not whole-payload equality.

Exact comparisons remain where they are the safety boundary: account and role
identities, resource/action sets, image digests, authorization and receipt hashes,
capacity/cost maxima, required routes and final zero-state counts.

## Carried-forward full-window boundary

All R1–R7 controls and reviewed constraints from packets 2026-019 through
2026-021 remain in force: stage-0 in-place synthetic credential rotation;
deadline-first independent CPU/GPU scale-down; two CPU nodes and one GPU maximum;
all workloads and eight scan-passed child images ready before endpoint DNS;
pull-only principal-independent ECR policies; resource-bounded S3 policy;
self-isolated endpoint SG; internal ALB ingress to orchestrator only; dependencies
`ClusterIP`; bounded post-create tag warning; receipt-per-stage for PASS, WARNING
and REFUSED; and cleanup before deadline disarm.

There is no PHI, production traffic, real Bedrock or Fish call, model adoption,
`approved/asr/` write or production SSM pointer. The 23-stage full-window order
is unchanged: `stage0`, `deadline`, `workers_ready`, `dra_ready`, `rag_ready`,
`asr_ready`, `tts_ready`, `llm_ready`, `orchestrator_ready`, `controller_window`,
`controller_ready`, `pre_endpoint_images`, `terraform_window`,
`endpoints_ready`, `fargate_probe`, `alb_ready`,
`alb_tag_mutation_warning`, `file_proof`, `websocket_proof`,
`cancellation_proof`, `failure_drills`, `isolation_proof`, `cleanup`.

## Fresh cold rehearsal and verification

| Check | Result |
|---|---|
| Cold receipt | `platform/evidence/receipts/B6-2026-022-COLD/cold_rehearsal.json` |
| Cold receipt SHA-256 | `b5b518b8c385fb343bc4a91de029817945b88969e4b9a5e0bf6580eaa52bab4f` |
| Scenario-results SHA-256 | `32672903002206d5cfba8acd56da0e63326f07922d8f83d447e298b7dc262141` |
| Full-window simulated PASS | `1`, with `23/23` PASS receipts |
| Full-window injected failures | `23`, each receipted and cleaned |
| Stage A simulated PASS | `1`, with `8/8` PASS receipts |
| Stage A injected failures | `7`, each receipted; cleanup or bounded recovery reaches zero state |
| Real AWS / kubectl calls | `0 / 0` |
| AWS / Kubernetes mutations | `0 / 0` |
| Focused Stage A, R5, Fargate and runner tests | `36 passed, 0 failed` |
| Canonical repository suite | `1,395 passed, 0 failed, 0 skipped, 7 deselected` |
| Terraform fmt / validate | `PASS / PASS` |

## Fresh R7 allowance request

The previous two-attempt allowance is exhausted; no seconds transfer. Stage A
and at most two full windows are requested inside the existing `$10` reservation.
The Stage A qualification is compute-light and cannot consume window-attempt
seconds. Attempt 2 exists only if attempt 1 refuses and reaches verified zero
state; a PASS ends the packet.

| Control | Requested value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Existing reservation | `$10.00` retained pending reconciliation |
| New reservation | `$0` |
| Stage A runs | `1` maximum |
| Stage A ceiling | `1,800` seconds and `$0.50` |
| Stage A stability proof | `3` consecutive private tasks |
| Stage A EKS/GPU/service mutations | `0 / 0 / 0` |
| Full-window attempts | `2` maximum, gated by Stage A PASS |
| Maximum per window | `4,500` seconds, non-transferable |
| Maximum worker seconds | `9,000` |
| Estimated compute for both windows | approximately `$3.20` |
| Combined Stage A plus window ceiling | `$3.70` |
| Earlier-window billing | `PENDING_AWS_COST_EXPLORER_LAG` |

The reservation does not authorize execution. Independent review and explicit
owner approval are required.

## Frozen source-hash table

| Path | SHA-256 |
|---|---|
| `infra/alb_controller.tf` | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| `infra/b6_6_endpoint_policy_override.tf` | `9dc7e893cd8e0e4612bd082541d7f884cd35e37e964202b577901a26f3b05dae` |
| `infra/b6_6_persistent_secret_override.tf` | `abe501946e6545b8d844d115de95e7f7f6736c840dfec20d2efead05c4a0ad68` |
| `infra/b6_6_window_override.tf` | `c9dc7ebfd17b4ea0e9bf9b50fee7af529405ab44ee4e08827d3a5bf06ef39962` |
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `infra/b6_integration_window.tf` | `2b9be98846f39270de93ff6855a68a888babd5b8b27fc7c9cc1f3703e5584bc6` |
| `infra/eks.tf` | `37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b` |
| `infra/variables.tf` | `45217079c585b89d80b5915900b4b45cb4ca454ff754df1492dae1396e6cb77f` |
| `pipeline/b6_integration_receipts.py` | `95b9c276c4b02f31174d14bf35d2d7badddad301123888a030a5e3581f1056e1` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json` | `831c164a6ca75017a3f9d11e38550cc52c7785b3abcb65f1963d82378995e244` |
| `platform/decisions/B6-WINDOW-VERIFIER-POLICY-2026-001.json` | `73eacb9cc6a9d9850098464f70380c92e25c46ac4aff7e4b67515c0269b5a236` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/designs/B6-WINDOW-DESIGN-REVIEW-2026-001.md` | `b55198105f9a8de95191ad9032679e73bbb4f33df4f9a9c47e3359b3d759fd2a` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-PACKET-2026-018-REFUSED-CREDENTIAL-LEGACY-VERSION-CARDINALITY.json` | `95735b36a225a3558cc95430258ec3d3b3a6ceb4976387498fa82004f5b3ca62` |
| `platform/evidence/B6-PACKET-2026-019-REFUSED-BRIDGE-PRINCIPAL.json` | `fcdf8fc4a1198cb38c1d905e83935698fddda6d0fdb5382da9e8e1a36c2e67e6` |
| `platform/evidence/B6-PACKET-2026-020-NOT-EXECUTED-PRINCIPAL-PREFLIGHT-CONDITION.json` | `64d9d6a29562535ce96137506cbac62d54286460d19dd6a23205a004d85394d5` |
| `platform/evidence/B6-PACKET-2026-020A-ATTEMPT-1-REFUSED-ENDPOINT-PLAN-GUARD.json` | `12c6c2cdfb72a88ef308d59a3ffac043a5330e7cb1c716b031e7662f798b8036` |
| `platform/evidence/B6-PACKET-2026-021-ATTEMPT-2-REFUSED-FARGATE-BOUNDARY.json` | `6f40490f9f8496036235085ebdc3b5b6042b5108753b6833245be5c293ed5b3b` |
| `platform/evidence/B6-R5-VERIFIER-AUDIT-2026-001.json` | `f4c55e8d31a65a9d10aa8d8e581be56732b1a88632df4d0c6d6417241b43413a` |
| `platform/evidence/receipts/B6-2026-020A-BRIDGE/persistent_secret_bridge.json` | `7e5c14f0afb1c6d2e2e34d49b3a251f6d31a1ba126bf1da0f3d59154acc22db7` |
| `platform/evidence/receipts/B6-2026-022-COLD/cold_rehearsal.json` | `b5b518b8c385fb343bc4a91de029817945b88969e4b9a5e0bf6580eaa52bab4f` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `a0dc826ac13a4cac83f8fc06e44e921a9911ee298515d976af8a5d876a9f64d9` |
| `scripts/b6_6_cleanup.sh` | `b53c91a84094685c1f783f2c39715b8c3fdfea08a9b0e74ab35206ff7b514403` |
| `scripts/b6_6_cold_rehearsal.py` | `6aab0b1869a327273cdc8ddabdeb448d5bf9c320d3756a9258c3324de3902bdf` |
| `scripts/b6_6_credential.py` | `cbb4bb9b7b36f0d06aa88a0f6b14a3cae0ff82fcee60c2b5cd63ca2763413754` |
| `scripts/b6_6_deadline.py` | `5cd2bc2a34e3b7b2b0a2f7767379ade170cecfaa4ca4ebc564ac56e5668acd79` |
| `scripts/b6_6_fargate_probe.py` | `41cf44dfe86d8f980d7a6c52e324d73a644df5e9f891a1e816384d21e04c7af7` |
| `scripts/b6_6_lbc_runtime.py` | `fd4294899a1d971f68e2b887677e8b703c66450e11a15b56c8e7d2854e282c8c` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_manifest_slice.py` | `61f79a8cdf04f8e94aac2a07b7f0f1924d560c7b7b9d0a5f64973b1ec4d217fb` |
| `scripts/b6_6_operations.sh` | `fa70f2e32ce833a4a9ffd1e7c0668b1c3e32cb313efa0fcaa2cd31599d067bd7` |
| `scripts/b6_6_persistent_secret_bridge.py` | `2f9ab3328d2b466702557853e21cab5e674d1ba22e3dcdef7c134480e497a083` |
| `scripts/b6_6_pre_endpoint_images.py` | `3ce744d886afe58e445bc415bf096bf74b9301d83f3b9c450452b144b4260491` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `ada3a4849cf6eb4853ec6f7a9f0778fe61954effa63c0e849bfb499f60cee2f1` |
| `scripts/b6_6_runner.py` | `329d28e25bbf99ba81c88185bf953afb11bf58f2cb67e71f41dc7cb87182019d` |
| `scripts/b6_6_stage_a.py` | `16df1750458a9e78fcb3b4aba0dff8c7bb7d9647c05f078cbda37be9997bbb67` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_persistent_secret_plan.py` | `d9f03d9a0fe67d259587403121bd4df19cbaf98e8e1150f03111b03ed1eebd20` |
| `scripts/check_b6_6_window_plan.py` | `f73a36f5e34482d5cf3ad2906c5badb9808248e229ec956b2e14ea777b7094fa` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_consolidated_window.py` | `61634b173050712461bee9bbe655a58ecb1e2e188391d8f6731448949353658c` |
| `tests/test_b6_6_fargate_boundary.py` | `640abad6ebe7ba0cfb56a90fbf9b89544995662b536d0692c3b737daa2b9de98` |
| `tests/test_b6_6_r5_verifier_audit.py` | `9eebb2068af7214982d9e066464991f48eb6fffa89a953c0dffb2c4b3492d70c` |
| `tests/test_b6_6_stage_a.py` | `d318a5022669f53b05159eac7a133fae60a5a5097e7f9b55504e697059b1324f` |

## Deviations

No deviations from the independent directive or R1–R7. The Stage A probe-only
scope is implemented directly. Three consecutive tasks define stability without
silently retrying a failed task. R7 is renewed only because the prior allowance
is exhausted.

## Approval boundary

Independent review must bind the prepared repository commit, packet SHA-256,
cold receipt SHA-256, R5 audit, Stage A source and eight-stage receipt contract,
the live refusal evidence, named Terraform receipts, all carried controls and
the combined allowance. Only then may the owner state:

> Approve B6 AWS change packet 2026-022 only, including one Stage A isolated
> qualification capped at 1,800 seconds and $0.50, followed only after Stage A
> PASS by a fresh two-attempt allowance of 9,000 seconds maximum (4,500 seconds
> per attempt, non-transferable), with a combined ceiling of approximately
> $3.70 inside the existing $10 reservation.

An owner-approved `B6-AWS-AUTH-2026-022` record is required before any execution.
