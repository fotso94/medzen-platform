# B1 — Terraform foundation. eu-central-1, account 558069890522.
#
# SAFETY RULE FOR THIS STACK: the VPC hosts MedZen PRODUCTION (internet-facing
# EHRbase ALB with clinical records, cache-proxy-prod, the live TTS gateway,
# 25 ENIs). Terraform therefore NEVER manages network primitives here — the VPC,
# subnets, IGW and route tables are read-only data sources. Everything this
# stack creates is additive and independently destroyable.

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.60" }
  }

  # State starts LOCAL so `plan` can run before any resource exists.
  # bootstrap.sh creates the bucket + lock table; then uncomment and re-init.
  # backend "s3" {
  #   bucket         = "medzen-speech-tfstate-558069890522"
  #   key            = "platform/terraform.tfstate"
  #   region         = "eu-central-1"
  #   dynamodb_table = "medzen-speech-tflock"
  #   encrypt        = true
  # }
}

provider "aws" {
  region  = var.region
  profile = var.profile

  # Refuse to run against the wrong account. Cheap, and this account has
  # production in it.
  allowed_account_ids = [var.account_id]

  default_tags {
    tags = {
      Project     = "medzen-speech"
      ManagedBy   = "terraform"
      Environment = var.environment
      Component   = "speech-platform"
    }
  }
}
