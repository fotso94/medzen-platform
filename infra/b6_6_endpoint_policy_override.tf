# Prospective B6 successor correction. Historical packet-2026-016 sources stay
# byte-identical; Terraform applies this override after the original blocks.
#
# AWS requires Principal in every custom VPC endpoint policy. Principal="*"
# removes the temporary-role propagation dependency while the action/resource
# boundary remains narrow. The temporary endpoint SG is also attached to the
# probe task at RunTask time, so its self-reference admits TLS from that exact
# temporary probe only. The separately attached backend SG still proves access
# to the internal ALB through the real MedZen backend network identity.

data "aws_iam_policy_document" "b6_probe_ecr_api_endpoint" {
  statement {
    sid       = "ProbeNetworkRegistryToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

data "aws_iam_policy_document" "b6_probe_ecr_dkr_endpoint" {
  statement {
    sid    = "ProbeNetworkQualifiedImagePull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [local.b6_probe_repository_arn]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_vpc_security_group_ingress_rule" "b6_probe_to_endpoints" {
  # A security-group self-reference is deliberate: the endpoint SG is also
  # attached to the one temporary Fargate probe by the reviewed runtime.
  referenced_security_group_id = aws_security_group.b6_probe_endpoints[0].id
}
