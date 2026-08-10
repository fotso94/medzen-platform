# B6 AWS change packet 2026-012 — synthetic secret and token restoration

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-10

Account/region: `558069890522` / `eu-central-1`

Required operator: `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested

Authorize restoration of the exact recoverable B6.6 synthetic client-key
secret left by packet 2026-010 cleanup, reconstruction of its two previously
reviewed access boundaries, and rotation to one newly generated synthetic test
token. The historical token hash is not reused and the historical plaintext is
never read.

This is a no-compute prerequisite packet. It does not authorize CPU/GPU
capacity, Kubernetes, a controller, an ALB, an integration-window retry, a
production SSM pointer, a model change or any provider call.

## Why a separate packet is required

Packet 2026-010 cleanup correctly removed the local plaintext token, deleted
the resource policy and supplemental orchestrator KMS policy, removed the
three Terraform resource addresses, and scheduled seven-day recoverable secret
deletion. Packet 2026-011 changed only the controller IAM policy and left that
secret state untouched.

The next bounded window needs a new synthetic token. Reusing the historical
hash without its lost plaintext would fail authentication, and reading the old
secret value would violate the no-plaintext recovery boundary.

## Exact live recovery precondition

Read-only verification on 2026-08-10 established:

- secret ARN:
  `arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE`;
- state: pending recoverable deletion since `2026-08-09T22:06:53.769Z`;
- recovery deadline: `2026-08-16T22:06:53Z`;
- KMS key:
  `arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57`;
- one historical version only:
  `f78c8aa8-2765-4788-9928-dd1ba7c406bf` at `AWSCURRENT`;
- resource policy absent;
- `medzen-orch-b6-client-secret-kms` absent;
- all three Terraform resource addresses absent;
- local token `/private/tmp/medzen-b6-6-client-token` absent;
- CPU and GPU desired/instances remain zero.

The packet expires at the recovery deadline. If AWS no longer reports this
exact recoverable secret and version state, execution refuses; a different
secret or a new secret ARN requires a new packet.

Preparation evidence:
`platform/evidence/B6-CLIENT-SECRET-RESTORATION-PREPARATION-2026-001.json`.

## Exact execution and receipt order

Every successful stage is persisted immediately using create-exclusive,
fsync-backed receipts under
`platform/evidence/receipts/B6-2026-012-LIVE/`.

1. **Bindings and zero state.** Require an owner authorization record with ID
   `B6-AWS-AUTH-2026-012`, exact final packet and manifest hashes, reviewed
   commit, clean starting worktree, exact account/operator, no local token,
   CPU/GPU at zero and the live recovery precondition above.
2. **Restore.** Run the packet-bound runner's `restore` phase. It validates the
   exact ARN, KMS key, tags and sole historical version before restoration and
   again validates the restored state. Persist `restore.json` before any later
   operation. It does not call `GetSecretValue`.
3. **Terraform reconciliation.** Import only the restored ARN at
   `aws_secretsmanager_secret.b6_client_keys[0]`. Generate a fresh,
   state-refreshed targeted plan with `enable_b6_client_keys=true`. The guard
   must prove exactly `2 add / 0 change / 0 destroy`:
   `aws_secretsmanager_secret_policy.b6_client_keys[0]` and
   `aws_iam_role_policy.b6_client_keys_kms[0]` are creates, while the imported
   secret is an exact no-op. Apply that exact saved plan once. Persist
   `terraform_reconciliation.json` immediately with plan hash, state
   lineage/serial, addresses, apply counts and policy hashes. Require a
   residual targeted plan of `NO_CHANGES`.
4. **Rotate.** Only after the Terraform receipt exists, validate the live
   resource policy and KMS inline policy. Generate 32 random bytes with the OS
   CSPRNG, encode as 43-character unpadded base64url, and create the plaintext
   file exclusively at `/private/tmp/medzen-b6-6-client-token`, mode `0600`,
   44 bytes including LF. Publish canonical JSON containing only the new
   lowercase SHA-256 for client `b6-window-probe`. Persist `rotation.json`
   immediately after `PutSecretValue` succeeds. No plaintext appears in output,
   receipts, Terraform or Git.
5. **Verify.** Require the new version at `AWSCURRENT` and the historical
   version at `AWSPREVIOUS`; then remove `AWSPREVIOUS` from the historical
   version and prove it has no staging labels. Require the operator's
   `GetSecretValue` to be explicitly denied. Persist `verification.json`.
   The orchestrator's live Pod Identity read remains deferred to the new
   bounded window.
6. **Close.** Reverify CPU/GPU and all window resources at zero, production
   serving pointer absent, `approved/` unchanged, source hashes unchanged and
   all four receipts durable. Commit immutable packet evidence containing only
   hashes and non-secret metadata.

Required commands after authorization include:

`python scripts/run_b6_client_secret_restoration.py restore --authorization <owner-auth.json> --receipts-dir platform/evidence/receipts/B6-2026-012-LIVE --apply`

`python scripts/check_b6_client_secret_restoration_plan.py --mode reconcile <fresh-plan>`

`python scripts/run_b6_client_secret_restoration.py rotate --authorization <owner-auth.json> --receipts-dir platform/evidence/receipts/B6-2026-012-LIVE --apply`

`python scripts/run_b6_client_secret_restoration.py verify --authorization <owner-auth.json> --receipts-dir platform/evidence/receipts/B6-2026-012-LIVE --apply`

## Exact access boundary

The resource policy:

- permits `secretsmanager:GetSecretValue` only to
  `arn:aws:iam::558069890522:role/medzen-orch-role`;
- explicitly denies that action to every other principal; and
- retains `block_public_policy = true`.

The additive inline KMS policy permits `kms:Decrypt` only through
`secretsmanager.eu-central-1.amazonaws.com` and only for encryption context
`arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys*`.
No KMS key or IAM role is created, and no other service-role policy changes.

## Fail-closed cleanup

Any missing receipt, drift, unknown state, identity mismatch, expired recovery
window, extra plan action, policy mismatch, publication error, readable secret
or version-transition mismatch stops execution. A refusal receipt is preserved.

If restoration did not occur, cleanup has nothing to change. If restoration
occurred but Terraform import did not, remove the exact local token if present
and reschedule only the exact secret for seven-day recoverable deletion. If the
secret entered Terraform state, generate a fresh targeted
`enable_b6_client_keys=false` plan. The same guard in `cleanup` mode accepts
only a non-empty subset of exact deletes for the secret, resource policy and
KMS inline policy; apply only that saved plan. Verify the secret is pending
recoverable deletion, all three Terraform addresses and the KMS policy are
absent, and the local token is absent.

Force deletion without recovery is prohibited. Cleanup starts no compute. A
cleanup failure leaves the later window blocked and must be recorded rather
than hidden.

## Cost and budget

- Aggregate ceiling: `$300`.
- Recognized committed guardrail: `$63.5288`.
- Existing single B6.6 reservation: `$10` under
  `B6-INTEGRATION-WINDOW-2026-001`.
- New reservation: `$0`.
- Maximum incremental secret cost within the existing reservation: `$0.10`.
- CPU/GPU desired and instances remain zero throughout this packet.

The reservation is a ceiling, not a claim of billed spend.

## Successor-window rule already made durable

The owner-directed post-create tag-mutation handling is recorded separately in
`platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-001.json`. The next
window packet must bind it and its tested classifier. Only exact post-create
`AddTags`/`RemoveTags` denials on the B6 listener or listener-rule may become a
receipted `WARNING_NON_FATAL`, and only after internal ALB, exact security
group, listener, healthy target and isolated readyz success are proven. Create,
creation-tag, unrelated-resource, functional or cleanup denials remain fatal.

The successor window cannot be finalized yet because it must bind the new
secret version ID, token hash and immutable packet-2026-012 execution evidence.

## Exact source bindings

| Source | SHA-256 |
|---|---|
| `platform/evidence/B6-LBC-IAM-LIFECYCLE-AWS-EXECUTION-2026-001.json` | `da38f29ec5cd218620e2c649a19500b24db04b7ecd0b55a873b61bb1fce09236` |
| `platform/evidence/B6-CLIENT-SECRET-RESTORATION-PREPARATION-2026-001.json` | `df6dc493770a06d49900280e6983cd3b9922e64d601332e92c54b6b5b6819196` |
| `platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-001.json` | `ba81fcaa8414b1d0335be833090dffde3653199e918fcfd32a06501b6ece10de` |
| `scripts/check_b6_client_secret_restoration_plan.py` | `a8e95327a4fbef70f043a446abc44c4f164530cf63057a02693245bb31daeef3` |
| `scripts/run_b6_client_secret_restoration.py` | `fdaa3f8f30ca4d29badaa3332a98306e8df8314f4afad5b3d33ee4210d847c40` |
| `tests/test_b6_client_secret_restoration.py` | `48c5302574dcec24d0fb581609cca004f6d661e3cf9f45f3ec53fb0a0b5bc0b7` |
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `platform/evidence/B6-CLIENT-API-KEYS-2026-001.json` | `6120c7a9b82dd51a2ceccd504156c8448c0322c5ba31e65334505caf3856c2e0` |
| `platform/evidence/B6-PACKET-2026-010-REFUSED-ALB-LISTENER-IAM.json` | `4ea2234f6803049d6d4afd4a24a2f03f118c1c45c090b173f61cfef8506fdabf` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/decisions/B6-LBC-TAG-MUTATION-RUNTIME-RULE-2026-001.json` | `a77d229f97939d74d5a161a6c1bb7a0a2514a1870fd0e1b63d20445ec425e16c` |
| `scripts/b6_6_lbc_tag_warning.py` | `0cce4e39f960270976120987af57d809b1d871a6127f44fca12470aadd21fd10` |
| `tests/test_b6_lbc_tag_warning.py` | `38621df49ae6cc56e6f1612ddc04095ffcf0a3c38a4471bf8b0fe22c6518a123` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |

Independent review and the owner authorization record must bind the final
packet SHA-256 and prepared commit. Any mismatch refuses execution.

## Explicit prohibitions

No workers, Kubernetes mutation, controller, DRA, ALB, Fargate task, ECR
change, SSM change, model change, `approved/` write, production SSM write,
Bedrock/Fish call, real client credential, training, PHI or integration-window
retry is authorized.

## Approval phrase

After independent review bound to the final packet SHA-256 and prepared commit,
the only valid owner phrase is:

`Approve B6 AWS change packet 2026-012 only.`
