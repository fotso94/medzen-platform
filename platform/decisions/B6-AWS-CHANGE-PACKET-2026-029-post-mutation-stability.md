# B6 AWS change packet 2026-029 — post-mutation stability

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL**

Packet 2026-028 is terminal. Both authorized attempts rotated the synthetic
credential successfully, but their immediate one-shot readback observed stale
Secrets Manager state and refused at stage zero. Later read-only checks proved
the secret healthy with exactly one `AWSCURRENT` and one `AWSPREVIOUS` version.
Both attempts used zero worker and GPU seconds and completed cleanup at exact
zero state.

This successor corrects that race and makes bounded stable observation a
standing rule for **every** asynchronous post-mutation verification in the
B6.6 runner. It requests two fresh, non-transferable 4,500-second window
attempts. This draft itself authorizes **no AWS, Terraform, Kubernetes, secret,
worker, service or production mutation**.

## Immutable predecessor

| Binding | Value |
|---|---|
| Packet 2026-028 | `6d5ed6d0af1324f1be8234bdd5eae22dff22253d0ec299acacde162bfa3197fe` |
| Authorization 2026-028 | `0a5326580cd5ec9576cadf281d725c72ba02ce72d8e881defcd46607b8e8973b` |
| Terminal evidence | `platform/evidence/B6-PACKET-2026-028-TERMINAL-STAGE0-CREDENTIAL-CONSISTENCY-REFUSALS.json` |
| Terminal evidence SHA-256 | `71351877c2abed9eddb739dcaf8b431aab77eb1975c4b2955b8061319b2d2647` |
| Attempts | `2 / 2` consumed; no continuation under packet 2026-028 |
| Attempt 1 / 2 | stage zero `REFUSED` / `REFUSED`; cleanup `PASS` / `PASS` |
| Worker / GPU seconds | `0 / 0` |
| Incremental compute | `$0.00` |
| Current CPU / GPU | desired `0 / 0`, instances `0 / 0` |
| Temporary endpoints / window ALB | `0 / 0` |
| Production serving pointer | absent |
| `approved/asr/` objects | `0` |
| Default Terraform plan | `NO_CHANGES` |

All packet-2026-028 records and receipts remain immutable. This packet
supersedes only its exhausted execution authority.

## Exact credential visibility correction

Stage zero still performs exactly one credential rotation:

1. Generate 32 random bytes once and encode one 43-character bearer token.
2. Write the token once to the existing mode-`0600` local ephemeral path.
3. Construct the canonical synthetic secret value and use its SHA-256 as the
   `ClientRequestToken` and expected Secrets Manager version ID.
4. Call `PutSecretValue` exactly once with `AWSCURRENT`.
5. Poll `ListSecretVersionIds` for at most `120` seconds, every `5` seconds.
6. Require the exact returned version ID to be the unique `AWSCURRENT` version
   with no additional stage for three consecutive observations.
7. Reset the stability counter on every missing, stale, ambiguous or changed
   observation. Timeout refuses.

A stale read never creates new material and never makes a second
`PutSecretValue` call. The operator plaintext-read denial and all existing
token-shape, hash and file-durability checks remain unchanged.

The standing control record is
`platform/decisions/B6-POST-MUTATION-STABILITY-2026-001.json`, SHA-256
`7b2111261ac51122f5f1ae78e34d8d4fcfff677ada8788918e8cf3e429393995`.

## Whole-runner post-mutation audit

The audit first enumerated the obvious credential, deadline, worker,
deployment, endpoint, ALB, probe and cleanup paths. It then found and corrected
three additional one-shot paths: the full seven-pod image-residency proof, the
controller tag-result classification, and final service/Ingress isolation.

| Audit result | Count |
|---|---:|
| Post-mutation verification paths | `31` |
| Corrected in this change | `30` |
| Already compliant before this change | `1` |
| One-shot paths remaining | `0` |
| Minimum stable observations | `2` |
| Deviations | `0` |

The only pre-existing compliant path was the ALB target-health gate, already
requiring three stable observations. All other paths now use two or three
consecutive observations within an explicit time bound. A pending response,
read error or changed projected state resets the counter. Unknown or malformed
state refuses.

The machine-readable audit is
`platform/evidence/B6-POST-MUTATION-VERIFIER-AUDIT-2026-001.json`, SHA-256
`f054dcc665bbd116290891f0417577f41e38cc80e0d66eed16005e09f40c7cf2`.

## Stable-observation coverage

The standing rule now covers:

