# B6 AWS change packet 2026-010 — token-encoding-corrected B6.6 window

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-09  
Account/region: `558069890522` / `eu-central-1`  
Required operator: `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested

Authorize one synthetic-only B6.6 integration window using the corrected token
encoding gate. This packet supersedes packet 2026-009 by reference only; it does
not edit or resume it. Packet 2026-009 refused before its first receipt, AWS
deadline, worker, Terraform apply or billable resource.

This draft authorizes no AWS or Kubernetes mutation. It requires independent
review bound to its final SHA-256 and prepared commit, followed by a new
`B6-AWS-AUTH-2026-010` owner record containing the exact source map below.

## Exact correction

Packet 2026-009 required a 44-byte newline-terminated token file but compared
the packet-006 43-byte bearer hash to all 44 bytes. Packet 2026-010 instead:

- requires exact path `/private/tmp/medzen-b6-6-client-token` and mode `0600`;
- requires exactly 44 bytes, exactly one final LF, no CR and no embedded line
  ending in the 43-byte bearer;
- hashes only the 43-byte bearer and compares it to immutable packet-006 value
  `fe83e1a29619c5b05b83b1d77d820dde850d35e6a75102947881e6d152d68be6`;
- emits only a sanitized refusal code; it never prints or persists plaintext.

Refusal evidence:
`platform/evidence/B6-PACKET-2026-009-REFUSED-TOKEN-ENCODING.json`, SHA-256
`3295768ed6d326125f4c5098908a0b6e090c800a93b35c199ecadf0a574d8a49`.
Correction evidence:
`platform/evidence/B6-6-LOCAL-CORRECTION-2026-002.json`, SHA-256
`2fc6bd357dcf6c111c0d6614a8f930a4f2cecb04915b141f152f60dc2c0b870b`.

## Unchanged reviewed boundary

All packet-2026-009 infrastructure, images, services, network isolation,
worker-registration correction, stage receipts, drills and cleanup controls are
unchanged. In particular:

- all seven workload images and the controller remain pinned to their
  scan-passed linux/amd64 child manifests;
- create remains machine-guarded at exactly `7 add / 0 change / 0 destroy`;
- current cleanup remains machine-guarded at exactly
  `0 add / 0 change / 3 destroy`, with no CPU node-group change;
- the worker gate waits for exactly two CPU and one GPU resource to exist and
  become Ready, and refuses excess capacity;
- cleanup is invoked through `bash`, persists immutable `INCOMPLETE` and
  `cleanup_recovery` receipts, and keeps dual AWS deadlines armed until exact
  CPU/GPU zero;
- the service sequence remains RAG → ASR/model-loader → text-only TTS → fake
  LLM → orchestrator → internal ALB and synthetic probes;
- B5 remains `BLOCKED`; no approved artifact, model registration, language
  approval, production SSM pointer, real provider, PHI or production traffic is
  permitted.

Terraform source hashes are byte-identical to packet 2026-009, so its live
read-only plan proofs remain applicable. Execution still regenerates fresh
plans and applies only after the same machine guards pass.

## Time and budget continuity

- Original cumulative worker allowance: `14,400 seconds`.
- Conservatively charged by packet 2026-008: `1,784 seconds`.
- Packet 2026-009 worker seconds: `0`.
- Maximum packet-2026-010 window: `12,600 seconds`, leaving the same 16-second
  safety margin.
- Existing active reservation: exactly `$10` under
  `B6-INTEGRATION-WINDOW-2026-001`; new reservation: `$0`.
- Aggregate ceiling remains `$300`; packet-008 billing remains pending.

Maximum capacity is unchanged: two `m6i.large` CPU nodes, one `g6.xlarge` GPU
node, one short-lived `0.25-vCPU / 0.5-GiB` Fargate probe and one internal ALB.

## Deadline-first stages and cleanup

The exact token gate, owner/review/source bindings, zero-state checks, registry,
secret metadata and absent production pointer all pass before AWS mutation.
Then the runner:

1. arms and verifies matching CPU/GPU scale-to-zero deadlines;
2. raises bounded workers and persists `workers_ready` only after exact
   existence and readiness;
3. applies the guarded seven-add window, controller and DRA;
4. deploys the five services from zero replicas in dependency order;
5. proves internal-ALB isolation, one Fargate `/readyz` probe, synthetic file,
   WebSocket, cancellation, refusal, RAG-unavailable and ClusterIP isolation;
6. cleans up in packet order, schedules recoverable seven-day secret deletion,
   removes the local token, proves zero, then disarms both deadlines.

Every receipt is write-once, fsync-persisted and PHI-safe. A later failure never
voids an earlier receipt. Any cleanup failure persists its safe failing stage
and retains the independent deadlines.

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
| `platform/evidence/B6-6-LOCAL-CORRECTION-2026-002.json` | `2fc6bd357dcf6c111c0d6614a8f930a4f2cecb04915b141f152f60dc2c0b870b` |
| `platform/evidence/B6-CLIENT-API-KEYS-2026-001.json` | `6120c7a9b82dd51a2ceccd504156c8448c0322c5ba31e65334505caf3856c2e0` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-LBC-QUALIFICATION-AWS-EXECUTION-2026-001.json` | `56265113cbfa3ebec85309ec9966dc5fb7a2dd28e1c5fad0b1a4dd6e946cb8f3` |
| `platform/evidence/B6-PACKET-2026-008-REFUSED-WORKER-REGISTRATION.json` | `f2b8acbabafb2642e5b70ddbae966930f2ba62201c7a2fb26f6e32bc3246d432` |
| `platform/evidence/B6-PACKET-2026-009-REFUSED-TOKEN-ENCODING.json` | `3295768ed6d326125f4c5098908a0b6e090c800a93b35c199ecadf0a574d8a49` |
| `platform/evidence/B6A-PACKET-2026-003C-A-SCAN-RESULT.json` | `1b1ed84205fe9a71c3b21b2a2658814855fd5fdcf6af00c5590bb4205e8dc70b` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `b282304a1c13ad9ba9191930d42d6f48dd7c5329dad7a4f45c00a0de388b33ca` |
| `scripts/b6_6_cleanup.sh` | `be0438b87ac48421573873a205a4a0cce9528e1d9a7a198406e6e5f1d46d8dcc` |
| `scripts/b6_6_deadline.py` | `c1ef7700b6fd746348724e7e532f00fdea178034d323dc28e39b3ae01cb61a4a` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_receipt.py` | `3629ffc8b3b6c34ec1d3cfdcde5c8aa28a73796806826f8dce5084a8995c7280` |
| `scripts/b6_6_token_binding.py` | `14685af82acece39fc90c18a980abd972f0ce0235b9a4603acf88e19b9499f4e` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_window_plan.py` | `8c19f2a5bc236f37d07c99999e093e48927e503bf2b52f786d34447ed117509f` |
| `scripts/pin_aws_lbc_digest.py` | `bcd3b3e97e992f96457df392f76aa9da8d91f801a0610cc5533660c6400ce5cb` |
| `scripts/run_b6_6_integration_window.sh` | `a444fbab34ab869739952cd25cf1eb5e860625c221fb5dc570ffa0b356712ce5` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |

Any mismatch, unknown state, stale resource, malformed token encoding, plan
delta, capacity excess or incomplete evidence refuses before the next stage.

## Explicit prohibitions

No public/production exposure, production SSM write, approved-artifact write,
model registration, language approval, real Bedrock/Fish call, training, PHI,
image mutation, scan waiver, unreviewed IAM change, capacity excess, deadline
extension or disarm-before-zero is authorized.

## Review and approval phrase

This packet is not authorized by its preparation. After independent review, the
only valid owner phrase is:

`Approve B6 AWS change packet 2026-010 only.`
