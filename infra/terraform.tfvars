# Resolved in B0. See B0_STATUS.md.
account_id  = "558069890522"
region      = "eu-central-1"
profile     = "medzen"
environment = "dev"

# SHARED WITH PRODUCTION — data sources only, never managed here.
vpc_id = "vpc-051aa9df8b64bf141"
subnet_ids = [
  "subnet-00232b25bc1ac407a", # eu-central-1a
  "subnet-05029419c6c61a536", # eu-central-1b
  "subnet-01fb2fc3f56bce55e", # eu-central-1c
]

data_bucket  = "medzen-speech"
audio_bucket = "medzen-audio-cache"

# 0 until L-DB2E81BA lands. Raise to 1 and re-apply; no other change needed.
gpu_desired_size = 0

# Dedicated publication boundary. This principal may assume the role, but the
# role itself is the only identity granted Parameter Store writes.
registry_publisher_principal_arn = "arn:aws:iam::558069890522:user/s.fotso"

# --- ACTIVATION FLAGS (Codex review #22 blocker 3) ---
# The three live systems below were originally applied with one-off -var
# flags; a plain plan then wanted to DESTROY them (reproduced: 6 deletions +
# 1 KMS policy change). Every enabled system is now PERSISTED here so any
# future plan/apply preserves the full live set. github_repo values are the
# exact strings read back from the deployed role trust policies 2026-08-24.
github_repo           = "fotso94/medzen-platform"
github_repo_immutable = "fotso94@16901658/medzen-platform@1322233937"

arm_launch_enabled              = true # medzen-arm-launch-role (live)
image_publisher_enabled         = true # medzen-image-publisher-role (live)
promotion_admission_enabled     = true # medzen-promotion-admission-role (live)
trainer_image_publisher_enabled = true # NEW: medzen-trainer-image-publisher-role (Arm-2, round 21/22)
