# B6 AWS change packet 2026-026 — empirical probe verifier

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Packet 2026-025 is terminal. Stage A created its exact temporary boundary, but
the endpoint verifier refused because it expected `PrefixListId` on an S3
gateway endpoint response. AWS does not return that field there. No probe task
or full-window attempt ran, and automatic cleanup returned temporary resources
and CPU/GPU capacity to zero.

This successor obtains the S3 managed prefix list through
`DescribePrefixLists`, verifies the gateway only by its real fields, replaces
redundant network-shape verification with the three-probe empirical gate, and
binds a recorded real response for every AWS read API interpreted by either
runner. This draft authorizes no AWS, Terraform, Kubernetes, worker, secret or
service mutation.

## Immutable predecessor

| Binding | Value |
|---|---|
| Packet 2026-025 | `a96752124a7b13e72f4be05ffbc44d6c4ab93cd31342308e6858c3bd9280e46b` |
| Authorization 2026-025 | `8d3dc8dfed1ad25c443802d4348d5833c33e83501a624f428aef84e24b34ce55` |
| Refusal evidence | `platform/evidence/B6-PACKET-2026-025-STAGE-A-REFUSED-S3-ENDPOINT-API-SHAPE.json` |
| Refusal evidence SHA-256 | `fd96de71fa26fe8d0afd2f753ba8ed7ef62e44823907c68b9ddd3cae0230385a` |
| Evidence commit | `0ffbbe2e6fec6933c530248ef26558925faa1cae` |
| Terminal stage | `stage_a_endpoints: REFUSED` |
| Stable probe passes | `0 / 3` |
| Full-window attempts unlocked / consumed | `0 / 0` |
| Cleanup | `PASS`, CPU/GPU `0 / 0`, temporary resources `0` |

The predecessor packet, authorization, receipts and refusal evidence remain
unchanged.

## Correct AWS APIs and reduced verifier

The runtime now resolves `com.amazonaws.eu-central-1.s3` with
`ec2:DescribePrefixLists`; it never expects the managed prefix-list ID on an
S3 gateway endpoint. The gateway endpoint is verified only by its real
security-relevant fields: `State`, `RouteTableIds` and `PolicyDocument`, after
basic exact endpoint identity and existence selection.

The endpoint verifier deliberately makes zero connectivity-shape assertions.
It no longer asserts interface subnets, attached security groups, private-DNS
shape, security-group rule grouping, task egress count or Terraform expression
wiring. Those properties remain in the Terraform design, but three consecutive
private Fargate tasks must each pull the exact digest through the temporary ECR
and S3 endpoint path, start the application process and exit zero. That is the
single empirical connectivity proof.

Checks retained because a successful pull cannot prove them are:

- exact temporary endpoint and endpoint-security-group existence and identity;
- `available` state for all three endpoints;
- exact least-privilege policy documents for ECR API, ECR DKR and S3;
- the one approved S3 gateway route table;
- the AWS-managed S3 prefix-list identity returned by `DescribePrefixLists`.

The prospective record is
`platform/decisions/B6-ENDPOINT-VERIFIER-2026-002-empirical.json`. The R5 audit
is `platform/evidence/B6-R5-VERIFIER-AUDIT-2026-002.json`. Historical verifier
records are preserved.

## Complete recorded-response fixture boundary

A read-only inventory of the entire Stage A and conditionally unlocked
full-window runner found **23 distinct explicit AWS read APIs** across nine
executable sources. It binds **29 recorded real responses**, with at least one
fixture for every API and **zero uncovered reads**. A static source audit
refuses the cold rehearsal if a read method or shell AWS read is added, removed
or renamed without updating the inventory and its real fixture.

Coverage includes Auto Scaling, EC2, ECS, EKS, ELBv2, IAM, Secrets Manager,
SSM and STS. The fixtures contain no secret plaintext, credentials, PHI, audio
or client request bodies. `GetSecretValue` is represented only by the real
expected `AccessDeniedException`; SSM fixtures contain only the non-serving
synthetic test registry. Terraform-provider internal reads are not parsed by
the application runner and remain controlled by the exact action/resource plan
guards.

Capture evidence:
`platform/evidence/B6-AWS-READ-FIXTURE-CAPTURE-2026-002.json`.

| Fixture control | Result |
|---|---:|
| Explicit AWS read APIs | `23` |
| Recorded real-response fixtures | `29` |
| Uncovered APIs | `0` |
| Live AWS calls during fixture audit | `0` |
| AWS mutations during capture | `0` |
| S3 prefix-list source | `ec2:DescribePrefixLists` |
| `PrefixListId` expected on gateway response | `false` |

