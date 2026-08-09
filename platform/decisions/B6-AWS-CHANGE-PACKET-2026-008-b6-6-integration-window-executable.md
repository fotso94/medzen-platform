# B6 AWS change packet 2026-008 — executable B6.6 synthetic integration window

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: `2026-08-09`

## Purpose and outcome boundary

Run one four-hour-maximum, synthetic-only integration rehearsal on the existing
EKS cluster. Install the already qualified internal-ALB controller, deploy the
five services plus the B6A model-loader and NVIDIA DRA driver by scan-passed
Linux/AMD64 child digest, prove file and WebSocket speech flows, exercise
bounded refusal paths, then remove every window resource and return CPU and GPU
workers to zero.

This packet is the executable successor to the immutable blocked revision 2,
`platform/decisions/B6-AWS-CHANGE-PACKET-2026-004-b6-6-integration-window-revision-2.md`,
SHA-256 `f9c6c6030cb0cd120dd6dbd7f3eb8dd93eb331791d812d58be865ab3fe513975`.
Its three blockers are now closed by independently reviewed evidence:

- ALB controller qualification: `VERIFIED_COMPLETE`, SHA-256
  `56265113cbfa3ebec85309ec9966dc5fb7a2dd28e1c5fad0b1a4dd6e946cb8f3`;
- synthetic client-key boundary: `VERIFIED_COMPLETE_WITH_LOCAL_RECEIPT_RECOVERY`,
  SHA-256 `6120c7a9b82dd51a2ceccd504156c8448c0322c5ba31e65334505caf3856c2e0`;
- deployed-classification registry publication: `VERIFIED_COMPLETE`, SHA-256
  `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595`.

This is a platform proof, not model promotion. Zero-shot Whisper large-v3 is
identified as non-serving `v0`; B5 remains `BLOCKED`; deferred languages keep
`approved_version: null`; and this packet makes no quality claim.

## Immutable preparation and cost bindings

| Binding | Value |
|---|---|
| Prepared source commit | `76e052ddb671e52125128c4892a20e54d1a60fc9` |
| Prepared source tree | `18c1b41bb8eaa76cd414bee8a39badbdb3962c1b` |
| Speech contract SHA-256 | `e544141a7ad894ac0b5d411c7d8a3b64767de40ca63de4b96afc579f6a244d0d` |
| B6A closure SHA-256 | `11a3c55f592387086556e85e882db6492b588cd0a5ee1be574566b707114ea51` |
| B6.5B scan evidence SHA-256 | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| 007A registry root | `/medzen/registry/test/b6/d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81` |
| 007A parameter versions | `index=1`, `routes/english=1`, `_manifest=1` |
| Cost registry | `COST-REGISTRY-2026-004`, SHA-256 `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| Active allocation | `B6-INTEGRATION-WINDOW-2026-001` — exactly `$10.00` |
| Guardrail | `$63.5288` committed + `$10` reserved = `$73.5288 / $300` |
| Headroom after reservation | `$226.4712` |

The reservation is not AWS authorization and is not actual spend. It is the
single active billable reservation. The live `BudgetRegistry` tag remains
`COST-REGISTRY-2026-003`, the already deployed immutable allocation namespace;
registry revision 004 changes accounting state without relabeling retained
historical resources.

## Exact deployable image table

A tag, OCI index, rebuilt image, different architecture or digest substitution
refuses the window. All eight child manifests completed live ECR scanning with
zero critical and zero high findings; no waiver is permitted.

| Component | Exact Linux/AMD64 child digest |
|---|---|
| AWS Load Balancer Controller 3.5.0 | `sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f` |
| NVIDIA DRA | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` |
| Model loader | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` |
| ASR runtime | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` |
| RAG index | `sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c` |
| LLM gateway | `sha256:88026dd9708073dcd3622e7dd68e7a70aff98cddd43129c53c017d571f533f5a` |
| TTS gateway | `sha256:88e83b97a03c593505435981c554d5d0f3045c4acb4a7224148d58e3af96087d` |
| Speech orchestrator | `sha256:fa2cccdf9891c080fcc1eb408a325e8afbd623e4f89469ea228ddf166dad62aa` |

The runner verifies each deployed pod's runtime `imageID`. Manifest pinning
alone is insufficient.

## Exact Terraform delta and IAM review boundary

The prepared targeted preview plan is SHA-256
`4a67b2da959e8ad25ca6e459eeee05fb00b8ac9932d8270dcc57f371ad9e10a1`;
its canonical Terraform JSON is SHA-256
`ba2560ed1a4618ba60fd9ebec601a6a685cff890d43137194c9e456e11804528`.
It passed `PASS_B6_6_CREATE` with exactly `7 add / 0 change / 0 destroy`:

1. `helm_release.b6_load_balancer_controller[0]`;
2. ALB ingress TCP/80 from exact backend SG `sg-0a83abae6ab954543`;
3. node ingress TCP/8080 from exact ALB SG `sg-0f0f6c66852830013`;
4. temporary ECS probe cluster;
5. temporary Fargate task definition;
6. one-hour `medzen-b6-window-probe-execution` role; and
7. its inline ECR pull policy.

