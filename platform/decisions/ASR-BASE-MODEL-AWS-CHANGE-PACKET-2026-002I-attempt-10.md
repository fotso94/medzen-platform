# ASR base-model AWS change packet 2026-002I — live-composition attempt 10

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002I only, authorizing numbered attempt 10 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, a write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002I` must bind this
packet. A committed read-only execution of the complete
`deadline_identity_and_acceptance` stage against the actual authorization,
bindings and packet must PASS before any attempt envelope or AWS call.

## Attempt-9 result and immutable history

Attempt 9 is consumed. The verified 9-object, 13,116,686,091-byte pre-staged
bundle passed read-only verification with zero upload. The canonical stage
wrapper then correctly refused because the live operation returned the nested
`PASS_PRESTAGED_BUNDLE_VERIFY_ONLY` status where the outer
`PASS_ARTIFACT_STAGE` status was required. No endpoint or GPU was started;
cleanup closed the reservation and confirmed zero state.

- attempt-9 refusal SHA-256:
  `dbbcf000a3db5501bb9fe63139bc12fa01253a110c58716eef81cfe989d2a2cf`
- live/rehearsal divergence diagnosis SHA-256:
  `57d8271db3f8e50afb45b7611a1f1af2b75bb1ac0e4f6175e2f1cbec5355bbe3`
- historical packet, authorization, dry validation, receipts and refusal are
  unchanged and cannot authorize attempt 10.

## Class-level rehearsal correction

`FakeOperations` has been eliminated. There is now exactly one stage
implementation: `scripts.asr_base_model_pilot_live.LiveOperations`. The cold
rehearsal constructs that class directly and injects fakes only at external
AWS, kubectl and Docker Scout boundaries. It does not replace or override a
stage method.

The real composition path now executes in rehearsal for all eleven stages,
including the local input-freeze audit, state snapshots, filesystem ordering,
receipt dependencies, refusal receipts and status-keyed cleanup. A machine
guard rejects any parallel class defining a canonical stage method.

This is the standing fidelity rule for the pilot executor: **everything except
paid external calls executes through the live composition code**. A separate
stage implementation may not be introduced for rehearsal.

## Direct fix and newly closed live-path gap

The artifact wrapper now preserves both typed outcomes without key collision:

- outer stage: `PASS_ARTIFACT_STAGE`;
- nested verification: `PASS_PRESTAGED_BUNDLE_VERIFY_ONLY`;
- in-attempt artifact upload: `0` bytes.

The wrapper-contract test invokes the canonical `stage_artifact_stage` against
the real `LiveOperations` and asserts those exact values.

The real-path rehearsal then exposed one additional pre-AWS defect:
`node_local_input_stage` expected a local `model-bindings.json` that the
verify-only artifact stage had never materialized. The artifact stage now
downloads the one exact version bound inside the verified pilot bundle,
verifies byte count `1,065` and SHA-256
`b66c1c7f34375df1352a2be74fd9f975f2911c4a5366ff83f311802709477f2c`,
then supplies it to node staging. This is a version-bound S3 read, not a
mutation, and it changes no model, data or bundle identity.

## Recorded boundary subjects

The rehearsal uses recorded exact inputs rather than invented stage results:

| Boundary subject | SHA-256 |
|---|---|
| frozen eval-manifest archive | `5e1ef06c7f7ddadbfe8b88e432b3beb6e03c96ec554d16ff65d741f0109ed944` |
| real ECR OCI-index response | `506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa` |
| exact pilot bundle | `b11ec2dcf066fa57a6dd06149f74bef49b2680efb430b8e20f4afe7a93ad75c7` |
| exact runtime rows | `0cfe61948ce6b83cca85cd8c552d646c5e2128bcf0a593e46a012c01d5d1adbc` |
| exact model bindings | `b66c1c7f34375df1352a2be74fd9f975f2911c4a5366ff83f311802709477f2c` |

All are hash-bound in
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002I.json`.

## Cold rehearsal proof

