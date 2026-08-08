# B6A AWS change packet 2026-003C-E — proven sampler full platform proof

Status: **BLOCKED — INDEPENDENT REVIEW AND OWNER APPROVAL REQUIRED**

Prepared: `2026-08-08`

This packet is not approved for execution. An independent reviewer must first
record a versioned `PASS` bound to the committed packet, executable source
hashes, proven sampler hash and remaining allowance. Only then may the owner use:

`Approve B6A AWS change packet 2026-003C-E only.`

Approval given before the independent review is not accepted by the executable
authorization gate.

## Purpose and prior result

003C-D stopped before model deployment because its SSM sampler depended on
`crictl`, which is absent from the EKS AL2023 GPU AMI. Its receipts and result
remain immutable.

The separately authorized debug window diagnosed that failure and proved the
replacement script live through the exact GPU-node and DRA-driver context:

- result: `PASS_PROVEN_120_SAMPLE_SCRIPT`;
- result record:
  `platform/evidence/B6A-SAMPLER-DEBUG-2026-001-RESULT.json`, SHA-256
  `87c25c36bf950099b1668c93018b9f35eb733eb3ecf9fc4af5ecff1e1201695c`;
- sampler: `scripts/b6a_003c_e_ssm_sampler.sh`, SHA-256
  `b6aa0e0621fca7fc6ee9e9a2bb9f59ff543efbb71b06a35e5497919d8a573d96`;
- execution: `AWS-RunShellScript` version `1` to the exact GPU instance, then
  `/usr/local/bin/nerdctl --namespace k8s.io` into the exact DRA `gpus`
  container and `chroot` into the driver root;
- proof: 120 numeric samples, GPU index 0, total memory 23,034 MiB, empty
  stderr.

The debug instance was selected for termination after 668 seconds and the final
state was GPU, workload, deadline, `approved/asr/` and production-registry zero.

003C-E reuses that exact sampler and attempts the still-unfinished B6A platform
proof. It does not train, promote, approve or deploy a production model. Local
engineering evidence is
`platform/evidence/B6A-LOCAL-ENGINEERING-2026-007.json`, SHA-256
`983d5cf94fb1d25b033456aa05da414610849de95f2aa1d19f4825a28d1cc376`.

## Prospective receipt rule v2

`platform/runtime-receipt-policy-v2.yaml`, SHA-256
`58cce1151f4c077c88f3ca3a1697ed99c97429c11388c47fdf5c87e9d5d06bdd`,
governs only prospective receipts. Historical policy v1 remains byte-identical
at SHA-256
`86f3b45417268b4c9713fb28076485bf44e779cbbdea6b9b1cf6911dfbee7bda`;
no prior decision, source or receipt is edited or reinterpreted.

Every reached stage persists one exclusive, atomic, fsync-backed terminal
receipt immediately. A later stage cannot replace, amend, invalidate or delete
an earlier receipt.

Raw stdout/stderr is allowed only for `local_bindings`,
`dra_stable_readiness` and `sampler_self_test`, and only while all three facts
are proven false: model artifact present, audio artifact present, and model or
audio workload applied. Each raw field is limited to 32,768 UTF-8 bytes and
must bind the exact command path and SHA-256. Credentials, secrets, audio,
transcript text and PHI remain prohibited without exception. The raw-output
exception expires before the model or audio exists on the node.

After that boundary, receipts contain hashes, identities, timestamps and
bounded numeric measurements only. In particular:

- `transcription: PASS` is durable before GPU-memory sampling starts;
- `gpu_memory_measurement` is an independent receipt;
- a failed memory reading becomes `INCOMPLETE_MEASUREMENT` and cannot void or
  downgrade the successful transcription receipt;
- transcript text and raw post-artifact logs are never persisted.

## Retained exact state

No build, push, scan, upload, IAM, KMS, DRA or Pod Identity change is included.
The packet read-only reuses:

- v0 artifact tree
  `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e`,
  six versioned KMS-encrypted objects and `3,090,838,860` bytes only below the
  existing `b6a/asr/v0/` prefix;
- manifest SHA-256
  `c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850`;
- ASR Pod Identity association `a-ajbhedkszqlnrrjk4`;
- NVIDIA DRA child digest
  `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246`;
- model-loader child digest
  `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5`;
- ASR-runtime child digest
  `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087`;
- workload render
  `platform/k8s/b6a/asr-platform-proof-003c-b.rendered.yaml`, SHA-256
  `9e51c009ea995c15261505b2416611a8bbf2e8071e75758527d9e180f9be8f68`;
- synthetic no-PHI WAV
  `platform/testdata/b6a-003c-b-synthetic.wav`, SHA-256
  `3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3`;
- the already independently reviewed SSM-agent-only node policy. No IAM write
  is proposed by 003C-E.

Any retained-state mismatch stops and requires a new packet.

## Required governance and preflight

The independent review must verify the exact committed packet and source
hashes, the v2 exception boundary, the proven sampler binding, deadline-first
ordering, one-GPU limit, transcription-before-memory ordering, cleanup and
budget. The subsequent owner authorization record must bind that review,
packet SHA-256, every executable source hash and a maximum window of 5,109
seconds. Missing or mismatched review, approval, source, artifact or state
refuses locally before GPU scale-up.

