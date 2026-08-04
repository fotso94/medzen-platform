# B6A AWS change packet 2026-003 — zero-shot ASR platform proof

Status: **BLOCKED — NOT AUTHORIZED**

Blockers: `BUDGET_RECONCILIATION`, `CLEAN_GPU_COMPONENT`, and
`EXPLICIT_OWNER_APPROVAL`.

No action in this packet has been applied to AWS. This is the first deployment
called **B6A**, not B6.1 and not full B6.

Account: `558069890522`

Region: `eu-central-1`

Cluster: `medzen-speech`, Kubernetes `1.36`

## Purpose and limits

Prove the thinnest untested serving chain:

`immutable v0 artifact -> model-loader -> SHA-256 verification -> GPU smoke
inference -> ASR Ready -> one transcription`

The model is the pinned, zero-shot `openai/whisper-large-v3` base converted to
CTranslate2 float16. It is a platform-test baseline only. Its preserved WERs
are Lingala `0.9207`, Luganda `1.0659`, and Oromo `1.1749`; all exceed the
prospective absolute maximum `0.20`. It is not approved for any language.

This packet does not use the blocked B4 candidate, alter the B5 report, approve
a model, update a language, publish the production registry, expose a public
endpoint, or authorize training.

## Bound local engineering

- Local evidence:
  `platform/evidence/B6A-LOCAL-ENGINEERING-2026-001.json`.
- Artifact tree SHA-256:
  `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e`.
- Manifest SHA-256:
  `c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850`.
- Artifact bytes: `3,090,835,702`.
- Base-model revision:
  `06f233fe06e710322aca913c1bc4249a0d71fce1`.
- Model-loader local image:
  `sha256:4e386a0488fcab36bf3a39b76229a2178f2f86bec483851f9d58bb2d494e12a6`.
- ASR-runtime local image:
  `sha256:35e348b9d44fa180de28dd80d68f1d100412b791b585d669c2dcbdc013cbcdc7`.
- Both MedZen runtime images report zero critical and zero high findings in
  the local Docker Scout scan.
- Review-only Kubernetes template:
  `platform/k8s/b6a/asr-platform-proof.template.yaml`. Its unresolved ECR
  digest tokens deliberately make it non-deployable.
- Review-only IAM templates under `platform/iam/b6a/`.

## Verified AWS pre-state

- EKS control plane, CPU node group, GPU node group and managed add-ons are
  `ACTIVE` at Kubernetes `1.36` with no reported health issues.
- GPU group is `g6.xlarge` on-demand with `min=0`, `desired=0`, `max=1`.
- The exact Frankfurt on-demand price observed through AWS Pricing is
  `$1.0064/hour`; continuous 730-hour operation would be about `$734.67`.
- ECR repositories `medzen-model-loader` and `medzen-asr-runtime` exist, are
  immutable, scan on push, use KMS encryption, and contain zero images.
- S3 bucket `medzen-speech` has versioning enabled and default SSE-KMS. The
  exact `b6a/asr/v0/<tree-sha>/` target is empty.
- `/medzen/registry` contains zero SSM parameters.
- Namespace `medzen` is absent.
- The AL2023 NVIDIA worker image supplies host drivers but the cluster has no
  NVIDIA DRA driver or Kubernetes device plugin. A GPU workload cannot yet be
  scheduled.

## GPU allocation component — hard blocker

Amazon EKS recommends the NVIDIA DRA driver for a new managed-node-group
deployment on Kubernetes 1.34 or later. It permits the model-loader and ASR
runtime containers in one Pod to reference the same GPU claim. The B6A
template therefore uses `resource.k8s.io/v1`, `gpu.nvidia.com`, and one shared
`ResourceClaimTemplate`.

The current components do not pass this project's deployment-image gate:

- NVIDIA DRA driver chart `0.4.1`, image
  `nvcr.io/nvidia/dra-driver-nvidia-gpu@sha256:a1f7731e18385b1441f7172a8941224dfac0f364f6f6a3043869c68d5adb170d`
  (`linux/amd64`): `1 critical`, `2 high`.
- NVIDIA Kubernetes device plugin `v0.19.0`: `2 critical`, `12 high`.

No exception is authorized. Before this packet can be activated, a new
version must be pinned and independently scanned at `0 critical / 0 high`, or
the owner must approve a separate, evidence-backed security exception. A
clean component must then be bound in a new packet revision. Installing an
unbound tag, running DRA and the device plugin together, or silently accepting
the present findings is prohibited.

## Proposed AWS changes after all blockers clear