The committed receipt is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002I-COLD/cold-rehearsal.json`,
SHA-256
`7e86781d13b273270b60b9720c6159fa15bb541c69ed4d3ca3a50d306221d657`.

It records:

- `PASS_COLD_REHEARSAL_REAL_LIVE_OPERATIONS`;
- one complete `PASS_PILOT` run through all eleven real stage methods;
- eight fail-closed injections: wrong digest, extra finding, isolation,
  deadline, cleanup, missing pre-staged object, in-window upload and
  infeasible window budget;
- wrapper contract PASS with distinct outer and nested statuses;
- zero real AWS calls, zero real kubectl calls and zero residual state;
- zero parallel fake stage implementations;
- byte-identical output in two consecutive runs after normalizing only the
  temporary external-workdir path and computed bounded deadline.

## Unchanged pilot subject and safety boundary

- risk acceptance SHA-256:
  `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c`;
- OCI index:
  `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`;
- linux/amd64 child:
  `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`;
- pre-staged pilot bundle identity:
  `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee`;
- frozen pilot: 540 rows across 47 languages, row-list SHA-256
  `2170eb450ae9b42c64e02f8753469eb7d74b7b3f2363ae3f770fbd3062e488b6`;
- exact accepted risk: four enumerated PyTorch HIGH findings, zero critical;
- offline-only, no inbound network, S3/ECR-only egress, frozen public audio,
  no PHI, no untrusted input, container destroyed after the window;
- serving images retain their no-waiver rule.

The image, build context, model checkpoints, tokenizer, input freeze, scan
subject and risk record are unchanged; no requalification or risk-record
rewrite is warranted. Risk acceptance still expires with its authorized
window and must be current when the owner approves.

## Exact attempt-10 scope

Only after all approval gates pass:

1. Numbered attempt 10 only; attempts 1–9 cannot be reused.
2. One non-transferable 10,800-second window, one GPU node maximum, fresh
   `$10` ceiling within the `$300` project ceiling.
3. External runtime/evidence workdir; safe evidence enters Git only after the
   terminal result and zero-state cleanup.
4. Existing exact ECR image read and digest-rescan; no image upload.
5. Existing pre-staged S3 bundle read/verify only; zero artifact upload.
6. Temporary S3/ECR endpoints, strict network policy, one encrypted 60-GiB
   volume, one GPU node and the offline 540-row pilot.
7. Status-keyed cleanup on every result, restoring CPU/GPU desired zero and
   removing every temporary endpoint, volume, namespace and deadline action.

Explicitly prohibited: attempt reuse or extension; IAM/KMS changes; Inspector
Enhanced or registry-wide scan changes; internet egress; inbound routes;
PHI/untrusted inputs; training, serving, promotion, `approved/asr`, production
SSM, MLflow registration or language-registry mutation.

## Execution-asset map

| Claimed stage | Canonical wrapper | Sole stage implementation |
|---|---|---|
| deadline/identity + pre-stage budget | `stage_deadline_identity_and_acceptance` | `LiveOperations.deadline_identity_and_acceptance` |
| input freeze/no-PHI | `stage_input_freeze_and_no_phi` | `LiveOperations.input_freeze_and_no_phi` |
| cost/zero state | `stage_cost_and_zero_state` | `LiveOperations.cost_and_zero_state` |
| exact image/rescan | `stage_image_publication_and_scan` | `LiveOperations.image_publication_and_scan` |
| pre-staged artifact verify | `stage_artifact_stage` | `LiveOperations.artifact_stage` |
| endpoints/isolation | `stage_private_endpoint_and_policy_gate` | `LiveOperations.private_endpoint_and_policy_gate` |
| GPU/sampler | `stage_gpu_and_sampler_gate` | `LiveOperations.gpu_and_sampler_gate` |
| node-local inputs | `stage_node_local_input_stage` | `LiveOperations.node_local_input_stage` |
| 540 pilot rows | `stage_pilot_rows` | `LiveOperations.pilot_rows` |
| aggregate | `stage_aggregate_report` | `LiveOperations.aggregate_report` |
| cleanup | `stage_cleanup_and_expiry` | `LiveOperations.cleanup_and_expiry` |

All 16 live/rehearsal modules are unconditionally hash-bound. Missing, extra
or changed bindings refuse before mutation.

Focused ASR validation reports `141 passed, 0 failed`. The repository suite in
the pinned `.venv` reports `1,671 passed, 59 failed, 7 deselected`. The same 59
pre-existing generated-language/B5-scope failures disclosed during review of
packet 2026-002 remain: stale generated records after data-only language
expansion, missing historical B5 aliases and downstream policy consumers.
They are outside this packet and are not silently repaired here. Running with
the unpinned system Python additionally lacks historical ML dependencies; that
environment result is not used as the canonical suite.

## Budget and post-approval gates

`COST-REGISTRY-2026-006`, SHA-256
`d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da`,
records `$74.4286064216` recognized committed guardrail, zero active
reservations and `$225.5713935784` headroom. This packet requests a fresh `$10`
ceiling. That reservation is a guardrail, not AWS authorization or a claim of
actual spend.

After exact owner approval, the order is fixed:

1. write and commit the write-once authorization;
2. run and commit the read-only complete stage-1 validation against the real
   authorization, packet, bindings, all module hashes and pre-stage proof;
3. only if it passes, execute attempt 10 once;
4. persist each stage receipt immediately and always run cleanup;
5. commit terminal evidence only after zero-state verification.

## Deviations

None. The rehearsal restructuring implements the reviewer requirement
directly. Historical evidence remains write-once. No AWS execution is
authorized by this packet draft.
