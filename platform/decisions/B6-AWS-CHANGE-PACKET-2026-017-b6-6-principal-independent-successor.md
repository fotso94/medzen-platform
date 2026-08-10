# B6 AWS change packet 2026-017 — principal-independent synthetic integration successor

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-10

Account/region: `558069890522` / `eu-central-1`

Required profile/operator: `medzen` / `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested

Authorize one bounded synthetic-only B6.6 integration window that:

1. restores and rotates the recoverable test credential as compute-free stage 0;
2. removes the same-apply role-propagation dependency from the two ECR VPC
   endpoint policies;
3. gives the one temporary Fargate probe an endpoint-exclusive network path;
4. arms both worker shutdown deadlines before any capacity rises; and
5. runs the unchanged digest-pinned integration proof and zero-state cleanup.

This draft authorizes no AWS or Kubernetes mutation. Execution requires an
independent review bound to this packet SHA-256 and prepared commit, followed
by an exact owner authorization record `B6-AWS-AUTH-2026-017`. No earlier
packet authorization applies.

## Immutable history

PR #27 is merged at `25fb88c37e4a5890a32fc15a529394661c951cd5`.
Packet 2026-016 remains closed by the immutable refusal record:

- packet SHA-256:
  `1560c5b6a775377cff43bf46a236bdd5da0c645cf3f846b33bc63ed50c670f6d`;
- result:
  `platform/evidence/B6-PACKET-2026-016-REFUSED-ECR-ENDPOINT-POLICY.json`;
- result SHA-256:
  `7538b6a3f9d80201b8161f43aef0115d0d3424d7daff33caa58e460308b940f3`;
- failure: both ECR interface endpoint policies referenced the temporary role
  within four seconds of that role's creation and AWS refused them;
- deployment, Fargate and ALB activity: none; and
- automatic cleanup: PASS at CPU/GPU desired zero and no window resources.

This packet supersedes only the prospective retry design. It does not edit,
resume or reinterpret packet 2026-016 or any earlier receipt.

## Endpoint-policy decision

The requested simpler alternative was evaluated first. The exact conclusion
is:

- a custom VPC endpoint policy cannot literally omit `Principal`; AWS requires
  a Principal element in every custom endpoint policy;
- omitting a custom policy selects the AWS default of
  `Principal:"*" / Action:"*" / Resource:"*"`, which is unnecessarily broad;
- a custom policy with required `Principal:"*"` can retain the exact action
  and resource boundary and removes any dependency on a newly created role ARN;
- endpoint policy is an additional boundary and does not grant the probe its
  identity permissions; the temporary probe role must still permit the exact
  pull actions.

Selected policies:

| Endpoint | Principal | Actions | Resources |
|---|---|---|---|
| ECR API | `*` | `ecr:GetAuthorizationToken` | `*` as required by ECR |
| ECR DKR | `*` | `ecr:BatchCheckLayerAvailability`, `ecr:BatchGetImage`, `ecr:GetDownloadUrlForLayer` | exact `medzen-rag-index` repository ARN |
| S3 gateway | unchanged `*` | `s3:GetObject` | exact regional ECR starport layer-bucket objects |

The default full-access endpoint policy is prohibited. A role ARN or any other
specific principal in either ECR endpoint policy is also prohibited. The
staged role-first design is not selected because the exact custom policy above
removes the propagation race without broadening actions or repository scope.

Official service references:

- `https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-access.html`
- `https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html`
- `https://docs.aws.amazon.com/IAM/latest/UserGuide/reference_policies_evaluation-logic.html`

## Network correction

Read-only evaluation proved that `sg-0a83abae6ab954543`, previously described
as the probe SG, is actually the shared production-tagged
`medzen-ehrbase-ecs-sg` with two in-use ECS interfaces. It is therefore not an
endpoint-exclusive source identity.

The successor uses two SGs on the one temporary Fargate task:

1. `sg-0a83abae6ab954543` only for the already reviewed path to the internal
   ALB; and
2. the temporary `medzen-b6-probe-vpce` SG for ECR endpoint access.

Both ECR interface endpoints use only the temporary SG. Its only ingress is a
self-reference on TCP/443. No standing workload carries that SG; only the one
temporary probe task and the two endpoint interfaces do. The task has no
public IP and uses only the reviewed three private subnets. This provides the
network boundary independently of the shared backend SG.

The read-only evaluation is recorded at
`platform/evidence/B6-6-ENDPOINT-POLICY-CORRECTION-EVALUATION-2026-001.json`.
Its saved Terraform plan was exactly `12 add / 0 change / 0 destroy`; no apply
was performed.

