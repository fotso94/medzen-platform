# Three keys so blast radius is separated: corpus, generated audio, audit logs.
resource "aws_kms_key" "data" {
  description             = "medzen-speech: corpus, manifests, model artifacts"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}
resource "aws_kms_alias" "data" {
  name          = "alias/medzen-data"
  target_key_id = aws_kms_key.data.key_id
}

resource "aws_kms_key" "audio" {
  description             = "medzen-speech: generated + user audio (biometric)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}
resource "aws_kms_alias" "audio" {
  name          = "alias/medzen-audio"
  target_key_id = aws_kms_key.audio.key_id
}

# CloudWatch Logs encrypts log groups with this key, so the key policy must
# grant the logs service principal. Without it CreateLogGroup fails with
# AccessDeniedException — the key exists, but the service cannot use it.
data "aws_iam_policy_document" "audit_key" {
  statement {
    sid       = "EnableRootAccountAdmin"
    effect    = "Allow"
    actions   = ["kms:*"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = ["arn:${data.aws_partition.current.partition}:iam::${var.account_id}:root"]
    }
  }
  statement {
    sid    = "AllowCloudWatchLogs"
    effect = "Allow"
    actions = [
      "kms:Encrypt*", "kms:Decrypt*", "kms:ReEncrypt*",
      "kms:GenerateDataKey*", "kms:Describe*",
    ]
    resources = ["*"]
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    # Scope to this account's log groups only — not every log group in AWS.
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:${data.aws_partition.current.partition}:logs:${var.region}:${var.account_id}:log-group:*"]
    }
  }
}

resource "aws_kms_key" "audit" {
  description             = "medzen-speech: full-turn audit stream (PHI)"
  enable_key_rotation     = true
  deletion_window_in_days = 30
  policy                  = data.aws_iam_policy_document.audit_key.json
}
resource "aws_kms_alias" "audit" {
  name          = "alias/medzen-audit"
  target_key_id = aws_kms_key.audit.key_id
}
