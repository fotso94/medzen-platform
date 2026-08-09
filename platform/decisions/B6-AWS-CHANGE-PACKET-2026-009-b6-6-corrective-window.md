# B6 AWS change packet 2026-009 — corrective B6.6 synthetic integration window

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-09  
AWS account: `558069890522`  
Region: `eu-central-1`  
Required operator: `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested

Authorize one corrective, synthetic-only B6.6 integration window. This packet
does not resume or reinterpret packet 2026-008. That packet is closed as
`REFUSED_WORKER_REGISTRATION_AND_CLEANUP_IMPLEMENTATION`; its receipts and
evidence remain immutable.

This draft authorizes no AWS or Kubernetes mutation. Execution requires an
independent architecture/IAM/security review bound to the final packet hash and
prepared commit, followed by a new owner authorization record
`B6-AWS-AUTH-2026-009` with the exact source map below.

## Why a new packet is required

Packet 2026-008 passed local bindings and armed both independent AWS shutdown
deadlines, then refused at `workers_ready`: its single GPU `kubectl wait` ran
before a matching GPU node resource existed. Its automatic cleanup also exposed
two source defects: the mode-0644 cleanup file was invoked directly, and the
disabled-secret Terraform plan indexed a count-zero resource. No service,
controller, ALB, Fargate probe or synthetic request was deployed. Emergency
cleanup proved both node groups and Auto Scaling groups at zero before removing
the deadlines.

Immutable execution evidence:
`platform/evidence/B6-PACKET-2026-008-REFUSED-WORKER-REGISTRATION.json`, SHA-256
`f2b8acbabafb2642e5b70ddbae966930f2ba62201c7a2fb26f6e32bc3246d432`.

## Corrective delta

1. A bounded worker gate waits for the exact two CPU and one GPU node resources
   to exist and become Ready. Missing resources are waited for, excess capacity
   refuses, and failures persist only a sanitized reason code.
2. The runner invokes the cleanup source explicitly through `bash`; file mode
   is no longer an unbound execution dependency.
3. Cleanup persists `INCOMPLETE` immediately at the failing step. A later
   bounded recovery uses a separate immutable `cleanup_recovery` receipt.
4. Disabled-secret Terraform evaluation uses a stable data-source address and
   a deterministic unused fallback ARN; live plan proof is
   `0 add / 0 change / 3 delete` with no CPU node-group delta.
5. CPU desired-size drift is ignored by Terraform, matching the existing GPU
   rule. Only the deadline-controlled EKS API path changes temporary capacity.
6. Prewindow checks refuse stale CPU/GPU nodes, synthetic pods, window Ingress,
   controller or DRA DaemonSet before capacity rises.
7. The retained local token must match packet-006 evidence by exact path, mode,
   byte length and SHA-256 before any AWS mutation.

Local correction evidence:
`platform/evidence/B6-6-LOCAL-CORRECTION-2026-001.json`, SHA-256
`00882ca9d28a867a46b9269144cf6567672415781b09cfc84153020479cdf881`.

## Live starting state required

Execution refuses unless all of these remain true:

- cluster `medzen-speech` is EKS `1.36` with support type `STANDARD`;
- CPU node group is `ACTIVE`, `min=0`, `desired=0`, `max=4`, healthy;
- GPU node group is `ACTIVE`, `min=0`, `desired=0`, `max=1`, healthy;
- both backing Auto Scaling groups have desired zero and no instances;
- neither group has a scheduled action;
- Kubernetes has zero CPU/GPU nodes, zero synthetic B6.6 pods, no window
  Ingress, no window controller and no NVIDIA DRA DaemonSet;
- the content-addressed deployment registry has exactly three parameters;
- `/medzen/registry/serving/current` remains absent;
- `medzen/client-api-keys` exists and is not pending deletion;
- `/private/tmp/medzen-b6-6-client-token` is mode `0600`, 44 bytes including
  newline, and SHA-256
  `fe83e1a29619c5b05b83b1d77d820dde850d35e6a75102947881e6d152d68be6`;
- the source bindings and future authorization are exact; and
- no other billable reservation is active.

## Immutable images

Every deployable image is pinned to the scan-passed linux/amd64 child manifest,
never a tag or OCI index:

| Component | Child digest |
|---|---|
| RAG index | `sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c` |
| LLM gateway | `sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a` |
| Speech orchestrator | `sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa` |
| TTS gateway | `sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d` |
| Model loader | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` |
| ASR runtime | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` |
| NVIDIA DRA | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` |
| AWS Load Balancer Controller | `sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f` |

