# B6 AWS change packet 2026-012A — imported-state normalization and restoration continuation

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-10

Account/region: `558069890522` / `eu-central-1`

Required operator: `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested

Authorize a narrow successor to the fail-closed packet-2026-012 execution:

1. normalize only the imported Terraform representation of the already
   restored exact synthetic secret;
2. create the same two previously reviewed access boundaries in a separate
   fresh plan; and
3. continue the already reviewed rotation-not-reuse and verification sequence.

Packet 2026-012, its authorization, receipts and refusal evidence remain
immutable. This packet must not call `RestoreSecret` again and does not
authorize attempt 4, workers, Kubernetes, an ALB or any production change.

## Why packet 2026-012 stopped

Packet 2026-012 restored the exact ARN and persisted its receipt, then imported
the ARN into `aws_secretsmanager_secret.b6_client_keys[0]`. The first fresh plan
contained the two expected creates but also one secret update, so the reviewed
guard refused it and nothing was applied.

AWS already has all nine exact allocation tags. The AWS provider imported all
nine into `tags_all`, but filtered provider-default `Project` and `Environment`
out of the five-entry explicit `tags` field. The resource configuration also
declares those two keys explicitly. The plan therefore proposed only:

- `tags`: add the already-live `Project` and `Environment` values;
- `force_overwrite_replica_secret`: `null` to reviewed default `false`; and
- `recovery_window_in_days`: provider-import `null` to configured `7`.

Every other field, `tags_all`, exact ARN, name and KMS key remained identical.
The refused combined plan was not applied.

The fail-closed cleanup plan then contained only the exact secret delete, but
its guard also refused because imported state omits the write-only recovery
field it previously required to equal `7`. It was not bypassed or applied.

Immutable refusal evidence:
`platform/evidence/B6-PACKET-2026-012-REFUSED-IMPORTED-STATE-DRIFT.json`.

## Exact starting state

- exact secret ARN restored and present;
- only historical version
  `f78c8aa8-2765-4788-9928-dd1ba7c406bf` at `AWSCURRENT`;
- exact Terraform secret address imported at state lineage
  `cc991efe-2154-ac03-fe73-48392ec7a9a3`, serial `29`;
- resource policy absent;
- `medzen-orch-b6-client-secret-kms` absent;
- no new secret version and no local token;
- CPU/GPU desired and instances zero;
- no packet-2026-012 Terraform plan applied.

Any difference refuses this packet.

## Stage 0 — adopt, do not restore

Use the successor-bound runner's `adopt` phase. It validates the exact restored
ARN, KMS key, nine tags, sole historical version and absent local token, then
persists a write-once `restore.json` carry-forward receipt in the new
`B6-2026-012A-LIVE` directory. It performs no AWS mutation and cannot invoke a
restore phase.

## Stage A — normalize only the imported secret representation

Generate a fresh, state-refreshed plan targeting only
`aws_secretsmanager_secret.b6_client_keys[0]` with
`enable_b6_client_keys=true`.

Required result: exactly `0 add / 1 update / 0 destroy`. The prospective guard
in `normalize` mode proves:

- the exact account, ARN, ID, name, KMS key and description;
- `tags_all` is the same exact nine-entry map before and after;
- explicit `tags` changes only from the exact five imported non-default keys to
  the exact seven configured keys;
- only `force_overwrite_replica_secret`, `recovery_window_in_days` and `tags`
  differ;
- those first two values move only from `null` to `false` and `7`;
- no after value is unknown; and
- no other resource action exists.

A live preview already produced `0/1/0`, plan SHA-256
`c328ba1fefee8a5d829ecead4d7b932502456275add902f13a925e54bbfb7b3c`,
and passed:

`PASS_B6_CLIENT_SECRET_NORMALIZATION_PLAN changes=1 add=0 update=1 destroy=0`

The preview is not retained and cannot be applied. Execution must regenerate a
fresh plan and apply only that newly guarded saved plan. Immediately persist
`terraform_normalization.json`, then require live AWS tags byte-for-byte equal
to the pre-apply nine-tag map and a residual secret-only plan of `NO_CHANGES`.

## Stage B — reconstruct the two access boundaries separately

Only after Stage A is receipted, generate a second fresh targeted plan for:

- `aws_secretsmanager_secret.b6_client_keys[0]` — exact no-op;
- `aws_secretsmanager_secret_policy.b6_client_keys[0]` — create; and
- `aws_iam_role_policy.b6_client_keys_kms[0]` — create.

Required result: exactly `2 add / 0 change / 0 destroy`. The existing
`reconcile` guard must now validate the fully known resource policy, the exact
orchestrator-only reader plus all-other-principals Deny, public-policy blocking,
and KMS decrypt only through Secrets Manager with the exact encryption context.

Apply only the second newly guarded saved plan. Persist
`terraform_reconciliation.json` immediately with both plan hashes, state
lineage/serial, exact addresses, policy hashes and apply counts. Require a
residual targeted plan of `NO_CHANGES`.

Splitting the plans is mandatory: no policy whose value is unknown during plan
may be applied.

## Rotation and verification

After all three prerequisite receipts exist, run the successor-bound `rotate`
and `verify` phases exactly as reviewed:

- generate a new 32-byte CSPRNG token, 43-character unpadded base64url;
- create only `/private/tmp/medzen-b6-6-client-token`, mode `0600`, 44 bytes
  including LF;
- store canonical JSON containing only its lowercase SHA-256 for synthetic
  client `b6-window-probe`;
- persist `rotation.json` immediately after `PutSecretValue`;
- require the new version at `AWSCURRENT` and the historical version at
  `AWSPREVIOUS`;
- remove `AWSPREVIOUS` from the historical version and prove it has no stages;
- prove the operator's `GetSecretValue` is explicitly denied; and
- persist `verification.json`.

The historical plaintext is never read or reused. The orchestrator live read
remains deferred to attempt 4.

## Corrected fail-closed cleanup

The prospective cleanup guard accepts only a non-empty subset of deletes for
the exact secret, resource policy and KMS inline policy. For the secret, it
requires the exact ARN/name/KMS key, exact nine-entry `tags_all`, an explicit
tag map equal to either the imported five-key form or normalized seven-key
form, and permits the provider's write-only recovery field to be only `null`
or `7`.

Any stage failure removes the exact local token, generates a fresh guarded
`enable_b6_client_keys=false` plan, applies only that saved plan, and proves
seven-day recoverable deletion plus absent state/policies. Force deletion is
prohibited. A cleanup failure is receipted and leaves attempt 4 blocked.

## Cost and budget continuity

- Aggregate ceiling: `$300`.
- Recognized committed guardrail: `$63.5288`.
- Existing single B6.6 reservation: `$10` under
  `B6-INTEGRATION-WINDOW-2026-001`.
- New reservation: `$0`.
- Maximum incremental secret cost stays within `$0.10` of that reservation.
- CPU/GPU remain at zero.

## Source bindings

| Source | SHA-256 |
|---|---|
| `platform/decisions/B6-AWS-CHANGE-PACKET-2026-012-synthetic-secret-token-restoration.md` | `aa4beadbdd3673d3fa124db585e137a49d03022b74654e8df0ac5af7d2f60949` |
| `platform/decisions/B6-AWS-AUTH-2026-012-synthetic-secret-token-restoration.json` | `971a123f5cda00da298172ffc1b7dfd081a77a1ccb85e8085f37cc30cee95aff` |
| `platform/evidence/B6-PACKET-2026-012-REFUSED-IMPORTED-STATE-DRIFT.json` | `19be424d2aee39f862c5e6de3b2335a87a8031b5e84e3fd6a50d03a465164c69` |
| `platform/evidence/B6-CLIENT-SECRET-SUCCESSOR-PREPARATION-2026-001.json` | `6a83e95dc4aadefbabba799593fdadb392ace0726a54265bd96bda564c3dbb75` |
| `platform/evidence/receipts/B6-2026-012-LIVE/restore.json` | `d47127c1592b0c63d7c4f5e99c70316027b662039afd387928af6ba27e9b4f34` |
| `platform/evidence/receipts/B6-2026-012-LIVE/terraform_reconciliation.json` | `49acce009f2866460508127c1c3c0f9199b525c53c2f2c21b50fd632e0f24433` |
| `platform/evidence/receipts/B6-2026-012-LIVE/cleanup.json` | `e3e0684e403938bef024c98eed0c81b38d0126f825d5f32e3710597fcf13940f` |
| `platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-001.json` | `ba81fcaa8414b1d0335be833090dffde3653199e918fcfd32a06501b6ece10de` |
| `platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-002.json` | `1dc091bf4ee3bcbb93b329839a341c66a787e26e79e4fb2b8de97a34364dc291` |
| `scripts/check_b6_client_secret_restoration_plan.py` | `9bb596216bcd9bd18440df9e698574021a278db8c7314a21ebced3d6d04d1f0e` |
| `scripts/run_b6_client_secret_restoration.py` | `baa58777cd05a3edad5f5236013ca5e3556dd654026790a6f2599022981422cc` |
| `tests/test_b6_client_secret_restoration.py` | `0d80c6ad2e6b33149ce38844b28b882b60d1924cee4f2c403ab853bdf8c03e63` |
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |

These are the final source bindings. Independent review and the owner
authorization must bind the final packet SHA-256 and prepared
commit. Any mismatch refuses execution.

## Explicit prohibitions

No rerun of `RestoreSecret`, no application of either refused packet-2026-012
plan, no combined unknown-policy plan, no secret-value read, no real client
credential, no workers, Kubernetes, controller, DRA, ALB, Fargate, ECR, SSM,
model, `approved/`, production serving pointer, provider call, training or PHI.

Attempt 4 remains a separate packet. It can be finalized only after this
packet produces immutable new-version and token-hash evidence.

## Approval phrase

After independent review bound to the final packet SHA-256 and prepared commit,
the only valid owner phrase is:

`Approve B6 AWS change packet 2026-012A only.`
