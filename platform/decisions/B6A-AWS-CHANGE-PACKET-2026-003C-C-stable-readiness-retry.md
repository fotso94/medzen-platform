# B6A AWS change packet 2026-003C-C — stable-DRA bounded retry

Status: **BLOCKED — NOT AUTHORIZED**

Prepared: `2026-08-05`

Required approval phrase:
`Approve B6A AWS change packet 2026-003C-C only.`

## Purpose and prior stop

Packet 2026-003C-B failed closed before the ASR workload was created. Its
immutable failure record is
`platform/evidence/B6A-PACKET-2026-003C-B-FAILED-DRA-READINESS.json`, SHA-256
`d73ed0643f611ec2e823c86a971bdd43a531e4a142f3242afc7d07fdbe772fcf`.
Every GPU state returned to zero and the independent deadline was disarmed only
after complete zero proof.

The DRA container started, but the proof read DRA Pod/ResourceSlice state only
once immediately after DaemonSet rollout and refused within one second. The
handler then discarded the safe assertion message. The most likely cause is a
DRA Pod or ResourceSlice readiness race, but the exact assertion is not
recoverable and is not claimed as proven.

This packet authorizes one corrected retry only. It adds stable readiness and
diagnostic evidence; it does not repeat artifact publication, Terraform/IAM,
Pod Identity, image scan or DRA installation. It is not training, promotion,
B6.1 or full B6.

Local remediation evidence:
`platform/evidence/B6A-LOCAL-ENGINEERING-2026-005.json`, SHA-256
`a780710c770e51bc738698ded4f064a708d5607bc7008bc133cf10fd3da044c3`.

## Retained exact state

The following completed 003C-B state must be read-only reused:

- Six versioned KMS-encrypted objects, `3,090,838,860` bytes, at only
  `s3://medzen-speech/b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/`.
- Artifact tree SHA-256
  `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e`
  and manifest SHA-256
  `c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850`.
- Dedicated read-only role
  `arn:aws:iam::558069890522:role/medzen-b6a-asr-role` and Pod Identity
  association `a-ajbhedkszqlnrrjk4`.
- Locked NVIDIA DRA render SHA-256
  `0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897`,
  already installed, with no DRA Pod while the GPU pool is zero.
- Exact scan-passed manifests:
  model-loader `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5`,
  ASR `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087`,
  and DRA `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246`.
- Workload render SHA-256
  `9e51c009ea995c15261505b2416611a8bbf2e8071e75758527d9e180f9be8f68`
  and synthetic no-PHI WAV SHA-256
  `3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3`.

Re-uploading the artifact, applying Terraform, changing identity, reapplying
DRA, rebuilding/retagging images or invoking a scan is prohibited. A retained
state mismatch stops and requires a new packet.

## Preconditions and new authorization

Before any write, create and commit `B6A-AWS-AUTH-2026-003C-C` bound to this
packet's exact SHA-256, the sources below, the owner approval time and a maximum
retry window of `7,140` seconds.

Fail closed unless all of these remain true:

- Profile `medzen`; caller
  `arn:aws:iam::558069890522:user/s.fotso`; account `558069890522`; region
  `eu-central-1`.
- Private branch is clean, synchronized and contains the authorization.
- All three exact deployable image scans remain `COMPLETE` with zero findings.
- The six retained artifact versions/checksums/KMS bindings match; no other
  object exists in that content-addressed prefix.
- Full Terraform plan is `NO_CHANGES`; role, inline policy and association are
  byte-equivalent to their approved boundary.
- Installed DRA DaemonSet, DeviceClass, RBAC and validation policy match the
  locked render; DRA desired Pods are zero while GPU desired is zero.
- EKS/ASG are healthy at `min=0, desired=0, max=1`, zero instances/nodes/Pods,
  and zero scheduled actions.
- `approved/asr/` and `/medzen/registry` remain empty.
- The existing `$15` reservation remains the only active billable reservation.

## Corrected execution

1. **Reverify only.** Recompute source/render/audio hashes and all retained/live
   preconditions. Perform no artifact, identity, DRA or workload write.
2. **Install cleanup first.** Set the `EXIT`, `INT` and `TERM` trap before any
   deadline or GPU change.
