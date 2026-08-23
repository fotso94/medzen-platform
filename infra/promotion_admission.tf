# B6v2 round 13 (Codex): the promotion-admission TRUST BOUNDARY.
#
# Round 12 left kms:Sign reachable by any account administrator (the key
# carried the default account-delegating policy) and declared no
# dedicated principal. Now:
#   - a DEDICATED OIDC role, assumable ONLY by the model-pipeline.yml
#     workflow running in the GitHub `promotion-admission` environment
#     (same environment + job_workflow_ref pattern as arm-launch);
#   - the signing key's policy grants kms:Sign to THAT role and nothing
#     else. The account root keeps ADMINISTRATION of the key (an explicit
#     PutKeyPolicy is a logged, deliberate change) but NOT kms:Sign —
#     AdministratorAccess no longer signs promotion evidence ambiently.
#   - the signing algorithm condition is ECDSA_SHA_256 (the key is
#     ECC_NIST_P256; the old template's RSASSA_PSS condition would have
#     blocked every legitimate signature).
variable "promotion_admission_enabled" {
  description = "owner switch: create the promotion-admission role and bind the signing-key policy"
  type        = bool
  default     = false
}

data "aws_iam_openid_connect_provider" "github_for_admission" {
  count = var.promotion_admission_enabled ? 1 : 0
  url   = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "promotion_admission_trust" {
  count = var.promotion_admission_enabled ? 1 : 0
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github_for_admission[0].arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo_immutable}:environment:promotion-admission"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:job_workflow_ref"
      # EMPIRICAL (arm-launch canaries, live role): sub is IMMUTABLE but
      # job_workflow_ref is the PLAIN name form
      values = ["${var.github_repo}/.github/workflows/model-pipeline.yml@refs/heads/master"]
    }
  }
}

resource "aws_iam_role" "promotion_admission" {
  count              = var.promotion_admission_enabled ? 1 : 0
  name               = "medzen-promotion-admission-role"
  assume_role_policy = data.aws_iam_policy_document.promotion_admission_trust[0].json

  lifecycle {
    precondition {
      condition     = var.github_repo == "fotso94/medzen-platform" && var.github_repo_immutable == "fotso94@16901658/medzen-platform@1322233937"
      error_message = "admission activation requires github_repo=fotso94/medzen-platform AND the verified immutable identity (a wrong value mints an unusable role)."
    }
  }
}

resource "aws_iam_role_policy" "promotion_admission" {
  count  = var.promotion_admission_enabled ? 1 : 0
  name   = "medzen-promotion-admission-access"
  role   = aws_iam_role.promotion_admission[0].id
  policy = file("${local.iam_dir}/medzen-promotion-admission-role.json")
}

data "aws_iam_policy_document" "promotion_signing_key" {
  # ADMINISTRATION only for the account root — deliberately NO kms:Sign
  statement {
    sid    = "AccountRootAdministersButCannotSign"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::558069890522:root"]
    }
    actions = [
      "kms:DescribeKey", "kms:GetKeyPolicy", "kms:PutKeyPolicy",
      "kms:GetPublicKey", "kms:Verify", "kms:EnableKey", "kms:DisableKey",
      "kms:ScheduleKeyDeletion", "kms:CancelKeyDeletion", "kms:CreateAlias",
      "kms:UpdateAlias", "kms:DeleteAlias", "kms:ListAliases",
      "kms:ListResourceTags", "kms:TagResource", "kms:UntagResource",
      "kms:UpdateKeyDescription", "kms:ListKeyPolicies",
      # read-only state Terraform refreshes on the key resource itself
      "kms:GetKeyRotationStatus", "kms:ListKeyRotations", "kms:ListGrants",
      "kms:ListRetirableGrants",
    ]
    resources = ["*"]
  }
  dynamic "statement" {
    for_each = var.promotion_admission_enabled ? [1] : []
    content {
      sid    = "AdmissionRoleSignsECDSADigestsOnly"
      effect = "Allow"
      principals {
        type        = "AWS"
        identifiers = [aws_iam_role.promotion_admission[0].arn]
      }
      actions   = ["kms:Sign", "kms:GetPublicKey", "kms:DescribeKey"]
      resources = ["*"]
      condition {
        test     = "StringEquals"
        variable = "kms:SigningAlgorithm"
        values   = ["ECDSA_SHA_256"]
      }
      condition {
        test     = "StringEquals"
        variable = "kms:MessageType"
        values   = ["DIGEST"]
      }
    }
  }
}

resource "aws_kms_key_policy" "promotion_signing" {
  key_id = aws_kms_key.promotion_signing.id
  policy = data.aws_iam_policy_document.promotion_signing_key.json
}

output "promotion_admission_role_arn" {
  value = var.promotion_admission_enabled ? aws_iam_role.promotion_admission[0].arn : null
}
