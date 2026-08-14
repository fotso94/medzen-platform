# ASR base-model AWS change packet 2026-003 — GPU root-storage correction

Status: **OWNER APPROVAL REQUIRED — NOT AUTHORIZED**

Prepared: `2026-08-14`

Account / region: `558069890522` / `eu-central-1`

Required profile / caller: `medzen` / `arn:aws:iam::558069890522:user/s.fotso`

Cluster / node group: `medzen-speech` / `gpu`

Reviewed source commit: `d88dcfb08b28f02d06c3805efe42a84c7bf128a2`

Required approval phrase:

> Approve ASR base-model AWS change packet 2026-003 only.

## Purpose and authorization boundary

Replace only the zero-sized EKS GPU managed node group so its root volume is
40 GiB instead of the AWS default 20 GiB. Attempt 19 proved that 20 GiB cannot
hold the exact 7.3 GB scan-qualified evaluation image while containerd unpacks
its 12.46 GB root filesystem. The independently accepted capacity calculation
sets 29 GiB as the measured minimum and 40 GiB as the operational floor.

This packet authorizes only the node-group storage correction after exact owner
approval. It does not authorize attempt 20, a GPU scale-up, a model evaluation,
new endpoints, model publication, B5 promotion, production SSM changes, or any
other Terraform delta.

## Immutable evidence

- Attempt-19 terminal refusal:
  `platform/evidence/ASR-BASE-MODEL-PACKET-2026-002R-ATTEMPT-19-EPHEMERAL-STORAGE-REFUSAL.json`,
  SHA-256 `09c8d917f4bab9151c316e602ec94685c5c2a3eeef1fa78d4f110f9427e1f739`.
- Exact image/rootfs capacity qualification:
  `platform/evidence/ASR-EVAL-RUNTIME-GPU-EPHEMERAL-STORAGE-QUALIFICATION-2026-001.json`,
  SHA-256 `1c2723537b9d157ee19dc3ad8aad5db55565b349b3db166bf18655902362a6d6`.
- Pre-apply plan qualification:
  `platform/evidence/ASR-BASE-MODEL-GPU-STORAGE-PLAN-2026-001.json`.

Attempt 19 is terminal and consumed. None of these records is amended or
reinterpreted by this packet.

## Verified pre-change state

The preparation-time live readback found:

- node group `gpu`: `ACTIVE`, health issues `0`;
- disk size: `20 GiB`;
- scaling: minimum `0`, desired `0`, maximum `1`;
- instance type: `g6.xlarge`;
- AMI type: `AL2023_x86_64_NVIDIA`;
- current GPU instances: `0` after attempt-19 cleanup.

Any different disk size, nonzero desired size, unhealthy node group, unexpected
caller, or intervening Terraform drift refuses before apply.

## Exact Terraform change

The only source change to the managed GPU node-group configuration is:

```hcl
resource "aws_eks_node_group" "gpu" {
  disk_size = 40
}
```

AWS requires replacement to change a managed node group's disk size. The live,
refreshed preparation plan is exactly:

- resource: `aws_eks_node_group.gpu` only;
- action: `delete`, then `create`;
- replacement cause: `disk_size` only;
- field transition: `20 -> 40 GiB`;
- summary: `1 add, 0 change, 1 destroy, 1 replacement`;
- scaling before and after: `min=0, desired=0, max=1`;
- instance type, AMI type, role, subnets, labels, taint, update policy and
  effective tags preserved;
- output changes: `0`.

Preparation plan SHA-256:
`5be91449a2e8905bcced4cab28ebb915ca6b4cb41f00c543355db756b8a8d6b7`
(`85,444` bytes).

The saved preparation plan is evidence, not execution authority. A full plan
JSON is intentionally not committed because Terraform plan JSON may contain
sensitive state values. The non-sensitive machine-checked summary is committed.

## Fail-closed machine guard

`scripts/check_asr_eval_gpu_storage_plan.py` refuses unless all of the following
are simultaneously true:

- exactly one mutable resource exists and it is `aws_eks_node_group.gpu`;
- actions are exactly `delete, create`;
- `replace_paths` is exactly `disk_size`;
- disk size is exactly `20 -> 40`;
- scaling remains `0/0/1`;
- the expected cluster, node-group name, `g6.xlarge` type, NVIDIA AL2023 AMI,
  label, taint, role, subnets, update configuration and effective tags remain;
- no output changes or unexpected unknown replacement fields exist.

The guard passes the refreshed live plan. Its focused suite passes 17 tests,
including wrong resource/action, extra mutation, output drift, non-exact disk
sizes, multiple replacement causes, configuration drift and nonzero GPU size.

## Approved execution sequence after exact owner approval

1. Write a new, versioned authorization record binding the exact approval
   phrase and packet SHA-256.
2. Verify caller, account and region.
3. Read the node group directly and require `ACTIVE`, zero health issues,
   `diskSize=20`, and `min=0, desired=0, max=1`.
4. Run Terraform validation and generate a fresh live-state saved plan from the
   reviewed source commit.
5. Run the fail-closed guard. Stop if the plan differs in any respect from the
   exact single replacement described above.
6. Apply only that content-addressed saved plan.
7. Wait for node group `gpu` to return `ACTIVE`; verify zero health issues,
   `diskSize=40`, and `min=0, desired=0, max=1`.
8. Verify no GPU EC2 instance or Kubernetes workload was started.
9. Generate a post-apply Terraform plan and require `NO_CHANGES`.
10. Commit an immutable evidence record with before/after live readbacks, plan
    and guard hashes, apply result, no-change proof and explicit non-events.

## Failure handling and rollback

The change runs while desired size is zero, so it replaces metadata and the
managed node-group boundary without disrupting a workload. If preconditions or
the guard fail, do not apply. If AWS partially applies or the replacement does
not return healthy, stop, retain the observed state, confirm no GPU instance is
running, and publish a refusal receipt; no unreviewed retry or configuration
change is permitted.

Automatic rollback to 20 GiB is prohibited because 20 GiB is now proven unsafe.
A deliberate rollback would be another managed-node-group replacement and
requires a separate reviewed packet. A failed create requiring forward recovery
at the same 40 GiB configuration likewise requires a new packet based on the
actual post-failure state.

## Cost

No GPU node is started, so this replacement is expected to create no GPU-compute
or node-root-volume charge. Future 40 GiB root volumes exist only while bounded
GPU nodes run and their cost remains inside each separately approved evaluation
window. No new spend reservation is requested by this packet.

## Required postconditions

- GPU node group: `ACTIVE`, `diskSize=40`, scaling `0/0/1`, health issues `0`.
- GPU instances: `0`; CPU desired: `0`.
- Terraform residual plan: `NO_CHANGES`.
- No IAM, KMS, S3, ECR, endpoint, SSM, registry, language, model or deployment
  mutation.
- B5 remains `BLOCKED`; production remains untouched.
- Attempt 20 remains unauthorized until a separate successor packet is reviewed
  after this change is verified complete.

No AWS mutation is permitted before independent review PASS and the exact owner
approval phrase above.
