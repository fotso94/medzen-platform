# ASR base-model AWS change packet 2026-002H — pre-staged attempt 9

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002H only, authorizing numbered attempt 9 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

No attempt, endpoint, GPU, Kubernetes or other live execution is authorized by
this draft. After independent review PASS and the exact phrase, a write-once
`ASR-BASE-MODEL-AWS-AUTH-2026-002H` and a committed read-only validation of
the complete `deadline_identity_and_acceptance` stage against the real
authorization, bindings and packet must PASS before any attempt-9 AWS call.

## Attempt-8 diagnosis and immutable history

Attempt 8 is consumed and remains immutable. It passed deadline/identity,
input freeze, zero-state/cost and image security, then was stopped safely in
`artifact_stage`; endpoints, GPU and inference never ran and cleanup returned
all runtime resources to zero. Its refusal SHA-256 is
`773b3761646438196a3eb81aa30b6991f4e6b53cf52fb4e37881b3c691ac6c8b`.

The diagnosis SHA-256 is
`57ff209d9ca3a3e5357d249b2f8c70fe325b6747d4eb82b4fd0e0a03f003cf9f`.
At approximately 10 Mbps, the 13,021,689,920 checkpoint bytes alone required
about 10,418 seconds of the 10,800-second window. The synchronous, single-call
`put_object` design therefore made the attempt unwinnable even when the
request was alive. This was a design defect, not an AWS/GPU/model failure.

## Complete bundle pre-staging proof

Bulk transfer is now complete before attempt authorization. The committed
proof is:

- path: `platform/evidence/ASR-BASE-MODEL-PRESTAGE-PROOF-2026-001.json`
- SHA-256: `72af317af42a4f49195aa5d22019f41939beb9c0be04979b457a1278ea621168`
- status: `PASS_COMPLETE_MODEL_BUNDLE_PRESTAGED`
- exact content-addressed prefix:
  `s3://medzen-speech/research/asr-base-model/pilot/1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee/`
- 9 exact-version objects; 13,116,686,091 bytes
- full model, tokenizer, audio, runtime rows, model bindings and pilot bundle
- create-only completion; exact object metadata, version, S3 checksum and full
  local SHA-256 read-back verified
- zero active multipart uploads after completion
- endpoints created 0; GPU started false; production/approved paths untouched

The uploader uses 64 MiB S3 multipart parts, SHA-256 per part, three bounded
attempts per part, create-only completion, a 240-second zero-progress watchdog
and a byte-progress heartbeat under an external workdir. Existing exact
objects are reused only after exact version/size/metadata/checksum and complete
byte read-back verification.

The first qualification pass refused safely when it incorrectly carried the
watchdog clock across non-transfer preparation work. The correction scopes the
watchdog to each object transfer and is covered by regression. That refusal
did not open an attempt, endpoint or GPU and left no multipart upload.

## Timed-window feasibility

Attempt 9 performs **zero artifact upload bytes**. `artifact_stage` is
verify-only: it loads the committed proof, verifies every exact S3 version and
checksum, downloads only the small canonical `pilot-bundle.json`, and refuses
on any absence or identity drift. The explicit budget model binds:

- immutable window: 10,800 seconds;
- fast-stage estimate: 7,200 seconds;
- cleanup reserve: 900 seconds;
- in-window upload: 0 bytes;
- measured pre-stage uplink present and positive;
- refusal if any remaining bytes divided by measured uplink plus fast-stage
  time and cleanup reserve exceed the immutable window.

## Unchanged subject and security boundary

- risk acceptance SHA-256:
  `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c`
- OCI index:
  `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`
- linux/amd64 child:
  `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`
- exact Scout preflight SHA-256:
  `cabd8497de52e02f180c5f9caf455413be7de6006fb281a65c122c109fb3bf4b`
- pilot bundle identity:
  `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee`
- frozen 540-row, 47-language selection:
  `2170eb450ae9b42c64e02f8753469eb7d74b7b3f2363ae3f770fbd3062e488b6`