3. **Arm the reduced independent deadline.** Create and read back only
   `medzen-b6a-003c-c-deadline-scale-zero` against
   `eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26`. It must execute no later
   than `7,140` seconds after arming with `min=0, desired=0, max=1`.
4. **Scale only GPU desired zero to one.** Wait for the node group to become
   healthy and for DRA DaemonSet rollout. More than one GPU node refuses.
5. **Require stable DRA readiness before workload.** For at most five minutes,
   poll every two seconds for exactly one Running/Ready/Available DRA Pod with
   the exact digest, `gpu.nvidia.com`, and at least one matching ResourceSlice
   on the same node containing at least one device. The same Pod UID, node and
   ResourceSlice fingerprint must pass three consecutive reads. Any absence,
   restart, digest change, node change or timeout refuses without applying the
   ASR workload.
6. **Persist diagnostics before cleanup.** On any refusal, write a local
   `B6A_003C_C_NO_PHI_V1` receipt with exact stage, safe reason and last DRA
   observation before returning failure to the trap. It must contain no logs,
   audio, transcript, credentials or patient data.
7. **Run the unchanged proof only after stable DRA.** Begin timestamped
   `nvidia-smi` sampling before workload apply, apply the exact private render,
   verify artifact files/tree/manifest, CUDA load, startup smoke, readiness and
   live child digests, then submit the one synthetic WAV through loopback-only
   port-forward. Require the platform-test disclosure, exact v0 identities,
   one transcription, measured peak L4 memory and PHI-safe logs.
8. **Clean up on every outcome.** Scale/delete only the exact B6A workload, set
   GPU desired zero, and prove EKS/ASG desired zero, zero instances, GPU nodes,
   B6A Pods and replicas. Delete the deadline only after all zero proofs pass;
   otherwise leave it armed and fail closed.
9. **Create immutable result evidence.** Record the authorization, source
   hashes, stable reads or diagnostic receipt, transcription-safe response,
   real memory measurement when reached, exact timestamps, conservative cost,
   cleanup and retained state.

## Cost boundary

The first attempt is conservatively counted as `60` billable seconds, despite
termination being initiated after `43` observed seconds. At the recorded
`$1.0064/hour`, its minimum estimate is `$0.0168`; actual billing is not yet
reconciled.

- New maximum window: `7,140` seconds.
- Conservative cumulative B6A maximum: `7,200` seconds.
- New maximum GPU estimate: `$1.9960`.
- Conservative cumulative GPU maximum: `$2.0128`.
- Existing reservation: `$15`; new reservation: `$0`.
- Aggregate project ceiling: `$300`; previously committed guardrail: `$47.5288`.

GPU desired size must be zero before and after. No concurrent billable packet
or second GPU node is permitted.

## Deterministic outcomes

- `B6A_PLATFORM_PROOF_COMPLETE`: stable DRA readiness, one transcription, real
  peak L4 measurement, immutable evidence and complete zero cleanup all pass.
- `BLOCKED_DRA_STABLE_READINESS`: stable DRA is not established; workload and
  transcription are not attempted; diagnostic and cleanup evidence complete.
- `BLOCKED_PLATFORM_PROOF`: stable DRA passes but a trusted ASR proof gate
  refuses.
- `FAILED_CLOSED_EXECUTION`: AWS, Kubernetes, deadline, diagnostic or cleanup
  execution prevents a trustworthy conclusion.

Even success completes only B6A. B5 remains `BLOCKED`; v0 remains non-approved;
orchestrator, streaming, LLM/RAG, TTS and full B6 remain incomplete.

## Explicitly prohibited

- Artifact upload/overwrite, Terraform apply, IAM/Pod Identity change or DRA
  apply/delete/reinstall.
- Image rebuild, retag, push, manual scan, digest substitution or waiver.
- Training, quality claim, language reactivation, approved-ASR write, model
  registration, MLflow stage, language serving field or production SSM change.
- Public endpoint, ingress, load balancer, production traffic or non-synthetic
  audio.
- More than one GPU node, more than 7,140 retry seconds, or removal of the
  deadline before complete zero proof.
- Editing any B4, B5, 003C-B authorization, packet or failure record.
- Claiming v0 production readiness, B5 pass, B6.1 or full B6 completion.

No operation in this packet is authorized until the owner uses the exact
approval phrase at the top after reviewing the committed packet.
