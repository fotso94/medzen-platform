# B6 AWS change packet 2026-022 — hardened Fargate boundary correction

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Packet 2026-021 is terminal. Its final authorized attempt passed fourteen stages
through private-endpoint readiness, then the local Fargate task-definition
verifier refused the correctly hardened task before `ecs:RunTask`. Automatic
cleanup completed and independent read-back confirmed exact zero state. This
successor corrects that verifier defect, implements the reviewer's named-resource
receipt condition and requests a fresh R7 allowance. This draft authorizes no AWS,
Terraform, Kubernetes, worker or secret mutation.

## Immutable predecessor and refusal

| Binding | Value |
|---|---|
| Packet 2026-021 | `a2354dafcd4d89ce9adf9b345ae23ee5364d53d5ca251deadf36a779d4dcae54` |
| Authorization 2026-021 | `9d46897a3a4375e1b245d2e239fcd7b93c9d3ecfc4f186d4dc2dbdab4edb71fe` |
| Attempt-2 result | `platform/evidence/B6-PACKET-2026-021-ATTEMPT-2-REFUSED-FARGATE-BOUNDARY.json` |
| Attempt-2 result SHA-256 | `6f40490f9f8496036235085ebdc3b5b6042b5108753b6833245be5c293ed5b3b` |
| Terminal stage | `fargate_probe: REFUSED` |
| Passed before refusal | `14` stages, through `endpoints_ready` |
| Fargate tasks started | `0` |
| Conversation proofs run | `0` |
| Cleanup | `PASS`, receipt `eade6838f707b5d187ec8e4d6785b7190dac24251f3620cec30fb7839a84fe54` |
| Prior allowance | `2 / 2` attempts consumed; `0` remain |

The registered ECS task revision had both `initProcessEnabled=true` and
`capabilities.drop=[ALL]`. AWS normalized the capability object by also returning
`add=[]`. The verifier compared the entire `linuxParameters` object to only
`{initProcessEnabled:true}` and refused the additional security control. No
`RunTask` event occurred in the refusal interval. This was a local verifier defect,
not an AWS, endpoint, image-pull, ALB, service or application failure.

## Correction 1 — exact hardened task boundary

The verifier now requires the exact AWS read-back shape:

```json
{
  "capabilities": {"add": [], "drop": ["ALL"]},
  "initProcessEnabled": true
}
```

It refuses a missing `drop=ALL`, a non-empty added-capability list, an omitted
normalized `add` list, or any other task/container boundary drift. Regression
tests reproduce the live accepted shape and prove the weaker and expanded shapes
refuse.

## Correction 2 — safe structured probe refusal

The probe helper now emits a fixed, allowlisted reason code for verifier
refusals. The operation dispatcher persists that JSON before returning nonzero,
and the receipt runner copies only three reviewed non-PHI fields:

- allowlisted reason code;
- whether the application started; and
- whether `/readyz` completed.

Request bodies, credentials, audio, transcripts, replies, citations, stdout and
stderr remain prohibited. Unknown or malformed helper results retain the generic
fail-closed stage reason and are not propagated.

## Correction 3 — Terraform receipts bind names and counts

The `controller_window` and `terraform_window` receipts now derive plan counts
and exact changed-resource names from the machine-readable Terraform plan before
apply. Both values are checked against the already reviewed guard and persisted.

`controller_window` must record exactly one create:

- `helm_release.b6_load_balancer_controller[0]`

`terraform_window` must record exactly eleven creates, zero changes and zero
destroys, with these exact names:

- `aws_ecs_cluster.b6_probe[0]`
- `aws_ecs_task_definition.b6_probe[0]`
- `aws_iam_role.b6_probe_execution[0]`
- `aws_iam_role_policy.b6_probe_execution[0]`
- `aws_security_group.b6_probe_endpoints[0]`
- `aws_vpc_endpoint.b6_probe_ecr_api[0]`
- `aws_vpc_endpoint.b6_probe_ecr_dkr[0]`
- `aws_vpc_endpoint.b6_probe_s3[0]`
- `aws_vpc_security_group_ingress_rule.b6_alb_from_backend[0]`
- `aws_vpc_security_group_ingress_rule.b6_nodes_from_alb[0]`
- `aws_vpc_security_group_ingress_rule.b6_probe_to_endpoints[0]`

