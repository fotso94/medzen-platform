# B6 AWS change packet 2026-005 — ALB controller qualification boundary

Status: **DRAFT — AWAITING INDEPENDENT REVIEW AND OWNER APPROVAL**

Prepared: `2026-08-09`

## Purpose

Qualify one exact AWS Load Balancer Controller release for the future B6.6
synthetic integration window. Create its immutable private ECR repository and
eighth live scan rule, mirror and scan the exact upstream Linux/AMD64 child,
then create the narrowly scoped controller role, Pod Identity association and
empty internal-ALB security group.

This packet does **not** install the Helm release. With worker nodes at zero,
the controller's failure-closed admission webhooks would have no Ready
endpoints and could block unrelated Kubernetes changes. The Terraform-managed
Helm release is therefore guarded by
`enable_b6_load_balancer_controller=false`. The B6.6 successor may set it true
only after deadline-first cleanup is armed and CPU nodes are Ready, and must
set it false/remove the release before scaling CPU back to zero.

## Immutable inputs

| Binding | Value |
|---|---|
| Preparation authorization commit | `1d4a3bbb1e79145cdcc10ee9ba0877c2cf3fe95d` |
| Preparation authorization tree | `41ba4711c9774418f32d9ad8615ddfdaf84fce1c` |
| Controller release | `v3.5.0`, released `2026-08-03` |
| Upstream index digest | `sha256:298acdff5a571731276aaea3d5cc450a264e4ad710a5bddf3e518f68a3f9f6cb` |
| Exact Linux/AMD64 child | `sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f` |
| Private repository | `558069890522.dkr.ecr.eu-central-1.amazonaws.com/medzen-aws-load-balancer-controller` |
| Immutable private tag | `v3.5.0-c2ebdeae779c` |
| Helm chart | `aws-load-balancer-controller` `3.5.0` |
| Chart package SHA-256 | `45051f634b33e10baccb3354d0681b7de787c60445e599fa276e0c9aedd4ccd5` |
| Design record SHA-256 | `49c39f78289b6dbe95fdd51b7a0be3d6c50de2fa1d8249028731574e51f7fb70` |
| Local qualification SHA-256 | `ad31e66e57bd789414446412da9e6626ceee517c53bb35b899341d7a1049636f` |
| Deterministic Deployment render SHA-256 | `18a14c29f8cb2734f2a176d03d7de410355682a85ad3ca45308413211c4d9275` |
| Narrow IAM template SHA-256 | `f8d3362a83dca756c1af76bf6facff131aef7888771766c53d10f93ca59a27bc` |
| Reviewed Helm values SHA-256 | `583a2ae8fc8f18bdd57dbf2bd2ba5136fa3ee0d2134b23f7998b1a09ca9ff519` |
| Digest post-renderer SHA-256 | `bcd3b3e97e992f96457df392f76aa9da8d91f801a0610cc5533660c6400ce5cb` |
| Terraform implementation SHA-256 | `c7005d9c2fa230f1192d2867c75d7691702ba9ff9191e878623c6956671c22eb` |
| Two-stage plan guard SHA-256 | `7723988df1be0d44a016440d9b2648f782e27e872747ff434c61500a1b276986` |

The official v3.5.0 IAM reference SHA-256 is
`16f232c9d9f79366fe949c4550ad517a202380058a9e48d45a4e215044a20a6a`.
The custom policy removes service-linked-role creation (the required ELB
service role already exists), all EC2 security-group mutation, WAF, Shield,
ACM, Cognito and certificate-management permissions.

## Preflight facts

- Account `558069890522`; Region `eu-central-1`; operator
  `arn:aws:iam::558069890522:user/s.fotso`.
- Cluster `medzen-speech` is Kubernetes `1.36` on `STANDARD` support.
- CPU node group: minimum `0`, desired `0`; GPU: minimum `0`, desired `0`.
- Live ECR configuration has exactly seven existing MedZen scan filters.
- The controller repository, role, Pod Identity and internal-ALB security group
  do not exist.
- Local Docker Scout indexed 103 packages in the exact child: `0` critical,
  `0` high; no waiver was used. This is not a substitute for the live ECR scan.

