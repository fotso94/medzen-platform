# B6 AWS change packet 2026-006 — synthetic client API key boundary

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: `2026-08-09`

## Purpose

Create the exact `medzen/client-api-keys` secret required by the orchestrator
using synthetic B6.6-only credentials. Encrypt it with the existing MedZen data
KMS key, limit secret-value reads to the orchestrator role, and publish only a
SHA-256 hash of one randomly generated bearer token. No real client key is
permitted.

## Immutable scope

| Binding | Value |
|---|---|
| Preparation authorization commit/tree | `1d4a3bbb1e79145cdcc10ee9ba0877c2cf3fe95d` / `41ba4711c9774418f32d9ad8615ddfdaf84fce1c` |
| Secret name | `medzen/client-api-keys` |
| KMS key | `arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57` |
| Only reader | `arn:aws:iam::558069890522:role/medzen-orch-role` |
| Request manifest SHA-256 | `8b2057b14faff1e34549acba76ebefd6b117abf1a956741f9a5b193dea801d68` |
| Additive Terraform boundary SHA-256 | `961694aa9177649fc3d76f4cda8f5301b32d226afd4b49271ec7c6b3744822c9` |
| Historical services source (unchanged) | `fd560a7f5522c04ec57015d9bbd73286a40937af0eca04c6383dbebaa1bdb0a2` |
| Plan guard SHA-256 | `1ce788931c5db26eb580b815ffbb2c175f505a9b7c9a20237ffae192e46fc0f8` |
| Publication runner SHA-256 | `54008f42ac01a127b4a0369f590e6df748a7963e3d7450c600a6facf8a335dc3` |

The historical `medzen/speech/client-api-keys` secret is different and remains
unchanged. No KMS key or IAM role is created.

## Terraform delta

Generate a fresh targeted plan for exactly:

- create `aws_secretsmanager_secret.b6_client_keys`;
- create `aws_secretsmanager_secret_policy.b6_client_keys`; and
- create additive policy `aws_iam_role_policy.b6_client_keys_kms` to add
  `kms:Decrypt` through `secretsmanager.eu-central-1.amazonaws.com` and only
  when the encryption context matches `medzen/client-api-keys*`.

Required result: `3 add / 0 change / 0 destroy`. Historical `services.yaml`,
generated service IAM and `secrets.tf` remain byte-identical. The preview plan passed the
guard and has SHA-256
`fba16dc4a2f3bcd4fad1067e249e2e3271797b86b49279467b276d06cc1fefce`.
Regenerate immediately before execution if the state serial changed and
require:

`python scripts/check_b6_client_secret_plan.py <fresh-plan>`

No secret value may appear in Terraform configuration, plan or state. Apply
the guarded plan only after independent IAM review confirms the generated KMS
conditions and resource policy.

## Synthetic value publication

After Terraform creates the empty secret and validates its policy:

1. Require a new owner-authorization JSON binding this packet and request
   manifest hashes. The runner refuses without it.
2. Generate 32 random bytes with the operating system CSPRNG and encode them
   as unpadded base64url.
3. Write the bearer token only to
   `/private/tmp/medzen-b6-6-client-token`, mode `0600`, using create-exclusive
   semantics. Never print, commit or include it in evidence.
4. Store canonical JSON containing one enabled client,
   `b6-window-probe`, and only the lowercase SHA-256 of the bearer token.
5. Verify Secrets Manager accepts the resource policy with public-policy
   blocking enabled.
6. Prove the owner/operator receives explicit `AccessDenied` on
   `GetSecretValue`. The orchestrator's live Pod Identity read is deferred to
   the B6.6 integration proof and must occur before accepting traffic.
7. Persist a receipt containing only hashes, version ID, policy outcome and
   non-events.

Execution command after approval:

`python scripts/run_b6_client_secret_publication.py --authorization <owner-auth.json> --receipt <evidence.json> --apply`

## Rotation and deletion

If the local token is lost or exposed, do not reuse the secret value. A new
reviewed rotation must generate a new 32-byte token, publish only its SHA-256
as `AWSCURRENT`, prove the old token is refused, then remove `AWSPREVIOUS`.

Normal B6.6 cleanup removes the plaintext file immediately and schedules
secret deletion with the configured seven-day recovery window. Force deletion
without recovery is prohibited. If this packet fails after token generation,
the runner removes the local token; if no later packet depends on the empty
secret, schedule the same seven-day deletion.

## Cost and non-events

Maximum incremental packet cost: `$0.10`. CPU/GPU remain zero. No deployment,
ALB, real client credential, production SSM, approved model, Bedrock/Fish call
or PHI is permitted.

## Approval phrase

Execution requires independent review and an owner authorization record
binding this packet and request manifest SHA-256. Suggested phrase:

`Approve B6 AWS change packet 2026-006 only.`
