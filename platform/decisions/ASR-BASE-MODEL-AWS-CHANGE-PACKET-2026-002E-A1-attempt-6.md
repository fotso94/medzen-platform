# ASR base-model AWS change packet 2026-002E-A1 — exact-gate continuation

Status: **DRAFT — INDEPENDENT REVIEW AND EXACT OWNER APPROVAL REQUIRED — NOT EXECUTABLE**

## Required approval phrase

> Approve ASR base-model AWS change packet 2026-002E-A1 only, continuing unconsumed numbered attempt 6 for one non-transferable 10,800-second offline evaluation attempt within the existing $10 ceiling and continuing ASR-EVAL-RUNTIME-RISK-ACCEPTANCE-2026-002 at SHA-256 06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c.

No AWS execution is authorized by this draft. After independent review PASS and
that exact phrase, a new write-once
`ASR-BASE-MODEL-AWS-AUTH-2026-002E-A1` must be committed. Then a new
committed read-only run of the complete `deadline_identity_and_acceptance`
stage against the actual A1 authorization, bindings and packet must PASS before
the attempt envelope or any AWS call.

## Continuity and write-once history

Packet 002E was independently reviewed and approved. Its authorization and
complete committed stage-1 dry run both passed. A separate local pre-attempt
check then found a later-stage mismatch before the attempt envelope and before
any AWS call:

- `bindings.security_gate` included four descriptive keys beyond the exact
  five-field executable gate;
- the cold rehearsal normalized the object to the five nested fields before
  invoking the runner;
- the live runner passes the top-level object unchanged to
  `validate_security_binding`;
- attempt 6 therefore would have refused deterministically at
  `image_publication_and_scan`.

The write-once refusal is
`platform/evidence/ASR-BASE-MODEL-PACKET-2026-002E-ATTEMPT-6-PRE-AWS-PREREQUISITE-REFUSAL.json`,
SHA-256
`69455cd2a4ff5831aef0487e8d13c8887c5f2274ec682d2dd82f5268e7795ff0`.
It records no attempt directory or envelope, zero stage receipts, zero runner
AWS calls, zero AWS mutations, GPU desired zero, zero GPU seconds and zero
cost. Numbered attempt 6 is unconsumed.

Packet 002E, AUTH-002E, its committed dry-run receipt, and the refusal remain
unchanged:

| Record | SHA-256 |
|---|---|
| Packet 002E | `131182330b04920474f1e99a38935a1694d8c324b7d2c8a98f078dd65dba03da` |
| AUTH-002E | `aa2366d3dffa70229a2e105990fb5f5be57d6ec771dce90ddc80a92aade38faf` |
| 002E complete stage-1 dry run | `ebd21c0ac90c148659c18c726ee817dd2793f80c7e6f122cfce1fd6abd0b71f0` |
| 002E pre-AWS refusal | `69455cd2a4ff5831aef0487e8d13c8887c5f2274ec682d2dd82f5268e7795ff0` |

## Class-level correction

The A1 bindings are
`platform/manifests/ASR-BASE-MODEL-PILOT-BINDINGS-2026-002E-A1.json`,
SHA-256
`82fc134d316c6df030accb16212f6f9b8fce9c70996a27a7d86de6b0b3cf6f9e`.

The correction has two parts:

1. the top-level `security_gate` is exactly equal to the five-field
   `digest_rescan_bindings.security_gate` and passes
   `scripts.asr_eval_digest_rescan.validate_security_binding` unchanged;
2. the cold rehearsal is forbidden to normalize or replace any bindings
   object. It passes the actual committed top-level A1 object to
   `execute_attempt`.

Descriptive scan roles and credential behavior remain in the packet and
authorization narrative, not inside the exact executable gate.

All 11 live executor module hashes remain identical to packet 002E. No live
executor source changed. Only the local cold-rehearsal driver changed; its
SHA-256 is
`fe973bc715ab1d89f408a467c5b35362d48cf1bef741f120b1bf54d55631d0ef`.

## Fresh committed-bindings rehearsal

The A1 cold receipt is
`platform/evidence/receipts/ASR-BASE-MODEL-2026-002E-A1-COLD/cold-rehearsal.json`,
SHA-256
`2947a598fe1e9281dd6ab8ea9a7f1c327454c136fea1f6ebd3a31fc62db968c8`.

Two runs are byte-identical. The receipt proves:

- actual committed bindings
  `82fc134d316c6df030accb16212f6f9b8fce9c70996a27a7d86de6b0b3cf6f9e`;
- `rehearsal_binding_normalization_permitted: false`;
- `PASS_EXACT_SECURITY_GATE_BINDING` on the actual top-level object;
- all 11 live executor module hashes pass;
- one full PASS and five injected refusals;
- wrong-digest, extra-finding, isolation, deadline and cleanup paths;
- every path returns to zero state;
- zero real AWS calls, zero kubectl calls, zero registry-scanning mutations.

A regression additionally asserts the committed A1 top-level object passes the
live exact validator byte-for-byte.

## Required post-approval committed gate

A1 does not reuse the earlier 002E dry-run receipt. After a new A1 authorization
is committed, the same full committed-artifact mechanism must produce:

`platform/evidence/ASR-BASE-MODEL-DEADLINE-IDENTITY-DRY-RUN-2026-002E-A1.json`

It must bind the A1 packet, bindings and authorization, validate all 11 live
executor hashes, enforce the reviewed-commit lineage, and record zero AWS calls,
zero mutations, no attempt start and no GPU. The live runner refuses if that
exact receipt is absent or differs.

## Unchanged scope

Everything else from packet 002E carries forward unchanged:

- exact immutable image index
  `sha256:506d6dd5933854fade34a05d5dfe6a35be7b97dc54da541f0814a3d3e4a6b2aa`;
- linux/amd64 child
  `sha256:85a82f348f6157adb36016d5b8d6155866ee0c4d40ae1faf4d80df677d50d14e`;
- risk acceptance SHA-256
  `06189414e82c7e497fe7b45d5395af0f03de523bc54c17e1b1e3ae91229d744c`;
- frozen 540-row, 47-language pilot;
- Whisper large-v3, Meta CTC-1B-v2 and Meta LLM-1B-v2;
- digest reconstruction, ECR Basic supplementary OS gate and pinned Docker
  Scout exact four-tuple Python gate;
- no upload, ECR scan-configuration mutation, Inspector, IAM or KMS change;
- S3/ECR-only private egress, no inbound traffic, internet, PHI or untrusted
  input;
- one GPU maximum, 10,800 seconds, existing $10 ceiling, scale to zero;
- no production, serving, training, B5, registry or MLflow change.

Attempts 1 through 5 remain consumed. Attempt 6 is continued, not duplicated;
no seventh attempt or transferred time is authorized.

## Deviations

None. The real-versus-rehearsal binding drift is removed rather than waived.

