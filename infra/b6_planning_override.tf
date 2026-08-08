# Full-B6 planning and local build work require no EKS worker compute.
#
# This is deliberately an override instead of an edit to eks.tf: historical
# B6A authorization records bind that file's exact SHA-256. Any future CPU
# scale-up is a billable execution change and must replace this intended state
# prospectively under a versioned AWS packet.
resource "aws_eks_node_group" "cpu" {
  scaling_config {
    desired_size = 0
    min_size     = 0
    max_size     = 4
  }
}
