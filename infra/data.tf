# READ-ONLY view of the shared production network. Nothing here is managed.
data "aws_caller_identity" "me" {}
data "aws_partition" "current" {}

data "aws_vpc" "shared" { id = var.vpc_id }

data "aws_subnet" "selected" {
  for_each = toset(var.subnet_ids)
  id       = each.value
}

# Guard: fail the plan if the subnets are not spread across >=2 AZs (EKS and
# ALB both require it) or if a subnet is not in the expected VPC.
locals {
  subnet_azs    = [for s in data.aws_subnet.selected : s.availability_zone]
  subnet_vpc_ok = alltrue([for s in data.aws_subnet.selected : s.vpc_id == var.vpc_id])
}

resource "terraform_data" "network_preconditions" {
  lifecycle {
    precondition {
      condition     = length(distinct(local.subnet_azs)) >= 2
      error_message = "Need subnets in >=2 AZs; got ${join(",", local.subnet_azs)}."
    }
    precondition {
      condition     = local.subnet_vpc_ok
      error_message = "One or more subnet_ids are not in ${var.vpc_id}."
    }
  }
}