## Credential restoration as stage 0

Credential restoration is now part of this packet rather than a separate
approval round. It uses the already proven packet-015 staged machinery and the
request manifest
`platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-004.json`.

Required start:

- exact secret ARN ending `medzen/client-api-keys-NxZGxE` is pending
  recoverable deletion;
- `daacb67e-fcd1-41e1-bf62-47a3f18c8d0b` is `AWSCURRENT`;
- the two older versions are unstaged;
- secret-policy and orchestrator KMS-policy Terraform addresses are absent;
- local synthetic material is absent; and
- CPU/GPU desired sizes are zero.

Stage 0 may only:

1. verify the exact zero boundary without plaintext reads;
2. restore the exact recoverable ARN;
3. import and normalize only its exact Terraform address if needed;
4. restore the same exact-reader resource policy and KMS inline policy;
5. generate 32 fresh random bytes, never reuse old material;
6. publish one fresh version and write the exact mode-`0600`, LF-terminated
   local test file;
7. remove every staging label from all three prior versions; and
8. persist immutable receipts binding the fresh version ID, bearer SHA-256 and
   canonical secret-value SHA-256 without plaintext.

The worker deadline cannot arm and compute cannot start until the dynamic
verification receipt and local file match. On any stage-0 failure, cleanup
removes local material and the three Terraform addresses, schedules seven-day
recoverable deletion, and writes its own cleanup receipt. Force deletion is
forbidden.

## Time and cost boundary

| Allowance fact | Seconds |
|---|---:|
| Maximum cumulative worker-window allowance | 14,400 |
| Conservatively consumed through packet 2026-016 | 5,985 |
| Remaining before this packet | 8,415 |
| Maximum packet-2026-017 worker deadline | 4,500 |
| Remaining after a full-cap run | 3,915 |

A successful window is estimated at approximately 3,000–4,500 seconds. The
current allowance therefore supports this capped attempt and leaves roughly
one minimum-duration real attempt, not two guaranteed full-cap attempts.
Credential stage 0 is compute-free and occurs before the worker-window clock;
its work must be kept bounded so it does not consume the practical execution
day or weaken secret recovery margins.

- Aggregate project ceiling: `$300`.
- Cost registry: `COST-REGISTRY-2026-004`.
- Existing allocation: `B6-INTEGRATION-WINDOW-2026-001`.
- Existing active reservation: `$10`.
- New reservation: `$0`.

The reservation is a ceiling, not claimed spend. No extension is authorized.

## Deadline-first compute boundary

After credential verification, the first compute-related stage creates and
verifies independent AWS-side scale-to-zero schedules for the exact CPU and
GPU auto-scaling groups. Only then may the runner request at most:

- two `m6i.large` CPU nodes;
- one `g6.xlarge` GPU node;
- one `0.25-vCPU / 0.5-GiB` Fargate task;
- one internal ALB; and
- the three temporary private endpoints.

The deadline is exactly 4,500 seconds. Both worker groups must return to
desired zero, with zero instances, before the deadline actions are disarmed.

## Exact guarded Terraform boundary

After stage 0, the saved create plan must be exactly
`12 add / 0 change / 0 destroy`:

1. one window-only load-balancer-controller Helm release;
2. two existing ALB/node ingress rules;
3. one temporary Fargate execution role and one inline policy;
4. one temporary ECS cluster and one task definition;
5. one temporary endpoint SG;
6. one TCP/443 self-referenced ingress rule on that SG;
7. ECR API and ECR DKR interface endpoints;
8. one resource-bounded S3 gateway endpoint.

The three synthetic-secret/access resources must be no-op. Any unknown,
replacement, extra resource or policy drift refuses. The full cleanup plan is
exactly `0 add / 0 change / 15 destroy`; partial creation permits only a
machine-guarded non-empty subset of those 15 deletes.

The runner waits up to 900 seconds for all three endpoints to become available
and for their SG, subnet, route-table, tag and policy bindings to read back.
It repeats the endpoint gate immediately before launching Fargate.

## Digest-pinned execution identities

