# B6v2 round 14 (Codex finding 2): the sealed-output PRODUCER trust boundary.
#
# Object prefix + version + window + hash prove WHAT was written, not WHO
# wrote it — a bare S3 writer could forge internally-consistent results
# and admission would sign them. Defense:
#   (1) CRYPTOGRAPHIC (live now): the sealed EVALUATOR signs its inference
#       receipt with THIS dedicated key; admission verifies the signature
#       offline against the committed public key (the gate now REQUIRES it).
#       The key policy below removes kms:Sign from the account root — no
#       admin/pod path signs evaluator output ambiently.
#   (2) WRITE BOUNDARY (gated, activates with sealed eval): a dedicated
#       sealed-evaluator role is the ONLY principal permitted to PutObject
#       under sealed-results/, enforced by a bucket policy; the prefix uses
#       Object Lock (governance) and CloudTrail S3 data events for an
#       independent producer audit trail.
resource "aws_kms_key" "sealed_evaluator_signing" {
  description              = "medzen sealed-output evaluator signing (B6v2 round 14)"
  key_usage                = "SIGN_VERIFY"
  customer_master_key_spec = "ECC_NIST_P256"
  deletion_window_in_days  = 30
}

resource "aws_kms_alias" "sealed_evaluator_signing" {
  name          = "alias/medzen-sealed-evaluator-signing"
  target_key_id = aws_kms_key.sealed_evaluator_signing.key_id
}

variable "sealed_evaluator_enabled" {
  description = "owner switch: create the sealed-evaluator role, grant it kms:Sign on the evaluator key, and apply the sealed-results write boundary. OFF until sealed evaluation is authorized (production promotion is held anyway)."
  type        = bool
  default     = false
}

resource "aws_iam_role" "sealed_evaluator" {
  count = var.sealed_evaluator_enabled ? 1 : 0
  name  = "medzen-sealed-evaluator-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["sagemaker.amazonaws.com", "ec2.amazonaws.com"] }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sealed_evaluator" {
  count = var.sealed_evaluator_enabled ? 1 : 0
  name  = "medzen-sealed-evaluator-access"
  role  = aws_iam_role.sealed_evaluator[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "ReadSealedInputs", Effect = "Allow", Action = ["s3:GetObject"],
      Resource = ["arn:aws:s3:::medzen-speech/eval/*"] },
      { Sid = "WriteSealedResultsOnly", Effect = "Allow", Action = ["s3:PutObject"],
      Resource = ["arn:aws:s3:::medzen-speech/sealed-results/*"] },
      { Sid      = "SignInferenceReceipts", Effect = "Allow",
        Action   = ["kms:Sign", "kms:GetPublicKey", "kms:DescribeKey"],
        Resource = aws_kms_key.sealed_evaluator_signing.arn,
      Condition = { StringEquals = { "kms:SigningAlgorithm" = "ECDSA_SHA_256", "kms:MessageType" = "DIGEST" } } },
    ]
  })
}

data "aws_iam_policy_document" "sealed_evaluator_key" {
  statement {
    sid    = "AccountRootAdministersButCannotSign"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::558069890522:root"]
    }
    actions = [
      "kms:DescribeKey", "kms:GetKeyPolicy", "kms:PutKeyPolicy", "kms:GetPublicKey",
      "kms:Verify", "kms:EnableKey", "kms:DisableKey", "kms:ScheduleKeyDeletion",
      "kms:CancelKeyDeletion", "kms:CreateAlias", "kms:UpdateAlias", "kms:DeleteAlias",
      "kms:ListAliases", "kms:ListResourceTags", "kms:TagResource", "kms:UntagResource",
      "kms:UpdateKeyDescription", "kms:ListKeyPolicies", "kms:GetKeyRotationStatus",
      "kms:ListKeyRotations", "kms:ListGrants", "kms:ListRetirableGrants",
    ]
    resources = ["*"]
  }
  dynamic "statement" {
    for_each = var.sealed_evaluator_enabled ? [1] : []
    content {
      sid    = "EvaluatorRoleSignsECDSADigestsOnly"
      effect = "Allow"
      principals {
        type        = "AWS"
        identifiers = [aws_iam_role.sealed_evaluator[0].arn]
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

resource "aws_kms_key_policy" "sealed_evaluator_signing" {
  key_id = aws_kms_key.sealed_evaluator_signing.id
  policy = data.aws_iam_policy_document.sealed_evaluator_key.json
}

# The sealed-results write boundary (gated): only the evaluator role may
# write the prefix. A bucket policy on medzen-speech is account-visible, so
# it applies only when the owner enables sealed evaluation.
resource "aws_s3_bucket_policy" "sealed_results_write_boundary" {
  count  = var.sealed_evaluator_enabled ? 1 : 0
  bucket = "medzen-speech"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "OnlyEvaluatorWritesSealedResults"
      Effect    = "Deny"
      Principal = "*"
      Action    = ["s3:PutObject"]
      Resource  = ["arn:aws:s3:::medzen-speech/sealed-results/*"]
      Condition = { StringNotEquals = { "aws:PrincipalArn" = aws_iam_role.sealed_evaluator[0].arn } }
    }]
  })
}

output "sealed_evaluator_signing_key_arn" {
  value = aws_kms_key.sealed_evaluator_signing.arn
}