## Stage A — repository and real scan gate

Generate a fresh targeted plan for exactly:

1. `aws_ecr_repository.b6_load_balancer_controller`: create; immutable tags,
   KMS encryption, scan on push, `force_delete=false`, no lifecycle deletion.
2. `aws_ecr_registry_scanning_configuration.b6a_runtime`: one in-place change
   from the current seven exact filters to the same seven plus
   `medzen-aws-load-balancer-controller`.

The prepared preview plan was `1 add / 1 change / 0 destroy`, SHA-256
`c8161a926d2827ce45405eabeb859172387f3eae63202b6a0784541f4362ca37`.
Because another packet can advance the Terraform state serial before this one
runs, that binary is review evidence, not blindly reusable. Immediately before
apply, regenerate it from the reviewed commit and require:

`python scripts/check_b6_lbc_plans.py a <fresh-plan>`

to return `PASS_B6_LBC_STAGE_A`. Any extra delta refuses the packet.

After Stage A apply:

1. Authenticate only to account `558069890522` ECR.
2. Pull the exact upstream child by digest with platform `linux/amd64`.
3. Push it under the immutable private tag. A rebuild is prohibited.
4. Require the private ECR child digest to equal
   `sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f`.
5. Query the scan by **child digest**, never by the OCI-index tag.
6. Wait for `COMPLETE`; require `0` critical and `0` high. Any finding,
   missing scan, digest mismatch, timeout or unsupported state stops before
   Stage B. No waiver is permitted.

## Stage B — narrow standing boundary, no Helm installation

Only after the ECR receipt passes, generate a fresh targeted plan with exactly
`4 add / 0 change / 0 destroy`:

- `aws_security_group.b6_internal_alb`: exact VPC, no ingress, only TCP 8080
  egress to the shared VPC CIDR;
- `aws_iam_role.b6_load_balancer_controller`: Pod Identity trust, one-hour
  maximum session;
- `aws_iam_role_policy.b6_load_balancer_controller`: the reviewed custom
  policy; and
- `aws_eks_pod_identity_association.b6_load_balancer_controller`: exact
  `kube-system/aws-load-balancer-controller` binding.

The prepared Stage-B preview passed the guard and has SHA-256
`956d39343367e15d02cc29e75095c70a22716cdbd24f326f5d895ac0d07d8b56`.
It is review evidence and must be regenerated after Stage A advances the state
serial.

Require `python scripts/check_b6_lbc_plans.py b <fresh-plan>` to pass. Perform
an independent IAM review of the rendered policy after the new security-group
ID is known and before applying Stage B.

The policy permits read-only regional discovery and only internal Application
Load Balancers with `medzen-b6-` names and controller-derived target groups
with the namespace-bound `k8s-medzen-` prefix, plus exact cluster/allocation
tags, the three reviewed subnets and the exact Terraform-owned ALB security
group. It has no EC2 security-group write action.

`helm_release.b6_load_balancer_controller[0]` must remain absent. CPU and GPU
capacity must remain zero. Verify no admission webhooks, controller Deployment,
Service or Ingress were created by this packet.

## Cost and rollback

Maximum incremental packet cost: `$0.20`. IAM, Pod Identity and a security
group have no hourly charge. Only small ECR storage/API scanning costs may
accrue. No ALB and no worker compute are permitted.

On a Stage-A scan refusal, retain the rejected digest and scan receipt as
evidence; do not proceed. A later separately approved cleanup may remove the
empty repository and restore the prior seven filters. On a Stage-B failure,
destroy only the four Stage-B resources, prove no ingress rules, ALBs or
controller Kubernetes objects exist, and leave both node groups at zero.

## Explicit prohibitions

- no public or internet-facing load balancer;
- no controller security-group mutation;
- no Helm installation or Kubernetes webhook creation;
- no CPU/GPU scale-up;
- no application deployment, Ingress, DNS or production SSM change;
- no model publication, registry approval, Bedrock/Fish call or PHI; and
- no security waiver.

## Approval phrase

Execution requires independent review and an owner authorization record that
binds this packet SHA-256 and the reviewed repository commit. Suggested phrase:

`Approve B6 AWS change packet 2026-005 only.`
