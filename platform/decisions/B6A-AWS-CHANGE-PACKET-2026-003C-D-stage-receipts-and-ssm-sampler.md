# B6A AWS change packet 2026-003C-D — durable stage receipts and independent memory proof

Status: **BLOCKED — INDEPENDENT IAM REVIEW AND OWNER APPROVAL REQUIRED**

Prepared: `2026-08-08`

Independent IAM review must be recorded first. Only after that record is
committed may the owner use the final approval phrase:

`Approve B6A AWS change packet 2026-003C-D only.`

An approval given before a versioned independent IAM review is present is not
accepted by this packet.

## Purpose and prior outcome

Packet 003C-C proved stable DRA readiness, artifact loading and ASR readiness,
then refused because no numeric GPU-memory sample was preserved. Its immutable
record is
`platform/evidence/B6A-PACKET-2026-003C-C-BLOCKED-GPU-MEMORY.json`, SHA-256
`d888d9d47d58787a4a804fc7f118bb1f94fc72d85d2196c0b8dd01a5bef0cf73`.
Cleanup returned GPU capacity and workloads to zero.

The implementation control flow reached a successful transcription before the
memory parser refused, but 003C-C wrote only the later failure receipt. It is
therefore not retroactively claimed as a durable transcription proof.

003C-D makes the correction permanent:

- every stage writes one exclusive, atomic, fsync-backed receipt immediately;
- the transcription receipt is durable before any memory sampler starts;
- memory is a second independent proof using a second synthetic inference;
- memory failure records `INCOMPLETE_MEASUREMENT` and never voids transcription
  `PASS`;
- the exact sampler context must pass a roughly two-minute SSM self-test before
  the model workload is deployed.

Design records:

- `B6A-DESIGN-2026-006`, SHA-256
  `8b64cee73972cd19582357a6c383be2d6340ea5ecd366a6fa1ba4be158923302`;
- `B6A-DESIGN-2026-007`, SHA-256
  `f0a5cdf9bba1f793f4e1e8f54f650410c131a63711d691f0d529f374eedea2fd`;
- `B6A-DESIGN-2026-008`, SHA-256
  `b6db236e5327ad233792053d85c50cebfa56d6052dbdadfc13a802af1f837fa1`.

Local engineering evidence:
`platform/evidence/B6A-LOCAL-ENGINEERING-2026-006.json`, SHA-256
`d97f1e61070912ed5a03f10f10076c84716603aa1c6990a93690d7a0e97c47e6`.

This remains B6A only. It is not training, model promotion, B6.1 or full B6.

## Standing receipt rule

`platform/runtime-receipt-policy-v1.yaml`, SHA-256
`86f3b45417268b4c9713fb28076485bf44e779cbbdea6b9b1cf6911dfbee7bda`,
governs this and future runtime proofs.

The ordered receipts are `local_bindings`, `deadline`,
`dra_stable_readiness`, `sampler_self_test`, `transcription`,
`gpu_memory_measurement`, `proof_summary`, and `cleanup`. Each terminal receipt
is write-once and includes its dependency hashes. Missing, malformed, unknown,
duplicated or out-of-order evidence refuses.

Receipts may contain hashes, identities, timestamps and bounded numeric
measurements. They may not contain audio, transcript text, raw service or
sampler logs, credentials, secrets or patient data. A later receipt cannot
amend, delete, invalidate or replace an earlier one.

## Independent IAM review gate

The GPU and CPU node groups share
`arn:aws:iam::558069890522:role/medzen-speech-node-role`. Live verification
found no SSM permissions and zero SSM managed nodes, so the required SSM test
cannot currently run.

The only Terraform change proposed is:

- create inline policy `medzen-speech-node-ssm-core` as
  `aws_iam_role_policy.node_ssm_core` on the shared node role;
- exact source `platform/iam/medzen-node-ssm-core.json`, SHA-256
  `ce0b898088de05b8c27c6be21aa038d5d8a4e354b84dd09c041d163631b8ac58`;
- local plan and guard: **1 create, 0 update, 0 delete**, no other resource.

The frozen policy permits only SSM Agent association/document/status calls and
the `ssmmessages`/legacy `ec2messages` channels. It grants no
`ssm:SendCommand`, Parameter Store reads, S3, CloudWatch Logs, application,
artifact, registry, KMS or serving permissions. Because the node role is
shared, both CPU and GPU nodes gain this no-ingress management channel.