| Image | Scan-passed linux/amd64 child manifest |
|---|---|
| `medzen-rag-index` | `sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c` |
| `medzen-llm-gateway` | `sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a` |
| `medzen-speech-tts-gateway` | `sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d` |
| `medzen-model-loader` | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` |
| `medzen-asr-runtime` | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` |
| `medzen-orchestrator` | `sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa` |
| `medzen-nvidia-dra` | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` |

The controller remains pinned to
`sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f`.
No build, push, tag resolution, waiver or scan exception is authorized.

## Unchanged integration proof

Deploy only after endpoint readiness, in this order:

`controller → DRA → RAG → ASR/model-loader → TTS → LLM → orchestrator`.

The window remains synthetic-only: zero-shot Whisper large-v3 `v0`, fake LLM,
text-only TTS and the content-addressed non-serving test registry. It must prove
internal ALB health, isolated Fargate readiness, file and WebSocket flows,
final-result preservation, bounded cancellation, controlled refusals,
RAG-unavailable behavior and ClusterIP-only dependencies. B5 promotion remains
blocked and production serving state is untouched.

Only the four previously documented post-create listener/rule tag-mutation
denials may be a receipted non-fatal warning, after functional ALB and Fargate
PASS. Creation, routing, health, endpoint, cleanup and unknown denials remain
fatal.

## Receipts and cleanup

Credential receipts are persisted under `credential/` as each stage succeeds.
The main ordered receipts remain:

`local_bindings → deadline → workers_ready → terraform_window → endpoints_ready → controller_ready → dra_ready → rag_ready → asr_ready → tts_ready → llm_ready → orchestrator_ready → fargate_probe → alb_ready → alb_tag_mutation_warning → file_proof → websocket_proof → cancellation_proof → failure_drills → isolation_proof → cleanup`.

The `local_bindings` receipt must bind the dynamically generated credential
version and hashes. Missing, duplicate or out-of-order receipts refuse. A later
failure never erases an earlier success.

Automatic cleanup stops Fargate; removes Ingress and proves ALB absence;
removes workloads and DRA; deletes the guarded Terraform set; proves endpoint
and SG absence; scales both node groups to zero; removes local credential
material; schedules recoverable secret deletion; persists credential and main
cleanup receipts; and only then disarms deadlines.

## Prospective source binding

The final owner authorization must contain the exact source-binding map
validated by `scripts/b6_6_successor_bindings.py`. The local preparation record
is added after this packet's content hash exists and is also required by that
authorization. The following execution sources are fixed in this draft:

<!-- SOURCE_BINDINGS_START -->
| Source | SHA-256 |
|---|---|
| `infra/alb_controller.tf` | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| `infra/b6_6_endpoint_policy_override.tf` | `9dc7e893cd8e0e4612bd082541d7f884cd35e37e964202b577901a26f3b05dae` |
| `infra/b6_6_window_override.tf` | `c9dc7ebfd17b4ea0e9bf9b50fee7af529405ab44ee4e08827d3a5bf06ef39962` |
| `infra/b6_integration_window.tf` | `df4fec719aa8a709e94d89040c1bc283d9f847aa894a803fb00bb93e99f3c144` |
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `infra/b6_planning_override.tf` | `296ccddd66a108f3667273f0b2683c62d48882340fe2585bf1069ae47b32d2bf` |
| `infra/eks.tf` | `37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b` |
| `infra/variables.tf` | `59c1226f9a797e13756575ef77b45ce9324e1f1fb4743bc7d84fa8bec4f272dd` |
| `pipeline/b6_integration_receipts.py` | `a5eb39b8b022021db63bb115ff905f5b229e96cd48b6d56da6919d952b19664e` |
| `platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-004.json` | `b5976d02655f086fe2e7317a0f91bc9e08e7faae85c3a11ebcae252cdcfc2618` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/decisions/B6-AWS-CHANGE-PACKET-2026-016-b6-6-final-window.md` | `1560c5b6a775377cff43bf46a236bdd5da0c645cf3f846b33bc63ed50c670f6d` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json` | `831c164a6ca75017a3f9d11e38550cc52c7785b3abcb65f1963d82378995e244` |
| `platform/evidence/B6-PACKET-2026-016-REFUSED-ECR-ENDPOINT-POLICY.json` | `7538b6a3f9d80201b8161f43aef0115d0d3424d7daff33caa58e460308b940f3` |
| `platform/evidence/B6-6-ENDPOINT-POLICY-CORRECTION-EVALUATION-2026-001.json` | `1d885c1c2892d42638674a6d4e14778c493c21bea0601631f24738c2a175e2a5` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/iam/medzen-lbc-role.policy.template.json` | `722567dc992e0782781ffbc8eba1456f2412510188b2634c672dd59c3d9218ec` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_deadline.py` | `250144e007aaf8a42906d5e563032107898bc4f9be7d08571f9a499f87446949` |
| `scripts/b6_6_fargate_probe.py` | `f869f694d482ed18872e7adbdfcffa5aa6401d64af26aebee62a7bd9f83a72b4` |
| `scripts/b6_6_lbc_runtime.py` | `3436e804bdfcd3034f1abeaf1ea9f1e82520b3cdbe1dda07851d54ab83848656` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `295b69276ac3036bd4c65275b07406b1f6f68101d0e7e2e5dfcbf5bce015126b` |
| `scripts/b6_6_receipt.py` | `3629ffc8b3b6c34ec1d3cfdcde5c8aa28a73796806826f8dce5084a8995c7280` |
| `scripts/b6_6_successor_bindings.py` | `2d3b7fdb4fd84e76c6ddf779256dec2583779b471a808021e2e4357b507103ab` |
| `scripts/b6_6_successor_cleanup.sh` | `099602e156c08ebd1c6bf2d6486378d14e8d5b0d790f072e815b5b15744fd780` |
| `scripts/b6_6_successor_credential_stage.py` | `451b18a39cad59c3ce665bdc2bcdcbb1c7a3aa01a5ac25a54259801dd0fd5dee` |
| `scripts/b6_6_successor_credential_stage.sh` | `05d3ab9c419ee553e75bb72cb085d4949b688505a0be6b065ab5d87cee4ef88f` |
| `scripts/b6_6_successor_deadline.py` | `a85e54393bb520578c6a91ae54efe8e16d21ba019c17e29359b4ad1b512a5db7` |
| `scripts/b6_6_successor_fargate_probe.py` | `78bb3885861e0a2ce47e2b6204612a92f55c2caf1e18cb957fdf588dc1985a0d` |
| `scripts/b6_6_successor_probe_endpoints.py` | `fc5a3dc4142a9e9ce1b4325fbd438dc04feb23d688e9fdbd7b5125a30c6556a3` |
| `scripts/b6_6_successor_secret_preflight.py` | `87c0d4be943db11bc2711ed3742013d117bd090ac7823782eab0922923e742a5` |
| `scripts/b6_6_successor_token_binding.py` | `e742d0809164feb4ee2e824a0813401471f10ce478758ff061f731178fe4bc8a` |
| `scripts/b6_6_successor_window.sh` | `ccb03a030ff427683afafe8db3b33687570918a4be3f39ec5b47b484025900fc` |
| `scripts/b6_6_token_binding.py` | `a42290d7bc719abab414105af3f5813fce717bc0d76af3753aafca2745daffff` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/b6_client_secret_restoration_2026_015_bindings.py` | `86adfba4c1f222cbd51836241123161bdecbd95412bc35b7e2b0ba327b6bdc4c` |
| `scripts/check_b6_6_successor_window_plan.py` | `d0dfc2176e19e43e1ec1e140728d3a87148a75cddfe791f46df2ef2cd02a5d37` |
| `scripts/check_b6_6_window_plan.py` | `1535d6f5fbd086f528de0b9ed652dd064a311545e68d044a1217b008dc9a2f7b` |
| `scripts/check_b6_client_secret_restoration_2026_015_plan.py` | `44046a24e025a8b50449cfc8a801a029d98bb03089de4b47c5ec196f2c91a349` |
| `scripts/check_b6_client_secret_restoration_plan.py` | `9bb596216bcd9bd18440df9e698574021a278db8c7314a21ebced3d6d04d1f0e` |
| `scripts/pin_aws_lbc_digest.py` | `bcd3b3e97e992f96457df392f76aa9da8d91f801a0610cc5533660c6400ce5cb` |
| `scripts/run_b6_client_secret_restoration_2026_015.py` | `e23d58b004aae2743a52eb84122606e1b7e0d1ac618d070f79bf59438181e22f` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_successor_packet.py` | `6d74b0734ce48a832474f9cbab0cd1e2e35962f48efab6a47950c2131cab4f40` |
<!-- SOURCE_BINDINGS_END -->

Any source change after review invalidates the packet and authorization.

## Explicit prohibitions

No public ALB or Fargate IP, NAT, permanent endpoint, production DNS/TLS or
traffic, production SSM write, approved artifact, model registration, MLflow
transition, approved-version or language-scope change, real Bedrock/Fish call,
training, green-bucket mutation, PHI, image change, scan waiver, standing IAM
broadening, cost/time extension, old credential plaintext read/reuse, force
secret deletion or deadline disarm before zero is authorized.

## Approval boundary

After independent review bound to this packet SHA-256 and prepared commit, the
only valid owner phrase is:

`Approve B6 AWS change packet 2026-017 only.`

Until that review and matching `B6-AWS-AUTH-2026-017` record exist, do not
execute this packet.
