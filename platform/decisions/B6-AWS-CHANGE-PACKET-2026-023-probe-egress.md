# B6 AWS change packet 2026-023 — probe egress correction

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Packet 2026-022 is terminal. Stage A created its isolated resources, then the
first probe task stopped before application start because the task ENI security
group had no egress. Cleanup removed every temporary resource and returned CPU
and GPU desired capacity to zero. No full-window attempt was unlocked or
consumed. This successor corrects that single network boundary, strengthens the
application-start classifier and makes missing task-ENI egress a zero-cost cold
rehearsal refusal. This draft authorizes no AWS, Terraform, Kubernetes, worker,
secret or service mutation.

## Immutable predecessor and refusal

| Binding | Value |
|---|---|
| Packet 2026-022 | `bf2281e7246e8c08920a9daa6e7b68d90723efd285140938b886a07c1eb0cf50` |
| Authorization 2026-022 | `486e8b53b490d46082bd23780282225e8729486af339febd2fae5efeb077a0a8` |
| Refusal evidence | `platform/evidence/B6-PACKET-2026-022-STAGE-A-REFUSED-ECR-EGRESS.json` |
| Refusal evidence SHA-256 | `9245724747ebca8e2a6f286dc9abd057789be70288d5b61cbdb691bd2b972114` |
| Terminal stage | `stage_a_probe_1: REFUSED` |
| Stable probe passes | `0 / 3` |
| Window attempts unlocked / consumed | `0 / 0` |
| EKS worker mutations | `0` |
| Cleanup | `PASS`, receipt SHA-256 `d27ecdb5bfc5d8e9d3b39dc8272279d48324dd54c101ea5a8326a66c7e5bbef1` |
| Final CPU / GPU desired capacity | `0 / 0` |

The stopped task had no `runtimeId`, but that field is not an application-start
signal. The independent investigation confirmed the task ENI had no permitted
outbound route to ECR. ECR layer downloads also redirect to S3, so ECR-only
egress would fail one step later.

## Exact egress correction

The self-isolated probe security group receives exactly two outbound rules:

1. TCP 443 to `aws_security_group.b6_probe_endpoints[0].id`, permitting the
   task ENI to reach only the packet-created ECR API/DKR interface endpoints.
2. TCP 443 to `aws_vpc_endpoint.b6_probe_s3[0].prefix_list_id`, permitting the
   layer downloads that ECR redirects to the resource-bounded S3 gateway
   endpoint.

No CIDR egress, internet gateway route or public IP is introduced. DNS is
explicitly `NOT_APPLICABLE_AMAZON_PROVIDED_VPC_RESOLVER`: security groups do not
filter traffic to the Amazon-provided VPC resolver, so a DNS SG rule would be
false control rather than additional isolation.

The endpoint verifier requires the two exact rules after endpoint availability.
A missing, extra, broader or differently targeted egress permission refuses
before any task launches.

## Plan boundaries

Stage A now permits exactly `11 add / 0 change / 0 destroy`:

- `aws_ecs_cluster.b6_probe[0]`
- `aws_ecs_task_definition.b6_probe[0]`
- `aws_iam_role.b6_probe_execution[0]`
- `aws_iam_role_policy.b6_probe_execution[0]`
- `aws_security_group.b6_probe_endpoints[0]`
- `aws_vpc_endpoint.b6_probe_ecr_api[0]`
- `aws_vpc_endpoint.b6_probe_ecr_dkr[0]`
- `aws_vpc_endpoint.b6_probe_s3[0]`
- `aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]`
- `aws_vpc_security_group_egress_rule.b6_probe_to_ecr_endpoints[0]`
- `aws_vpc_security_group_egress_rule.b6_probe_to_s3[0]`

The full-window endpoint plan remains the same Stage A resources plus the two
reviewed ALB/node ingress rules, now exactly `13 add / 0 change / 0 destroy`.
The load-balancer controller remains a separate, exact `1 / 0 / 0` plan. Every
guard compares both counts and complete resource-name sets before apply.

## Application-start classifier

`application_started` is now derived only from ECS container state and exit
code: container `lastStatus=RUNNING` proves start immediately; a stopped
container proves start only when it carries an integer `exitCode`. `runtimeId`
is never consulted. Overall task success still requires task `STOPPED`,
container `STOPPED` and exit code `0`. Tests prove all three boundaries: a
runtime ID without an exit code does not classify start, a running container
does, and a stopped container with a numeric nonzero exit classifies start but
refuses the task result.

