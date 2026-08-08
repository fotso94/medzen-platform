# B6A AWS change packet 2026-003C-F — bounded SSM discovery full proof

Status: **BLOCKED — INDEPENDENT REVIEW AND OWNER APPROVAL REQUIRED**

Prepared: `2026-08-08`

This packet is not approved for execution. An independent reviewer must first
record a versioned `PASS` bound to the committed packet and source hashes. Only
after that may the owner use:

`Approve B6A AWS change packet 2026-003C-F only.`

Approval before the independent review is not accepted by the executable gate.

## Purpose and prior outcome

003C-E passed bindings, deadline and stable DRA readiness, then stopped before
model deployment when the first `GetCommandInvocation` immediately after a
successful `SendCommand` returned `InvocationDoesNotExist`. Cleanup passed and
returned GPU, workload and deadline state to zero. Its immutable result is
`platform/evidence/B6A-PACKET-2026-003C-E-BLOCKED-SSM-INVOCATION.json`,
SHA-256
`f29f4508879219848a0c598ca28d779e9421ec0b31673b3c59e97dcb67cc9491`.

The command ID was recovered as
`889c2ab6-650e-40e8-b7c0-e7fa6a9d8071`. Cleanup began immediately, so the
pending command became `Undeliverable`; the proven sampler script never ran.
Peak L4 GPU memory therefore remains `NOT_MEASURED` and B6A remains incomplete.

003C-F corrects only the invocation-discovery control:

- send the exact command once and retain its ID immediately;
- treat only `InvocationDoesNotExist` as retryable during at most 60 polls at
  three-second intervals;
- preserve the command ID in every terminal post-dispatch outcome;
- fail closed on permanent discovery timeout, unexpected lookup error, unknown
  status or non-PASS sampler output;
- place the repository root in `PYTHONPATH` inside the bound launcher rather
  than relying on an operator environment correction.

Design record: `platform/decisions/B6A-DESIGN-2026-009-ssm-invocation-discovery.json`,
SHA-256
`2e70661e142cdf511c72e158fb3043e63368d86d06bd16f5cfcc3099569e907e`.
Local evidence: `platform/evidence/B6A-LOCAL-ENGINEERING-2026-008.json`,
SHA-256
`41057729a542a6a6ab661ce9d09df16b03339f372a76f20ed52af4130e7aa1f5`.

This remains B6A only. It is not training, promotion, B6.1 or full B6.

## Exact source and policy boundary

The independent review must verify these exact bindings:

- `pipeline/runtime_receipts_v2.py`:
  `898c70d1e502bd84d01a5e6f619d6c6d6fe26627e3ae678b26c87d47145367f2`;
- `platform/runtime-receipt-policy-v2.yaml`:
  `58cce1151f4c077c88f3ca3a1697ed99c97429c11388c47fdf5c87e9d5d06bdd`;
- proven sampler `scripts/b6a_003c_e_ssm_sampler.sh`:
  `b6aa0e0621fca7fc6ee9e9a2bb9f59ff543efbb71b06a35e5497919d8a573d96`;
- retained transcription/memory proof `scripts/run_b6a_003c_e_proof.py`:
  `73236c92533bf56b8c9b8f60ead18033d17fc0cb10377fc238bb22ff5a417ea5`;
- `scripts/b6a_003c_f_common.py`:
  `b22117796f88a5559ea182abc4d546f091a96d7f1a2fef25779024308f849175`;
- `scripts/b6a_003c_f_bindings.py`:
  `f871d4f98df318734242af1e03b8fe8988ac6ad4f69d7362ab232fd9199cb294`;
- `scripts/b6a_003c_f_deadline.py`:
  `76bd978d17972b85bf4303c63db0690a2b4bdc32c1d67a7ac28633c573451009`;
- `scripts/b6a_003c_f_cleanup.sh`:
  `8bbfe9f29d382b8d3edb10022b37d04c310cefca2a65bf1244e6fe1495c79a53`;
- `scripts/run_b6a_003c_f_sampler_self_test.py`:
  `fd96fa3f77f5c933da8bad9f863820d28b5e58c0fa39677c37c1580589deb94f`;
- `scripts/run_b6a_003c_f_gpu_window.sh`:
  `20dc4556f8d96114bca19a923f1c2ac80666fe95fa41626c8ec8a23aa55f2b1b`.

