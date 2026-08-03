variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "profile" {
  type    = string
  default = "medzen"
}

variable "account_id" {
  type = string
}

variable "environment" {
  type    = string
  default = "dev"
}

variable "name" {
  type    = string
  default = "medzen-speech"
}

# --- From B0. Shared with MedZen production: read-only, never managed here. --
variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "data_bucket" {
  type = string
}

variable "audio_bucket" {
  type = string
}

variable "eks_version" {
  type    = string
  default = "1.31"
}

variable "cpu_instance_type" {
  type    = string
  default = "m6i.large"
}

variable "gpu_instance_type" {
  type    = string
  default = "g6.xlarge"
}

variable "gpu_desired_size" {
  type        = number
  default     = 0
  description = "Stays 0 until quota L-DB2E81BA lands (CASE_OPENED 2026-07-29)."
}

variable "github_repo" {
  type        = string
  default     = "REPLACE/medzen-platform"
  description = "owner/repo for the GitHub Actions OIDC trust policy"
}
