# B6 AWS change packet 2026-025 — per-rule endpoint verifier

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Packet 2026-024 is terminal. Its Stage A preflight and exact 11-resource
Terraform apply passed, but the endpoint verifier refused before any probe task
ran. Automatic cleanup returned every temporary resource and CPU/GPU capacity
to zero. The two full-window attempts remained locked and unused.

Independent live investigation conclusively confirmed the cause: EC2
`DescribeSecurityGroups` merged two egress rules sharing protocol and ports
into one `IpPermissionsEgress` object, while the verifier required two objects.
This successor moves egress verification to the 1:1 per-rule API, binds real
AWS response fixtures, and retains exact safe pre-model exception text.

This draft authorizes no AWS, Terraform, Kubernetes, worker, secret or service
mutation.

## Immutable predecessor

| Binding | Value |
|---|---|
| Packet 2026-024 | `2b9b17452e14bf4566b7d48f8578dcbad84ec8786d4ad149761b5d5cb49c4144` |
| Authorization 2026-024 | `5dbc93a016a975dcc35a732b1e1a341361ff18707d3655632332486501b7c6d8` |
| Refusal evidence | `platform/evidence/B6-PACKET-2026-024-STAGE-A-REFUSED-ENDPOINT-VERIFIER-SHAPE.json` |
| Refusal evidence SHA-256 | `5f529aa18145d2647047408ef1ebd5fba9c9038e317ea11507931e843645cb4a` |
| Evidence commit | `e63409b35dc7771bb6a0cdf9d1eaac3d9d6be1ea` |
| Terminal stage | `stage_a_endpoints: REFUSED` |
| Stable probe passes | `0 / 3` |
| Full-window attempts unlocked / consumed | `0 / 0` |
| Cleanup | `PASS`, CPU/GPU `0 / 0`, temporary resources `0` |

The predecessor evidence remains immutable. Its root-cause confidence field is
not rewritten; this packet records the later conclusive investigation.

## Conclusive live-response finding

The first standing fixture set was captured read-only from
`sg-070fc00321934eacb` in account `558069890522`, region `eu-central-1`:

| API | Observed egress shape | Fixture SHA-256 |
|---|---:|---|
| `ec2:DescribeSecurityGroups` | `1` merged permission object | `2f9129d630cadc5f2915a5bf8c9b9885096fb39b43bfbe5125fab23d71c5a49a` |
| `ec2:DescribeSecurityGroupRules` | `2` individual rules | `96dd135dc918f7b7de260d8aa92df3bd7ffd184b8796ab1b90e43933927af469` |

The merged object contains both an IPv4 destination and a referenced security
group. The per-rule response returns them separately. The same fixtures also
record the protocol `-1` quirk: `DescribeSecurityGroupRules` returns
`FromPort=-1` and `ToPort=-1`, while `DescribeSecurityGroups` omits both fields.

Capture evidence:
`platform/evidence/B6-AWS-READ-FIXTURE-CAPTURE-2026-001.json`, SHA-256
`6bd723750bcf006ea760d78617b21781114c5a63d9943d91b5c3e2ce2cbe876d`.
The capture made zero AWS mutations and contains no credentials or PHI.

## Per-rule verifier correction

Endpoint ingress, tags and network identity continue to use
`DescribeSecurityGroups` and `DescribeVpcEndpoints` where their shapes are
appropriate. Egress policy verification now uses only
`DescribeSecurityGroupRules`, filtered by the exact temporary endpoint security
group ID. Pagination is followed and malformed or repeated continuation tokens
fail closed.

The policy requires exactly two egress rules and no others:

1. one `tcp/443` rule whose `ReferencedGroupInfo.GroupId` equals the exact
   endpoint security group, with no prefix-list or CIDR destination;
2. one `tcp/443` rule whose `PrefixListId` equals the packet-created S3 gateway
   endpoint prefix list, with no referenced-group or CIDR destination.

Each rule must have a non-empty `sgr-` identity and the exact group ID. Any
additional egress rule, missing destination, wrong destination, CIDR, protocol,
port, malformed page or ambiguous direction refuses Stage A. The implementation
does not attempt to un-merge `IpPermissionsEgress` objects.