Policy v2 remains prospective and unchanged. Raw stdout/stderr is allowed only
for the bounded pre-artifact stages and only with all no-model/no-audio facts
false, an exact command path/hash, and the 32,768-byte field cap. Credentials,
secrets, audio, transcript text and PHI remain prohibited. Historical 003C-E
sources, decisions, evidence and receipts remain unchanged.

## Retained resources

No Terraform, IAM, KMS, ECR, image, DRA, artifact or Pod Identity mutation is
included. Read-only reuse remains bound to:

- v0 artifact tree
  `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e`;
- manifest
  `c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850`;
- Pod Identity association `a-ajbhedkszqlnrrjk4`;
- DRA child digest
  `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246`;
- model-loader child digest
  `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5`;
- ASR-runtime child digest
  `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087`;
- workload render SHA-256
  `9e51c009ea995c15261505b2416611a8bbf2e8071e75758527d9e180f9be8f68`;
- synthetic no-PHI WAV SHA-256
  `3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3`.

Any mismatch stops and requires another packet.

## Preconditions

After independent review and owner approval, create and push an authorization
record bound to this packet, the review and every source hash. Before the
window require the exact `medzen` identity/account/region, clean synchronized
private branch, EKS `1.36`/`STANDARD`, Terraform `NO_CHANGES`, GPU
`min=0, desired=0, max=1`, no instances/nodes/Pods/deadlines, exact retained
resources, zero `approved/asr/` objects, zero `/medzen/registry` parameters and
the existing `$15` reservation only.

## Itemized execution

1. Persist `local_bindings: PASS`, install cleanup traps, then arm and read back
   only `medzen-b6a-003c-f-deadline-scale-zero` before GPU desired changes.
2. Scale only the GPU node group to one. Require one healthy node and three
   unchanged DRA readiness reads.
3. Before any model or audio exists, require the exact instance Online in SSM.
   Send the proven sampler once through `AWS-RunShellScript` version `1`.
4. Poll through only bounded `InvocationDoesNotExist`. Require the exact
   120-sample numeric PASS summary. Any terminal refusal preserves the command
   ID, aborts before model deployment and cleans up.
5. Apply only the bound private workload. Require artifact/tree verification,
   exact live images, CUDA load, startup smoke and readiness disclosure.
6. Send only the synthetic WAV over loopback. Require HTTP 200, response schema,
   v0/platform-test disclosure and PHI-safe logs.
7. Persist and fsync `transcription: PASS` immediately with hashes only.
8. Only after that receipt exists, start the independent numeric memory sampler
   and send a second synthetic request. Persist memory `PASS` or
   `INCOMPLETE_MEASUREMENT`; memory failure cannot void transcription PASS.
9. On every outcome, remove/scale only the B6A workload, set GPU desired zero,
   prove all compute/workload state zero, then disarm the deadline and persist
   `cleanup: PASS`.
10. Publish immutable result evidence and audit receipt ordering, numeric peak
    L4 memory, cleanup zero and budget.

## Budget

- Project ceiling: `$300`.
- Committed guardrail: `$47.5288`.
- Existing reservation: `$15`; new reservation: `$0`.
- Conservative prior B6A GPU time: `2,590` seconds.
- Maximum remaining allowance: `4,610` seconds.
- Cumulative maximum: `7,200` seconds.
- Recorded g6.xlarge rate: `$1.0064/hour`.
- 003C-F maximum GPU estimate: `$1.2887`.

GPU desired is zero before and after. One GPU only.

## Outcomes and prohibitions

Permitted outcomes remain `B6A_PLATFORM_PROOF_COMPLETE`,
`INCOMPLETE_MEASUREMENT`, `BLOCKED_SSM_SAMPLER_SELF_TEST`,
`BLOCKED_PLATFORM_PROOF`, or `FAILED_CLOSED_EXECUTION`.

Even a complete B6A result does not pass B5, approve v0 or complete full B6.
Prohibited: execution before both review gates; more than one GPU or 4,610
seconds; IAM/Terraform resource, image, DRA, artifact, approved-ASR, model
registry, MLflow, production SSM, training or language changes; public traffic,
PHI or non-synthetic audio; editing any historical record.

No AWS or Kubernetes operation in this packet is authorized until independent
review PASS and later exact owner approval are both committed.
