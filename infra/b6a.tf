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

# AWS now evaluates Basic scan-on-push through the regional registry rules,
# not only the deprecated per-repository flag. Packet 2026-005 must be
# separately approved before this exact account/region-level change is applied.
# Exact filters intentionally exclude the separately owned medzen-tts-gateway.
resource "aws_ecr_registry_scanning_configuration" "b6a_runtime" {
  scan_type = "BASIC"

  rule {
    scan_frequency = "SCAN_ON_PUSH"

    repository_filter {
      filter      = "medzen-model-loader"
      filter_type = "WILDCARD"
    }

    repository_filter {
      filter      = "medzen-asr-runtime"
      filter_type = "WILDCARD"
    }

    repository_filter {
      filter      = "medzen-nvidia-dra"
      filter_type = "WILDCARD"
    }
  }
}