All applicable ECR scans are complete with zero critical and zero high
findings. No rebuild, repush, mutable tag, waiver or image substitution is
authorized.

## Terraform proof and allowed resources

The live read-only previews were not applied:

- create: exactly `7 add / 0 change / 0 destroy`, machine guard
  `PASS_B6_6_CREATE`, ephemeral plan SHA-256
  `0eab2b1232b4ba46e78ba22216807b0b54358a015dbf2eccc8b0cb9875579e4c`;
- current cleanup: exactly `0 add / 0 change / 3 destroy`, machine guard
  `PASS_B6_6_CLEANUP`, no CPU node-group delta, ephemeral plan SHA-256
  `97124b5463321f45b32b7052bef6654d48a8010c0432e0f167653367a7a7cbf3`.

The create plan may add only:

- one window Helm release for the digest-pinned controller;
- two exact source-security-group ingress rules;
- one ephemeral ECS execution role and one narrow ECR-pull inline policy;
- one ECS cluster and one Fargate task definition.

The retained secret resources are unchanged during create. A successful full
cleanup expects the seven window deletes plus the three recoverable secret
deletes. An early cleanup accepts only a non-empty delete-only subset of those
ten exact addresses. Any create, update or unknown address refuses.

The ephemeral ECS role trusts only `ecs-tasks.amazonaws.com`; its inline policy
permits `ecr:GetAuthorizationToken` and read-only layer/manifest access for the
exact RAG repository. It has no application task role and no SSM, Secrets
Manager, KMS, S3, logging, Bedrock, network-mutation or write permission.

## Deadline-first execution

The cumulative original allowance remains 14,400 seconds. Packet 2026-008 is
conservatively charged 1,784 seconds from earliest worker launch through final
zero proof. This packet permits at most 12,600 additional seconds, leaving a
16-second safety margin. It creates no new reservation.

Execution order is binding:

1. Validate the owner authorization, independent review, all source hashes,
   caller/account, token binding, retained boundaries and zero-state checks.
2. While capacity is zero, arm and read back CPU and GPU AWS-side scale-to-zero
   actions at the same 12,600-second deadline. A partial arm is rolled back.
3. Scale to at most two `m6i.large` CPU nodes and one `g6.xlarge` GPU node.
   Poll until exactly those three resources exist and are Ready; persist the
   worker receipt immediately or refuse with a sanitized receipt.
4. Regenerate, guard and apply the exact seven-add Terraform plan. Prove the
   controller Ready on its scan-passed child digest.
5. Apply and prove the DRA driver, then apply the 23-object service bundle with
   every application Deployment initially at zero.
6. Raise and prove Ready in order: RAG → ASR/model-loader → text-only TTS →
   fake LLM → orchestrator.
7. Prove the single ALB is internal, application type and bound only to
   `sg-0f0f6c66852830013`; run at most one short-lived Fargate `/readyz` probe
   from exact backend SG `sg-0a83abae6ab954543`.
8. Through local port-forwarding, run authenticated synthetic file, WebSocket,
   cancellation/barge-in, controlled-refusal and RAG-unavailable proofs.
9. Prove all dependencies are `ClusterIP`, only the orchestrator has Ingress,
   no public load balancer exists, and every receipt is durable and PHI-safe.

Every stage receipt is write-once and fsync-persisted immediately. It contains
no audio, transcript, reply, citation text, credential, raw command output or
PHI. A later failure never voids an earlier successful receipt.

## Service, model and data limits

- The ASR artifact remains zero-shot Whisper large-v3 `v0`, tree SHA-256
  `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e`.
- This serving proof does not approve the model or change B5's `BLOCKED`
  decision, any language artifact, or any `approved_version`.
- LLM is fake, TTS is text-only and RAG is embedded synthetic/non-clinical.
- Real Bedrock, Fish, clinical content, client traffic, PHI, training and
  green-bucket mutation are prohibited.
- Network exposure is one internal HTTP/80 ALB for synthetic testing only;
  there is no public listener, DNS alias, TLS or production traffic.

## Cost boundary

- Aggregate ceiling: `$300`.
- Recognized committed guardrail: `$63.5288`.
- Existing active reservation: exactly `$10.00` under
  `B6-INTEGRATION-WINDOW-2026-001`; new reservation: `$0`.
- Maximum capacity: two `m6i.large`, one `g6.xlarge`, one short-lived
  `0.25-vCPU / 0.5-GiB` Fargate task and one internal ALB.
