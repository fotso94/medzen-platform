# B6A AWS change packet 2026-002 — B1 serving-registry publication path

Status: **OWNER_APPROVAL_REQUIRED — NOT AUTHORIZED**

This packet is deliberately separate from the completed EKS packet
`B6A-AWS-CHANGE-PACKET-2026-001-eks-upgrade.md`. No action in this packet has
been applied to AWS.

## Purpose

Complete B1's missing publication boundary for `/medzen/registry/*` without
publishing a model, activating a serving snapshot, deploying a service or
starting GPU capacity.

## Preconditions

1. Restore read access to the Terraform backend
   `s3://medzen-speech-tfstate-558069890522/platform/terraform.tfstate` and its
   DynamoDB lock table. The current `medzen` operator receives AccessDenied.
2. Pull and compare the remote state with live AWS. Do not create or import
   resources from guesses.
3. Produce and review a saved Terraform plan. It must show no deletion, no
   network primitive change, no EKS/node-group scaling and no SSM parameter.
4. Verify the owner-assumer principal remains exactly
   `arn:aws:iam::558069890522:user/s.fotso` or replace it prospectively in a
   new packet.

## Itemized AWS changes proposed

1. Create IAM role `medzen-registry-publisher-role` with a one-hour maximum
   session and trust limited to the exact approved same-account principal.
2. Attach one inline policy that:
   - permits Get/Put only for
     `arn:aws:ssm:eu-central-1:558069890522:parameter/medzen/registry/*`;
   - explicitly denies Put outside that prefix;
   - explicitly denies `DeleteParameter` and `DeleteParameters` everywhere;
   - permits KMS Encrypt/Decrypt/GenerateDataKey only through
     `ssm.eu-central-1.amazonaws.com` and only with the exact registry parameter
     encryption context;
   - permits DescribeKey for the existing data key.
3. Update inline policies on `medzen-orch-role`, `medzen-llm-role` and
   `medzen-tts-role` to add KMS Decrypt for registry SecureStrings, constrained
   to the SSM service and exact registry path. Their SSM permissions remain
   read-only.
4. Reuse existing KMS key
   `arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57`
   (`alias/medzen-data`). Create no KMS key.

## Explicitly not included

- No SSM parameter creation or update.
- No `/medzen/registry/serving/current` change.
- No approved artifact publication, model registration or approved-version
  change.
- No B5 report regeneration or reinterpretation.
- No EKS, node-group, VPC, subnet, route, security-group or endpoint change.
- No GPU scale-up, container deployment, training or inference.
- No GitHub OIDC or CI role creation; that belongs to B7.

## Validation after an approved apply

1. Re-run Terraform plan and prove zero residual changes.
2. Simulate/inspect all publisher and runtime policies:
   - publisher Put inside exact prefix allowed;
   - publisher Put outside prefix denied;
   - all deletes denied;
   - runtime reads allowed and runtime writes denied;
   - trainer writes denied.
3. Exercise KMS/SSM only in a separately authorized non-serving test namespace
   or with local policy tests. This packet does not authorize a production
   parameter write.
4. Confirm `/medzen/registry` still contains zero parameters.

## Rollback

Because this packet creates no parameters, rollback is IAM-only: remove the
publisher inline policy and role, and restore the three runtime inline policies
to their exact pre-change documents. Do not schedule KMS deletion because no
key is created.

## Cost

IAM roles and standard Parameter Store parameters have no standing hourly
compute cost. This packet creates no parameter and no new KMS key. Any later
KMS API request or advanced-parameter use must be covered by the aggregate
`$300` budget and its own publication authorization.
