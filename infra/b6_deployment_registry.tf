# Additive B6.5C publication permission. The historical B6.5A publisher policy
# remains byte-identical and keeps all of its Deny statements. This supplemental
# policy grants only the dependent tag action with the new exact allocation.

data "aws_iam_policy_document" "registry_publisher_b6_5c_tags" {
  statement {
    sid       = "TagOnlyB65CDeploymentRegistryParameters"
    effect    = "Allow"
    actions   = ["ssm:AddTagsToResource"]
    resources = [local.registry_parameter_arn]
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values = [
        "Project",
        "Environment",
        "CostCenter",
        "Stage",
        "Workstream",
        "BudgetRegistry",
      ]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Project"
      values   = ["medzen-speech"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Environment"
      values   = ["dev"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/CostCenter"
      values   = ["speech-platform"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Stage"
      values   = ["B6.5C"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/Workstream"
      values   = ["ssm-deployment-registry"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/BudgetRegistry"
      values   = ["COST-REGISTRY-2026-003"]
    }
  }
}

resource "aws_iam_role_policy" "registry_publisher_b6_5c_tags" {
  name   = "medzen-registry-publisher-b6-5c-tags"
  role   = aws_iam_role.registry_publisher.id
  policy = data.aws_iam_policy_document.registry_publisher_b6_5c_tags.json
}