Before any Terraform apply, an independent reviewer must commit a versioned
record bound to this packet, the exact policy SHA, the fresh guarded plan SHA
and the reviewed Git commit. It must explicitly accept or reject:

1. the shared CPU/GPU role scope;
2. every allowed action and wildcard resource required by the agent protocol;
3. the absence of Parameter Store, sender and output-sink permissions;
4. the operator's existing `SendCommand`, `GetCommandInvocation` and
   `DescribeInstanceInformation` authority;
5. rollback by removing only this inline policy after confirming no SSM command
   is active.

The implementer or owner approval alone is not the independent review.

## Retained exact state

The following completed state is read-only reused:

- six versioned KMS-encrypted v0 objects, `3,090,838,860` bytes, only at
  `s3://medzen-speech/b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/`;
- artifact tree SHA-256
  `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e`;
- manifest SHA-256
  `c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850`;
- existing read-only B6A ASR role and Pod Identity association
  `a-ajbhedkszqlnrrjk4`;
- existing locked NVIDIA DRA installation and exact scan-passed digest
  `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246`;
- model-loader child digest
  `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5`;
- ASR-runtime child digest
  `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087`;
- workload render SHA-256
  `9e51c009ea995c15261505b2416611a8bbf2e8071e75758527d9e180f9be8f68`;
- synthetic no-PHI WAV SHA-256
  `3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3`.

Artifact upload, image build/push/scan, identity changes and DRA reapply are not
repeated. Any mismatch stops and requires a new packet.

## Preconditions after independent review and owner approval

Create and commit `B6A-AWS-AUTH-2026-003C-D`, bound to the final packet SHA,
independent review receipt, source hashes, owner approval time and maximum GPU
window `6,520` seconds. The executable authorization check refuses without the
independent review status `PASS`.

Before applying or opening the GPU window, require:

- profile `medzen`, caller `arn:aws:iam::558069890522:user/s.fotso`, account
  `558069890522`, region `eu-central-1`;
- private branch clean, synchronized and containing the authorization;
- a fresh Terraform plan, not the disposable preparation plan, accepted by
  `scripts/check_b6a_003c_d_iam_plan.py` as exactly one create;
- all retained artifact, image, identity, DRA and workload hashes unchanged;
- EKS `1.36`, `STANDARD`, healthy CPU baseline and GPU
  `min=0, desired=0, max=1` with zero GPU instances, nodes and Pods;
- zero scheduled actions, zero `approved/asr/` objects and zero
  `/medzen/registry` parameters;
- the existing `$15` reservation as the only active billable reservation.

## Itemized execution

### Phase A — reviewed IAM enablement, no GPU

1. Apply only the fresh guarded Terraform plan that creates
   `aws_iam_role_policy.node_ssm_core`.
2. Read back the inline policy and require byte-equivalent JSON to the reviewed
   source. A full post-apply plan must be `NO_CHANGES`.
3. Map the two current CPU Kubernetes nodes to their exact EC2 instance IDs and
   require both to appear `Online` in Systems Manager. Do not send a CPU
   command. Failure stops before a deadline or GPU write.
4. Persist an immutable IAM apply/registration result. The policy is a standing
   no-ingress management boundary with no direct service charge; it remains
   after B6A unless a separately recorded rollback removes it.

### Phase B — bounded GPU and predeploy sampler proof

5. Persist `local_bindings`, install cleanup traps, then arm and read back only
   `medzen-b6a-003c-d-deadline-scale-zero` against the exact GPU ASG. Maximum
   remaining window: `6,520` seconds.
6. Scale only GPU desired zero to one. Require one healthy GPU node and the same
   DRA Pod UID, node, digest and ResourceSlice fingerprint across three
   consecutive reads.
7. Before any model workload, map that node to its exact EC2 instance ID and
   require it `Online` in SSM.
8. Invoke immutable AWS-managed document `AWS-RunShellScript` **version `1`**
   on only that instance. No S3 or CloudWatch output destination is configured.
   Through `crictl`, bind the exact DRA Pod UID, `gpus` container and DRA digest,
   then execute
   `/busybox/chroot /driver-root /usr/bin/nvidia-smi` for 120 numeric samples at
   approximately one-second intervals.
9. Persist `sampler_self_test` immediately. Anything other than one exact safe
   PASS summary aborts before the model deploy, writes a REFUSED receipt and
   enters cleanup.

This SSM sampler is the first explicit diagnostic command on the GPU node.
Automatic EKS/DRA startup necessarily precedes it; no model workload does.

