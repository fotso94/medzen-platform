# B6 AWS change packet 2026-027 — ALB and probe stability

Status: **DRAFT — AWAITING INDEPENDENT REVIEW, COST REVIEW AND OWNER APPROVAL**

Packet 2026-026 is terminal. Stage A passed three consecutive private probe
launches and cleaned up, but both subsequently authorized integration-window
attempts launched the ready check before the new ALB target had finished
registering. Both attempts refused, persisted receipts and returned CPU, GPU,
Kubernetes, ALB, endpoint and temporary Terraform state to zero.

This successor keeps that Stage A proof, places a real target-health gate before
the Fargate ready check, adds a second bounded retry inside the ready-check
container, and requests two fresh 4,500-second attempts. This draft authorizes
no AWS, Terraform, Kubernetes, secret, worker or service mutation.

## Immutable predecessor and current state

| Binding | Value |
|---|---|
| Packet 2026-026 | `c39130c456b36b128f3c52fab22a533243c9d8e235128c574c3c56f892634702` |
| Authorization 2026-026 | `2eeed4f4b59a74a385f07f4fbe77a843f61358caae3586cec494dc6106cdaeb1` |
| Terminal evidence | `platform/evidence/B6-PACKET-2026-026-TERMINAL-FARGATE-TARGET-READINESS-RACE.json` |
| Terminal evidence SHA-256 | `c82c51b6bd652000594c28d325a528ed415148149b670f3b075a3266cc2e9cc8` |
| Packet-026 attempts consumed | `2 / 2`, both `REFUSED` at `fargate_probe` |
| Stage A | `PASS`, three consecutive private task passes |
| Last cleanup | `PASS`, CPU/GPU `0 / 0`, temporary resources `0` |
| Production serving pointer | absent |
| `approved/asr/` changes | `0` |

All packet-026 decisions, receipts and terminal evidence remain unchanged.

## Cost reconciliation before allowance

`COST-REGISTRY-2026-005` was produced before this request and supersedes
revision 004 by reference. Read-only AWS Cost Explorer and CloudTrail checks
cover every execution from the revision-004 timestamp through packet-026
cleanup.

| Reconciled item | Amount |
|---|---:|
| Direct `g6.xlarge` + `m6i.large` usage before credits | `$0.5141739888` |
| Direct worker credits | `-$0.5141739888` |
| Direct worker net | `$0.00` |
| Relevant-service account-wide net upper bound | `$0.8998064216` |
| Recognized committed guardrail after reconciliation | `$64.4286064216` |
| Existing active reservation | `$10.00` |
| Committed plus reserved | `$74.4286064216` |
| Headroom under the `$300` ceiling | `$225.5713935784` |

AWS currently exposes neither active `Project` cost-allocation values nor
hourly/resource Cost Explorer data or a billing export, and the current daily
results remain estimated. The ledger therefore recognizes the entire account-
wide net charge for every service class these runs could touch, not zero and
not a fabricated exact project allocation. This deliberately overstates the
MedZen guardrail. Credits do not increase the allowance. Independent or
qualified financial review of this disclosed treatment is required with the
packet review.

Bindings:

- `platform/evidence/B6-COST-RECONCILIATION-2026-005.json`, SHA-256
  `3fa05595ca23b6d49a35a7ff12e54b78d1c6c121e89b365bd7c14b95267ad0a9`;
- `platform/finance/COST-REGISTRY-2026-005.json`, SHA-256
  `db7512d2d4ec2f54efa89e8527f9b310992393de191e38db0e7813d9279bcd2d`.

## Fix 1 — `alb_ready` is now the real launch gate

The 23-stage chain now orders `endpoints_ready → alb_ready → fargate_probe`.
The `alb_ready` stage applies the exact ingress and then, within a 900-second
bound:

1. waits for the internal ingress hostname;
2. resolves the exact `medzen-b6-window` internal application load balancer;
3. waits for `active` state and exactly one attached target group;
4. treats absent targets and only `Elb.InitialHealthChecking` or
   `Elb.RegistrationInProgress` as bounded retry states;
5. requires the same exact target set to be wholly `healthy` for three
   consecutive observations, ten seconds apart;
6. persists the `alb_ready` receipt before a Fargate task may launch.

Ambiguous identity, malformed targets, unknown states and terminal unhealthy
states refuse immediately. A timeout refuses as
`ALB_TARGET_STABLE_HEALTH_TIMEOUT`. The later route/tag verifier remains in
place; this gate changes sequencing, not the reviewed ALB boundary.

A read-only live `DescribeTargetHealth` response with two healthy targets is
recorded at
`tests/fixtures/aws/elbv2-describe-target-health-medzen-ehrbase-healthy.json`,
SHA-256
`ea75fe927ad48a9aaae9ddd15553fce1f28aaacb4c3fb07271af34c438b2a813`.
It supplements, rather than replaces, the existing real empty response and the
packet-026 `RegistrationInProgress` evidence. Capture record
`B6-AWS-READ-FIXTURE-CAPTURE-2026-003` has SHA-256
`2a9cf8406ce9ec685c7657ce9eae80d9159f994bcfaed083e7449948e3aa70f6`.
The fixture inventory is now 23 read APIs / 30 real responses / 0 uncovered.

## Fix 2 — in-container readiness retry remains mandatory

Stable target health does not remove the possibility of DNS propagation or a
first-connection reset from the exact private Fargate execution context. The
digest-pinned task definition and the live task-definition verifier therefore
bind the same exact inline program:

