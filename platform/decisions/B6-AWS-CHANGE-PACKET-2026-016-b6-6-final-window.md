# B6 AWS change packet 2026-016 — final synthetic integration window

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-10
Account/region: `558069890522` / `eu-central-1`
Required profile/operator: `medzen` / `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested

Authorize one final, bounded, synthetic-only B6.6 integration window using the
private-probe corrections independently reviewed in PR #24 and the fresh
credential produced by owner-approved packet 2026-015.

This draft itself authorizes no AWS or Kubernetes mutation. Execution requires
an independent review bound to this final packet SHA-256 and prepared commit,
then a new owner authorization record `B6-AWS-AUTH-2026-016` with the exact
packet, credential, cost and source bindings below. No authorization from an
earlier packet applies.

## Immutable history and prerequisite closure

The reviewed non-executable design remains unchanged:

- packet: `B6-AWS-CHANGE-PACKET-2026-014`;
- SHA-256:
  `f31cb8f36d76d32884639bbe8bfb750ca807a92847d24f0abf4e1eef7d8c6428`;
- published-master commit:
  `ab6208cdd9e45e6950069f1589af63fc4654f7c0`; and
- outcome: design PASS, deliberately blocked on fresh credential evidence.

Packet 2026-015 was independently reviewed, owner-approved and executed once.
Its immutable execution evidence is:

- path:
  `platform/evidence/B6-CLIENT-SECRET-RESTORATION-AWS-EXECUTION-2026-002.json`;
- SHA-256:
  `3d221399287dc55c3ae2d72d1a5e381680dc5263d21451c8434ccc21f95becb3`;
- status: `VERIFIED_COMPLETE`;
- restore calls: one;
- new secret ARNs: zero;
- old plaintext reads or reuse: zero;
- CPU/GPU: zero throughout; and
- integration-window activity: zero.

Packets 2026-013, 2026-014 and 2026-015, their authorizations, receipts and
evidence remain immutable. Packet 2026-016 supersedes the window design by
reference; it does not reinterpret or resume an earlier execution.

## Exact fresh credential binding

The final window accepts only:

| Binding | Exact value |
|---|---|
| Secret ARN | `arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE` |
| Current version | `daacb67e-fcd1-41e1-bf62-47a3f18c8d0b` at `AWSCURRENT` |
| Prior version | `d09d567e-9bde-482a-b95a-3cab990a1006`, no stages |
| Older version | `f78c8aa8-2765-4788-9928-dd1ba7c406bf`, no stages |
| Bearer token SHA-256 | `77f2979e024c42e91db938fecdb6214359637b316ad5edf6bbf1008fe59a89ea` |
| Secret-value SHA-256 | `39bd665f417671bc57066271ecf012df81179326a7f07e3a1c8220953d78a41a` |
| Resource-policy SHA-256 | `318a323fe01349dca140c8eff48cfef9da1cda163b6cc7616d3da718c0d20cb1` |
| KMS-policy SHA-256 | `8a9c8064b7a66e8003e326b4ae02a1288c7d304fd471734146f70fbaacbd5dd4` |

The local token must be the exact 44-byte, LF-terminated, mode-`0600` file at
`/private/tmp/medzen-b6-6-client-token`. The binding gate hashes only the
43-byte bearer value. It never prints, persists or reads the value from
Secrets Manager. Any missing file, different hash, version-map drift, policy
drift or additional secret version refuses before capacity changes.

## Read-only starting-state preview

The post-restoration audit on 2026-08-10 found:

- CPU and GPU groups active at desired zero with zero instances;
- no B6 worker shutdown schedules;
- no `medzen-b6-window` ALB;
- no `medzen-b6-window-probe` ECS cluster;
- no temporary ECR API, ECR DKR or S3 endpoints and no endpoint SG;
- the three synthetic-secret Terraform addresses present and no other window
  address;
- production pointer `/medzen/registry/serving/current` absent; and
- the fresh credential and access boundaries exactly as above.

Execution must reproduce this state. The preview is not a substitute for the
runtime gates.

## Deadline-first execution and capacity

Before either node group can rise, stage 0 creates and verifies simultaneous
AWS-side scale-to-zero schedules for the exact CPU and GPU auto-scaling groups.
Only then may the runner request at most:

- two `m6i.large` CPU nodes;
- one `g6.xlarge` GPU node;
- one `0.25-vCPU / 0.5-GiB` Fargate task;
- one internal ALB; and
- the exact temporary private probe endpoints below.

Maximum cumulative integration-window allowance: `14,400 seconds`.
Charged through packet 2026-013: `4,819 seconds`.
Maximum final-window deadline: `9,581 seconds`.

The runner uses only
`platform/evidence/receipts/B6-2026-016-LIVE/`, refuses a pre-existing receipt
directory or dirty reviewed worktree, and preserves each receipt immediately.

## Exact guarded Terraform window

The fresh create plan must be exactly `12 add / 0 change / 0 destroy`:

1. one window-only load-balancer-controller Helm release;
2. two existing ALB/node ingress rules;
3. one temporary Fargate execution role and one inline policy;
4. one temporary ECS cluster and one task definition;
5. one endpoint-side security group;
6. one TCP/443 rule sourced only from probe SG
   `sg-0a83abae6ab954543`;
7. one ECR API interface endpoint;
8. one ECR DKR interface endpoint; and
9. one S3 gateway endpoint.

The already-present secret, resource policy and KMS policy must be no-op. Any
unknown, replacement, extra address or secret/IAM change outside the exact
temporary set refuses. Only the machine-guarded saved plan may be applied.

## Private endpoint and probe boundary

The Fargate task has no public IP and uses only the reviewed three private
subnets and probe SG. Both interface endpoints:

- use the exact three subnets;
- enable private DNS;
- use only endpoint SG `medzen-b6-probe-vpce`; and
- accept TCP/443 only from the exact probe SG.

The ECR API policy permits only `ecr:GetAuthorizationToken`; ECR DKR permits
only the three image-pull actions on `medzen-rag-index`; the S3 gateway policy
permits only `s3:GetObject` on the regional ECR starport layer bucket and only
the main route table `rtb-0c6eb6874ce0565dc`.

The runner waits at most 900 seconds for all three endpoints to report
`available` and for every SG, subnet, route-table, tag and policy binding to
read back exactly. It persists `endpoints_ready` before controller/model
deployment and repeats the same live gate immediately before `RunTask`.

## Digest-pinned images

The seven workload/driver execution identities are the scan-passed linux/amd64
child manifests below; tags and OCI indexes are not execution identities.

| Image | Child manifest digest |
|---|---|
| `medzen-rag-index` | `sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c` |
| `medzen-llm-gateway` | `sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a` |
| `medzen-speech-tts-gateway` | `sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d` |
| `medzen-model-loader` | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` |
| `medzen-asr-runtime` | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` |
| `medzen-orchestrator` | `sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa` |
| `medzen-nvidia-dra` | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` |

