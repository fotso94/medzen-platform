# medzen-tts-gateway (no "speech-") belongs to a SEPARATE, unrelated project.
# Never managed, referenced or touched by this stack. Ours is
# medzen-speech-tts-gateway.
locals {
  ecr_repos = [
    "medzen-orchestrator",
    "medzen-asr-runtime",
    "medzen-llm-gateway",
    "medzen-rag-index",
    "medzen-model-loader",
    "medzen-speech-tts-gateway",
    "medzen-trainer",
  ]
}

resource "aws_ecr_repository" "svc" {
  for_each             = toset(local.ecr_repos)
  name                 = each.value
  image_tag_mutability = "IMMUTABLE" # a tag is a model version; it must not move
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = aws_kms_key.data.arn
  }
}

resource "aws_ecr_lifecycle_policy" "svc" {
  for_each   = aws_ecr_repository.svc
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "keep last 10"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 }
      action       = { type = "expire" }
    }]
  })
}
