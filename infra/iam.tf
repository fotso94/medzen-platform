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

# ---- GitHub Actions OIDC ---------------------------------------------------
data "aws_iam_openid_connect_provider" "github" {
  count = var.github_repo == "REPLACE/medzen-platform" ? 0 : 1
  url   = "https://token.actions.githubusercontent.com"
}

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