Because Terraform state can advance, the preview is review evidence, not an
apply artifact. Execution regenerates a fresh targeted plan and applies it only
if the same guard returns the exact seven-resource delta.

The new role is the only IAM role created. Its trust is only
`ecs-tasks.amazonaws.com`; it has no task role and therefore no application AWS
permissions. The execution policy permits `ecr:GetAuthorizationToken` plus
layer and manifest reads for only `medzen-rag-index`. It has no SSM, Secrets
Manager, KMS, S3, logging, Bedrock, network-mutation or write permission. An
independent IAM review of this exact packet and source commit is mandatory
before owner approval.

## Deadline-first execution order

Every receipt is write-once, fsync-persisted immediately, contains no audio,
transcript, reply, citation text, credential or PHI, and cannot be voided by a
later failure.

0. Validate the owner authorization, independent review, all source hashes,
   account, token mode, live three-parameter registry, secret metadata and
   absent production pointer.
1. While both worker groups are still zero, arm and read back two AWS-side
   scheduled actions with the same deadline: CPU `min=0/desired=0/max=4` and GPU
   `0/0/1`. Any partial arm is rolled back before refusal.
2. Scale to at most two `m6i.large` CPU nodes and one `g6.xlarge` GPU node; prove
   all three Ready.
3. Generate, guard and apply the exact Terraform window plan. Prove the
   controller is Ready and running its scan-passed child digest.
4. Apply the retained NVIDIA DRA render and prove its exact digest and Ready
   DaemonSet.
5. Apply the 23-object B6.6 bundle with all five Deployments initially at zero.
   Raise and prove Ready in order: RAG → ASR/model-loader → text-only TTS → fake
   LLM → orchestrator.
6. Wait for the single internal ALB. Require `scheme=internal`,
   `type=application` and only SG `sg-0f0f6c66852830013`.
7. Run at most one 0.25-vCPU/0.5-GiB Fargate probe using the exact backend SG.
   It receives only the internal `/readyz` URL through a runtime override and
   must exit zero. No bearer token or request content enters ECS configuration.
8. Through a local `kubectl port-forward`, run authenticated synthetic file,
   WebSocket, cancellation/barge-in and controlled-refusal proofs.
9. Run one live RAG-unavailable drill: scale RAG to zero, require controlled 503
   `DEPENDENCY_UNAVAILABLE` and no 500 cascade, then restore Ready. Real Bedrock
   and Fish stay disabled; their timeout/breaker paths remain bound to the
   already passing pinned local suites rather than making provider calls.
10. Prove all dependencies are `ClusterIP`, the orchestrator is the only
    Ingress backend, source and target security-group IDs are exact, and no
    public load balancer exists.

The synthetic WAV is
`platform/testdata/orchestrator/synthetic-file-request.wav`, SHA-256
`97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b`.
The successful file result must have three synthetic citations,
`tts_backend=text_only`, ASR `v0`, fake LLM identity, null TTS model and exact
registry identity. Streaming must preserve final events, queue limits `4/8`
and cancellation within `250 ms`.

## Network and service boundary

- VPC: `vpc-051aa9df8b64bf141`.
- Internal ALB security group: `sg-0f0f6c66852830013`.
- Exact MedZen backend source group: `sg-0a83abae6ab954543`.
- EKS node group security group: `sg-070fc00321934eacb`.
- ALB listener: HTTP/80, synthetic window only; no public listener, DNS alias or
  production traffic. Production use requires a future TLS decision.
- Only `speech-orchestrator:8080` is an ALB target. ASR, RAG, LLM and TTS have
  no Ingress, `NodePort`, external IP or load balancer.
- NetworkPolicies default-deny ingress and permit only orchestrator-to-service
  dependencies. The source-SG and ALB-SG rules are Terraform-owned and
  window-scoped; the controller has no EC2 security-group write permission.

## Provider, model and data constraints

- ASR uses only the B6A zero-shot Whisper large-v3 `v0` manifest, artifact tree
  `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e`.
- LLM provider is `fake`; Bedrock calls and token spend are zero.
- TTS provider is `text_only`; Fish calls are zero.
- RAG uses only the embedded synthetic, non-clinical index.
- No real client key, voice, PHI, production or clinical content is permitted.
- Green-bucket data, B4 evidence and all language artifacts remain untouched.

## Duration and cost ceiling

- Maximum wall-clock duration: `14,400 seconds` / four hours, including cleanup.
- Maximum capacity: two `m6i.large`, one `g6.xlarge`, one short-lived
  `0.25 vCPU / 0.5 GiB` Fargate task and one internal ALB.
- Existing compute estimate: `$1.2364/hour`; four-hour worker estimate
  `$4.9456`. ALB, Fargate, logs and API charges are included in the conservative
  `$10.00` all-in ceiling, not claimed zero.
