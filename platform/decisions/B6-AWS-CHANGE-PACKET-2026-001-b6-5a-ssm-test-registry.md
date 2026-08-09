# B6 AWS change packet 2026-001 — B6.5A versioned SSM test registry

Status: **AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL — NOT AUTHORIZED**

This packet is a proposal. It authorizes no AWS call, IAM apply or Parameter
Store write by itself. Execution requires an independent review bound to this
packet's SHA-256 and a separate versioned owner-approval record bound to the
same packet and request-manifest hashes.

## Outcome and scope

Publish the already reviewed B6.3 local registry fixture, without changing its
values, as three KMS-encrypted `SecureString` parameters beneath this exact
content-addressed, non-serving root:

`/medzen/registry/test/b6/a2486c03eb20b6fd3d30b5ea38eb4d29895c2e1ab26073d21282a9bbedacb8e6`

The snapshot is deliberately local/synthetic. It identifies
`v0-local-synthetic-asr`, `fake-bedrock-local-v1`, the synthetic RAG snapshot
and text-only TTS. Publication proves the SSM adapter and readiness boundary;
it does not prove a real provider, production routing, clinical quality or
model approval. B6.6 may bind this snapshot only with that interpretation. A
different integration identity requires a new content-addressed source and a
new packet revision.

## Immutable repository and source bindings

| Binding | Value |
|---|---|
| Starting Git commit | `e91110ff161d01a5ba341442c9f4fc0c3b79c2c9` |
| Starting Git tree | `a46e90a31ba616d6ce8899d54bd703e0d383b580` |
| Full-B6 plan SHA-256 | `3cfba1521281384aabbc91c4c5f04f7e2bec51444cfdf15f8a28b56a8f20418b` |
| Source JSON SHA-256 | `a853365107b6e16e270ee75f9e830b99a1f848861321ee3cf69cfe59ddaf0f86` |
| Generated fixture SHA-256 | `31f3db63c2955b445d02f4a00cace3cdf9e8e49da797478716a3a2945db59b8a` |
| Fixture generator SHA-256 | `943c5ef0f67426fbb3ad28124804068c0899c47237f0557383c8a106e4e46b92` |
| Snapshot/source-material SHA-256 | `a2486c03eb20b6fd3d30b5ea38eb4d29895c2e1ab26073d21282a9bbedacb8e6` |
| Exact request manifest | `platform/manifests/B6-5A-SSM-TEST-REGISTRY-2026-001.json` |
| Request-manifest SHA-256 | `75ec85328d1424acc80a0db55d5f407571c3ffcdc0e4f9e8a4ebe8962075edb3` |
| Local validator | `scripts/check_b6_5a_ssm_packet.py` |
| Validator SHA-256 | `b284e0bf974a33d0aaa24f4a1b2df2876b3696ac54ab58d976aa7535bd82fbcc` |

The request manifest carries every exact parameter value, not merely a
summary. Its file hash binds the complete ordered write set.

## Exact AWS boundary

| Field | Bound value |
|---|---|
| Account | `558069890522` |
| Region | `eu-central-1` |
| Required operator | `arn:aws:iam::558069890522:user/s.fotso` |
| Existing publisher role | `arn:aws:iam::558069890522:role/medzen-registry-publisher-role` |
| Existing KMS key | `arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57` |
| Parameter type/tier/data type | `SecureString` / `Standard` / `text` |
| Maximum and exact parameter count | `3` / `3` |
| Overwrite | `false` |
| Production alias changes | `0` |

### Exact parameter names and value hashes

Values are the exact UTF-8 strings in the hashed request manifest.

| Publish order | Name | Value SHA-256 |
|---:|---|---|
| 1 | `.../index` | `4ce97b58eee7813181108062d50caa20a63504dbf1f6006134a523c8ed0e5f51` |
| 2 | `.../routes/english` | `8a04906cd140ff2f2dcb003d6953c348f81055b67924594edd02b2a40ed914b2` |
| 3 | `.../_manifest` | `e09464c797b15392552cfcbd3035d7847f191df1e761c5ed69148a21d7377e36` |

Here `...` means only the exact root printed above. The completion manifest is
written last so an interrupted partial write cannot appear complete.

## Allocation and cost controls

- Allocation ID: `B6-SSM-TEST-REGISTRY`.
- Maximum incremental packet cost: `$0.10`.
- Compute scale-up: none; CPU and GPU desired capacity remain zero.
- Standard parameters create no parameter storage charge. KMS/API requests are
  bounded by the packet ceiling and the aggregate `$300` guardrail.

Every parameter receives exactly these tags at creation:

| Tag | Value |
|---|---|
| `Project` | `medzen-speech` |
| `Environment` | `dev` |
| `CostCenter` | `speech-platform` |
| `Stage` | `B6.5A` |
| `Workstream` | `ssm-test-registry` |
| `BudgetRegistry` | `COST-REGISTRY-2026-001` |

## Necessary publisher-policy correction

The existing publisher role is retained; no role or KMS key is created. Its
current policy permits `PutParameter` but not the dependent
`ssm:AddTagsToResource` action required when parameters are created with tags.
The packet therefore includes exactly one in-place IAM policy update:

1. allow `ssm:AddTagsToResource` only for `/medzen/registry/*` and only for the
   six allocation tag keys;
