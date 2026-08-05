# B6A AWS change packet 2026-003B — remediated zero-shot platform proof

Status: **BLOCKED — NOT AUTHORIZED**

Prepared: `2026-08-05`

Required approval phrase after every prerequisite is closed:
`Approve B6A AWS change packet 2026-003B only.`

## Governing interpretation

Packet 2026-003A stopped correctly at its ECR image gate. It cannot resume.
Its immutable failure record remains SHA-256
`e7bac17f17eacefdfe418a91041f694764e68b004485712d4abc358836cecb68`.

This packet supersedes 003A prospectively and retains the unchanged purpose,
budget, non-promotion boundaries, two-hour limit, failure rules and cleanup
rules from packets 003 and 003A. It incorporates the owner-approved local-only
responsibility change in `B6A-DESIGN-2026-002`:

- The model-loader downloads and verifies the exact manifest, every file hash
  and the artifact-tree hash, writes an atomic schema-v2 marker and exits. It
  has no inference stack and requests no GPU.
- The ASR runtime refuses an incomplete marker, loads the serving CUDA model,
  performs one bounded inference, and reports ready only after that succeeds.
- The startup probe polls `/readyz`; it does not execute inference itself.

B5 remains `BLOCKED`, the zero-shot model remains `PLATFORM_PROOF_ONLY`, and
this packet cannot approve a model, language or production serving alias.

## Mandatory prerequisite — packet 2026-005

`B6A-AWS-CHANGE-PACKET-2026-005-ecr-scan-rules.md` must first be explicitly
approved, applied and closed with immutable evidence proving that the regional
ECR Basic Scanning configuration has exact `SCAN_ON_PUSH` filters for:

- `medzen-model-loader`
- `medzen-asr-runtime`
- `medzen-nvidia-dra`

Until then, **003B is ineligible for approval**. No operation from this packet
may be combined with packet 2026-005.

## Bound local engineering and image identities

Local evidence:
`platform/evidence/B6A-LOCAL-ENGINEERING-2026-002.json`.

Source commit: `69bdce16cb880d5ff3d75ee9058651329214654d`.

| Image | Bound local tag | Bound local OCI index | Local size | Scout C/H |
|---|---|---|---:|---:|
| Model loader | `medzen-model-loader:b6a-003b-69bdce1` | `sha256:ac6f694b2e420c908b5aba21fa5009a2e3051359624710161b55645c0dfbcdbc` | 43,903,251 | 0 / 0 |
| ASR runtime | `medzen-asr-runtime:b6a-003b-69bdce1` | `sha256:cb0d783dbc9973ca4badf53871b5a54220e9e4de71c94d42c72b235a929d3ad0` | 3,111,673,226 | 0 / 0 |
| NVIDIA DRA | `medzen-nvidia-dra:v0.4.1-medzen.2` | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` | evidence-bound | 0 / 0 |

No serving-plane vulnerability waiver exists. A missing local image or any
identity mismatch invalidates this packet; do not rebuild and substitute a new
identity under the same approval.

The OCI index may contain a provenance attestation that ECR Basic Scanning
cannot scan. After every push, resolve the deployable `linux/amd64` child
manifest digest and require its automatic scan to reach `COMPLETE` at
`0 critical / 0 high`. A top-level index result, local Scout result or manual
scan is not a substitute for proving the new scan-on-push trigger works.

The prepared 003B identity source also produced a read-only targeted proof of
`3 add / 0 change / 0 destroy`, accepted by the 003B guard:

- Preparation path: `/private/tmp/medzen-b6a-003b-identity-preparation.tfplan`
- Bytes: `46,327`
- SHA-256: `4cbf19a5be8058a781843b9de03557974401dbe9efec47196c6b8b2d78d23e67`

It has **not** been applied and is not executable authorization. After packet
2026-005 is applied, 003B execution must generate a fresh plan without relying
on the preparation target and pass the same exact identity guard.

## Sequential stop-on-mismatch execution

All operations use profile `medzen`, account `558069890522`, region
`eu-central-1`. Before starting, verify EKS 1.36 `STANDARD`, zero cluster
health issues, GPU desired size `0`, no B6A namespace, no B6A role, no artifact
at the target prefix, no approved-ASR write and no production registry value.

1. Verify packet 2026-005 completion evidence and read back the exact live ECR
   scan rules. Any absence or additional filter stops 003B.
2. Inspect the three local images and require the exact identities above.
   Re-run local critical/high scans and runtime smokes; any mismatch stops.
3. Tag and push **only the model-loader** to its existing immutable repository.
   Record the top-level index and `linux/amd64` child digests. Require an
   automatic child scan at `COMPLETE`, `0 critical / 0 high`. Otherwise stop.
4. Only after step 3 passes, push the ASR runtime and apply the identical child
   digest scan gate. Otherwise stop.
5. Only after step 4 passes, push the NVIDIA DRA image and apply the identical
   child digest scan gate. Otherwise stop.
6. Only after all three ECR gates pass, upload the existing immutable zero-shot
   artifact and manifest to the exact non-approved
   `s3://medzen-speech/b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/`
   path. Require object hashes, KMS encryption and version identifiers. Write
   nothing under `approved/`.
