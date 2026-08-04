# B6A is a temporary, non-serving platform proof. Packet 2026-003A stopped at
# the model-loader ECR scan gate, so only its evidence-retained DRA repository
# remains active in Terraform. No B6A runtime identity is declared or created.

resource "aws_ecr_repository" "b6a_nvidia_dra" {
  name                 = "medzen-nvidia-dra"
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

# Deliberately no lifecycle policy: packet 2026-003A requires the
# evidence-bound NVIDIA DRA digest to remain retained throughout B6A.
