# Codex stage-1 review (2026-08-25) finding 1 item 2: the six 2000-step Arm-2
# comparative arms are CAMPAIGN jobs (tier by job class, not price) and need a
# dedicated launch role that can create ONLY those six exact jobs, tagged
# medzen-tier=arm, through the owner-approved `arm2-stage1-launch` environment
# and the reusable arm2-stage1-launch-exec.yml (job_workflow_ref-bound).
# SERIAL campaign reservation + the launcher's full above-tier chain apply.
# Dark until the owner sets arm2_stage1_launch_enabled=true (at launch
# authorization time, after the conditional second review).
variable "arm2_stage1_launch_enabled" {
  description = "owner switch: create the dedicated Arm-2 stage-1 campaign launch OIDC role"
  type        = bool
  default     = false
}

data "aws_iam_openid_connect_provider" "github_for_arm2_stage1" {
  count = var.arm2_stage1_launch_enabled ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "arm2_stage1_trust" {
  count = var.arm2_stage1_launch_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github_for_arm2_stage1[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo_immutable}:environment:arm2-stage1-launch"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = ["${var.github_repo}/.github/workflows/arm2-stage1-launch-exec.yml@refs/heads/master"]
    }
  }
}

resource "aws_iam_role" "arm2_stage1_launch" {
  count              = var.arm2_stage1_launch_enabled ? 1 : 0
  name               = "medzen-arm2-stage1-launch-role"
  assume_role_policy = data.aws_iam_policy_document.arm2_stage1_trust[0].json
  lifecycle {
    precondition {
      condition = (var.github_repo == "fotso94/medzen-platform"
      && var.github_repo_immutable == "fotso94@16901658/medzen-platform@1322233937")
      error_message = "arm2-stage1 activation requires the exact github_repo AND github_repo_immutable (both are bound into the OIDC trust)"
    }
  }
}

resource "aws_iam_role_policy" "arm2_stage1_launch" {
  count  = var.arm2_stage1_launch_enabled ? 1 : 0
  name   = "medzen-arm2-stage1-launch-access"
  role   = aws_iam_role.arm2_stage1_launch[0].id
  policy = file("${local.iam_dir}/medzen-arm2-stage1-launch-role.json")
}

output "arm2_stage1_launch_role_arn" {
  value = var.arm2_stage1_launch_enabled ? aws_iam_role.arm2_stage1_launch[0].arn : null
}