The separately qualified, temporary AWS Load Balancer Controller is pinned to
`sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f`.
No image build, push, tag resolution, waiver or scan exception is authorized.

## Deployment, functional proof and isolation

Deploy in this order after endpoint readiness:

`controller → DRA → RAG → ASR/model-loader → TTS → LLM → orchestrator`.

All services remain synthetic-only. ASR is zero-shot Whisper large-v3 `v0`,
LLM uses the fake provider, TTS is text-only, and the registry is the existing
content-addressed non-serving test snapshot. B5 remains `BLOCKED`.

The window must prove, with ordered receipts:

1. all exact digest-pinned workloads ready;
2. one internal HTTP/80 ALB and healthy orchestrator target;
3. exactly three non-default listener rules in priorities 1–3 for speech,
   stream and readiness paths;
4. one isolated Fargate `/readyz` success before ALB classification;
5. file transcription and cited text reply;
6. WebSocket streaming, final-result preservation and bounded cancellation;
7. authentication and malformed-request refusals;
8. RAG-unavailable controlled behavior without a 500 cascade; and
9. ClusterIP-only dependencies and no public load balancer.

Only the four narrowly documented post-create listener/rule tag-mutation
denials may be a receipted non-fatal warning, and only after functional ALB and
Fargate PASS. Creation, routing, health, endpoint, cleanup and all unknown
denials remain fatal.