Before opening the window, require:

- profile `medzen`, caller `arn:aws:iam::558069890522:user/s.fotso`, account
  `558069890522`, region `eu-central-1`;
- private branch clean, pushed and synchronized with the authorization;
- EKS `1.36`, `STANDARD`, healthy CPU baseline, GPU
  `min=0, desired=0, max=1`, zero GPU instances/nodes/Pods and zero scheduled
  actions;
- the exact retained artifacts, images, DRA, identity, workload and audio;
- Terraform `NO_CHANGES`;
- zero `approved/asr/` objects and zero `/medzen/registry` parameters;
- the existing `$15` reservation as the only active packet reservation.

## Itemized execution

1. Persist `local_bindings: PASS`, install cleanup traps, then arm and read back
   only `medzen-b6a-003c-e-deadline-scale-zero`. The deadline is armed before
   GPU desired changes from zero.
2. Scale only the GPU node group from desired zero to one. Require one healthy
   GPU node and three stable DRA reads with an unchanged node, Pod UID, child
   digest and ResourceSlice fingerprint.
3. Before model or audio exists on the node, require that exact instance Online
   in SSM and run the proven sampler through immutable `AWS-RunShellScript`
   version `1`. Require its one exact 120-sample numeric PASS summary. Preserve
   bounded raw stdout/stderr under policy v2; any other result persists
   `sampler_self_test: REFUSED`, aborts before deployment and cleans up.
4. Apply only the bound private B6A workload. Require the init container to
   verify the tree and manifest, then require CUDA model load, startup smoke,
   readiness disclosure and the exact live child image digests.
5. Send only the bound synthetic WAV through a loopback-only port-forward.
   Require HTTP 200, exact v0/platform-test disclosure, response schema and
   PHI-safe logs.
6. Persist and fsync `transcription: PASS` immediately, storing response and
   transcript hashes but no transcript text.
7. Only after that receipt exists, start numeric GPU-memory sampling through
   the same DRA driver-root path and send a second copy of the synthetic request.
   Persist either `gpu_memory_measurement: PASS` with numeric baseline, peak and
   total memory, or `INCOMPLETE_MEASUREMENT` without raw post-artifact output.
8. Persist the independent proof summary. A memory failure preserves
   transcription `PASS` and leaves B6A incomplete.
9. On every outcome, delete/scale only the B6A workload, set GPU desired zero,
   and prove EKS/ASG/EC2 GPU zero, zero GPU nodes and Pods, zero workload
   replicas, zero approved objects and unchanged production registry state.
10. Delete the deadline only after all zero proofs pass; otherwise leave it
    armed and refuse. Persist `cleanup: PASS` and publish immutable execution
    evidence referencing every stage receipt.

## Budget boundary

- Aggregate project ceiling: `$300`.
- Previously committed guardrail: `$47.5288`.
- Existing reservation: `$15`; new reservation: `$0`.
- Conservative prior B6A GPU time: `2,091` seconds.
- Maximum remaining allowance: `5,109` seconds.
- Conservative cumulative maximum: `7,200` seconds.
- Recorded g6.xlarge rate: `$1.0064/hour`.
- 003C-E maximum GPU estimate: `$1.4285`.
- Conservative cumulative GPU estimate: `$2.013`.

GPU desired is zero before and after. No second GPU or concurrent billable
packet is permitted.

## Deterministic outcomes

- `B6A_PLATFORM_PROOF_COMPLETE`: sampler, transcription, GPU-memory and cleanup
  receipts all independently pass.
- `INCOMPLETE_MEASUREMENT`: transcription PASS remains durable, memory is
  incomplete, cleanup passes and B6A remains incomplete.
- `BLOCKED_SSM_SAMPLER_SELF_TEST`: no model workload is applied; sampler
  refusal and cleanup receipts are durable.
- `BLOCKED_PLATFORM_PROOF`: transcription refuses; no transcription PASS is
  claimed.
- `FAILED_CLOSED_EXECUTION`: governance, AWS, Kubernetes, receipt or cleanup
  controls prevent a trustworthy conclusion.

Even `B6A_PLATFORM_PROOF_COMPLETE` completes only B6A. B5 remains `BLOCKED`,
v0 remains non-approved, deferred-language `approved_version` values remain
null, and full B6 remains incomplete.

## Explicitly prohibited

- Execution before independent review PASS and later exact owner approval.
- More than one GPU, more than 5,109 additional conservative GPU seconds, or
  any operation outside this itemized sequence.
- IAM, KMS, ECR, image, DRA, Pod Identity, Terraform-resource, artifact or
  registry mutation.
- Training, language-scope change, use of GreenBucket data, quality
  reclassification, approved-ASR write, model registration, MLflow transition,
  or production SSM/serving change.
- Public endpoint, ingress, load balancer, production traffic, PHI or
  non-synthetic audio.
- Raw output after the pre-artifact boundary or any audio, transcript text,
  credential, secret or PHI in a receipt.
- Editing or reinterpreting any B4, B5 or prior B6A decision, evidence or
  receipt.

No AWS or Kubernetes operation in this packet is authorized until both gates
complete in order: independent review PASS, then exact owner approval.
