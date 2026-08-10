# B6 AWS change packet 2026-020A — principal-preflight continuation

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

This successor preserves packet 2026-020 as approved but not executed, implements
its reviewer's binding live-principal requirement, and otherwise carries forward
the already reviewed bridge, R1–R7 window controls, and unused allowance without
expansion. This draft authorizes no AWS, Terraform, Kubernetes or worker action.

## Immutable predecessor

| Binding | Value |
|---|---|
| Packet 2026-020 | `0c1bae0ca8faf5c7f4279f325d0d91c645588b140e923fc47150c312921d4c1e` |
| Reviewed commit | `92ad8f6d7659eba960e43def6586bb5d972b5d19` |
| Reviewed cold rehearsal | `450689352d02c4d127318f42480f0e86cbe7a23311d0ce9430ee63ee22cd15eb` |
| Non-execution record | `platform/evidence/B6-PACKET-2026-020-NOT-EXECUTED-PRINCIPAL-PREFLIGHT-CONDITION.json` |
| Worker seconds consumed | `0` |
| Attempts started | `0` |

Packet 2026-020 was not executed because its bridge did not independently resolve
every referenced IAM principal before its first mutation. No AWS, Terraform,
Kubernetes or compute mutation occurred. It is terminal and may not be retried.

## Sole correction: live principal preflight

Before `RestoreSecret`, `PutResourcePolicy`, Terraform import, plan, or apply, the
bridge now resolves and exact-matches every non-wildcard IAM principal it will
reference:

| Use | Exact principal | Live check |
|---|---|---|
| Secret read Allow and Deny exception | `arn:aws:iam::558069890522:role/medzen-orch-role` | `iam:GetRole`, returned ARN must match byte-for-byte |
| Terraform registry-publisher variable | `arn:aws:iam::558069890522:user/s.fotso` | `iam:GetUser`, returned ARN must match byte-for-byte |

The policy's `Principal: *` Deny subject is a wildcard set, not a concrete IAM
identity, and therefore has no existence check. Any missing principal, denied
lookup, malformed response, or ARN mismatch refuses before mutation and persists
the write-once bridge receipt. A regression test proves both exact resolutions,
the missing/mismatched-principal refusal, and source ordering before both possible
Secrets Manager mutations.

After this preflight, continuation behavior is unchanged: require the exact
restored/KMS-bound secret and current operator deny; idempotently reapply the
canonical policy; import the existing secret and policy; permit the guarded plan
to touch only the three persistent credential addresses; and verify the permanent
operator-denied state. No plaintext read is permitted.

## Window boundary carried forward unchanged

Only a PASS bridge receipt at
`platform/evidence/receipts/B6-2026-020A-BRIDGE/persistent_secret_bridge.json`
may unlock attempt 1. The consolidated 23-stage runner remains governed by R1–R7:

- one write-once v2 receipt engine with finally-guaranteed refusal and cleanup;
- in-place 32-byte credential rotation with 0600/44-byte/single-LF token;
- fresh unchanged cold rehearsal before each attempt;
- all images present and Running before endpoints;
- principal-independent pull-only endpoints and self-isolated endpoint SG;
- bounded 1,200-second worker registration poll;
- dual deadline before compute;
- exact ALB tag-warning/fatal boundary; and
- persistent secret retained while every temporary resource returns to zero.

Attempt receipts are restricted to
`platform/evidence/receipts/B6-2026-020A-A1-LIVE/` and, conditionally,
`B6-2026-020A-A2-LIVE/`.

## Replacement cold rehearsal

| Binding | Value |
|---|---|
| Receipt | `platform/evidence/receipts/B6-2026-020A-COLD/cold_rehearsal.json` |
| Receipt SHA-256 | `301c47aa6bc073d9c4f3d03cadc47837f757b27e23ff37c584c11a238b8a4feb` |
| Scenario-results SHA-256 | `2e3f75248730da81e7159147954c9fd3f933e9e35caecac635d9c062a19cd3cf` |
| Full PASS | `1` with `23/23` PASS receipts |
| Injected failures | `23`, each with the refusing receipt persisted |
| Cleanup from injected failures | `23/23` complete |
| Real AWS / kubectl calls | `0 / 0` |
| Frozen source hashes | `43` |