- Existing worker estimate: `$1.2364/hour`; packet-008 actual billing remains
  pending and is not reconstructed from wall time.
- The reservation remains open until attributable billing and complete cleanup
  are recorded. No other reservation may be active.

## Cleanup and zero-state closure

`EXIT`, interrupt and termination invoke the hash-bound cleanup through `bash`.
Cleanup order remains:

1. stop the Fargate probe;
2. delete the Ingress and wait for ALB/listener/target-group absence;
3. scale/delete application objects and delete DRA;
4. generate and machine-guard the delete-only Terraform plan, then remove the
   controller, SG rules, ECS resources, temporary role/policy and three
   synthetic-secret resources with seven-day recoverable deletion;
5. set both node groups to zero, prove both Auto Scaling groups empty, and only
   then disarm both deadlines;
6. remove exactly `/private/tmp/medzen-b6-6-client-token`;
7. prove zero window pods, deployments, Ingress, ALB, ECS resources, worker
   nodes/instances and deadline actions, plus absent production SSM pointer and
   zero approved/model-registration changes.

If cleanup refuses, it writes an immutable `INCOMPLETE` receipt naming only the
safe failing step and keeps the deadlines armed. A later bounded recovery writes
`cleanup_recovery`; it never overwrites the first receipt.

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
| `pipeline/b6_integration_receipts.py` | `0d23969a2de73db1793404ba168365f6bf5b2b8d6cfb38e73525636079cc6976` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-6-LOCAL-CORRECTION-2026-001.json` | `00882ca9d28a867a46b9269144cf6567672415781b09cfc84153020479cdf881` |
| `platform/evidence/B6-CLIENT-API-KEYS-2026-001.json` | `6120c7a9b82dd51a2ceccd504156c8448c0322c5ba31e65334505caf3856c2e0` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-LBC-QUALIFICATION-AWS-EXECUTION-2026-001.json` | `56265113cbfa3ebec85309ec9966dc5fb7a2dd28e1c5fad0b1a4dd6e946cb8f3` |
| `platform/evidence/B6-PACKET-2026-008-REFUSED-WORKER-REGISTRATION.json` | `f2b8acbabafb2642e5b70ddbae966930f2ba62201c7a2fb26f6e32bc3246d432` |
| `platform/evidence/B6A-PACKET-2026-003C-A-SCAN-RESULT.json` | `1b1ed84205fe9a71c3b21b2a2658814855fd5fdcf6af00c5590bb4205e8dc70b` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `f2d75eed840b951b42c5b70da78959eb3050bd3804722691aac02ef698b754f5` |
| `scripts/b6_6_cleanup.sh` | `be0438b87ac48421573873a205a4a0cce9528e1d9a7a198406e6e5f1d46d8dcc` |
| `scripts/b6_6_deadline.py` | `c1ef7700b6fd746348724e7e532f00fdea178034d323dc28e39b3ae01cb61a4a` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_receipt.py` | `3629ffc8b3b6c34ec1d3cfdcde5c8aa28a73796806826f8dce5084a8995c7280` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_window_plan.py` | `8c19f2a5bc236f37d07c99999e093e48927e503bf2b52f786d34447ed117509f` |
| `scripts/pin_aws_lbc_digest.py` | `bcd3b3e97e992f96457df392f76aa9da8d91f801a0610cc5533660c6400ce5cb` |
| `scripts/run_b6_6_integration_window.sh` | `4cd380ebdf4456cc6f4720ddca3cb786042e37c4f11cde2431725d80b9fa8a63` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |

Any missing or mismatched source, unknown state, plan delta, stale resource,
capacity excess, deadline defect or incomplete evidence refuses before the next
stage.

## Explicit prohibitions

- no public ALB, production DNS/TLS/traffic, production SSM alias or registry
  write;
- no `approved/asr/` write, model registration, MLflow transition, language
  artifact/approved-version change, training or B5 reinterpretation;
- no real Bedrock or Fish call, clinical content, PHI or green-bucket change;
- no image rebuild, push, tag deployment, scan waiver or unreviewed IAM delta;
- no capacity or cumulative time above this packet's limits; and
- no disarming the AWS deadlines before exact CPU and GPU zero is proven.

## Review and approval phrase

This packet is not authorized by its preparation. After independent review of
the final packet SHA-256 and prepared commit, the only valid owner phrase is:

`Approve B6 AWS change packet 2026-009 only.`