All steps are sequential and stop on the first mismatch.

1. Reconcile the `$25` reservation for completed packet 2026-001 and prove no
   unresolved reservation remains under `B6A-BUDGET-2026-001`.
2. Re-verify the AWS caller, cluster/node/add-on health, empty ECR repositories,
   empty S3 target prefix, empty `/medzen/registry`, and GPU desired size zero.
3. Push the two scan-passed MedZen images to their existing immutable ECR
   repositories. Record their ECR `linux/amd64` digests and refuse if push-time
   enhanced/basic scan reports any critical or high finding.
4. Upload the exact local artifact and canonical manifest only to
   `s3://medzen-speech/b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/`
   with the existing KMS key, immutable object checksums, version ids and
   platform-test tags. Re-download the manifest and verify its SHA-256. Never
   write under `approved/asr/`.
5. Create role `medzen-b6a-asr-role` from the reviewed trust and permission
   templates. It may read only the exact B6A prefix through S3/KMS and may not
   mutate S3 or read `approved/asr/`. Create one EKS Pod Identity association
   for namespace `medzen`, service account `asr-runtime-b6a`.
6. Install the separately approved, digest-pinned, clean NVIDIA DRA chart with
   compute-domain resources disabled, GPU resources explicitly enabled, and
   the kubelet plugin restricted to nodes labeled `workload=gpu`. Verify its
   rendered manifests before apply. Do not install the legacy device plugin.
7. Create namespace, service account, private ConfigMap, shared GPU claim,
   Deployment, internal ClusterIP Service and ingress-deny NetworkPolicy from
   the reviewed template after replacing both image tokens with the recorded
   ECR digests. No Ingress, LoadBalancer, ALB or public route is allowed.
8. Set GPU node-group desired size to `1`, leaving `min=0` and `max=1`. Wait
   for one Ready node and verify one NVIDIA `ResourceSlice` and the
   `gpu.nvidia.com` DeviceClass before allowing the Pod to start.
9. Prove in order: exact manifest download, per-object SHA-256 verification,
   artifact-tree SHA-256, CUDA CTranslate2 smoke inference, ready marker,
   runtime startup, `/readyz`, model identity, and one bounded transcription.
   Capture startup time, latency and peak L4 GPU memory. Logs must contain no
   audio, transcript or other patient data.
10. Record immutable evidence, scale the Deployment to zero, set the GPU group
    back to desired zero, and verify no GPU node or B6A Pod remains. Retain the
    immutable ECR/S3 test artifacts and their evidence; they remain
    non-approved and non-serving.

## Cost reservation after activation

- Maximum GPU test window: `2 hours`.
- Exact GPU instance maximum for that window: `$2.0128`.
- Proposed total reservation: `$15`, covering GPU time plus bounded ECR/S3,
  KMS, log and data-transfer overhead.
- GPU desired size must be zero before and after the window. The packet stops
  and scales down at two hours even if the proof is incomplete.
- This reservation cannot be opened while packet 2026-001's `$25` reservation
  is unresolved.

## Success conditions

- The full loader-to-transcription chain passes once on an L4 GPU.
- Artifact and manifest hashes match exactly.
- Peak L4 GPU memory is measured and recorded, not inferred from disk size.
- Deployment and GPU node return to zero after the bounded test.
- Approved ASR writes: `0`.
- Registered models/model versions: `0 / 0`.
- Language `artifact` and `approved_version` changes: `0`.
- Production SSM changes: `0`.
- B5 BLOCKED report unchanged.
- Result is `B6A_PLATFORM_PROOF_COMPLETE`; B6.1 and full B6 are not claimed.

## Failure and rollback

Any mismatch, failed scan, missing claim, hash failure, failed smoke inference,
unready Pod, PHI-bearing log or budget/time breach stops the test. Scale the
Deployment and GPU group to zero first. Delete only the newly created B6A Pod
Identity association and IAM role if their policy is incorrect; do not delete
immutable evidence objects or reuse their prefix. Preserve logs with PHI-safe
redaction, ECR digests, S3 version ids and the failure reason. Do not fall back
to CPU, an unpinned image, a different model, the B4 artifact or a production
registry path.

## Approval semantics

The owner's earlier B6A authorization approved local engineering and AWS
packet 2026-001 only. It did not approve these operations. Even after the cost
and clean-component blockers are resolved, an explicit owner message naming
the revised B6A deployment packet is required before any ECR push, S3 upload,
IAM creation, Kubernetes mutation, or GPU scale-up.
