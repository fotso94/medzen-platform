# B6 AWS change packet 2026-024 — rendered-description charset guard

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Packet 2026-023 is terminal and merged through PR #36. Its Stage A plan guard
passed at exactly 11 additions, but EC2 refused the S3 security-group egress
rule because the description contained an apostrophe. Automatic cleanup
returned every temporary resource and CPU/GPU capacity to zero. No probe task
or full-window attempt ran. This successor corrects the string and eliminates
the whole rendered-description character-class failure mode before apply.

This draft authorizes no AWS, Terraform, Kubernetes, worker, secret or service
mutation.

## Immutable predecessor

| Binding | Value |
|---|---|
| Packet 2026-023 | `503ca67317d2d668a56ad76e08377e5acbd953f7c1c83cef72364d55561b8775` |
| Authorization 2026-023 | `efdd93eeb83ae51d29d61b444035b691974be2d62aa047b361376a6d33c9741d` |
| Refusal evidence | `platform/evidence/B6-PACKET-2026-023-STAGE-A-REFUSED-SG-DESCRIPTION.json` |
| Refusal evidence SHA-256 | `4523a31c5bca84d4704228c7e48a782d0e04fa68cdd024e4b216c759e8c25616` |
| Merged PR | `https://github.com/fotso94/medzen-platform/pull/36` |
| Merge commit | `205b2d59615eb7107911270c0c1c394ab4cf29f1` |
| Terminal stage | `stage_a_terraform: REFUSED` |
| Stable probe passes | `0 / 3` |
| Full-window attempts unlocked / consumed | `0 / 0` |
| Cleanup | `PASS`, CPU/GPU `0 / 0`, temporary resources `0` |

## Prospective correction

The S3 rule description is now:

`TLS from the isolated probe to the ECR S3 layer endpoint`

Only the disallowed apostrophe was removed. Both reviewed network paths remain
byte-for-byte equivalent in purpose and scope: TCP 443 to the self-referenced
ECR endpoint SG and TCP 443 to the packet-created S3 gateway endpoint prefix
list. DNS remains exempt because security groups cannot filter the
Amazon-provided VPC resolver.

## Full rendered-plan description lint

One compiled regex now validates every `description` field recursively across
the rendered Terraform JSON plan. The allowed class is exactly:

`A-Za-z0-9. _-:/()#,@[]+=&;{}!$*`

Null provider-default descriptions are accepted as absent. Direct strings and
Terraform `constant_value` descriptions are validated. Any unknown description
shape fails closed. Any invalid character reports only its plan path and Unicode
code point; the apostrophe is `U+0027`.

The guard runs for create, controller, endpoint, qualification, destroy and
cleanup plan modes. It therefore executes against the actual newly rendered
plan before every apply, not only against the pre-review projection.

To keep the cold rehearsal free of AWS calls and sensitive state, the actual
read-only Stage A plan was reduced to a description-only projection:

- projection: `platform/evidence/B6-RENDERED-TERRAFORM-DESCRIPTIONS-2026-001.json`
- projection file SHA-256:
  `c931a63dc78ca7e4a1441a3fc481d3ad9e9aeea32e4a4be6e347a0f23148304c`
- canonical inventory SHA-256:
  `07ad67c8409d7b5f547bca51c6926cdd2e1fd0ea83a2918347a2d2ca7026b880`
- fields: `50` total, `48` strings, `2` nulls, `0` invalid
- retained credentials, secrets, audio, transcripts, replies or citations: `0`

The cold rehearsal validates all 50 projected fields with the same function
used by the live plan guard, then injects an apostrophe and proves refusal. This
is one generic control, not a special-case comparison against the prior string.

## Plan and execution boundary carried unchanged

The newly rendered read-only Stage A plan passed the guard at exactly
`11 add / 0 change / 0 destroy`. Its temporary binary SHA-256 was
`54544b9e6d08af271bbd1c9ac6b954a865aeef11971ce1336314b387e584656e`;
the binary was not committed because Terraform plan JSON can expose sensitive
state. No apply was run while preparing this packet.

