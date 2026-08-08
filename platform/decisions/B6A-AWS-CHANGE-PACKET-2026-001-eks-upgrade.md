# B6A AWS change packet 2026-001 - EKS standard-support upgrade

Status: `OWNER_APPROVED`

Authorized: `2026-08-03T23:35:11Z`

Authorization source: owner message approving the corrected phased B6A plan,
ordering the EKS extended-support upgrade first, and extending the aggregate
project ceiling to `$300`.

Account: `558069890522`

Region: `eu-central-1`

Required profile: `medzen`

Cluster: `medzen-speech`

## Purpose

Move the idle MedZen EKS serving cluster from Kubernetes `1.31` extended
support to Kubernetes `1.36` standard support before any B6A runtime is built
or deployed. The upgrade is forward-only and proceeds one minor version at a
time: `1.31 -> 1.32 -> 1.33 -> 1.34 -> 1.35 -> 1.36`.

This packet does not deploy a MedZen service, scale the GPU group, create an
approved model, publish the production registry, or change any B4/B5 record.

## Verified pre-change state

- Caller: `arn:aws:iam::558069890522:user/s.fotso`.
- Cluster: `ACTIVE`, Kubernetes `1.31`, platform `eks.66`, support policy
  `EXTENDED`.
- Kubernetes `1.36`: `STANDARD_SUPPORT` through `2027-08-01`.
- Control-plane logs enabled: API, audit and authenticator.
- CPU node group: `ACTIVE`, Kubernetes `1.31`, two `m6i.large` on-demand
  nodes, `min=2`, `desired=2`, `max=4`, no health issues.
- GPU node group: `ACTIVE`, Kubernetes `1.31`, `g6.xlarge` on-demand,
  `min=0`, `desired=0`, `max=1`, no health issues.
- Workloads: only the AWS-managed `kube-system` pods; no MedZen runtime is
  deployed.
- Add-ons are `ACTIVE` with no health issues: CoreDNS
  `v1.11.4-eksbuild.40`, VPC CNI `v1.22.3-eksbuild.1`, kube-proxy
  `v1.31.14-eksbuild.24`, Pod Identity agent `v1.3.10-eksbuild.3`.
- First-step upgrade insights for `1.32`: five `PASSING`; add-on
  compatibility `UNKNOWN`. The independent add-on compatibility query proves
  the installed CoreDNS, VPC CNI and Pod Identity versions are the AWS default
  compatible versions for `1.32`; kube-proxy must move to
  `v1.32.13-eksbuild.20`.
- The configured Terraform identity cannot read the remote state object and
  receives S3 `403 AccessDenied`. No Terraform apply is permitted until that
  access is repaired. The repository target version is still updated so the
  intended end state is explicit.

## Cost reservation

- Bound budget decision: `B6A-BUDGET-2026-001`.
- Maximum incremental reservation for this upgrade: `$25`.
- Current approximate hourly infrastructure while the two CPU nodes run:
  EKS extended-support control plane `$0.60` plus two `m6i.large` nodes at
  `$0.115` each = `$0.83/hour`, before small EBS, public IPv4 and logging
  charges.
- GPU desired size must remain `0` throughout this packet.
- No additional node group or other billable service may be created.

## Approved operations

For each next minor version, and never concurrently:

1. Re-verify caller account, cluster `ACTIVE`, node groups `ACTIVE`, add-ons
   `ACTIVE`, and no outstanding EKS update.
2. Query EKS upgrade-readiness insights for exactly the next version. Any
   `ERROR` refuses. Any `UNKNOWN` must be resolved by an explicit compatible
   add-on-version query before continuing.
3. Upgrade the control plane to exactly the next minor version and wait for
   the returned update id to reach `Successful`.
4. Upgrade both managed node-group versions to the same minor version without
   changing their scaling configuration, and wait for each update to reach
   `Successful`.
5. Query the AWS default compatible versions for all four managed add-ons.
   Update only versions that differ, using conflict preservation. Wait for
   every add-on to return `ACTIVE` with no health issue.
6. Verify Kubernetes nodes are `Ready`, all `kube-system` pods are ready, the
   API is responsive, and the next-version readiness insights contain no
   `ERROR` before starting another minor upgrade.
7. Record update ids, versions, timestamps, health results and observed cost
   duration in an immutable local evidence receipt.

The first failed or ambiguous check stops the packet. It never skips a minor
version and never starts a second update while one is in progress.

## Prohibited operations

- No GPU scaling or GPU pod.
- No MedZen runtime deployment.
- No ALB, ingress, route-table, subnet, VPC or security-group change.
- No production SSM publication.
- No model conversion, training, registration, stage transition or copy to
  `approved/asr/`.
- No language `artifact` or `approved_version` change.
- No B5 report regeneration or change.
- No change to the separately owned `medzen-tts-gateway` repository or ECS
  service.
- No Terraform apply while the remote state is unreadable.

## Failure and recovery

EKS control-plane versions cannot be downgraded. Recovery is therefore
forward-fix only:

- If an update fails, stop and preserve the update id and AWS error.
- Do not advance to another minor version.
- Leave GPU size at zero and do not deploy workloads.
- Repair the reported control-plane, node-group or add-on issue at the current
  version, then re-run readiness checks before any retry.
- If AWS reports a control-plane failure that cannot be corrected locally,
  open AWS Support with the recorded update id.

The two CPU nodes provide a real post-update readiness check. Scale-to-zero is
a separate cost-containment change after the cluster reaches `1.36` and after
the Terraform scaling configuration is corrected; it is not bundled into this
forward-only version packet.

## Required postconditions

- Cluster `ACTIVE` at Kubernetes `1.36` under standard support.
- CPU and GPU managed node groups `ACTIVE` at `1.36`.
- GPU scaling unchanged at `min=0`, `desired=0`, `max=1`.
- All four managed add-ons `ACTIVE`, compatible with `1.36`, no health issues.
- Nodes `Ready`; every `kube-system` pod ready.
- MedZen deployments: `0`.
- Approved ASR writes: `0`.
- Production SSM writes: `0`.
- Registered models/model versions: `0 / 0`.
- B5 BLOCKED report unchanged.
- Immutable upgrade receipt committed before any later B6A deployment packet.
