# B6A AWS change packet 2026-005 — exact ECR scan-on-push rules

Status: **BLOCKED — NOT AUTHORIZED**

Prepared: `2026-08-05`

Required approval phrase: `Approve B6A AWS change packet 2026-005 only.`

## Why this packet exists

Packet 2026-003A proved that the regional ECR registry currently uses Basic
Scanning with no matching scan-on-push rules. The deprecated repository flag
was `true`, but the OCI index received no top-level scan and the deployable
`linux/amd64` child manifest had to be scanned explicitly. The child scan
correctly stopped the run at one critical and three high findings.

The immutable failure record remains:

- `platform/evidence/B6A-PACKET-2026-003A-FAILED-IMAGE-SCAN.json`
- SHA-256 `e7bac17f17eacefdfe418a91041f694764e68b004485712d4abc358836cecb68`

This packet establishes the registry-level trigger before packet 2026-003B.
It neither pushes nor scans an image by itself.

## Exact proposed AWS delta

In account `558069890522`, region `eu-central-1`, set the ECR registry scanning
configuration to:

- Scan type: `BASIC`
- Frequency: `SCAN_ON_PUSH`
- Exact repository filters:
  - `medzen-model-loader`
  - `medzen-asr-runtime`
  - `medzen-nvidia-dra`

The filter type required by ECR is `WILDCARD`, but each filter value is an
exact repository name and contains no wildcard character. In particular,
`medzen-tts-gateway` is separately owned and must not be matched, scanned,
modified or referenced by this change.

The Terraform saved plan must be accepted by
`scripts/check_b6a_ecr_scanning_plan.py` as
`PASS_EXACT_B6A_PACKET_2026_005`. The only changed Terraform address may be:

`aws_ecr_registry_scanning_configuration.b6a_runtime`

No replacement, deletion or output change is allowed.

The read-only preparation plan generated on `2026-08-05` satisfies that
guard with `1 add / 0 change / 0 destroy`:

- Preparation path: `/private/tmp/medzen-b6a-005.tfplan`
- Bytes: `52,171`
- SHA-256: `bb4c4a1ccebb547147b7077fb5e721795efc382dbc17d894c70f5cd7bad8ddd7`

This saved plan has **not** been applied. It must be regenerated after the
packet is committed and approved, and the regenerated plan must independently
pass the same exact guard before any apply.

## Preconditions and execution sequence

1. Verify the caller is the owner-approved `medzen` profile in account
   `558069890522` and region `eu-central-1`.
2. Capture the live registry scan configuration. It is expected to be
   `BASIC` with an empty rule list; any difference stops the packet.
3. Produce and save a Terraform plan.
4. Run the machine guard and record the saved-plan SHA-256 and byte count.
5. Apply that exact saved plan once.
6. Read the live configuration back and require exactly the three filters
   above at `SCAN_ON_PUSH`.
7. Run a residual Terraform plan and require `NO_CHANGES`.
8. Commit an immutable execution receipt before packet 2026-003B is eligible.

## Explicitly prohibited

- ECR repository creation, deletion or mutation
- Image push, tag, deletion or scan invocation
- Any S3, IAM, KMS, SSM, EKS, Kubernetes or GPU change
- Any filter matching `medzen-*` or the separately owned TTS repository
- Any security waiver
- Any model, registry-language, approved-ASR or B5 evidence change
- Any claim that B6A is deployed or complete

## Cost and rollback

No compute, GPU, artifact storage or image publication is part of this packet.
Billing must still be reconciled conservatively under the existing `$300`
aggregate ceiling.

On a verified regression, restore the exact pre-change regional registry
configuration captured in step 2, verify it by read-back, and record the
rollback. Do not delete repositories or images as rollback.

No operation in this packet is authorized until the owner explicitly approves
`B6A AWS change packet 2026-005 only`.
