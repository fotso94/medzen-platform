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
  type = string
  # Live upgrade is performed one minor at a time under
  # B6A-AWS-CHANGE-PACKET-2026-001. This is the intended reconciled end state;
  # local operations must use scripts/terraform_medzen.sh so backend and
  # provider calls resolve to the same owner-approved account.
  default = "1.36"
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

variable "enable_b6_load_balancer_controller" {
  type        = bool
  default     = false
  description = "True only inside an approved, deadline-first B6.6 window after CPU nodes are Ready; reset false before scale-to-zero."
}

variable "enable_b6_integration_window" {
  type        = bool
  default     = false
  description = "True only inside an approved B6.6 window after both worker deadlines are armed; reset false during cleanup."
}

variable "enable_b6_probe_qualification" {
  type        = bool
  default     = false
  description = "True only for the current reviewed Stage A isolated Fargate qualification; never starts EKS workers or ALB resources."
}

variable "enable_b6_client_keys" {
  type        = bool
  default     = true
  description = "Retains the packet-006 synthetic key boundary through B6.6; set false only during the approved final cleanup."
}

variable "github_repo" {
  type        = string
  default     = "REPLACE/medzen-platform"
  description = "owner/repo for the GitHub Actions OIDC trust policy"
}

variable "registry_publisher_principal_arn" {
  type        = string
  description = "Exact same-account principal allowed to assume the dedicated registry publisher role."

  validation {
    condition = can(regex(
      "^arn:aws:iam::[0-9]{12}:(user|role)/[A-Za-z0-9+=,.@_/-]+$",
      var.registry_publisher_principal_arn,
    ))
    error_message = "registry_publisher_principal_arn must be an exact IAM user or role ARN."
  }
}

variable "b7_ci_enabled" {
  description = "owner switch for the LEGACY B7 CI resources (role, EKS wiring, OIDC provider resource) — kept OFF so the arm-launch activation plan contains ONLY the arm role (Codex #25 / owner fast-path: exactly 2 add, 0 change, 0 destroy). B7 activation additionally requires importing the existing OIDC provider first."
  type        = bool
  default     = false
}

variable "github_repo_immutable" {
  description = "GitHub immutable OIDC identity owner@id/repo@id (Codex review #27: repos created after the immutable-subject rollout present this form in sub/job_workflow_ref; verified via the API — owner fotso94=16901658, repo medzen-platform=1322233937)"
  type        = string
  default     = "fotso94@16901658/medzen-platform@1322233937"
}
