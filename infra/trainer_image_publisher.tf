# Codex review #20 F1: the Arm-2 trainer-image workflow assumed
# medzen-image-publisher-role, but that role trusts ONLY
# model-images-publish.yml and its ECR policy grants only medzen-model-loader
# + medzen-asr-runtime — so the new workflow's assume-role simulated to
# implicitDeny and could never push medzen-trainer-omniasr. This is a SEPARATE,
# dedicated, non-deployment publisher role scoped to exactly that one repo and
# bound to exactly the reusable arm2-trainer-image-publish.yml executor
# (invoked by the arm2-trainer-image.yml caller). Owner sets the repo
# variable MEDZEN_TRAINER_IMAGE_PUBLISHER_ROLE_ARN to switch it live; like the
# other publisher role it grants NO eks/deploy, so activation never un-darks
# deploys.
variable "trainer_image_publisher_enabled" {
  description = "owner switch: create the dedicated non-deploy trainer-image ECR publisher OIDC role"
  type        = bool
  default     = false
}

data "aws_iam_openid_connect_provider" "github_for_trainer_publisher" {
  count = var.trainer_image_publisher_enabled ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "trainer_image_publisher_trust" {
  count = var.trainer_image_publisher_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github_for_trainer_publisher[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      # workflow_dispatch on master only (the deliberate, owner-run image build)
      values = ["repo:${var.github_repo_immutable}:ref:refs/heads/master"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      # Codex review #21 F1: job_workflow_ref is the DOCUMENTED claim for jobs
      # running inside a REUSABLE workflow, so the credential-bearing job lives
      # in the reusable arm2-trainer-image-publish.yml (caller/reusable split,
      # the same proven structure as model-images-publish.yml) and the trust
      # binds to that file at refs/heads/master. The earlier top-level binding
      # relied on an undocumented claim shape and could have been unassumable.
      values = ["${var.github_repo}/.github/workflows/arm2-trainer-image-publish.yml@refs/heads/master"]
    }
  }
}

resource "aws_iam_role" "trainer_image_publisher" {
  count              = var.trainer_image_publisher_enabled ? 1 : 0
  name               = "medzen-trainer-image-publisher-role"
  assume_role_policy = data.aws_iam_policy_document.trainer_image_publisher_trust[0].json
  lifecycle {
    precondition {
      condition     = var.github_repo == "fotso94/medzen-platform"
      error_message = "trainer-image-publisher activation requires github_repo=fotso94/medzen-platform"
    }
  }
}

resource "aws_iam_role_policy" "trainer_image_publisher" {
  count = var.trainer_image_publisher_enabled ? 1 : 0
  name  = "medzen-trainer-image-publisher-access"
  role  = aws_iam_role.trainer_image_publisher[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "EcrAuth", Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Sid = "PushOnlyTheTrainerRepo", Effect = "Allow",
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage",
        "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:DescribeImages"],
        Resource = [
          "arn:aws:ecr:eu-central-1:558069890522:repository/medzen-trainer-omniasr",
      ] },
    ]
    # deliberately NO eks:*, NO deploy, NO other ECR repo — publish only.
  })
}

output "trainer_image_publisher_role_arn" {
  value = var.trainer_image_publisher_enabled ? aws_iam_role.trainer_image_publisher[0].arn : null
}
