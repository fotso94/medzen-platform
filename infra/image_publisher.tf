# B6v2 round 16 (Codex finding 2): a DEDICATED, non-deployment image
# publisher role. The CI publish job must build+scan+push the EXACT
# manifests through a role that can ONLY push to the two ECR repos — NOT
# the shared CI role (which also grants EKS deploy and is gated with the
# HELD deploy path). Activating image publishing therefore never un-darks
# deploys. Owner sets the repo variable MEDZEN_IMAGE_PUBLISHER_ROLE_ARN to
# switch the CI publish job live.
variable "image_publisher_enabled" {
  description = "owner switch: create the dedicated non-deploy ECR image-publisher OIDC role"
  type        = bool
  default     = false
}

data "aws_iam_openid_connect_provider" "github_for_publisher" {
  count = var.image_publisher_enabled ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "image_publisher_trust" {
  count = var.image_publisher_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github_for_publisher[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo_immutable}:ref:refs/heads/master"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = ["${var.github_repo}/.github/workflows/model-images.yml@refs/heads/master"]
    }
  }
}

resource "aws_iam_role" "image_publisher" {
  count              = var.image_publisher_enabled ? 1 : 0
  name               = "medzen-image-publisher-role"
  assume_role_policy = data.aws_iam_policy_document.image_publisher_trust[0].json
  lifecycle {
    precondition {
      condition     = var.github_repo == "fotso94/medzen-platform"
      error_message = "image-publisher activation requires github_repo=fotso94/medzen-platform"
    }
  }
}

resource "aws_iam_role_policy" "image_publisher" {
  count = var.image_publisher_enabled ? 1 : 0
  name  = "medzen-image-publisher-access"
  role  = aws_iam_role.image_publisher[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "EcrAuth", Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Sid = "PushOnlyTheTwoRepos", Effect = "Allow",
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
        "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"],
        Resource = [
          "arn:aws:ecr:eu-central-1:558069890522:repository/medzen-model-loader",
          "arn:aws:ecr:eu-central-1:558069890522:repository/medzen-asr-runtime",
      ] },
    ]
    # deliberately NO eks:*, NO kubectl access, NO deploy — publish only.
  })
}

output "image_publisher_role_arn" {
  value = var.image_publisher_enabled ? aws_iam_role.image_publisher[0].arn : null
}