- exact Secrets Manager version visibility, local token durability and stable operator denial;
- deadline creation and deletion;
- CPU/GPU worker registration and zero state;
- DRA, RAG, ASR, TTS, LLM, orchestrator and controller readiness;
- workload and full pre-endpoint image residency;
- temporary endpoint creation and deletion;
- ALB target health, full ALB shape and tag-result classification;
- Fargate task terminal state and local port-forward readiness;
- RAG drill removal and restoration;
- final isolation state;
- ECS, ALB, Kubernetes, Terraform, endpoint and local-file cleanup.

Receipts retain the stable-observation and poll counts. The original 23-stage
order and write-once PASS/REFUSED receipt wrapper are unchanged.

## Fixture provenance and stale-to-current injection

The existing recorded-real `ListSecretVersionIds` response remains the API-
shape authority. Two sanitized regression projections model the sequence
observed in packet 2026-028: stale old-current state followed by the exact new
version as current. The test substitutes the exact created version only in
memory; it generates one credential and performs no AWS call.

The provenance record is
`platform/evidence/B6-SECRETSMANAGER-VISIBILITY-FIXTURE-PROVENANCE-2026-001.json`,
SHA-256
`5d8d0cf679bb7ac13b863ca7ed1e33065ba7097649133c682adf834b79a57df1`.
It explicitly does not claim the sanitized projections are raw live responses.

## Unchanged execution boundary

The successful packet-2026-026 Stage A qualification is reused and may not be
rerun. Each requested full attempt retains the reviewed boundary:

- exact 23-stage order and receipt-per-stage runner;
- deadline armed before worker scale-up, maximum `4,500` seconds;
- at most two `m6i.large` CPU workers and one `g6.xlarge` GPU worker;
- seven digest-pinned, scan-passed images;
- all workloads and images present before private endpoint DNS redirection;
- controller plan `1 add / 0 change / 0 destroy`;
- endpoint/probe plan `13 add / 0 change / 0 destroy`;
- stable internal ALB target and private Fargate ready check;
- synthetic file, WebSocket, cancellation and failure-drill proofs;
- synthetic credentials only; no PHI and no production traffic;
- status-keyed cleanup and stable exact zero-state proof.

Attempt 2 may start only after attempt 1 refuses, cleanup passes, and exact
zero state is independently confirmed. A successful attempt terminates the
packet. Unused seconds are not transferable.

## Fresh cold rehearsal

The immutable receipt is
`platform/evidence/receipts/B6-2026-029-COLD/cold_rehearsal.json`, SHA-256
`3fa137c13ac365672c5fbe8bd2cbc4088de76e409bf6a901814615328438bfca`.
Two independent generations produced the same complete receipt hash and the
same canonical payload SHA-256
`5010e545a13a7a03af56418baaeb5945980f718608750baa9e6b36c61181f093`.

| Check | Result |
|---|---|
| Full simulated PASS | `1` |
| Stage-level injected refusals | `23` |
| Existing ALB/Fargate gate injections | `4` |
| File-proof assertion injections | `13` |
| Pre-deadline cleanup injections | `2` |
| Total injected refusals | `42` |
| Stale-to-current transient injection | `1`, `2` stale + `3` stable current reads |
| Credential writes / additional credentials | `1 / 0` |
| Post-mutation audit | `31` paths, `30` corrected, `0` one-shot remaining |
| Stage A simulated PASS / refusals | `1 / 7` |
| Recorded AWS API coverage | `23 APIs / 30 recorded-real fixtures / 0 uncovered` |
| Real AWS / kubectl calls | `0 / 0` |
| AWS / Kubernetes mutations | `0 / 0` |
| Canonical repository suite | `1,464 passed, 0 failed, 0 skipped, 7 deselected` |
| Known warning | `1` Starlette/httpx deprecation warning |
| Terraform fmt / validate | `PASS / PASS` |
| Python compile / shell syntax | `PASS / PASS` |

## Allowance request

`COST-REGISTRY-2026-005` remains the latest reconciled ledger. Packet-2026-028
used zero worker/GPU seconds and `$0.00` incremental compute. The existing
`$10` reservation remains the only active reservation; no new reservation is
requested and no credit is used to enlarge it.

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

Bindings:

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

None. The exact-version visibility poll, stale-to-current regression, standing
post-mutation rule and whole-runner audit are implemented as directed.

## Approval boundary

Independent review must bind the prepared clean commit, this packet SHA-256,
the cold-rehearsal SHA-256, the standing rule, the machine-readable audit, the
stale-to-current injection and the requested allowance. Only after review
`PASS` may the owner state exactly:

> Approve B6 AWS change packet 2026-029 only, including two non-transferable
> 4,500-second attempts within the existing $10 reservation.
