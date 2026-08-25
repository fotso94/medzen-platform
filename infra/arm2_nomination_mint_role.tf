# Codex round 36 finding 3: the producer and mint roles must be ISOLATED. Round
# 35 gave both roles the IDENTICAL OIDC subject (environment:arm2-nomination-
# mint) AND the identical job_workflow_ref (one exec file), so a token minted by
# either job satisfied BOTH trusts. This file gives each role its OWN protected
# environment AND its OWN reusable exec workflow, so producer and mint tokens
# differ in TWO independent claims and neither trust is satisfiable by the
# other's token. Both roles are dark until the owner sets
# arm2_nomination_mint_enabled=true AFTER the live-mint packet
# (B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001) passes review and
# scripts/verify_protected_environments.py confirms BOTH environments enforce
# the owner as required reviewer.
variable "arm2_nomination_mint_enabled" {
  description = "owner switch: create the dedicated Arm-2 nomination-mint + training-index OIDC roles"
  type        = bool
  default     = false
}

data "aws_iam_openid_connect_provider" "github_for_nomination_mint" {
  count = var.arm2_nomination_mint_enabled ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

# ---- MINT role: reads the 7 pinned nomination/candidate identity manifests ---
data "aws_iam_policy_document" "arm2_nomination_mint_trust" {
  count = var.arm2_nomination_mint_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github_for_nomination_mint[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      # distinct environment for the mint role (finding 3)
      values = ["repo:${var.github_repo_immutable}:environment:arm2-nomination-mint-mint"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      # distinct exec workflow for the mint role (finding 3)
      values = ["${var.github_repo}/.github/workflows/arm2-nomination-mint-mint-exec.yml@refs/heads/master"]
    }
  }
}

resource "aws_iam_role" "arm2_nomination_mint" {
  count              = var.arm2_nomination_mint_enabled ? 1 : 0
  name               = "medzen-arm2-nomination-mint-role"
  assume_role_policy = data.aws_iam_policy_document.arm2_nomination_mint_trust[0].json
  lifecycle {
    precondition {
      condition = (var.github_repo == "fotso94/medzen-platform"
      && var.github_repo_immutable == "fotso94@16901658/medzen-platform@1322233937")
      error_message = "nomination-mint activation requires the exact github_repo AND github_repo_immutable (both are bound into the OIDC trust)"
    }
  }
}

resource "aws_iam_role_policy" "arm2_nomination_mint" {
  count  = var.arm2_nomination_mint_enabled ? 1 : 0
  name   = "medzen-arm2-nomination-mint-access"
  role   = aws_iam_role.arm2_nomination_mint[0].id
  policy = file("${local.iam_dir}/medzen-arm2-nomination-mint-role.json")
}

output "arm2_nomination_mint_role_arn" {
  value = var.arm2_nomination_mint_enabled ? aws_iam_role.arm2_nomination_mint[0].arn : null
}

# ---- PRODUCER role: reads curated/* only; eval/* explicitly denied ----------
data "aws_iam_policy_document" "arm2_training_index_trust" {
  count = var.arm2_nomination_mint_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github_for_nomination_mint[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      # distinct environment for the producer role (finding 3)
      values = ["repo:${var.github_repo_immutable}:environment:arm2-nomination-mint-producer"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      # distinct exec workflow for the producer role (finding 3)
      values = ["${var.github_repo}/.github/workflows/arm2-nomination-mint-producer-exec.yml@refs/heads/master"]
    }
  }
}

resource "aws_iam_role" "arm2_training_index" {
  count              = var.arm2_nomination_mint_enabled ? 1 : 0
  name               = "medzen-arm2-training-index-role"
  assume_role_policy = data.aws_iam_policy_document.arm2_training_index_trust[0].json
  lifecycle {
    precondition {
      condition = (var.github_repo == "fotso94/medzen-platform"
      && var.github_repo_immutable == "fotso94@16901658/medzen-platform@1322233937")
      error_message = "training-index activation requires the exact github_repo AND github_repo_immutable (both are bound into the OIDC trust)"
    }
  }
}

resource "aws_iam_role_policy" "arm2_training_index" {
  count  = var.arm2_nomination_mint_enabled ? 1 : 0
  name   = "medzen-arm2-training-index-access"
  role   = aws_iam_role.arm2_training_index[0].id
  policy = file("${local.iam_dir}/medzen-arm2-training-index-role.json")
}

output "arm2_training_index_role_arn" {
  value = var.arm2_nomination_mint_enabled ? aws_iam_role.arm2_training_index[0].arn : null
}
