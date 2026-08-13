# ASR base-model AWS change packet 2026-002J — recorded-response attempt 11

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002J only, authorizing numbered attempt 11 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, a write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002J` must bind this
packet. A committed, read-only execution of the complete
`deadline_identity_and_acceptance` stage against the actual authorization,
bindings, packet, pre-stage proof, fixture record and all executor modules must
PASS before any attempt envelope or AWS mutation.

## Attempt-10 result and immutable history

Attempt 10 is consumed. Five stages passed live: deadline/identity, input
freeze, cost/zero-state, exact-image security, and the verify-only pre-staged
artifact gate. The private-endpoint stage then refused before GPU startup
because `scripts/asr_base_model_pilot_live.py` assumed an S3 gateway endpoint
returned `PrefixListId`. The real `DescribeVpcEndpoints` response has no such
field. Cleanup restored zero CPU/GPU desired capacity and removed the temporary
endpoints, endpoint security group and deadline action.

- attempt-10 refusal SHA-256:
  `b11912d94f3d3c723b68318b6528d7024111085d9c44486d1d5a60075806b565`;
- GPU started: `false`; evaluation started: `false`; recognized GPU cost:
  `$0.00`;
- packet 002I, its authorization, dry validation, live receipts and refusal
  remain byte-unchanged and cannot authorize attempt 11.

## Direct correction

The network gate now resolves the regional S3 managed prefix list with
`EC2 DescribePrefixLists`, filtered to
`com.amazonaws.eu-central-1.s3`, and requires exactly one non-empty
`PrefixListId`. Only that identifier is passed to
`GetManagedPrefixListEntries`. Missing, malformed or ambiguous results refuse
with a typed reason.

The recorded gateway-endpoint response is also a permanent negative fixture:
it contains no `PrefixListId`. A regression test fails if the old
`DescribeVpcEndpoints` extraction or an invented fake field returns.

## Complete live-response fixture sweep

A `$0`, read-only sweep of the live account captured every AWS read API used by
the executor and its security, staging and publication helpers. Its immutable
record is:

- path:
  `platform/evidence/ASR-BASE-MODEL-AWS-READ-FIXTURE-CAPTURE-2026-001.json`;
- SHA-256:
  `e423ec4ba4f41e27a464a4a9d84a72d83cabe50184de08dafa8018dbecd4cfc0`;
- caller: `arn:aws:iam::558069890522:user/s.fotso`;
- 22 distinct read APIs, 40 hash-bound responses, zero uncovered reads;
- AWS mutations: `0`; GPU nodes: `0`; cost: `$0.00`.

The static inventory rejects any new or unmapped AWS read method. The fixture
catalog verifies the capture-record hash, each response hash, discovered API
coverage and dynamic paths before rehearsal. Presigned ECR credentials,
unrelated instance metadata, S3 object bodies and historical SSM output are
sanitized; byte counts and content hashes are retained where content matters.
No audio, predictions, PHI or credentials are present.

Rehearsal AWS reads now replay these captured payloads. A value can change only
at a path declared by the capture record and already present in the real
payload. Undeclared paths, absent keys, stale API coverage or any hash mismatch
refuse before rehearsal.

## Rehearsal and execution-asset completeness

There remains one stage implementation:
`scripts.asr_base_model_pilot_live.LiveOperations`. Rehearsal executes it
directly and replaces only AWS, kubectl and Docker Scout boundaries. It runs
real stage composition, filesystem ordering, state snapshots, input-freeze
audit, receipt chaining, refusal handling and cleanup.

All 11 stages map to the same wrappers and operations used live:

| Stage | Canonical wrapper | Sole implementation |
|---|---|---|
| deadline/identity | `stage_deadline_identity_and_acceptance` | `LiveOperations.deadline_identity_and_acceptance` |
| input freeze/no-PHI | `stage_input_freeze_and_no_phi` | `LiveOperations.input_freeze_and_no_phi` |
| cost/zero state | `stage_cost_and_zero_state` | `LiveOperations.cost_and_zero_state` |
| image/security | `stage_image_publication_and_scan` | `LiveOperations.image_publication_and_scan` |
| artifact verify | `stage_artifact_stage` | `LiveOperations.artifact_stage` |
| endpoints/isolation | `stage_private_endpoint_and_policy_gate` | `LiveOperations.private_endpoint_and_policy_gate` |
| GPU/sampler | `stage_gpu_and_sampler_gate` | `LiveOperations.gpu_and_sampler_gate` |
| node-local inputs | `stage_node_local_input_stage` | `LiveOperations.node_local_input_stage` |
| pilot rows | `stage_pilot_rows` | `LiveOperations.pilot_rows` |
| aggregate | `stage_aggregate_report` | `LiveOperations.aggregate_report` |
| cleanup | `stage_cleanup_and_expiry` | `LiveOperations.cleanup_and_expiry` |

All 17 live/rehearsal modules are unconditionally hash-bound in
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002J.json`. Missing,
extra or changed modules refuse. The response-capture utility is separately
reviewable but is not executable in the paid attempt.

