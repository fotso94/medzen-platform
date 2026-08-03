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
