# Codex round 34 finding 5: the nomination live mint must be authorized by the
# owner-approved protected GitHub environment + a DEDICATED scoped read role —
# never by a committed token. This is that role: it can read EXACTLY the 18
# pinned identity manifests (policy file carries REAL Deny statements for
# everything else — writes, lists, deletes, reads outside the pins, KMS beyond
# Decrypt), and its trust admits ONLY the owner-approved `arm2-nomination-mint`
# environment through the pinned exec workflow on master. Dark until the owner
# sets arm2_nomination_mint_enabled=true AFTER the live-mint packet
# (B5-UNIVERSAL-ARM2-NOMINATION-LIVE-MINT-PACKET-2026-001) passes review and
# scripts/verify_protected_environments.py confirms the environment enforces
# the owner as required reviewer.
variable "arm2_nomination_mint_enabled" {
  description = "owner switch: create the dedicated Arm-2 nomination-mint read-only OIDC role"
  type        = bool
  default     = false
}

data "aws_iam_openid_connect_provider" "github_for_nomination_mint" {
  count = var.arm2_nomination_mint_enabled ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

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
      # owner-approved environment (required reviewer: the owner) — verified
      # via verify_protected_environments BEFORE activation, because GitHub
      # silently creates referenced-but-missing environments unprotected
      values = ["repo:${var.github_repo_immutable}:environment:arm2-nomination-mint"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = ["${var.github_repo}/.github/workflows/arm2-nomination-mint-exec.yml@refs/heads/master"]
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

# Codex round 35 finding 5: the training-identity-index PRODUCER needs its OWN
# role — the mint role reads only the 7 pinned eval objects and can never read
# curated/*, while this role reads ONLY curated/* (content-hash-verified
# against the committed adoption records) and is EXPLICITLY DENIED every
# eval/* read, so it can never touch an eval or sealed object. Same protected
# environment + exec workflow; same dark-until-enabled switch.
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
      values   = ["repo:${var.github_repo_immutable}:environment:arm2-nomination-mint"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      values   = ["${var.github_repo}/.github/workflows/arm2-nomination-mint-exec.yml@refs/heads/master"]
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