## Receipt ordering and cleanup

Required receipt order:

`local_bindings → deadline → workers_ready → terraform_window → endpoints_ready → controller_ready → dra_ready → rag_ready → asr_ready → tts_ready → llm_ready → orchestrator_ready → fargate_probe → alb_ready → alb_tag_mutation_warning → file_proof → websocket_proof → cancellation_proof → failure_drills → isolation_proof → cleanup`.

Missing, malformed, duplicate or out-of-order receipts refuse. A later failure
never erases an earlier success.

Automatic cleanup must:

1. stop the probe task;
2. remove Ingress and prove the ALB absent;
3. remove workloads and DRA;
4. apply a fresh plan with exactly `0 add / 0 change / 15 destroy`, or
   only a guarded non-empty subset after partial creation;
5. prove all three endpoints and their SG absent, with `deleting` counted as
   present;
6. prove both worker groups active at desired zero with zero instances;
7. disarm deadlines only after zero; and
8. remove the local token and persist cleanup.

The 15 deletes are the 12 temporary window resources plus the three synthetic
secret/access-boundary resources. Force deletion without recovery is forbidden.
Cleanup failure is fatal and leaves deadlines armed.

## Cost boundary

- Aggregate project ceiling: `$300`.
- Cost registry: `COST-REGISTRY-2026-004`.
- Allocation: `B6-INTEGRATION-WINDOW-2026-001`.
- Existing active reservation: `$10`.
- New reservation: `$0`.
- Maximum authorized window allocation: `$10` total.

The temporary endpoint and compute charges must remain within the existing
reservation. Billing is reconciled after zero-state closure; the reservation
is not evidence of actual spend.

## Exact source bindings

