# Ephemeral B6.6 integration-window boundary. Every resource in this file is
# absent by default and exists only while an independently approved window is
# active. The window runner must arm both worker-group deadlines before setting
# enable_b6_integration_window=true.

locals {
  b6_backend_security_group_id = "sg-0a83abae6ab954543"
  b6_node_security_group_id    = "sg-070fc00321934eacb"
  b6_main_route_table_id       = "rtb-0c6eb6874ce0565dc"
  b6_probe_execution_role_arn  = "arn:${data.aws_partition.current.partition}:iam::${var.account_id}:role/medzen-b6-window-probe-execution"
  b6_probe_repository_arn      = "arn:${data.aws_partition.current.partition}:ecr:${var.region}:${var.account_id}:repository/medzen-rag-index"
  b6_ecr_layer_bucket_arn      = "arn:${data.aws_partition.current.partition}:s3:::prod-${var.region}-starport-layer-bucket/*"
  b6_probe_image               = "${var.account_id}.dkr.ecr.${var.region}.amazonaws.com/medzen-rag-index@sha256:fe4663812f88bd35d520fee3e80450981347c970f2a561eb8163b14183b7194c"
  b6_probe_resources_enabled   = var.enable_b6_probe_qualification || var.enable_b6_integration_window
  b6_window_tags = {
    Project        = "medzen-speech"
    Environment    = var.environment
    CostCenter     = "speech-platform"
    Stage          = "B6.6"
    Workstream     = "integration-window"
    BudgetRegistry = "COST-REGISTRY-2026-003"
  }
}

check "b6_probe_mode_is_exclusive" {
  assert {
    condition     = !(var.enable_b6_probe_qualification && var.enable_b6_integration_window)
    error_message = "B6 probe qualification and the full integration window may not be enabled together."
  }
}

# The two interface endpoints have one dedicated endpoint-side security group.
# Its only ingress is TLS from the already reviewed Fargate probe security
# group. It has no broad CIDR ingress and is deleted with the window.
resource "aws_security_group" "b6_probe_endpoints" {
  count = local.b6_probe_resources_enabled ? 1 : 0

  name        = "medzen-b6-probe-vpce"
  description = "Window-only private ECR endpoints for the B6.6 Fargate probe"
  vpc_id      = var.vpc_id

  tags = merge(local.b6_window_tags, {
    Name     = "medzen-b6-probe-vpce"
    Boundary = "B6-6-PROBE"
  })
}

resource "aws_vpc_security_group_ingress_rule" "b6_probe_to_endpoints" {
  count = local.b6_probe_resources_enabled ? 1 : 0

  security_group_id            = aws_security_group.b6_probe_endpoints[0].id
  referenced_security_group_id = local.b6_backend_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "TLS from the exact B6.6 Fargate probe SG only"

  tags = local.b6_window_tags
}

data "aws_iam_policy_document" "b6_probe_ecr_api_endpoint" {
  statement {
    sid       = "ExactProbeRoleRegistryToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
    principals {
      type        = "AWS"
      identifiers = [local.b6_probe_execution_role_arn]
    }
  }
}

data "aws_iam_policy_document" "b6_probe_ecr_dkr_endpoint" {
  statement {
    sid    = "ExactProbeRoleQualifiedImagePull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
    ]
    resources = [local.b6_probe_repository_arn]
    principals {
      type        = "AWS"
      identifiers = [local.b6_probe_execution_role_arn]
    }
  }
}

data "aws_iam_policy_document" "b6_probe_s3_endpoint" {
  statement {
    sid       = "MinimumEcrLayerBucketRead"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = [local.b6_ecr_layer_bucket_arn]
    principals {
      # Gateway endpoint policies require Principal="*". The boundary is the
      # one AWS-documented ECR layer bucket and GetObject action only.
      type        = "*"
      identifiers = ["*"]
    }
  }
}

resource "aws_vpc_endpoint" "b6_probe_ecr_api" {
  count = local.b6_probe_resources_enabled ? 1 : 0

  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = sort(var.subnet_ids)
  security_group_ids  = [aws_security_group.b6_probe_endpoints[0].id]
  private_dns_enabled = true
  policy              = data.aws_iam_policy_document.b6_probe_ecr_api_endpoint.json

  tags = merge(local.b6_window_tags, {
    Name            = "medzen-b6-probe-ecr-api"
    Boundary        = "B6-6-PROBE"
    EndpointPurpose = "ecr-api"
  })
}

resource "aws_vpc_endpoint" "b6_probe_ecr_dkr" {
  count = local.b6_probe_resources_enabled ? 1 : 0

  vpc_id              = var.vpc_id
  service_name        = "com.amazonaws.${var.region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = sort(var.subnet_ids)
  security_group_ids  = [aws_security_group.b6_probe_endpoints[0].id]
  private_dns_enabled = true
  policy              = data.aws_iam_policy_document.b6_probe_ecr_dkr_endpoint.json

  tags = merge(local.b6_window_tags, {
    Name            = "medzen-b6-probe-ecr-dkr"
    Boundary        = "B6-6-PROBE"
    EndpointPurpose = "ecr-dkr"
  })
}

resource "aws_vpc_endpoint" "b6_probe_s3" {
  count = local.b6_probe_resources_enabled ? 1 : 0

  vpc_id            = var.vpc_id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [local.b6_main_route_table_id]
  policy            = data.aws_iam_policy_document.b6_probe_s3_endpoint.json

  tags = merge(local.b6_window_tags, {
    Name            = "medzen-b6-probe-s3"
    Boundary        = "B6-6-PROBE"
    EndpointPurpose = "s3"
  })
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
  count = local.b6_probe_resources_enabled ? 1 : 0

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
  count = local.b6_probe_resources_enabled ? 1 : 0

  name   = "medzen-b6-window-probe-pull"
  role   = aws_iam_role.b6_probe_execution[0].id
  policy = data.aws_iam_policy_document.b6_probe_execution.json
}

resource "aws_ecs_cluster" "b6_probe" {
  count = local.b6_probe_resources_enabled ? 1 : 0

  name = "medzen-b6-window-probe"
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
  tags = local.b6_window_tags
}

resource "aws_ecs_task_definition" "b6_probe" {
  count = local.b6_probe_resources_enabled ? 1 : 0

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