Stage A remains one 1,800-second, `$0.50` maximum qualification with no EKS
worker, GPU, service, controller, ALB or secret mutation. Exactly three
consecutive private probe tasks must pass; the first refusal terminates Stage A
and cleanup always runs. Only a complete three-pass chain plus verified zero
state unlocks either full window.

Both full-window attempts remain 4,500 seconds each and non-transferable.
Attempt 2 exists only after attempt 1 refuses and cleanup proves zero state. A
PASS ends the packet. All R1–R7 controls and the complete 23-stage sequence carry
forward unchanged. No PHI, production traffic, real Bedrock or Fish call, model
adoption, `approved/asr/` write or production SSM pointer is permitted.

## Fresh cold rehearsal and tests

| Check | Result |
|---|---|
| Cold receipt | `platform/evidence/receipts/B6-2026-024-COLD/cold_rehearsal.json` |
| Cold receipt SHA-256 | `7a3f55e8b1960144584f72b1fc2f851327c760837b79c83df354ac2729d22a8d` |
| Scenario-results SHA-256 | `450a4d5b46fe2321a4829361b68f68eb8746b02a23dc14c232ef1c05bdf0f73b` |
| Description projection | `50` fields, `0` invalid |
| Apostrophe refusal cases | `1`, identified as `U+0027` |
| Full-window simulated PASS / injected refusals | `1 / 23` |
| Stage A simulated PASS / injected refusals | `1 / 7` |
| Real AWS / kubectl calls in cold rehearsal | `0 / 0` |
| AWS / Kubernetes mutations in cold rehearsal | `0 / 0` |
| Focused suites | `47 passed, 0 failed` |
| Canonical repository suite | `1,406 passed, 0 failed, 0 skipped, 7 deselected` |
| Terraform fmt / validate | `PASS / PASS` |
| Read-only Stage A plan guard | `PASS`, exact `11 / 0 / 0` |
| Packet-2026-023 failed-plan replay | `REFUSED`, exit `2`, apostrophe `U+0027` found at all `3` rendered paths |

## Allowance continuity request

Packet 2026-023 consumed its one Stage A run and is not retriable. It consumed
no full-window attempt. Packet 2026-024 requests one corrected Stage A and the
same two contingent window attempts inside the existing `$10` reservation.

| Control | Requested value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Existing reservation | `$10.00` retained pending reconciliation |
| New reservation | `$0` |
| Corrected Stage A runs | `1` maximum |
| Corrected Stage A ceiling | `1,800` seconds and `$0.50` |
| Stage A stability proof | `3` consecutive private tasks |
| Stage A EKS/GPU/service mutations | `0 / 0 / 0` |
| Full-window attempts | `2` maximum, gated by Stage A PASS |
| Maximum per window | `4,500` seconds, non-transferable |
| Maximum requested worker seconds | `9,000` |
| Estimated compute for both windows | approximately `$3.20` |
| New Stage A plus window ceiling | `$3.70` |
| Conservative exposure including packet 022 and 023 Stage A maxima | `$4.70`, within `$10` |

## Frozen source-hash table

