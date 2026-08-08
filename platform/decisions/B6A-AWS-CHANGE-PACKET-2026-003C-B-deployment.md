# B6A AWS change packet 2026-003C-B — bounded zero-shot ASR platform proof

Status: **BLOCKED — NOT AUTHORIZED**

Prepared: `2026-08-05`

Required approval phrase:
`Approve B6A AWS change packet 2026-003C-B only.`

## Purpose and governing boundary

Packet 2026-003C-A closed as `PASS_SCAN_ONLY`. This packet is the first
deployment proof: publish the non-serving zero-shot artifact outside
`approved/asr/`, create one narrowly scoped ASR identity, install the locked
NVIDIA DRA component, start one GPU for at most two hours, return one synthetic
transcription, measure peak L4 memory, and return GPU capacity to zero.

This is **B6A**, not B6.1 or full B6. The artifact is Whisper large-v3 v0,
CTranslate2 float16, not fine-tuned and not production-approved. Its disclosed
zero-shot WER is Lingala `0.9207`, Luganda `1.0659`, Oromo `1.1749`; it fails
the `0.20` absolute gate. B5 remains `BLOCKED`, the immutable B5 report
`25217157215ea979440187aa050772ffdf248d75e1ae823d5dcb72cb9d8def30`
is unchanged, and all nine deferred languages retain `approved_version: null`.

Local engineering evidence:
`platform/evidence/B6A-LOCAL-ENGINEERING-2026-004.json`, SHA-256
`c35214da9688300c047a1afc8483a140a5bf55d5a7aa06428c43d6ede347cb58`.

This packet supersedes the unapproved deployment intent in 2026-003A and
2026-003B prospectively. Their records and every prior stop remain immutable.
It does not authorize a vulnerability waiver, rebuild, digest substitution,
training, promotion, production traffic or public endpoint.

## Exact immutable subjects

ECR scans attach to the platform-specific child manifest under an OCI index,
not to the index tag. Deployment must therefore pin the same scan-passed child
that was inspected. NVIDIA DRA is a single Docker manifest and has no child.

| Component | Exact deployable digest | Authoritative scan |
|---|---|---|
| model-loader | `sha256:cb794f2169dc65f391a0c9ec789997ce19a31b38d9087f263fba0863ba0414a5` | complete, 0 critical / 0 high |
| ASR runtime | `sha256:434ac9e757b56949324e9e480490042fcbf35285f27bdee713bd771b502f4087` | complete, 0 critical / 0 high |
| NVIDIA DRA | `sha256:7fb313758a20a04e80a53d5f6d1efe8e6fe936bc845001c21204c1c361d59246` | complete, 0 critical / 0 high |

The workload render is
`platform/k8s/b6a/asr-platform-proof-003c-b.rendered.yaml`, SHA-256
`9e51c009ea995c15261505b2416611a8bbf2e8071e75758527d9e180f9be8f68`.
The minimized DRA render is
`platform/k8s/b6a/nvidia-dra-003c-b.locked.yaml`, SHA-256
`0a03a12d34d94ef21f7c45a4041caadfbf9bd3bb2eab218186ef3d84b5c69897`.
It is derived from NVIDIA chart `0.4.1`, chart SHA-256
`e7c3bf452849d99f3952b1b2f6593ba851828e6752b7608b07d1c976d974daa4`,
retains only `gpu.nvidia.com`, disables compute-domain components, schedules
only on `workload=gpu`, and binds ResourceSlice validation to the actual
DaemonSet service account. No legacy device plugin is installed.

The artifact is bound as follows:

- Local tree and future prefix:
  `5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e`.
- Manifest SHA-256:
  `c64978f4f231516caa2387ab4ccee569ddf4a0f3b98049278c0afe4c307fd850`.
- Five artifact files: `3,090,835,702` bytes; with manifest:
  `3,090,838,860` bytes and six objects.
- Base, tokenizer and processor revision:
  `06f233fe06e710322aca913c1bc4249a0d71fce1`.
- Future location only:
  `s3://medzen-speech/b6a/asr/v0/5adf77568813513bc3697a1501ba354c04c7b93ea374fc5407cf4f6402f7431e/`.