2. allow `ssm:ListTagsForResource` only for `/medzen/registry/*` so readback can
   verify the exact tags; and
3. preserve the explicit deny on `ssm:DeleteParameter` and
   `ssm:DeleteParameters` everywhere.

The proposed Terraform source is `infra/ssm.tf`, SHA-256
`3bca452b19d7983428ef7fe834e0f113c29d64ee94e818196e7765db2d65581a`.
This corrects the publication boundary for tagged writes without granting a
new writer or weakening immutable-history controls. AWS documents
`ssm:AddTagsToResource` as a dependent authorization for tagged
`PutParameter` requests.

Before execution, a saved live Terraform plan must be independently checked to
show exactly `0 add, 1 change, 0 destroy`: only
`aws_iam_role_policy.registry_publisher` may change. Any other action refuses
the packet.

## Required preflight after approval

Persist each result before continuing:

1. Verify the caller is the exact operator/account/region above.
2. Verify the independent review and owner-approval records match the packet
   and request-manifest SHA-256 values.
3. Run the local validator and fixture generator in check mode.
4. Produce the saved Terraform plan and enforce the exact one-policy-update
   boundary above.
5. Simulate/inspect that the publisher role can Put/Get/tag/list-tags only
   inside `/medzen/registry/*`, cannot write outside it and cannot delete.
6. Prove the operator identity can delete exactly the three test parameters.
   This is mandatory because the publisher role intentionally cannot perform
   rollback deletion. If the exact rollback permission is absent, refuse
   before any write; do not broaden IAM inside the run.
7. Read the three exact names with decryption. Allowed initial states are:
   all absent, or all present with byte-identical values and exact tags. A
   partial set, different value, unexpected version/type/key or tag mismatch
   refuses.
8. Prove `/medzen/registry/serving/current` is unchanged and record its
   existence/value hash without placing decrypted values in logs.

## Authorized execution sequence after approval

1. Apply the one reviewed IAM policy update and prove a zero-residual plan.
2. Assume the existing publisher role for at most one hour. Never persist or
   log session credentials.
3. If the complete snapshot is absent, create `index`, then
   `routes/english`, then `_manifest`, with overwrite disabled and the exact
   KMS key, values and tags from the request manifest.
4. After every create, read back with decryption and persist the returned
   parameter version, type, key identity, value SHA-256 and exact tag set.
5. If all three already exist identically, perform no write and record
   `REUSE_IDENTICAL_COMPLETE`; an immutable collision or partial snapshot
   refuses.
6. Read the complete root through the publisher role and validate the returned
   values with the unchanged `RegistryRouter`. Simulate the exact orchestrator
   role's SSM reads and KMS decrypt through SSM and require `allowed`, while
   its writes remain denied. The orchestrator role trusts only EKS Pod
   Identity, so this zero-compute packet does not add an owner trust path or
   claim an actual pod-role session; B6.6 performs the runtime Pod Identity
   read when the separately authorized integration window starts.
7. Write a canonical execution receipt, hash it, and commit both the receipt
   and its content address. Record the production-alias before/after hashes and
   prove zero changes.

## Stop and rollback

Any failed IAM plan, identity check, permission simulation, write, readback,
tag check, router validation, receipt persistence or production-alias check
stops the run.

Rollback is deletion of these exact three names only:

1. `.../_manifest`
2. `.../routes/english`
3. `.../index`

Use the verified owner/operator identity, never the publisher role. Delete the
manifest first, then the route and index, so the snapshot becomes incomplete
before any data removal. Read the exact root afterward and require zero
parameters. No recursive or broader prefix deletion is allowed. If the IAM
policy update itself must be rolled back, restore the pre-packet Terraform
source and apply a separately reviewed plan showing only the publisher policy
returning to its prior document.

Rollback never deletes a production snapshot, changes a serving pointer,
modifies a model, or changes approved registry fields.

## Explicit prohibitions and unchanged state

- No write to `/medzen/registry/serving/current` or any production snapshot.
- No production SSM alias, routing or parameter update.
- No `artifact` or `approved_version` field.
- No model registration, stage transition, approved ASR publication or B5
  report change.
- No EKS, node-group, pod, ECR, network, Bedrock, Fish, training or inference
  action.
- No new IAM role, KMS key or standing compute cost.
- B5 remains `BLOCKED`; deferred-language state remains unchanged.

## Expected successful result

- IAM policy updates: `1`; IAM roles created: `0`.
- Test `SecureString` parameters: `3`, all version `1` on a fresh publish.
- Test snapshot outcome: `PUBLISHED_VERIFIED_NON_SERVING` or
  `REUSE_IDENTICAL_COMPLETE`.
- Publisher readback and fail-closed router initialization: `PASS`.
- Orchestrator-role read/KMS policy simulation: `PASS`; actual Pod Identity
  read remains a B6.6 runtime check.
- Production alias changes: `0`.
- CPU/GPU desired capacity changes: `0`.
- Immutable publication/readback receipt: committed and content-addressed.
- B6.5A closes only after independent review of that receipt.

## Approval boundary

Approval must name exactly:

`B6 AWS change packet 2026-001 (B6.5A SSM test registry)`

It must bind this packet's final SHA-256 and request-manifest SHA-256
`75ec85328d1424acc80a0db55d5f407571c3ffcdc0e4f9e8a4ebe8962075edb3`.
Approval of this packet does not approve B6.6 or any other AWS action.
