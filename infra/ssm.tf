# B1 registry publication boundary.
#
# Parameter Store has no namespace resource to create. This file creates the
# only writer identity for /medzen/registry/* and binds SecureString operations
# to the existing data KMS key. Parameters are created later by the reviewed
# publisher; Terraform must not own mutable serving pointers.

locals {
  registry_parameter_prefix = "/medzen/registry"
  registry_parameter_arn    = "arn:${data.aws_partition.current.partition}:ssm:${var.region}:${var.account_id}:parameter${local.registry_parameter_prefix}/*"
}

data "aws_iam_policy_document" "registry_publisher_trust" {
  statement {
    sid     = "ExactOwnerApprovedPrincipal"
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [var.registry_publisher_principal_arn]
    }
  }
}

resource "aws_iam_role" "registry_publisher" {
  name                 = "medzen-registry-publisher-role"
  assume_role_policy   = data.aws_iam_policy_document.registry_publisher_trust.json
  max_session_duration = 3600

  lifecycle {
    precondition {
      condition = startswith(
        var.registry_publisher_principal_arn,
        "arn:${data.aws_partition.current.partition}:iam::${var.account_id}:",
      )
      error_message = "The registry publisher assumer must be in account_id."
    }
  }
}

data "aws_iam_policy_document" "registry_publisher" {
  statement {
    sid    = "ReadAndWriteExactRegistryPrefix"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:GetParametersByPath",
      "ssm:PutParameter",
    ]
    resources = [local.registry_parameter_arn]
  }

  # A future accidental broad attachment cannot turn this dedicated role into
  # a general Parameter Store writer.
  statement {
    sid           = "DenyWritesOutsideRegistryPrefix"
    effect        = "Deny"
    actions       = ["ssm:PutParameter"]
    not_resources = [local.registry_parameter_arn]
  }

  # Snapshot history and old serving-pointer versions are rollback evidence.
  statement {
    sid       = "DenyParameterDeletion"
    effect    = "Deny"
    actions   = ["ssm:DeleteParameter", "ssm:DeleteParameters"]
    resources = ["*"]
  }

  statement {
    sid       = "DescribeRegistryEncryptionKey"
    effect    = "Allow"
    actions   = ["kms:DescribeKey"]
    resources = [aws_kms_key.data.arn]
  }

  statement {
    sid    = "EncryptRegistrySecureStringsOnlyThroughSsm"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:Encrypt",
      "kms:GenerateDataKey",
    ]
    resources = [aws_kms_key.data.arn]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.region}.amazonaws.com"]
    }
    condition {
      test     = "StringLike"
      variable = "kms:EncryptionContext:PARAMETER_ARN"
      values   = [local.registry_parameter_arn]
    }
  }
}

resource "aws_iam_role_policy" "registry_publisher" {
  name   = "medzen-registry-publisher-access"
  role   = aws_iam_role.registry_publisher.id
  policy = data.aws_iam_policy_document.registry_publisher.json
}
