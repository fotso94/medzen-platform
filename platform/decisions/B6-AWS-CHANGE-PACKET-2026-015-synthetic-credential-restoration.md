# B6 AWS change packet 2026-015 — post-attempt-4 synthetic credential restoration

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: 2026-08-10
Account/region: `558069890522` / `eu-central-1`
Required profile/operator: `medzen` / `arn:aws:iam::558069890522:user/s.fotso`

## Decision requested

Authorize one narrow, no-compute restoration of the exact recoverable B6.6
synthetic client-key secret, reconstruction of its two reviewed access
boundaries and rotation to a newly generated synthetic token.

This packet reuses the staged and fail-closed machinery proven by packet
2026-012A. It does not authorize the B6.6 integration window, CPU/GPU capacity,
Kubernetes, an ALB, Fargate, a production SSM pointer, a model change or a real
client credential.

This draft itself authorizes no AWS or Terraform mutation. Execution requires
an independent review bound to the final packet SHA-256 and prepared commit,
followed by a new owner authorization record `B6-AWS-AUTH-2026-015` containing
the exact packet, manifest, cost and source bindings below.

## Immutable history and packet numbering

PR #24 is merged at published-master commit
`ab6208cdd9e45e6950069f1589af63fc4654f7c0` after independent review PASS.
The reviewed draft window packet 2026-014 remains byte-identical at SHA-256
`f31cb8f36d76d32884639bbe8bfb750ca807a92847d24f0abf4e1eef7d8c6428`.

Packet 2026-015 is a separate prerequisite. It does not amend or authorize
draft packet 2026-014. After this packet produces immutable new-version and
token-hash evidence, the final executable window must be a new versioned
packet. No prior packet, authorization, manifest, receipt or evidence record
may be rewritten.

## Evidence-bound starting state

Packet-2026-013 cleanup is immutable at:

- path:
  `platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json`;
- SHA-256:
  `daa679e744738094059a6faae53e5ebb7d44dd920c4da1ba5bac71100049590a`;
- cleanup: `PASS`;
- synthetic secret: scheduled for seven-day recoverable deletion;
- local token: removed;
- Terraform window/secret resources: absent;
- CPU/GPU desired and instances: zero; and
- production serving pointer: absent.

The execution preflight must re-read AWS and refuse unless all of the following
are simultaneously true:

- the exact ARN is still pending recoverable deletion:
  `arn:aws:secretsmanager:eu-central-1:558069890522:secret:medzen/client-api-keys-NxZGxE`;
- its exact KMS key and nine allocation/classification tags are unchanged;
- version `d09d567e-9bde-482a-b95a-3cab990a1006` is the only `AWSCURRENT`;
- older version `f78c8aa8-2765-4788-9928-dd1ba7c406bf` has no stage;
- no other secret version exists;
- the secret resource policy and
  `medzen-orch-b6-client-secret-kms` inline policy are absent;
- all three synthetic-secret Terraform addresses are absent;
- `/private/tmp/medzen-b6-6-client-token` is absent;
- both node groups and their exact auto-scaling groups are active at desired
  zero with zero instances; and
- `/medzen/registry/serving/current` is absent.

This packet expires no later than `2026-08-17T04:00:00Z`. AWS deletion before
execution, a different recovery deadline, ARN, version map or any other drift
fails closed and requires a new packet. It never creates a different secret.

Request manifest:
`platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-003.json`.

## Exact staged execution

Receipts are create-exclusive, fsync-backed, contain no plaintext and are
persisted under
`platform/evidence/receipts/B6-2026-015-LIVE/` immediately after each stage.

### Stage 0 — bindings and zero-state preflight

Validate owner authorization, independent review, final packet and manifest
hashes, every source hash, account/operator, exact pending secret state,
versions, absent policies/state/token, zero compute and absent production
pointer. Persist `preflight.json` before mutation.

### Stage 1 — restore only the exact secret

Call `RestoreSecret` exactly once for the exact ARN. Re-read identity, tags,
KMS key and the exact two-version map after restoration. Persist `restore.json`
immediately.

No `GetSecretValue` call is permitted to recover an old value. No new secret
ARN may be created.

### Stage 2 — exact Terraform import

Import only the restored ARN at
`aws_secretsmanager_secret.b6_client_keys[0]`. The resource policy and KMS
policy addresses must remain absent. Persist `terraform_import.json` with state
lineage and serial.

### Stage 3 — normalize only if needed

Create a fresh saved plan targeting only the secret address. Exactly one of two
results is accepted:

1. `NO_NORMALIZATION_REQUIRED`: an exact no-op with full ARN, name, KMS, tags,
   recovery and force-overwrite readback; or
2. `APPLY_EXACT_NORMALIZATION`: exactly `0 add / 1 update / 0 destroy`, changing
   only imported representation fields
   `force_overwrite_replica_secret`, `recovery_window_in_days` and `tags`, with
   `tags_all` byte-identical and no live tag change.

Any unknown after value, extra address, field, add, delete or policy value
refuses. If normalization is required, apply only that guarded saved plan.
Generate a second secret-only plan and require exact `NO_CHANGES`. Persist
`terraform_normalization.json` with both plan hashes and the selected mode.

Normalization and policy creation must never be combined in one plan.

### Stage 4 — reconstruct the two access boundaries

Only after the normalization residual is no-op, generate a separate fresh plan
for the exact three addresses. Required result:

- secret: no-op;
- `aws_secretsmanager_secret_policy.b6_client_keys[0]`: create; and
- `aws_iam_role_policy.b6_client_keys_kms[0]`: create.

The exact delta is `2 add / 0 change / 0 destroy`. The resource policy allows
`GetSecretValue` only to `medzen-orch-role`, explicitly denies all other
principals and blocks public policy. The additive KMS policy permits decrypt
only via Secrets Manager with the exact secret-ARN encryption context.

Apply only the guarded saved plan, then require a fresh all-boundary residual
plan of `NO_CHANGES`. Persist `terraform_reconciliation.json` before rotation.

### Stage 5 — rotate, never reuse

Only after all prior receipts exist:

- generate 32 bytes from the operating-system CSPRNG;
- encode them as exactly 43 unpadded base64url characters;
- create only `/private/tmp/medzen-b6-6-client-token`, exclusively, mode
  `0600`, 44 bytes including one LF;
- store canonical JSON containing only the lowercase SHA-256 for synthetic
  client `b6-window-probe`; and
- persist `rotation.json` immediately after `PutSecretValue` succeeds.

The removed historical token and the value stored in either previous secret
version are never read or reused. Plaintext may exist only in the exact local
file and process memory; it never enters Git, Terraform, stdout or receipts.

### Stage 6 — version and denial verification

Require:

- the new version at `AWSCURRENT`;
- `d09d567e-9bde-482a-b95a-3cab990a1006` at `AWSPREVIOUS` immediately after
  rotation;
- `f78c8aa8-2765-4788-9928-dd1ba7c406bf` still unstaged;
- removal of `AWSPREVIOUS` from `d09d…`;
- final map: new version `AWSCURRENT`, both previous versions unstaged;
- operator `GetSecretValue` explicitly denied; and
- orchestrator role remains the only allowed reader.

Persist `verification.json`. The fresh version ID, bearer hash and secret-value
hash become the only permissible inputs to the later final-window packet.

## Executable entry point

After review and owner approval, execute only:

`AWS_PROFILE=medzen bash scripts/run_b6_client_secret_restoration_2026_015.sh <owner-authorization.json> <absolute-repo>/platform/evidence/receipts/B6-2026-015-LIVE`

The script refuses without the exact authorization and receipt directory. It
performs the stages above, not a subset or a reordered sequence.

## Fail-closed cleanup

The execution script arms cleanup immediately before restoration. Any later
failure:

1. removes only the exact local token first;
2. creates a fresh `enable_b6_client_keys=false` plan over the exact three
   addresses;
3. permits only a non-empty subset of exact deletes and no update, replacement
   or unrelated address;
4. applies only that guarded plan;
5. schedules seven-day recoverable deletion if the exact secret was restored
   before import or Terraform deletion did not already schedule it;
6. proves all three Terraform addresses and both access policies absent; and
7. persists `cleanup.json`.

Force deletion without recovery is prohibited. Cleanup failure returns a
distinct failure and blocks every later window. A failed stage never erases an
earlier receipt.

## Cost and resource impact

- Aggregate project ceiling: `$300`.
- Cost registry: `COST-REGISTRY-2026-004`.
- Existing B6 allocation: `B6-INTEGRATION-WINDOW-2026-001`, `$10` reservation.
- New reservation: `$0`.
- Maximum incremental secret cost inside the existing reservation: `$0.10`.
- CPU/GPU desired and instances: zero throughout.
- Compute, ALB, Fargate, endpoint and Kubernetes cost: `$0`.

The reservation is a ceiling, not a claim of actual spend. Any attributable
secret cost must be reconciled later without delaying the no-compute safety
proof.

## Required execution evidence

The post-run evidence must bind:

- final packet, owner authorization, review, manifest and source hashes;
- preflight and every stage receipt hash and timestamp;
- exact Terraform import address, state lineage/serial, both plan pairs and
  guard outcomes;
- whether normalization was applied or already unnecessary;
- exact resource-policy and KMS-policy hashes;
- new version ID, bearer SHA-256 and secret-value SHA-256, never plaintext;
- both prior version IDs with no stages;
- explicit operator-read denial;
- zero compute, absent production pointer and no integration-window activity;
- cleanup outcome if any stage refused; and
- exact incremental cost status.

That evidence authorizes no integration window. It is only a prerequisite for
a new final executable-window packet.

## Exact source bindings

<!-- SOURCE_BINDINGS_START -->
| Source | SHA-256 |
|---|---|
| `infra/b6_client_secret.tf` | `9594a8463dfee4c617939aff14c37e158b4b8b40771b19e83633c178db383c84` |
| `infra/variables.tf` | `59c1226f9a797e13756575ef77b45ce9324e1f1fb4743bc7d84fa8bec4f272dd` |
| `platform/decisions/B6-AWS-CHANGE-PACKET-2026-012A-import-state-normalization-continuation.md` | `20b5a851b04b4d685a19d770aa3b903f36dab40f95c8bfe599bf9ce97ed7fe10` |
| `platform/decisions/B6-AWS-CHANGE-PACKET-2026-014-b6-6-private-probe-successor.md` | `f31cb8f36d76d32884639bbe8bfb750ca807a92847d24f0abf4e1eef7d8c6428` |
| `platform/evidence/B6-CLIENT-SECRET-RESTORATION-CONTINUATION-AWS-EXECUTION-2026-001.json` | `1d949f019ce0b2e69f1fba525d535d61fc19ed07e99f08d11729c1c099784c89` |
| `platform/evidence/B6-PACKET-2026-013-REFUSED-FARGATE-ECR-NETWORK.json` | `daa679e744738094059a6faae53e5ebb7d44dd920c4da1ba5bac71100049590a` |
| `platform/finance/COST-REGISTRY-2026-004.json` | `56ef3255490b9d7c02244c5cc11c4040de6879635c49d039d16083dea5eaf5eb` |
| `platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-002.json` | `1dc091bf4ee3bcbb93b329839a341c66a787e26e79e4fb2b8de97a34364dc291` |
| `platform/manifests/B6-CLIENT-API-KEYS-RESTORE-2026-003.json` | `1208429af15cf2ed53ad28e82d99eae3154e34f1e7bddd4e3d90edb03d506b88` |
| `scripts/b6_client_secret_restoration_2026_015_bindings.py` | `86adfba4c1f222cbd51836241123161bdecbd95412bc35b7e2b0ba327b6bdc4c` |
| `scripts/check_b6_client_secret_restoration_2026_015_plan.py` | `44046a24e025a8b50449cfc8a801a029d98bb03089de4b47c5ec196f2c91a349` |
| `scripts/check_b6_client_secret_restoration_plan.py` | `9bb596216bcd9bd18440df9e698574021a278db8c7314a21ebced3d6d04d1f0e` |
| `scripts/run_b6_client_secret_restoration_2026_015.py` | `e23d58b004aae2743a52eb84122606e1b7e0d1ac618d070f79bf59438181e22f` |
| `scripts/run_b6_client_secret_restoration_2026_015.sh` | `5a8465c69c934e9bb89897d981564ee7e16a02dc4a67462db20e63fb18b21728` |
| `scripts/terraform_medzen.sh` | `1a1d9c158001d9b15ac1403bde2138ec284c250b28715818b395697317c274f1` |
| `tests/test_b6_client_secret_restoration_2026_015.py` | `ba331e5f25cdf11bda397c7a787896ddfc6432d0974f1b2703fb61703607c276` |
<!-- SOURCE_BINDINGS_END -->

Any missing source, changed hash, stale starting state, malformed authorization,
unknown plan value, absent receipt or credential drift refuses.

## Explicit prohibitions

No different secret or ARN, old plaintext read/reuse, `GetSecretValue` recovery,
combined unknown-policy plan, force deletion, worker scale-up, Kubernetes,
controller, DRA, ALB, Fargate, VPC endpoint, image change, production SSM,
approved artifact, model registration, MLflow transition, language approval,
real provider call, training, green-bucket mutation, PHI or final-window
execution is authorized.

## Approval phrase

After independent review bound to the final packet SHA-256 and prepared commit,
the only valid owner phrase is:

`Approve B6 AWS change packet 2026-015 only.`