The image, model, tokenizer, evaluation input, scanner, accepted four PyTorch
HIGH tuples and offline-only risk subject are unchanged. The risk acceptance
remains non-precedential for serving, time-boxed to the evaluation window, and
requires S3/ECR-only egress, no inbound traffic, no PHI and destruction of the
runtime container after execution.

## Exact attempt-9 scope

Authorized only after review, exact approval, write-once authorization and the
committed real-artifact stage-1 dry validation:

1. Numbered attempt 9 only; attempts 1–8 cannot be reused.
2. One GPU node maximum for 10,800 non-transferable seconds.
3. Fresh `$10` attempt ceiling within the existing `$300` project ceiling.
4. External runtime/evidence workdir; safe evidence committed only after the
   terminal result and zero-state verification.
5. Read/verify the existing exact immutable ECR image; no image upload.
6. Digest reconstruction and pinned Scout scan with exactly the accepted four
   HIGH findings and zero critical findings; ECR Basic remains the 0/0 OS gate.
7. Read/verify the pre-staged bundle; no S3 mutations in `artifact_stage`.
8. Temporary private endpoints, strict network policy, one encrypted 60 GiB
   volume, one GPU node and the offline 540-row pilot.
9. Status-keyed cleanup on every outcome, restoring CPU/GPU desired zero and
   removing every temporary endpoint, volume, namespace and deadline action.

Explicitly prohibited: any new artifact upload inside attempt 9; any reuse or
extension; IAM/KMS changes; Inspector Enhanced or registry-wide scan changes;
internet egress; inbound routes; PHI/untrusted inputs; training, serving,
promotion, approved/asr, production SSM, MLflow registration or language
registry mutation.

## Execution asset map

| Claimed stage | Runner | Live operation |
|---|---|---|
| deadline/identity, pre-stage and window budget | `stage_deadline_identity_and_acceptance` | `LiveOperations.deadline_identity_and_acceptance` |
| input freeze/no-PHI | `stage_input_freeze_and_no_phi` | `LiveOperations.input_freeze_and_no_phi` |
| cost/zero state | `stage_cost_and_zero_state` | `LiveOperations.cost_and_zero_state` |
| exact image/rescan | `stage_image_publication_and_scan` | `LiveOperations.image_publication_and_scan` |
| pre-staged artifact verify | `stage_artifact_stage` | `LiveOperations.artifact_stage` |
| endpoints/isolation | `stage_private_endpoint_and_policy_gate` | `LiveOperations.private_endpoint_and_policy_gate` |
| GPU/sampler | `stage_gpu_and_sampler_gate` | `LiveOperations.gpu_and_sampler_gate` |
| node-local inputs | `stage_node_local_input_stage` | `LiveOperations.node_local_input_stage` |
| pilot rows | `stage_pilot_rows` | `LiveOperations.pilot_rows` |
| aggregate | `stage_aggregate_report` | `LiveOperations.aggregate_report` |
| cleanup | `stage_cleanup_and_expiry` | `LiveOperations.cleanup_and_expiry` |

Every live module, including the pre-staging verifier, is unconditionally
bound in `ASR-BASE-MODEL-PILOT-BINDINGS-2026-002H.json`.

## Rehearsal and post-approval gates

The fresh cold rehearsal uses the committed bindings/proof and the shared live
runner/receipt/filesystem ordering. It must contain one clean PASS and injected
refusals for: wrong image digest, extra finding, isolation, deadline, cleanup,
missing pre-staged object/version, any in-attempt upload byte, and infeasible
uplink/window arithmetic. AWS and kubectl calls alone are faked.

After approval, write AUTH-2026-002H. Then run and commit the complete read-only
`deadline_identity_and_acceptance` stage against the actual authorization,
bindings and packet. It must validate all committed artifacts—including the
pre-stage proof—and all module hashes before any attempt envelope or AWS call.

## Deviations

None. Historical records remain write-once. Pre-staging is the requested
boundary change; it does not authorize attempt 9 by itself.