## Allowance continuity

| Control | Value |
|---|---:|
| Aggregate ceiling | `$300.00` |
| Existing reservation | `$10.00` |
| Worker seconds consumed by packets 2026-019 and 2026-020 | `0` |
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
| `platform/evidence/B6-PACKET-2026-020-NOT-EXECUTED-PRINCIPAL-PREFLIGHT-CONDITION.json` | `64d9d6a29562535ce96137506cbac62d54286460d19dd6a23205a004d85394d5` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `88cdfe185228b88fc721981ff09c4eda01774fe57f3b9cf66c58d1bc6fb56af9` |
| `scripts/b6_6_cleanup.sh` | `819d6bf33f1adbb89b58127a173e81c826fbb2bc558cd80ff3436ea71400789b` |
| `scripts/b6_6_cold_rehearsal.py` | `8ba7f444375c22c3c5947f333d75bb7c21698554ebef7133ba2b594554daef18` |
| `scripts/b6_6_credential.py` | `5d8c7a60cf28f68267b3e47373a5699b4e59776d8ffdaa4e6fb020975352be4d` |
| `scripts/b6_6_deadline.py` | `fd3962f185c91359e3e046959801294bbbedd1c1db918c823c00a58abe2fa0e0` |
| `scripts/b6_6_fargate_probe.py` | `b7a904aaea8a148121c7959fd79fd9af44eb12c3645dcc76dbc4ab35c21f3c8c` |
| `scripts/b6_6_lbc_runtime.py` | `3436e804bdfcd3034f1abeaf1ea9f1e82520b3cdbe1dda07851d54ab83848656` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_manifest_slice.py` | `61f79a8cdf04f8e94aac2a07b7f0f1924d560c7b7b9d0a5f64973b1ec4d217fb` |
| `scripts/b6_6_operations.sh` | `5eae072a78914921520b95491a1df0c0ea9b6f448df1072f472a83c1b3db5ada` |
| `scripts/b6_6_persistent_secret_bridge.py` | `2f9ab3328d2b466702557853e21cab5e674d1ba22e3dcdef7c134480e497a083` |
| `scripts/b6_6_pre_endpoint_images.py` | `3ce744d886afe58e445bc415bf096bf74b9301d83f3b9c450452b144b4260491` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `a38377a37b8f53556389bcf947a9115ec02ca92db109fb9daa05ea2ab5db684d` |
| `scripts/b6_6_runner.py` | `4a86d9880260f08c037918bacf380c402ab843af3920d6deed9f9cc3af999b52` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_persistent_secret_plan.py` | `d9f03d9a0fe67d259587403121bd4df19cbaf98e8e1150f03111b03ed1eebd20` |
| `scripts/check_b6_6_window_plan.py` | `6d726830b1fd895ca3c444760696b267aa57c7e9c992448c5e14d7c346f6caa0` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_consolidated_window.py` | `58854475ade0ed002474da1f70aa200a6257e65bfbbb7fc084d2c45630622770` |
| `platform/evidence/receipts/B6-2026-020A-COLD/cold_rehearsal.json` | `301c47aa6bc073d9c4f3d03cadc47837f757b27e23ff37c584c11a238b8a4feb` |

## Deviations

No new R1–R7 design deviation. The only delta from packet 2026-020 is the
reviewer-required, read-only principal-existence preflight and the versioned paths
needed to preserve 2026-020 unchanged.

## Approval boundary

Independent review must bind the prepared commit, this packet's SHA-256, the
replacement cold receipt, the terminal 2026-020 record, and the fact that every
concrete principal is resolved before mutation. Only then may the owner state:

> Approve B6 AWS change packet 2026-020A only, including continuation of the unused 9,000-second two-attempt allowance.

An owner-approved `B6-AWS-AUTH-2026-020A` record is required before the bridge or
any worker attempt.
