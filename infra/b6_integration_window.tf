# Ephemeral B6.6 integration-window boundary. Every resource in this file is
# absent by default and exists only while an independently approved window is
# active. The window runner must arm both worker-group deadlines before setting
# enable_b6_integration_window=true.

locals {
  b6_backend_security_group_id = "sg-0a83abae6ab954543"
  b6_node_security_group_id    = "sg-070fc00321934eacb"
  b6_probe_image               = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com/medzen-rag-index@sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"
  b6_window_tags = {
    Project        = "medzen-speech"
    Environment    = var.environment
    CostCenter     = "speech-platform"
    Stage          = "B6.6"
    Workstream     = "integration-window"
    BudgetRegistry = "COST-REGISTRY-2026-003"
  }
}

resource "aws_vpc_security_group_ingress_rule" "b6_alb_from_backend" {
  count = var.enable_b6_integration_window ? 1 : 0

  security_group_id            = aws_security_group.b6_internal_alb.id
  referenced_security_group_id = local.b6_backend_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  description                  = "B6.6 synthetic backend probe to internal orchestrator ALB"

  tags = local.b6_window_tags
}

resource "aws_vpc_security_group_ingress_rule" "b6_nodes_from_alb" {
  count = var.enable_b6_integration_window ? 1 : 0

  security_group_id            = local.b6_node_security_group_id
  referenced_security_group_id = aws_security_group.b6_internal_alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  description                  = "B6.6 internal ALB to orchestrator pod target"

  tags = local.b6_window_tags
}

data "aws_iam_policy_document" "b6_probe_execution_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "b6_probe_execution" {
  count = var.enable_b6_integration_window ? 1 : 0

  name                 = "medzen-b6-window-probe-execution"
  max_session_duration = 3600
  assume_role_policy   = data.aws_iam_policy_document.b6_probe_execution_trust.json
  tags                 = local.b6_window_tags
}

data "aws_iam_policy_document" "b6_probe_execution" {
  statement {
    sid       = "GetPrivateRegistryToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid    = "PullOnlyQualifiedProbeImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [
      "arn:${data.aws_partition.current.partition}:ecr:${var.region}:${var.account_id}:repository/medzen-rag-index"
    ]
  }
}

resource "aws_iam_role_policy" "b6_probe_execution" {
  count = var.enable_b6_integration_window ? 1 : 0

  name   = "medzen-b6-window-probe-pull"
  role   = aws_iam_role.b6_probe_execution[0].id
  policy = data.aws_iam_policy_document.b6_probe_execution.json
}

resource "aws_ecs_cluster" "b6_probe" {
  count = var.enable_b6_integration_window ? 1 : 0

  name = "medzen-b6-window-probe"
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
  tags = local.b6_window_tags
}

resource "aws_ecs_task_definition" "b6_probe" {
  count = var.enable_b6_integration_window ? 1 : 0

  family                   = "medzen-b6-window-probe"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.b6_probe_execution[0].arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([{
    name       = "probe"
    image      = local.b6_probe_image
    essential  = true
    entryPoint = ["/usr/local/bin/python", "-c"]
    command = [
      "import json,os,urllib.request; u=os.environ['TARGET_URL']; r=urllib.request.urlopen(u,timeout=15); v=json.load(r); assert r.status==200 and v.get('ready') is True"
    ]
    environment            = [{ name = "TARGET_URL", value = "http://not-set.invalid/readyz" }]
    readonlyRootFilesystem = true
    linuxParameters = {
      initProcessEnabled = true
      capabilities       = { drop = ["ALL"] }
    }
  }])

  tags = local.b6_window_tags
}