The final receipt will be written last at
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002J-COLD/cold-rehearsal.json`.
It must record one full `PASS_PILOT` rehearsal, all standing injected refusal
paths, 22/22 API coverage, 40 hash-bound response fixtures, zero invented
fields, zero real AWS/kubectl calls, and zero residual state. Its SHA-256 is
inserted here only after the source and bindings have stopped changing.

## Unchanged subject and safety boundary

- risk acceptance SHA-256:
  `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c`;
- OCI index:
  `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`;
- linux/amd64 child:
  `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`;
- pilot bundle:
  `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee`;
- frozen evaluation: 540 rows across 47 languages, row-list SHA-256
  `2170eb450ae9b42c64e02f8753469eb7d74b7b3f2363ae3f770fbd3062e488b6`;
- exact accepted risk: four enumerated PyTorch HIGH findings, zero critical;
- offline only, no inbound network, S3/ECR-only egress, frozen public audio,
  no PHI, no untrusted input, container destroyed after the window;
- serving images retain the absolute zero-critical/zero-high no-waiver rule.

The image, build context, checkpoints, tokenizer, scan subject, pre-staged
objects, input freeze and risk record are unchanged. Attempt 11 skips image and
artifact upload through the existing digest/version checks.

## Exact attempt-11 scope

Only after every review and authorization gate passes:

1. Attempt 11 only; attempts 1–10 cannot be reused.
2. One non-transferable 10,800-second window, one GPU node maximum, fresh
   `$10` ceiling within the `$300` project ceiling.
3. External evidence workdir; safe receipts enter Git only after terminal
   cleanup.
4. Existing ECR image read and digest-rescan only; no image upload.
5. Existing pre-staged S3 bundle read/verify only; no artifact upload.
6. Temporary S3/ECR endpoints, strict network policy, one encrypted 60-GiB
   volume, one GPU node and the offline 540-row pilot.
7. Every stage receipt persists immediately; every result runs status-keyed
   cleanup and proves CPU/GPU zero plus no endpoint, volume, namespace or
   deadline-action residue.

Prohibited: attempt reuse or extension; IAM/KMS changes; Inspector Enhanced or
registry-wide scan changes; internet egress; inbound routes; PHI/untrusted
inputs; training, serving, promotion, `approved/asr`, production SSM, MLflow
registration or language-registry mutation.

## Budget and post-approval order

`COST-REGISTRY-2026-006`, SHA-256
`d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da`,
records `$74.4286064216` recognized committed guardrail, zero active
reservations and `$225.5713935784` headroom. This packet requests a fresh `$10`
ceiling; it is a guardrail, not a forecast that all `$10` will be spent.

After exact approval, the order is fixed:

1. write and commit authorization 002J;
2. run and commit the real-artifact stage-1 dry validation;
3. only on PASS, execute attempt 11 once;
4. persist every stage receipt immediately and always run cleanup;
5. commit terminal evidence only after zero-state verification.

## Deviations and capture limitations

No deviation applies to ordinary AWS read responses: every such response is a
hash-bound live capture and no response key may be invented.

Two read-after-write collections cannot contain their future items during a
zero-mutation capture: the deadline action and the GPU Auto Scaling instance.
Their empty live response envelopes are captured. During rehearsal only, their
collection values are derived from the exact preceding rehearsed mutation and
only fields consumed by `LiveOperations` are supplied. This limitation is
explicitly declared and machine-checked; it does not infer an undocumented AWS
field and it is not used for the S3 prefix-list correction. If the reviewer
interprets the requirement as demanding non-empty live examples of those two
future resources, this packet must remain on HOLD rather than silently weaken
the rule.

Historical records remain write-once. No AWS execution is authorized by this
draft.