The one test input is
`platform/testdata/b6a-003c-b-synthetic.wav`, SHA-256
`3e7b78cbf65b5b857d0bd2ea6b2568ce74c523be2b319ade8930c9ac6a7630c3`,
`155,962` bytes. It was generated locally twice with byte-identical output,
contains no PHI or clinical content, and is not derived from project data.

Any different file, image, chart, render, artifact, audio input or hash stops
the packet and requires a new versioned packet.

## Live preconditions and authorization record

Execution uses AWS profile `medzen`, account `558069890522`, region
`eu-central-1`, and caller
`arn:aws:iam::558069890522:user/s.fotso`. Before the first write, create a new
`B6A-AWS-AUTH-2026-003C-B` record that binds this committed packet's SHA-256,
the exact resources above, owner identity and approval timestamp. Every
mutation script refuses without that record and packet hash.

Fail closed before any write unless all of these remain true:

- Repository branch and remote contain the committed packet and authorization.
- All three exact ECR scan subjects remain `COMPLETE` with zero critical/high.
- S3 versioning is `Enabled`; default encryption is the MedZen data KMS key
  `arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57`.
- The exact B6A artifact prefix and `approved/asr/` each contain zero objects.
- EKS `medzen-speech` and GPU node group `gpu` are `ACTIVE` and healthy at
  `min=0, desired=0, max=1`.
- Underlying Auto Scaling group is exactly
  `eks-gpu-b8cfd795-fa28-70a1-b844-258a0f0adc26`, has zero instances and has
  no scheduled actions.
- `/medzen/registry` remains empty and no public B6A endpoint exists.
- The $15 reservation is still the only active billable reservation.

## Itemized authorized sequence

Operations are sequential. A failed identity, hash, scan, plan, policy,
readiness, inference, memory, logging, deadline or cleanup check stops the next
step and invokes the failure path.

1. **Reverify source and live state.** Recompute every bound hash, repeat the
   local artifact verifier, repeat the ECR scan-by-deployable-digest checks,
   and prove all live preconditions above.
2. **Publish the non-serving artifact.** Upload only the five manifest-listed
   files using explicit SSE-KMS, object checksum SHA-256, `If-None-Match: *`
   and versioned immutable writes. Read back and verify every checksum and
   version ID. Upload `MANIFEST.json` last so an interrupted partial prefix
   never appears ready. No object may be overwritten. No `approved/` path is
   permitted.
3. **Create the exact ASR read-only identity.** Generate a fresh saved Terraform
   plan at the authorized commit. It must pass
   `PASS_EXACT_B6A_PACKET_2026_003C_B_IDENTITY_PHASE` and contain exactly three
   additions, zero changes and zero deletions:
   `aws_iam_role.b6a_asr`, `aws_iam_role_policy.b6a_asr`, and
   `aws_eks_pod_identity_association.b6a_asr`. Apply only that saved plan. The
   preparation-time plan SHA is evidence only and is not executable.
4. **Install the locked DRA boundary while GPU remains zero.** Apply only the
   committed locked DRA render. Verify the exact DRA digest, one kubelet
   DaemonSet restricted to GPU nodes, the one DeviceClass, its RBAC and its
   service-account-bound ResourceSlice validation policy. No compute-domain,
   legacy plugin, public route or GPU workload is permitted.
5. **Prepare the private workload.** Reverify the committed workload render,
   exact child digests, ClusterIP-only Service, deny-all ingress, one replica,
   one DRA claim and exact non-approved manifest URI. Do not apply it yet.
6. **Arm the independent deadline before scale-up.** Create and immediately
   read back one Auto Scaling scheduled action named
   `medzen-b6a-003c-b-deadline-scale-zero` against the exact GPU ASG. Its start
   time must be no later than two hours from arming and its values must be
   `min=0, desired=0, max=1`. Install the process `EXIT`, `INT` and `TERM`
   cleanup trap before the deadline is armed. If arming or read-back differs,
   do not scale.
7. **Open the bounded GPU window.** Change only the EKS GPU node group's desired
   size from zero to one and wait for the exact DRA Pod, DeviceClass and
   ResourceSlice. Maximum is one `g6.xlarge`; a second GPU node is forbidden.
