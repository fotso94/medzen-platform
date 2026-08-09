# B6 AWS change packet 2026-007A — deployment registry classification retry

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: `2026-08-09`

## Purpose

Retry only the three-parameter, non-serving B6.5C deployment-registry
publication after packet 2026-007 failed closed at its final runtime gate and
automatically deleted all three parameters. The snapshot was not rejected for
content or hash drift: the packet-bound v1 runner selected the router's local
fixture classification by default instead of explicitly selecting the deployed
B6.6 classification.

This retry preserves packet 2026-007, its runner and its failure evidence
unchanged. It adds a versioned wrapper that delegates every publication,
read-back and rollback control to the reviewed v1 runner while replacing only
the final router construction with the explicit deployed classification.

## Immutable bindings

| Binding | Value |
|---|---|
| Failed packet 2026-007 SHA-256 | `2c55aba2728bd7669800796963b297265ce125869bc2464a8f72c1050ef7481f` |
| Failure evidence SHA-256 | `6055040b24558da9fc2fe42f9b18dcf83054f9ffdb2b65fcd7c98d2b28a7b82d` |
| Preserved v1 runner SHA-256 | `b3c92c8705bccecaede2888d120326c8872c8129630eec3d903738dd9beb1ffc` |
| Retry v2 runner SHA-256 | `391ec0665c68143668cd5b7d042dbb96a62834bca96e9c41d2ab19b7c888163a` |
| Retry regression tests SHA-256 | `2830466d8fc5079293e93a3d544a8d4ac10f926dfa76d1b6d6fdf76cf1242533` |
| Request manifest SHA-256 | `8cef584e4e582077dfc97ef7272a28606e6cef513bf5bdbf742baa4dee7dc6b5` |
| Generated fixture SHA-256 | `33433626b0f2070a714df31e16306d6a511652b0870b0ba3cb5ec701847c9821` |
| Snapshot material SHA-256 | `d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81` |
| Exact root | `/medzen/registry/test/b6/d4f9696d288e0ea6c1d139f496e00eaf097b77ea8b3a4f5a26a6470286adfe81` |
| Deployed classification | `B6_6_SYNTHETIC_INTEGRATION_ONLY` |

No source registry value, parameter name, KMS key, tag, IAM policy, snapshot
hash or serving field changes in this retry.

## Local proof required before execution

Run the retry, registry-router and boundary suites in the pinned environment.
They must prove:

1. the old default call reproduces `registry manifest identity is invalid`;
2. the v2 wrapper accepts the exact generated fixture only when
   `expected_classification=B6_6_SYNTHETIC_INTEGRATION_ONLY`;
3. the resolved English route and snapshot identity are unchanged;
4. the packet-2026-007 failure receipt still binds the exact old runner hash;
5. relative authorization or receipt paths refuse before AWS access; and
6. all existing boundary tests continue to pass.

Prepared result: `25 passed / 0 failed / 0 skipped / 0 deselected` across the
three directly relevant suites. Independent review must reproduce this result.

## AWS preflight and unchanged infrastructure

Before execution, require all of the following:

- caller `arn:aws:iam::558069890522:user/s.fotso` in `eu-central-1`;
- the exact root contains zero parameters;
- `/medzen/registry/serving/current` is absent;
- `medzen-registry-publisher-b6-5c-tags` is present and byte-identical to the
  packet-2026-007 Terraform policy;
- a targeted Terraform plan for
  `aws_iam_role_policy.registry_publisher_b6_5c_tags` returns `NO_CHANGES`;
- CPU desired capacity is `0`; GPU desired capacity is `0`; and
- the request validator returns
  `PASS_B6_DEPLOYMENT_REGISTRY_PACKET parameters=3` with the exact snapshot.

This retry has **zero Terraform, IAM, KMS, ECR, EKS, security-group or secret
changes**.

## Exact publication

Create a new owner-authorization JSON binding this packet, the unchanged
request manifest and the v2 runner. Then execute with absolute paths only:

`python scripts/run_b6_deployment_registry_publication_v2.py --authorization /absolute/owner-auth.json --receipt /absolute/evidence.json --apply`

The inherited v1 controls must:

1. assume only `medzen-registry-publisher-role` for at most one hour;
2. prove the production pointer absent before writing;
3. refuse a partial or different root;
4. create `index`, then `routes/english`, then `_manifest`, all as create-only
   version-1 KMS `SecureString` parameters;
5. read back exact values, hashes, types, versions, KMS identity and tags;
6. reconstruct the snapshot through the deployed-classification router;
7. prove the production pointer unchanged and absent; and
8. write the immutable receipt immediately after those checks.

Any failure after a new parameter is created must delete exactly the names
created in that attempt with the owner identity. No reuse of the packet
2026-007 authorization is permitted.

## Cost, rollback and prohibitions

Maximum incremental cost: `$0.10`. Rollback remains deletion of exactly the
three bound parameter names, followed by proof that the root is empty and the
production pointer is absent.

No IAM change, Terraform apply, parameter outside the exact root, production
alias, node scale-up, deployment, ALB, model or approved-artifact change,
Bedrock/Fish call, PHI, overwrite or security waiver is permitted.

## Approval phrase

Execution requires independent review and a new owner authorization binding
this exact packet SHA-256:

`Approve B6 AWS change packet 2026-007A only.`

Only a `VERIFIED_COMPLETE` 007A receipt permits preparation of the executable
B6.6 successor packet.
