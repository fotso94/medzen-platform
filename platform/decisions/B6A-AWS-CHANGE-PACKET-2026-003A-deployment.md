# B6A AWS change packet 2026-003A — zero-shot ASR platform proof

Status: **BLOCKED — NOT AUTHORIZED**

Prepared: `2026-08-04`

Remaining blockers: `PACKET_2026_004_STANDARD_SUPPORT` and
`EXPLICIT_OWNER_APPROVAL_OF_2026_003A`.

This version supersedes the unapproved packet 2026-003 prospectively. The
original file remains unchanged at SHA-256
`e5af50e5bded83ec2feffa682481a76fc706b8aee0a3fea7c56ab33341192da1`.
Every unchanged purpose, limit, proposed operation, success condition, failure
rule and rollback rule in packet 2026-003 is incorporated here. The changes
below replace its former blocker and GPU-component sections.

## Blockers now resolved

- **Terraform identity:** packet 2026-002 was planned and applied using the
  explicit `medzen` profile and exact owner identity. The dedicated registry
  IAM boundary is verified complete, `/medzen/registry` remains empty, and the
  residual Terraform plan has no changes. The earlier 403 was a wrong default
  credential chain, not unavailable remote state.
- **Budget:** `B6A-BUDGET-2026-002` conservatively recognizes the full `$25`
  upgrade reservation as committed, reserves `$15` for this bounded proof,
  and leaves `$237.4712` under the `$300` ceiling. This reservation is not AWS
  or deployment authorization.
- **GPU component scan:** the local, reproducible
  `medzen-nvidia-dra:v0.4.1-medzen.2` image is built for `linux/amd64` at local
  image id
  `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246`.
  Docker Scout reports `0 critical / 0 high` across 112 indexed packages.
  Evidence is `B6A-NVIDIA-DRA-LOCAL-2026-001`, SHA-256
  `6c33d2e1e0dc381608fec5875010a657e46da2e8c53944dea1af3d6c60e5c800`.

No serving-plane vulnerability waiver is used. The exact released DRA v0.4.1
and NVIDIA Container Toolkit v1.19.1 sources are pinned; `x/net` is updated to
`0.55.0`, gRPC to `1.82.1`, and the CDI helper is rebuilt with Go `1.26.4`.
All upstream Go tests and both binary version smokes pass. The source recipe
contains no push, AWS or Kubernetes operation.

## Remaining prerequisite

Packet 2026-004 must first be explicitly approved, applied and verified so the
EKS cluster uses `upgradePolicy.supportType=STANDARD`. That packet is exactly
one in-place cluster-policy update and must not be bundled with this
deployment. Packet 2026-003A then still requires a separate explicit owner
approval naming this exact revision.

## Revised AWS delta after approval

The sequential stop-on-mismatch procedure in packet 2026-003 remains in force,
with these replacements and additions:

1. Rebuild the NVIDIA DRA image from
   `scripts/build_b6a_nvidia_dra.sh`; require the bound image id and a local
   `0 critical / 0 high` scan before any AWS write.
2. Create one dedicated ECR repository named `medzen-nvidia-dra`. It must be
   immutable, scan on push, use the existing MedZen data KMS key, and have no
   lifecycle rule that can remove the evidence-bound deployment digest during
   B6A. No existing repository may be repurposed.
3. Push the model-loader, ASR runtime and locally rebuilt NVIDIA DRA images to
   their exact MedZen ECR repositories. Record their ECR `linux/amd64` digests.
   Refuse on any push-time critical or high finding; a local scan cannot replace
   the registry scan.
4. Render the NVIDIA DRA v0.4.1 chart locally with the custom ECR repository and
   exact ECR digest. Verify the rendered workload uses only that digest,
   restricts the kubelet plugin to `workload=gpu`, enables GPU resources,
   disables compute-domain resources, and does not install the legacy device
   plugin. Apply only the reviewed render.
5. Continue the unchanged packet 2026-003 sequence for the immutable v0 model,
   B6A role and Pod Identity, internal ASR workload, two-hour GPU window,
   loader/hash/smoke/transcription proof, L4 memory measurement, evidence, and
   scale-to-zero cleanup.

The future Terraform/Kubernetes plan for this packet must contain only these
itemized resources plus those already listed in packet 2026-003. Any unrelated
EKS, node-group, network, SSM, KMS-key, language-registry or production-serving
change refuses the entire packet.

## Cost and outcome remain unchanged

- Maximum reservation: `$15`.
- Maximum GPU window: `2 hours` on one `g6.xlarge`.
- GPU desired size: `0` before and after.
- Approved ASR writes: `0`.
- Registered models/model versions: `0 / 0`.
- Production SSM changes: `0`.
- Language `artifact` and `approved_version` changes: `0`.
- B5 BLOCKED report: unchanged.
- Permitted success label: `B6A_PLATFORM_PROOF_COMPLETE`, not B6.1 or full B6.

No ECR repository creation, image push, S3 upload, IAM creation, Kubernetes
mutation or GPU scale-up is authorized until packet 2026-004 is complete and
the owner explicitly approves `B6A-AWS-CHANGE-PACKET-2026-003A`.
