# Packet 2026-009 prospectively owns temporary B6.6 CPU desired capacity.
# Historical eks.tf and b6_planning_override.tf remain byte-identical to their
# prior evidence bindings. The deadline-controlled EKS API path scales the
# window; Terraform must not pull desired capacity to zero during ordered
# teardown of the controller and other window resources.
resource "aws_eks_node_group" "cpu" {
  lifecycle { ignore_changes = [scaling_config[0].desired_size] }
}
