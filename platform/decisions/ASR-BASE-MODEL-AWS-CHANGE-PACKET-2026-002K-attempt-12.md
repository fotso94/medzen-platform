# ASR base-model AWS change packet 2026-002K — stable GPU-node readiness attempt 12

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002K only, authorizing numbered attempt 12 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft authorizes nothing. After independent review PASS and the exact
owner phrase, a write-once `ASR-BASE-MODEL-AWS-AUTH-2026-002K` must bind this
packet. A committed, read-only execution of the complete
`deadline_identity_and_acceptance` stage against the actual authorization,
bindings, packet, pre-stage proof, readiness fixtures and all executor modules
must PASS before any attempt envelope or AWS mutation.

## Attempt-11 result and immutable history

Attempt 11 is consumed. Six stages passed live: deadline/identity, input
freeze, cost/zero-state, exact-image security, verify-only pre-staged artifact,
and private endpoints/isolation. The GPU stage then refused on a single
`kubectl get nodes -l workload=gpu` read that returned an empty list. Cleanup
restored CPU and GPU desired capacity to zero and removed the temporary
endpoints, endpoint security group and deadline action.

- refusal:
  `platform/evidence/ASR-BASE-MODEL-PACKET-2026-002J-ATTEMPT-11-GPU-NODE-READINESS-REFUSAL.json`;
- refusal SHA-256:
  `90354a2d3a921be51f121c4b192b172d18c9a99403467ef5b3c78e4752acbaa1`;
- diagnosis:
  `platform/evidence/ASR-BASE-MODEL-PACKET-2026-002J-ATTEMPT-11-GPU-NODE-READINESS-DIAGNOSIS.json`;
- diagnosis SHA-256:
  `bf430990f005692e28e54f7c17cce7796e76e8915bb8069f30b239e5986e0e82`;
- evaluation started: `false`; attempt-11 actual AWS billing:
  `PENDING_COST_EXPLORER_INGESTION`;
- packet 002J, its authorization, dry validation, receipts, refusal and
  diagnosis remain byte-unchanged and cannot authorize attempt 12.

## Root cause confirmed from the same live attempt

A read-only query of the already-enabled EKS audit logs recovered the same
attempt's Kubernetes transition without starting compute:

- the executor's one read returned zero labeled nodes at 05:09:58Z;
- the node registered with `workload=gpu` and `Ready=False` at
  05:10:07.827809Z;
- the same node reached `Ready=True` at 05:10:30.965299Z.

The bound capture is
`platform/evidence/ASR-BASE-MODEL-GPU-NODE-READINESS-FIXTURE-CAPTURE-2026-001.json`,
SHA-256
`34663d3ae7218f9423d15b4fa9aa11f4f4940022deaf87a409e6c0f4c91e5e56`.
It records zero AWS/Kubernetes mutations, no compute started for capture, and
the sanitized exact empty, NotReady and Ready shapes. This confirms a
kubelet-registration/readiness race; it is not inferred to be an image, model,
network or infrastructure failure.

## Direct correction

The one-shot read is replaced by a bounded stable-readiness poll:

- selector: `workload=gpu`;
- fixed interval: 10 seconds;
- hard timeout: 600 seconds;
- success requires exactly one labeled node with `Ready=True` on two
  consecutive observations;
- both observations must name the same node;
- empty, non-Ready, multiple, incomplete or changing-node observations reset
  the consecutive count;
- malformed response: `GPU_NODE_RESPONSE_MALFORMED`;
- no stable success by the bound: `GPU_NODE_READY_TIMEOUT`.

No volume, DRA, sampler or model work can begin before this gate passes.
Timeout is a receipted refusal and the existing status-keyed cleanup always
runs.

## Rehearsal and execution-asset completeness

There remains one stage implementation:
`scripts.asr_base_model_pilot_live.LiveOperations`. Rehearsal executes it
directly and replaces only AWS, kubectl and Docker Scout boundaries. It runs
real stage composition, timing logic, filesystem ordering, state snapshots,
receipt chaining, refusal handling and cleanup.