| Path | SHA-256 |
|---|---|
| `infra/alb_controller.tf` | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| `infra/b6_6_endpoint_policy_override.tf` | `9dc7e893cd8e0e4612bd082541d7f884cd35e37e964202b577901a26f3b05dae` |
| `infra/b6_6_persistent_secret_override.tf` | `abe501946e6545b8d844d115de95e7f7f6736c840dfec20d2efead05c4a0ad68` |
| `infra/b6_6_window_override.tf` | `c9dc7ebfd17b4ea0e9bf9b50fee7af529405ab44ee4e08827d3a5bf06ef39962` |
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `infra/b6_integration_window.tf` | `1c5627768a5092fbb50d5fbe31a8e303d68450841d47c63e27330c784021f5a2` |
| `infra/eks.tf` | `37103846a11bcdb2e2aca5f81f221d6ee767675c77481b5451484447fd0aca7b` |
| `infra/variables.tf` | `b8455916219f0a6858a73e4d0e83a04b57947306baf251b7a2228d52abf78c79` |
| `pipeline/b6_integration_receipts.py` | `95b9c276c4b02f31174d14bf35d2d7badddad301123888a030a5e3581f1056e1` |
| `platform/decisions/B6-AWS-AUTH-2026-022-stage-a-and-window.json` | `486e8b53b490d46082bd23780282225e8729486af339febd2fae5efeb077a0a8` |
| `platform/decisions/B6-AWS-AUTH-2026-023-stage-a-and-window.json` | `efdd93eeb83ae51d29d61b444035b691974be2d62aa047b361376a6d33c9741d` |
| `platform/decisions/B6-AWS-CHANGE-PACKET-2026-022-fargate-boundary.md` | `bf2281e7246e8c08920a9daa6e7b68d90723efd285140938b886a07c1eb0cf50` |
| `platform/decisions/B6-AWS-CHANGE-PACKET-2026-023-probe-egress.md` | `503ca67317d2d668a56ad76e08377e5acbd953f7c1c83cef72364d55561b8775` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-002.json` | `831c164a6ca75017a3f9d11e38550cc52c7785b3abcb65f1963d82378995e244` |
| `platform/decisions/B6-WINDOW-VERIFIER-POLICY-2026-001.json` | `73eacb9cc6a9d9850098464f70380c92e25c46ac4aff7e4b67515c0269b5a236` |
| `platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml` | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| `platform/designs/B6-WINDOW-DESIGN-REVIEW-2026-001.md` | `b55198105f9a8de95191ad9032679e73bbb4f33df4f9a9c47e3359b3d759fd2a` |
| `platform/evidence/B6-5B-ECR-SCAN-RESULT-2026-001.json` | `f0364c098d8e7cbcc53b9fb0dddd46a8dda8295b420803e179106e326e160c83` |
| `platform/evidence/B6-BACKEND-TASK-ENI-SG-EGRESS-READBACK-2026-001.json` | `e34ef5d6bdc32fd794a03122bf65ddff8b482b2f1da7fa8c29514d7c5f0fc3f4` |
| `platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json` | `68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595` |
| `platform/evidence/B6-PACKET-2026-018-REFUSED-CREDENTIAL-LEGACY-VERSION-CARDINALITY.json` | `95735b36a225a3558cc95430258ec3d3b3a6ceb4976387498fa82004f5b3ca62` |
| `platform/evidence/B6-PACKET-2026-019-REFUSED-BRIDGE-PRINCIPAL.json` | `fcdf8fc4a1198cb38c1d905e83935698fddda6d0fdb5382da9e8e1a36c2e67e6` |
| `platform/evidence/B6-PACKET-2026-020-NOT-EXECUTED-PRINCIPAL-PREFLIGHT-CONDITION.json` | `64d9d6a29562535ce96137506cbac62d54286460d19dd6a23205a004d85394d5` |
| `platform/evidence/B6-PACKET-2026-020A-ATTEMPT-1-REFUSED-ENDPOINT-PLAN-GUARD.json` | `12c6c2cdfb72a88ef308d59a3ffac043a5330e7cb1c716b031e7662f798b8036` |
| `platform/evidence/B6-PACKET-2026-021-ATTEMPT-2-REFUSED-FARGATE-BOUNDARY.json` | `6f40490f9f8496036235085ebdc3b5b6042b5108753b6833245be5c293ed5b3b` |
| `platform/evidence/B6-PACKET-2026-022-STAGE-A-REFUSED-ECR-EGRESS.json` | `9245724747ebca8e2a6f286dc9abd057789be70288d5b61cbdb691bd2b972114` |
| `platform/evidence/B6-PACKET-2026-023-STAGE-A-REFUSED-SG-DESCRIPTION.json` | `4523a31c5bca84d4704228c7e48a782d0e04fa68cdd024e4b216c759e8c25616` |
| `platform/evidence/B6-R5-VERIFIER-AUDIT-2026-001.json` | `f4c55e8d31a65a9d10aa8d8e581be56732b1a88632df4d0c6d6417241b43413a` |
| `platform/evidence/B6-RENDERED-TERRAFORM-DESCRIPTIONS-2026-001.json` | `c931a63dc78ca7e4a1441a3fc481d3ad9e9aeea32e4a4be6e347a0f23148304c` |
| `platform/evidence/receipts/B6-2026-020A-BRIDGE/persistent_secret_bridge.json` | `7e5c14f0afb1c6d2e2e34d49b3a251f6d31a1ba126bf1da0f3d59154acc22db7` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a.json` | `8d4d15d78353b1026f2cc5c869b520e9c1fde0ef80c9f1ca4c2e9449461420d4` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_cleanup.json` | `d27ecdb5bfc5d8e9d3b39dc8272279d48324dd54c101ea5a8326a66c7e5bbef1` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_endpoints.json` | `a15275d56869a07761eb9a8e533735265bfc24e8a1c7f630494918176147ee9e` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_preflight.json` | `e55ba7575a59be97a5882e944d4d6cedc23a4c11c99e9484212f1f40f55a16f4` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_probe_1.json` | `08245c02446686680559b8481320642ec8a7ec10291fba1d0095cbebbb786d07` |
| `platform/evidence/receipts/B6-2026-022-STAGE-A-LIVE/stage_a_terraform.json` | `4dd1c4d80a26b8e76a64d5975143b146ae663f1ed0bc2b910d5cf7b1c63a51e5` |
| `platform/evidence/receipts/B6-2026-023-COLD/cold_rehearsal.json` | `16dd4f3ad29c8e8ac0fbc381bad6e16a35acfa5be29c0af7185f27ab67b461de` |
| `platform/evidence/receipts/B6-2026-023-STAGE-A-LIVE/stage_a.json` | `eb91429d52ad06a542cd42cdc499cc794e8c53de54d151d56d3b0ce17e75d55c` |
| `platform/evidence/receipts/B6-2026-023-STAGE-A-LIVE/stage_a_cleanup.json` | `1b086b750332580b376fa4b18eabbb265cfac31224dd6766889026a833f17c85` |
| `platform/evidence/receipts/B6-2026-023-STAGE-A-LIVE/stage_a_preflight.json` | `f1d569b7ba9911c6478191bb559546fad91f110ea10dd97816ed358b60fd9d2e` |
| `platform/evidence/receipts/B6-2026-023-STAGE-A-LIVE/stage_a_terraform.json` | `7791c0c1cc964e9e7d1d4a1e12a36bd8e79bb7da073dcc6528a71f237c4a056b` |
| `platform/evidence/receipts/B6-2026-024-COLD/cold_rehearsal.json` | `7a3f55e8b1960144584f72b1fc2f851327c760837b79c83df354ac2729d22a8d` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/k8s/b6-6/integration-window.yaml` | `ac3874f56bd0525cc39eaf1a786d3dd76eff67b75089d8146403338b6396351a` |
| `platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml` | `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897` |
| `platform/testdata/orchestrator/synthetic-file-request.wav` | `97592cb9f83e38439ea9d7ff1841e502bf1ef5b60be096dd91ac80a320e5402b` |
| `scripts/b6_6_bindings.py` | `813ea67b8b48147c8ab79e3aeafc28ba19fcee08edb02ab6d5f176eb1b693e4c` |
| `scripts/b6_6_cleanup.sh` | `1e4b8cee1b9cc3ececb5909d3927e89590d56043440bfe87ce23a6d6caaa926c` |
| `scripts/b6_6_cold_rehearsal.py` | `617e731e7a7ec0228a6f160bfdabd87e82dc853eb1efd4ca3891e0d7c485fe03` |
| `scripts/b6_6_credential.py` | `cbb4bb9b7b36f0d06aa88a0f6b14a3cae0ff82fcee60c2b5cd63ca2763413754` |
| `scripts/b6_6_deadline.py` | `5cd2bc2a34e3b7b2b0a2f7767379ade170cecfaa4ca4ebc564ac56e5668acd79` |
| `scripts/b6_6_fargate_probe.py` | `98405044cfc12213f5983a6382218eafa078b5eace18c31232a99a1d2b690207` |
| `scripts/b6_6_lbc_runtime.py` | `fd4294899a1d971f68e2b887677e8b703c66450e11a15b56c8e7d2854e282c8c` |
| `scripts/b6_6_lbc_tag_warning.py` | `e45472f297003b89d4487d9a740b40344ed965dbe3263a14cc63f0a083c26720` |
| `scripts/b6_6_manifest_slice.py` | `61f79a8cdf04f8e94aac2a07b7f0f1924d560c7b7b9d0a5f64973b1ec4d217fb` |
| `scripts/b6_6_operations.sh` | `a9421ba14e3e846c9f71247e380096f19b8f723b4387d411065212a6f6d8ca15` |
| `scripts/b6_6_persistent_secret_bridge.py` | `2f9ab3328d2b466702557853e21cab5e674d1ba22e3dcdef7c134480e497a083` |
| `scripts/b6_6_pre_endpoint_images.py` | `3ce744d886afe58e445bc415bf096bf74b9301d83f3b9c450452b144b4260491` |
| `scripts/b6_6_probe.py` | `fbd3f062b1aa845f78a2407a7077c738846d86a8c5c2f672e21fe48b8a107105` |
| `scripts/b6_6_probe_endpoints.py` | `a7c96f2487ef46de4e678d605b2fc2f53c3d3425ef6a69ebf0dea98713e60903` |
| `scripts/b6_6_runner.py` | `4933ecf5dbc8d79ef5d9c1daa29571727f31f7e71dedb517099ae80135703a74` |
| `scripts/b6_6_stage_a.py` | `7e416f2ff256c53990285a01a94780d862724aaa4a0d552900c1205174c89148` |
| `scripts/b6_6_wait_workers.py` | `078e9434cc3a7727a6b99f7eb8dc7e353bda5b458072d1df6e169ece31660af3` |
| `scripts/check_b6_6_persistent_secret_plan.py` | `d9f03d9a0fe67d259587403121bd4df19cbaf98e8e1150f03111b03ed1eebd20` |
| `scripts/check_b6_6_window_plan.py` | `5a84433bca2972b7cb84e400e1454aaca3313c848e3bd7cce0688b2d64cf4a0f` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_6_consolidated_window.py` | `3f333155fdc2cf7f6d1971d571292ffa755ca6b20ef8ab3e1f9925b3ce0ecdc9` |
| `tests/test_b6_6_fargate_boundary.py` | `9275dab60ae53de5aea06ef2724343b1936b13ec0c916070787732ecffd274d8` |
| `tests/test_b6_6_r5_verifier_audit.py` | `9eebb2068af7214982d9e066464991f48eb6fffa89a953c0dffb2c4b3492d70c` |
| `tests/test_b6_6_stage_a.py` | `a96bcb2ced80d21e64aa9e195e75d19f8169806217de01845713c38f955f865b` |

## Deviations

None. The instance string is corrected and the whole rendered-description
character class is enforced by one regex in both the live plan guard and the
zero-AWS cold rehearsal. Stage A's three-consecutive-pass gate and both locked
full-window attempts remain unchanged.

## Approval boundary

Independent review must bind the prepared repository commit, packet SHA-256,
cold receipt SHA-256, rendered-plan projection, regex implementation, exact
11-resource plan and allowance. Only then may the owner state:

> Approve B6 AWS change packet 2026-024 only, including one corrected Stage A
> qualification capped at 1,800 seconds and $0.50, followed only after three
> consecutive Stage A passes and zero-state cleanup by two full-window attempts
> capped at 4,500 seconds each and approximately $3.20 combined, within the
> existing $10 reservation.
