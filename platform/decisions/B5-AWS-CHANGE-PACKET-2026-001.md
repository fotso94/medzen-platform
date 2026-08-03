# B5 AWS change packet 2026-001

Status: `AWAITING_EXPLICIT_OWNER_APPROVAL`

Prepared: 2026-08-03

Account: `558069890522`

Region: `eu-central-1`

Required credential profile: `medzen`

Verified caller: `arn:aws:iam::558069890522:user/s.fotso`

The default local AWS profile currently resolves to a different account. Every
approved command in this packet must therefore use `--profile medzen` and must
first prove the caller account is exactly `558069890522`. A caller mismatch
refuses the operation.

## Bound local evidence

- Gate report:
  `platform/evidence/b5/gate-reports/25217157215ea979440187aa050772ffdf248d75e1ae823d5dcb72cb9d8def30.json`
- Gate report SHA-256:
  `25217157215ea979440187aa050772ffdf248d75e1ae823d5dcb72cb9d8def30`
- Gate report outcome: `BLOCKED`
- Gate-engine commit: `4eeaab758d272696f39534c909d9f77a6ac52baf`
- MLflow target run: `94639149aa074000a1f0215ccebade8b`
- MLflow resolution evidence:
  `platform/evidence/B5-MLFLOW-RUN-RESOLUTION-2026-001.json`
- Non-promotable dry-run manifest:
  `platform/evidence/b5/dry-run-manifests/a91eebbfffa80891a11c6ccdc278b157d7c645abdde491f5d50b23fc03bddace.json`
- Dry-run manifest SHA-256:
  `a91eebbfffa80891a11c6ccdc278b157d7c645abdde491f5d50b23fc03bddace`
- Dry-run manifest outcome: `BLOCKED`
- Dry-run signature purpose: `B5_DRY_RUN_NON_PROMOTABLE`

## Invariants for either approval

The following operations remain prohibited:

- Any write to `s3://medzen-speech/approved/asr/`.
- Model registration or model-version creation.
- Any MLflow stage transition.
- Any language `artifact` or `approved_version` change.
- Any production SSM registry or serving-alias change.
- Deployment or B6 transition.
- Applying the production permission template or approved-prefix bucket-policy
  template.

All S3 writes must use `If-None-Match: *`, SSE-KMS with the existing MedZen
encryption key, content-addressed keys, and create-only behavior. An existing
different object refuses. No historical B4 object may be overwritten.

## Approval A — attach the BLOCKED report to MLflow

Purpose: finish B5.1 by attaching the immutable report to the already resolved
source run without registering a model.

This approval does not create IAM or KMS resources.

### Exact read

- Read only this source snapshot version:
  `s3://medzen-speech/mlflow/snapshots/b4-scoped-count-tolerance-61145b7/attempt-5/conversion-diagnostic/mlflow.db`
- Required VersionId:
  `MJxldbvfXM3MlXOQWWEU5.b1MU1VUGgL`
- Required SHA-256:
  `ee107190414512919a230059cc4be4d0d3c7275e3838ae17214ed65d43d98545`

### Exact writes

1. Create the content-addressed report object:
   `s3://medzen-speech/mlflow/artifacts/94639149aa074000a1f0215ccebade8b/b5/gate-reports/25217157215ea979440187aa050772ffdf248d75e1ae823d5dcb72cb9d8def30.json`.
2. In a local copy of the verified SQLite snapshot, add only these run tags to
   run `94639149aa074000a1f0215ccebade8b`:
   `b5.gate_report_uri`, `b5.gate_report_sha256`,
   `b5.gate_outcome=BLOCKED`, and
   `b5.model_registration_permitted=false`.
3. Create a new snapshot object at:
   `s3://medzen-speech/mlflow/snapshots/b4-scoped-count-tolerance-61145b7/attempt-5/b5-blocked-gate-report/25217157215ea979440187aa050772ffdf248d75e1ae823d5dcb72cb9d8def30/mlflow.db`.

### Required postconditions

