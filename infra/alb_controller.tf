# B6.6 ingress boundary. This controller watches only the medzen namespace,
# cannot modify EC2 security groups and can create only internal, tagged B6
# load-balancing resources in the exact shared VPC/subnets. Packet 2026-005
# must be independently approved before any resource in this file is applied.

locals {
  lbc_version      = "3.5.0"
  lbc_child_digest = "sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f"
  lbc_private_tag  = "v3.5.0-c2ebdeae779c"
  lbc_repository   = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com/medzen-aws-load-balancer-controller"
}

resource "aws_ecr_repository" "b6_load_balancer_controller" {
  name                 = "medzen-aws-load-balancer-controller"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
}

# Packet evidence must remain retained. There is deliberately no lifecycle
# policy on this repository.

resource "aws_security_group" "b6_internal_alb" {
  name        = "medzen-b6-internal-alb"
  description = "B6.6 synthetic integration internal ALB; ingress added only by the window packet"
  vpc_id      = var.vpc_id

  # No ingress exists in the controller packet. The successor B6.6 packet may
  # add exactly one source-SG rule after reconfirming the backend SG owner.
  egress {
    description = "Only orchestrator HTTP targets inside the shared VPC"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.shared.cidr_block]
  }

  tags = {
    Name           = "medzen-b6-internal-alb"
    Stage          = "B6.6"
    Workstream     = "integration-window"
    BudgetRegistry = "COST-REGISTRY-2026-003"
  }
}

resource "aws_iam_role" "b6_load_balancer_controller" {
  name                 = "medzen-lbc-role"
  max_session_duration = 3600
  assume_role_policy   = data.aws_iam_policy_document.pod_identity_trust.json
}

resource "aws_iam_role_policy" "b6_load_balancer_controller" {
  name = "medzen-lbc-access"
  role = aws_iam_role.b6_load_balancer_controller.id
  policy = templatefile(
    "${path.module}/../platform/iam/medzen-lbc-role.policy.template.json",
    { alb_security_group_id = aws_security_group.b6_internal_alb.id },
  )
}

resource "aws_eks_pod_identity_association" "b6_load_balancer_controller" {
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "kube-system"
  service_account = "aws-load-balancer-controller"
  role_arn        = aws_iam_role.b6_load_balancer_controller.arn
  depends_on      = [aws_eks_addon.core]
}

resource "helm_release" "b6_load_balancer_controller" {
  count = var.enable_b6_load_balancer_controller ? 1 : 0

  name       = "aws-load-balancer-controller"
  repository = "https://aws.github.io/eks-charts"
  chart      = "aws-load-balancer-controller"
  version    = local.lbc_version
  namespace  = "kube-system"

  # A controller with no Ready worker leaves failure-closed admission webhooks
  # unavailable. Therefore packet 2026-005 never creates this release. B6.6
  # enables it only after deadline-first cleanup is armed and CPU nodes are
  # Ready, then disables/removes it before returning CPU capacity to zero.
  wait          = true
  atomic        = true
  wait_for_jobs = true
  max_history   = 3

  values = [file(
    "${path.module}/../platform/designs/B6-LBC-HELM-VALUES-2026-001.yaml"
  )]

  postrender = {
    binary_path = "${path.module}/../scripts/pin_aws_lbc_digest.py"
  }

  depends_on = [
    aws_ecr_repository.b6_load_balancer_controller,
    aws_eks_pod_identity_association.b6_load_balancer_controller,
  ]

  lifecycle {
    precondition {
      condition     = local.lbc_child_digest == "sha256:c2ebdeae779c796e3d071d7a0d3a4ebdbb31e4e8d53e3e5372ee0ab0c4f3f08f"
      error_message = "AWS LBC deploy digest differs from the locally qualified child manifest."
    }
  }
}
