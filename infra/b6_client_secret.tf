# Additive B6.6 client-auth boundary. Historical services.yaml, generated IAM
# and secrets.tf are hash-bound evidence and remain byte-identical. Packet
# 2026-006 must be independently approved before any resource here is applied.

resource "aws_secretsmanager_secret" "b6_client_keys" {
  count = var.enable_b6_client_keys ? 1 : 0

  name                    = "medzen/client-api-keys"
  description             = "B6.6 synthetic integration-only client API key hashes"
  kms_key_id              = aws_kms_key.data.arn
  recovery_window_in_days = 7

  tags = {
    Project        = "medzen-speech"
    Environment    = var.environment
    CostCenter     = "speech-platform"
    Stage          = "B6.6"
    Workstream     = "integration-window-auth"
    BudgetRegistry = "COST-REGISTRY-2026-003"
    Classification = "SYNTHETIC_TEST_ONLY"
  }
}

data "aws_iam_policy_document" "b6_client_keys" {
  statement {
    sid       = "AllowOnlyOrchestratorRead"
    effect    = "Allow"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.b6_client_keys[0].arn]
    principals {
      type        = "AWS"
      identifiers = [aws_iam_role.pod["speech-orchestrator"].arn]
    }
  }

  statement {
    sid       = "DenyEveryOtherPrincipalRead"
    effect    = "Deny"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.b6_client_keys[0].arn]
    principals {
      type        = "AWS"
      identifiers = ["*"]
    }
    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = [aws_iam_role.pod["speech-orchestrator"].arn]
    }
  }
}

resource "aws_secretsmanager_secret_policy" "b6_client_keys" {
  count = var.enable_b6_client_keys ? 1 : 0

  secret_arn          = aws_secretsmanager_secret.b6_client_keys[0].arn
  policy              = data.aws_iam_policy_document.b6_client_keys.json
  block_public_policy = true
}

data "aws_iam_policy_document" "b6_client_keys_kms" {
  statement {
    sid       = "DescribeExistingB6ClientKeyKmsKey"
    effect    = "Allow"
    actions   = ["kms:DescribeKey"]
    resources = [aws_kms_key.data.arn]
  }

  statement {
    sid       = "DecryptOnlyB6ClientKeyViaSecretsManager"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [aws_kms_key.data.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["secretsmanager.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:SecretARN"
      values = [
        "arn:${data.aws_partition.current.partition}:secretsmanager:${var.region}:${var.account_id}:secret:medzen/client-api-keys*"
      ]
    }
  }
}

resource "aws_iam_role_policy" "b6_client_keys_kms" {
  count = var.enable_b6_client_keys ? 1 : 0

  name   = "medzen-orch-b6-client-secret-kms"
  role   = aws_iam_role.pod["speech-orchestrator"].id
  policy = data.aws_iam_policy_document.b6_client_keys_kms.json
}

# Packet 2026-006 created these resources before the lifecycle gate existed.
# This explicit state-address migration is a no-op while the gate is true and
# permits B6.6 cleanup to schedule recoverable deletion without manual state
# surgery.
moved {
  from = aws_secretsmanager_secret.b6_client_keys
  to   = aws_secretsmanager_secret.b6_client_keys[0]
}

moved {
  from = aws_secretsmanager_secret_policy.b6_client_keys
  to   = aws_secretsmanager_secret_policy.b6_client_keys[0]
}

moved {
  from = aws_iam_role_policy.b6_client_keys_kms
  to   = aws_iam_role_policy.b6_client_keys_kms[0]
}
