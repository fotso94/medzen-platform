# B6A AWS change packet 2026-004 — EKS standard support policy

Status: **OWNER APPROVAL REQUIRED — NOT AUTHORIZED**

Prepared: `2026-08-04`

Account: `558069890522`

Region: `eu-central-1`

Required profile and caller: `medzen` / `arn:aws:iam::558069890522:user/s.fotso`

Cluster: `medzen-speech`, Kubernetes `1.36`

## Purpose

Change only the EKS support policy from `EXTENDED` to `STANDARD`. The cluster
version upgrade completed under packet 2026-001, but AWS retained the prior
support policy. Leaving it on `EXTENDED` can create extended-support charges
after the Kubernetes version reaches the end of standard support.

This is intentionally separate from packet 2026-002. It does not deploy a
service, change Kubernetes version, resize a node group, install a component,
publish a registry value, or start a GPU.

## Verified pre-change state

- Cluster `medzen-speech` is `ACTIVE` at Kubernetes `1.36`.
- `upgradePolicy.supportType` is `EXTENDED`.
- GPU node group `gpu` is `ACTIVE` with `min=0`, `desired=0`, `max=1`.
- `/medzen/registry` contains zero parameters.
- Packet 2026-002 has completed and its post-apply Terraform plan has no
  residual changes.

## Exact proposed change

Terraform resource `aws_eks_cluster.this` receives one in-place update:

```hcl
upgrade_policy {
  support_type = "STANDARD"
}
```

The reviewed plan must contain exactly one changed resource, with action
`update`: `aws_eks_cluster.this`. It must contain zero creates, deletes,
replacements, output changes, or other updates.

The preparation-time saved plan was generated with a live state refresh and
passed the fail-closed checker as `PASS_EXACT_B6A_PACKET_2026_004`:

- Plan SHA-256: `9ee54d6ca474ad409178a19477ce68376c485c0a2e6ce1f4c9e2a1f7cdb052e6`.
- Plan size: `51,819` bytes.
- Summary: `0 add, 1 change, 0 destroy`.

This saved plan is review evidence, not standing authority. It must be
recreated and checked again after approval so intervening drift fails closed.

## Operational consequence to accept

`STANDARD` prevents the cluster from entering paid extended support. AWS may
automatically upgrade the control plane to the next supported Kubernetes minor
when the current version reaches the end of standard support. The platform
must therefore keep add-ons and workloads compatible and monitor the published
support calendar. This packet does not perform such a future version upgrade.

## Approved-operation request

After explicit owner approval naming packet 2026-004:

1. Re-verify the exact AWS caller, cluster health, support policy, GPU size zero
   and empty registry.
2. Recreate and machine-check the saved Terraform plan.
3. Apply only the reviewed saved plan if it remains exactly one in-place EKS
   support-policy update.
4. Verify the cluster returns `ACTIVE` at version `1.36`, support type becomes
   `STANDARD`, both node groups retain their scaling, and the registry remains
   empty.
5. Create an immutable local evidence receipt and run a no-change plan.

## Prohibited operations

- No Kubernetes version, add-on, node-image, scaling or network change.
- No GPU start and no Kubernetes workload mutation.
- No SSM parameter, KMS key, IAM policy, ECR or S3 change.
- No model publication, approval, registration, stage transition or training.
- No language registry change and no change to any B4/B5 evidence.
- No packet 2026-003 deployment operation.

## Cost and rollback

The API setting itself has no expected direct charge and is intended to avoid
future extended-support fees. If the setting is rejected or the plan contains
anything beyond the one allowed in-place update, stop without applying.
Reverting to `EXTENDED` later is a separate owner-reviewed decision and may
restore extended-support billing; it is not authorized here.

## Required postconditions

- Cluster: `ACTIVE`, Kubernetes `1.36`, support type `STANDARD`.
- GPU desired size: `0`.
- Registry parameters: `0`.
- MedZen deployments: `0`.
- Approved ASR writes and model registrations: `0`.
- B5 BLOCKED report: unchanged.

Explicit owner approval naming `B6A-AWS-CHANGE-PACKET-2026-004` is required
before any AWS mutation.
