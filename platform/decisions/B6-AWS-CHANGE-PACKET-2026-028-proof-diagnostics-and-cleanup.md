# B6 AWS change packet 2026-028 — proof diagnostics and status-keyed cleanup

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL**

Packet 2026-027 is terminal. Its first attempt proved the corrected ALB target-
health gate and private Fargate ready check, then refused at the first synthetic
file-conversation proof without retaining the failed assertion. Its second
attempt rotated the synthetic credential and then refused at stage zero without
retaining the exact later preflight reason. Because no deadline had been armed,
cleanup then waited for a deadline that could never exist. The waits were
stopped, the local token was removed, and an independent readback proved exact
zero state. The independent exit review accepted that evidence as `PASS`.

This successor fixes those three local control defects and requests two fresh,
non-transferable 4,500-second integration-window attempts. This draft itself
authorizes **no AWS, Terraform, Kubernetes, secret, worker or service mutation**.

## Immutable predecessor and current state

| Binding | Value |
|---|---|
| Packet 2026-027 | `ef2e0939f2975eaaf72eea4ba0e33908561093b233faab4c51dc177955decb84` |
| Authorization 2026-027 | `c6b6fbcc00717041912343e0c3209d91f7bdecdbc3e216f80d10112e1fa69f77` |
| Terminal evidence | `platform/evidence/B6-PACKET-2026-027-TERMINAL-FILE-PROOF-AND-STAGE0-REFUSALS.json` |
| Terminal evidence SHA-256 | `3bcc1775fee62310701346621a05ef06407c98e1a8cb41372f54f47556d3dec6` |
| Packet-027 attempts | `2 / 2` consumed; no continuation authorized |
| Packet-027 attempt 1 | ALB stability `PASS`, Fargate readyz `PASS`, file proof `REFUSED`, cleanup `PASS` |
| Packet-027 attempt 2 | stage zero `REFUSED`, compute `0`, independent cleanup readback `PASS` |
| Current CPU / GPU | desired `0 / 0`, instances `0 / 0` |
| Temporary endpoints / window ALB | `0 / 0` |
| Production serving pointer | absent |
| `approved/asr/` objects | `0` |
| Default Terraform plan | `NO_CHANGES` |

All packet-2026-027 decisions, receipts and evidence remain unchanged. Its
path-only evidence correction also remains additive and immutable.

## Control 1 — assertion-specific synthetic proof diagnostics

The probe client now returns a structured diagnostic on every refusal, and the
shell writes that diagnostic to the stage payload **before** returning the
nonzero code. The runner validates and copies only these bounded fields into
the immutable receipt:

- HTTP status, or `null` when no HTTP response exists;
- sanitized response body, maximum `1,024` UTF-8 bytes;
- truncation flag and SHA-256 of the original response body;
- exact failed assertion and its unique exit code;
- bounded safe error text, maximum `512` UTF-8 bytes;
- `synthetic_only: true` and `phi_present: false`.

The sanitizer redacts credentials and all transcript, reply-text, citation-text,
content, snippet, quote and audio values. It preserves diagnostic structure,
HTTP status, model versions, backend labels and counts. Raw audio, request
headers, credentials and service logs remain prohibited. Unknown, malformed,
oversized or out-of-scope diagnostics are not propagated and the stage remains
refused.

The file-conversation assertions and exact exits are:

| Exit | Failed assertion |
|---:|---|
| `31` | HTTP status is `200` |
| `32` | response is valid JSON |
| `33` | response is an object |
| `34` | `reply` is an object |
| `35` | `tts_backend` is `text_only` |
| `36` | `citations` is a list |
| `37` | citation count is exactly `3` |
| `38` | `model_versions` is an object |
| `39` | model-version keys are exact |
| `40` | registry snapshot matches the deployment snapshot |
| `41` | ASR version is `v0` |
| `42` | LLM version is the fake local provider |
| `43` | TTS model version is null in text-only mode |

Transport, token, WebSocket, cancellation, controlled-refusal and dependency-
refusal assertions also have distinct codes in the same immutable registry.

## Control 2 — exact stage-zero refusal reasons

Stage zero now persists its payload before returning nonzero and names the
failed invariant independently for:

1. source and authorization bindings;
2. local ephemeral-path absence;
3. fresh synthetic-credential rotation;
4. AWS account identity;
5. exact three-parameter test registry;
6. absent production serving pointer;
7. zero workload nodes;
8. zero synthetic pods;
9. absent window controller;
10. absent DRA driver; and
11. absent temporary endpoints.

Each assertion has a distinct exit code and a bounded exact safe error. This
stage runs before workers, models or audio; neither credentials nor plaintext
secret values may enter its receipt.

## Control 3 — cleanup follows actual deadline state

Cleanup proves CPU and GPU zero first, then reads the immutable deadline receipt
as `PASS`, `REFUSED` or `ABSENT` and inspects the scheduled actions that actually
exist:

| Receipt / action state | Required cleanup behavior |
|---|---|
| `PASS`, exact actions present | delete those exact actions after zero; require zero actions |
| `PASS`, actions already absent | record already absent; require zero actions |
| `REFUSED` or `ABSENT`, zero actions | finish immediately as already disarmed |
| `REFUSED`, one exact partial action | delete only that exact action after zero; require zero |
| unknown receipt state or unexpected action | refuse immediately |
| CPU or GPU not proven zero | bounded retry, then refuse on timeout |

