# B6 AWS change packet 2026-021 — endpoint and cleanup guards

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

This successor preserves packet 2026-020A attempt 1 as a terminal refusal,
corrects the two local guard defects it exposed, and requests only the single
non-transferable attempt that remains from the approved two-attempt allowance.
This draft authorizes no AWS, Terraform, Kubernetes or worker action.

## Immutable predecessor and live result

| Binding | Value |
|---|---|
| Packet 2026-020A | `f332208a708413a192b3e0365f90a0bf9d5c2b494dac5bca38fbe262c3ef03d3` |
| Authorization 2026-020A | `3c9fa4cc4a85a1fc8e8c70e79f65e106af768ad316616e6fc5733cf5e3524457` |
| Persistent bridge receipt | `7e5c14f0afb1c6d2e2e34d49b3a251f6d31a1ba126bf1da0f3d59154acc22db7` (`PASS`) |
| Attempt-1 result evidence | `12c6c2cdfb72a88ef308d59a3ffac043a5330e7cb1c716b031e7662f798b8036` |
| Attempt-1 terminal stage | `terraform_window: REFUSED` |
| Original cleanup receipt | `cleanup: REFUSED` |
| Manual recovery | verified zero state |
| Attempts consumed / remaining | `1 / 1` |

Attempt 1 passed twelve stages through `pre_endpoint_images`. All five services,
the DRA driver and the temporary controller were Ready from their exact pinned
digests. The endpoint plan contained exactly eleven creates and no changes or
destroys, but it was never applied. No endpoint, Fargate probe, ALB or conversation
proof was started. Manual recovery subsequently verified CPU/GPU zero, no deadline
actions, no temporary resources, no production pointer and the persistent secret
retained with the operator denied.

## Correction 1 — controller no-op is in the endpoint plan

The refused endpoint plan targeted only the eleven endpoint/probe resources.
Terraform correctly omitted the already-created controller from the targeted
plan, while `validate_endpoints` required that controller's no-op entry. The
validator therefore refused on an absent entry before apply.

The endpoint plan target set now also includes exactly
`helm_release.b6_load_balancer_controller`. The controller remains enabled, so its
only permitted action is `no-op`; the eleven endpoint/probe resources must remain
exactly `create`. The existing validator continues to refuse any controller
create, update, replacement or deletion and any endpoint delta other than the
reviewed eleven creates.

## Correction 2 — cleanup follows stage status, not file existence

The original cleanup selected the full twelve-resource destroy guard whenever
`terraform_window.json` existed. A REFUSED receipt is mandatory even when the
stage stops before apply, so existence incorrectly implied that all resources had
been created. The actual cleanup plan contained only the controller deletion and
was rejected by the full-destroy guard.

Cleanup now parses the immutable receipt status:

- `terraform_window: PASS` requires the exact twelve-resource destroy plan;
- `REFUSED`, absent or malformed status uses the bounded partial-cleanup guard,
  which permits only delete actions on a non-empty subset of those same twelve
  addresses; and
- any create, update, replacement, unknown address or empty/malformed guard input
  refuses.

This does not weaken successful-window cleanup. It makes pre-apply and partial-
apply refusals recoverable inside the same bounded address set.

## Carried-forward controls

All other reviewed R1–R7 controls are unchanged, including:

- the already completed, exact-principal-verified persistent-secret bridge;
- one write-once v2 receipt engine with finally-guaranteed receipts;
- in-place synthetic credential rotation with operator plaintext denial;
- fresh unchanged cold rehearsal before the attempt;
- images and all workloads Ready before endpoint redirection;
- principal-independent pull-only endpoints and self-isolated endpoint SG;
- 1,200-second worker gate and 4,500-second dual deadline before compute;
- bounded tag-mutation warning rule; and
- persistent secret retention with exact zero-state cleanup proof.

Execution must use `--attempt 2` and the previously unused directory
`platform/evidence/receipts/B6-2026-021-A2-LIVE/`. Attempt 1 may not be replayed.

## Replacement cold rehearsal

| Binding | Value |
|---|---|
| Receipt | `platform/evidence/receipts/B6-2026-021-COLD/cold_rehearsal.json` |
| Receipt SHA-256 | `ef6d729c6b4b7584c1b0164219ed0f6fb3645f319b103201e4b7a32af77131d1` |
| Scenario-results SHA-256 | `2e3f75248730da81e7159147954c9fd3f933e9e35caecac635d9c062a19cd3cf` |
| Full PASS | `1` with `23/23` PASS receipts |
| Injected failures | `23`, each with a refusing receipt |
| Cleanup from injected failures | `23/23` complete |
| Real AWS / kubectl calls | `0 / 0` |
| Frozen source hashes including cold receipt | `45` |