The controller must remain a no-op in the endpoint plan. A replacement, update,
unknown address or count/name disagreement refuses before apply.

## Local-material cleanup correction

The exact temporary ALB-hostname file is now packet-versioned and removed during
cleanup together with the synthetic token. Neither contains PHI, but both must be
absent in final zero-state proof.

## Carried-forward execution boundary

All R1–R7 controls and every reviewed constraint from packets 2026-019 through
2026-021 remain unchanged:

- compute-free in-place synthetic credential rotation at stage 0, with operator
  plaintext denial and the persistent secret retained;
- independent CPU and GPU scale-to-zero deadlines armed before workers;
- at most two CPU nodes, one GPU node and one private Fargate probe;
- all platform workloads and eight exact scan-passed child images ready before
  private endpoint DNS is enabled;
- principal-independent pull-only ECR endpoint policies, a resource-bounded S3
  endpoint policy and the self-isolated endpoint security group;
- internal ALB only, ingress to the orchestrator only, dependencies `ClusterIP`;
- the bounded four-pair non-fatal post-create tag-mutation rule, with every
  create/delete and non-tag failure remaining fatal;
- write-once receipt-per-stage behavior for PASS, WARNING and REFUSED outcomes;
- no PHI, production traffic, real Bedrock, Fish provider call, model adoption,
  `approved/asr/` write or production SSM pointer; and
- full automatic cleanup before deadline disarm, with persistent-secret operator
  denial re-proven.

The exact 23-stage sequence remains:

1. `stage0`
2. `deadline`
3. `workers_ready`
4. `dra_ready`
5. `rag_ready`
6. `asr_ready`
7. `tts_ready`
8. `llm_ready`
9. `orchestrator_ready`
10. `controller_window`
11. `controller_ready`
12. `pre_endpoint_images`
13. `terraform_window`
14. `endpoints_ready`
15. `fargate_probe`
16. `alb_ready`
17. `alb_tag_mutation_warning`
18. `file_proof`
19. `websocket_proof`
20. `cancellation_proof`
21. `failure_drills`
22. `isolation_proof`
23. `cleanup`

## Replacement cold rehearsal and tests

| Check | Result |
|---|---|
| Cold receipt | `platform/evidence/receipts/B6-2026-022-COLD/cold_rehearsal.json` |
| Cold receipt SHA-256 | `1cbddb33619f392d49f29298866fea8321e4cfe40c6553d35e70a57ba16e4f4a` |
| Scenario-results SHA-256 | `411f8209add90bb5ab00bfcccd86b6f7a029cec04ff3518fa8c16e760221a037` |
| Full simulated PASS | `1`, with `23/23` PASS receipts |
| Injected failures | `23`, each with a REFUSED receipt |
| Injected cleanup | `23/23` completed |
| Real AWS / kubectl calls | `0 / 0` |
| Focused correction tests | `26 passed, 0 failed, 0 skipped, 0 deselected` |
| Canonical repository suite | `1,385 passed, 0 failed, 0 skipped, 7 deselected` |
| Terraform fmt / validate | `PASS / PASS` |

## Fresh R7 allowance request

The prior two-attempt allowance is exhausted and no seconds transfer. This packet
requests a new, explicit two-attempt allowance because no live conversation proof
has yet run. Attempt 2 is available only if attempt 1 refuses and reaches verified
zero state; it is never an automatic retry and requires a new immutable execution
directory. A PASS ends the packet and makes the second attempt unavailable.

| Control | Requested value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Existing reservation | `$10.00` retained pending reconciliation |
| New reservation | `$0` |
| Attempts | `2` maximum |
| Maximum per attempt | `4,500` seconds, non-transferable |
| Maximum requested worker seconds | `9,000` |
| Estimated compute if both windows are used | approximately `$3.20` |
| Actual earlier-window billing | `PENDING_AWS_COST_EXPLORER_LAG` |