## Static task-ENI egress lint

The cold rehearsal now enumerates every security group attached to a task ENI
in either Stage A or the full window and fails closed unless each has at least
one egress rule:

- the packet-created probe SG has the two exact Terraform-managed rules above;
- the existing backend task SG has one live-readback-attested outbound rule,
  bound by `platform/evidence/B6-BACKEND-TASK-ENI-SG-EGRESS-READBACK-2026-001.json`,
  SHA-256 `e34ef5d6bdc32fd794a03122bf65ddff8b482b2f1da7fa8c29514d7c5f0fc3f4`.

The generic lint is independent of those exact policy checks. Removing the
egress mapping for either attached SG produces a cold refusal. This catches the
missing-egress bug class from a Terraform plan for `$0`.

## Stage A and window controls carried unchanged

Stage A remains one isolated, compute-light qualification with no EKS worker,
GPU, service, controller, ALB or secret mutation. It creates only the probe
cluster/task definition, task execution identity, isolated security group and
the three private endpoints. After endpoint readiness it launches exactly three
consecutive probe tasks. The first refusal stops Stage A; no hidden task retry is
allowed. Cleanup always runs, and only an aggregate Stage A PASS with three
consecutive task passes plus verified zero state unlocks either full window.

The Stage A operations deadline remains 1,200 seconds plus a separately bounded
600-second cleanup allowance: 1,800 seconds and `$0.50` maximum. Its receipt
chain remains `stage_a_preflight`, `stage_a_terraform`, `stage_a_endpoints`,
`stage_a_probe_1`, `stage_a_probe_2`, `stage_a_probe_3`, `stage_a_cleanup`, and
the aggregate `stage_a` receipt.

Both full-window attempts remain locked and unchanged at 4,500 seconds each,
non-transferable. Attempt 2 exists only if attempt 1 refuses and cleanup proves
zero state. A PASS ends the packet. All R1–R7 controls and the 23-stage window
sequence from packet 2026-022 carry forward unchanged, including deadline-first
shutdown, digest-pinned images, services before endpoints, receipt-per-stage,
internal-only ALB, dependency `ClusterIP` isolation, synthetic traffic only and
cleanup before deadline disarm.

No PHI, production traffic, real Bedrock or Fish call, model adoption,
`approved/asr/` write or production SSM pointer is permitted.

## Fresh cold rehearsal and verification

| Check | Result |
|---|---|
| Cold receipt | `platform/evidence/receipts/B6-2026-023-COLD/cold_rehearsal.json` |
| Cold receipt SHA-256 | `16dd4f3ad29c8e8ac0fbc381bad6e16a35acfa5be29c0af7185f27ab67b461de` |
| Scenario-results SHA-256 | `450a4d5b46fe2321a4829361b68f68eb8746b02a23dc14c232ef1c05bdf0f73b` |
| Full-window simulated PASS | `1`, with `23/23` PASS receipts |
| Full-window injected failures | `23`, each receipted and cleaned |
| Stage A simulated PASS | `1`, with `8/8` PASS receipts |
| Stage A injected failures | `7`, each receipted; cleanup reaches zero state |
| Task-ENI SG lint | `2` SGs, `3` egress rules, `0` missing; `2` removal-refusal cases |
| Real AWS / kubectl calls | `0 / 0` |
| AWS / Kubernetes mutations | `0 / 0` |
| Focused egress, Stage A, classifier and window tests | `43 passed, 0 failed` |
| Canonical repository suite | `1,402 passed, 0 failed, 0 skipped, 7 deselected` |
| Live read-only Stage A Terraform plan guard | `PASS`, exact `11 / 0 / 0` |
| Terraform fmt / validate | `PASS / PASS` |

## Allowance continuity request

Packet 2026-022's Stage A refused before any full-window attempt was unlocked.
The two attempts therefore remain unused but cannot be exercised under that
terminal packet. Packet 2026-023 requests one corrected Stage A plus the same
two contingent window attempts inside the existing `$10` reservation.

