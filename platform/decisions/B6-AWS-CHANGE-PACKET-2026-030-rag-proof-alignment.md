# B6 AWS change packet 2026-030 — RAG proof alignment

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND EXACT OWNER APPROVAL**

Packet 2026-029 is terminal after one attempt. Its live `file_proof`
correctly refused an HTTP 503 instead of returning an unbound or uncited
clinical answer. Cleanup passed at exact zero state. The second 2026-029
attempt was deliberately unspent and is not transferable.

This single successor packet binds the exact RAG identity extracted from the
deployed image, verifies the existing content-addressed non-serving SSM
snapshot by exact read-back, rebinds the synthetic proof to a prior live-proven
spoken fixture, and requests two fresh bounded window attempts. This draft
authorizes **no AWS, Terraform, Kubernetes, secret, worker, service, SSM or
production mutation**.

## Immutable predecessor

| Binding | Value |
|---|---|
| Packet 2026-029 | `b32bbeee830d9c399451430f1f1d7a4e668cf97e03374f3b4839c73179564a5b` |
| Authorization 2026-029 | `e4880d55fd80051ac3c09d0d1894256670beb4520b874ab26483d9dacdcbd73f` |
| Terminal evidence | `platform/evidence/B6-PACKET-2026-029-ATTEMPT-1-REFUSED-RAG-ALIGNMENT.json` |
| Terminal-evidence SHA-256 | `4130fef13f578d6d88fc0dca5e68c803c53b98aa9001d60b3b1eaae301047201` |
| Attempt 1 | `file_proof REFUSED`; HTTP `503`; cleanup `PASS` |
| Attempt 2 | `NOT_EXECUTED`; deliberately unspent; not transferable |
| CPU / GPU after cleanup | desired `0 / 0`, instances `0 / 0` |
| Temporary endpoints / ALB / deadline actions | `0 / 0 / 0` |
| Production serving pointer / `approved/asr/` objects | `0 / 0` |

All predecessor decisions, receipts and evidence remain immutable.

## Extracted deployed RAG identity

The exact deployed Linux/amd64 child image was inspected locally with no
network and a read-only root filesystem:

- image: `medzen-rag-index@sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c`;
- embedded alias: `current`;
- embedded index manifest SHA-256:
  `6dc2a9217b44a8cd9523ee051f19a7e20d1cab447ad0029a42796c5211797160`;
- classification: `SYNTHETIC_NON_CLINICAL`;
- document count: `3`.

The immutable extraction record is
`platform/evidence/B6-RAG-IMAGE-INDEX-IDENTITY-2026-001.json`, SHA-256
`a724aabc2aa2b225d6f51c05510120091de4ed6abbe4f0095271d42d4ea6a112`.

## Content-addressed deployment snapshot

Before credential rotation or any worker mutation, stage zero performs a
read-only exact verification of all three parameters at:

`/medzen/registry/test/b6/d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81`

It requires exact name, type, version and canonical value equality with
`platform/generated/registry-ssm/b6-v0-synthetic.json`; reconstructs the
deployed `RegistryRouter`; requires the route RAG SHA-256 to equal
`6dc2a921...7160`; and requires `/medzen/registry/serving` to be empty. Any
missing, extra, malformed, stale or mismatched value refuses before credential
rotation. The receipt outcome is `PASS_REUSE_IDENTICAL_COMPLETE`, with:

- parameters created / changed / deleted: `0 / 0 / 0`;
- AWS reads: `3` including caller identity;
- AWS writes: `0`;
- production pointer changes: `0`.

The checks use the recorded-real SSM responses at:

- `tests/fixtures/aws/ssm-get-parameters-by-path-b6-test-registry.json`,
  SHA-256 `4ee22bd11af7cb918e3cf05681f54a7f499176e7f761b0eaa03713842c2ac120`;
- `tests/fixtures/aws/ssm-get-parameters-by-path-serving-empty.json`,
  SHA-256 `29eca2eed652792a10926adbae9daf1c82afc4ada0f5e9dd8193ec0fbc2a583b`.

The original 007A publication evidence remains the immutable publication
authority:
`platform/evidence/B6-DEPLOYMENT-REGISTRY-2026-001-RETRY-007A.json`, SHA-256
`68aa1a8f50bfa28d4216f3f366bb75d910a9d4ad63a849bcc9267669304f3595`.

## Corrected proof binding

The 2026-029 window sent the one-second 440 Hz B6.3 mock tone into real
zero-shot ASR. That fixture was suitable for mocked ASR contracts, not a live
speech-to-RAG proof. The successor uses only:

`platform/testdata/b6a-003c-b-synthetic.wav`

- audio SHA-256:
  `3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3`;
- synthetic phrase: “This is a synthetic MedZen platform test. No patient data
  is present.”;
- normalized phrase SHA-256:
  `4c0a11f2c67286a5de444f776a927da784fde10f80fd8f9140c4e907285c9d19`;
- prior live B6A transcription receipt SHA-256:
  `b1f748ac175a07e6fcc6b153af4f91fb24852c3ff995d2f8df13aaf6b557e3ac`;
- offline retrieval result: exactly `3` citations — `synthetic-card`,
  `synthetic-hours`, and `synthetic-support`.