This request remains within the existing reservation but is not authorized by the
reservation. It requires explicit owner approval after independent review.

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
| `platform/evidence/B6-PACKET-2026-021-ATTEMPT-2-REFUSED-FARGATE-BOUNDARY.json` | `6f40490f9f8496036235085ebdc3b5b6042b5108753b6833245be5c293ed5b3b` |
| `platform/evidence/receipts/B6-2026-020A-BRIDGE/persistent_secret_bridge.json` | `7e5c14f0afb1c6d2e2e34d49b3a251f6d31a1ba126bf1da0f3d59154acc22db7` |
| `platform/evidence/receipts/B6-2026-022-COLD/cold_rehearsal.json` | `1cbddb33619f392d49f29298866fea8321e4cfe40c6553d35e70a57ba16e4f4a` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `833e42b027272312e9398f0344e8aa53bee8dcb846c048e2d092c67250561995` |
| `scripts/b6_6_cleanup.sh` | `462aa9c63e715c7141f197261ad2c02528e37f2b13b5ce01a0802ff566174632` |
| `scripts/b6_6_cold_rehearsal.py` | `18ae277505f924110d14b934e583a8b4be31ecd1aefb08f63a85a09ea5280600` |
| `scripts/b6_6_credential.py` | `5d8c7a60cf28f68267b3e47373a5699b4e59776d8ffdaa4e6fb020975352be4d` |
| `scripts/b6_6_deadline.py` | `fd3962f185c91359e3e046959801294bbbedd1c1db918c823c00a58abe2fa0e0` |
| `scripts/b6_6_fargate_probe.py` | `300d062f341ff247804666d5e840da3e422809ae6f441afc7fd9537ded35d369` |
| `scripts/b6_6_lbc_runtime.py` | `3436e804bdfcd3034f1abeaf1ea9f1e82520b3cdbe1dda07851d54ab83848656` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_manifest_slice.py` | `61f79a8cdf04f8e94aac2a07b7f0f1924d560c7b7b9d0a5f64973b1ec4d217fb` |
| `scripts/b6_6_operations.sh` | `98af7f743abedfea7e88867e19b9b27ad7d5b467047523735e9033d09b900d5f` |
| `scripts/b6_6_persistent_secret_bridge.py` | `2f9ab3328d2b466702557853e21cab5e674d1ba22e3dcdef7c134480e497a083` |
| `scripts/b6_6_pre_endpoint_images.py` | `3ce744d886afe58e445bc415bf096bf74b9301d83f3b9c450452b144b4260491` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `a38377a37b8f53556389bcf947a9115ec02ca92db109fb9daa05ea2ab5db684d` |
| `scripts/b6_6_runner.py` | `72447177401a2b194186bb4b8b9b90a5d4f16ebdaadb4b2cc63892531ad3d20f` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_persistent_secret_plan.py` | `d9f03d9a0fe67d259587403121bd4df19cbaf98e8e1150f03111b03ed1eebd20` |
| `scripts/check_b6_6_window_plan.py` | `6d726830b1fd895ca3c444760696b267aa57c7e9c992448c5e14d7c346f6caa0` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_consolidated_window.py` | `572532eff413bf8434e518e1d3fae6aa50a1ae0ba62132dcd9147df7c46a360d` |
| `tests/test_b6_6_fargate_boundary.py` | `f441450866bd3674e08d66fe0590e9b9e4a988eb434d26e80bdaf8c54486a49f` |

## Deviations

No deviation from R1–R7. The request renews R7 only because the prior allowance
is exhausted. Requiring the complete hardened task definition, recording safe
refusal codes, and persisting Terraform plan names strengthen the reviewed
boundary without changing deployment scope.

## Approval boundary

Independent review must bind the prepared repository commit, this packet's
SHA-256, cold receipt SHA-256, the live refusal evidence, the exact hardened task
shape, safe refusal allowlist, both named Terraform receipt payloads, all carried
controls and the fresh two-attempt allowance. Only then may the owner state:

> Approve B6 AWS change packet 2026-022 only, including a fresh two-attempt
> allowance of 9,000 seconds maximum (4,500 seconds per attempt,
> non-transferable), approximately $3.20 within the existing $10 reservation.

An owner-approved `B6-AWS-AUTH-2026-022` record is required before execution.
