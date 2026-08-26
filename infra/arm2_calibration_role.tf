# Codex review #26 finding 6: the Arm-2 calibration needs its OWN launch role.
# The existing medzen-arm-launch-role only authorizes the historical Arm-1 job
# (and is arm-tier), so it cannot create the Arm-2 calibration job. This is a
# dedicated, calibration-scoped role that can create ONLY
# medzen-b5-b5-universal-arm2-ftcal-2026-001, tagged medzen-tier=calibration,
# under the same hard conditions (instance/subnet/SG/KMS/runtime) as the arm
# role, carries the NoRemoteDebugEver deny (Codex #24), reads ONLY the exact
# calibration artifact prefix, and passes ONLY the trainer role. Trust binds to
# the owner-approved `arm2-calibration` environment + the reusable launch exec.
# Dark until the owner sets arm2_calibration_enabled=true.
variable "arm2_calibration_enabled" {
  description = "owner switch: create the dedicated Arm-2 calibration launch OIDC role"
  type        = bool
  default     = true # LIVE since activation — default reflects reality (Codex final correction item 5: a bare plan must be zero-destroy)
}

data "aws_iam_openid_connect_provider" "github_for_arm2_calibration" {
  count = var.arm2_calibration_enabled ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "arm2_calibration_trust" {
  count = var.arm2_calibration_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github_for_arm2_calibration[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      # owner-approved environment (required reviewer: the owner) — the same
      # protected pattern as arm-launch-approval, so a human gates every launch
      values = ["repo:${var.github_repo_immutable}:environment:arm2-calibration"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = ["${var.github_repo}/.github/workflows/arm2-calibration-launch-exec.yml@refs/heads/master"]
    }
  }
}

resource "aws_iam_role" "arm2_calibration" {
  count              = var.arm2_calibration_enabled ? 1 : 0
  name               = "medzen-arm2-calibration-role"
  assume_role_policy = data.aws_iam_policy_document.arm2_calibration_trust[0].json
  lifecycle {
    precondition {
      condition = (var.github_repo == "fotso94/medzen-platform"
      && var.github_repo_immutable == "fotso94@16901658/medzen-platform@1322233937")
      error_message = "arm2-calibration activation requires the exact github_repo AND github_repo_immutable (both are bound into the OIDC trust)"
    }
  }
}

resource "aws_iam_role_policy" "arm2_calibration" {
  count  = var.arm2_calibration_enabled ? 1 : 0
  name   = "medzen-arm2-calibration-access"
  role   = aws_iam_role.arm2_calibration[0].id
  policy = file("${local.iam_dir}/medzen-arm2-calibration-role.json")
}

output "arm2_calibration_role_arn" {
  value = var.arm2_calibration_enabled ? aws_iam_role.arm2_calibration[0].arn : null
}
