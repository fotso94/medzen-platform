# B6A AWS change packet 2026-003C-A — scan-only image qualification

Status: **BLOCKED — NOT AUTHORIZED**

Prepared: `2026-08-05`

Required approval phrase:
`Approve B6A AWS change packet 2026-003C-A only.`

## Purpose and governing boundary

Packet 2026-003B stopped correctly at the ASR runtime's authoritative ECR
scan and cannot resume. Its immutable failure record remains SHA-256
`385b77b3e9aac7e9393ef10c912ae926e5527f3d92a47d1be24c3b19a5f4e4a9`.

This packet qualifies the two remaining exact image identities against the
live automatic ECR scanner before any new deployment packet is prepared. It
is deliberately scan-only: it authorizes no artifact upload, identity work,
cluster change, GPU window or deployment. A scan pass is not B6A completion.

Local engineering evidence:
`platform/evidence/B6A-LOCAL-ENGINEERING-2026-003.json`, SHA-256
`a2b1b6c7a739c0859f6d71463b829c4db0d4cc3023f797989e995f6ea6095784`.

The B5 BLOCKED gate report remains unchanged. No fine-tuned model is approved,
no language is reactivated, and no production serving alias is changed.

## Live read-only preconditions

All operations use profile `medzen`, account `558069890522`, region
`eu-central-1`. Immediately before a push, fail closed unless all of these
remain true:

- Caller is `arn:aws:iam::558069890522:user/s.fotso`.
- Regional ECR scanning is `BASIC` with one `SCAN_ON_PUSH` rule whose exact
  filters are `medzen-model-loader`, `medzen-asr-runtime` and
  `medzen-nvidia-dra`.
- All three repositories are tag-immutable, scan-on-push enabled and encrypted
  with KMS key
  `arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57`.
- The GPU node group is `ACTIVE`, healthy and remains `min=0, desired=0,
  max=1`.
- The new ASR tag is absent and the DRA repository remains empty.
- The retained model-loader deployable child
  `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5`
  still has automatic scan status `COMPLETE` with zero critical and zero high
  findings.

Any precondition mismatch stops the packet before authentication or push.

## Exact immutable subjects

The passed model-loader is read-only evidence and must not be rebuilt,
retagged or pushed.

| Subject | Local identity | ECR tag | Size | Local C/H |
|---|---|---|---:|---:|
| ASR runtime | OCI index `sha256:47d86776bb02dc9f06f40496a9905d89eb1fc25ab181607702743b06deb53a56`; deployable `linux/amd64` child `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` | `medzen-asr-runtime:b6a-003c-89f94d3` | 3,105,391,446 bytes | 0 / 0 |
| NVIDIA DRA | Docker manifest `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` | `medzen-nvidia-dra:v0.4.1-medzen.2-7fb313758a20` | 90,223,329 bytes | 0 / 0 |

The ASR image is bound to source commit
`89f94d330de24478d0084cdf9010f7ac7a968303`, config digest
`sha256:6c72dc9a0d40ca76872f6e142d170e28d073caa14e91ada32989370993f22670`
and non-root identity `10001:10001`. Its final image contains no
`python3-pip-whl`, `python3-setuptools-whl`, `python3.12-venv`, pip executable
or pip module. Import and CUDA/cuDNN dynamic-link smokes pass.

Local scans are prerequisites, not authoritative substitutes. Any local
identity, package, smoke or critical/high scan mismatch stops before push. A
rebuild or different digest requires a new packet.

## Allowed execution

1. Reverify the live preconditions and exact local identities above.
2. Authenticate Docker to the exact private ECR registry.
3. Tag and push only the exact ASR identity to its exact immutable tag.
4. Independently tag and push only the exact NVIDIA DRA identity to its exact
   immutable tag.
5. For ASR, resolve and match the pushed deployable `linux/amd64` child digest;
   the OCI index or attestation result is not a deployable scan result. For
   DRA, match the pushed single manifest digest.
6. Wait for each image's automatic scan-on-push result to reach `COMPLETE` and
   record all severity counts and findings. Do not invoke a manual scan.
7. Produce one immutable result record covering both independent scan
   subjects, their pushed digests, automatic scan timestamps and findings.

The two image scans are independent qualification subjects. A security finding
for one subject does not authorize deployment and does not prevent collecting
the other subject's automatic scan result in this scan-only packet. An
identity, repository, push, configuration or scanner-execution failure does
stop the packet immediately because the intended evidence cannot be trusted.

## Deterministic outcome

- `PASS_SCAN_ONLY`: both automatic scans are `COMPLETE`, both identities match,
  and both have `0 critical / 0 high` findings.
- `BLOCKED_IMAGE_SCAN`: either completed authoritative scan contains a critical
  or high finding, or a required authoritative result is missing.
- `FAILED_CLOSED_EXECUTION`: an identity, repository, configuration, push or
  scanner-execution failure prevents trustworthy evaluation.

No outcome from this packet permits deployment. Only `PASS_SCAN_ONLY` permits
preparation of a separate `2026-003C-B` deployment packet with fresh owner
approval.

## Explicitly prohibited

- Rebuilding or substituting either image.
- Reusing, copying or creating a vulnerability waiver.
- Calling `ecr:StartImageScan`; automatic scan-on-push is required.
- Uploading the zero-shot artifact or writing any S3 object.
- Creating or changing IAM, Pod Identity, Terraform state, EKS, Kubernetes,
  SSM or KMS resources.
- Installing NVIDIA DRA or applying any workload manifest.
- Scaling a GPU node or opening a GPU test window.
- Writing under `approved/asr/`, registering a model, changing any language
  `artifact` or `approved_version`, or changing production SSM.
- Describing a scan pass as a deployment, B6A completion, B6.1 or full B6.
- Deleting the new image evidence; deletion is not authorized by this packet.

## Cost and retention

- Aggregate project ceiling: `$300`.
- Committed before the existing reservation: `$47.5288`.
- Existing open packet reservation: `$15`; no second reservation is created.
- GPU desired size remains `0`; GPU hours and GPU cost remain `0`.
- ECR storage and scanning charges are not asserted as zero and will be
  reconciled against the existing reservation when billing data settles.
- The pushed images and scan records remain immutable evidence on either pass
  or refusal.

No operation in this packet is authorized until the owner uses the exact
approval phrase at the top of this record after reviewing the committed packet.
