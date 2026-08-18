# B7-ACTIVATION-PACKET-2026-001 — CI/CD activation, owner-gated

Status: AUTHORED DARK. Terraform validated; `terraform plan` is
No-changes while `var.github_repo` carries its placeholder — nothing
can activate by accident. Activation is ONE owner-gated apply plus four
GitHub console steps.

## AWS mutations (single gated apply)

Setting `github_repo = "<owner>/medzen-platform"` materializes exactly:
1. `aws_iam_openid_connect_provider.github` — the GitHub OIDC provider
   (audience sts.amazonaws.com, pinned thumbprint).
2. `aws_iam_role.ci` (medzen-ci-role) — assumable ONLY by
   `repo:<owner>/medzen-platform:ref:refs/heads/main` (no forks, no
   other branches).
3. `aws_iam_role_policy.ci` from platform/iam/medzen-ci-role.json:
   ECR push to the five service repos; eks:DescribeCluster; terraform
   state bucket rw; the NON-production registry alias parameter only —
   with explicit Denies on eval/approved writes, the production serving
   alias, Secrets Manager and Bedrock (the trainer-role posture).
4. EKS access entry + AmazonEKSEditPolicy scoped to the `medzen`
   NAMESPACE only — CI can roll deployments, never touch the cluster.

Review note: the former read-only OIDC data source was removed — it
would have raced the managed resource at first activation apply.

## GitHub console steps (owner)

A. Repository variable `MEDZEN_CI_ROLE_ARN` = the role ARN from the
   apply output — the single value that turns the dark jobs on.
B. `infrastructure` environment with the owner as required reviewer
   (gates terraform apply AND the RAG alias flip).
C. Branch protection on `main`; CODEOWNERS with the clinical owner on
   `platform/testdata/rag-index/**`, require code-owner review.
D. After activation: ONE real alias-restore drill (B8) before any
   production traffic, receipts committed.

## What stays fail-closed after activation

The two placeholder jobs (model-pipeline `open-registry-pr`,
content-rag `alias-flip`) each need their own reviewed change, exactly
as the checklist records; activation does not loosen them.