### Phase C — separate transcription and memory proofs

10. Apply only the exact private B6A workload render. Require loader tree and
    manifest verification, CUDA model load, startup smoke, readiness disclosure
    and exact live child digests.
11. Send the synthetic WAV through loopback-only port-forward and require HTTP
    200, the exact v0/platform-test disclosure, response schema and PHI-safe
    logs.
12. Persist and fsync the `transcription: PASS` receipt immediately. Store only
    response/transcript hashes and bounded metadata; store no transcript text.
13. Only after that receipt exists, start the already self-tested sampler and
    require a parsed numeric baseline. Send a second copy of the same synthetic
    request as the independent serving-memory exercise, then persist either:
    - `gpu_memory_measurement: PASS` with numeric baseline/peak/total; or
    - `gpu_memory_measurement: INCOMPLETE_MEASUREMENT` with no raw output.
14. If memory is incomplete, preserve transcription `PASS`, write
    `proof_summary: INCOMPLETE_MEASUREMENT`, keep B6A incomplete and clean up.

The memory scope is loaded v0 plus a serving inference. It is not a reconstructed
003C-C startup trace and does not alter any B4/B5 quality result.

### Phase D — cleanup and evidence

15. On every outcome, scale/delete only the B6A workload, set GPU desired zero,
    and prove EKS/ASG zero, zero GPU instances/nodes/B6A Pods and zero replicas.
16. Delete the deadline only after all zero proofs pass; otherwise leave it
    armed and refuse. Persist `cleanup: PASS` immediately after complete proof.
17. Create immutable execution evidence that references every separate receipt,
    exact AWS/SSM command identities, timestamps, conservative cost and retained
    state.

## CPU node cost handling

The two CPU nodes currently host only `kube-system` services. CPU scaling is not
part of this packet. If independent review means the next attempt will not start
within roughly 24 hours, first reverify that no non-system workload is
scheduled, then use a separate recorded action to set CPU minimum and desired
to zero. The EKS control plane continues billing even when both node groups are
zero.

## Budget boundary

- Aggregate project ceiling: `$300`.
- Previously committed guardrail: `$47.5288`.
- Existing reservation: `$15`; new reservation: `$0`.
- Conservative prior B6A GPU time: `680` seconds.
- Confirmed remaining allowance: `6,520` seconds.
- Conservative cumulative maximum: `7,200` seconds.
- Recorded g6.xlarge rate: `$1.0064/hour`.
- 003C-D maximum GPU estimate: `$1.8227`.
- Conservative cumulative GPU estimate: `$2.0128`.

GPU desired is zero before and after. No second GPU or concurrent billable
packet is permitted.

## Deterministic outcomes

- `B6A_PLATFORM_PROOF_COMPLETE`: transcription PASS, GPU-memory PASS and
  cleanup PASS are all independently durable.
- `INCOMPLETE_MEASUREMENT`: transcription PASS is durable, GPU memory is
  incomplete, cleanup passes, and B6A remains incomplete.
- `BLOCKED_SSM_SAMPLER_SELF_TEST`: no model workload is applied; refusal and
  cleanup receipts are durable.
- `BLOCKED_PLATFORM_PROOF`: the transcription stage refuses; no transcription
  PASS is claimed.
- `FAILED_CLOSED_EXECUTION`: IAM, AWS, Kubernetes, receipt or cleanup controls
  prevent a trustworthy conclusion.

Even `B6A_PLATFORM_PROOF_COMPLETE` completes only B6A. B5 remains `BLOCKED`, v0
remains non-approved, and full B6 remains incomplete.

## Explicitly prohibited

- Any IAM apply before a versioned independent review PASS and subsequent exact
  owner approval.
- Any action not present in the fresh one-create Terraform plan.
- SSM command to a CPU node, more than one GPU instance, mutable SSM document
  default, remote S3/CloudWatch command output or raw sampler output retention.
- Artifact upload/overwrite, image or DRA change, security waiver, identity or
  Pod Identity change.
- Training, quality reclassification, language reactivation, approved-ASR
  write, model registration, MLflow stage or production SSM/serving change.
- Public endpoint, ingress, load balancer, production traffic or non-synthetic
  audio.
- Editing any B4, B5, 003C-B or 003C-C record, or claiming 003C-C had a durable
  transcription proof.

No operation in this packet is authorized until both governance gates are
complete in order: independent IAM review PASS, then exact owner approval.