Implementation:
`scripts/b6_6_probe_endpoints.py`, SHA-256
`f7015576c769a76f24b62aea15a1e173d5e9ec711d690f257b1a6adc4c2311f2`.

## Standing AWS fixture-fidelity rule

`B6-AWS-READ-FIXTURE-FIDELITY-2026-001` is now the standing rule for executable
AWS read-response verifiers. Their parser boundary must be regression-tested
against an immutable real AWS response; a hand-written client-response
dictionary cannot substitute for a captured response. Pure policy evaluators
may use domain-level values only after the recorded-response parser boundary is
covered.

The cold rehearsal verifies both fixture hashes, the `2 -> 1` merge, the
per-rule count and the protocol `-1` optional-port difference with zero live
calls. Production responses that cannot normalize into the recorded domain
shape fail closed.

Standing decision:
`platform/decisions/B6-AWS-READ-FIXTURE-FIDELITY-2026-001.json`, SHA-256
`4d048375b6b17d9e84ec29babbf5bb8b007b74d6736032a424293c541d8ee822`.

## Exact safe pre-model refusal details

Every Stage A exception path now persists `safe_exception_text` before cleanup:
normal stage refusal, unexpected exception, top-level refusal and cleanup
recovery refusal. This includes the exact `EndpointRefusal` text that was lost
in packet 2026-024. Stage A contains no model, audio, request or client payload.

The existing receipt engine still rejects forbidden keys, credential-like
values and bearer/authorization material. Historical receipts and
`platform/runtime-receipt-policy-v2.yaml` remain unchanged.

Implementation:
`scripts/b6_6_stage_a.py`, SHA-256
`5a6cb493eefc19a6bed4612929aceabbac1d4093cf51c1e65da2f4bfc93b2c47`.

## Regression coverage

The tests prove:

- recorded real responses expose two individual egress rules but one merged
  permission object;
- the runtime reader calls `DescribeSecurityGroupRules`, not the merged egress
  API;
- exactly one self-reference plus one S3 prefix-list rule passes;
- removing either destination refuses;
- unexpected destinations, extra rules, protocol `-1`, wrong ports and
  malformed response shapes refuse;
- the protocol `-1` port-field difference remains bound to real fixtures;
- exact safe `EndpointRefusal` text is persisted before cleanup;
- all seven Stage A refusal points retain exact safe text and complete cleanup.

## Plan and execution boundary carried unchanged

The fresh read-only Stage A plan passed at exactly
`11 add / 0 change / 0 destroy`. Its temporary binary SHA-256 is
`e9e65b2d8db9e4878e5dfa62561ab52bf4001c80ac08a449fa86959f8fbcda6a`.
The binary is not committed because Terraform plan data can contain sensitive
state. No apply was run while preparing this packet.

Stage A remains one 1,800-second, `$0.50` maximum isolated qualification. It
cannot mutate EKS workers, GPU capacity, services, controller, ALB or the client
secret. Exactly three consecutive private probe tasks must pass; the first
refusal terminates Stage A and cleanup always runs. Only a complete three-pass
chain plus verified zero-state cleanup unlocks either full window.

Both full-window attempts remain capped at 4,500 seconds each and are
non-transferable. Attempt 2 exists only after attempt 1 refuses and cleanup
proves zero state. A PASS ends the packet. All R1-R7 controls, dual deadline,
image digest pins, credential rotation, 900-second endpoint readiness gate and
complete 23-stage receipt sequence carry forward unchanged.

No PHI, production traffic, real Bedrock or Fish call, model adoption,
`approved/asr/` write or production SSM pointer is permitted.

## Fresh cold rehearsal and validation