## Stage A and locked windows

Stage A remains one isolated qualification with a 1,800-second and `$0.50`
ceiling. Its Terraform boundary remains exactly `11 add / 0 change / 0
destroy`, with no EKS worker, GPU, service, ALB, controller or client-secret
mutation. The freshly generated read-only plan passed the machine guard and
had temporary binary SHA-256
`b3c894e7ff2ecf3d37246dbe0ccddfacc4555bae1dce3e62cedf35d75d14218a`;
the binary was deleted after inspection because plan files can contain
sensitive state.

The first probe refusal terminates Stage A. Exactly three consecutive probes
plus successful zero-state cleanup are required to unlock either full-window
attempt. Both attempts remain capped at 4,500 seconds each, are
non-transferable, and remain locked until Stage A passes. Attempt 2 is allowed
only after attempt 1 refuses and cleanup proves zero state; a PASS ends this
packet.

All prior R1-R7 controls remain: digest pins, dual deadline, persistent-secret
rotation without plaintext read, endpoint readiness wait, receipt-per-stage,
safe pre-model exception text, 23-stage window receipts, and cleanup before
deadline disarm. No PHI, production traffic, real Bedrock/Fish invocation,
model adoption, `approved/asr/` write or production SSM pointer is permitted.

## Fresh cold rehearsal and validation

The final content-addressed cold receipt is attached at
`platform/evidence/receipts/B6-2026-026-COLD/cold_rehearsal.json`, SHA-256
`e4ba622c2f757944d26e2a3383f6c84644ffb17acab1206950a201e16c0702b8`.
It binds the exact 124 non-self sources used by the authorization validator.

| Check | Result |
|---|---|
| Full-window simulated PASS / injected refusals | `1 / 23` |
| Stage A simulated PASS / injected refusals | `1 / 7` |
| Empirical connectivity gate | `3` consecutive probe passes required |
| Endpoint network-shape assertions | `0` |
| Exact policy documents / gateway route assertion | `3 / 1` |
| Recorded AWS API fixtures | `23 APIs / 29 fixtures / 0 uncovered` |
| Real AWS / kubectl calls in cold rehearsal | `0 / 0` |
| AWS / Kubernetes mutations in cold rehearsal | `0 / 0` |
| Focused suites | `50 passed, 0 failed` |
| Canonical repository suite | `1,409 passed, 0 failed, 0 skipped, 7 deselected` |
| Known warning | `1` Starlette/httpx deprecation warning |
| Terraform fmt / validate | `PASS / PASS` |
| Read-only Stage A plan guard | `PASS`, exact `11 / 0 / 0` |

## Allowance continuity request

Packet 2026-025 consumed its single Stage A run but no full-window attempt.
Packet 2026-026 requests one corrected Stage A and carries forward both locked
window attempts inside the existing `$10` reservation; it requests no new
reservation.

| Control | Requested value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Recognized committed guardrail | `$63.5288` |
| Existing reservation | `$10.00` retained |
| New reservation | `$0` |
| Stage A runs | `1` maximum |
| Stage A ceiling | `1,800` seconds and `$0.50` |
| Stage A stability proof | `3` consecutive private tasks |
| Stage A EKS/GPU/service mutations | `0 / 0 / 0` |
| Full-window attempts | `2` maximum, locked until Stage A PASS |
| Maximum per window | `4,500` seconds, non-transferable |
| Maximum requested worker seconds | `9,000` |
| Estimated compute for both windows | approximately `$3.20` |
| New Stage A plus both windows | `$3.70` maximum |
| Conservative packet 022-026 Stage A maxima plus this request | `$5.70`, within `$10` |

## Deviations

None. All three owner requirements are implemented directly: the correct
prefix-list API and gateway fields, recorded real responses for every runner
AWS read API, and the R5 reduction to security policy/basic existence plus the
three-pass empirical connectivity gate. The staircase and both locked attempts
are unchanged.

## Approval boundary

Independent review must bind the prepared repository commit, this packet's
SHA-256, the final cold-rehearsal SHA-256, the complete 23-API/29-fixture map,
the zero-network-shape R5 audit, exact 11-resource plan and allowance. Only then
may the owner state:

> Approve B6 AWS change packet 2026-026 only, including one corrected Stage A
> qualification capped at 1,800 seconds and $0.50, followed only after three
> consecutive Stage A passes and zero-state cleanup by two full-window attempts
> capped at 4,500 seconds each and approximately $3.20 combined, within the
> existing $10 reservation.
