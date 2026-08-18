# Policies are GENERATED from platform/services.yaml by platform/generate.py.
# Terraform consumes them verbatim, so the cluster cannot drift from A2.
locals {
  iam_dir = "${path.module}/../platform/iam"

  pod_roles = {
    "speech-orchestrator" = "medzen-orch-role"
    "asr-runtime"         = "medzen-asr-role"
    "llm-gateway"         = "medzen-llm-role"
    "tts-gateway"         = "medzen-tts-role"
    "rag-index"           = "medzen-rag-role"
    "model-loader"        = "medzen-loader-role"
  }
}

data "aws_iam_policy_document" "pod_identity_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole", "sts:TagSession"]
    principals {
      type        = "Service"
      identifiers = ["pods.eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "pod" {
  for_each           = local.pod_roles
  name               = each.value
  assume_role_policy = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_iam_role_policy" "pod" {
  for_each = local.pod_roles
  name     = "${each.value}-access"
  role     = aws_iam_role.pod[each.key].id
  policy   = file("${local.iam_dir}/${each.value}.json")
}

# ---- trainer (offline, EC2/SageMaker) -------------------------------------
data "aws_iam_policy_document" "trainer_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com", "sagemaker.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "trainer" {
  name               = "medzen-trainer-role"
  assume_role_policy = data.aws_iam_policy_document.trainer_trust.json
}

resource "aws_iam_role_policy" "trainer" {
  name   = "medzen-trainer-access"
  role   = aws_iam_role.trainer.id
  policy = file("${local.iam_dir}/medzen-trainer-role.json")
}

# SageMaker execution delta the B4-era trainer role lacked
# (B5-T5-CALIBRATION-PACKET-2026-001, mutation 6).
resource "aws_iam_role_policy" "trainer_b5_t5_sagemaker" {
  name   = "medzen-b5-t5-sagemaker"
  role   = aws_iam_role.trainer.id
  policy = file("${local.iam_dir}/medzen-b5-t5-sagemaker-policy.json")
}

# ---- GitHub Actions OIDC ---------------------------------------------------
# (the provider is now MANAGED below in the CI-role block — the former
# read-only data source would have raced the resource at first activation
# apply; removed by the task-G review, it had no references)

# EC2 cannot assume a role directly — it needs an instance profile. The role
# already exists with the A3 guardrails (explicit Deny on eval writes,
# secretsmanager and bedrock); this only makes it attachable to a trainer box.
resource "aws_iam_instance_profile" "trainer" {
  name = "medzen-trainer-profile"
  role = aws_iam_role.trainer.name
}

# ---- builder (offline, EC2 image build) ------------------------------------
# Separate from the trainer so a training job cannot overwrite the image it
# runs from. The dev laptop is arm64, so building linux/amd64 there would mean
# QEMU emulation of a ~10 GB CUDA image; an in-region x86 box builds natively
# and pushes to ECR over the AWS network instead of a home uplink.
resource "aws_iam_role" "builder" {
  name               = "medzen-builder-role"
  assume_role_policy = data.aws_iam_policy_document.trainer_trust.json # ec2 + sagemaker
}

resource "aws_iam_role_policy" "builder" {
  name   = "medzen-builder-access"
  role   = aws_iam_role.builder.id
  policy = file("${local.iam_dir}/medzen-builder-role.json")
}

resource "aws_iam_instance_profile" "builder" {
  name = "medzen-builder-profile"
  role = aws_iam_role.builder.name
}

# ---- CI role (B7 activation packet, task G) --------------------------------
# Dark until var.github_repo is real AND the owner applies: the OIDC provider
# and role only materialize when the placeholder is replaced. Trust is
# scoped to THIS repository's main branch — no other repo, ref or fork can
# assume it. Same explicit-Deny posture as the trainer role.
resource "aws_iam_openid_connect_provider" "github" {
  count           = var.github_repo == "REPLACE/medzen-platform" ? 0 : 1
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "ci_trust" {
  count = var.github_repo == "REPLACE/medzen-platform" ? 0 : 1
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:ref:refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "ci" {
  count              = var.github_repo == "REPLACE/medzen-platform" ? 0 : 1
  name               = "medzen-ci-role"
  assume_role_policy = data.aws_iam_policy_document.ci_trust[0].json
}

resource "aws_iam_role_policy" "ci" {
  count  = var.github_repo == "REPLACE/medzen-platform" ? 0 : 1
  name   = "medzen-ci-access"
  role   = aws_iam_role.ci[0].id
  policy = file("${local.iam_dir}/medzen-ci-role.json")
}

# Namespace-scoped Kubernetes access: the CI role may patch deployments in
# the medzen namespace only (rollout of the five services); cluster admin
# stays human. The access policy is the EKS-managed edit policy scoped by
# namespace — auditable in one place here.
resource "aws_eks_access_entry" "ci" {
  count         = var.github_repo == "REPLACE/medzen-platform" ? 0 : 1
  cluster_name  = "medzen-speech"
  principal_arn = aws_iam_role.ci[0].arn
}

resource "aws_eks_access_policy_association" "ci_medzen_edit" {
  count         = var.github_repo == "REPLACE/medzen-platform" ? 0 : 1
  cluster_name  = "medzen-speech"
  principal_arn = aws_iam_role.ci[0].arn
  policy_arn    = "arn:aws:eks::aws:cluster-access-policy/AmazonEKSEditPolicy"
  access_scope {
    type       = "namespace"
    namespaces = ["medzen"]
  }
  depends_on = [aws_eks_access_entry.ci]
}