7. Produce a new saved Terraform plan for only the dedicated B6A role, inline
   policy and Pod Identity association. Require `0 destroy`, the exact
   reviewed trust/policy templates, one-hour maximum role session and access
   only to the content-addressed B6A artifact path. Require
   `scripts/check_b6a_003b_identity_plan.py` to return
   `PASS_EXACT_B6A_PACKET_2026_003B_IDENTITY_PHASE`. Apply only that saved plan.
8. Render NVIDIA DRA with its exact ECR child digest and the existing reviewed
   restrictions: GPU workload nodes only, GPU resources enabled, compute-domain
   resources disabled, no legacy device plugin. Apply only the reviewed render.
9. Replace the two Kubernetes image placeholders with the exact scan-passed
   ECR child digests. Confirm the loader has no GPU claim and ASR is the only
   container claiming the one DRA GPU. Apply the internal namespace, service
   account, config, claim, deployment, ClusterIP service and deny-ingress
   policy only.
10. Scale one `g6.xlarge` GPU node for at most two hours. Prove:
    artifact download, schema-v2 hash marker, ASR CUDA load, one-time startup
    smoke, `/readyz`, one internal transcription, exact response disclosure,
    SHA-256 continuity and peak L4 GPU memory measurement.
11. On success or failure, delete only the B6A Kubernetes workload, scale the
    GPU node group to desired `0`, verify no GPU node or B6A pod remains, and
    commit immutable evidence. Retain image/artifact evidence needed for audit.

## Hard stops and rollback

Any failed or missing scan, identity mismatch, hash failure, failed startup
smoke, missing DRA claim, unexpected Terraform/Kubernetes delta, budget risk,
or inability to prove cleanup stops the sequence. Set the deployment to zero
and the GPU group to desired zero first. Do not make a later step repair an
earlier failed gate.

No failed image may be deployed. No waiver may be introduced during execution.
Any new build, package or digest requires a new versioned packet.

## Budget and non-promotion boundaries

- Existing aggregate ceiling: `$300`.
- Existing 003A reservation: `$15`, still open pending reconciliation; this
  packet does not create a second `$15` reservation.
- Historical committed amount before reservation: `$47.5288`.
- Maximum GPU window: two hours on one `g6.xlarge`.
- GPU desired size: `0` before and after.
- Approved ASR writes: `0`.
- Registered models/model versions: `0 / 0`.
- Production SSM changes: `0`.
- Language `artifact` and `approved_version` changes: `0`.
- B5 BLOCKED report: unchanged.
- Only permitted success label: `B6A_PLATFORM_PROOF_COMPLETE`, never B6.1 or
  full B6.

No ECR push, artifact upload, IAM/Kubernetes mutation, GPU scale-up or other
003B operation is authorized until packet 2026-005 is verified complete and
the owner then explicitly approves `B6A AWS change packet 2026-003B only`.