The delayed-success rehearsal replays the captured sequence
`empty -> NotReady -> Ready -> Ready` and must complete `PASS_PILOT`. The
never-ready rehearsal replays `empty -> NotReady` through all 60 reads and
must refuse `GPU_NODE_READY_TIMEOUT` at the 600-second hard bound. Separate
regressions prove an intervening empty observation resets the Ready count and a
malformed response refuses immediately. No Kubernetes response field is
invented.

All 17 live/rehearsal modules are unconditionally hash-bound in
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002K.json`, SHA-256
`81e7592c156965b1e1862b5c218ba71c6b2918cd717b2a7334094d1fc017ba38`.
Missing, extra or changed modules refuse.

All 11 stage mappings remain unchanged:

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

The final cold-rehearsal receipt must be generated after this packet and
bindings are committed, must be written last, and must be inserted here by
SHA-256 before review. Until that receipt and final packet hash exist, this
packet remains incomplete and non-executable.

## Cost reconciliation and requested ceiling

`COST-REGISTRY-2026-007`, SHA-256
`cc63a81e5d6750c6c15731e542684e2ab3a7330ae080b546d15ffb45fb3fc1c4`,
conservatively closes attempt 11 by carrying its full $10 ceiling into
recognized committed guardrail. AWS Cost Explorer still returns no August 13
eu-central-1 EC2 Compute usage groups and marks the day estimated, so the
registry explicitly does not call zero a finalized actual charge. When the
g6.xlarge line lands, a new non-destructive revision must record gross,
credits and net separately.

Current guardrail:

- project ceiling: $300;
- recognized committed guardrail: $84.4286064216;
- active reservation: $0;
- headroom before this request: $215.5713935784;
- requested attempt-12 ceiling: $10;
- headroom if approved: $205.5713935784.

The request is a ceiling, not a forecast. Credits cannot expand headroom.

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
objects, input freeze and risk record are unchanged. Attempt 12 skips image and
artifact upload through existing digest/version checks.

## Exact attempt-12 scope

Only after every review and authorization gate passes:

1. Attempt 12 only; attempts 1–11 cannot be reused.
2. One non-transferable 10,800-second window, one GPU node maximum, fresh $10
   ceiling within the $300 project ceiling.
3. External evidence workdir; safe receipts enter Git only after terminal
   cleanup.
4. Existing ECR image read and digest-rescan only; no image upload.
5. Existing pre-staged S3 bundle read/verify only; no artifact upload.
6. Temporary S3/ECR endpoints, strict network policy, one encrypted 60-GiB
   volume, one GPU node and the offline 540-row pilot.
7. Stable labeled-node readiness must pass before volume, DRA, sampler or model
   work.
8. Every stage receipt persists immediately; every result runs status-keyed
   cleanup and proves CPU/GPU zero plus no endpoint, volume, namespace or
   deadline-action residue.
9. Attempt-12 actual billing is reconciled in a successor cost record when AWS
   publishes it.

Prohibited: attempt reuse or extension; IAM/KMS changes; Inspector Enhanced or
registry-wide scan changes; internet egress; inbound routes; PHI/untrusted
inputs; training, serving, promotion, `approved/asr`, production SSM, MLflow
registration or language-registry mutation.

## Post-approval order

After exact approval, the order is fixed:

1. write and commit authorization 002K;
2. run and commit the real-artifact stage-1 dry validation;
3. only on PASS, execute attempt 12 once;
4. persist every stage receipt immediately and always run cleanup;
5. commit terminal evidence only after zero-state verification;
6. reconcile actual attempt-12 and pending attempt-11 billing when Cost
   Explorer has ingested the usage rows.

## Deviations

None from the reviewer requirements. The Ready shape was recovered read-only
from the same attempt's EKS audit log rather than by starting a new GPU node.
This is stronger evidence than a synthetic Ready fixture and caused no
mutation or spend.

Historical records remain write-once. No AWS execution is authorized by this
draft.