<!-- SOURCE_BINDINGS_START -->
| Source | SHA-256 |
|---|---|
| `infra/alb_controller.tf` | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| `infra/b6_6_window_override.tf` | `c9dc7ebfd17b4ea0e9bf9b50fee7af529405ab44ee4e08827d3a5bf06ef39962` |
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `infra/b6_integration_window.tf` | `df4fec719aa8a709e94d89040c1bc283d9f847aa894a803fb00bb93e99f3c144` |
| `infra/b6_planning_override.tf` | `296ccddd66a108f3667273f0b2683c62d48882340fe2585bf1069ae47b32d2bf` |
| `infra/eks.tf` | `37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b` |
| `infra/variables.tf` | `59c1226f9a797e13756575ef77b45ce9324e1f1fb4743bc7d84fa8bec4f272dd` |
| `pipeline/b6_integration_receipts.py` | `a5eb39b8b022021db63bb115ff905f5b229e96cd48b6d56da6919d952b19664e` |
| `platform/decisions/B6-AWS-AUTH-2026-015-synthetic-credential-restoration.json` | `223a09ecba79ff487ba0d3f40efdc27b9bad5c71f066fc84a0cd45bcc3f8b117` |
| `platform/decisions/B6-AWS-CHANGE-PACKET-2026-014-b6-6-private-probe-successor.md` | `f31cb8f36d76d32884639bbe8bfb750ca807a92847d24f0abf4e1eef7d8c6428` |
| `platform/decisions/B6-AWS-CHANGE-PACKET-2026-015-synthetic-credential-restoration.md` | `48d809c9dff33b61139bc160f29bd134260e2d667f4a56c5df437d8882648f6d` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-001.json` | `a77d229f97939d74d5a161a6c1bb7a0a2514a1870fd0e1b63d20445ec425e16c` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json` | `831c164a6ca75017a3f9d11e38550cc52c7785b3abcb65f1963d82378995e244` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-6-LOCAL-CORRECTION-2026-001.json` | `00882ca9d28a867a46b9269144cf6567672415781b09cfc84153020479cdf881` |
| `platform/evidence/B6-6-LOCAL-CORRECTION-2026-002.json` | `2fc6bd357dcf6c111c0d6614a8f930a4f2cecb04915b141f152f60dc2c0b870b` |
| `platform/evidence/B6-CLIENT-API-KEYS-2026-001.json` | `6120c7a9b82dd51a2ceccd504156c8448c0322c5ba31e65334505caf3856c2e0` |
| `platform/evidence/B6-CLIENT-SECRET-RESTORATION-AWS-EXECUTION-2026-002.json` | `3d221399287dc55c3ae2d72d1a5e381680dc5263d21451c8434ccc21f95becb3` |
| `platform/evidence/B6-CLIENT-SECRET-RESTORATION-CONTINUATION-AWS-EXECUTION-2026-001.json` | `1d949f019ce0b2e69f1fba525d535d61fc19ed07e99f08d11729c1c099784c89` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-LBC-IAM-LIFECYCLE-AWS-EXECUTION-2026-001.json` | `da38f29ec5cd218620e2c649a19500b24db04b7ecd0b55a873b61bb1fce09236` |
| `platform/evidence/B6-LBC-QUALIFICATION-AWS-EXECUTION-2026-001.json` | `56265113cbfa3ebec85309ec9966dc5fb7a2dd28e1c5fad0b1a4dd6e946cb8f3` |
| `platform/evidence/B6-PACKET-2026-008-REFUSED-WORKER-REGISTRATION.json` | `f2b8acbabafb2642e5b70ddbae966930f2ba62201c7a2fb26f6e32bc3246d432` |
| `platform/evidence/B6-PACKET-2026-009-REFUSED-TOKEN-ENCODING.json` | `3295768ed6d326125f4c5098908a0b6e090c800a93b35c199ecadf0a574d8a49` |
| `platform/evidence/B6-PACKET-2026-010-REFUSED-ALB-LISTENER-IAM.json` | `4ea2234f6803049d6d4afd4a24a2f03f118c1c45c090b173f61cfef8506fdabf` |
| `platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json` | `daa679e744738094059a6faae53e5ebb7d44dd920c4da1ba5bac71100049590a` |
| `platform/evidence/B6A-PACKET-2026-003C-A-SCAN-RESULT.json` | `1b1ed84205fe9a71c3b21b2a2658814855fd5fdcf6af00c5590bb4205e8dc70b` |
| `platform/evidence/receipts/B6-2026-015-LIVE/preflight.json` | `47b17f2337df32ead13d2ec5970f900296a2c50b93eb1ce9dd38713361c92f83` |
| `platform/evidence/receipts/B6-2026-015-LIVE/restore.json` | `9d90280dcbfe8190ce146e2825e0178b10241138f5511a6ca90a64636efada36` |
| `platform/evidence/receipts/B6-2026-015-LIVE/rotation.json` | `9c605f0e9e7597028470ee8c3f14ee4286d4d5d18f896df4b74ff9e045166fe2` |
| `platform/evidence/receipts/B6-2026-015-LIVE/terraform_import.json` | `68a1ae99ab22e29d06b1da7a5c15e80da986eca0adf6e4f4a3ca848e63d33d37` |
| `platform/evidence/receipts/B6-2026-015-LIVE/terraform_normalization.json` | `675140c96c9d603c87b6befe76d8eedc3d3d5ef25af6bb72ca42dc8e8f791126` |
| `platform/evidence/receipts/B6-2026-015-LIVE/terraform_reconciliation.json` | `7db9ee560648f75c09a4e1cda15de758e72ff7c2a390e4b20fc09560f4aefa25` |
| `platform/evidence/receipts/B6-2026-015-LIVE/verification.json` | `2e66718a532314f72bf85b1cabe8e126adacf9452eec8c352a5de0f288fb3036` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `86a3b4e1b2d1d91c9b5135e1f4476cc9ac539ff0935a7d71e34b82948f853b25` |
| `scripts/b6_6_cleanup.sh` | `a1774857645a75f87efea7fc17db3b141688c0c48de0d2c945c73aa2bd78a7e0` |
| `scripts/b6_6_deadline.py` | `250144e007aaf8a42906d5e563032107898bc4f9be7d08571f9a499f87446949` |
| `scripts/b6_6_fargate_probe.py` | `f869f694d482ed18872e7adbdfcffa5aa6401d64af26aebee62a7bd9f83a72b4` |
| `scripts/b6_6_lbc_runtime.py` | `3436e804bdfcd3034f1abeaf1ea9f1e82520b3cdbe1dda07851d54ab83848656` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `295b69276ac3036bd4c65275b07406b1f6f68101d0e7e2e5dfcbf5bce015126b` |
| `scripts/b6_6_receipt.py` | `3629ffc8b3b6c34ec1d3cfdcde5c8aa28a73796806826f8dce5084a8995c7280` |
| `scripts/b6_6_secret_preflight.py` | `075a5547b32fcbd131be78edb9ee071b4326320a6585487e6b03daf8e32d75ef` |
| `scripts/b6_6_token_binding.py` | `a42290d7bc719abab414105af3f5813fce717bc0d76af3753aafca2745daffff` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_window_plan.py` | `1535d6f5fbd086f528de0b9ed652dd064a311545e68d044a1217b008dc9a2f7b` |
| `scripts/pin_aws_lbc_digest.py` | `bcd3b3e97e992f96457df392f76aa9da8d91f801a0610cc5533660c6400ce5cb` |
| `scripts/run_b6_6_integration_window.sh` | `7a71104594bb7c421f7806d846911634e320af4c0376bdcb1dc212c2490e46d0` |
| `scripts/run_b6_client_secret_restoration.py` | `baa58777cd05a3edad5f5236013ca5e3556dd654026790a6f2599022981422cc` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_attempt_4_runtime.py` | `0a3fd06c71d200a49c0ac14a243d74d428c5d5988611e121aeabe4fd77f36ccd` |
| `tests/test_b6_6_executable_assets.py` | `a57ad2c7ffb547cd9bccc7e8ee1622f916dccd22ac1a6853ecaa362b5b23d40a` |
| `tests/test_b6_6_final_window_successor.py` | `53ad821bf447770fdedb321235977375654b024517f2c27d5d851224a0fb316c` |
| `tests/test_b6_6_private_probe_successor.py` | `5175b54df83e08083b33966e73be2100f534e64c46332cc862eac05ebf9993fc` |
| `tests/test_b6_lbc_tag_warning.py` | `60de28ddd7a056f50c2a8afae6bc16b0473c7fde19e3413f51a7732c387fce58` |
<!-- SOURCE_BINDINGS_END -->

Any missing source, changed hash, stale credential, unknown state, plan delta,
late endpoint, absent deadline, malformed receipt or incomplete cleanup refuses.

## Explicit prohibitions

No public ALB, public Fargate IP, NAT, permanent endpoint, production DNS/TLS
or traffic, production SSM write, approved artifact, model registration, MLflow
transition, approved-version or language-scope change, real Bedrock/Fish call,
training, green-bucket mutation, PHI, image change, scan waiver, standing IAM
broadening, cost/time extension, secret plaintext read, historical-token reuse,
force secret deletion or deadline disarm before zero is authorized.

## Approval boundary

After independent review bound to the final packet SHA-256 and prepared commit,
the only valid owner phrase is:

`Approve B6 AWS change packet 2026-016 only.`

Until that review and a matching `B6-AWS-AUTH-2026-016` record exist, do not
execute this packet.