8. **Measure and prove the chain.** Start timestamped `nvidia-smi` sampling
   through the already scanned DRA Pod before applying the ASR workload. Then
   apply the exact workload, require model download, every file/tree SHA,
   bounded startup smoke, CUDA load and `/readyz`. Use loopback-only
   `kubectl port-forward`; expose no public endpoint. Submit the one bound WAV
   and require HTTP 200, `PLATFORM_PROOF_ONLY`, `production_approved: false`,
   exact v0 model identities and one transcript. Reverify the running
   model-loader and ASR Pod image digests. Record baseline, timestamped samples,
   peak and total L4 MiB. `NOT_MEASURED` refuses. Check logs contain no audio,
   phrase, transcript, patient data or credentials.
9. **Clean up immediately.** Whether proof passes or fails, scale/delete the
   exact B6A workload, set the EKS GPU node group to desired zero and wait for
   it to become active. Prove EKS desired zero, ASG desired zero with no
   instances, zero GPU nodes, zero B6A Pods and zero ASR replicas. Only after
   all zero proofs pass may the scheduled action be deleted and its absence
   verified. If cleanup proof is incomplete, leave the AWS-side action armed
   and report failure. If the action already executed, report that fact and
   continue zero-state verification; never claim a clean pass without it.
10. **Create immutable result evidence.** Bind the authorization, packet,
    repository commit, artifact objects/version IDs, identity plan/apply,
    Kubernetes resource identities, deadline receipt, transcript-safe proof,
    measured GPU memory, exact cleanup states, elapsed GPU time and cost.

The artifact, ASR identity and locked DRA objects may remain after the test as
non-serving reproducibility evidence. DRA has zero Pods when the GPU pool is
zero. The ASR workload must be deleted or at zero replicas, GPU desired size
must be zero, and no scheduled action may be removed until zero is proven.
Retention is not model approval or adoption.

## Deterministic outcomes

- `B6A_PLATFORM_PROOF_COMPLETE`: every item passes, one transcription is
  returned, peak L4 memory is measured, evidence is complete, and all cleanup
  zero proofs pass.
- `BLOCKED_PLATFORM_PROOF`: a trusted scan, readiness, inference, resource,
  log-safety or evidence gate refuses the proof.
- `FAILED_CLOSED_EXECUTION`: identity, upload, AWS, Kubernetes, deadline or
  cleanup execution prevents a trustworthy conclusion.

No outcome changes B5. Even the permitted success closes only the narrow B6A
artifact-to-transcription proof. Orchestrator, streaming, LLM/RAG, TTS and the
remaining B6 work stay incomplete.

## Explicitly prohibited

- Any image rebuild, retag, substitution, manual scan or security waiver.
- Any training, fine-tuned model use, quality claim or language reactivation.
- Any write to `approved/asr/`, model registration, MLflow stage transition,
  language `artifact`/`approved_version` change or production SSM change.
- Any model-serving alias, public endpoint, ingress, load balancer or production
  traffic.
- Any second GPU node, window over two hours, or concurrent billable packet.
- Any KMS key, ECR, network, CPU node group, production registry or unrelated
  Terraform change.
- Any audio except the exact synthetic no-PHI WAV.
- Any deletion of historical B4, B5, B6A stop or scan evidence.
- Describing v0 as production-ready, B5-passed, B6.1-complete or full B6.

## Cost and rollback boundary

- Aggregate ceiling: `$300`.
- Conservatively committed: `$47.5288`.
- Existing reservation: `$15`; no new reservation is created.
- Remaining after reservation: `$237.4712`.
- GPU limit: one `g6.xlarge` for at most two hours, desired zero before/after.
- Exact GPU and storage charges remain subject to billing reconciliation.

Rollback is scale-to-zero and deletion of only the exact namespaced B6A
workload. Artifact, identity and DRA retention is deliberate evidence
retention, not serving state. Any proposed later deletion or broader rollback
requires a separate reviewed action because these retained records are
reproducibility evidence.

No operation in this packet is authorized until the owner uses the exact
approval phrase at the top after reviewing the committed packet.