This removes the fixed wait for a nonexistent deadline while preserving the
deadline-first safety boundary for attempts that reach the deadline stage.

The prospective control record is
`platform/decisions/B6-PROOF-DIAGNOSTIC-CLEANUP-2026-001.json`, SHA-256
`48c5fcfa8121f5f048753cd9771ce34acf5c03d4e7222348036c72c4ce5b0b6f`.
The prospective receipt rule is `platform/runtime-receipt-policy-v3.yaml`,
SHA-256 `049dc6336f2507f037f6bbcc7e1784db66f0c274227d833bc761a1b7ab197a54`.
Both are non-retroactive.

## Unchanged execution boundary

The successful packet-2026-026 Stage A qualification is reused and is not
authorized to rerun. Each full attempt retains the existing reviewed boundary:

- exact 23-stage order and receipt-per-stage runner;
- deadline armed before worker scale-up, maximum `4,500` seconds;
- at most two `m6i.large` CPU workers and one `g6.xlarge` GPU worker;
- seven digest-pinned, scan-passed images;
- all Kubernetes workloads running before private endpoint DNS redirection;
- controller plan `1 add / 0 change / 0 destroy`;
- endpoint/probe plan `13 add / 0 change / 0 destroy`;
- stable internal ALB target health before the private Fargate ready check;
- 24-attempt in-container ready retry with DNS/connect/bad-status exits;
- synthetic credentials rotated at stage zero and never readable by the operator;
- cleanup of the 14 temporary Terraform resources and synthetic Kubernetes
  window, followed by exact zero-state proof.

Attempt 2 may start only after attempt 1 refuses, its cleanup receipt passes and
independent readback confirms zero state. A successful attempt terminates the
packet. Seconds are not transferable between attempts.

## Fresh cold rehearsal

The immutable receipt is
`platform/evidence/receipts/B6-2026-028-COLD/cold_rehearsal.json`, SHA-256
`762b03d4fdccf27796f345652d4b9b67215927b963687deeaa4f03c7b7edb3b8`.
Two independent generations produced the identical canonical payload SHA-256
`6ec8659180ec103b2d8ad4952a0487e76e70a6137d6155882b4efa49868d44bd`.

| Check | Result |
|---|---|
| Full simulated PASS | `1` |
| Stage-level injected refusals | `23` |
| Existing ALB/Fargate gate injections | `4` |
| File-proof assertion injections | `13`, all distinct exits |
| Pre-deadline cleanup injections | `2` |
| Total injected refusals | `42` |
| Stage A simulated PASS / refusals | `1 / 7` |
| Sanitizer redaction/truncation injection | `PASS` |
| Recorded AWS fixture coverage | `23 APIs / 30 fixtures / 0 uncovered` |
| Real AWS / kubectl calls | `0 / 0` |
| AWS / Kubernetes mutations | `0 / 0` |
| Focused suites | `93 passed, 0 failed` |
| Canonical repository suite | `1,454 passed, 0 failed, 0 skipped, 7 deselected` |
| Known warning | `1` Starlette/httpx deprecation warning |
| Terraform fmt / validate | `PASS / PASS` |

## Allowance request

`COST-REGISTRY-2026-005` remains the latest reconciled ledger. Packet-2026-027
attempt 2 used zero compute; attempt 1 remained within its 4,500-second guard.
AWS billing for that latest window may still lag, so this packet does not invent
an exact attributable amount or reduce the recognized guardrail. The existing
`$10` reservation remains the governing ceiling and no new reservation is
requested.

| Control | Requested value |
|---|---:|
| Aggregate project ceiling | `$300.00` |
| Recognized committed guardrail | `$64.4286064216` |
| Existing active reservation | `$10.00` |
| New reservation | `$0.00` |
| Fresh full-window attempts | `2` maximum |
| Maximum per attempt | `4,500` seconds, non-transferable |
| Maximum requested worker seconds | `9,000` |
| Estimated compute for both attempts | approximately `$3.20` |
| Stage A runs | `0` |
| Fresh cold rehearsal | required before each attempt |

Bindings remain:

- `platform/finance/COST-REGISTRY-2026-005.json`, SHA-256
  `db7512d2d4ec2f54efa89e8527f9b310992393de191e38db0e7813d9279bcd2d`;
- `platform/evidence/B6-COST-RECONCILIATION-2026-005.json`, SHA-256
  `3fa05595ca23b6d49a35a7ff12e54b78d1c6c121e89b365bd7c14b95267ad0a9`.

## Prohibited operations

No production SSM pointer, `approved/asr/` object, model registration, MLflow
stage transition, fine-tune adoption, production traffic, PHI, real client
credential, real Bedrock call or Fish call is permitted. No unreviewed IAM,
Terraform, image, source, scope or safety-boundary change is permitted.

## Deviations

None. All three exit-review directives are implemented directly. The response-
body exception is narrower than the owner authorization because it additionally
redacts synthetic transcript, reply and citation content while preserving the
diagnostic response structure and failed assertion.

## Approval boundary

Independent review must bind the prepared clean commit, this packet SHA-256,
the cold-rehearsal SHA-256, the policy/control records, all assertion exit codes,
the status-keyed cleanup behavior and the requested allowance. Only after a
review `PASS` may the owner state exactly:

> Approve B6 AWS change packet 2026-028 only, including two non-transferable
> 4,500-second attempts within the existing $10 reservation.
