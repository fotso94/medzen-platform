# B5 engineering report 2026-001

Status: `LOCAL_ENGINEERING_COMPLETE_EXTERNAL_ATTACHMENT_PENDING`

Date: 2026-08-03

## Governing interpretation

The original Base-v5 plan and B0-B5 handoff were reviewed together. Historical
B4 evidence remains unchanged. B4 is treated as complete only under its
owner-approved simplified, non-promotable exit. The selected float16 artifact
is a negative B5 test case, not a production candidate.

## Repository verification

The requested starting state was verified before changes:

- Branch: `master`.
- Commit: `d4a9f954f1a293e2f39ee1823452a9f0c036a3cd`.
- Commit count: `132`.
- Worktree: clean.
- Remotes: `0`.
- B4 committed spend: `$22.5288 / $100`.
- Unresolved B4 reservations: `0`.

Local milestone commits:

- `bc9c77f` — refusal-only B5 authorization.
- `4eeaab7` — five-state gate engine and promotion boundary.
- `4cdef17` — non-promotable dry-run signing domain.

## Current-artifact gate result

- Report:
  `platform/evidence/b5/gate-reports/25217157215ea979440187aa050772ffdf248d75e1ae823d5dcb72cb9d8def30.json`.
- SHA-256:
  `25217157215ea979440187aa050772ffdf248d75e1ae823d5dcb72cb9d8def30`.
- Bytes: `107817`.
- Engine commit: `4eeaab758d272696f39534c909d9f77a6ac52baf`.
- Generated twice: identical hash and bytes; second generation performed no
  overwrite.
- Overall: `BLOCKED`.

Gate-state counts:

- `PASS`: 11.
- `FAIL`: 4.
- `NOT_EVALUATED`: 31.
- `DEFERRED`: 6.
- `NOT_APPLICABLE`: 16.

Lingala, Luganda and Oromo fail the unchanged inherited absolute WER maximum of
`0.20`. The measured WERs are `0.7284`, `0.7002` and `0.6997`. Lingala's
untouched holdout supplies the fourth absolute-WER failure. Relative gains do
not override these failures.

Hausa, Igbo, Pidgin, Swahili and Yoruba remain `NOT_EVALUATED`; Acholi, Akan,
Amharic, Ewe, Fula and Shona remain `DEFERRED`. Wolof ASR is
`NOT_APPLICABLE`. English/French replay and code-switch gates remain
`NOT_EVALUATED`.

## Required zero side effects

- Models registered: `0`.
- Model versions created: `0`.
- Objects copied to `approved/asr/`: `0`.
- Language `artifact` changes: `0`.
- Language `approved_version` changes: `0`.
- Production SSM serving-alias changes: `0`.
- Deployments: `0`.
- B6: `BLOCKED`.

## Termination and decode controls

The three owner-approved termination contexts are executable and separate:

- Checkpoint selection: maximum one unique EOS/cap failure per language per
  checkpoint, audio SHA-256 identity, same-row double condition counted once,
  and immediate refusal on checksum recurrence at a later checkpoint.
- Untouched Lingala holdout: strict zero failures and no checkpoint-selection
  influence.
- Post-conversion: maximum one unique failure per language, no new/different
  checksum and no count increase.

Generated language YAML was not hand-edited. Decode state now resolves from
`registry/decode-approvals/v1.yaml`. Lingala, Luganda and Oromo remain
`NOT_EVALUATED` with `pending_experiment`; no strategy was invented. All ASR
`artifact` and `approved_version` fields remain null.

## Promotion-boundary engineering

- A dedicated promotion role, trust policy, approved-prefix policy and KMS key
  policy are versioned as design-only templates.
- A separate dry-run IAM policy contains no approved-ASR permission.
- Production writes require a signed PASS, complete/content-address tags,
  pinned SSE-KMS and `If-None-Match: *`.
- Real signing uses `RSA_3072`, `SIGN_VERIFY`, `RSASSA_PSS_SHA_256`,
  `MessageType=DIGEST`, base64 signature encoding and canonical JSON.
- Production and dry-run signatures have different mandatory purposes. A
  BLOCKED dry-run signature cannot verify as a promotion signature.
- The current content-addressed dry-run manifest is
  `a91eebbfffa80891a11c6ccdc278b157d7c645abdde491f5d50b23fc03bddace`.
  It explicitly binds the missing processor revision, adoption-object body
  hash and decode configuration as `NOT_EVALUATED` and remains `BLOCKED`.
- MLflow attachment code exposes no model-registration or stage-transition
  operation.
- SSM exercise is dry-run only and refuses the production namespace.

## Verification results

- B5 behavioral suite: `52 passed, 0 failed, 0 skipped, 0 deselected`.
- Canonical host suite: `748 passed, 0 failed, 0 skipped, 7 deselected`.
- Exact pinned container
  `sha256:b6060d239952dc1747b0f8ee80285670c5cc026cf2f00eab6fc5f1df73eaf237`,
  repository mounted read-only: `706 passed, 0 failed, 42 skipped, 7 deselected`.
- Pinned skips are the image's documented lack of `envsubst`, AWS credentials
  and the Git executable; host coverage supplies those checks.
- Terraform format: pass.
- Terraform validation with locked AWS provider: pass.
- Language regeneration check: `languages current (27 files)`.
- JSON policy parsing, Python compilation and Git whitespace checks: pass.

## Completion semantics

- B5.1 engineering and immutable report: complete locally; MLflow attachment is
  pending explicit approval of AWS packet `2026-001A`.
- B5.2 publication: `BLOCKED_NOT_APPLICABLE_FOR_CURRENT_ARTIFACT`.
- B5.3 registry promotion:
  `BLOCKED_NOT_APPLICABLE_FOR_CURRENT_ARTIFACT`.
- B6: `BLOCKED`.

This is B5 engineering readiness, not B5 promotion success.

## Open blockers

1. AWS packet `platform/decisions/B5-AWS-CHANGE-PACKET-2026-001.md` awaits
   explicit owner approval. No AWS write or KMS/IAM resource change has been
   made.
2. The repository still has no Git remote. A reviewed PR, protected merge,
   B7 CI and verified off-device Git backup remain blocked pending the hosting
   provider, private destination, organization/account, authentication method
   and branch-protection expectations.
3. Peak L4 GPU memory remains `NOT_MEASURED`. The 1,535,958,060-byte difference
   is on-disk artifact size only, not a GPU-memory claim. Float16 production
   resource deviation is not complete.