- The original snapshot version and bytes remain unchanged.
- Registered models before/after: `0 / 0`.
- Model versions before/after: `0 / 0`.
- The target run remains `FINISHED` and has no stage transition.
- Approved-ASR writes: `0`.
- SSM writes: `0`.
- A local immutable attachment receipt records both new S3 VersionIds and the
  new snapshot SHA-256.

### Failure and recovery

If the report object is created but snapshot creation fails, it remains an
unreferenced immutable BLOCKED artifact. Retry may only accept byte-identical
content at the same key. Consumers continue using the original snapshot. No
automatic deletion or overwrite is authorized.

## Approval B — create and exercise the real KMS dry-run boundary

Purpose: test real asymmetric KMS signing and verification only for the
explicitly BLOCKED dry-run manifest. This approval is independent of Approval
A and is not needed to attach the MLflow report.

### IAM changes

1. Create role `medzen-b5-promotion-role` using
   `platform/iam/medzen-b5-promotion-trust.template.json` with the trust
   placeholder resolved only to
   `arn:aws:iam::558069890522:user/s.fotso`.
2. Attach only
   `platform/iam/medzen-b5-promotion-dry-run-role.template.json`, with its KMS
   key placeholder resolved to the key created below.
3. Do not attach
   `platform/iam/medzen-b5-promotion-role.template.json`.
4. The role has no approved-ASR permission. Trainer, builder, ASR runtime and
   loader roles are explicitly denied assumption.

### KMS changes

1. Create one customer-managed asymmetric KMS key:
   `KeySpec=RSA_3072`, `KeyUsage=SIGN_VERIFY`, single-Region,
   description `MedZen B5 promotion manifest signing v1`.
2. Apply `platform/promotion/kms-key-policy.template.json`.
3. Create alias `alias/medzen-b5-promotion-signing-v1`.
4. Record the resulting immutable key ARN, key ID, DER SubjectPublicKeyInfo,
   public-key SHA-256, and supported signing algorithms.
5. Permit `kms:Sign` only to `medzen-b5-promotion-role`, with
   `kms:SigningAlgorithm=RSASSA_PSS_SHA_256` and
   `kms:MessageType=DIGEST`.

The existing symmetric SSE-KMS key remains encryption-only and is not used to
sign.

### Dry-run exercise

1. Assume `medzen-b5-promotion-role`.
2. Sign the SHA-256 digest of the exact canonical bytes of dry-run manifest
   `a91eebb...bddace` using `RSASSA_PSS_SHA_256` and `MessageType=DIGEST`.
3. Verify with `kms:Verify` and separately archive the public key for offline
   verification.
4. Prove tampered-manifest and wrong-key verification both refuse.
5. Prove the dry-run signature envelope is rejected by the production
   promotion verifier because its purpose is
   `B5_DRY_RUN_NON_PROMOTABLE`, not `B5_PROMOTION`.
6. If S3 publication of the dry-run evidence is exercised, write only beneath:
   `s3://medzen-speech/candidates/b5-dry-run/a762edd7a726ba7da3e4e6d417b80e427d96a3b327a48aece6282ffa6525e459/`.
   The manifest and signature envelope must use content-addressed filenames and
   the dry-run role's `BLOCKED` tags.

### Required postconditions

- Approved-ASR writes: `0`.
- Production SSM changes: `0`.
- Registered models and model versions: `0`.
- No stage transition, deployment or B6 transition.
- KMS Sign/Verify success, tamper refusal and wrong-key refusal are captured in
  a versioned local receipt.

### Rotation, revocation and recovery

Asymmetric KMS keys do not support automatic or on-demand rotation. Rotation
means creating a new key and moving the alias for new signatures while keeping
the old key ARN/public key for historical verification. On suspected
compromise, disable the key and deny new Sign calls. Scheduling deletion is a
separate destructive operation and is not authorized by this packet.

## Approval choices

The owner may approve either part independently with one of these explicit
statements:

- `Approve B5 AWS packet 2026-001A only.`
- `Approve B5 AWS packet 2026-001B only.`
- `Approve B5 AWS packet 2026-001A and 2026-001B.`

Silence, a general request to continue, or approval of one part does not
authorize the other part.
