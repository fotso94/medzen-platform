# B6v2 round 15 (Codex finding 1): the sealed-output PRODUCER boundary,
# corrected. Round 14 required the isolated evaluator container to KMS-sign
# its receipt — IMPOSSIBLE: the promotion contract REQUIRES SageMaker
# network isolation, under which the container has NO credentials to reach
# KMS. The evaluator therefore does NOT sign anything. Producer
# authentication is established at ADMISSION (which HAS credentials):
#   - the isolated job writes outputs under its DEDICATED execution role
#     into a dedicated Object-Lock bucket;
#   - a bucket policy makes that execution role the ONLY writer;
#   - CloudTrail S3 DATA EVENTS record the PutObject principal;
#   - admission (the protected workflow) verifies, before KMS-signing the
#     evidence ROOT, that every output object was written by the execution
#     role and carries Object-Lock retention.
# All gated behind sealed_evaluator_enabled (sealed evaluation is HELD).
variable "sealed_evaluator_enabled" {
  description = "owner switch: create the sealed-output provenance boundary (Object-Lock bucket, execution role, write-boundary policy, CloudTrail data-event store). OFF until sealed evaluation is authorized."
  type        = bool
  default     = false
}

# Dedicated Object-Lock bucket for sealed evaluator outputs (Object Lock
# can only be enabled at creation, so it is a dedicated bucket, not the
# shared medzen-speech).
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
    ]
    # NOTE: deliberately NO kms:Sign — the isolated evaluator cannot reach
    # KMS and does not sign. Provenance is CloudTrail + Object Lock.
  })
}

# The write boundary: ONLY the execution role may write the bucket, and no
# one may weaken Object-Lock retention.
resource "aws_s3_bucket_policy" "sealed_results" {
  count  = var.sealed_evaluator_enabled ? 1 : 0
  bucket = aws_s3_bucket.sealed_results[0].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "OnlyEvaluatorWrites"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["s3:PutObject"]
        Resource  = ["${aws_s3_bucket.sealed_results[0].arn}/*"]
        Condition = { StringNotEquals = { "aws:PrincipalArn" = aws_iam_role.sealed_evaluator[0].arn } }
      },
      {
        Sid       = "NoOneBypassesRetention"
        Effect    = "Deny"
        Principal = "*"
        Action    = ["s3:PutObjectRetention", "s3:BypassGovernanceRetention"]
        Resource  = ["${aws_s3_bucket.sealed_results[0].arn}/*"]
        Condition = { StringNotEquals = { "aws:PrincipalArn" = aws_iam_role.sealed_evaluator[0].arn } }
      },
    ]
  })
}

# CloudTrail S3 DATA-EVENT store (Lake) — admission queries it for the
# PutObject principal of each sealed output object.
resource "aws_cloudtrail_event_data_store" "sealed_results" {
  count            = var.sealed_evaluator_enabled ? 1 : 0
  name             = "medzen-sealed-results-data-events"
  retention_period = 2555 # max (~7 years, days)
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
}

output "sealed_results_bucket" {
  value = var.sealed_evaluator_enabled ? aws_s3_bucket.sealed_results[0].bucket : null
}
output "sealed_results_cloudtrail_store" {
  value = var.sealed_evaluator_enabled ? aws_cloudtrail_event_data_store.sealed_results[0].arn : null
}