The deployment manifest binds both the proof-audio SHA-256 and RAG-index
SHA-256. Stage zero checks the immutable image evidence, registry source,
embedded alias and index, audio, prior live transcript receipt, deployment
ConfigMap and citation result before any AWS call. A mismatch refuses.

## Fail-closed rehearsal

The cold rehearsal contains both required paths:

1. Injected observed RAG SHA-256 of 64 zeroes: `REFUSED` with
   `RAG_INDEX_IDENTITY_MISMATCH`.
2. Exact image, registry, audio, transcript and three-citation binding:
   `PASS_ALIGNED_RAG_PROOF_PATH`.

The actual orchestrator regression also injects a wrong RAG snapshot and
reproduces HTTP `503 DEPENDENCY_UNAVAILABLE` before LLM/TTS execution.
Incomplete citation counts of `0`, `1` or `2` refuse even when the index hash
matches.

## Unchanged execution boundary

The successful 2026-026 Stage A qualification is reused and may not be rerun.
Each requested full attempt retains the reviewed boundary:

- exact 23-stage receipt-per-stage runner;
- deadline armed before worker scale-up, maximum `4,500` seconds;
- at most two `m6i.large` CPU workers and one `g6.xlarge` GPU worker;
- seven digest-pinned, scan-passed images;
- controller plan `1 add / 0 change / 0 destroy`;
- endpoint/probe plan `13 add / 0 change / 0 destroy`;
- stable target health and private Fargate ready proof;
- synthetic file, WebSocket, cancellation, failure-drill and isolation proofs;
- synthetic credentials only, no PHI and no production traffic;
- status-keyed cleanup and stable exact zero-state proof.

Attempt 2 may start only if attempt 1 refuses, cleanup passes and exact zero
state is independently confirmed. A successful attempt terminates the packet.
Unused seconds are not transferable.

## Fresh cold rehearsal

The immutable receipt is
`platform/evidence/receipts/B6-2026-030-COLD/cold_rehearsal.json`, SHA-256
`17f3b8de03488c95b211513526892b0fe9adf8f0c29625402ddeec395b0d8f87`.
Two independent generations produced identical canonical payload SHA-256
`a773090eceef39c5e5877e5cb9d632438a8eb9e288afe328d94a9dca9d3fe9d1`
and scenario-result SHA-256
`227e98e67c6075d282781390495d14fd05da2c88c8b450742556a874d5569af8`.
Receipt timestamps are intentionally not part of the deterministic payload.

| Check | Result |
|---|---|
| Full simulated PASS | `1` |
| Stage-level injected refusals | `23` |
| ALB/Fargate gate injections | `4` |
| File-proof assertion injections | `13` |
| Pre-deadline cleanup injections | `2` |
| Registry/RAG mismatch injection | `1` |
| Total injected refusals | `43` |
| Stage A simulated PASS / refusals | `1 / 7` |
| Recorded AWS API coverage | `23 APIs / 30 fixtures / 0 uncovered` |
| Real AWS / kubectl calls | `0 / 0` |
| AWS / Kubernetes mutations | `0 / 0` |
| Canonical repository suite | `1,480 passed, 0 failed, 0 skipped, 7 deselected` |
| Known warning | `1` Starlette/httpx deprecation warning |
| Terraform fmt / backend-disabled validate | `PASS / PASS` |
| Python compile / shell syntax / diff check | `PASS / PASS / PASS` |

The updated whole-runner stability audit is
`platform/evidence/B6-POST-MUTATION-VERIFIER-AUDIT-2026-002.json`, SHA-256
`bfb2594f8d710f98743831373241ec0aa7b6a1ef45403eaf27984eb0dbffa4b9`;
all `31` post-mutation paths remain bounded stable observations and `0`
one-shot verifications remain.

## Allowance request

`COST-REGISTRY-2026-005` remains the latest reconciled ledger. The 2026-029
attempt used one bounded window; its final billing allocation remains inside
the existing reservation and does not authorize this successor. This packet
requests two fresh, non-transferable attempts without increasing the active
`$10` reservation.

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

**Requested new deployment-snapshot publication replaced by exact read-only
reuse.** Extraction proved the deployed image and current content-addressed
snapshot already bind the exact same RAG alias and index SHA-256. Republishing
the identical three values would resolve to the same content-addressed root
and would not change the observed request-level refusal. Inventing different
metadata or changing the registry schema would be an unsupported safety
change. Therefore this packet applies the proven 007A mechanics as an exact
`REUSE_IDENTICAL_COMPLETE` read-back gate with zero writes, and corrects the
actual proof-input binding. Independent review must explicitly accept or
reject this deviation; it is not a silent adaptation.

No other deviation is requested.

## Approval boundary

Independent review must bind the prepared clean commit, this packet SHA-256,
the cold-rehearsal SHA-256, the extracted image identity, the no-write exact
SSM read-back, both mismatch-refusal and aligned-pass paths, and the requested
allowance. Only after review `PASS` may the owner state exactly:

> Approve B6 AWS change packet 2026-030 only, including two non-transferable
> 4,500-second attempts within the existing $10 reservation.
