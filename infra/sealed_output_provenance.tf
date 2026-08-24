# B6v2 rounds 15-16 (Codex findings 1): the sealed-output PRODUCER boundary.
#
# The promotion contract REQUIRES SageMaker network isolation, so the
# evaluator container has NO credentials to reach KMS — it does not sign.
# Producer authentication is established at ADMISSION (which HAS creds):
#   - the isolated job writes outputs under its DEDICATED execution role
#     into a dedicated Object-Lock, KMS-encrypted, no-public-access bucket;
#   - a bucket policy makes that execution role the ONLY writer;
#   - a standard CloudTrail trail (round 16: NOT Lake, which is closed to
#     new customers after 2026-05-31) records S3 DATA EVENTS to a
#     CloudWatch Logs group;
#   - admission verifies, before KMS-signing the evidence root, that every
#     output object was written by the EXACT execution role IN THE EXACT
#     account (CloudWatch Logs Insights) and carries Object-Lock retention.
# All gated behind sealed_evaluator_enabled (sealed evaluation is HELD).
variable "sealed_evaluator_enabled" {
  description = "owner switch: create the sealed-output provenance boundary. OFF until sealed evaluation is authorized."
  type        = bool
  default     = false
}

locals {
  medzen_kms_key_arn = "arn:aws:kms:eu-central-1:558069890522:key/9c336116-c648-4548-95c6-1b926478ae57"
}

resource "aws_s3_bucket" "sealed_results" {
  count               = var.sealed_evaluator_enabled ? 1 : 0
  bucket              = "medzen-sealed-results"
  object_lock_enabled = true
}

resource "aws_s3_bucket_versioning" "sealed_results" {
  count  = var.sealed_evaluator_enabled ? 1 : 0
  bucket = aws_s3_bucket.sealed_results[0].id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_object_lock_configuration" "sealed_results" {
  count  = var.sealed_evaluator_enabled ? 1 : 0
  bucket = aws_s3_bucket.sealed_results[0].id
  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 3650
    }
  }
}

# Codex #16: KMS-at-rest enforcement + no public access were missing.
resource "aws_s3_bucket_server_side_encryption_configuration" "sealed_results" {
  count  = var.sealed_evaluator_enabled ? 1 : 0
  bucket = aws_s3_bucket.sealed_results[0].id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = local.medzen_kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "sealed_results" {
  count                   = var.sealed_evaluator_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.sealed_results[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_iam_role" "sealed_evaluator" {
  count = var.sealed_evaluator_enabled ? 1 : 0
  name  = "medzen-sealed-evaluator-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = ["sagemaker.amazonaws.com"] }
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
      { Sid    = "WriteSealedResultsWithRetention", Effect = "Allow",
        Action = ["s3:PutObject", "s3:PutObjectRetention"],
      Resource = ["${aws_s3_bucket.sealed_results[0].arn}/*"] },
      { Sid    = "UseTheDataKey", Effect = "Allow",
        Action = ["kms:GenerateDataKey", "kms:Decrypt"],
      Resource = [local.medzen_kms_key_arn] },
    ]
    # deliberately NO kms:Sign — the isolated evaluator cannot reach KMS.
  })
}

resource "aws_s3_bucket_policy" "sealed_results" {
  count  = var.sealed_evaluator_enabled ? 1 : 0
  bucket = aws_s3_bucket.sealed_results[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid    = "OnlyEvaluatorWrites", Effect = "Deny", Principal = "*",
        Action = ["s3:PutObject"], Resource = ["${aws_s3_bucket.sealed_results[0].arn}/*"],
      Condition = { StringNotEquals = { "aws:PrincipalArn" = aws_iam_role.sealed_evaluator[0].arn } } },
      { Sid      = "NoOneBypassesRetention", Effect = "Deny", Principal = "*",
        Action   = ["s3:PutObjectRetention", "s3:BypassGovernanceRetention"],
        Resource = ["${aws_s3_bucket.sealed_results[0].arn}/*"],
      Condition = { StringNotEquals = { "aws:PrincipalArn" = aws_iam_role.sealed_evaluator[0].arn } } },
      { Sid      = "TLSOnly", Effect = "Deny", Principal = "*", Action = ["s3:*"],
        Resource = ["${aws_s3_bucket.sealed_results[0].arn}", "${aws_s3_bucket.sealed_results[0].arn}/*"],
      Condition = { Bool = { "aws:SecureTransport" = "false" } } },
      # Codex review #17: ENFORCE the exact KMS key at write time.
      { Sid      = "RequireKmsEncryption", Effect = "Deny", Principal = "*", Action = ["s3:PutObject"],
        Resource = ["${aws_s3_bucket.sealed_results[0].arn}/*"],
      Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption" = "aws:kms" } } },
      { Sid      = "RequireExactKmsKey", Effect = "Deny", Principal = "*", Action = ["s3:PutObject"],
        Resource = ["${aws_s3_bucket.sealed_results[0].arn}/*"],
      Condition = { StringNotEquals = { "s3:x-amz-server-side-encryption-aws-kms-key-id" = local.medzen_kms_key_arn } } },
    ]
  })
}