| Control | Requested value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Existing reservation | `$10.00` retained pending reconciliation |
| New reservation | `$0` |
| Corrected Stage A runs | `1` maximum |
| Corrected Stage A ceiling | `1,800` seconds and `$0.50` |
| Stage A stability proof | `3` consecutive private tasks |
| Stage A EKS/GPU/service mutations | `0 / 0 / 0` |
| Full-window attempts | `2` maximum, gated by Stage A PASS |
| Maximum per window | `4,500` seconds, non-transferable |
| Maximum requested worker seconds | `9,000` |
| Estimated compute for both windows | approximately `$3.20` |
| New Stage A plus window ceiling | `$3.70` |
| Conservative exposure including prior Stage A maximum | `$4.20`, within `$10` |

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
| `infra/b6_integration_window.tf` | `9f595ca443c150c8e85ff4ccd3de0277e41f5196b8368cf0bc7496f8b7dd0e7a` |
| `infra/eks.tf` | `37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b` |
| `infra/variables.tf` | `b8455916219f0a6858a73e4d0e83a04b57947306baf251b7a2228d52abf78c79` |
| `pipeline/b6_integration_receipts.py` | `95b9c276c4b02f31174d14bf35d2d7badddad301123888a030a5e3581f1056e1` |
| `platform/decisions/B6-AWS-AUTH-2026-022-stage-a-and-window.json` | `486e8b53b490d46082bd23780282225e8729486af339febd2fae5efeb077a0a8` |
| `platform/decisions/B6-AWS-CHANGE-PACKET-2026-022-fargate-boundary.md` | `bf2281e7246e8c08920a9daa6e7b68d90723efd285140938b886a07c1eb0cf50` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json` | `831c164a6ca75017a3f9d11e38550cc52c7785b3abcb65f1963d82378995e244` |
| `platform/decisions/B6-WINDOW-VERIFIER-POLICY-2026-001.json` | `73eacb9cc6a9d9850098464f70380c92e25c46ac4aff7e4b67515c0269b5a236` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/designs/B6-WINDOW-DESIGN-REVIEW-2026-001.md` | `b55198105f9a8de95191ad9032679e73bbb4f33df4f9a9c47e3359b3d759fd2a` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-BACKEND-TASK-ENI-SG-EGRESS-READBACK-2026-001.json` | `e34ef5d6bdc32fd794a03122bf65ddff8b482b2f1da7fa8c29514d7c5f0fc3f4` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-PACKET-2026-018-REFUSED-CREDENTIAL-LEGACY-VERSION-CARDINALITY.json` | `95735b36a225a3558cc95430258ec3d3b3a6ceb4976387498fa82004f5b3ca62` |
| `platform/evidence/B6-PACKET-2026-019-REFUSED-BRIDGE-PRINCIPAL.json` | `fcdf8fc4a1198cb38c1d905e83935698fddda6d0fdb5382da9e8e1a36c2e67e6` |
| `platform/evidence/B6-PACKET-2026-020-NOT-EXECUTED-PRINCIPAL-PREFLIGHT-CONDITION.json` | `64d9d6a29562535ce96137506cbac62d54286460d19dd6a23205a004d85394d5` |
| `platform/evidence/B6-PACKET-2026-020A-ATTEMPT-1-REFUSED-ENDPOINT-PLAN-GUARD.json` | `12c6c2cdfb72a88ef308d59a3ffac043a5330e7cb1c716b031e7662f798b8036` |
| `platform/evidence/B6-PACKET-2026-021-ATTEMPT-2-REFUSED-FARGATE-BOUNDARY.json` | `6f40490f9f8496036235085ebdc3b5b6042b5108753b6833245be5c293ed5b3b` |
| `platform/evidence/B6-PACKET-2026-022-STAGE-A-REFUSED-ECR-EGRESS.json` | `9245724747ebca8e2a6f286dc9abd057789be70288d5b61cbdb691bd2b972114` |
| `platform/evidence/B6-R5-VERIFIER-AUDIT-2026-001.json` | `f4c55e8d31a65a9d10aa8d8e581be56732b1a88632df4d0c6d6417241b43413a` |
| `platform/evidence/receipts/B6-2026-020A-BRIDGE/persistent_secret_bridge.json` | `7e5c14f0afb1c6d2e2e34d49b3a251f6d31a1ba126bf1da0f3d59154acc22db7` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a.json` | `8d4d15d78353b1026f2cc5c869b520e9c1fde0ef80c9f1ca4c2e9449461420d4` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_cleanup.json` | `d27ecdb5bfc5d8e9d3b39dc8272279d48324dd54c101ea5a8326a66c7e5bbef1` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_endpoints.json` | `a15275d56869a07761eb9a8e533735265bfc24e8a1c7f630494918176147ee9e` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_preflight.json` | `e55ba7575a59be97a5882e944d4d6cedc23a4c11c99e9484212f1f40f55a16f4` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_probe_1.json` | `08245c02446686680559b8481320642ec8a7ec10291fba1d0095cbebbb786d07` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_terraform.json` | `4dd1c4d80a26b8e76a64d5975143b146ae663f1ed0bc2b910d5cf7b1c63a51e5` |
| `platform/evidence/receipts/B6-2026-023-COLD/cold_rehearsal.json` | `16dd4f3ad29c8e8ac0fbc381bad6e16a35acfa5be29c0af7185f27ab67b461de` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `97d3c768a13e20921006c0188af5a42462683cf55aa5fccc19b05b6697f3bc12` |
| `scripts/b6_6_cleanup.sh` | `5ea9de3ef7bf1553e6f4e3af3258e95618b8968c70e59be77c0da559d55b0c1b` |
| `scripts/b6_6_cold_rehearsal.py` | `ce41a691ec01b482a75782b4e04ae5b0ffaca3db67dda0113a8bfde323ad6c7d` |
| `scripts/b6_6_credential.py` | `cbb4bb9b7b36f0d06aa88a0f6b14a3cae0ff82fcee60c2b5cd63ca2763413754` |
| `scripts/b6_6_deadline.py` | `5cd2bc2a34e3b7b2b0a2f7767379ade170cecfaa4ca4ebc564ac56e5668acd79` |
| `scripts/b6_6_fargate_probe.py` | `98405044cfc12213f5983a6382218eafa078b5eace18c31232a99a1d2b690207` |
| `scripts/b6_6_lbc_runtime.py` | `fd4294899a1d971f68e2b887677e8b703c66450e11a15b56c8e7d2854e282c8c` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_manifest_slice.py` | `61f79a8cdf04f8e94aac2a07b7f0f1924d560c7b7b9d0a5f64973b1ec4d217fb` |
| `scripts/b6_6_operations.sh` | `2677b1a06dd0954d574fe1e85605ed191eaba07a03742c9f13cbc5ba329ee985` |
| `scripts/b6_6_persistent_secret_bridge.py` | `2f9ab3328d2b466702557853e21cab5e674d1ba22e3dcdef7c134480e497a083` |
| `scripts/b6_6_pre_endpoint_images.py` | `3ce744d886afe58e445bc415bf096bf74b9301d83f3b9c450452b144b4260491` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `a7c96f2487ef46de4e678d605b2fc2f53c3d3425ef6a69ebf0dea98713e60903` |
| `scripts/b6_6_runner.py` | `fd3e2f0085fa6a1fade06746300d7e1b3e3c726a4ec6e635cbf3ff596bf81bc4` |
| `scripts/b6_6_stage_a.py` | `deb1ef8f90733b70a4f1e7326703e6d8285ae857ba892b1ca0c888d1bb65b7c2` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_persistent_secret_plan.py` | `d9f03d9a0fe67d259587403121bd4df19cbaf98e8e1150f03111b03ed1eebd20` |
| `scripts/check_b6_6_window_plan.py` | `468f5ddda85c8573b3d1327e19b431d8dfa52bd20ba09ee8fc14a038a0fc1ddc` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_consolidated_window.py` | `3f333155fdc2cf7f6d1971d571292ffa755ca6b20ef8ab3e1f9925b3ce0ecdc9` |
| `tests/test_b6_6_fargate_boundary.py` | `9275dab60ae53de5aea06ef2724343b1936b13ec0c916070787732ecffd274d8` |
| `tests/test_b6_6_r5_verifier_audit.py` | `9eebb2068af7214982d9e066464991f48eb6fffa89a953c0dffb2c4b3492d70c` |
| `tests/test_b6_6_stage_a.py` | `0c52537f448f60c7c663e7efe393a4cbc8e9b1d227eb8f05b1b48cd8f78d7780` |

## Deviations

None. Both required egress paths, the DNS exemption, last-status/exit-code
classifier, task-ENI static lint, three-consecutive-pass Stage A gate and both
locked window attempts are implemented exactly as directed.

## Approval boundary

Independent review must bind the prepared repository commit, packet SHA-256,
cold receipt SHA-256, exact egress rules, classifier, static lint, Stage A
receipt contract and allowance. Only then may the owner state:

> Approve B6 AWS change packet 2026-023 only, including one corrected Stage A
> qualification capped at 1,800 seconds and $0.50, followed only after Stage A
> PASS by two full-window attempts capped at 4,500 seconds each and approximately
> $3.20 combined, within the existing $10 reservation.