| Check | Result |
|---|---|
| Cold receipt | `platform/evidence/receipts/B6-2026-025-COLD/cold_rehearsal.json` |
| Cold receipt SHA-256 | `5e2f4897c95d1a1ac37788c26fe5d97ea5f7819f07eb932dd9fcbf5b1ad1cdd1` |
| Scenario-results SHA-256 | `beeb3296097a029174070c23359adb76a8907bdef447481f4372671094ef591e` |
| Full-window simulated PASS / injected refusals | `1 / 23` |
| Stage A simulated PASS / injected refusals | `1 / 7` |
| Real AWS / kubectl calls in cold rehearsal | `0 / 0` |
| AWS / Kubernetes mutations in cold rehearsal | `0 / 0` |
| Recorded AWS fixture check | `PASS`, `2` rules -> `1` merged object |
| Protocol `-1` optional-port fixture | `PASS` |
| Focused suites | `51 passed, 0 failed` |
| Canonical repository suite | `1,412 passed, 0 failed, 0 skipped, 7 deselected` |
| Known warning | `1` Starlette/httpx deprecation warning |
| Terraform fmt / validate | `PASS / PASS` |
| Read-only Stage A plan guard | `PASS`, exact `11 / 0 / 0` |

The cold receipt contains the authoritative exact source-hash map for 83
non-self packet-bound files in `.payload.runner_source_hashes`; its own SHA-256
above is the 84th binding. The binding validator requires the authorization
record to repeat the exact 84-entry map and checks every file before any live
operation. Key binding hashes are:

| Source | SHA-256 |
|---|---|
| `scripts/b6_6_bindings.py` | `2eac7aa00483830939068272dfa192d479e40192a03bec47e26a93910d867445` |
| `scripts/b6_6_probe_endpoints.py` | `f7015576c769a76f24b62aea15a1e173d5e9ec711d690f257b1a6adc4c2311f2` |
| `scripts/b6_6_stage_a.py` | `5a6cb493eefc19a6bed4612929aceabbac1d4093cf51c1e65da2f4bfc93b2c47` |
| `scripts/b6_6_cold_rehearsal.py` | `3a03b191bde8dda5bdad54eb8c5985ae49d74ecf14c8550dbea85a91fabc71dc` |
| `scripts/b6_6_runner.py` | `f9f96af791cfd2e8c66e063f837976b5f41f5216c23c87542f5a9629db2d918a` |
| `tests/test_b6_6_stage_a.py` | `191a7ca52329a4c7d92c68714d53de2bd26ae92d8a9c317fadbbaec6b3c82e38` |

## Allowance continuity request

Packet 2026-024 consumed its one Stage A run and is not retriable. It consumed
no full-window attempt. Packet 2026-025 requests one corrected Stage A and the
same two contingent full-window attempts inside the existing `$10` reservation.

| Control | Requested value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Existing reservation | `$10.00` retained pending reconciliation |
| New reservation | `$0` |
| Corrected Stage A runs | `1` maximum |
| Corrected Stage A ceiling | `1,800` seconds and `$0.50` |
| Stage A stability proof | `3` consecutive private tasks |
| Stage A EKS/GPU/service mutations | `0 / 0 / 0` |
| Full-window attempts | `2` maximum, locked until Stage A PASS |
| Maximum per window | `4,500` seconds, non-transferable |
| Maximum requested worker seconds | `9,000` |
| Estimated compute for both windows | approximately `$3.20` |
| New Stage A plus window ceiling | `$3.70` |
| Conservative exposure including packet 022-024 Stage A maxima and this request | `$5.20`, within `$10` |

## Deviations

None. The verifier uses the required per-rule API, real response fixtures are
the standing parser boundary, exact safe pre-model errors are durable, and the
three-pass Stage A staircase plus both locked window attempts are unchanged.

## Approval boundary

Independent review must bind the prepared repository commit, packet SHA-256,
cold receipt SHA-256, both raw fixture hashes, the standing fixture-fidelity
decision, exact per-rule policy, safe receipt text, exact 11-resource plan and
allowance. Only then may the owner state:

> Approve B6 AWS change packet 2026-025 only, including one corrected Stage A
> qualification capped at 1,800 seconds and $0.50, followed only after three
> consecutive Stage A passes and zero-state cleanup by two full-window attempts
> capped at 4,500 seconds each and approximately $3.20 combined, within the
> existing $10 reservation.
