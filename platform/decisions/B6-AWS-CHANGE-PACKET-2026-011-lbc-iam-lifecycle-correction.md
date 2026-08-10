# B6 AWS change packet 2026-011 — ALB controller full-lifecycle IAM correction

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-10

Account/region: `558069890522` / `eu-central-1`

Required operator: `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested

Authorize exactly one in-place update to the existing Terraform-managed inline
policy `medzen-lbc-access` on `medzen-lbc-role`. The update corrects the ALB
parent-resource authorization that refused packet 2026-010 and closes the
entire listener/rule lifecycle in one reviewed change.

This draft authorizes nothing. In particular, it does not authorize secret or
token restoration, worker capacity, Helm, Kubernetes, an ALB, a retry window,
SSM publication, model changes or any other AWS mutation.

## Why the correction is needed

Packet 2026-010 reached every service readiness stage, then the AWS Load
Balancer Controller received `AccessDenied` on `CreateListener` against this
load-balancer parent ARN:

`arn:aws:elasticloadbalancing:eu-central-1:558069890522:loadbalancer/app/medzen-b6-window/ec356cda681eae2a`

The old statement listed `CreateListener` but scoped it only to listener and
listener-rule child ARNs. AWS documents the load balancer as the resource type
for `CreateListener` and the listener as the resource type for `CreateRule`.
The official controller policy likewise treats create and delete resource
semantics separately.

Refusal record:
`platform/evidence/B6-PACKET-2026-010-REFUSED-ALB-LISTENER-IAM.json`.
Automatic cleanup passed and the cluster returned to exact zero workers and
zero window resources before this packet was prepared.

## Exact policy delta

Replace only `CreateAndManageExactB6ListenersAndRules` with five statements:

1. `CreateOnlyExactB6ListenersOnClusterTaggedAlb` — `CreateListener` only on
   the exact `medzen-b6-*` load-balancer parent ARN, in `eu-central-1`, with the
   cluster resource tag.
2. `CreateOnlyExactB6RulesOnClusterTaggedListener` — `CreateRule` only on the
   exact listener parent ARN with the same region and cluster-tag boundary.
3. `ManageOnlyExactClusterTaggedB6Listeners` — only `ModifyListener` and
   `DeleteListener` on exact listener child ARNs.
4. `ManageOnlyExactClusterTaggedB6Rules` — only `ModifyRule`,
   `SetRulePriorities` and `DeleteRule` on exact rule child ARNs.
5. `TagOnlyDuringExactB6ListenerAndRuleCreation` — dependent `AddTags` on
   `Resource: "*"` only when `elasticloadbalancing:CreateAction` is exactly
   `CreateListener` or `CreateRule`, the exact existing request-tag values are
   present, and every tag key is in the existing allowlist.

There are no new IAM action names: both live and proposed policies contain the
same 41 unique actions. Every unrelated statement is unchanged. In particular,
`TagOnlyExactB6Resources` has identical canonical SHA-256 before and after:
`936c19acea4cf0a122e5568cd14e94fd61d0875a439e2fc5a20c25dade2b3683`.

The live policy contains no `Deny` statement. Therefore this proposal removes
no Deny statement and adds none; its region, name, cluster-tag, internal-scheme,
exact subnet, exact security-group and tag-key boundaries remain fail closed.

## Full lifecycle simulation already completed

Evidence:
`platform/evidence/B6-LBC-IAM-LIFECYCLE-SIMULATION-2026-001.json`.

The live role was tested with `SimulatePrincipalPolicy` before any change:

- 44 total scenarios;
- 18 allowed and 26 implicit denies;
- required failures: `CreateListener` on its load-balancer parent plus the two
  dependent creation-tag paths;
- the other 18 required lifecycle operations were allowed;
- all 19 negative boundary cases remained denied.

The proposal then passed both required pre-apply simulations:

- live role plus proposed policy overlay: **21/21 required lifecycle operations
  allowed**, zero mismatches;
- proposed policy alone: **21/21 required operations allowed**, all **19/19
  negative boundary cases denied**, zero mismatches.

The matrix includes creation, modification, priorities, tagging, target
registration, and the complete listener/rule/load-balancer/target-group delete
path used by cleanup. It explicitly tests `CreateListener` on the load-balancer
parent and `CreateRule` on the listener parent.

### IAM Simulator listener/rule tagging limitation

Exact `AddTags`/`RemoveTags` probes on listener and listener-rule ARNs are kept
in the report. AWS IAM Simulator returned `implicitDeny` for those four pairs
even for a control policy allowing both actions on `Resource: "*"`. They are
therefore classified as simulator observations, not hidden and not claimed as
PASS. The dependent creation-tag authorization is tested separately using the
documented `elasticloadbalancing:CreateAction` context and passes for both
listener and rule creation with exact tags; unexpected tag keys remain denied.

This packet does not treat simulation as a deployment proof. The later bounded
window must still prove the controller's real tag and cleanup behavior.

## Exact no-compute Terraform plan

A live, state-refreshed targeted plan was generated without applying it:

- plan: `0 add / 1 in-place update / 0 destroy`;
- only address:
  `aws_iam_role_policy.b6_load_balancer_controller`;
- plan SHA-256:
  `350fad7294212ec67ee7bdb6f9bf7a3141785ae5806069eb1baa01629e5e3ad0`;
- machine guard:
  `PASS_B6_LBC_IAM_CORRECTION changes=1 add=0 update=1 destroy=0`.

The saved plan is temporary and will not be reused after review. Execution must
regenerate a fresh state-refreshed plan, and
`scripts/check_b6_lbc_iam_correction_plan.py` must accept it before apply.

## Authorized execution, only after review and approval

1. Verify exact caller, account, region, approved commit, clean worktree and all
   source hashes below.
2. Read back the live policy and refuse if its canonical hash is not
   `f1cd1b2134a5431d38bfdef223eb10dfe52041ceadc2bbbbd441047f97ae97fe`.
3. Verify CPU desired/instances = 0, GPU desired/instances = 0, Kubernetes
   workers = 0, the window controller/ingress/DRA are absent, and no window
   shutdown deadline remains.
4. Generate a fresh targeted Terraform plan. Refuse unless the guard proves
   exactly `0 add / 1 update / 0 destroy` at the single inline-policy address.
5. Apply that exact saved plan once.
6. Read back the policy and require canonical SHA-256
   `38db52d2a17acf608da3f8236b5b41baaad09e6d6e67e53ffc09e3c188309529`.
7. Run `scripts/simulate_b6_lbc_lifecycle.py --mode live-postapply`. Require
   21/21 required operations allowed, all 19 negative boundary cases denied,
   zero mismatches, and retain the four exact-ARN tag simulator observations.
8. Verify zero worker/window state again and publish an immutable receipt.

Any drift, extra plan action, simulation mismatch, unknown decision, missing
source, post-apply hash mismatch or nonzero worker/window state stops the packet.

## Rollback

If apply succeeds but policy read-back or a required post-apply simulation
fails, restore the exact pre-change inline policy whose canonical SHA-256 is
`f1cd1b2134a5431d38bfdef223eb10dfe52041ceadc2bbbbd441047f97ae97fe`.
Verify that hash, rerun the live baseline, record the failure and stop. Rollback
must not start compute, install the controller, create an ALB, restore the
secret or perform any unrelated change.

## Cost and standing-resource impact

Incremental expected cost: `$0`. This is one policy-document update on an
existing role. It creates no standing resource and does not use the existing
`$10` B6.6 reservation.

## Exact source bindings

| Source | SHA-256 |
|---|---|
| `infra/alb_controller.tf` | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| `platform/evidence/B6-PACKET-2026-010-REFUSED-ALB-LISTENER-IAM.json` | `4ea2234f6803049d6d4afd4a24a2f03f118c1c45c090b173f61cfef8506fdabf` |
| `platform/evidence/B6-LBC-IAM-LIFECYCLE-SIMULATION-2026-001.json` | `3001d13c666cc8dc48401e5997b252915927aa459e59289317fb55736aae2842` |
| `platform/iam/medzen-lbc-role.policy.template.json` | `722567dc992e0782781ffbc8eba1456f2412510188b2634c672dd59c3d9218ec` |
| `scripts/simulate_b6_lbc_lifecycle.py` | `9c8443fdd0de256e20046ea307623a84e7ca6ca06da2506ba4576b893d266ab4` |
| `scripts/check_b6_lbc_iam_correction_plan.py` | `5583f3e5366c9cd6c65a4e47239346efc56b87075902ecb8029ab5c2ba966f6e` |
| `tests/test_b6_lbc_iam_lifecycle.py` | `9bd513f8bbb73f1ba457989e932d61adf29772685e958b01e926c2eb788646a8` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |

Independent review and the owner authorization record must bind the final
packet SHA-256 and prepared commit. Any mismatch refuses execution.

## Explicit prohibitions

No worker scale-up, Helm/controller install, Kubernetes mutation, load
balancer, target group, listener, rule, secret/token restoration, SSM change,
ECR change, model change, `approved/` write, production SSM write, provider
call, training, PHI or integration-window retry is authorized.

After this IAM packet executes and its evidence is independently accepted, the
synthetic secret/token restoration must be a separate packet. A new bounded
window is a third packet and cannot reuse packet 2026-010.

## Approval phrase

After independent review bound to the final packet SHA-256 and prepared commit,
the only valid owner phrase is:

`Approve B6 AWS change packet 2026-011 only.`