# --- standard CloudTrail (NOT Lake): S3 data events -> CloudWatch Logs ---
resource "aws_s3_bucket" "sealed_trail_logs" {
  count         = var.sealed_evaluator_enabled ? 1 : 0
  bucket        = "medzen-sealed-results-trail-logs"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "sealed_trail_logs" {
  count                   = var.sealed_evaluator_enabled ? 1 : 0
  bucket                  = aws_s3_bucket.sealed_trail_logs[0].id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

data "aws_iam_policy_document" "sealed_trail_bucket" {
  count = var.sealed_evaluator_enabled ? 1 : 0
  statement {
    sid       = "AWSCloudTrailAclCheck"
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.sealed_trail_logs[0].arn]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
  }
  statement {
    sid       = "AWSCloudTrailWrite"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.sealed_trail_logs[0].arn}/AWSLogs/558069890522/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
  }
}

resource "aws_s3_bucket_policy" "sealed_trail_logs" {
  count  = var.sealed_evaluator_enabled ? 1 : 0
  bucket = aws_s3_bucket.sealed_trail_logs[0].id
  policy = data.aws_iam_policy_document.sealed_trail_bucket[0].json
}

resource "aws_cloudwatch_log_group" "sealed_trail" {
  count             = var.sealed_evaluator_enabled ? 1 : 0
  name              = "/medzen/sealed-results/cloudtrail"
  retention_in_days = 3653
}

resource "aws_iam_role" "sealed_trail_to_logs" {
  count = var.sealed_evaluator_enabled ? 1 : 0
  name  = "medzen-sealed-trail-to-logs"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "cloudtrail.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "sealed_trail_to_logs" {
  count = var.sealed_evaluator_enabled ? 1 : 0
  name  = "deliver-to-logs"
  role  = aws_iam_role.sealed_trail_to_logs[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = ["${aws_cloudwatch_log_group.sealed_trail[0].arn}:*"]
    }]
  })
}

resource "aws_cloudtrail" "sealed_results" {
  count                         = var.sealed_evaluator_enabled ? 1 : 0
  name                          = "medzen-sealed-results-data-events"
  s3_bucket_name                = aws_s3_bucket.sealed_trail_logs[0].id
  cloud_watch_logs_group_arn    = "${aws_cloudwatch_log_group.sealed_trail[0].arn}:*"
  cloud_watch_logs_role_arn     = aws_iam_role.sealed_trail_to_logs[0].arn
  include_global_service_events = false
  advanced_event_selector {
    name = "sealed-results S3 data events"
    field_selector {
      field  = "eventCategory"
      equals = ["Data"]
    }
    field_selector {
      field  = "resources.type"
      equals = ["AWS::S3::Object"]
    }
    field_selector {
      field       = "resources.ARN"
      starts_with = ["${aws_s3_bucket.sealed_results[0].arn}/"]
    }
  }
  depends_on = [aws_s3_bucket_policy.sealed_trail_logs]
}

output "sealed_results_bucket" {
  value = var.sealed_evaluator_enabled ? aws_s3_bucket.sealed_results[0].bucket : null
}
output "sealed_results_log_group" {
  value = var.sealed_evaluator_enabled ? aws_cloudwatch_log_group.sealed_trail[0].name : null
}
