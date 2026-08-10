# B6 AWS change packet 2026-020 — bridge-principal continuation

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

This successor preserves packet 2026-019 as a terminal refusal, corrects one
packet-bound IAM-role literal, completes the already approved persistent-secret
bridge from the safely contained state, and carries forward the untouched
two-attempt allowance. This draft authorizes no AWS, Terraform, Kubernetes or
worker action.

## Trigger and immutable predecessor

| Binding | Value |
|---|---|
| Packet 2026-019 | `a3bd79d594e791fa1a4f396aebf16f1e03ddfec2377f8bb9c6b736232c29602b` |
| Authorization 2026-019 | `1404b048071fa0128e17008793355af8b09ae8bbf8ebd4f8bdbd95ff0c725218` |
| Refusing bridge receipt | `2dd182d17cbe94ffb75553a6b58240c83dde5ccf77e3453a4219b8bb1a4b82bc` |
| Result evidence | `fcdf8fc4a1198cb38c1d905e83935698fddda6d0fdb5382da9e8e1a36c2e67e6` |
| Worker seconds consumed | `0` |
| Attempts started | `0` |

The reviewed bridge restored the exact secret and then submitted a Secrets Manager
resource policy whose allowed principal was the nonexistent role
`arn:aws:iam::558069890522:role/medzen-speech-orchestrator`. AWS refused it with
`MalformedPolicyDocumentException`. The v2 receipt was persisted before any
Terraform import, deadline, worker or window resource.

Immediate containment installed the byte-equivalent established Terraform policy
using `arn:aws:iam::558069890522:role/medzen-orch-role`. Canonical policy SHA-256 is
`318a323fe01349dca140c8eff48cfef9da1cda163b6cc7616d3da718c0d20cb1`.
Operator `GetSecretValue` is explicitly denied. No plaintext was read or recorded.
CPU and GPU desired/instance counts remain zero.

## Exact correction

The sole functional correction changes the bridge principal constructor from the
nonexistent `medzen-speech-orchestrator` role to the existing, Terraform-defined
`medzen-orch-role`. A regression test parses the policy and proves both its Allow
principal and Deny exception use that exact ARN.

The bridge adds a `continuation` mode that refuses unless:

- the exact secret ARN/name/KMS identity is present and no longer pending deletion;
- the operator is already denied;
- no plaintext is read;
- the existing secret and resource policy are imported rather than recreated; and
- the guarded Terraform plan touches only the three persistent credential
  addresses, permits no delete/replacement, never creates the secret or resource
  policy, and creates the missing orchestrator KMS reader policy exactly once.

It then reapplies the canonical resource policy idempotently, imports the secret
and policy, applies the guarded plan, re-verifies the identity and operator deny,
and persists a new write-once PASS/REFUSED `persistent_secret_bridge` receipt at
`platform/evidence/receipts/B6-2026-020-BRIDGE/`. The packet-019 receipt remains
unchanged.

## Window boundary carried forward

After the continuation receipt is PASS, the consolidated 23-stage runner from
packet 2026-019 is unchanged except for binding packet/authorization 2026-020 and
requiring the new bridge receipt. All R1–R7 controls remain in force:

- one v2 receipt engine, finally-guaranteed receipts and cleanup;
- in-place 32-byte credential rotation with 0600/44-byte/single-LF token;
- cold rehearsal before each attempt;
- images before endpoints;
- principal-independent pull-only endpoints and self-isolated endpoint SG;
- bounded 1,200-second worker poll;
- dual deadline before compute;
- exact ALB tag-warning/fatal boundary; and
- persistent secret retained while every temporary resource returns to zero.

## Replacement cold rehearsal

| Binding | Value |
|---|---|
| Receipt | `platform/evidence/receipts/B6-2026-020-COLD/cold_rehearsal.json` |
| Receipt SHA-256 | `450689352d02c4d127318f42480f0e86cbe7a23311d0ce9430ee63ee22cd15eb` |
| Scenario-results SHA-256 | `2e3f75248730da81e7159147954c9fd3f933e9e35caecac635d9c062a19cd3cf` |
| Full PASS | `1` with `23/23` PASS receipts |
| Injected failures | `23`, each with the refusing receipt persisted |
| Cleanup from injected failures | `23/23` complete |
| Real AWS / kubectl calls | `0 / 0` |
| Frozen source hashes | `41` |

## Allowance continuity

| Control | Value |
|---|---:|
| Aggregate ceiling | `$300.00` |
| Existing reservation | `$10.00` |
| Worker seconds consumed by packet 2026-019 | `0` |
| Attempts consumed | `0` |
| Attempts requested under this successor | at most `2` |
| Maximum per attempt | `4,500` seconds, non-transferable |
| Total remaining allowance | `9,000` seconds |
| Estimated two-attempt compute | approximately `$3.20` |
| New reservation | `$0` |

Attempt 2 remains conditional on a terminal attempt-1 trail, cleanup PASS, and a
fresh unchanged cold rehearsal. A third attempt is prohibited.

## Frozen source-hash table

