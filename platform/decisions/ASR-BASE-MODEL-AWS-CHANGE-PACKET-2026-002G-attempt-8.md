# ASR base-model AWS change packet 2026-002G — external-workdir fidelity successor

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002G only, authorizing numbered attempt 8 for one non-transferable 10,800-second offline evaluation attempt within a fresh $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

No AWS or Kubernetes execution is authorized by this draft. After independent
review PASS and that exact owner phrase, a write-once
`ASR-BASE-MODEL-AWS-AUTH-2026-002G` must be committed. A complete read-only
`deadline_identity_and_acceptance` validation against the actual committed
authorization, bindings and packet must then PASS and be committed before the
attempt envelope or any AWS call.

## Why attempt 7 stopped

Attempt 7 was consumed at `deadline_identity_and_acceptance` with
`REVIEWED_CLEAN_COMMIT_REQUIRED`. Its envelope existed, but the input freeze,
security, artifact, endpoint, GPU, row and aggregate stages never ran. Cleanup
passed with CPU/GPU desired zero, zero endpoints, zero volumes, zero evaluation
namespaces, zero deadline actions, zero staged objects and `$0` incremental
cost. The write-once refusal is:

- `platform/evidence/ASR-BASE-MODEL-PACKET-2026-002F-ATTEMPT-7-WORKTREE-BOUNDARY-REFUSAL.json`
- SHA-256 `f1e26bb8c4a004b0c88499f7460a1a1d895cf8d74f84063b8b43cd6f7bad1d78`

Packet 002F, AUTH-002F, its stage-1 receipt, attempt envelope, stage receipts
and terminal evidence are unchanged. Attempt 7 may not be reused.

## Root cause

The 002F live invocation placed its runtime evidence directory inside the
reviewed repository. The runner configured its diagnostics journal and wrote
the attempt envelope before the live stage invoked the clean-worktree gate.
The gate therefore correctly detected the runner's own untracked directory and
refused. The retained diagnostic reports only:

```text
?? platform/evidence/receipts/ASR-BASE-MODEL-2026-002F-A7-LIVE/
```

This was a live/rehearsal filesystem-order divergence, not AWS, packet,
authorization, image, Scout, risk-acceptance or input drift.

## Class-level correction

One runner-owned bootstrap now governs both live and rehearsal execution:

1. Resolve and refuse any runtime workdir inside the reviewed worktree.
2. Read and require a clean reviewed Git HEAD before creating any runtime
   directory, diagnostics journal, receipt, envelope or result.
3. Create the externally located workdir exactly once.
4. Use `<external-workdir>/receipts` as the canonical write-once receipt store.
5. Run the same prerequisite, envelope, stage, receipt and cleanup ordering.
6. Commit externally written receipts only after the terminal run, mirroring
   the standing receipt-last rule.

The live CLI and cold rehearsal both call:

- `scripts.asr_base_model_pilot_runner.build_attempt_context`
- `scripts.asr_base_model_pilot_runner.execute_attempt`
- `pipeline.asr_base_model_pilot_receipts.ReceiptStore`

The runner records its filesystem order in the terminal result. The new
regression suite proves that an in-worktree path refuses before creation, a
dirty reviewed tree refuses before external runtime creation, and a complete
fake-operations attempt writes every envelope/receipt/result externally while
leaving the reviewed repository clean.

## Rehearsal fidelity boundary

The standing boundary is **everything except paid external calls**.

The final cold rehearsal does not fake:

- path resolution or external-workdir enforcement;
- Git clean-HEAD verification;
- context construction and receipt-store pathing;
- workdir, diagnostics, envelope, stage-receipt and result filesystem effects;
- stage sequence, refusal handling, cleanup or receipt dependencies;
- plan, workload, module-integrity, security-binding or authorization-schema
  validation.

It replaces only AWS and kubectl calls with `FakeOperations`. One clean PASS
and the five existing injected refusals all use the same runner and external
filesystem ordering as live. Any future rehearsal shortcut around the shared
bootstrap violates this packet.

## Immutable bindings

The prospective bindings are:

- `platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002G.json`
- SHA-256 `ff2fd9c3ac598eb2d179aac1a276fd3b406a917f68cb702f4783cacb89952c98`

All 13 executor module hashes are required on attempt 8. Conditional omission
or normalization is prohibited. The final receipt path is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002G-COLD/cold-rehearsal.json`;
its SHA-256 will be bound here only after the final clean-commit rehearsal.

## Unchanged security and evaluation subject

- risk acceptance SHA-256:
  `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c`
- OCI index:
  `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`
- linux/amd64 child:
  `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`
- exact Scout preflight SHA-256:
  `cabd8497de52e02f180c5f9caf455413be7de6006fb281a65c122c109fb3bf4b`
- pilot bundle SHA-256:
  `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee`
- pilot row-list SHA-256:
  `2170eb450ae9b42c64e02f8753469eb7d74b7b3f2363ae3f770fbd3062e488b6`
- frozen scope: 540 rows across 47 languages
- scanner: Docker Scout `1.18.3`, commit
  `aa68fc25c596bea659d54867443238fd30218d23`
- required findings: 0 critical and exactly the four accepted PyTorch HIGH
  tuples; ECR Basic remains the supplementary 0-critical/0-high OS gate.

No image rebuild/upload, risk re-acceptance, input change, model change,
tokenizer change, finding waiver or registry-scanning mutation is introduced.

## Exact execution scope

Authorized only after every post-review gate:

- numbered attempt 8 only;
- one GPU node maximum for 10,800 non-transferable seconds;
- fresh maximum cost ceiling `$10` within the `$300` project ceiling;
- live workdir entirely outside the reviewed repository;
- read/verify the existing immutable ECR image; no upload;
- exact ECR-child reconstruction plus the digest-verified archive Scout scan;
- create-only research asset staging and frozen 540-row pilot;
- temporary private endpoints, strict network policy, evaluation volume and
  workload; S3/ECR-only egress and no inbound path;
- mandatory cleanup and CPU/GPU desired zero on every terminal outcome;
- post-run copy and commit of safe immutable evidence only.

Explicitly prohibited:

- reuse of attempts 1–7 or any ninth attempt;
- any live workdir, receipt store, diagnostics journal or envelope inside the
  reviewed repository;
- Inspector Enhanced or registry scanning configuration mutation;
- image rebuild/upload, source, image, model, tokenizer, input, finding or
  scanner drift;
- IAM/KMS changes, internet egress, PHI, untrusted input or inbound traffic;
- serving, production, training, promotion, `approved/asr`, production SSM,
  MLflow registration or language-registry mutation;
- citing the offline risk acceptance as serving precedent.

## Required review and post-approval gates

Independent review must verify source-level shared bootstrap usage, the three
workdir regression cases, all-module bindings, unchanged historical hashes,
unchanged image/risk subject, and the final receipt-last cold rehearsal.

After exact owner approval:

1. Write and commit AUTH-2026-002G.
2. Run and commit the complete read-only stage-1 validation against the actual
   committed authorization, bindings and packet.
3. Require the committed Scout preflight and live Scout credentials.
4. Invoke attempt 8 only with a fresh, nonexistent workdir outside the
   repository.
5. Copy safe receipts into Git only after the terminal run and zero-state
   verification.

## Deviations

None. Historical records remain write-once. The only external behavior change
is the location and shared ordering of runtime evidence filesystem effects.