## Allowance continuity

| Control | Value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Existing reservation | `$10.00` |
| Attempts already consumed | `1` |
| Attempts requested here | `1` |
| Maximum duration | `4,500` seconds, non-transferable |
| Maximum requested worker seconds | `4,500` |
| Estimated compute | approximately `$1.60` |
| New reservation | `$0` |

A refusal or incomplete proof terminates the remaining allowance. No third attempt
is authorized. Actual attempt-1 billing remains pending normal AWS billing lag and
will be reconciled without releasing the existing reservation early.

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
| `platform/evidence/B6-PACKET-2026-020A-ATTEMPT-1-REFUSED-ENDPOINT-PLAN-GUARD.json` | `12c6c2cdfb72a88ef308d59a3ffac043a5330e7cb1c716b031e7662f798b8036` |
| `platform/evidence/receipts/B6-2026-020A-BRIDGE/persistent_secret_bridge.json` | `7e5c14f0afb1c6d2e2e34d49b3a251f6d31a1ba126bf1da0f3d59154acc22db7` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `c60997165869710e8b518611a6ea41b98100514c093f0ddc5565ec4e1fc26f52` |
| `scripts/b6_6_cleanup.sh` | `fee022f89cf61604d15a5dd147f63881f3e60e94231dc0eba9c63d9d1a148671` |
| `scripts/b6_6_cold_rehearsal.py` | `8ba7f444375c22c3c5947f333d75bb7c21698554ebef7133ba2b594554daef18` |
| `scripts/b6_6_credential.py` | `5d8c7a60cf28f68267b3e47373a5699b4e59776d8ffdaa4e6fb020975352be4d` |
| `scripts/b6_6_deadline.py` | `fd3962f185c91359e3e046959801294bbbedd1c1db918c823c00a58abe2fa0e0` |
| `scripts/b6_6_fargate_probe.py` | `b7a904aaea8a148121c7959fd79fd9af44eb12c3645dcc76dbc4ab35c21f3c8c` |
| `scripts/b6_6_lbc_runtime.py` | `3436e804bdfcd3034f1abeaf1ea9f1e82520b3cdbe1dda07851d54ab83848656` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_manifest_slice.py` | `61f79a8cdf04f8e94aac2a07b7f0f1924d560c7b7b9d0a5f64973b1ec4d217fb` |
| `scripts/b6_6_operations.sh` | `3aeaedf5369bf7787abe4eabd638e91682c2153ec97e7a79958463f7ae012027` |
| `scripts/b6_6_persistent_secret_bridge.py` | `2f9ab3328d2b466702557853e21cab5e674d1ba22e3dcdef7c134480e497a083` |
| `scripts/b6_6_pre_endpoint_images.py` | `3ce744d886afe58e445bc415bf096bf74b9301d83f3b9c450452b144b4260491` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `a38377a37b8f53556389bcf947a9115ec02ca92db109fb9daa05ea2ab5db684d` |
| `scripts/b6_6_runner.py` | `4c0883bc6cddd10bd5f1c93bf1540b204f1cc8828b6db657bb650d92882ea348` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_persistent_secret_plan.py` | `d9f03d9a0fe67d259587403121bd4df19cbaf98e8e1150f03111b03ed1eebd20` |
| `scripts/check_b6_6_window_plan.py` | `6d726830b1fd895ca3c444760696b267aa57c7e9c992448c5e14d7c346f6caa0` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_consolidated_window.py` | `591e57ead285ee3bfff12281bc9bc0d4921bcd8d257a8be3d6b36ca721f8fea1` |
| `platform/evidence/receipts/B6-2026-021-COLD/cold_rehearsal.json` | `ef6d729c6b4b7584c1b0164219ed0f6fb3645f319b103201e4b7a32af77131d1` |

## Deviations

No R1–R7 design deviation. The plan target now makes the already-required
controller no-op observable, and cleanup status selection restores the intended
partial-refusal behavior within the already reviewed address set.

## Approval boundary

Independent review must bind the prepared commit, this packet's SHA-256, the
replacement cold receipt, the attempt-1 refusal/zero-state record, the controller
no-op target and the status-selected cleanup guard. Only then may the owner state:

> Approve B6 AWS change packet 2026-021 only, including the single remaining 4,500-second attempt within the existing $10 reservation.

An owner-approved `B6-AWS-AUTH-2026-021` record is required before attempt 2.