| Path | SHA-256 |
|---|---|
| `infra/alb_controller.tf` | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| `infra/b6_6_endpoint_policy_override.tf` | `9dc7e893cd8e0e4612bd082541d7f884cd35e37e964202b577901a26f3b05dae` |
| `infra/b6_6_persistent_secret_override.tf` | `abe501946e6545b8d844d115de95e7f7f6736c840dfec20d2efead05c4a0ad68` |
| `infra/b6_6_window_override.tf` | `c9dc7ebfd17b4ea0e9bf9b50fee7af529405ab44ee4e08827d3a5bf06ef39962` |
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `infra/b6_integration_window.tf` | `df4fec719aa8a709e94d89040c1bc283d9f847aa894a803fb00bb93e99f3c144` |
| `infra/eks.tf` | `37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b` |
| `infra/variables.tf` | `59c1226f9a797e13756575ef77b45ce9324e1f1fb4743bc7d84fa8bec4f272dd` |
| `pipeline/b6_integration_receipts.py` | `3010e6b6062aea498f2599fd33c63feba8fbde4cc5b09883b2a772908b705f36` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json` | `831c164a6ca75017a3f9d11e38550cc52c7785b3abcb65f1963d82378995e244` |
| `platform/decisions/B6-WINDOW-VERIFIER-POLICY-2026-001.json` | `73eacb9cc6a9d9850098464f70380c92e25c46ac4aff7e4b67515c0269b5a236` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/designs/B6-WINDOW-DESIGN-REVIEW-2026-001.md` | `b55198105f9a8de95191ad9032679e73bbb4f33df4f9a9c47e3359b3d759fd2a` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-PACKET-2026-018-REFUSED-CREDENTIAL-LEGACY-VERSION-CARDINALITY.json` | `95735b36a225a3558cc95430258ec3d3b3a6ceb4976387498fa82004f5b3ca62` |
| `platform/evidence/B6-PACKET-2026-019-REFUSED-BRIDGE-PRINCIPAL.json` | `fcdf8fc4a1198cb38c1d905e83935698fddda6d0fdb5382da9e8e1a36c2e67e6` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `9c947dcba5c830d6e50ad23fdc246a5ace4979ba0f79d2454722705e7a2418c2` |
| `scripts/b6_6_cleanup.sh` | `819d6bf33f1adbb89b58127a173e81c826fbb2bc558cd80ff3436ea71400789b` |
| `scripts/b6_6_cold_rehearsal.py` | `8ba7f444375c22c3c5947f333d75bb7c21698554ebef7133ba2b594554daef18` |
| `scripts/b6_6_credential.py` | `5d8c7a60cf28f68267b3e47373a5699b4e59776d8ffdaa4e6fb020975352be4d` |
| `scripts/b6_6_deadline.py` | `fd3962f185c91359e3e046959801294bbbedd1c1db918c823c00a58abe2fa0e0` |
| `scripts/b6_6_fargate_probe.py` | `b7a904aaea8a148121c7959fd79fd9af44eb12c3645dcc76dbc4ab35c21f3c8c` |
| `scripts/b6_6_lbc_runtime.py` | `3436e804bdfcd3034f1abeaf1ea9f1e82520b3cdbe1dda07851d54ab83848656` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_manifest_slice.py` | `61f79a8cdf04f8e94aac2a07b7f0f1924d560c7b7b9d0a5f64973b1ec4d217fb` |
| `scripts/b6_6_operations.sh` | `5eae072a78914921520b95491a1df0c0ea9b6f448df1072f472a83c1b3db5ada` |
| `scripts/b6_6_persistent_secret_bridge.py` | `6920ddadbc8a41e638ebba3bd6e31ee4f040a3c543c88587011f423d5f15bf27` |
| `scripts/b6_6_pre_endpoint_images.py` | `3ce744d886afe58e445bc415bf096bf74b9301d83f3b9c450452b144b4260491` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `a38377a37b8f53556389bcf947a9115ec02ca92db109fb9daa05ea2ab5db684d` |
| `scripts/b6_6_runner.py` | `9ec55164af3e660f552007240c295b44ee44e239a3fb1b51026bc3b957c84ae3` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_persistent_secret_plan.py` | `d9f03d9a0fe67d259587403121bd4df19cbaf98e8e1150f03111b03ed1eebd20` |
| `scripts/check_b6_6_window_plan.py` | `6d726830b1fd895ca3c444760696b267aa57c7e9c992448c5e14d7c346f6caa0` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_consolidated_window.py` | `0aa90856793a58d7536d94013084f6eabd1abd60af8e70048a5bbe4f152454c1` |
| `platform/evidence/receipts/B6-2026-020-COLD/cold_rehearsal.json` | `450689352d02c4d127318f42480f0e86cbe7a23311d0ce9430ee63ee22cd15eb` |

## Deviations

The packet-019 one-time restoration bridge deviation remains accepted and is now
partially complete. This successor adds no new design deviation: it corrects the
principal to the existing Terraform role and resumes only from the contained state.

## Approval boundary

Independent review must bind the prepared commit, this packet's SHA-256, the new
cold receipt and the exact principal correction. Only then may the owner state:

> Approve B6 AWS change packet 2026-020 only, including continuation of the unused 9,000-second two-attempt allowance.

An owner-approved `B6-AWS-AUTH-2026-020` record is required before continuation or
any worker attempt.
