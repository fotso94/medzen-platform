# EKS in the SHARED production VPC. Every network primitive is a data source;
# this stack creates only the cluster, its node groups and its own SGs.

data "aws_iam_policy_document" "eks_cluster_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["eks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cluster" {
  name               = "${var.name}-cluster-role"
  assume_role_policy = data.aws_iam_policy_document.eks_cluster_trust.json
}

resource "aws_iam_role_policy_attachment" "cluster" {
  for_each = toset([
    "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSClusterPolicy",
  ])
  role       = aws_iam_role.cluster.name
  policy_arn = each.value
}

resource "aws_eks_cluster" "this" {
  name     = var.name
  version  = var.eks_version
  role_arn = aws_iam_role.cluster.arn

  # Keep the cluster on the normal support track. AWS will automatically move
  # a cluster to the next supported minor when standard support ends, so every
  # application release must remain compatible with that documented behavior.
  upgrade_policy {
    support_type = "STANDARD"
  }

  vpc_config {
    subnet_ids              = var.subnet_ids
    endpoint_private_access = true
    endpoint_public_access  = true
    # TODO before production: restrict to the office/CI egress CIDRs.
    public_access_cidrs = ["0.0.0.0/0"]
  }

  # Control-plane logs are cheap and the only record of an API-server incident.
  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  access_config {
    authentication_mode                         = "API_AND_CONFIG_MAP"
    bootstrap_cluster_creator_admin_permissions = true
  }

  depends_on = [aws_iam_role_policy_attachment.cluster]
}

# ---- node role -------------------------------------------------------------
data "aws_iam_policy_document" "node_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "node" {
  name               = "${var.name}-node-role"
  assume_role_policy = data.aws_iam_policy_document.node_trust.json
}

resource "aws_iam_role_policy_attachment" "node" {
  for_each = toset([
    "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKSWorkerNodePolicy",
    "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEKS_CNI_Policy",
    "arn:${data.aws_partition.current.partition}:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
  ])
  role       = aws_iam_role.node.name
  policy_arn = each.value
}

# Systems Manager is the independently reviewed, no-ingress control path for
# bounded node-level diagnostics. The exact permissions are frozen locally
# instead of following a mutable AWS-managed policy version. Because the CPU
# and GPU node groups share this role, any apply requires an explicit packet
# and independent IAM review.
resource "aws_iam_role_policy" "node_ssm_core" {
  name   = "${var.name}-node-ssm-core"
  role   = aws_iam_role.node.id
  policy = file("${path.module}/../platform/iam/medzen-node-ssm-core.json")
}

# ---- CPU node group --------------------------------------------------------
resource "aws_eks_node_group" "cpu" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "cpu"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.subnet_ids
  instance_types  = [var.cpu_instance_type]

  scaling_config {
    desired_size = 2
    min_size     = 2
    max_size     = 4
  }
  update_config { max_unavailable = 1 }
  labels = { workload = "cpu" }

  depends_on = [aws_iam_role_policy_attachment.node]
}

# ---- GPU node group --------------------------------------------------------
# desired_size = 0 until the quota lands. The group exists so the taint,
# labels and AMI type are already correct when capacity arrives — flipping
# gpu_desired_size to 1 is then the only change.
resource "aws_eks_node_group" "gpu" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "gpu"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = var.subnet_ids
  instance_types  = [var.gpu_instance_type]
  ami_type        = "AL2023_x86_64_NVIDIA"
  # The exact scan-qualified offline-evaluation image needs 28.1 GiB after
  # pull/unpack, kubelet reserve, workload scratch and the measured safety
  # margin. Forty GiB is the independently reviewed operational floor.
  disk_size = 40

  scaling_config {
    desired_size = var.gpu_desired_size
    min_size     = 0
    max_size     = 1
  }
  update_config { max_unavailable = 1 }
  labels = { workload = "gpu" }

  taint {
    key    = "nvidia.com/gpu"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  lifecycle { ignore_changes = [scaling_config[0].desired_size] }
  depends_on = [aws_iam_role_policy_attachment.node]
}

# ---- addons ----------------------------------------------------------------
resource "aws_eks_addon" "core" {
  for_each                    = toset(["vpc-cni", "coredns", "kube-proxy", "eks-pod-identity-agent"])
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = each.value
  resolve_conflicts_on_create = "OVERWRITE"
  depends_on                  = [aws_eks_node_group.cpu]
}

# ---- Pod Identity: one association per service (A2) ------------------------
resource "aws_eks_pod_identity_association" "svc" {
  for_each        = local.pod_roles
  cluster_name    = aws_eks_cluster.this.name
  namespace       = "medzen"
  service_account = each.key
  role_arn        = aws_iam_role.pod[each.key].arn
  depends_on      = [aws_eks_addon.core]
}
