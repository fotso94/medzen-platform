# B6 AWS change packet 2026-007 — deployment registry snapshot

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: `2026-08-09`

## Purpose

Publish the exact B6.6 synthetic deployment registry through the proven B6.5A
content-addressed mechanics: three create-only, KMS-encrypted SecureStrings at
a new non-serving test root. Do not create or change a production serving
pointer.

## Immutable bindings

| Binding | Value |
|---|---|
| Preparation authorization commit/tree | `1d4a3bbb1e79145cdcc10ee9ba0877c2cf3fe95d` / `41ba4711c9774418f32d9ad8615ddfdaf84fce1c` |
| Snapshot material SHA-256 | `d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81` |
| Exact root | `/medzen/registry/test/b6/d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81` |
| Generated fixture SHA-256 | `33433626b0f2070a714df31e16306d6a511652b0870b0ba3cb5ec701847c9821` |
| Request manifest SHA-256 | `8cef584e4e582077dfc97ef7272a28606e6cef513bf5bdbf742baa4dee7dc6b5` |
| Additive publisher-policy source SHA-256 | `6ae1854713d94df5c0581ba58357950d2239c44a93907c42d5e039eb2ffa8298` |
| Historical B6.5A policy source (unchanged) | `3bca452b19d7983428ef7fe834e0f113c29d64ee94e818196e7765db2d65581a` |
| Packet validator SHA-256 | `c37d79e450f672fcbca4a97f2feaf817ae1623f87e358d98fcbf2f3e975ad220` |
| Plan guard SHA-256 | `98222ed3e4deef72118651b1418cb1e428318d76289ea0bda94841b97843e7db` |
| Publication runner SHA-256 | `b3c92c8705bccecaede2888d120326c8872c8129630eec3d903738dd9beb1ffc` |
| Publisher role | `arn:aws:iam::558069890522:role/medzen-registry-publisher-role` |
| KMS key | `arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57` |

Read-only preflight on `2026-08-09` found zero parameters at the exact new root
and confirmed `/medzen/registry/serving/current` is absent.

## Exact parameters

The request manifest binds every byte and hash for exactly:

1. `<root>/index`, publish order `1`;
2. `<root>/routes/english`, publish order `2`; and
3. `<root>/_manifest`, publish order `3` and completion marker.

All are Standard-tier `SecureString`, expected initial version `1`,
`Overwrite=false`. The route identifies the retained zero-shot ASR v0,
synthetic RAG content, fake LLM provider and text-only TTS. It is not a model
approval and contains no `artifact` or `approved_version` serving field.

## Terraform and IAM delta

Create only the additive
`aws_iam_role_policy.registry_publisher_b6_5c_tags`. It grants only
`ssm:AddTagsToResource` beneath `/medzen/registry/*` with the six exact B6.5C
allocation values. The historical B6.5A inline policy remains byte-identical,
including its outside-prefix write deny and global parameter-deletion deny.
The supplemental allocation is:

- `Stage=B6.5C`;
- `Workstream=ssm-deployment-registry`; and
- `BudgetRegistry=COST-REGISTRY-2026-003`.

Required plan: `1 add / 0 change / 0 destroy`. The preview plan passed the
guard and has SHA-256
`255cd8f57f3346f40f1d3dd2903eaf797681cce496ce180b824cde7817a7bcdd`.
Regenerate if the state serial changed and require:

`python scripts/check_b6_deployment_registry_plan.py <fresh-plan>`

Then apply only that guarded plan and require the local request validator:

`python scripts/check_b6_deployment_registry_packet.py`

to return `PASS_B6_DEPLOYMENT_REGISTRY_PACKET`.

## Publication and verification

After a separate owner-authorization JSON binds this packet and request
manifest, execute:

`python scripts/run_b6_deployment_registry_publication.py --authorization <owner-auth.json> --receipt <evidence.json> --apply`

The runner assumes the existing publisher role for at most one hour, refuses a
partial or different root, creates data before the completion manifest, reads
back decrypted values, types, KMS key, versions and exact tags, reconstructs
the snapshot through the orchestrator registry router, and proves the
production pointer is byte-identical before/after (expected absent). If a new
publication fails after a partial write, the owner identity deletes only the
names created in that attempt.

## Rollback, cost and prohibitions

Rollback is deletion of exactly the three bound parameter names by the owner
operator identity; the publisher role remains unable to delete. Reconfirm the
production pointer before and after. Maximum incremental packet cost: `$0.10`.

No other parameter, production alias, IAM role, KMS key, node, workload, ALB,
model, approved artifact, Bedrock/Fish call or PHI is permitted. CPU/GPU remain
zero.

## Approval phrase

Execution requires independent review and an owner authorization record
binding this packet and request manifest SHA-256. Suggested phrase:

`Approve B6 AWS change packet 2026-007 only.`