- No execution may start if any other reservation becomes active or recognized
  committed plus reserved exceeds `$300`.

## Cleanup and zero-state proof

The `EXIT`, interrupt and termination trap invokes the same independently
executable cleanup script. The AWS-side deadlines remain armed until cleanup
proves both groups zero. Cleanup order is binding:

1. stop any Fargate probe;
2. delete Ingress and wait until ALB/listener/target group are absent;
3. scale all application Deployments to zero and delete the 22 namespaced
   workload objects while retaining the pre-existing `medzen` namespace;
4. delete the DRA render;
5. generate a delete-only Terraform plan, uninstall the controller, remove the
   two SG rules, ECS objects and temporary role/policy;
6. remove the three packet-006 synthetic-secret Terraform resources, scheduling
   the secret's configured seven-day recoverable deletion—never force-delete;
7. set CPU and GPU managed node groups to zero, prove their Auto Scaling groups
   contain no instances, then remove both scheduled actions;
8. remove exactly `/private/tmp/medzen-b6-6-client-token` and prove it absent;
9. prove zero B6.6 pods/Ingress, production serving pointer absent, and zero
   changes to `approved/asr/`, registered models, approved versions and B5.

An early failure may yield a strict delete-only subset of the ten normal
Terraform cleanup addresses; the cleanup guard accepts no create, update or
unknown address. A successful full run expects seven window deletes plus the
three synthetic-secret deletes. Cleanup failure keeps the AWS deadlines armed
and the packet outcome incomplete.

The immutable 007A three-parameter test snapshot remains non-serving evidence.
It is not deleted and no `/medzen/registry/serving/current` pointer is created.

## Complete source-hash binding required in authorization

The owner authorization must contain exactly the following source map; omission
or mismatch refuses before any AWS mutation:

| Source | SHA-256 |
|---|---|
| `infra/alb_controller.tf` | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| `infra/b6_client_secret.tf` | `b978839377007b0bfe992bdf216aa101c45349fceb5a3eb43bbce067197a1afa` |
| `infra/b6_integration_window.tf` | `73ec7282cf4f5b8e7dfbb3081ebdd689424d96260bb03564983f73a9b33ca205` |
| `infra/variables.tf` | `59c1226f9a797e13756575ef77b45ce9324e1f1fb4743bc7d84fa8bec4f272dd` |
| `pipeline/b6_integration_receipts.py` | `0f3b8d3fd9e79a04c4ad44ed1882bb3f6b97fc0b7e0b12ca082c167729d0ff15` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-CLIENT-API-KEYS-2026-001.json` | `6120c7a9b82dd51a2ceccd504156c8448c0322c5ba31e65334505caf3856c2e0` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-LBC-QUALIFICATION-AWS-EXECUTION-2026-001.json` | `56265113cbfa3ebec85309ec9966dc5fb7a2dd28e1c5fad0b1a4dd6e946cb8f3` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `d4e0db6b9b650ddafbefc8c6166c6d62cdccbb1acaa3e0f75b767324632f4d31` |
| `scripts/b6_6_cleanup.sh` | `b993af392407d9b106e657172a4b4dacca2871a7c6a0fee0bdb32c3bf5f01711` |
| `scripts/b6_6_deadline.py` | `486b8e2332742584f03ed211199aa1aaf7bc5cb478b769b41afe3943aec901a5` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_receipt.py` | `3629ffc8b3b6c34ec1d3cfdcde5c8aa28a73796806826f8dce5084a8995c7280` |
| `scripts/check_b6_6_window_plan.py` | `8c19f2a5bc236f37d07c99999e093e48927e503bf2b52f786d34447ed117509f` |
| `scripts/pin_aws_lbc_digest.py` | `bcd3b3e97e992f96457df392f76aa9da8d91f801a0610cc5533660c6400ce5cb` |
| `scripts/run_b6_6_integration_window.sh` | `fb80d750a1f19a158692d88f9c2b4d86e0babc91298279a4c102d7738ed5731f` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |

## Explicit prohibitions

- no public ALB, production DNS, production SSM alias or serving-registry write;
- no `approved/asr/` write, model registration, MLflow transition, language
  artifact/approved-version change, deployment claim or B5/B6 promotion;
- no real Bedrock or Fish invocation, training, green-bucket mutation or PHI;
- no image rebuild, tag deployment, scan waiver or unreviewed IAM permission;
- no capacity beyond the stated nodes/task or execution beyond four hours; and
- no application execution before both AWS-side deadlines are armed and
  verified.

## Review and approval phrase

This packet is not authorized by its preparation. It requires an independent
architecture/IAM/security review bound to the final packet SHA-256 and prepared
commit, followed by an owner authorization record
`B6-AWS-AUTH-2026-008` containing that review, the exact source-hash map and the
cost binding.

Suggested owner phrase after review:

`Approve B6 AWS change packet 2026-008 only.`
