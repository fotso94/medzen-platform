# ASR base-model AWS change packet 2026-002A — attempt-2 preflight successor

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

Usable only after independent review PASS of this committed packet and the
exact hashes below:

> Approve ASR base-model AWS change packet 2026-002A only, authorizing numbered attempt 2 for one non-transferable 10,800-second offline evaluation attempt within the existing $10 reservation and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

This draft is not authorization. A new write-once
`ASR-BASE-MODEL-AWS-AUTH-2026-002A` must capture the exact post-review phrase,
this packet SHA-256, the reviewed commit and expiry before any AWS mutation.

## Purpose

Authorize only the single unused numbered attempt 2 after attempt 1 refused in
the first preflight stage. The scientific scope, inputs, evaluation image,
scan findings, risk acceptance, cost ceiling, AWS boundary, deadline, cleanup
and terminal outcomes remain those independently reviewed in packet 2026-002.

This is a representation and invocation correction, not a new compute scope.
Attempt 1 is not reusable. No seconds or allowance transfer from attempt 1 is
claimed.

## Write-once history

| Record | SHA-256 | Treatment |
|---|---|---|
| Packet 2026-002 | `8485592d0b082a84c9405304774128cf907d7b0c7f0f99d227fae20b562c29d9` | Unchanged |
| Authorization 2026-002 | `22bff1fb34f161ea86ab123d14f176080689e3e19fb501b35f4a1850529532f0` | Unchanged and not reused |
| Attempt-1 refusal | `cd7ee9e9abd089ea4b5fddde1bddd3ff94cd856f95ab4ea71f92c6f933def215` | Valid terminal evidence |
| Attempt-1 factual addendum | `3787f5a5e31c097c75e25113c10b909223c29a0e6c02c2a37b2c1be12c19a954` | Corrects mutation-call count without altering the refusal |
| Risk acceptance 2026-002 | `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c` | Continued for the unchanged image, subject to original expiry rules |
| Scan subject 2026-004 | `d5b95462e0421092b4b2cd21329c2217e781ab5542f8724c105479bbb8359266` | Unchanged |

Attempt 1 created no AWS resource, opened no reservation, started no GPU and
incurred no compute cost. Cleanup did issue one no-op
`UpdateNodegroupConfig(0,1,0)` enforcement call, documented by the factual
addendum and its CloudTrail event. Its cleanup receipt is PASS with CPU/GPU
desired zero, endpoints zero and volumes zero.

## Exact successor bindings

| Binding | Value |
|---|---|
| Reviewed executor commit | `b2252dd703150dda3f9c6eb0618c4724f9e4118b` |
| Bindings manifest | `platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002A.json`, SHA-256 `569f77fd44514a006015e1422cf7b99cc16555d360967669d96462e63226c40c` |
| Qualification | `platform/evidence/B6-ASR-EVAL-RUNTIME-LOCAL-QUALIFICATION-2026-004.json`, SHA-256 `31e877fe319964ef7972a74bfdbd68191a293a0d92f6c7a846922d54a2265b41` |
| Cold rehearsal | `platform/evidence/receipts/ASR-BASE-MODEL-2026-002A-COLD-002/cold-rehearsal.json`, SHA-256 `78d009db1197231fdf4ed21e7f84ea3268c084b2c32f3837caf4e644b473dd1a` |
| Input freeze | `f59692a7ab5da0a9b257792e04813ec2c4c2317ffb1d68d7e5586789afa9a0ad` |
| Pilot bundle | `1cdca3e75195c7c7417550154e36a1f372715e17efd13c835c87ee503fa84eee` |
| Cost registry | `platform/finance/COST-REGISTRY-2026-006.json`, SHA-256 `d80b1a00d87baa44e162078ff8b51fbda99b3e8733974761e156318e8429e9da` |

Exact unchanged image:

- local tag `medzen-asr-eval-runtime:pilot-5d1b8a0`;
- OCI index `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`;
- linux/amd64 child `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`;
- local scan `0 critical`, exactly the four owner-accepted PyTorch highs.

The executor correction changes no file under
`services/asr-eval-runtime/`, so it does not change the evaluated image or its
exact-image risk subject. The authoritative ECR child scan remains mandatory
before compute and refuses on any drift.

## Attempt-1 diagnosis and corrections

Attempt 1 refused because authorization 2026-002 recorded the approved limits
under `owner_approval`, while the executor expected top-level `attempts`.
The refusal receipt lost that typed reason because file-path invocation created
two Python identities for `OperationRefusal`. A temporary `.venv` symlink also
would have violated the reviewed-worktree cleanliness check if the earlier
predicate had passed.

All three classes are corrected prospectively:

1. `validate_authorization_payload` requires top-level `attempts`, explicit
   `authorized_numbers`, exact seconds, non-transferability and the requested
   attempt number.
2. Refusal reason/outcome retention is attribute-based, so duplicate module
   identity cannot erase safe diagnostics.
3. Execution uses `python -m scripts.asr_base_model_pilot_runner` with the
   dependency interpreter outside the reviewed worktree. No symlink or other
   dependency artifact exists inside it.
4. Pre-GPU cleanup uses read-only zero verification. It calls
   `UpdateNodegroupConfig` only when durable state proves the attempt scaled
   the GPU, eliminating the no-op mutation found in attempt 1.

The successor authorization must contain exactly:

```json
{
  "attempts": {
    "authorized_numbers": [2],
    "maximum": 1,
    "seconds_each": 10800,
    "non_transferable": true
  }
}
```

## Qualification

Focused suites: **55 passed, 0 failed, 0 skipped, 0 deselected**.

Two cold-rehearsal runs were byte-identical. They prove:

- authorization schema PASS for attempt 2 only;
- exact attempt-2 plan and workload render;
- one complete `PASS_PILOT` fake execution;
- isolation, deadline and cleanup refusal injections;
- precise typed refusal retention;
- zero fake state after every outcome;
- zero real AWS calls, kubectl calls or mutations.

## Execution boundary

If approved, execute from a clean detached worktree at the reviewed executor
commit, with the external interpreter and module invocation stated above.
Attempt 2 must arm its 10,800-second scale-to-zero action before later mutation.

All packet-2026-002 operations and prohibitions carry forward unchanged. In
particular:

- one GPU node maximum; CPU remains zero;
- strict network isolation before torch import;
- exact 540-row, three-model offline pilot only;
- no IAM/KMS changes, serving, production SSM, `approved/asr/`, MLflow model
  registration, language registry mutation, training or full-suite scoring;
- status-keyed cleanup in `finally` after every result;
- no third attempt and no reuse of attempt 1.

The permitted terminal outcomes remain `PASS_PILOT`,
`INCOMPLETE_MEASUREMENT`, `BLOCKED_INPUT_FREEZE`, `BLOCKED_IMAGE_SCAN`,
`BLOCKED_NETWORK_ISOLATION`, and `FAILED_CLOSED_EXECUTION`.

## Cost and expiry

This requests no new reservation. It uses only numbered attempt 2 within the
existing `$10` packet reservation. Maximum additional GPU time is 10,800
seconds; unused seconds are non-transferable. The successor authorization must
expire no later than the risk record's original seven-day lifetime rule and is
void on any source, image, input, model, tokenizer, finding or severity drift.

## Deviations

1. Only attempt 2 is requested; attempt 1 is terminal and cannot be reused.
2. Executor source changes are outside the evaluation-image build context, so
   the exact scanned image and risk record are continued rather than rebuilt.
3. The authorization representation and runner invocation are made explicit;
   no scientific, AWS, network, cost or safety boundary is broadened.

No other adaptation is made.
