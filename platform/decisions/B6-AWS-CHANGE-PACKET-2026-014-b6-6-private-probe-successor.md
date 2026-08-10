# B6 AWS change packet 2026-014 — private-probe successor window

Status: **DRAFT TECHNICAL CORRECTION COMPLETE — EXECUTION BLOCKED ON FRESH SYNTHETIC CREDENTIAL EVIDENCE, INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-10  
Account/region: `558069890522` / `eu-central-1`  
Required profile/operator: `medzen` / `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested at this stage

Review the local successor design that corrects all three findings from packet
2026-013:

1. provide the private ECR image-pull path required by the no-public-IP
   Fargate probe;
2. wait for that path to become available before any probe task is launched;
   and
3. verify the exact three ALB path rules produced by the reviewed Ingress.

This draft authorizes no AWS, Kubernetes or Terraform action. It is deliberately
not executable yet: packet-2026-013 cleanup removed the local synthetic token,
scheduled recoverable deletion of the test secret and removed its access
policies. A separate, reviewed credential-restoration execution must produce a
fresh secret version, fresh token hash and immutable verification evidence.
Those exact values must replace the historical attempt-4 credential bindings
before this packet can receive final independent review or owner execution
approval. Reusing the removed token, reading its plaintext from Secrets Manager
or silently relying on the pending-deletion secret is prohibited.

No `B6-AWS-AUTH-2026-014` record exists or may be created from this draft.

## Trigger and immutable history

PR #23 is merged on published `master` at
`1d4431a396aa53ef63bcc838c958e7d5f847b8f0`. Packet 2026-013 is closed by:

- `platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json`;
- SHA-256
  `daa679e744738094059a6faae53e5ebb7d44dd920c4da1ba5bac71100049590a`;
- outcome `INCOMPLETE_FARGATE_IMAGE_PULL_NETWORK_REFUSAL`; and
- automatic cleanup `PASS`, with CPU/GPU zero, ALB absent, window Terraform
  resources absent, local token removed and the synthetic secret scheduled for
  recoverable deletion.

That packet, authorization, receipt set and refusal record remain byte-for-byte
unchanged. This packet supersedes packet 2026-013 by reference only and cannot
resume it.

## Correction 1 — exact temporary private image-pull path

The probe remains one Fargate task with `assignPublicIp=DISABLED`, the exact
three reviewed subnets and security group `sg-0a83abae6ab954543`. No NAT,
public IP or general Internet egress is introduced.

The fresh machine-guarded window plan must contain exactly five additional
window-only resources:

1. one endpoint-side security group named `medzen-b6-probe-vpce`;
2. one TCP/443 ingress rule whose only source is the exact Fargate probe
   security group `sg-0a83abae6ab954543`;
3. one ECR API interface endpoint in the exact three subnets, private DNS on;
4. one ECR DKR interface endpoint in the exact three subnets, private DNS on;
   and
5. one S3 gateway endpoint attached only to VPC main route table
   `rtb-0c6eb6874ce0565dc` for ECR layer delivery.

The endpoint security group has no CIDR ingress, IPv6 ingress, prefix-list
ingress or egress rules. Both interface endpoints use only that endpoint-side
security group. Their policies allow only the temporary probe execution role:

- ECR API: `ecr:GetAuthorizationToken`;
- ECR DKR: `ecr:BatchCheckLayerAvailability`, `ecr:BatchGetImage` and
  `ecr:GetDownloadUrlForLayer`, restricted to repository
  `medzen-rag-index`; and
- S3 gateway: the AWS-required gateway-endpoint `Principal: "*"`, restricted
  to only `s3:GetObject` on the regional ECR starport layer bucket. Gateway
  endpoint policies do not accept a role ARN in `Principal`; the resource and
  action are the AWS-documented minimum ECR layer-download boundary.

This follows the official Amazon ECR private-endpoint guidance and its minimum
S3 layer-bucket example:
`https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html`.

Any extra endpoint, endpoint policy statement, principal, action, resource,
subnet, route table, security-group rule or public network path refuses the
window. The endpoints are not standing infrastructure: all three and the
endpoint-side security group are destroyed during automatic cleanup.

## Correction 2 — availability gate before Fargate

After applying the exact saved plan, the runner waits at most 900 seconds for:

- all three exact endpoints to exist and report `available`;
- both interface endpoints to report private DNS enabled, the exact three
  subnets and only the exact endpoint security group;
- the S3 gateway endpoint to report only the exact route table;
- exact endpoint tags and policies to read back; and
- endpoint SG ingress to read as TCP/443 from only the exact probe SG.

A pending state may be polled every 15 seconds. A mismatch fails immediately;
timeout persists a refusal and enters cleanup. The runner persists an
`endpoints_ready` PASS receipt before it installs the controller or deploys a
model. Immediately before `RunTask`, the isolated Fargate probe re-verifies the
same live endpoint boundary. It cannot launch early.

The probe persists its own `fargate_probe` PASS or REFUSED receipt before the
ALB verifier. A refusal stores only normalized, PHI-free reason codes and a
hash of the task ARN; raw AWS stop text is not persisted.

## Correction 3 — exact three-route ALB proof

The reviewed Ingress deterministically produces three non-default listener
rules targeting the single healthy orchestrator target group:

| Priority | Exact prefix expansion |
|---:|---|
| 1 | `/v1/conversations/speech`, `/v1/conversations/speech/*` |
| 2 | `/v1/conversations/stream`, `/v1/conversations/stream/*` |
| 3 | `/readyz`, `/readyz/*` |

The successor verifier now requires one internal active application load
balancer, one HTTP/80 listener, one default rule, exactly the three rules above,
one healthy target group, the exact forwarding action and required
creation-time tags on the ALB, listener and all three rules. Missing, extra,
duplicate or reordered priorities, changed paths or a different target refuse.

The owner-directed tag-mutation exception is narrowed by the new immutable
decision draft
`platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json`. Only an
exact post-create `AddTags` or `RemoveTags` denial on the one live listener or
one of the three exact live rule ARNs may be a receipted non-fatal warning, and
only after both `fargate_probe` and `alb_ready` are PASS. Creation, functional
or cleanup denial remains fatal. The historical revision 001 is unchanged.

## Exact plan and cleanup guards

After credentials are separately restored and bound, the executable window
must generate and apply one fresh saved Terraform plan with exactly `12 add / 0 change / 0 destroy`:

- the seven previously reviewed window resources;
- one endpoint SG, one probe-to-endpoint SG rule and three endpoints; and
- no secret or standing IAM mutation in the create plan.

Following a fully applied window, cleanup must generate a fresh plan with
exactly `0 add / 0 change / 15 destroy`: the 12 window resources plus the same
three synthetic-secret resources that the prerequisite restoration recreates.
On a pre-apply or partially applied refusal, the subset cleanup guard permits
only a non-empty subset of those exact 15 deletes. No update, replacement,
unknown address or empty-success cleanup is accepted.

Cleanup order is binding:

1. stop the single probe task if it exists;
2. delete the Ingress and wait for the ALB to be absent;
3. remove synthetic workloads and DRA;
4. destroy the exact Terraform window and secret subset;
5. prove both ECR interface endpoints, the S3 gateway endpoint and endpoint SG
   absent, treating `deleting` as still present;
6. scale GPU and CPU groups to zero and wait for both groups to be active at
   zero;
7. disarm both AWS-side deadlines only after that zero proof; and
8. remove the local token and persist the cleanup receipt.

Endpoint deletion or security-group deletion failure is fatal and leaves the
deadlines armed.

## Receipt sequence and fail-closed states

The write-once receipt chain is:

`local_bindings → deadline → workers_ready → terraform_window → endpoints_ready → controller_ready → dra_ready → rag_ready → asr_ready → tts_ready → llm_ready → orchestrator_ready → fargate_probe → alb_ready → alb_tag_mutation_warning → synthetic proofs → isolation_proof → cleanup`

Unknown, missing, malformed or out-of-order evidence refuses. A later failure
never erases an earlier receipt. The Fargate receipt precedes the ALB receipt;
therefore an image-pull or readiness failure cannot be misreported as ALB
success.

## Images and synthetic-only deployment boundary

The six service/init images, DRA driver and controller remain pinned to the
same scan-passed linux/amd64 child manifests used by packet 2026-013. No tag or
OCI index is an execution identity. This packet adds no image build, push,
scan waiver or provider call.

The window remains synthetic-only and non-serving:

- zero-shot Whisper large-v3 is still `v0`, not an approved fine-tune;
- the LLM provider is fake and TTS is text-only;
- the registry path is the existing content-addressed non-serving test path;
- `/medzen/registry/serving/current` must remain absent;
- all dependency services remain `ClusterIP`;
- the only Ingress is the temporary internal orchestrator ALB; and
- B5 promotion remains `BLOCKED`, with no `approved/asr/`, model registration,
  approved-version, production SSM or language-scope change.

## Time and cost continuity

- Maximum cumulative integration-window allowance: `14,400 seconds`.
- Charged through packet 2026-013: `4,819 seconds`.
- Maximum successor deadline: `9,581 seconds`.
- Existing reservation: `$10` in `COST-REGISTRY-2026-004` allocation
  `B6-INTEGRATION-WINDOW-2026-001`.
- New reservation: `$0`.
- Maximum capacity: two `m6i.large` CPU nodes, one `g6.xlarge` GPU node, one
  `0.25-vCPU / 0.5-GiB` Fargate task, one internal ALB, two interface endpoints
  across the exact three subnets and one S3 gateway endpoint.

The endpoint resources are torn down within the same bounded window. The
reservation is a ceiling, not a claim of actual billed spend. Attributable
billing remains to be reconciled after execution.

## Local verification completed

The successor tests cover:

- exact endpoint services, subnets, route table, policies and SG-only ingress;
- rejection of CIDR, source-SG, DNS, policy and endpoint-set drift;
- pending-to-available wait behavior and the 900-second bound;
- no-public-IP Fargate launch after live endpoint recheck;
- sanitized Fargate refusal evidence;
- exact three-route priorities, paths, target and tag set;
- receipt ordering;
- exact 12-create and 15-destroy guards;
- endpoint absence before deadline disarm; and
- unchanged packet-2026-013, authorization and refusal hashes.

Canonical suite results and exact source hashes will be recorded in the local
preparation evidence before this draft is presented for review.

## Exact source bindings

The locally prepared correction binds the sources below. A future executable
revision must additionally bind the fresh credential-restoration packet,
execution evidence, secret version and token hash without weakening this set.

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
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-001.json` | `a77d229f97939d74d5a161a6c1bb7a0a2514a1870fd0e1b63d20445ec425e16c` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json` | `831c164a6ca75017a3f9d11e38550cc52c7785b3abcb65f1963d82378995e244` |
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
| `platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json` | `daa679e744738094059a6faae53e5ebb7d44dd920c4da1ba5bac71100049590a` |
| `platform/evidence/B6A-PACKET-2026-003C-A-SCAN-RESULT.json` | `1b1ed84205fe9a71c3b21b2a2658814855fd5fdcf6af00c5590bb4205e8dc70b` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `0bc9b3ba70b244c606dcf1eb816d8b40114f7c6e82f184e0950f5d02cf630972` |
| `scripts/b6_6_cleanup.sh` | `a64e72554d3ee22cd87a53a00800626a1fca08772f087e3b0e68b55caf97577d` |
| `scripts/b6_6_deadline.py` | `250144e007aaf8a42906d5e563032107898bc4f9be7d08571f9a499f87446949` |
| `scripts/b6_6_fargate_probe.py` | `f869f694d482ed18872e7adbdfcffa5aa6401d64af26aebee62a7bd9f83a72b4` |
| `scripts/b6_6_lbc_runtime.py` | `3436e804bdfcd3034f1abeaf1ea9f1e82520b3cdbe1dda07851d54ab83848656` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `295b69276ac3036bd4c65275b07406b1f6f68101d0e7e2e5dfcbf5bce015126b` |
| `scripts/b6_6_receipt.py` | `3629ffc8b3b6c34ec1d3cfdcde5c8aa28a73796806826f8dce5084a8995c7280` |
| `scripts/b6_6_secret_preflight.py` | `ce5c011ab2774d52db2dc9fc60ab7df11df92abd88ac817ef76ae59b7b1f140f` |
| `scripts/b6_6_token_binding.py` | `55e920fd6c717340cc4028bf7a1d37f941f8ae1da6449bea3640ba0503f8f0df` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_window_plan.py` | `1535d6f5fbd086f528de0b9ed652dd064a311545e68d044a1217b008dc9a2f7b` |
| `scripts/pin_aws_lbc_digest.py` | `bcd3b3e97e992f96457df392f76aa9da8d91f801a0610cc5533660c6400ce5cb` |
| `scripts/run_b6_6_integration_window.sh` | `04112cdb9fee330b0e3f3799042b02ab7a084a094c3f6d1bd1a0aa42da9f8087` |
| `scripts/run_b6_client_secret_restoration.py` | `baa58777cd05a3edad5f5236013ca5e3556dd654026790a6f2599022981422cc` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_attempt_4_runtime.py` | `525b35d0bbe9cbfdde0857b75e65cf8eb4037244b3e4e29a8a9b0066dd7c7b22` |
| `tests/test_b6_6_executable_assets.py` | `d4f1d7d322077b53c7e83f690273ee34613ed43f29b6c5580f3dbd55f47ec447` |
| `tests/test_b6_6_private_probe_successor.py` | `8a53a3b27efbd216e703f91f0ff3e9ceaaf129d98822a8ffba83936a5f4a2cc3` |
| `tests/test_b6_lbc_tag_warning.py` | `60de28ddd7a056f50c2a8afae6bc16b0473c7fde19e3413f51a7732c387fce58` |
<!-- SOURCE_BINDINGS_END -->

Any missing source, changed hash, unsafe path, stale credential, unknown state,
plan delta or incomplete evidence refuses before the next stage.

## Explicit prohibitions

This draft does not authorize AWS or Kubernetes execution. No public ALB, NAT,
public Fargate IP, permanent VPC endpoint, production DNS/TLS/traffic,
production SSM write, approved artifact, model registration, MLflow transition,
language approval, real Bedrock/Fish call, training, green-bucket mutation, PHI,
image change, scan waiver, IAM broadening, cost/time extension, force secret
deletion, deadline disarm before zero, historical-token reuse or secret
plaintext read is permitted.

## Finalization boundary

Before execution approval can be requested:

1. independently review and owner-approve a narrow synthetic credential
   restoration;
2. execute it at CPU/GPU zero and publish immutable evidence containing only
   the new version ID and hashes, never plaintext;
3. update this packet's exact credential and evidence bindings;
4. rerun the canonical suite and deterministic source hashing;
5. obtain an independent review bound to the final packet SHA-256 and prepared
   commit; and
6. create a new owner authorization record bound to that final review.

Until all six are true, **do not approve or execute packet 2026-014**.