- at most `24` attempts;
- `10` seconds between attempts;
- `5` seconds per request;
- exit `0` on the first HTTP `200` whose JSON body contains `ready: true`;
- exit `21` after DNS-resolution exhaustion;
- exit `22` after connection, reset or timeout exhaustion;
- exit `23` after HTTP-status or response-body exhaustion.

The receipt maps those exits to
`PROBE_DNS_RETRIES_EXHAUSTED`,
`PROBE_CONNECT_RETRIES_EXHAUSTED`, or
`PROBE_BAD_STATUS_OR_BODY_RETRIES_EXHAUSTED`. Unknown exit codes remain
fail-closed. No response body, token, transcript, audio or service log enters a
receipt.

The prospective control record is
`platform/decisions/B6-ALB-PROBE-STABILITY-2026-001.json`, SHA-256
`ed8bb76cc09e5e397cfb19b0166e8566c532941a0b5975365a67627a132c216c`.

## Stage A reuse and execution staircase

Stage A is not rerun. Packet 2026-026 already proved the complete private image
pull/start path three consecutive times and cleaned all 11 qualification
resources to zero. The successor validator binds that packet, its authorization
and every Stage A receipt, including:

| Stage A receipt | SHA-256 |
|---|---|
| Aggregate `stage_a` | `e0ac64176a04ec9de2dbd9e633a3c4f93889f5c49958cf910019f0941ffd0c22` |
| Cleanup | `6442e398edd48621d8c4cd8bdfe3d5f2d46bd3bdf331f56e51c8433c83301979` |

Attempt 1 is allowed only from the independently reviewed clean commit and
after a fresh cold rehearsal matches the bound receipt. Attempt 2 is allowed
only if attempt 1 refuses and its cleanup receipt proves exact zero state. A
PASS terminates the packet; attempts cannot be resumed and unused seconds
cannot transfer between them.

Each attempt preserves the existing reviewed infrastructure boundary:

- controller plan: exactly `1 add / 0 change / 0 destroy`;
- endpoint/probe plan: exactly `13 add / 0 change / 0 destroy` with named
  resources in its receipt;
- all seven service/controller images pinned to scan-passed child manifests;
- at most two `m6i.large` CPU workers, one `g6.xlarge` GPU worker and one
  private Fargate probe task;
- dual deadline-first shutdown, receipt-per-stage, and automatic cleanup;
- cleanup deletes only the 14 permitted temporary Terraform resources and the
  synthetic Kubernetes window, then proves CPU/GPU and temporary state zero;
- the KMS-encrypted synthetic secret persists, is rotated at stage 0 and
  remains unreadable to the operator.

## Fresh cold rehearsal

The content-addressed receipt is
`platform/evidence/receipts/B6-2026-027-COLD/cold_rehearsal.json`, SHA-256
`5ba49e1c6c8a1d191e16b1c768bd49957b9ba11fa348cca56df704f34f6cdbab`.
It binds 142 non-self sources.

| Check | Result |
|---|---|
| Full-window simulated PASS / stage refusals | `1 / 23` |
| New-gate injected refusals | `4` |
| Target-health injections | real healthy stability, real empty timeout, initial-registration retry |
| In-container injections | exit `21 / 22 / 23`, each classified separately |
| Stage A simulated PASS / refusals | `1 / 7` |
| Recorded AWS fixture coverage | `23 APIs / 30 fixtures / 0 uncovered` |
| Real AWS / kubectl calls | `0 / 0` |
| AWS / Kubernetes mutations | `0 / 0` |
| Selected-payload regeneration | byte-identical |
| Focused suites | `71 passed, 0 failed` |
| Canonical repository suite | `1,425 passed, 0 failed, 0 skipped, 7 deselected` |
| Known warning | `1` Starlette/httpx deprecation warning |
| Terraform fmt / validate | `PASS / PASS` |

## Allowance request

This packet requests two new full-window attempts within the existing `$10`
reservation; it requests no new reservation and no Stage A spend.

| Control | Requested value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Reconciled committed guardrail | `$64.4286064216` |
| Existing reservation | `$10.00` retained |
| New reservation | `$0.00` |
| Full-window attempts | `2` maximum |
| Maximum per attempt | `4,500` seconds, non-transferable |
| Maximum requested worker seconds | `9,000` |
| Estimated compute for both attempts | approximately `$3.20` |
| Stage A runs | `0` |
| Cold rehearsal | required again before each attempt |

The request remains within the existing reservation and the `$300` aggregate
ceiling even without subtracting credits. The reservation is a guardrail, not
AWS authorization or a prediction that all `$10` will be billed.

## Prohibited operations

No production SSM pointer, `approved/asr/` object, model registration, MLflow
stage transition, fine-tune adoption, production traffic, PHI, real client
credential, real Bedrock call or Fish call is permitted. No unreviewed IAM,
Terraform, image, source, scope or safety-boundary change is permitted. Every
PASS or REFUSED stage receipt must persist before cleanup, and cleanup must
reach exact zero state before deadline disarm.

## Deviations

None. Both requested race controls and the cost-first reconciliation are
implemented directly. Stage A reuse is not a deviation: it is an immutable
passing prerequisite from packet 2026-026 and this packet changes only behavior
after that qualification boundary.

## Approval boundary

Independent review must bind the prepared clean commit, this packet SHA-256,
the cold-rehearsal SHA-256, `COST-REGISTRY-2026-005`, the exact retry program,
the stable-target gate and the reused packet-026 Stage A chain. A qualified
reviewer must accept the disclosed conservative cost attribution. Only then may
the owner state:

> Approve B6 AWS change packet 2026-027 only, including two new integration-
> window attempts capped at 4,500 seconds each and approximately $3.20
> combined, within the existing $10 reservation. Stage A is reused and is not
> authorized to rerun.
